"""Completion proofs read the complete pinned evidence, whatever the shape of the published head (2026-10-05).

The binding v2 contract (updater handoff 2026-10-05 section 3 Wave 4; v2_impl replaces the cd_final references /
deduplicated_fields): a ``stock-data-publication-v2`` head may hold ``$evidence`` references at
``metadata.{source_receipts,diagnostics}`` and ``metadata.last_attempt.{source_receipts,diagnostics}`` (objects in
``<store>/_evidence``) and may leave out ``last_attempt`` fields canonical-equal to the metadata, listed in
``last_attempt.same_as_metadata``.  PublishedSources hands out the entry as recorded, so a v2 head proves exactly
what the equivalent v1 head proves; anything that does not verify fails closed (visibly).  An absent attempt field
that is not listed keeps meaning "this attempt had none" (cd_final A/F2), so it never borrows the metadata's
receipts or window.  C13: a conflict is decided on every item and nesting level of the pinned evidence; only the
reported diagnostic is bounded.  C6: the proof re-scoped by ``_route_window`` keeps whatever producer metadata the
full read proved.
"""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import stock_data_sources as sds
from stock_data_sources import (
    EVIDENCE_UNRESOLVED_PREFIX, LAST_ATTEMPT_COPY_FIELDS, PublishedSources, SourceSpec, attempt_view,
    last_attempt_unresolved, published_completion, published_entry_view, published_evidence_unresolved,
    published_no_events, resolved_last_attempt)
from published_single_stock_projection import _route_window, _select, _bounded_producer_evidence
from published_v2_testdata import V1, V2, canonical, compact_entry, evidence_reference, write_evidence
from test_published_no_events import (
    DAY, FixtureMixin, government_completion_entry, official_no_data_entry, proved_entry)


SHAPES = ("v1_full_copies", "v2_compact", "v2_all_references")


def shaped_sources(case, entry, rows=(), *, shape="v2_all_references", mutate=None):
    """PublishedSources of a one-entry head in ``shape`` (v2: evidence objects in ``<root>/_evidence``).
    ``mutate(entry, objects, root)`` may damage the stored form before it is written."""
    temporary = tempfile.TemporaryDirectory()
    case.addCleanup(temporary.cleanup)
    root = Path(temporary.name)
    raw = json.dumps(list(rows), ensure_ascii=False).encode("utf-8")
    (root / "payload.json").write_bytes(raw)
    entry = copy.deepcopy(entry)
    entry.update(path="payload.json", sha256=hashlib.sha256(raw).hexdigest(), size=len(raw),
                 record_count=len(rows))
    objects = {}
    if shape != "v1_full_copies":
        entry = compact_entry(entry, objects, inline_max=0 if shape == "v2_all_references" else 64 * 1024)
    if mutate is not None:
        mutate(entry, objects, root)
    write_evidence(root, objects)
    (root / "manifest.json").write_bytes(canonical(dict(
        schema=V1 if shape == "v1_full_copies" else V2, datasets=[entry])))
    return PublishedSources([SourceSpec("manifest_json", root, "manifest.json", "shared_market_data")])


PROOF_KEYS = ("status", "reason", "completion_state", "source_available_complete", "completion_evidence_valid",
              "requested_scope_complete", "due_scope_complete", "completion_start_date", "completion_end_date",
              "data_complete", "history_complete", "usable", "model_usable", "evidence_scope", "last_attempt",
              "completion_reasons", "producer_gaps", "no_events", "pending_release_dates", "completion_scope")


def no_events_entry_with_attempt():
    entry = proved_entry()
    metadata = entry["metadata"]
    metadata["last_attempt"] = dict(status="SUCCESS", warnings=[], **{
        field: copy.deepcopy(metadata[field]) for field in LAST_ATTEMPT_COPY_FIELDS if field in metadata})
    return entry


class HeadShapeTests(FixtureMixin, unittest.TestCase):
    def chain(self, entry, *, rows=(), day, shape="v2_all_references", mutate=None):
        sources = shaped_sources(self, entry, rows, shape=shape, mutate=mutate)
        loaded = sources.rows(entry["route_id"], stock_id="2330", start_date=day, end_date=day)
        selected = _select(sources, entry["route_id"], "2330", day, day)
        window = _route_window(selected, dict(start_date=day, end_date=day, before_count=0,
                                              field_map=dict(stock_field="stock_id", date_field="date")))
        return dict(loaded=loaded, selected=selected, window=window)

    def assert_same_proofs(self, results, expected_state):
        reference_name, reference_result = results[0]
        for name, result in results:
            for stage, item in result.items():
                self.assertEqual(item["completion_state"], expected_state, (name, stage))
                for key in PROOF_KEYS:
                    self.assertEqual(item.get(key), reference_result[stage].get(key), (name, stage, key))

    def test_completion_proofs_are_identical_for_every_head_shape(self):
        cases = (
            (government_completion_entry(), "2023-01-11", "source_unavailable", "available"),
            (government_completion_entry(pending=True), DAY, "pending_release", "missing"),
            (official_no_data_entry(), "2026-09-30", "confirmed_no_data", "available"),
            (no_events_entry_with_attempt(), DAY, "no_events", "available"),
        )
        for entry, day, state, status in cases:
            with self.subTest(state=state):
                stored = compact_entry(entry, {}, inline_max=0)
                attempt = stored["metadata"]["last_attempt"]
                self.assertIn("source_receipts", attempt["same_as_metadata"])
                self.assertIn("coverage", attempt["same_as_metadata"])
                self.assertEqual(stored["metadata"]["source_receipts"]["$evidence"], "stock-data-evidence-v1")
                results = [(shape, self.chain(entry, day=day, shape=shape)) for shape in SHAPES]
                self.assert_same_proofs(results, state)
                self.assertEqual(results[0][1]["window"]["status"], status)
                if state != "pending_release":
                    self.assertTrue(results[0][1]["window"]["source_available_complete"])
                    self.assertEqual((results[0][1]["window"]["completion_start_date"],
                                      results[0][1]["window"]["completion_end_date"]), (day, day))

    def test_unlisted_absent_attempt_fields_mean_the_attempt_had_none(self):
        # cd_final A/F2: the producer of 2026-10 leaves last_attempt fields out for other reasons (a retained
        # publication whose attempt comes from a newer candidate, minimal Order21 attempts); such an attempt never
        # borrows the metadata's receipts or window, so a proof that needs them is not established.
        def drop_listed(entry, objects, root):
            entry["metadata"]["last_attempt"].pop("same_as_metadata")      # left out and NOT listed
        cases = (
            (government_completion_entry(), "2023-01-11", "source_unavailable"),
            (government_completion_entry(pending=True), DAY, "pending_release"),
            (official_no_data_entry(), "2026-09-30", "confirmed_no_data"),
        )
        for entry, day, state in cases:
            with self.subTest(state=state):
                stored = compact_entry(entry, {}, inline_max=64 * 1024)
                drop_listed(stored, {}, None)
                self.assertIs(resolved_last_attempt(stored["metadata"]), stored["metadata"]["last_attempt"])
                self.assertNotIn("source_receipts", resolved_last_attempt(stored["metadata"]))
                for stage, item in self.chain(entry, day=day, shape="v2_compact", mutate=drop_listed).items():
                    self.assertNotEqual(item["completion_state"], state, stage)
                    self.assertFalse(item["source_available_complete"], stage)
                    self.assertFalse(item["completion_evidence_valid"], stage)
        # the no_events proof never read the attempt's copies (only its status, flags and coverage when present):
        # an unlisted left-out coverage proves what the full copy proves, exactly as before
        stored = compact_entry(no_events_entry_with_attempt(), {}, inline_max=64 * 1024)
        silent = copy.deepcopy(stored)
        silent["metadata"]["last_attempt"].pop("same_as_metadata")
        events = [published_no_events("official_trading_restrictions", shaped, verified_empty=True, end_date=DAY)
                  for shaped in (no_events_entry_with_attempt(), stored, silent)]
        self.assertEqual(events[0]["reason"], "no_events")
        self.assertTrue(all(event == events[0] for event in events))

    def test_invalid_same_as_metadata_fails_closed_and_is_disclosed(self):
        def listing(value):
            def mutate(entry, objects, root):
                entry["metadata"]["last_attempt"]["same_as_metadata"] = value
            mutate.__name__ = f"listing_{value!r}"
            return mutate

        def listed_but_missing_in_metadata(entry, objects, root):
            entry["metadata"].pop("errors")

        def listed_and_also_present(entry, objects, root):
            entry["metadata"]["last_attempt"]["errors"] = []

        base = compact_entry(government_completion_entry(), {}, inline_max=64 * 1024)      # evidence inline
        listed = base["metadata"]["last_attempt"]["same_as_metadata"]
        self.assertEqual(listed, compact_entry(government_completion_entry(), {}, inline_max=0)[
            "metadata"]["last_attempt"]["same_as_metadata"])
        self.assertEqual(listed, sorted(listed))
        mutations = (listing("source_receipts"), listing(list(reversed(listed))), listing(listed + [listed[0]]),
                     listing(listed + ["status"]), listing([1]), listing(None), listing({}),
                     listed_but_missing_in_metadata, listed_and_also_present)
        for mutate in mutations:
            with self.subTest(mutation=mutate.__name__):
                stored = copy.deepcopy(base)
                mutate(stored, {}, None)
                view = published_entry_view(stored)["metadata"]
                self.assertEqual(published_evidence_unresolved(view), ("last_attempt.same_as_metadata",))
                with self.assertRaises(sds.PublishedEvidenceError):
                    attempt_view(stored["metadata"])
                for item in self.chain(government_completion_entry(), day="2023-01-11", mutate=mutate).values():
                    self.assertNotEqual(item["status"], "available")
                    self.assertFalse(item["source_available_complete"])
                    self.assertFalse(item["completion_evidence_valid"])
                    self.assertIn(EVIDENCE_UNRESOLVED_PREFIX, json.dumps(item["last_attempt"]))

    def test_any_unresolved_attempt_value_refuses_the_no_events_proof(self):
        base = compact_entry(no_events_entry_with_attempt(), {}, inline_max=64 * 1024)
        self.assertEqual(published_no_events("official_trading_restrictions", base, verified_empty=True,
                                             end_date=DAY)["reason"], "no_events")
        bad_reference = evidence_reference(base["metadata"]["source_receipts"])
        bad_reference["sha256"] = "e" * 64
        for field, value in (("source_receipts", bad_reference), ("diagnostics", dict(bad_reference)),
                             ("coverage", dict(bad_reference)), ("same_as_metadata", ["coverage", "coverage"])):
            with self.subTest(field=field):
                entry = copy.deepcopy(base)
                attempt = entry["metadata"]["last_attempt"]
                if field in attempt.get("same_as_metadata", ()):
                    attempt["same_as_metadata"].remove(field)
                attempt[field] = value
                self.assertIsNone(published_no_events("official_trading_restrictions", entry,
                                                      verified_empty=True, end_date=DAY))
                self.assertIsNone(sds.published_event_snapshot_day("official_trading_restrictions", entry,
                                                                   row_count=0))
                proof = published_completion("official_trading_restrictions", entry, verified_object=True,
                                             query_empty=True, global_empty=True, start_date=DAY, end_date=DAY)
                self.assertNotEqual(proof["completion_state"], "no_events")
                self.assertTrue(str(proof["last_attempt"][field]).startswith(EVIDENCE_UNRESOLVED_PREFIX))

    def test_pure_proofs_are_identical_for_every_inline_head_shape(self):
        # without a store only inline values resolve: the v2 storage form with inline evidence proves the same
        for entry, route, kwargs, state in (
                (government_completion_entry(), "finmind_government_bank",
                 dict(start_date="2023-01-11", end_date="2023-01-11"), "source_unavailable"),
                (official_no_data_entry(), "official_market_daily",
                 dict(start_date="2026-09-30", end_date="2026-09-30"), "confirmed_no_data")):
            proofs = [published_completion(route, shaped, verified_object=True, query_empty=True,
                                           global_empty=True, **kwargs)
                      for shaped in (entry, compact_entry(entry, {}, inline_max=64 * 1024))]
            self.assertEqual(proofs[0]["completion_state"], state)
            self.assertEqual(proofs[0], proofs[1])
        events = [published_no_events("official_trading_restrictions", shaped, verified_empty=True, end_date=DAY)
                  for shaped in (no_events_entry_with_attempt(),
                                 compact_entry(no_events_entry_with_attempt(), {}, inline_max=64 * 1024))]
        self.assertEqual(events[0]["reason"], "no_events")
        self.assertEqual(events[0], events[1])

    def test_references_without_a_store_fail_closed(self):
        # a stored v2 entry read outside PublishedSources (no evidence store): its references cannot be verified
        stored = compact_entry(government_completion_entry(), {}, inline_max=0)
        proof = published_completion("finmind_government_bank", stored, verified_object=True, query_empty=True,
                                     global_empty=True, start_date="2023-01-11", end_date="2023-01-11")
        self.assertNotEqual(proof["completion_state"], "source_unavailable")
        self.assertFalse(proof["source_available_complete"])
        self.assertIn("evidence_store_unavailable", json.dumps(proof["completion_reasons"]))
        stored = compact_entry(no_events_entry_with_attempt(), {}, inline_max=0)
        self.assertIsNone(published_no_events("official_trading_restrictions", stored, verified_empty=True,
                                              end_date=DAY))

    def test_tampered_or_misplaced_evidence_fails_closed_and_is_disclosed(self):
        def on_object(change):
            def mutate(entry, objects, root):
                reference = entry["metadata"]["source_receipts"]
                raw = objects[reference["sha256"]]
                new_raw, new_ref = change(raw, reference)
                if new_raw is not None:
                    objects[reference["sha256"]] = new_raw if new_ref is None else raw
                    if new_ref is not None:
                        objects[new_ref["sha256"]] = new_raw
                if new_ref is not None:
                    entry["metadata"]["source_receipts"] = new_ref
            mutate.__name__ = change.__name__
            return mutate

        def flipped_byte(raw, reference):
            return raw[:-2] + (b"0" if raw[-2:-1] != b"0" else b"1") + raw[-1:], None

        def missing_object(raw, reference):
            return None, dict(reference, sha256="f" * 64)

        def size_mismatch(raw, reference):
            return None, dict(reference, size=reference["size"] + 1)

        def duplicate_key(raw, reference):
            text = raw.decode("utf-8")
            new = ('[{"status":"EMPTY",' + text[2:]).encode("utf-8")
            return new, dict(reference, sha256=hashlib.sha256(new).hexdigest(), size=len(new))

        def nan_value(raw, reference):
            new = raw[:-1] + b',NaN]'
            return new, dict(reference, sha256=hashlib.sha256(new).hexdigest(), size=len(new), items=2)

        def not_canonical(raw, reference):
            new = b"[ " + raw[1:]
            return new, dict(reference, sha256=hashlib.sha256(new).hexdigest(), size=len(new))

        def wrong_type(raw, reference):
            return None, dict(reference, type="object")

        def wrong_items(raw, reference):
            return None, dict(reference, items=2)

        def extra_key(raw, reference):
            return None, dict(reference, ref="metadata.source_receipts")

        def wrong_schema(raw, reference):
            return None, dict(reference, **{"$evidence": "stock-data-evidence-v0"})

        def misplaced_reference(entry, objects, root):
            # a reference anywhere but the four evidence positions is refused, even if its object verifies
            coverage = entry["metadata"]["coverage"]
            raw = canonical(coverage)
            objects[hashlib.sha256(raw).hexdigest()] = raw
            entry["metadata"]["coverage"] = evidence_reference(coverage, raw)
            entry["metadata"]["last_attempt"]["same_as_metadata"].remove("coverage")
            entry["metadata"]["last_attempt"]["coverage"] = copy.deepcopy(coverage)

        mutations = [on_object(change) for change in (
            flipped_byte, missing_object, size_mismatch, duplicate_key, nan_value, not_canonical, wrong_type,
            wrong_items, extra_key, wrong_schema)] + [misplaced_reference]
        for mutate in mutations:
            with self.subTest(mutation=mutate.__name__):
                result = self.chain(government_completion_entry(), day="2023-01-11", mutate=mutate)
                for stage, item in result.items():
                    self.assertNotEqual(item["status"], "available", stage)
                    self.assertFalse(item["source_available_complete"], stage)
                    self.assertFalse(item["completion_evidence_valid"], stage)
                    self.assertIn(EVIDENCE_UNRESOLVED_PREFIX, json.dumps(item["completion_reasons"]), stage)
        # the untampered references prove the state (control)
        control = self.chain(government_completion_entry(), day="2023-01-11")
        self.assertTrue(all(item["completion_state"] == "source_unavailable" for item in control.values()))

    def test_view_shares_values_and_keeps_attempt_only_fields(self):
        entry = government_completion_entry()
        metadata = entry["metadata"]
        self.assertIs(resolved_last_attempt(metadata), metadata["last_attempt"])        # v1: nothing to do
        self.assertIs(published_entry_view(entry), entry)
        metadata["prior_gaps"] = [dict(date="2023-01-10", kind="unknown_gap")]           # never copied by the producer
        metadata["last_attempt"].pop("source_receipts")
        # an absent, unlisted field: the attempt had none, nothing is restored
        self.assertIs(resolved_last_attempt(metadata), metadata["last_attempt"])
        metadata["last_attempt"]["same_as_metadata"] = ["source_receipts"]
        before = copy.deepcopy(metadata)
        resolved = resolved_last_attempt(metadata)
        self.assertIsNot(resolved, metadata["last_attempt"])
        self.assertIs(resolved["source_receipts"], metadata["source_receipts"])
        self.assertNotIn("same_as_metadata", resolved)
        self.assertEqual(last_attempt_unresolved(resolved), ())
        self.assertNotIn("prior_gaps", resolved)
        self.assertEqual((resolved["status"], resolved["warnings"]), ("SUCCESS", []))
        self.assertEqual(metadata, before)                                                # the input is not changed
        view = published_entry_view(entry)
        self.assertIs(published_entry_view(view), view)                                  # idempotent
        self.assertIsNone(resolved_last_attempt(dict(metadata, last_attempt=None)))
        self.assertEqual(resolved_last_attempt(dict(metadata, last_attempt="x")), "x")

    def test_bounded_producer_evidence_is_identical_for_every_head_shape(self):
        entry = government_completion_entry(status="PARTIAL")
        entry["route_id"] = "finmind_trading_daily_report"
        entry["metadata"].update(reason="backfill_pending", gaps=[dict(date="2026-09-25", reason="pending")])
        entry["metadata"]["last_attempt"].update(reason="backfill_pending",
                                                 gaps=[dict(date="2026-09-25", reason="pending")])
        stored = compact_entry(entry, {}, inline_max=64 * 1024)
        self.assertIn("gaps", stored["metadata"]["last_attempt"]["same_as_metadata"])
        evidence = [_bounded_producer_evidence(shaped, source_id="s", snapshot_id="n", root="r")
                    for shaped in (entry, stored)]
        self.assertEqual(evidence[0]["metadata"]["last_attempt"]["gaps_count"], 1)
        self.assertEqual(evidence[0]["metadata"]["last_attempt"]["reason"], "backfill_pending")
        self.assertEqual(evidence[0], evidence[1])


class FullEvidenceConflictTests(FixtureMixin, unittest.TestCase):
    BASE = dict(route_id="finmind_institutional", status="SUCCESS", path="x.json", sha256="0" * 64, size=1,
                record_count=1, selected_ref=dict(path="x.json", sha256="0" * 64, size=1))

    def test_conflict_after_sixteen_gaps_is_detected_and_the_report_stays_bounded(self):
        gaps = [f"missing:2026-01-{day:02d}" for day in range(1, 21)] + ["unit_conflict"]
        result = published_completion("finmind_institutional", dict(self.BASE, metadata=dict(gaps=gaps)))
        self.assertEqual(result["completion_state"], "conflict")
        self.assertEqual(result["status"], "partial")
        self.assertIs(result["model_usable"], False)
        self.assertEqual(len(result["completion_reasons"]["published"]["gaps"]), 16)
        self.assertLessEqual(len(result["producer_gaps"]), 16)

    def test_deeply_nested_and_long_conflicts_are_detected(self):
        inner = {"status": "value_conflict"}
        for _ in range(5):
            inner = {"coverage": inner}
        self.assertEqual(published_completion("finmind_institutional", dict(
            self.BASE, metadata=dict(coverage=inner)))["completion_state"], "conflict")
        attempt = dict(status="SUCCESS", warnings=["note"] * 30 + ["duplicate_natural_key_conflict"])
        self.assertEqual(published_completion("finmind_institutional", dict(
            self.BASE, metadata=dict(last_attempt=attempt)))["completion_state"], "conflict")
        # the resolved / status semantics are unchanged: a resolved status is not a conflict
        self.assertEqual(published_completion("finmind_institutional", dict(self.BASE, metadata=dict(
            gaps=["note"] * 20 + [dict(status="resolved", reason="unit_conflict")])))["completion_state"], "unknown")

    def test_a_late_conflict_reaches_the_window_and_the_dataset_status(self):
        entry = government_completion_entry(status="PARTIAL")
        entry["route_id"] = "official_sector_observations"
        entry["metadata"] = dict(gaps=[dict(kind="missing", date=f"2026-09-{day:02d}") for day in range(1, 25)]
                                 + [dict(kind="unit_conflict", date=DAY)],
                                 last_attempt=dict(status="SUCCESS", warnings=[]))
        sources = self.sources(entry, [dict(stock_id="2330", date=DAY, value=2)])
        selected = _select(sources, entry["route_id"], "2330", DAY, DAY)
        window = _route_window(selected, dict(start_date=DAY, end_date=DAY, before_count=0,
                                              field_map=dict(stock_field="stock_id", date_field="date")))
        for item in (selected, window):
            self.assertEqual(item["completion_state"], "conflict")
            self.assertEqual(item["status"], "partial")
            self.assertFalse(item["source_available_complete"])
            self.assertLessEqual(len(item["producer_gaps"]), 16)

    def test_large_producer_metadata_keeps_the_window_proof(self):
        # Since 2026-10-04 the producer publishes per-request receipts and larger coverage maps; the re-scoped
        # proof keeps exactly what the full read proved, whatever their size (formerly a 32 KiB private copy).
        for entry, state, day in ((government_completion_entry(), "source_unavailable", "2023-01-11"),
                                  (official_no_data_entry(), "confirmed_no_data", "2026-09-30")):
            extra = {f"extra_metric_{index:03d}": 0 for index in range(80)}
            receipts = [dict(date=f"2021-{index % 12 + 1:02d}-01", note="x" * 64) for index in range(3000)]
            entry["metadata"]["last_attempt"]["coverage"].update(extra)
            entry["metadata"]["coverage"].update(extra)
            entry["metadata"]["history_receipts"] = receipts
            with self.subTest(state=state):
                sources = self.sources(entry)
                selected = _select(sources, entry["route_id"], "2330", day, day)
                window = _route_window(selected, dict(start_date=day, end_date=day, before_count=0,
                                                      field_map=dict(stock_field="stock_id", date_field="date")))
                self.assertNotIn("completion_proof_budget_exceeded", selected["gaps"])
                self.assertEqual(len(selected["_completion_inputs"]), 1)
                private = selected["_completion_inputs"][0]["entry"]["metadata"]
                self.assertEqual(len(private["history_receipts"]), 3000)
                for item in (selected, window):
                    self.assertEqual(item["completion_state"], state)
                    self.assertTrue(item["source_available_complete"])
                    self.assertEqual((item["completion_start_date"], item["completion_end_date"]), (day, day))


class ReferenceDetectionTests(unittest.TestCase):
    def test_only_well_formed_evidence_references_resolve(self):
        good = evidence_reference([1, 2])
        self.assertTrue(sds.is_evidence_reference(good))
        self.assertEqual(sds._evidence_reference_fields(good)[1:], (len(canonical([1, 2])), "list", 2))
        self.assertTrue(sds.is_evidence_reference({"$evidence": "x"}))                    # marked: fails closed
        for value in ([], "ref", None, dict(sha256="a" * 64, size=1, ref="metadata.gaps"), {"status": "SUCCESS"}):
            self.assertFalse(sds.is_evidence_reference(value), value)
        for broken in (dict(good, size=True), dict(good, size=-1), dict(good, items=1.0), dict(good, type="str"),
                       dict(good, sha256="A" * 64), dict(good, extra=1),
                       {key: value for key, value in good.items() if key != "items"}):
            with self.assertRaises(sds.PublishedEvidenceError):
                sds._evidence_reference_fields(broken)

    def test_the_old_cd_final_shapes_are_plain_attempt_values(self):
        # single contract: {sha256,size,ref} values and deduplicated_fields are no longer markers; an attempt
        # holding them is read as is (it does not prove anything it does not hold)
        entry = government_completion_entry()
        attempt = entry["metadata"]["last_attempt"]
        attempt.pop("source_receipts")
        attempt["deduplicated_fields"] = ["source_receipts"]
        attempt["coverage"] = dict(sha256="a" * 64, size=1, ref="metadata.coverage")
        self.assertIs(resolved_last_attempt(entry["metadata"]), attempt)
        proof = published_completion("finmind_government_bank", entry, verified_object=True, query_empty=True,
                                     global_empty=True, start_date="2023-01-11", end_date="2023-01-11")
        self.assertNotEqual(proof["completion_state"], "source_unavailable")


if __name__ == "__main__":
    unittest.main()
