"""The binding v2 published-head contract on the reader side (v2_impl, 2026-10-05).

Spec: D:/stock/docs/CLAUDE_TO_CODEX_20261005_UPDATER_HANG_TRUNCATION_DEDUP_HANDOFF.md section 3 Wave 4.
  * ``stock-data-publication-v2`` heads are pinned, bound and validated like v1 heads; PublishedSources hands out
    every entry as recorded (``$evidence`` references resolved from ``<store>/_evidence`` and verified,
    ``last_attempt.same_as_metadata`` restored), so a v2 head reads exactly like the equivalent v1 head, while the
    pinned catalog / snapshot descriptor keep the small stored form;
  * each evidence object is verified and loaded once per reader; a changed object fails closed;
  * legacy archives ``<store>/_manifests/<sha>.splice.json`` (+ ``_evidence/blob/<sha>.bin``) are reconstructed by
    streaming (bounded memory) and verified blob by blob and in total; ``<sha>.json`` is preferred; any tampering
    fails closed; the training pin and a pinned spec read them too.
"""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import tracemalloc
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

import stock_data_sources as sources
import published_single_stock_projection as projection
from published_single_stock_projection import _route_window, _select
from published_v2_testdata import (
    V1, V2, canonical, sha, v2_document, write_evidence, write_splice_archive)
from test_published_no_events import DAY, official_no_data_entry, proved_entry


PROOF_KEYS = ("status", "reason", "completion_state", "source_available_complete", "completion_evidence_valid",
              "requested_scope_complete", "due_scope_complete", "completion_start_date", "completion_end_date",
              "data_complete", "history_complete", "usable", "model_usable", "evidence_scope", "last_attempt",
              "completion_reasons", "producer_gaps", "no_events", "pending_release_dates", "completion_scope")


def _payload(rows):
    return json.dumps({"data": rows}, separators=(",", ":")).encode("utf-8")


def _entries(root: Path):
    """Three routes: a large-evidence price entry, a PARTIAL board entry with a declared gap, an event snapshot."""
    root.mkdir(parents=True, exist_ok=True)
    price_rows = [{"stock_id": "2330", "date": f"2026-09-{day:02d}", "close": 100.0 + day} for day in (28, 29, 30)]
    board_rows = [{"code": "2330", "stock_id": "2330", "date": "2026-09-29", "market": "TWSE", "close": 129.0}]
    payloads = {"price.json": _payload(price_rows), "board.json": _payload(board_rows), "events.json": b"[]"}
    for name, raw in payloads.items():
        (root / name).write_bytes(raw)
    receipts = [{"date": f"2021-{index % 12 + 1:02d}-{index % 28 + 1:02d}", "market": "TWSE", "row_count": index,
                 "sha256": hashlib.sha256(str(index).encode()).hexdigest(), "note": "x" * 40}
                for index in range(1500)]                                       # > 64 KiB -> a reference
    diagnostics = [{"kind": "retry", "index": index} for index in range(3000)]   # > 64 KiB -> a reference
    price = {"route_id": "finmind_price_adj", "status": "SUCCESS", "path": "price.json",
             "sha256": sha(payloads["price.json"]), "size": len(payloads["price.json"]), "record_count": 3,
             "actual_min_date": "2026-09-28", "actual_max_date": "2026-09-30",
             "metadata": {"expected_asof": "2026-09-30", "requested_from_date": "2021-10-01",
                          "source_receipts": receipts, "diagnostics": diagnostics,
                          "coverage": {"total": 1500, "success": 1500},
                          "last_attempt": {"status": "SUCCESS", "warnings": [], "expected_asof": "2026-09-30",
                                           "requested_from_date": "2021-10-01",
                                           "source_receipts": copy.deepcopy(receipts),
                                           "diagnostics": copy.deepcopy(diagnostics[:2900]),   # differs: kept
                                           "coverage": {"total": 1500, "success": 1500}}}}
    board_gaps = [{"date": "2026-09-26", "reason": "declared_gap"}]
    board = {"route_id": "official_market_daily", "status": "PARTIAL", "path": "board.json",
             "sha256": sha(payloads["board.json"]), "size": len(payloads["board.json"]), "record_count": 1,
             "actual_min_date": "2026-09-29", "actual_max_date": "2026-09-29",
             "metadata": {"expected_asof": "2026-09-30", "gaps": board_gaps, "source_receipts": receipts[:900],
                          "coverage": {"total": 3, "success": 2},
                          "last_attempt": {"status": "PARTIAL", "gaps": copy.deepcopy(board_gaps),
                                           "source_receipts": copy.deepcopy(receipts[:900]),
                                           "coverage": {"total": 3, "success": 2}}}}
    events = proved_entry()
    events["metadata"]["last_attempt"] = dict(status="SUCCESS", warnings=[], **{
        key: copy.deepcopy(events["metadata"][key]) for key in ("coverage", "snapshot_observation",
                                                                "snapshot_complete", "observed_asof",
                                                                "history_unavailable", "source_receipts")})
    events.pop("selected_ref")
    events.update(path="events.json", sha256=sha(b"[]"), size=2)
    return [price, board, events]


def _head(entries, schema=V1):
    return {"schema": schema, "producer_id": "fixture-producer", "run_id": "run-1",
            "published_at": "2026-09-30T12:00:00Z", "datasets": entries}


class V2HeadReaderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = (Path(temp.name) / "shared_market_data").resolve()
        (self.root / "_metadata").mkdir(parents=True)
        self.v1 = _head(_entries(self.root))
        self.objects = {}
        self.v2 = v2_document(self.v1, self.objects)
        write_evidence(self.root, self.objects)
        self.v1_path = self.root / "_metadata" / "v1_head.json"
        self.v2_path = self.root / "_metadata" / "stock_data_current.json"
        self.v1_path.write_bytes(canonical(self.v1))
        self.v2_path.write_bytes(canonical(self.v2))

    def pin(self, path):
        return sources.PublishedSources([sources.SourceSpec("manifest_json", self.root, path, "shared_market_data")])

    def test_the_fixture_is_a_real_v2_head(self):
        price, board, events = self.v2["datasets"]
        self.assertEqual(price["metadata"]["source_receipts"]["$evidence"], "stock-data-evidence-v1")
        self.assertEqual(price["metadata"]["diagnostics"]["type"], "list")
        attempt = price["metadata"]["last_attempt"]
        self.assertEqual(attempt["same_as_metadata"], ["coverage", "expected_asof", "requested_from_date",
                                                      "source_receipts"])
        self.assertEqual(attempt["diagnostics"]["items"], 2900)                  # a different value is kept
        self.assertEqual(board["metadata"]["last_attempt"]["same_as_metadata"],
                         ["coverage", "gaps", "source_receipts"])
        self.assertIsInstance(events["metadata"]["source_receipts"], list)       # small evidence stays inline
        self.assertEqual(len(self.objects), 4)
        self.assertLess(len(canonical(self.v2)), len(canonical(self.v1)) // 5)

    def test_v1_and_v2_heads_read_and_prove_alike(self):
        v1, v2 = self.pin(self.v1_path), self.pin(self.v2_path)
        self.assertEqual((v1._sources[0].get("error"), v2._sources[0].get("error")), (None, None))
        for route in ("finmind_price_adj", "official_market_daily", "official_trading_restrictions"):
            with self.subTest(route=route):
                left, right = v1.projection_artifacts(route), v2.projection_artifacts(route)
                self.assertEqual(canonical(left[0]["metadata"]), canonical(right[0]["metadata"]))
                if route != "official_market_daily":
                    self.assertEqual(canonical(v1.artifacts(route)[0].metadata),
                                     canonical(v2.artifacts(route)[0].metadata))
                for start, end in (("2026-09-28", "2026-09-30"), (DAY, DAY), ("2026-09-26", "2026-09-29")):
                    results = []
                    for reader in (v1, v2):
                        loaded = reader.rows(route, stock_id="2330", start_date=start, end_date=end)
                        selected = _select(reader, route, "2330", start, end)
                        window = _route_window(selected, dict(start_date=start, end_date=end, before_count=0,
                                                              field_map=dict(stock_field="stock_id",
                                                                             date_field="date")))
                        results.append((loaded, selected, window))
                    for left_item, right_item in zip(*results):
                        for key in PROOF_KEYS:
                            self.assertEqual(canonical(left_item.get(key)), canonical(right_item.get(key)),
                                             (route, start, key))
                        self.assertEqual(left_item.get("records"), right_item.get("records"))
        # the event snapshot is proved from both heads (receipts, last_attempt flags and coverage)
        self.assertEqual(sources.published_no_events(
            "official_trading_restrictions", v2.artifacts("official_trading_restrictions")[0].metadata,
            verified_empty=True, end_date=DAY)["reason"], "no_events")

    def test_the_catalog_and_descriptor_keep_the_stored_form_and_bind_it(self):
        reader = self.pin(self.v2_path)
        snapshot = reader.snapshot_descriptor()
        stored = canonical(snapshot)
        self.assertIn(b'"$evidence":"stock-data-evidence-v1"', stored)
        self.assertIn(b'"same_as_metadata"', stored)
        self.assertLess(len(stored), len(canonical(self.pin(self.v1_path).snapshot_descriptor())) // 4)
        restored = sources.PublishedSources.from_descriptor(snapshot)
        self.assertEqual(restored.validate_pinned_context(snapshot)["status"], "verified")
        # the restored catalog hands out the same views (its own evidence store)
        self.assertEqual(canonical(restored.projection_artifacts("finmind_price_adj")[0]["metadata"]),
                         canonical(reader.projection_artifacts("finmind_price_adj")[0]["metadata"]))
        # a resealed descriptor whose stored entry was edited is still refused by the binding
        edited = copy.deepcopy(snapshot)
        pairs = edited["pinned_sources"][0]["entries"]["finmind_price_adj"]
        self.assertIs(pairs[0][0], pairs[0][1])                     # the pinned pair: ref is the entry
        pairs[0][1]["metadata"]["last_attempt"]["same_as_metadata"] = ["coverage"]
        edited.pop("descriptor_sha256")
        edited["descriptor_sha256"] = sources._sha(sources._canonical(edited))
        with self.assertRaisesRegex(ValueError, "published_context_pin_binding_mismatch:entry_changed"):
            sources.PublishedSources.from_descriptor(edited).validate_pinned_context(edited)

    def test_views_and_evidence_objects_are_computed_once(self):
        # v2_final: the views are computed when the head is pinned (so unresolved evidence is disclosed from the
        # start, R-4) and every later read hands out the same view without loading anything again
        loads = []
        original = sources._json

        def counting(raw):
            loads.append(len(raw))
            return original(raw)
        with patch.object(sources, "_json", counting):
            reader = self.pin(self.v2_path)
        pinned_loads = len(loads) - 1                       # minus the head document itself
        with patch.object(sources, "_json", counting):
            first = reader.projection_artifacts("finmind_price_adj")[0]["metadata"]
            again = reader.projection_artifacts("finmind_price_adj")[0]["metadata"]
        artifact = reader.artifacts("finmind_price_adj")[0].metadata
        self.assertIs(first, again)
        self.assertIs(artifact["metadata"], first["metadata"])
        self.assertEqual(len(loads) - 1, pinned_loads)      # nothing loaded after the pin
        self.assertEqual(pinned_loads, 4)                   # every object once: price receipts (metadata and
        #                                                     attempt share it), 2 price diagnostics, board receipts
        self.assertIs(first["metadata"]["source_receipts"], first["metadata"]["last_attempt"]["source_receipts"])
        self.assertEqual(len(reader._evidence_stores[0]._values), 4)
        stored = reader._sources[0]["entries"]["finmind_price_adj"][0][1]
        self.assertTrue(sources.is_evidence_reference(stored["metadata"]["source_receipts"]))  # catalog unchanged

    def test_a_changed_or_missing_object_fails_closed_for_that_route_only(self):
        reference = self.v2["datasets"][1]["metadata"]["source_receipts"]
        target = self.root / "_evidence" / (reference["sha256"] + ".json")
        target.write_bytes(target.read_bytes().replace(b'"TWSE"', b'"TPEx"', 1))
        reader = self.pin(self.v2_path)
        # disclosed from the pin on (one source diagnostic per entry; v2_final R-4)
        self.assertEqual([item for item in reader.diagnostics if "published_evidence_unresolved" in item],
                         [f"shared_market_data/official_market_daily: published_evidence_unresolved:"
                          f"last_attempt.source_receipts,source_receipts "
                          f"(evidence_object_mismatch:{reference['sha256']})"])
        board = reader.projection_artifacts("official_market_daily")[0]["metadata"]
        self.assertTrue(sources.is_evidence_unresolved(board["metadata"]["source_receipts"]))
        self.assertIn("evidence_object_mismatch", board["metadata"]["source_receipts"])
        self.assertTrue(sources.is_evidence_unresolved(board["metadata"]["last_attempt"]["source_receipts"]))
        # the price route's own objects are untouched and still verify
        price = reader.projection_artifacts("finmind_price_adj")[0]["metadata"]
        self.assertEqual(len(price["metadata"]["source_receipts"]), 1500)
        # a missing object
        (self.root / "_evidence" / (self.v2["datasets"][0]["metadata"]["diagnostics"]["sha256"] + ".json")).rename(
            self.root / "moved_away.json")
        reader = self.pin(self.v2_path)
        price = reader.projection_artifacts("finmind_price_adj")[0]["metadata"]
        self.assertIn("evidence_object_missing", price["metadata"]["diagnostics"])
        self.assertEqual(sources.published_evidence_unresolved(price["metadata"]), ("diagnostics",))

    def test_unknown_schema_is_refused(self):
        self.v2_path.write_bytes(canonical(dict(self.v2, schema="stock-data-publication-v3")))
        reader = self.pin(self.v2_path)
        self.assertIn("unknown publication schema", reader._sources[0]["error"])

    def test_other_readers_accept_both_schemas(self):
        import stock_query_projection
        for document in (self.v1, self.v2):
            self.v2_path.write_bytes(canonical(document))
            pinned = stock_query_projection.pin_current_publication(self.root, ["finmind_price_adj"])
            self.assertEqual(pinned["routes"][0]["sha256"], document["datasets"][0]["sha256"])
        self.v2_path.write_bytes(canonical(dict(self.v2, schema="stock-data-publication-v3")))
        with self.assertRaises(stock_query_projection.ProjectionIntegrityError):
            stock_query_projection.pin_current_publication(self.root, ["finmind_price_adj"])


class SpliceArchiveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = (Path(temp.name) / "shared_market_data").resolve()
        (self.root / "_metadata").mkdir(parents=True)
        canonical_root = patch.object(sources, "_CANONICAL_SHARED_MARKET_DATA_ROOT", self.root)
        canonical_root.start()
        self.addCleanup(canonical_root.stop)
        self.document = _head(_entries(self.root))
        self.raw = canonical(self.document)
        self.digest = sha(self.raw)
        self.current = self.root / "_metadata" / "stock_data_current.json"
        self.current.write_bytes(self.raw)
        self.reader = sources.PublishedSources([
            sources.SourceSpec("manifest_json", self.root, self.current, "shared_market_data")])
        self.snapshot = self.reader.snapshot_descriptor()
        self.splice = write_splice_archive(self.root, self.raw, self.document)
        self.current.write_bytes(canonical(dict(self.document, run_id="run-2")))      # the head advanced

    def splice_document(self):
        return json.loads(self.splice.read_bytes())

    def write_splice(self, document):
        self.splice.write_bytes(canonical(document))

    def blob(self, index=0):
        segment = [item for item in self.splice_document()["segments"] if "b" in item][index]
        return self.root / "_evidence" / "blob" / (segment["b"] + ".bin")

    def validate(self):
        return self.reader.validate_pinned_context(self.snapshot)

    def test_the_splice_reconstructs_the_archive(self):
        document = self.splice_document()
        self.assertEqual((document["sha256"], document["size"]), (self.digest, len(self.raw)))
        self.assertEqual(sum(1 for item in document["segments"] if "b" in item), 3)
        self.assertLess(len(self.splice.read_bytes()), 1000)                 # text segments + 3 blob names
        self.assertEqual(sources.read_publication_manifest(self.splice, self.root), self.raw)
        self.assertEqual(sources.publication_archive_path(self.root, self.digest), self.splice)

    def test_validation_reads_the_splice_when_the_json_archive_is_gone(self):
        proof = self.validate()
        self.assertEqual(proof["status"], "verified")
        self.assertEqual(proof["verified_pin_paths"], (str(self.splice),))
        self.assertEqual(proof["pin_bytes_read"], len(self.raw))
        restored = sources.PublishedSources.from_descriptor(self.snapshot)
        self.assertEqual(restored.validate_pinned_context(self.snapshot)["verified_pin_paths"], (str(self.splice),))

    def test_the_json_archive_is_preferred(self):
        archive = self.root / "_manifests" / (self.digest + ".json")
        archive.write_bytes(self.raw)
        self.blob().write_bytes(b"damaged")                     # the splice would fail: it is not read
        self.assertEqual(self.validate()["verified_pin_paths"], (str(archive),))

    def test_a_spec_pinned_to_a_splice_or_a_migrated_json_path_reads_the_archive(self):
        for path in (self.splice, self.root / "_manifests" / (self.digest + ".json")):
            with self.subTest(path=path.name):
                pinned = sources.PublishedSources([
                    sources.SourceSpec("manifest_json", self.root, path, "shared_market_data")])
                state = pinned._sources[0]
                self.assertIsNone(state.get("error"))
                self.assertEqual(state["identity"]["manifest_sha256"], self.digest)
                self.assertEqual(state["identity"]["manifest_path"], str(path))
                self.assertEqual(canonical(state["entries"]), canonical(self.reader._sources[0]["entries"]))
                descriptor = pinned.snapshot_descriptor()
                self.assertEqual(pinned.validate_pinned_context(descriptor)["verified_pin_paths"],
                                 (str(self.splice),))

    def test_any_tampering_fails_closed(self):
        def flip_blob(_):
            path = self.blob(1)
            data = bytearray(path.read_bytes())
            data[10] ^= 1
            path.write_bytes(bytes(data))

        def truncate_blob(_):
            path = self.blob(0)
            path.write_bytes(path.read_bytes()[:-1])

        def extend_blob(_):
            path = self.blob(2)
            path.write_bytes(path.read_bytes() + b" ")

        def missing_blob(_):
            self.blob(0).rename(self.root / "moved.bin")

        def edit_text(document):
            document["segments"][0]["t"] = document["segments"][0]["t"].replace("datasets", "datasetz")

        def wrong_length(document):
            segment = next(item for item in document["segments"] if "b" in item)
            segment["n"] -= 1

        def wrong_size(document):
            document["size"] += 1

        def drop_segment(document):
            document["segments"].pop()

        def foreign_sha(document):
            document["sha256"] = "0" * 64

        def extra_key(document):
            document["note"] = "x"

        def bad_segment(document):
            document["segments"].append({"t": "", "b": "0" * 64})

        def wrong_schema(document):
            document["$archive"] = "stock-data-archive-splice-v0"

        def lone_surrogate(document):
            document["segments"][-1]["t"] = document["segments"][-1]["t"] + "\ud800"

        file_mutations = (flip_blob, truncate_blob, extend_blob, missing_blob)
        document_mutations = (edit_text, wrong_length, wrong_size, drop_segment, foreign_sha, extra_key,
                              bad_segment, wrong_schema, lone_surrogate)
        original = self.splice.read_bytes()
        blobs = {path: path.read_bytes() for path in (self.root / "_evidence" / "blob").iterdir()}
        for mutate in file_mutations + document_mutations:
            with self.subTest(mutation=mutate.__name__):
                self.splice.write_bytes(original)
                for path in list((self.root / "_evidence" / "blob").iterdir()):
                    path.unlink()
                for path, data in blobs.items():
                    path.write_bytes(data)
                if mutate in document_mutations:
                    document = self.splice_document()
                    mutate(document)
                    self.splice.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")),
                                           encoding="utf-8")
                else:
                    mutate(None)
                with self.assertRaises(ValueError):
                    self.validate()
                with self.assertRaises((ValueError, OSError)):
                    sources.read_publication_manifest(self.splice, self.root)

    def test_duplicate_keys_in_the_splice_document_fail_closed(self):
        text = self.splice.read_text(encoding="utf-8")
        self.splice.write_text('{"size":1,' + text[1:], encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "published_context_pin_splice_invalid"):
            self.validate()

    def test_streaming_verification_memory_is_bounded(self):
        # an 11 MB archive in 6 blobs: hashing and strictly parsing the reconstruction keeps ~one block
        big_root = self.root.parent / "big"
        entries = []
        for index in range(6):
            rows = [{"i": index, "j": j, "note": "y" * 180} for j in range(9000)]
            entries.append({"route_id": f"route_{index:02d}", "status": "SUCCESS", "path": "p.json",
                            "sha256": "a" * 64, "size": 1, "metadata": {"rows": rows}})
        document = _head(entries)
        raw = canonical(document)
        self.assertGreater(len(raw), 10_000_000)
        splice = write_splice_archive(big_root, raw, document)
        del document, entries
        tracemalloc.start()
        try:
            count = sources._verify_pin_file(splice, sha(raw), None, big_root)
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(count, len(raw))
        self.assertLess(peak, 8_000_000, peak)


def _fixture_specs(root: Path, document):
    (root / "_metadata").mkdir(parents=True, exist_ok=True)
    raw = canonical(document)
    head = root / "_metadata" / "stock_data_current.json"
    head.write_bytes(raw)
    return (sources.SourceSpec("manifest_json", root, head, "shared_market_data"),), head, raw


def test_session_import_of_a_v2_head(tmp_path):
    root = (tmp_path / "shared_market_data").resolve()
    objects = {}
    document = v2_document(_head(_entries(root)), objects)
    write_evidence(root, objects)
    specs, head, raw = _fixture_specs(root, document)
    reader = sources.PublishedSources(specs)
    session = {"schema": "single-stock-published-session-v1", "expected_asof": "2026-09-30",
               "data_date": "2026-09-30",
               "snapshot_descriptor": sources.descriptor_storage(reader.snapshot_descriptor())}
    unsigned = dict(session, snapshot_descriptor=sources.descriptor_from_storage(session["snapshot_descriptor"]))
    session["descriptor_sha256"] = projection._digest(unsigned)
    path = tmp_path / "session.json"
    path.write_bytes(canonical(session))
    context = projection.import_published_sources_context(path, sha(path.read_bytes()), "2026-09-30")
    assert context["pin_validation"]["status"] == "verified"
    view = context["sources"].projection_artifacts("finmind_price_adj")[0]["metadata"]["metadata"]
    assert len(view["source_receipts"]) == 1500 and view["last_attempt"]["source_receipts"] is view["source_receipts"]
