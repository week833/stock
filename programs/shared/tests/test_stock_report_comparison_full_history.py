"""Native offline fixtures only: no models, live sources, services or network."""
import hashlib
import json
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stock_cache_lifecycle as lifecycle
import stock_report_comparison as comparison
import stock_report_comparison_full_history as history
from test_stock_report_comparison import central, revenue


@pytest.fixture
def native_case(tmp_path, monkeypatch):
    query = history._query()
    registry = tmp_path / "native"
    monkeypatch.setattr(lifecycle, "source_registry", lambda root: registry)
    monkeypatch.setattr(query, "prune_query_cache", lambda *a, **k: pytest.fail("No GC, including native implicit GC"))
    monkeypatch.setattr(history.runtime, "_load_context", lambda **k: pytest.fail("No model context"))
    routes = {route: [dict(stock_id="2376", date="20261022", value=1)] for route in comparison._ROUTES}
    routes["finmind_month_revenue"] = [dict(revenue(year, 9, year), stock_id=stock)
                                       for year in range(2021, 2027) for stock in ("2376", "2330")]
    routes["finmind_per"] = [dict(stock_id="2376", date=day, per=value) for day, value in
                             [("20261022", 20), ("unknown", 999), ("20210101", 10), ("20261022", 30)]]
    routes["tdcc_share_distribution_raw"] = [dict(證券代號="2376", 資料日期="20261016", 持股分級="15")]
    root, head = central(tmp_path, routes)
    options = dict(central_root=root, head_sha=head, state_root=tmp_path / "state", consumer_temp_root=tmp_path / "consumer")
    return root, head, routes, registry, query, options


def test_native_complete_all_history_source_order_and_no_gc(native_case, monkeypatch):
    root, head, routes, registry, query, options = native_case
    real_commit, real_query = query._commit_shard_objects, query.query_published_rows
    commits, queries, leases = [], [], []
    real_lease = lifecycle.CacheLease

    class Lease(real_lease):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            leases.append(self)

    def commit(path, shards):
        assert str((registry / "locks/lifecycle.lock").resolve()) in lifecycle._CATALOG_THREADS.held
        commits.append(path)
        return real_commit(path, shards)

    def read(root, entry, route, **kwargs):
        assert kwargs["allow_build"] is False and kwargs["parent_identity"] == head
        assert kwargs["stock_ids"] is kwargs["start_date"] is kwargs["end_date"] is kwargs["columns"] is None
        queries.append(route)
        return real_query(root, entry, route, **kwargs)

    monkeypatch.setattr(lifecycle, "CacheLease", Lease)
    monkeypatch.setattr(query, "_commit_shard_objects", commit)
    monkeypatch.setattr(query, "query_published_rows", read)
    result = history.prepare_once(**options)
    assert result["status"] == "ready" and result["ready"] is True
    assert len(commits) == len(queries) == 8
    assert result["raw_fallback_routes"] == ["tdcc_share_distribution_raw"]
    assert len(result["accelerated_routes"]) == 7
    assert result["gc"]["no_delete"] and not result["gc"]["scan_performed"]
    for item in result["routes"]:
        expected = hashlib.sha256(b"".join(history.runtime._canonical(row) + b"\n" for row in routes[item["route_id"]])).hexdigest()
        assert item["source_order_rows_sha256"] == expected
        assert item["source_rows"] == item["readback_rows"] == len(routes[item["route_id"]])
        assert item["readback_metrics"]["source_bytes_read"] == 0
        assert item["commit_lock_seconds"] is None and not item["lock_timing_decomposed"]
        assert item["built"] and not item["validation_reused"]
    revenue_item = next(item for item in result["routes"] if item["route_id"] == "finmind_month_revenue")
    assert revenue_item["minimum_date"] == "2021-10-01" and revenue_item["stock_count"] == 2
    assert all(lease.path is None for lease in leases)
    parent = leases[0].value
    assert len(parent["physical_refs"]) == 9 and len(parent["cache_keys"]) == 8
    assert parent["physical_refs"][0]["ref"]["ref"] == "_manifests/" + head + ".json"
    assert history._previous(options["state_root"] / "latest.json")["ready"] is True


def test_same_physical_sha_across_heads_reuses_native_seal_and_validation(native_case, monkeypatch):
    root, head, routes, registry, query, options = native_case
    first = history.prepare_once(**options)
    document = json.loads((root / "_manifests" / (head + ".json")).read_bytes())
    document["revision"] = 2
    raw = json.dumps(document).encode("utf-8")
    second_head = hashlib.sha256(raw).hexdigest()
    (root / "_manifests" / (second_head + ".json")).write_bytes(raw)
    monkeypatch.setattr(query, "_build_projection", lambda *a: pytest.fail("Unchanged artifacts must not rebuild"))
    monkeypatch.setattr(query, "query_published_rows", lambda *a, **k: pytest.fail("Verified warm check must not scan all shards"))
    second = history.prepare_once(**dict(options, head_sha=second_head))
    assert second["ready"] and second["head_sha256"] == second_head
    assert [r["cache_key"] for r in first["routes"]] == [r["cache_key"] for r in second["routes"]]
    assert all(r["validation_reused"] and not r["built"] for r in second["routes"])


def test_interrupted_route_resumes_without_rebuilding_completed_route(native_case, monkeypatch):
    root, head, routes, registry, query, options = native_case
    original = history._prepare_route
    calls = []
    def interrupted(root, entry, *args):
        calls.append(entry["route_id"])
        if len(calls) == 2:
            raise OSError("fixture interruption")
        return original(root, entry, *args)
    monkeypatch.setattr(history, "_prepare_route", interrupted)
    failed = history.prepare_once(**options)
    assert failed["status"] == "failed" and len(failed["routes"]) == 1
    monkeypatch.setattr(history, "_prepare_route", original)
    result = history.prepare_once(**options)
    assert result["ready"] and result["routes"][0]["validation_reused"]
    assert all(not r["validation_reused"] for r in result["routes"][1:])


@pytest.mark.parametrize("tamper", ["index", "state"])
def test_seal_or_state_tamper_fails_closed_without_rebuild(native_case, monkeypatch, tamper):
    root, head, routes, registry, query, options = native_case
    first = history.prepare_once(**options)
    path = (registry / "indexes" / (first["routes"][0]["cache_key"] + ".json") if tamper == "index"
            else options["state_root"] / "latest.json")
    raw = path.read_bytes()
    path.write_bytes(raw.replace(b'"schema":', b'"schemX":', 1))
    monkeypatch.setattr(query, "_build_projection", lambda *a: pytest.fail("Integrity errors cannot rebuild"))
    result = history.prepare_once(**options)
    assert result["status"] == "failed" and result["error"]["type"] == "ProjectionIntegrityError"
    assert not result["ready"]


def test_pause_before_next_route_does_not_scan_or_build(native_case, monkeypatch):
    root, head, routes, registry, query, options = native_case
    options["state_root"].mkdir()
    (options["state_root"] / "pause_requested.json").write_text('{}', encoding="utf-8")
    monkeypatch.setattr(query, "_build_projection", lambda *a: pytest.fail("Pause must stop before source scan"))
    result = history.prepare_once(**options)
    assert result["status"] == "deferred" and result["routes"] == []


def test_native_writer_busy_is_deferred_without_state_overwrite(native_case, monkeypatch):
    root, head, routes, registry, query, options = native_case
    first = history.prepare_once(**options)
    path = options["state_root"] / "latest.json"
    raw = path.read_bytes()
    @contextmanager
    def busy(*args, **kwargs):
        raise query.ProjectionLockTimeoutError("fixture busy")
        yield
    monkeypatch.setattr(query, "_writer_lock", busy)
    assert history.prepare_once(**options)["status"] == "deferred"
    assert path.read_bytes() == raw


@pytest.mark.parametrize("error", ["UnsupportedProjectionError", "ProjectionIntegrityError"])
def test_only_unsupported_route_continues_with_explicit_raw_fallback(native_case, monkeypatch, error):
    root, head, routes, registry, query, options = native_case
    original = history._prepare_route
    failed_route = list(comparison._ROUTES)[1]
    calls = []
    def prepare(root, entry, *args):
        calls.append(entry["route_id"])
        if entry["route_id"] == failed_route:
            raise getattr(query, error)("fixture native incompatibility")
        return original(root, entry, *args)
    monkeypatch.setattr(history, "_prepare_route", prepare)
    result = history.prepare_once(**options)
    if error == "UnsupportedProjectionError":
        assert not result["ready"] and result["status"] == "partial_with_raw_fallback"
        assert calls == list(comparison._ROUTES) and result["sealed_verified_routes"] == 7
        failed = result["routes"][1]
        assert failed["status"] == "unsupported" and failed["native_seal_verified"] is False
        assert failed_route in result["raw_fallback_routes"]
    else:
        assert not result["ready"] and result["status"] == "failed"
        assert calls == list(comparison._ROUTES)[:2]


@pytest.mark.parametrize("kind", ["agree", "undated", "disagree", "date_missing"])
def test_ambiguous_date_mapping_requires_full_source_equivalence(native_case, monkeypatch, tmp_path, kind):
    root, head, routes, registry, query, options = native_case
    rows = [dict(stock_id="2376", date="20260131", period_end="2026-01-31", report_date="2026-01-31", value=1)]
    if kind == "undated":
        rows.append(dict(stock_id="2376", value=2))
    elif kind == "disagree":
        rows.append(dict(stock_id="2376", date="20260131", report_date="2026-02-01", value=2))
    elif kind == "date_missing":
        rows.append(dict(stock_id="2376", report_date="2026-01-31", value=2))
    route = "finmind_cash_flows_statement"
    root, head = central(tmp_path / "multiple_dates", {**routes, route: rows})
    result = history.prepare_once(**dict(options, central_root=root, head_sha=head))
    item = next(row for row in result["routes"] if row["route_id"] == route)
    if kind in {"agree", "undated"}:
        assert result["ready"] and item["source_rows"] == item["readback_rows"] == len(rows)
        assert item["field_map"] == {"stock_field": "stock_id", "date_field": "date"}
        proof = history._previous(Path(item["date_alias_proof"]["path"]))
        assert proof["safe_mapping"] and proof["full_source_hash_size_verified"]
        assert proof["source_order_rows_sha256"] == item["source_order_rows_sha256"]
        entry = next(e for e in comparison._head(root, head)[1]["datasets"] if e["route_id"] == route)
        if kind == "undated":
            assert item["missing_date_rows"] == 1
            with pytest.raises(query.UnsupportedProjectionError, match="date_filter_not_lossless"):
                query.query_published_rows(root, entry, route, parent_identity=head, allow_build=False,
                                           start_date="2026-01-01", end_date="2026-02-01")
        else:
            selected = query.query_published_rows(root, entry, route, parent_identity=head, allow_build=False,
                                                  start_date="2026-01-01", end_date="2026-02-01")
            try:
                assert list(selected.iter_rows()) == rows
            finally:
                selected.close()
    else:
        assert not result["ready"] and result["status"] == "partial_with_raw_fallback"
        assert item["status"] == "unsupported" and item["native_seal_verified"] is False
        assert not (registry / "complete" / (item["cache_key"] + ".json")).exists()


def test_existing_native_date_mapping_is_not_replaced(native_case, tmp_path):
    root, head, routes, registry, query, options = native_case
    route = "finmind_cash_flows_statement"
    rows = [dict(stock_id="2376", date="20260131", period_end="2025-12-31", value=1)]
    root, head = central(tmp_path / "existing_mapping", {**routes, route: rows})
    entry = next(e for e in comparison._head(root, head)[1]["datasets"] if e["route_id"] == route)
    prepared = query._prepare_descriptor(root, entry, route, head, None)
    key = query._cache_key(prepared)
    query._build_projection(prepared, registry, key, query._physical_identity(prepared),
                            {"stock_field": "stock_id", "date_field": "period_end"}, 2, 1024)
    raw = (registry / "indexes" / (key + ".json")).read_bytes()
    result = history.prepare_once(**dict(options, central_root=root, head_sha=head))
    item = next(row for row in result["routes"] if row["route_id"] == route)
    assert result["ready"] and item["field_map"]["date_field"] == "period_end"
    assert not item["built"] and item["date_alias_proof"] is None
    assert (registry / "indexes" / (key + ".json")).read_bytes() == raw
