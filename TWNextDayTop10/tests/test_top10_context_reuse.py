"""Top10 load-scoped pin reuse; native pins/identity, synthetic calendar/query rows."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

SHARED_TESTS = Path(__file__).resolve().parents[2] / "programs" / "shared" / "tests"
if str(SHARED_TESTS) not in sys.path:
    sys.path.insert(0, str(SHARED_TESTS))

import test_stock_daily_selected_context_performance as fixtures  # noqa: E402

case = fixtures.case
contract = fixtures.contract
published = fixtures.published


class _Selected:
    def __init__(self, rows, root, route, orders):
        self._rows = rows
        self.orders = orders
        self.cache_key = "top10-fixture-" + route
        self.predicate = {"route": route}
        self.completion_path = root / (route + ".completion")
        self.index_manifest_path = root / (route + ".index")
        for path in (self.completion_path, self.index_manifest_path):
            path.write_bytes(route.encode("utf-8"))
        self.lineage = {"synthetic": True}
        self.metrics = {"rows": len(rows)}

    def iter_rows(self, *, order="source"):
        self.orders.append(order)
        if order == "stock_shard":
            self.metrics.update(iteration_order=order, stock_sequence_preserved=True,
                                source_sequence_preserved=False)
        return iter(copy.deepcopy(self._rows))

    def close(self):
        pass


@pytest.fixture
def top10_case(case, monkeypatch):
    # Reuse the shared fixture's native immutable reader and identity proof.
    # Calendar assembly and route queries are synthetic; no production data is read.
    axis = pd.bdate_range(end=case.day, periods=253).strftime("%Y-%m-%d").tolist()
    case.scope["body"]["markets"]["TWSE"]["historical_sessions"] = axis
    case.events, case.iteration_orders, case.query_hook = [], [], None
    calendar_fixture = contract._published_calendar_axes_from_sources

    def calendar(*args, **kwargs):
        _, evidence = calendar_fixture(*args, **kwargs)
        return {"TWSE": list(axis)}, evidence

    def query_rows(root, artifact, route, *, stock_ids, start_date, end_date, **kwargs):
        case.events.append("query:" + route)
        if case.query_hook is not None:
            case.query_hook()
        rows = []
        for day in axis:
            if "2330" not in stock_ids or not start_date <= day <= end_date:
                continue
            row = {"stock_id": "2330", "date": day, "close": 100.0}
            if route == "finmind_price_adj":
                row.update(open=100.0, spread=0.0, Trading_Volume=1000,
                           _shared_collected_at="2026-10-17T00:00:00+00:00")
            else:
                assert route == "official_market_daily"
                row.update(volume=1000, market="TWSE")
            rows.append(row)
        return _Selected(rows, case.root, route, case.iteration_orders)

    monkeypatch.setattr(contract, "_published_calendar_axes_from_sources", calendar)
    monkeypatch.setitem(sys.modules, "stock_query_projection",
                        SimpleNamespace(query_published_rows=query_rows))
    return case


def _load(case, *, enabled=True, reference=None, **kwargs):
    return contract.load_c0_published_panel(
        None, context_or_sources=case.reference if reference is None else reference,
        consumer_id="top10", runtime_binding=case.active,
        top10_single_build=enabled, **kwargs,
    )


def _trace_native(case, monkeypatch):
    calls = {"snapshot": 0, "readers": [], "proofs": []}
    native_snapshot = published.PublishedSources.snapshot_descriptor
    native_validate = published.PublishedSources.validate_pinned_context

    def snapshot(self, *args, **kwargs):
        calls["snapshot"] += 1
        return native_snapshot(self, *args, **kwargs)

    def validate(self, descriptor):
        case.events.append("pin")
        calls["readers"].append(self)
        proof = native_validate(self, descriptor)
        calls["proofs"].append(proof)
        return proof

    monkeypatch.setattr(published.PublishedSources, "snapshot_descriptor", snapshot)
    monkeypatch.setattr(published.PublishedSources, "validate_pinned_context", validate)
    return calls


def _without_iteration_disclosure(value):
    if isinstance(value, dict):
        return {key: ({name: item for name, item in nested.items()
                       if name not in {"iteration_order", "stock_sequence_preserved",
                                       "source_sequence_preserved"}}
                      if key == "query_metrics" else _without_iteration_disclosure(nested))
                for key, nested in value.items()}
    if isinstance(value, list):
        return [_without_iteration_disclosure(item) for item in value]
    return value


def test_top10_panel_matches_default_with_two_native_checks_and_snapshots(top10_case, monkeypatch):
    expected = _load(top10_case, enabled=False)
    assert top10_case.iteration_orders == ["source", "source"]
    top10_case.events.clear()
    top10_case.iteration_orders.clear()
    calls = _trace_native(top10_case, monkeypatch)
    actual = _load(top10_case)
    try:
        assert actual.keys() == expected.keys()
        pd.testing.assert_frame_equal(actual["panel"], expected["panel"], check_exact=True)
        for key in actual.keys() - {"panel", "sources"}:
            assert _without_iteration_disclosure(actual[key]) == _without_iteration_disclosure(expected[key]), key
        assert top10_case.iteration_orders == ["stock_shard", "stock_shard"]
        for binding in actual["source_binding"]["query_bindings"].values():
            metrics = binding["query_metrics"]
            assert metrics["iteration_order"] == "stock_shard"
            assert metrics["stock_sequence_preserved"] is True
            assert metrics["source_sequence_preserved"] is False
        assert actual["sources"] is not expected["sources"]
        assert actual["coverage"]["eligible"] == 1
        assert len(actual["panel"]) == 253
        assert calls["snapshot"] == len(calls["proofs"]) == 2
        assert calls["readers"] == [actual["sources"]] * 2
        assert top10_case.events == ["pin", "query:finmind_price_adj",
                                     "query:official_market_daily", "pin"]
        assert all(proof["verified_pin_paths"] == (str(top10_case.archive),)
                   for proof in calls["proofs"])
        assert all(proof["pin_bytes_read"] == top10_case.archive.stat().st_size
                   for proof in calls["proofs"])
        assert all(proof["repinned"] is False and proof["descriptor_refreshed"] is False
                   and proof["large_artifacts_verified"] is False for proof in calls["proofs"])
    finally:
        expected["sources"].close()
        actual["sources"].close()


@pytest.mark.parametrize(("consumer", "selected", "expected"), [
    ("top10", False, 3), ("single", False, 3), ("single", True, 2), ("mix", False, 3),
])
def test_public_context_keeps_existing_consumer_checks(case, monkeypatch, consumer, selected, expected):
    calls = []
    native_validate = published.PublishedSources.validate_pinned_context

    def validate(self, descriptor):
        calls.append(self)
        return native_validate(self, descriptor)

    monkeypatch.setattr(published.PublishedSources, "validate_pinned_context", validate)
    loaded = contract.load_c0_published_context(case.reference, consumer_id=consumer,
        runtime_binding=case.active, selected_single_build=selected)
    try:
        assert len(calls) == expected
        assert all(reader is loaded["sources"] for reader in calls)
    finally:
        loaded["sources"].close()


@pytest.mark.parametrize("consumer", ["single", "mix", None])
def test_top10_opt_in_rejects_other_consumers(top10_case, consumer):
    with pytest.raises((TypeError, ValueError)):
        contract.load_c0_published_panel(None, context_or_sources=top10_case.reference,
            consumer_id=consumer, runtime_binding=top10_case.active, top10_single_build=True)
    assert not top10_case.events


@pytest.mark.parametrize("flag", [1, "true"])
def test_top10_opt_in_requires_boolean(top10_case, flag):
    with pytest.raises((TypeError, ValueError)):
        _load(top10_case, enabled=flag)
    assert not top10_case.events


def test_top10_opt_in_rejects_a_reader_pinned_through_mutable_head(top10_case):
    reader = published.PublishedSources([{"kind": "manifest_json", "root": str(top10_case.root),
        "manifest_path": str(top10_case.current), "source_id": "shared_market_data"}])
    try:
        with pytest.raises(ValueError):
            _load(top10_case, reference=reader)
        assert not top10_case.events
    finally:
        reader.close()


def test_top10_entry_repin_stops_before_sidecars(top10_case, monkeypatch):
    native_validate = published.PublishedSources.validate_pinned_context

    def remove_archive(self, descriptor):
        top10_case.archive.unlink()
        return native_validate(self, descriptor)

    monkeypatch.setattr(published.PublishedSources, "validate_pinned_context", remove_archive)
    monkeypatch.setattr(contract, "_load_application_scope",
                        lambda *a, **k: pytest.fail("entry repin reached sidecars"))
    with pytest.raises(ValueError):
        _load(top10_case)
    assert not top10_case.events


@pytest.mark.parametrize("change", ["bytes", "missing_archive", "catalog", "fingerprint"])
def test_top10_exit_rejects_pin_change_during_projection(top10_case, monkeypatch, change):
    calls = _trace_native(top10_case, monkeypatch)

    def change_pin():
        top10_case.query_hook = None
        reader = calls["readers"][0]
        if change == "bytes":
            top10_case.archive.write_bytes(top10_case.archive.read_bytes() + b" ")
        elif change == "missing_archive":
            top10_case.archive.unlink()
        elif change == "catalog":
            reader._sources[0]["entries"]["finmind_price_adj"][0][1]["status"] = "PARTIAL"
        else:
            reader.fingerprint = "c" * 64

    top10_case.query_hook = change_pin
    with pytest.raises(ValueError):
        _load(top10_case)
    assert top10_case.query_hook is None
    assert top10_case.events[0] == top10_case.events[-1] == "pin"


@pytest.mark.parametrize("reference", ["identity_ref", "raw_ref", "receipt_ref"])
def test_top10_identity_sidecar_bytes_are_still_verified(top10_case, reference):
    (top10_case.root / getattr(top10_case, reference)["ref"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="current_identity_content_digest_or_size_mismatch"):
        _load(top10_case)
    assert not top10_case.events


@pytest.mark.parametrize("change", ["schema", "classification"])
def test_top10_identity_schema_and_classification_are_not_bypassed(top10_case, change):
    if change == "schema":
        top10_case.identity["schema"] = "invalid-identity-schema"
    else:
        top10_case.identity["records"][0]["is_ordinary_share"] = False
    top10_case.publish()
    with pytest.raises(ValueError):
        _load(top10_case)
    assert not top10_case.events


@pytest.mark.parametrize("part", ["scope", "calendar"])
def test_top10_native_scope_and_calendar_content_gates_remain(top10_case, monkeypatch, part):
    if part == "scope":
        monkeypatch.setattr(contract, "_load_application_scope", top10_case.native_scope)
        reference = top10_case.scope_ref
    else:
        monkeypatch.setattr(contract, "_published_calendar_axes_from_sources", top10_case.native_calendar)
        reference = top10_case.calendar_ref
    # The inherited small sidecar intentionally has invalid domain/schema content.
    with pytest.raises(ValueError):
        _load(top10_case)
    (top10_case.root / reference["ref"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="current_identity_content_digest_or_size_mismatch"):
        _load(top10_case)


def test_top10_each_load_rechecks_sidecars_and_keeps_its_immutable_head(top10_case):
    first = _load(top10_case)
    try:
        advanced = json.loads(top10_case.current.read_text(encoding="utf-8"))
        advanced["run_id"] = "new-current-not-adopted"
        top10_case.current.write_bytes(contract._canonical(advanced))
        second = _load(top10_case)
        try:
            assert second["sources"] is not first["sources"]
            assert second["descriptor"]["sources"] == first["descriptor"]["sources"]
        finally:
            second["sources"].close()
    finally:
        first["sources"].close()
    (top10_case.root / top10_case.identity_ref["ref"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="current_identity_content_digest_or_size_mismatch"):
        _load(top10_case)
