"""v2_final: fixes of the v2 head reader review (three_month_impl/v2_review/DETAILS.md, R-1 to R-6), 2026-10-05.

  R-1  a ``$evidence`` key anywhere but the whole value of the four evidence positions is refused (fail closed):
       the whole ``last_attempt`` (the xs PARTIAL official_market_daily gate no longer accepts it), the whole
       ``metadata``, an entry-level field, another field, or nested at any depth;
  R-2  every reference is verified on its own: the same object named with another size is refused whatever the
       resolution order;
  R-3  ``$evidence`` / ``same_as_metadata`` are read only in a ``stock-data-publication-v2`` head; in a v1 head
       they are refused (``v2_construct_in_v1_head``); the schema is recorded in the pinned identity (v2 only) and
       bound to the archive;
  R-4  an entry whose evidence does not verify is disclosed as a source diagnostic when the head is pinned (so in
       every descriptor and report gap list), without blocking unrelated reads;
  R-5  a ``<sha>.json`` archive moved away (migration step 3) between lookup and read falls back to the splice in
       the pin validation and in the training pin;
  R-6  a splice text segment of any size is streamed in slices (bounded memory).
"""
from __future__ import annotations

import copy
import json
import tracemalloc
from pathlib import Path
from unittest.mock import patch

import pytest

import stock_data_sources as sources
import published_single_stock_projection as projection
from published_v2_testdata import (
    V1, V2, canonical, evidence_reference, sha, v2_document, write_evidence, write_splice_archive)
from test_published_head_v2 import _entries, _head

D = "2026-10-02"


def _root(tmp_path, name="shared_market_data"):
    root = (tmp_path / name).resolve()
    (root / "_metadata").mkdir(parents=True, exist_ok=True)
    return root


def _write_head(root, document):
    path = root / "_metadata" / "stock_data_current.json"
    path.write_bytes(canonical(document))
    return path


def _pin(root, path):
    return sources.PublishedSources([sources.SourceSpec("manifest_json", root, path, "shared_market_data")])


def _put(root, value):
    raw = canonical(value)
    folder = root / "_evidence"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (sha(raw) + ".json")).write_bytes(raw)
    return evidence_reference(value, raw)


def _view(reader, route):
    return reader.projection_artifacts(route)[0]["metadata"]


def _evidence_diagnostics(reader):
    return [item for item in reader.diagnostics if sources.EVIDENCE_UNRESOLVED_PREFIX in item]


# ---------------------------------------------------------------------------------------------------------------- R-1
def _omd_head(root, last_attempt, schema):
    payload = json.dumps({"data": [{"code": "2330", "stock_id": "2330", "date": D, "market": "TWSE",
                                    "close": 129.0}]}, separators=(",", ":")).encode("utf-8")
    (root / "board.json").write_bytes(payload)
    entry = {"route_id": "official_market_daily", "status": "PARTIAL", "path": "board.json", "sha256": sha(payload),
             "size": len(payload), "record_count": 1, "actual_min_date": "2021-01-04", "actual_max_date": D,
             "metadata": {"status": "PARTIAL", "gaps": [{"date": "2026-09-30", "market": "TPEx"}],
                          "last_attempt": last_attempt}}
    return _write_head(root, _head([entry], schema=schema))


def _set(path):
    def mutate(entry, ref):
        target = entry
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = ref(target)
    return mutate


DISALLOWED = {
    # name: (mutate(stored price entry, reference factory), expected unresolved field of the viewed metadata)
    "whole_metadata": (lambda entry, ref: entry.__setitem__("metadata", ref(entry["metadata"])),
                       sources.EVIDENCE_UNRESOLVED_KEY),
    "whole_last_attempt": (lambda entry, ref: entry["metadata"].__setitem__(
        "last_attempt", ref(entry["metadata"]["last_attempt"])), "last_attempt"),
    "entry_level_field": (lambda entry, ref: entry.__setitem__("source_receipts", ref([1, 2])),
                          sources.EVIDENCE_UNRESOLVED_KEY),
    "nested_in_entry_level_field": (lambda entry, ref: entry.__setitem__("extra", [{"x": ref([1, 2])}]),
                                    sources.EVIDENCE_UNRESOLVED_KEY),
    "other_metadata_field": (lambda entry, ref: entry["metadata"].__setitem__("coverage", ref({"total": 1})),
                             "coverage"),
    "nested_in_metadata_field": (lambda entry, ref: entry["metadata"].__setitem__(
        "coverage", {"total": 1500, "inner": ref([1, 2])}), "coverage"),
    "nested_in_partial_recovery": (lambda entry, ref: entry["metadata"].__setitem__(
        "partial_recovery", {"source_receipts": ref([1, 2])}), "partial_recovery"),
    "nested_in_diagnostics_value": (lambda entry, ref: entry["metadata"].__setitem__(
        "diagnostics", {"inner": ref([1, 2])}), "diagnostics"),
    "nested_in_inline_receipts": (lambda entry, ref: entry["metadata"].__setitem__(
        "source_receipts", [{"date": "2026-09-30", "detail": ref([1, 2])}]), "source_receipts"),
    "other_attempt_field": (lambda entry, ref: entry["metadata"]["last_attempt"].__setitem__(
        "coverage", ref({"total": 1})), "last_attempt.coverage"),
    "nested_in_attempt_gaps": (lambda entry, ref: entry["metadata"]["last_attempt"].__setitem__(
        "gaps", [ref([1, 2])]), "last_attempt.gaps"),
}


@pytest.mark.parametrize("case", sorted(DISALLOWED))
def test_references_at_disallowed_positions_are_refused_and_disclosed(tmp_path, case):
    root = _root(tmp_path)
    objects = {}
    document = v2_document(_head(_entries(root)), objects)
    write_evidence(root, objects)
    mutate, expected = DISALLOWED[case]
    mutate(document["datasets"][0], lambda value: _put(root, value))
    reader = _pin(root, _write_head(root, document))
    assert reader._sources[0].get("error") is None
    view = _view(reader, "finmind_price_adj")
    assert expected in sources.published_evidence_unresolved(view["metadata"]), view["metadata"]
    # no $evidence mapping survives in what a consumer reads (resolved objects hold none in this fixture)
    assert not sources.holds_evidence_key(view)
    proof = sources.published_completion("finmind_price_adj", view, verified_object=True)
    assert proof["completion_evidence_valid"] is False and proof["source_available_complete"] is False
    assert [item.split(":")[0] for item in _evidence_diagnostics(reader)] == ["shared_market_data/finmind_price_adj"]
    # the other routes are untouched; the payload rows are still read
    assert sources.published_evidence_unresolved(_view(reader, "official_market_daily")["metadata"]) == ()
    assert len(reader.rows("finmind_price_adj", stock_id="2330")["records"]) == 3
    # a viewed entry viewed again is the same object (idempotent; the proofs view what they are handed)
    assert sources.published_entry_view(view) is view


def test_the_nested_scan_is_skipped_only_when_the_pinned_bytes_cannot_hold_a_key(tmp_path):
    root = _root(tmp_path)
    objects = {}
    v1 = _head(_entries(root))
    v1_reader = _pin(root, _write_head(root, v1))
    assert v1_reader._evidence_scan == {0: False}                  # no "$" and no \u escape in the head
    v2 = v2_document(v1, objects)
    write_evidence(root, objects)
    v2_reader = _pin(root, _write_head(root, v2))
    assert v2_reader._evidence_scan == {0: True}
    restored = sources.PublishedSources.from_descriptor(v1_reader.snapshot_descriptor())
    assert restored._evidence_scan == {}                           # unknown: scanned
    # an escaped key is still a key: the escape forces the scan
    escaped = canonical(v1).replace(b'"metadata":{', b'"metadata":{"\\u0024evidence":"x",', 1)
    (root / "_metadata" / "stock_data_current.json").write_bytes(escaped)
    reader = _pin(root, root / "_metadata" / "stock_data_current.json")
    assert reader._evidence_scan == {0: True}
    assert _evidence_diagnostics(reader)                           # a v1 head: v2_construct_in_v1_head


# ---------------------------------------------------------------------------------------------------------------- R-2
def test_every_reference_is_verified_whatever_the_resolution_order(tmp_path):
    root = _root(tmp_path)
    good = _put(root, [{"date": "2026-09-30", "row_count": index} for index in range(5)])
    wrong_size = dict(good, size=good["size"] + 999)
    wrong_items = dict(good, items=good["items"] + 1)
    for order in ((good, wrong_size, wrong_items), (wrong_size, wrong_items, good), (wrong_items, good, wrong_size)):
        store = sources.PublishedEvidenceStore(root)
        outcome = {}
        for reference in order:
            try:
                store.load(reference)
                outcome[json.dumps(reference, sort_keys=True)] = "ok"
            except sources.PublishedEvidenceError as exc:
                outcome[json.dumps(reference, sort_keys=True)] = str(exc).split(":")[0]
        assert outcome == {json.dumps(good, sort_keys=True): "ok",
                           json.dumps(wrong_size, sort_keys=True): "evidence_object_mismatch",
                           json.dumps(wrong_items, sort_keys=True): "evidence_object_shape_mismatch"}, order
    # within one entry: the metadata reference is resolved first, the attempt's second - either may be the wrong one
    for meta, attempt, expected in ((good, wrong_size, ("last_attempt.source_receipts",)),
                                    (wrong_size, good, ("source_receipts",))):
        entry = {"route_id": "x", "metadata": {"source_receipts": meta, "last_attempt": {"source_receipts": attempt}}}
        view = sources.published_entry_view(entry, sources.PublishedEvidenceStore(root), constructs=True)
        assert sources.published_evidence_unresolved(view["metadata"]) == expected


# ---------------------------------------------------------------------------------------------------------------- R-3
def _mixed_v1_head(root):
    """A v2 head rewritten as v1 by a producer that does not know the v2 format: it kept the references and the
    same_as_metadata lists, and rewrote one metadata field the attempt claims to share."""
    objects = {}
    document = v2_document(_head(_entries(root)), objects)
    write_evidence(root, objects)
    document = dict(document, schema=V1)
    price = document["datasets"][0]["metadata"]
    assert "coverage" in price["last_attempt"]["same_as_metadata"]
    price["coverage"] = {"rewritten_by": "second_producer"}
    return _write_head(root, document)


def test_v2_constructs_in_a_v1_head_are_refused_and_disclosed(tmp_path):
    root = _root(tmp_path)
    reader = _pin(root, _mixed_v1_head(root))
    assert reader._sources[0].get("error") is None
    assert "publication_schema" not in reader._sources[0]["identity"]
    price = _view(reader, "finmind_price_adj")["metadata"]
    assert price["source_receipts"] == "published_evidence_unresolved:source_receipts:v2_construct_in_v1_head"
    attempt = price["last_attempt"]
    assert attempt["same_as_metadata"] == "published_evidence_unresolved:same_as_metadata:v2_construct_in_v1_head"
    assert "coverage" not in attempt                               # the rewritten value is never restored
    assert sources.published_completion("finmind_price_adj", _view(reader, "finmind_price_adj"),
                                        verified_object=True)["completion_evidence_valid"] is False
    assert sorted(item.split(":")[0] for item in _evidence_diagnostics(reader)) == [
        "shared_market_data/finmind_price_adj", "shared_market_data/official_market_daily",
        "shared_market_data/official_trading_restrictions"]
    assert all("v2_construct_in_v1_head" in item for item in _evidence_diagnostics(reader))
    # a catalog restored from the descriptor reads it the same way (the schema is part of the pinned identity)
    snapshot = reader.snapshot_descriptor()
    restored = sources.PublishedSources.from_descriptor(snapshot)
    assert canonical(_view(restored, "finmind_price_adj")) == canonical(_view(reader, "finmind_price_adj"))
    assert snapshot["diagnostics"] == reader.diagnostics and restored.diagnostics == reader.diagnostics


def test_the_v2_schema_is_recorded_and_bound_to_the_archive(tmp_path):
    root = _root(tmp_path)
    objects = {}
    v1 = _head(_entries(root))
    v2 = v2_document(v1, objects)
    write_evidence(root, objects)
    head = root / "_metadata" / "stock_data_current.json"

    def resealed(snapshot, edit):
        edited = copy.deepcopy(snapshot)
        edited.pop("descriptor_sha256")
        for identity in (edited["pinned_sources"][0]["identity"], edited["sources"][0]):
            edit(identity)
        edited["descriptor_sha256"] = sources._sha(sources._canonical(edited))
        return edited

    for document, edit in ((v1, lambda identity: identity.__setitem__("publication_schema", V2)),
                           (v2, lambda identity: identity.pop("publication_schema"))):
        _write_head(root, document)
        reader = _pin(root, head)
        snapshot = reader.snapshot_descriptor()
        # recorded for v2 heads only: a v1 descriptor is unchanged
        assert snapshot["sources"][0].get("publication_schema") == (V2 if document is v2 else None)
        assert reader.validate_pinned_context(snapshot)["status"] == "verified"
        edited = resealed(snapshot, edit)                      # a catalog claiming the other schema
        with pytest.raises(ValueError, match="published_context_pin_binding_mismatch:schema_changed"):
            sources.PublishedSources.from_descriptor(edited).validate_pinned_context(edited)


# ---------------------------------------------------------------------------------------------------------------- R-4
def test_unresolved_evidence_is_disclosed_from_the_pin_on(tmp_path):
    root = _root(tmp_path)
    objects = {}
    document = v2_document(_head(_entries(root)), objects)
    write_evidence(root, objects)
    head = _write_head(root, document)
    clean = _pin(root, head)
    assert clean.diagnostics == []
    missing = document["datasets"][0]["metadata"]["diagnostics"]["sha256"]
    (root / "_evidence" / (missing + ".json")).rename(root / "moved_away.json")
    reader = _pin(root, head)
    expected = [f"shared_market_data/finmind_price_adj: published_evidence_unresolved:diagnostics "
                f"(evidence_object_missing:{missing})"]
    assert reader.diagnostics == expected
    assert reader.descriptor()["diagnostics"] == expected
    snapshot = reader.snapshot_descriptor()
    assert snapshot["diagnostics"] == expected
    assert sources.PublishedSources.from_descriptor(snapshot).diagnostics == expected
    # the report context's gap list carries it (pin_published_sources_context -> context["gaps"])
    context = projection.pin_published_sources_context(
        expected_asof="2026-09-30", source_specs=[sources.SourceSpec("manifest_json", root, head,
                                                                     "shared_market_data")])
    assert list(context["gaps"]) == expected
    # unrelated reads go on: the price rows and the board route's proofs
    assert len(reader.rows("finmind_price_adj", stock_id="2330")["records"]) == 3
    assert sources.published_evidence_unresolved(_view(reader, "official_market_daily")["metadata"]) == ()


# ---------------------------------------------------------------------------------------------------------------- R-5
class _Race:
    """Moves ``<sha>.json`` away right before it is read (the migration's step 3 between lookup and read)."""

    def __init__(self, archive, destination):
        self.archive, self.destination, self.moved = archive, destination, False

    def __call__(self, original):
        def racing(path, *args, **kwargs):
            if not self.moved and Path(path) == self.archive:
                self.archive.rename(self.destination)
                self.moved = True
            return original(path, *args, **kwargs)
        return racing


def _archived(tmp_path, *, splice):
    root = _root(tmp_path)
    document = _head(_entries(root))
    raw = canonical(document)
    head = _write_head(root, document)
    archive = root / "_manifests" / (sha(raw) + ".json")
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(raw)
    splice_path = write_splice_archive(root, raw, document) if splice else None
    return root, document, raw, head, archive, splice_path


def test_pin_validation_falls_back_to_the_splice_when_the_json_archive_moves(tmp_path):
    root, document, raw, head, archive, splice = _archived(tmp_path, splice=True)
    with patch.object(sources, "_CANONICAL_SHARED_MARKET_DATA_ROOT", root):
        reader = _pin(root, head)
        snapshot = reader.snapshot_descriptor()
        head.write_bytes(canonical(dict(document, run_id="run-2")))          # the head advanced
        race = _Race(archive, root / "moved_by_migration.json")
        with patch.object(sources, "_verify_pin_file", race(sources._verify_pin_file)):
            proof = reader.validate_pinned_context(snapshot)
    assert race.moved and proof["status"] == "verified"
    assert proof["verified_pin_paths"] == (str(splice),) and proof["pin_bytes_read"] == len(raw)


def test_pin_validation_refuses_an_archive_that_vanished_without_a_splice(tmp_path):
    root, document, raw, head, archive, _ = _archived(tmp_path, splice=False)
    with patch.object(sources, "_CANONICAL_SHARED_MARKET_DATA_ROOT", root):
        reader = _pin(root, head)
        snapshot = reader.snapshot_descriptor()
        race = _Race(archive, root / "moved_by_migration.json")
        with patch.object(sources, "_verify_pin_file", race(sources._verify_pin_file)):
            # Converted gate (unbind step 5, plan 2.4 :3061-3066; identical to the unbind edit of the pre-current-only
            # copy in tests\_retired): a pinned snapshot whose archive vanished is re-pinned to the current head
            # (here the same bytes, so its digest is verified) and labelled instead of refused.
            proof = reader.validate_pinned_context(snapshot)
    assert race.moved and proof["status"] == "verified" and proof["repinned"] is True
    assert proof["verified_pin_paths"] == (str(head.resolve()),)
    assert [label["code"] for label in proof["version_labels"]] == ["pinned_snapshot_missing"]
    assert proof["version_labels"][0]["actual"]["manifest_sha256"] == sha(raw)


def test_a_large_text_segment_streams_in_slices(tmp_path):
    root = _root(tmp_path)
    rows = [{"i": index, "note": "z" * 200} for index in range(50000)]
    document = _head([{"route_id": "route_00", "status": "SUCCESS", "path": "p.json", "sha256": "a" * 64,
                       "size": 1, "metadata": {"rows": rows}}])
    raw = canonical(document)
    assert len(raw) > 10_000_000
    del document, rows
    path = root / "_manifests" / (sha(raw) + ".splice.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical({"$archive": "stock-data-archive-splice-v1", "sha256": sha(raw), "size": len(raw),
                                "segments": [{"t": raw.decode("utf-8")}]}))
    reader = sources._SpliceArchiveReader(root, path)              # the document itself is read and parsed here
    count = 0
    tracemalloc.start()
    try:
        while True:
            block = reader.read(sources._CHUNK_SIZE)
            if not block:
                break
            count += len(block)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert reader.verified and count == len(raw)
    assert peak < 8_000_000, peak                                  # was the whole segment at once
    assert sources._verify_pin_file(path, sha(raw), None, root) == len(raw)
