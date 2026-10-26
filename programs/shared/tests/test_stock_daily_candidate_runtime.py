"""Isolated orchestration fixtures. Do not run until the delegated QA phase."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock_daily_candidate_runtime as runtime


@pytest.fixture
def sealed_case(monkeypatch, tmp_path):
    routes = sorted(runtime.CORE_ROUTES | {"finmind_month_revenue", "finmind_per", "finmind_gold_price"})
    plan = [{"route_id": route, "name_zh": "測試來源" + str(i),
        "usage": "not-selected" if route == "finmind_gold_price" else "used",
        "reason_zh": "測試事前選擇規則"} for i, route in enumerate(routes)]
    days = pd.bdate_range("2024-11-01", "2027-03-01").strftime("%Y-%m-%d").tolist()
    ends = {"TWSE": "2026-10-16", "TPEX": "2026-10-15"}
    records = [{"stock_id": "2330", "market": "TWSE", "stock_name": "台積電", "industry": "半導體"},
        {"stock_id": "6488", "market": "TPEX", "stock_name": "環球晶", "industry": "半導體"}]
    px, board = [], []
    for stock in records:
        for i, day in enumerate(days):
            if day > "2026-10-16":
                continue
            px.append({"stock_id": stock["stock_id"], "date": day, "open": 100 + i,
                "close": 101 + i, "spread": 1, "max": 103 + i, "min": 99 + i,
                "Trading_Volume": 2000, "Trading_money": 123456,
                "Trading_turnover": 42, "_shared_collected_at": "2026-10-17T06:00:00+08:00"})
            board.append({"stock_id": stock["stock_id"], "date": day, "market": stock["market"],
                "close": 101 + i, "volume": 2000})
    payloads = {"finmind_price_adj": px, "official_market_daily": board,
        "finmind_month_revenue": [{"stock_id": "2330", "date": "2026-09-30", "revenue": 9}],
        "finmind_per": [{"stock_id": "2330", "date": "2026-10-15", "PER": 20}]}
    calls, streams, feature_calls, closed_results = [], [], [], []
    proof = tmp_path / "projection-proof.json"
    proof.write_text('{"sealed":true}', encoding="utf-8")

    class Sources:
        fingerprint = "a" * 64

        def snapshot_descriptor(self):
            return {"sources": [{"root": str(runtime.PUBLISHED_ROOT), "manifest_sha256": "a" * 64}],
                "descriptor_sha256": "b" * 64}

        def projection_artifacts(self, route):
            return [{"root": str(runtime.PUBLISHED_ROOT), "artifact_descriptor": {
                "route_id": route, "path": "published/" + route + ".json", "sha256": "c" * 64,
                "size": 123, "status": "PARTIAL" if route == "finmind_month_revenue" else "SUCCESS"},
                "source_identity": {"snapshot_id": "a" * 64}, "parent_identity": "a" * 64}]

        def iter_artifact_rows(self, route):
            streams.append(route)
            artifact = SimpleNamespace(content_sha256="c" * 64, snapshot_id="a" * 64, status="PARTIAL")
            yield artifact, iter(payloads.get(route, []))

    def query(root, artifact, route, **kwargs):
        calls.append((route, kwargs))
        rows = [row for row in payloads.get(route, []) if runtime._row_selected(row, route,
            kwargs["stock_ids"] or [], kwargs["start_date"], kwargs["end_date"])]
        result = SimpleNamespace(cache_key="fixture-" + route, closed=False,
            predicate={key: kwargs[key] for key in ("stock_ids", "start_date", "end_date")},
            lineage={"parent": "a" * 64}, metrics={"cache_hit": True}, completion_path=proof,
            index_manifest_path=proof)

        def iter_rows():
            assert result.closed is False
            yield from rows

        def close():
            if not result.closed:
                result.closed = True
                closed_results.append(route)

        result.iter_rows, result.close = iter_rows, close
        return result

    def features(panel, axes, **kwargs):
        feature_calls.append({"panel": panel.copy(), "axes": copy.deepcopy(axes), **kwargs})
        frame = panel[["stock_id", "market", "session_date", "adjusted_close"]].copy()
        frame["fixture_feature"] = 1.5
        return {"features": frame, "labels": pd.DataFrame(), "metadata": {
            "feature_schema": runtime.FEATURE_SCHEMA, "feature_order": ["fixture_feature"],
            "feature_order_by_horizon": {str(h): ["fixture_feature"] for h in kwargs["horizons"]},
            "feature_metadata": {"fixture_feature": {"label_zh": "測試特徵", "source_route": "finmind_price_adj"}},
            "horizons": list(kwargs["horizons"]), "asof_by_market": kwargs["asof_by_market"],
            "source_manifest": kwargs["source_manifest"]},
            "analysis_context": {"2330": {"fundamentals": [{"period_end": "2026-09-30", "revenue": 9}],
                "flows": [], "news": [], "global": [], "technical": []}}}

    feature_module = SimpleNamespace(SOURCE_CATALOG={route: {} for route in routes},
        select_source_plan=lambda horizons, industry=None: copy.deepcopy(plan),
        build_candidate_features=features, __file__=str(proof))
    monkeypatch.setitem(sys.modules, "stock_daily_feature_catalog", feature_module)
    import stock_query_projection as projection
    monkeypatch.setattr(projection, "query_published_rows", query)
    context = {"sources": Sources(), "market_sessions": {"TWSE": days, "TPEX": days},
        "analysis_asof_by_market": ends, "asof_by_market": ends,
        "current_identity": {"records": records, "binding": {"sha256": "d" * 64},
            "excluded": [{"stock_id": "0050", "reason": "ETF"}]},
        "calendar_evidence": {"calendar_verified_by_market": {"TWSE": True, "TPEX": True},
            "binding": {"sha256": "e" * 64}},
        "application_scope": {"binding": {"sha256": "f" * 64}, "capture_binding": {"sha256": "1" * 64}},
        "version_labels": []}
    return SimpleNamespace(context=context, calls=calls, streams=streams, feature_calls=feature_calls,
        payloads=payloads, plan=plan, root=tmp_path, proof=proof, closed_results=closed_results)


def load(case, **kwargs):
    return runtime.load_candidate_inputs(["2330", "6488"], context=case.context,
        cache_dir=case.root / "cache", **kwargs)


def test_selected_routes_are_batched_and_never_build_central_projection(sealed_case):
    result = load(sealed_case)
    assert len(sealed_case.calls) == 4
    assert len({route for route, _ in sealed_case.calls}) == 4
    assert all(call["stock_ids"] == ["2330", "6488"] for _, call in sealed_case.calls)
    assert all(call["allow_build"] is False for _, call in sealed_case.calls)
    assert all(call["end_date"] == {"2330": "2026-10-16", "6488": "2026-10-15"} for _, call in sealed_case.calls)
    assert all(Path(call["consumer_temp_root"]).is_relative_to(sealed_case.root) for _, call in sealed_case.calls)
    assert result["snapshot"]["head_sha256"] == "a" * 64
    assert not sealed_case.streams
    assert sealed_case.closed_results == [route for route, _ in sealed_case.calls]


def test_default_native_result_closes_on_integrity_failure_without_cache_or_stream(sealed_case, monkeypatch):
    import stock_query_projection as projection
    original = projection.query_published_rows

    def failing_query(*args, **kwargs):
        result = original(*args, **kwargs)
        original_rows = result.iter_rows

        def damaged_rows():
            iterator = original_rows()
            try:
                yield next(iterator)
                raise projection.ProjectionIntegrityError("fixture row integrity failure")
            finally:
                iterator.close()

        result.iter_rows = damaged_rows
        return result

    monkeypatch.setattr(projection, "query_published_rows", failing_query)
    with pytest.raises(projection.ProjectionIntegrityError, match="fixture row integrity"):
        load(sealed_case)
    assert sealed_case.closed_results == [route for route, _ in sealed_case.calls]
    assert len(sealed_case.closed_results) == 1 and not sealed_case.streams
    assert not list((sealed_case.root / "cache").rglob("sealed_candidate_rows/*/queries/*.json"))


def test_eighteen_months_uses_full_calendar_and_own_market_asof(sealed_case):
    result = load(sealed_case)
    assert result["panel"].groupby("market").session_date.max().to_dict() == {
        "TWSE": "2026-10-16", "TPEX": "2026-10-15"}
    starts = result["panel"].groupby("market").session_date.min().to_dict()
    assert starts["TWSE"] == "2025-04-16"
    assert starts["TPEX"] == "2025-04-15"
    assert all(item["expected_sessions"] > 253 for item in result["snapshot"]["window_by_market"].values())
    assert all(len(days) > 400 for days in sealed_case.feature_calls[0]["axes"].values())


def test_unknown_publication_time_stays_context_only_and_partial(sealed_case):
    result = load(sealed_case)
    source = next(item for item in result["source_manifest"] if item["route_id"] == "finmind_month_revenue")
    assert source["usage"] == "context-only"
    assert source["status"] == "PARTIAL"
    assert source["historical_available_time"] == "unknown"
    frame = sealed_case.feature_calls[0]["source_frames"]["finmind_month_revenue"]
    assert "available_time" not in frame
    assert "available_at" not in frame


def test_not_selected_is_not_missing_and_does_not_read_payload(sealed_case):
    result = load(sealed_case)
    source = next(item for item in result["source_manifest"] if item["route_id"] == "finmind_gold_price")
    assert source["availability"] == source["status"] == "not-selected"
    assert source["coverage"] is None
    assert "finmind_gold_price" not in {route for route, _ in sealed_case.calls}
    assert "finmind_gold_price" not in result["source_frames"]


def test_missing_selected_route_preserves_missing_without_invented_zero(sealed_case, monkeypatch):
    original = sealed_case.context["sources"].projection_artifacts
    monkeypatch.setattr(sealed_case.context["sources"], "projection_artifacts",
        lambda route: [] if route == "finmind_per" else original(route))
    result = load(sealed_case)
    source = next(item for item in result["source_manifest"] if item["route_id"] == "finmind_per")
    assert source["availability"] == "missing"
    assert "finmind_per" not in result["source_frames"]


def test_cold_projection_falls_back_once_per_route_with_sealed_hash(sealed_case, monkeypatch):
    import stock_query_projection as projection

    def unavailable(*args, **kwargs):
        raise projection.ProjectionUnavailableError("projection_not_built")

    monkeypatch.setattr(projection, "query_published_rows", unavailable)
    result = load(sealed_case, allow_full_scan=True)
    assert len(sealed_case.streams) == len(set(sealed_case.streams)) == 4
    assert all(item["reader"] == "sealed_stream_fallback" for item in result["snapshot"]["query_bindings"].values())
    assert result["panel"].session_date.min() == "2025-04-15"


def test_interactive_cache_miss_never_starts_full_market_scan(sealed_case, monkeypatch):
    import stock_query_projection as projection

    def unavailable(*args, **kwargs):
        raise projection.ProjectionUnavailableError("projection_not_built")

    monkeypatch.setattr(projection, "query_published_rows", unavailable)
    with pytest.raises(runtime.CandidateProjectionNotReady, match="先離線"):
        load(sealed_case)
    assert not sealed_case.streams


def test_training_cache_superset_serves_same_head_single_stock_without_scan(sealed_case, monkeypatch):
    import stock_query_projection as projection

    def unavailable(*args, **kwargs):
        raise projection.ProjectionUnavailableError("projection_not_built")

    monkeypatch.setattr(projection, "query_published_rows", unavailable)
    trained = load(sealed_case, allow_full_scan=True)
    assert len(sealed_case.streams) == 4
    sealed_case.streams.clear()
    monkeypatch.setattr(projection, "query_published_rows",
        lambda *args, **kwargs: pytest.fail("同快照子集合應直接使用已封存消費端快取"))
    reported = runtime.load_candidate_inputs(["2330"], context=sealed_case.context,
        cache_dir=sealed_case.root / "cache", horizons=[1, 63])
    assert not sealed_case.streams
    assert set(reported["panel"].stock_id) == {"2330"}
    assert reported["snapshot"]["head_sha256"] == trained["snapshot"]["head_sha256"]
    assert all(binding["reader"] == "consumer_sealed_cache" for binding in reported["snapshot"]["query_bindings"].values())


def test_changed_artifact_hash_cannot_reuse_old_consumer_rows(sealed_case, monkeypatch):
    import stock_query_projection as projection
    load(sealed_case, allow_full_scan=True)
    original = sealed_case.context["sources"].projection_artifacts

    def changed(route):
        items = copy.deepcopy(original(route))
        if route == "finmind_price_adj":
            items[0]["artifact_descriptor"]["sha256"] = "9" * 64
        return items

    def unavailable(*args, **kwargs):
        raise projection.ProjectionUnavailableError("projection_not_built")

    monkeypatch.setattr(sealed_case.context["sources"], "projection_artifacts", changed)
    monkeypatch.setattr(projection, "query_published_rows", unavailable)
    with pytest.raises(runtime.CandidateProjectionNotReady):
        runtime.load_candidate_inputs(["2330"], context=sealed_case.context, cache_dir=sealed_case.root / "cache")
    assert not sealed_case.streams


@pytest.mark.parametrize("read_only", [False, True])
def test_new_head_reuses_physical_cache_with_current_binding_and_immutable_origin(sealed_case, monkeypatch, read_only):
    import stock_query_projection as projection
    first = load(sealed_case, allow_full_scan=True)
    old_head, new_head, fingerprint = "a" * 64, "8" * 64, "9" * 64
    origins = {route: runtime._cache_document(Path(binding["consumer_cache"]["manifest_path"]))
        for route, binding in first["snapshot"]["query_bindings"].items()}
    cache = sealed_case.root / "cache"
    before = {str(path.relative_to(cache)): path.read_bytes() for path in cache.rglob("*") if path.is_file()}
    sources = sealed_case.context["sources"]
    original = sources.projection_artifacts
    events, current_items, verified_routes = [], {}, []

    def newer_items(route):
        items = copy.deepcopy(original(route))
        for item in items:
            item["parent_identity"] = new_head
            item["source_identity"].update(snapshot_id=new_head, producer_run_id="new-source-run")
        events.append(("current_source", route))
        current_items[route] = items[0]
        return items

    original_rows = runtime._iter_cache_rows

    def checked_rows(base, document, **kwargs):
        route = document["identity"]["route_id"]
        assert ("current_source", route) in events
        assert current_items[route]["parent_identity"] == new_head
        assert document["identity"] == runtime._cache_identity(current_items[route], route)
        yield from original_rows(base, document, **kwargs)
        verified_routes.append(route)

    descriptor = copy.deepcopy(first["snapshot"]["descriptor"])
    descriptor["sources"][0]["manifest_sha256"] = new_head
    descriptor["snapshot_fingerprint"] = fingerprint
    descriptor["descriptor_sha256"] = runtime._cache_key(
        {key: value for key, value in descriptor.items() if key != "descriptor_sha256"})
    monkeypatch.setattr(sources, "fingerprint", fingerprint)
    monkeypatch.setattr(sources, "snapshot_descriptor", lambda: copy.deepcopy(descriptor))
    monkeypatch.setattr(sources, "projection_artifacts", newer_items)
    monkeypatch.setattr(runtime, "_iter_cache_rows", checked_rows)
    monkeypatch.setattr(projection, "query_published_rows", lambda *a, **k: pytest.fail("identical physical cache must be read"))
    monkeypatch.setattr(sources, "iter_artifact_rows", lambda *a: pytest.fail("cache reuse must not scan source rows"))
    monkeypatch.setattr(runtime, "_save_consumer_cache", lambda *a: pytest.fail("cache reuse must not publish"))
    monkeypatch.setattr(runtime, "_prune_after_cache_build", lambda *a: pytest.fail("cache reuse must not prune"))
    result = load(sealed_case, read_only=read_only)
    assert result["snapshot"]["head_sha256"] == new_head != first["snapshot"]["head_sha256"]
    assert result["snapshot"]["snapshot_fingerprint"] == fingerprint != first["snapshot"]["snapshot_fingerprint"]
    assert result["snapshot"]["descriptor"] == descriptor
    assert set(verified_routes) == set(origins) and len(verified_routes) == len(origins)
    for entry in result["source_manifest"]:
        route = entry["route_id"]
        if route not in origins:
            continue
        binding, origin = entry["query_binding"], origins[route]
        assert entry["source_identity"] == current_items[route]["source_identity"]
        assert entry["source_identity"]["snapshot_id"] == new_head
        assert entry["object"] == current_items[route]["artifact_descriptor"] == origin["source_provenance"]["artifact"]
        assert binding["reader"] == "consumer_sealed_cache"
        assert binding["source_identity"] == runtime._cache_identity(current_items[route], route)
        # Old head/run identify only the immutable cache origin, not the current pin.
        assert binding["source_provenance"] == origin["source_provenance"]
        assert binding["source_provenance"]["parent_identity"] == old_head
        assert binding["sealed_read_evidence"] == origin["sealed_read_evidence"]
        assert result["snapshot"]["query_bindings"][route] == binding
        if read_only:
            assert result["read_plan"]["head_sha256"] == new_head
            assert result["read_plan"]["routes"][route]["parent_identity"] == new_head
    pd.testing.assert_frame_equal(result["prepared"]["features"], first["prepared"]["features"])
    assert result["snapshot"]["window_by_market"] == first["snapshot"]["window_by_market"]
    assert {str(path.relative_to(cache)): path.read_bytes() for path in cache.rglob("*") if path.is_file()} == before
    assert not sealed_case.streams


@pytest.mark.parametrize("read_only", [False, True])
def test_new_head_rejects_stale_route_binding_before_reading_existing_cache(sealed_case, monkeypatch, read_only):
    load(sealed_case)
    sources = sealed_case.context["sources"]
    original = sources.projection_artifacts
    consulted = []

    def stale_items(route):
        consulted.append(route)
        return original(route)

    monkeypatch.setattr(sources, "fingerprint", "9" * 64)
    monkeypatch.setattr(sources, "snapshot_descriptor", lambda: {"sources": [
        {"root": str(runtime.PUBLISHED_ROOT), "manifest_sha256": "8" * 64}]})
    monkeypatch.setattr(sources, "projection_artifacts", stale_items)
    monkeypatch.setattr(runtime, "_read_consumer_cache", lambda *a, **k:
        pytest.fail("current route/head binding must verify before looking up old cache"))
    with pytest.raises(runtime.CandidateRuntimeError, match="head 不一致"):
        load(sealed_case, read_only=read_only)
    assert consulted == ["finmind_month_revenue"] and not sealed_case.streams


def test_source_head_mismatch_is_rejected_before_payload_read(sealed_case, monkeypatch):
    original = sealed_case.context["sources"].projection_artifacts

    def mismatched(route):
        items = copy.deepcopy(original(route))
        items[0]["parent_identity"] = "7" * 64
        return items

    monkeypatch.setattr(sealed_case.context["sources"], "projection_artifacts", mismatched)
    with pytest.raises(runtime.CandidateRuntimeError, match="head 不一致"):
        load(sealed_case, allow_full_scan=True)
    assert not sealed_case.calls and not sealed_case.streams


def test_optional_projection_not_ready_is_not_legitimate_empty(sealed_case, monkeypatch):
    import stock_query_projection as projection
    original = projection.query_published_rows

    def optional_unavailable(root, item, route, **kwargs):
        if route == "finmind_per":
            raise projection.ProjectionUnavailableError("projection_not_built")
        return original(root, item, route, **kwargs)

    monkeypatch.setattr(projection, "query_published_rows", optional_unavailable)
    result = load(sealed_case)
    source = next(item for item in result["source_manifest"] if item["route_id"] == "finmind_per")
    assert source["status"] == "projection_not_ready"
    assert source["availability"] == "unknown_availability"
    assert not sealed_case.streams


@pytest.mark.parametrize("read_only", [False, True])
def test_cache_rows_digest_mismatch_never_falls_back_to_live_scan(sealed_case, monkeypatch, read_only):
    result = load(sealed_case)
    reference = result["snapshot"]["query_bindings"]["finmind_month_revenue"]["consumer_cache"]
    manifest = Path(reference["manifest_path"])
    descriptor = runtime._cache_document(manifest)["rows"]
    path = manifest.parent.parent / "rows" / (descriptor["sha256"] + ".jsonl")
    original, stamp = path.read_bytes(), path.stat()
    records = [json.loads(line) for line in original.splitlines()]
    assert records[0]["revenue"] == 9
    records[0]["revenue"] = 8
    changed = b"".join(runtime._canonical(row) + b"\n" for row in records)
    assert len(changed) == len(original) and hashlib.sha256(changed).hexdigest() != descriptor["sha256"]
    path.write_bytes(changed)
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    monkeypatch.setattr(sealed_case.context["sources"], "iter_artifact_rows", lambda *a:
        pytest.fail("digest failure must not fall back to source rows"))
    monkeypatch.setattr(runtime, "_save_consumer_cache", lambda *a: pytest.fail("digest failure must not publish"))
    with pytest.raises(runtime.CandidateRuntimeError, match="digest"):
        load(sealed_case, allow_full_scan=True, read_only=read_only)
    assert not sealed_case.streams


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("component,error", [("rows", "含非物件資料列"), ("ordinals", "原始列順序無效")])
def test_cache_component_structure_failure_never_falls_back_to_live_scan(sealed_case, monkeypatch, read_only, component, error):
    result = load(sealed_case)
    reference = result["snapshot"]["query_bindings"]["finmind_month_revenue"]["consumer_cache"]
    manifest = Path(reference["manifest_path"])
    rows = runtime._cache_document(manifest)["rows"]
    descriptor = rows if component == "rows" else rows["ordinals"]
    path = manifest.parent.parent / component / (descriptor["sha256"] + ".jsonl")
    path.write_bytes(b"1\n" if component == "rows" else b'{"tampered":true}\n')
    monkeypatch.setattr(sealed_case.context["sources"], "iter_artifact_rows", lambda *a:
        pytest.fail("structure failure must not fall back to source rows"))
    monkeypatch.setattr(runtime, "_save_consumer_cache", lambda *a: pytest.fail("structure failure must not publish"))
    with pytest.raises(runtime.CandidateRuntimeError, match=error):
        load(sealed_case, allow_full_scan=True, read_only=read_only)
    assert not sealed_case.streams


def test_full_scan_fallback_rejects_artifact_hash_mismatch(sealed_case, monkeypatch):
    import stock_query_projection as projection
    original = sealed_case.context["sources"].iter_artifact_rows

    def wrong_hash(route):
        for artifact, iterator in original(route):
            artifact.content_sha256 = "9" * 64
            yield artifact, iterator

    def unavailable(*args, **kwargs):
        raise projection.ProjectionUnavailableError("projection_not_built")

    monkeypatch.setattr(sealed_case.context["sources"], "iter_artifact_rows", wrong_hash)
    monkeypatch.setattr(projection, "query_published_rows", unavailable)
    with pytest.raises(runtime.CandidateRuntimeError, match="雜湊不同"):
        load(sealed_case, allow_full_scan=True)


def test_activity_and_raw_ohlc_are_official_while_adjusted_prices_stay_separate(sealed_case):
    row = next(item for item in sealed_case.payloads["official_market_daily"]
        if item["stock_id"] == "2330" and item["date"] == "2026-10-16")
    row.update(volume=7777, open=40, high=55, low=35, close=50)
    result = load(sealed_case)
    price = result["panel"].loc[(result["panel"].stock_id == "2330") &
        (result["panel"].session_date == "2026-10-16")].iloc[0]
    assert price.volume == 7777 and price.volume_unit == "股"
    assert pd.isna(price.amount) and pd.isna(price.transactions)
    assert price.raw_open == 40 and price.raw_close == 50
    assert price.open != 40 and price.adjusted_close != 50
    assert price.high != price.raw_high and price.low != price.raw_low


def test_integrity_failure_never_falls_back(sealed_case, monkeypatch):
    import stock_query_projection as projection

    def corrupt(*args, **kwargs):
        raise projection.ProjectionIntegrityError("shard_hash_mismatch")

    monkeypatch.setattr(projection, "query_published_rows", corrupt)
    with pytest.raises(projection.ProjectionIntegrityError):
        load(sealed_case)
    assert not sealed_case.streams


def test_missing_price_is_partial_and_not_filled(sealed_case):
    missing = "2026-09-15"
    sealed_case.payloads["finmind_price_adj"] = [row for row in sealed_case.payloads["finmind_price_adj"]
        if not (row["stock_id"] == "2330" and row["date"] == missing)]
    # Keep the observed adjusted chain valid while leaving the market session absent.
    next_row = next(row for row in sealed_case.payloads["finmind_price_adj"]
        if row["stock_id"] == "2330" and row["date"] == "2026-09-16")
    next_row["spread"] = 2
    result = load(sealed_case)
    assessment = next(item for item in result["data_quality_assessments"] if item["stock_id"] == "2330")
    assert assessment["status"] == "partial" and missing in assessment["missing_dates"]
    assert not ((result["panel"].stock_id == "2330") & (result["panel"].session_date == missing)).any()


def test_etf_or_unknown_identity_is_not_eligible(sealed_case):
    with pytest.raises(runtime.CandidateRuntimeError, match="普通股"):
        runtime.load_candidate_inputs(["0050"], context=sealed_case.context, cache_dir=sealed_case.root / "cache")
    assert not sealed_case.calls


def test_pinned_head_is_exact_and_missing_pin_never_uses_current(sealed_case, monkeypatch):
    import stock_daily_contract as contract
    root = sealed_case.root / "published"
    manifests = root / "_manifests"
    manifests.mkdir(parents=True)
    raw = b'{"schema":"fixture-pinned"}'
    digest = hashlib.sha256(raw).hexdigest()
    (manifests / (digest + ".json")).write_bytes(raw)
    monkeypatch.setattr(runtime, "PUBLISHED_ROOT", root)
    calls = []
    monkeypatch.setattr(contract, "load_c0_published_context", lambda ref, **kwargs: calls.append(ref) or sealed_case.context)
    runtime._load_context(pinned_head_sha256=digest)
    assert calls[0]["manifest_sha256"] == digest
    assert calls[0]["manifest_path"] == str(manifests / (digest + ".json"))
    with pytest.raises(FileNotFoundError):
        runtime._load_context(pinned_head_sha256="0" * 64)
    assert len(calls) == 1


def test_report_payload_binding_recomputes_and_unknown_target_is_null(sealed_case, monkeypatch):
    inputs = load(sealed_case, horizons=[1, 63])
    inputs["market_sessions"] = {market: [day for day in axis if day <= inputs["asof_by_market"][market]]
        for market, axis in inputs["market_sessions"].items()}
    model_path = sealed_case.root / "candidate_model.json"
    model_path.write_text('{"candidate":true}', encoding="utf-8")
    model = {"schema": runtime.MODEL_SCHEMA, "metadata": inputs["prepared"]["metadata"],
        "horizons": {"1": {"status": "not_trained", "selected": {"direction": None, "return": None}}}}
    monkeypatch.setitem(sys.modules, "stock_daily_candidate_model", SimpleNamespace(
        predict_candidate=lambda model, prepared: pd.DataFrame()))
    payloads = runtime._predict_payloads(model, model_path, inputs, [1, 63])
    assert len(payloads) == 4
    for payload in payloads:
        document = dict(payload)
        expected = document.pop("runtime_payload_sha256")
        assert hashlib.sha256(runtime._canonical(document)).hexdigest() == expected
        assert payload["model_binding"]["sha256"] == hashlib.sha256(model_path.read_bytes()).hexdigest()
        assert payload["snapshot_binding"]["head_sha256"] == "a" * 64
        assert payload["prediction"]["prob_up"] is None
        assert payload["target_session_date"] is None
        assert payload["price_history"][-1]["adjusted_close"] == payload["price_history"][-1]["close"]
        assert "raw_close" in payload["price_history"][-1]
        assert payload["research_only"] is True and payload["promotion"] is False


def test_training_seals_runtime_metadata_before_fit_and_never_promotes(sealed_case, monkeypatch):
    fitted, saved = [], []

    def fit(prepared, **kwargs):
        fitted.append(copy.deepcopy(prepared["metadata"]))
        assert "snapshot_binding" in prepared["metadata"]
        assert "runtime_input_contract" in prepared["metadata"]
        assert set(prepared["metadata"]["runtime_input_contract"]["source_plan_by_horizon"]) == {"1", "5", "20", "60", "63"}
        return {"schema": runtime.MODEL_SCHEMA, "metadata": copy.deepcopy(prepared["metadata"]),
            "research_only": True, "promotion": False, "horizons": {}}

    def save(model, path):
        assert model["metadata"] == fitted[-1]
        saved.append(copy.deepcopy(model))
        runtime._write_json(path, model)
        return {"path": str(path)}

    fake = SimpleNamespace(MODEL_SCHEMA=runtime.MODEL_SCHEMA, fit_candidate=fit, save_candidate=save,
        load_candidate=lambda path: json.loads(path.read_text(encoding="utf-8")))
    monkeypatch.setitem(sys.modules, "stock_daily_candidate_model", fake)
    monkeypatch.setattr(runtime.secrets, "randbits", lambda bits: 12345)
    result = runtime.train_candidate_run(sample_size=50, context=sealed_case.context,
        output_dir=sealed_case.root / "candidates", cache_dir=sealed_case.root / "cache", write_reports=False)
    assert result["manifest"]["seed"] == 12345
    assert result["manifest"]["sample_size_actual"] == 2
    assert result["manifest"]["horizons"] == [1, 5, 20, 60, 63]
    assert result["manifest"]["promotion"] is False
    assert len(saved) == 1
    assert all(path.name != "current.json" for path in sealed_case.root.rglob("*.json"))


@pytest.mark.parametrize("validation", [{}, {"valid": False}, {"valid": 1}, {"pass": True}, {"status": "passed"}])
def test_report_requires_explicit_valid_true(sealed_case, monkeypatch, validation):
    fake_report = SimpleNamespace(write_candidate_report=lambda payload, target: {"pdf_path": str(target / "fixture.pdf")},
        validate_candidate_report=lambda payload, path: validation)
    monkeypatch.setitem(sys.modules, "stock_candidate_report", fake_report)
    with pytest.raises(runtime.CandidateRuntimeError, match="驗證未通過"):
        runtime._write_reports([{"stock": {"stock_id": "2330"}, "horizon_sessions": 1,
            "runtime_payload_sha256": "a" * 64}], sealed_case.root / "reports")
    assert not list(sealed_case.root.rglob("runtime_report_receipt.json"))


def test_report_pass_records_receipt_only_after_validation(sealed_case, monkeypatch):
    fake_report = SimpleNamespace(write_candidate_report=lambda payload, target: {"pdf_path": str(target / "fixture.pdf")},
        validate_candidate_report=lambda payload, path: {"valid": True})
    monkeypatch.setitem(sys.modules, "stock_candidate_report", fake_report)
    runtime._write_reports([{"stock": {"stock_id": "2330"}, "horizon_sessions": 1,
        "runtime_payload_sha256": "a" * 64}], sealed_case.root / "reports")
    receipt = json.loads((sealed_case.root / "reports" / "2330" / "H1" / "runtime_report_receipt.json").read_text(encoding="utf-8"))
    assert receipt["validation"] == {"valid": True}
    assert receipt["research_only"] is True and receipt["promotion"] is False


def test_report_uses_only_frozen_requested_plans_and_keeps_full_schema(sealed_case, monkeypatch):
    inputs = load(sealed_case)
    metadata = copy.deepcopy(inputs["prepared"]["metadata"])
    by_h = {}
    for horizon in runtime.DEFAULT_HORIZONS:
        by_h[str(horizon)] = copy.deepcopy(sealed_case.plan)
        if horizon == 1:
            for item in by_h[str(horizon)]:
                if item["route_id"] == "finmind_month_revenue":
                    item["usage"], item["reason_zh"] = "not-selected", "已封存短期不選財務資料規則"
    metadata["runtime_input_contract"] = {"horizons": list(runtime.DEFAULT_HORIZONS),
        "source_plan_by_horizon": by_h, "feature_module_sha256": runtime._sha_file(sealed_case.proof)}
    metadata["snapshot_binding"] = {"head_sha256": "b" * 64}
    metadata["feature_order"].append("long_only_feature")
    metadata["feature_metadata"]["long_only_feature"] = {"label_zh": "長期參考特徵", "source_route": "finmind_month_revenue"}
    metadata["feature_order_by_horizon"]["63"].append("long_only_feature")
    model = {"schema": runtime.MODEL_SCHEMA, "metadata": metadata, "horizons": {}}
    model_path = sealed_case.root / "candidate.json"
    model_path.write_text('{"fixture":true}', encoding="utf-8")
    monkeypatch.setitem(sys.modules, "stock_daily_candidate_model", SimpleNamespace(load_candidate=lambda path: model))
    monkeypatch.setattr(runtime, "_load_context", lambda **kwargs: sealed_case.context)
    observed = []
    monkeypatch.setattr(runtime, "_predict_payloads", lambda model, path, loaded, horizons, **kwargs: observed.append(loaded) or [])
    monkeypatch.setattr(runtime, "_write_reports", lambda *args: None)
    monkeypatch.setattr(sys.modules["stock_daily_feature_catalog"], "select_source_plan",
        lambda *args, **kwargs: pytest.fail("推論不可重新選來源規則"))
    sealed_case.calls.clear()
    runtime.generate_candidate_reports("2330", model_path=model_path, output_dir=sealed_case.root / "reports", horizons=[1])
    loaded = observed[0]
    assert "finmind_month_revenue" not in {route for route, _ in sealed_case.calls}
    assert loaded["prepared"]["metadata"]["feature_order"] == metadata["feature_order"]
    assert loaded["prepared"]["features"]["long_only_feature"].isna().all()
    assert loaded["prepared"]["metadata"]["requested_horizons"] == [1]
    assert loaded["snapshot"]["version_labels"][-1]["code"] == "candidate_inference_snapshot_differs"


def test_feature_values_disclose_actual_selected_channels_not_just_eligible(sealed_case):
    inputs = load(sealed_case, horizons=[1])
    metadata = copy.deepcopy(inputs["prepared"]["metadata"])
    metadata["feature_metadata"]["fixture_feature"].update(calculation_zh="實際來源測試計算", unit="比例")
    metadata["feature_order"].append("reference_only")
    metadata["feature_metadata"]["reference_only"] = {"label_zh": "未學到參考欄位", "source_route": "finmind_price_adj"}
    model = {"metadata": metadata, "horizons": {"1": {
        "selected": {"direction": "expanded_linear", "return": None}, "candidates": {"expanded_linear": {
            "transformer": {"effective_feature_order": ["fixture_feature"], "effective_indicator_order": [],
                "feature_validity": {"fixture_feature": {"state": "fit_informative", "reason_zh": "訓練期有變化"}}},
            "feature_coverage": {"fixture_feature": {"fit": {"coverage": 1.0}}}}}}}}
    values = runtime._feature_values(inputs["prepared"], "2330", "TWSE", "2026-10-16", model, 1)
    actual, reference = values
    assert actual["selected"] is True and actual["calculation_zh"] == "實際來源測試計算"
    assert actual["selected_by_task"]["direction"]["numeric"] is True
    assert actual["feature_validity"]["direction"]["state"] == "fit_informative"
    assert actual["coverage"]["training_by_task"]["direction"]["fit"]["coverage"] == 1.0
    assert reference["selected"] is False and reference["value"] is None


def test_current_feature_limitations_are_forwarded_without_other_stock_attribution(sealed_case, monkeypatch):
    inputs = load(sealed_case, horizons=[1])
    inputs["prepared"]["metadata"]["limitations"] = [
        {"stock_id": "2330", "market": "TWSE", "reason_zh": "本股公開時間未知，未回填歷史。"},
        {"stock_id": "6488", "market": "TPEX", "reason_zh": "其他股票專屬阻擋，不可誤套。"},
        {"route_id": "finmind_per", "reason_zh": "未分股的批次估值限制。"}]
    inputs["prepared"]["metadata"]["limitations_by_stock"] = {"2330": [
        {"reason_zh": "本股公開時間未知，未回填歷史。"}, {"reason_zh": "本股單位未證實。"}]}
    model_path = sealed_case.root / "candidate_model.json"
    model_path.write_text('{"fixture":true}', encoding="utf-8")
    model = {"schema": runtime.MODEL_SCHEMA, "metadata": inputs["prepared"]["metadata"], "horizons": {}}
    monkeypatch.setitem(sys.modules, "stock_daily_candidate_model", SimpleNamespace(
        predict_candidate=lambda model, prepared: pd.DataFrame()))
    payload = next(item for item in runtime._predict_payloads(model, model_path, inputs, [1])
        if item["stock"]["stock_id"] == "2330")
    assert payload["limitations"].count("本股公開時間未知，未回填歷史。") == 1
    assert "本股單位未證實。" in payload["limitations"]
    assert "其他股票專屬阻擋，不可誤套。" not in payload["limitations"]
    assert "未分股的批次估值限制。" not in payload["limitations"]
    assert payload["feature_limitations"]["unattributed_batch"][0]["reason_zh"] == "未分股的批次估值限制。"
    assert payload["feature_limitations"]["unattributed_applied_to_stock"] is False


def test_single_stock_query_can_disclose_unscoped_current_feature_reasons(sealed_case):
    inputs = load(sealed_case, horizons=[1])
    inputs["prepared"]["metadata"]["limitations"] = [{"route_id": "official_market_daily",
        "reason_zh": "缺少官方原價，該特徵保留缺值。"}]
    texts, evidence = runtime._feature_limitations(inputs["prepared"], "2330", "TWSE", 1)
    assert "缺少官方原價，該特徵保留缺值。" in texts
    assert not evidence["unattributed_batch"]


def test_training_default_does_not_generate_pdfs(monkeypatch, tmp_path):
    import inspect
    assert inspect.signature(runtime.train_candidate_run).parameters["write_reports"].default is False
    observed = []
    monkeypatch.setattr(runtime, "train_candidate_run", lambda **kwargs: observed.append(kwargs) or {"run_dir": str(tmp_path)})
    assert runtime.main(["train"]) == 0
    assert observed[0]["write_reports"] is False
    assert runtime.main(["train", "--write-reports"]) == 0
    assert observed[1]["write_reports"] is True


@pytest.mark.parametrize("path", [runtime.PUBLISHED_ROOT / "_query" / "x.json",
    Path(r"D:\TWStockData\program_data\stock_daily_c0\models\current.json"),
    Path(r"D:\MIX All\candidate.json")])
def test_product_write_boundaries_reject_formal_or_central_targets(path):
    with pytest.raises(runtime.CandidateRuntimeError):
        runtime._consumer_path(path)


def test_invalid_horizons_are_not_silently_substituted():
    with pytest.raises(runtime.CandidateRuntimeError):
        runtime._horizons([252])


def test_candidate_artifact_change_during_load_is_not_bound_as_the_original(tmp_path):
    path = tmp_path / "model.json"
    path.write_bytes(b'{"original":true}')

    def replace_artifact(target):
        target.write_bytes(b'{"replacement":true}')
        return {"schema": runtime.MODEL_SCHEMA}

    with pytest.raises(runtime.CandidateRuntimeError, match="載入期間變更"):
        runtime._load_bound_model(SimpleNamespace(load_candidate=replace_artifact), path)
