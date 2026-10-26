"""Offline fixtures only; execution is reserved for the consolidated QA owner."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock_daily_candidate_runtime as runtime
import stock_daily_selected_cache as selected
import stock_query_projection as projection
import stock_daily_selected_deployment as deployment

FIFTEEN_ROUTES = {
    "official_finance_reporting_deadline", "finmind_month_revenue", "finmind_balance_sheet",
    "finmind_cash_flows_statement", "finmind_dividend_result", "finmind_shareholding",
    "finmind_financial_statements", "official_sector_features", "finmind_day_trading", "finmind_per",
    "finmind_margin", "finmind_institutional", "finmind_dividend", "news_and_hot_topics_raw",
    "finmind_industry_chain_flow",
}


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setattr(deployment, "active_selected_binding", lambda: None)
    published = tmp_path / "published"
    published.mkdir()
    monkeypatch.setattr(runtime, "PUBLISHED_ROOT", published)
    proof = tmp_path / "proof.json"
    proof.write_text('{"sealed":true}\n', encoding="utf-8")
    records = [{"stock_id": "1101", "market": "TWSE", "stock_name": "台泥"},
        {"stock_id": "2330", "market": "TWSE", "stock_name": "台積電"},
        {"stock_id": "6488", "market": "TPEX", "stock_name": "環球晶"}]
    routes = sorted(runtime.CORE_ROUTES | FIFTEEN_ROUTES | {"global_market_history", "finmind_gold_price"})
    plan = [{"route_id": route, "usage": "not-selected" if route == "finmind_gold_price" else
        "context-only" if route in runtime.SNAPSHOT_ROUTES | {"finmind_dividend", "finmind_dividend_result",
            "news_and_hot_topics_raw"} else "used", "name_zh": "封存測試來源", "reason_zh": "封存規則"}
        for route in routes]
    feature = SimpleNamespace(SOURCE_CATALOG={route: {} for route in routes}, __file__=str(proof))
    monkeypatch.setitem(sys.modules, "stock_daily_feature_catalog", feature)
    model = {"schema": runtime.MODEL_SCHEMA, "metadata": {"feature_schema": runtime.FEATURE_SCHEMA,
        "training_stock_ids": ["1101"], "runtime_input_contract": {"horizons": [1, 5, 20, 60, 63],
            "lookback_calendar_months": 18, "feature_module_sha256": runtime._sha_file(proof),
            "source_plan_by_horizon": {str(h): copy.deepcopy(plan) for h in (1, 5, 20, 60, 63)}}}}
    for entry in model["metadata"]["runtime_input_contract"]["source_plan_by_horizon"]["1"]:
        if entry["route_id"] == "finmind_month_revenue":
            entry["usage"] = "context-only"
    model_path = tmp_path / "candidate_model.json"
    model_path.write_bytes(runtime._canonical(model))
    monkeypatch.setitem(sys.modules, "stock_daily_candidate_model",
        SimpleNamespace(load_candidate=lambda path: copy.deepcopy(model)))
    payloads = {route: [{"stock_id": item["stock_id"], "date": "2026-10-15", "value": 7}
        for item in records] for route in routes}
    payloads["global_market_history"] = [
        {"data_id": "SP500", "date": "2025-04-15", "value": 1},
        {"data_id": "SP500", "date": "2026-10-16", "value": 2}]
    state = SimpleNamespace(head="a" * 64, missing=set(), partial=set(), streams=[], stream_ends=[],
        queries=[], warm=set(), fail_tail=set(), wrong_hash=set(), projection_failure=set(), after_stream=None)

    def item_for(route):
        raw = runtime._canonical({"data": payloads[route]})
        return {"root": str(published), "parent_identity": state.head,
            "source_identity": {"snapshot_id": state.head}, "artifact_descriptor": {
                "route_id": route, "path": route + ".json", "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "status": "PARTIAL" if route in state.partial else "SUCCESS"}}

    def rows(route):
        yield from payloads[route]
        if route in state.fail_tail:
            raise ValueError("source_changed_at_stream_end")
        state.stream_ends.append(route)
        if state.after_stream is not None:
            state.after_stream(route)

    class Sources:
        fingerprint = "b" * 64

        def snapshot_descriptor(self):
            return {"sources": [{"root": str(published), "manifest_sha256": state.head}]}

        def projection_artifacts(self, route):
            return [] if route in state.missing else [item_for(route)]

        def iter_artifact_rows(self, route):
            state.streams.append(route)
            item = item_for(route)
            descriptor = item["artifact_descriptor"]
            artifact = SimpleNamespace(content_sha256="9" * 64 if route in state.wrong_hash else descriptor["sha256"],
                snapshot_id=state.head, status=descriptor["status"], metadata={"selected_ref": {
                    "root": str(published), "path": descriptor["path"],
                    "sha256": descriptor["sha256"], "size": descriptor["size"]}})
            yield artifact, rows(route)

    def query(root, descriptor, route, **kwargs):
        state.queries.append((route, kwargs))
        assert kwargs["allow_build"] is False
        assert kwargs["stock_ids"] is kwargs["start_date"] is kwargs["end_date"] is None
        assert Path(kwargs["consumer_temp_root"]).is_relative_to(tmp_path)
        if route in state.projection_failure:
            raise projection.ProjectionIntegrityError("sealed_shard_corrupt")
        if route not in state.warm:
            raise projection.ProjectionUnavailableError("projection_not_built")
        return SimpleNamespace(iter_rows=lambda: rows(route), cache_key="fixture-" + route,
            predicate={"stock_ids": None}, lineage={"source_artifact": descriptor}, metrics={"cache_hit": True},
            completion_path=proof, index_manifest_path=proof)

    monkeypatch.setattr(projection, "query_published_rows", query)
    days = ["2025-04-15", "2025-04-16", "2026-10-14", "2026-10-15", "2026-10-16", "2027-03-01"]
    context = {"sources": Sources(), "current_identity": {"records": records, "binding": {"verified": True}},
        "calendar_evidence": {"calendar_verified_by_market": {"TWSE": True, "TPEX": True}},
        "application_scope": {"binding": {"verified": True}, "capture_binding": {"verified": True}},
        "asof_by_market": {"TWSE": "2026-10-16", "TPEX": "2026-10-15"},
        "market_sessions": {"TWSE": days, "TPEX": days}}
    pins = []

    def load_context(**kwargs):
        pins.append(kwargs.get("pinned_head_sha256"))
        return context

    monkeypatch.setattr(runtime, "_load_context", load_context)
    return SimpleNamespace(root=tmp_path, cache=tmp_path / "cache", model_path=model_path, model=model,
        context=context, state=state, payloads=payloads, item_for=item_for, records=records, plan=plan, pins=pins)


def prepare(case, **kwargs):
    return selected.prepare_selected_cache(case.model_path, case.cache, **kwargs)


def read_cached(case, route, stock, *, usage=None):
    frozen = selected._frozen_inputs(case.model_path, (1, 63), None, [stock])
    entry = next(item for item in frozen["plan"] if item["route_id"] == route)
    scope = selected._requests(route, usage or entry["usage"], frozen)
    scope = scope[None if route in runtime.GLOBAL_ROUTES else stock]
    item = case.item_for(route)
    return runtime._read_route(case.context["sources"], item, route, [stock], scope["start"], scope["end"],
        case.cache, allow_full_scan=False)


def test_all_current_stocks_including_non_training_2330_and_fifteen_routes(case, monkeypatch):
    receipt = prepare(case)
    selected_routes = {entry["route_id"] for entry in case.plan if entry["usage"] != "not-selected"}
    streamed = selected_routes - runtime.CORE_ROUTES | {"finmind_price_adj", "official_market_daily"}
    assert receipt["ready"] is True and receipt["all_stocks"] is True
    assert receipt["stock_ids"] == ["1101", "2330", "6488"]
    assert receipt["routes_coverage"]["selected"] == len(selected_routes)
    assert set(case.state.streams) == streamed
    assert len(case.state.streams) == len(set(case.state.streams))
    assert set(case.state.stream_ends) == streamed
    assert FIFTEEN_ROUTES.issubset({entry["route_id"] for entry in receipt["routes"] if entry["cache_ready"]})
    monkeypatch.setattr(projection, "query_published_rows", lambda *args, **kwargs: pytest.fail("prepared cache miss"))
    for route in FIFTEEN_ROUTES | {"finmind_price_adj", "official_market_daily"}:
        rows, binding = read_cached(case, route, "2330")
        assert rows and binding["reader"] == "consumer_sealed_cache"
    assert not (case.root / "published" / "_query").exists()


def test_same_head_second_prepare_reuses_caches_without_queries_or_source_reads(case):
    prepare(case)
    case.state.streams.clear()
    case.state.queries.clear()
    receipt = prepare(case)
    assert receipt["ready"] is True
    assert not case.state.streams and not case.state.queries
    assert all(item.get("prepared_queries", 0) == 0 for item in receipt["routes"])


def test_exact_scope_cache_precedes_unrelated_broken_manifest(case):
    prepare(case)
    for queries in (case.cache / "sealed_candidate_rows").glob("*/queries"):
        (queries / ("0" * 64 + ".json")).write_text("broken-unrelated-cache", encoding="utf-8")
    case.state.streams.clear()
    case.state.queries.clear()
    assert read_cached(case, "finmind_per", "2330")[1]["reader"] == "consumer_sealed_cache"
    assert prepare(case)["ready"] is True
    assert not case.state.streams and not case.state.queries


def test_training_subset_cache_reused_and_remaining_stocks_stream_once(case):
    route = "finmind_per"
    frozen = selected._frozen_inputs(case.model_path, (1, 63), None, ["1101"])
    scope = selected._requests(route, "used", frozen)["1101"]
    runtime._save_consumer_cache(case.cache, runtime._cache_identity(case.item_for(route), route), scope,
        [case.payloads[route][0]], {"reader": "sealed_stream_fallback", "stream_exhausted": True})
    receipt = prepare(case)
    item = next(entry for entry in receipt["routes"] if entry["route_id"] == route)
    assert item["reused_queries"] == 1 and item["prepared_queries"] == 2
    assert case.state.streams.count(route) == 1
    assert read_cached(case, route, "2330")[0][0]["stock_id"] == "2330"


def test_sealed_projection_is_reused_with_lossless_runtime_filtering(case):
    case.state.warm.update({"finmind_price_adj", "official_market_daily", "official_sector_features"})
    receipt = prepare(case)
    assert receipt["ready"] is True
    assert not case.state.warm.intersection(case.state.streams)
    for route in case.state.warm:
        entry = next(item for item in receipt["routes"] if item["route_id"] == route)
        assert entry["read_evidence"]["reader"] == "sealed_projection"
        assert entry["read_evidence"]["stream_exhausted"] is True


def test_row_aliases_unknown_dates_unkeyed_rows_and_market_cutoffs_match_runtime(case):
    route = "finmind_per"
    case.payloads[route] = [
        {"stock_id": "2330", "date": "2025-04-16", "value": "18-month-start"},
        {"stock_id": "2330", "date": "2025-04-15", "value": "too-old"},
        {"code": "6488", "report_date": "2026-10-15", "value": "own-cutoff"},
        {"data_id": "6488", "date": "2026-10-16", "value": "after-tpex-cutoff"},
        {"date": "2026-10-16", "value": "unkeyed-twse-only"},
        {"date": "invalid", "observation_date": "2026-10-15", "value": "date-alias"},
        {"stock_id": "6488", "value": "unknown-date-kept"},
        {"stock_id": "0050", "date": "2026-10-15", "value": "etf-excluded"}]
    prepare(case)
    for stock in ("2330", "6488"):
        rows, _ = read_cached(case, route, stock)
        frozen = selected._frozen_inputs(case.model_path, (1, 63), None, [stock])
        scope = selected._requests(route, "used", frozen)[stock]
        expected = [row for row in case.payloads[route] if selected._selected(row, route, scope)]
        assert rows == expected
    assert not any(row.get("value") == "after-tpex-cutoff" for row in read_cached(case, route, "6488")[0])


def test_global_ids_are_not_filtered_as_stock_ids_and_superset_covers_both_markets(case):
    prepare(case)
    route = "global_market_history"
    assert case.state.streams.count(route) == 1
    twse, _ = read_cached(case, route, "2330")
    tpex, _ = read_cached(case, route, "6488")
    assert [row["value"] for row in twse] == [2]
    assert [row["value"] for row in tpex] == [1]


def test_snapshot_routes_do_not_arbitrarily_limit_old_history(case):
    route = "official_finance_reporting_deadline"
    case.payloads[route].append({"stock_id": "2330", "date": "2010-01-01", "value": "retained-snapshot"})
    prepare(case)
    assert any(row.get("value") == "retained-snapshot" for row in read_cached(case, route, "2330")[0])


def test_partial_source_can_be_cache_ready_without_claiming_data_complete(case):
    case.state.partial.add("finmind_month_revenue")
    receipt = prepare(case)
    entry = next(item for item in receipt["routes"] if item["route_id"] == "finmind_month_revenue")
    assert receipt["ready"] is True and receipt["status"] == "ready"
    assert receipt["source_partial"] is True and receipt["coverage_status"] == "source_partial"
    assert receipt["data_completeness_verified"] is False
    assert entry["publication_status"] == "PARTIAL" and entry["source_partial"] is True
    assert entry["cache_ready"] is True


@pytest.mark.parametrize("partial", [False, True])
def test_zero_rows_is_published_query_empty_not_legitimate_absence(case, partial):
    route = "finmind_dividend_result"
    case.payloads[route] = []
    if partial:
        case.state.partial.add(route)
    receipt = prepare(case)
    entry = next(item for item in receipt["routes"] if item["route_id"] == route)
    assert receipt["ready"] is True and entry["cache_ready"] is True
    for stock in entry["stocks"].values():
        assert stock["status"] == "published_query_empty"
        assert stock["absence_status"] == "absence_unverified"
        assert stock["legitimate_empty"] is False and stock["financial_absence_proved"] is False
    assert read_cached(case, route, "2330")[0] == []


def test_missing_route_is_explicit_non_ready_and_does_not_create_empty_cache(case):
    route = "finmind_per"
    case.state.missing.add(route)
    receipt = prepare(case)
    entry = next(item for item in receipt["routes"] if item["route_id"] == route)
    assert receipt["ready"] is False and receipt["routes_coverage"]["missing"] == 1
    assert entry["status"] == "missing" and not entry["cache_ready"]
    assert all(item["status"] == "missing" and not item["legitimate_empty"] for item in entry["stocks"].values())
    assert route not in case.state.streams


@pytest.mark.parametrize("failure,expected_error", [
    ("fail_tail", ValueError),
    ("wrong_hash", runtime.CandidateRuntimeError),
    ("projection_failure", projection.ProjectionIntegrityError),
])
def test_source_integrity_failure_does_not_commit_any_cache_for_failed_route(case, failure, expected_error):
    route = "finmind_price_adj"
    getattr(case.state, failure).add(route)
    with pytest.raises(expected_error):
        prepare(case)
    base = case.cache / "sealed_candidate_rows" / hashlib.sha256(runtime._canonical(
        runtime._cache_identity(case.item_for(route), route))).hexdigest()
    assert not list(base.rglob("*.json"))
    receipts = list((case.cache / "selected_cache_preparations").glob("*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["status"] == "failed" and receipt["ready"] is False
    assert receipt["routes_coverage"]["failed"] == 1
    assert receipt["routes_coverage"]["pending"] > 0
    if failure == "projection_failure":
        assert route not in case.state.streams


def test_head_and_run_provenance_only_reuses_components_but_changed_content_or_scope_does_not(case, monkeypatch):
    route = "finmind_per"
    future = {"stock_id": "2330", "date": "2027-03-01", "value": "future-scope-row"}
    case.payloads[route].append(future)
    sources = case.context["sources"]
    provenance = SimpleNamespace(run_id="source-run-1")
    original_artifacts, original_snapshot = sources.projection_artifacts, sources.snapshot_descriptor

    def artifacts(name):
        items = original_artifacts(name)
        for item in items:
            item["source_identity"]["producer_run_id"] = provenance.run_id
        return items

    def snapshot():
        value = original_snapshot()
        value["sources"][0]["producer_run_id"] = provenance.run_id
        return value

    monkeypatch.setattr(sources, "projection_artifacts", artifacts)
    monkeypatch.setattr(sources, "snapshot_descriptor", snapshot)

    def components():
        result = {}
        for path in (case.cache / "sealed_candidate_rows").rglob("*.jsonl"):
            raw, stat = path.read_bytes(), path.stat()
            result[str(path.relative_to(case.cache))] = (raw, stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino)
        return result

    first = prepare(case)
    assert first["ready"] is True
    first_route = next(item for item in first["routes"] if item["route_id"] == route)
    manifest_path = Path(first_route["stocks"]["2330"]["cache"]["manifest_path"])
    manifest_bytes = manifest_path.read_bytes()
    origin = runtime._cache_document(manifest_path)
    assert origin["source_provenance"]["parent_identity"] == first["head_sha256"]
    assert origin["source_provenance"]["source_identity"]["producer_run_id"] == "source-run-1"
    assert origin["sealed_read_evidence"]["offline_preparation_run_id"] == first["run_id"]
    before = components()
    assert before and sum(item[1] for item in before.values()) > 0

    previous = first
    for head, source_run in (("8" * 64, "source-run-2"), ("8" * 64, "source-run-3")):
        case.state.head, provenance.run_id = head, source_run
        case.state.streams.clear()
        case.state.queries.clear()
        current = prepare(case, pinned_head_sha256=head)
        current_route = next(item for item in current["routes"] if item["route_id"] == route)
        assert current["ready"] is True and current["head_sha256"] == head
        assert current["run_id"] != previous["run_id"]
        assert current["snapshot_binding"]["descriptor"]["sources"][0]["producer_run_id"] == source_run
        assert current["code_binding"] == first["code_binding"]
        assert current_route["artifact"] == first_route["artifact"]
        assert current_route["stocks"]["2330"]["cache"]["manifest_path"] == str(manifest_path)
        assert all(item.get("prepared_queries", 0) == 0 for item in current["routes"])
        assert sum(item.get("reused_queries", 0) for item in current["routes"]) > 0
        rows, binding = read_cached(case, route, "2330")
        assert rows == [{"stock_id": "2330", "date": "2026-10-15", "value": 7}]
        # Current receipt observes the new head/run; immutable cache provenance
        # still names the source and preparation that originally wrote its bytes.
        assert binding["source_provenance"] == origin["source_provenance"]
        assert binding["sealed_read_evidence"]["offline_preparation_run_id"] == first["run_id"]
        assert manifest_path.read_bytes() == manifest_bytes
        assert not case.state.streams and not case.state.queries
        assert components() == before  # exact files/count, bytes, mtime and native identity
        previous = current
    assert first["head_sha256"] != previous["head_sha256"]

    case.payloads[route][1]["value"] = "changed-content"
    case.state.head, provenance.run_id = "9" * 64, "changed-body-run"
    case.state.streams.clear()
    case.state.queries.clear()
    changed = prepare(case)
    changed_route = next(item for item in changed["routes"] if item["route_id"] == route)
    assert changed["ready"] is True and changed_route["artifact"] != first_route["artifact"]
    assert changed_route["prepared_queries"] > 0 and route in case.state.streams
    assert Path(changed_route["stocks"]["2330"]["cache"]["manifest_path"]) != manifest_path
    assert read_cached(case, route, "2330")[0] == [case.payloads[route][1]]

    same_artifact = copy.deepcopy(changed_route["artifact"])
    case.context["asof_by_market"]["TWSE"] = "2027-03-01"
    case.state.head, provenance.run_id = "c" * 64, "changed-scope-run"
    case.state.streams.clear()
    case.state.queries.clear()
    expanded = prepare(case)
    expanded_route = next(item for item in expanded["routes"] if item["route_id"] == route)
    assert expanded["ready"] is True and expanded_route["artifact"] == same_artifact
    assert expanded_route["prepared_queries"] > 0 and route in case.state.streams
    assert read_cached(case, route, "2330")[0] == [case.payloads[route][1], future]


def test_news_common_rows_have_one_component_per_window_and_read_back_in_source_order(case):
    route = "news_and_hot_topics_raw"
    case.payloads[route] = [
        {"date": "2026-09-14", "value": "too-old"},
        {"date": "2026-09-15", "value": "common-tpex-start"},
        {"stock_id": "1101", "date": "2026-09-16", "value": "own-1101-first"},
        {"date": "2026-09-16", "value": "common-first"},
        {"stock_id": "2330", "date": "2026-10-15", "value": "own-2330-first"},
        {"date": "2026-10-15", "value": "common-middle"},
        {"stock_id": "6488", "date": "2026-10-15", "value": "own-6488"},
        {"date": "2026-10-16", "value": "common-twse-end"},
        {"stock_id": "1101", "date": "2026-10-16", "value": "own-1101-last"},
        {"stock_id": "2330", "date": "2026-10-16", "value": "own-2330-last"},
        {"stock_id": "6488", "date": "2026-10-16", "value": "after-tpex-cutoff"},
        {"date": "2026-10-17", "value": "future-common"}]
    payload = case.payloads[route]
    expected_common = [payload[3], payload[5], payload[7]]
    expected_own = {"1101": [payload[2], payload[8]], "2330": [payload[4], payload[9]], "6488": [payload[6]]}
    expected_rows = {"1101": [payload[2], payload[3], payload[5], payload[7], payload[8]],
        "2330": [payload[3], payload[4], payload[5], payload[7], payload[9]],
        "6488": [payload[1], payload[3], payload[5], payload[6]]}

    def documents(receipt):
        item = next(item for item in receipt["routes"] if item["route_id"] == route)
        return {stock: (Path(value["cache"]["manifest_path"]).parent.parent,
            runtime._cache_document(value["cache"]["manifest_path"])) for stock, value in item["stocks"].items()}

    def rows_at(path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def common_files(base):
        result = {}
        for path in (base / "rows").glob("*.jsonl"):
            data = rows_at(path)
            if data and all(row.get("stock_id") is None for row in data):
                result[path.resolve()] = data
        return result

    first = prepare(case, stock_ids=["1101", "2330"])
    assert first["ready"] is True
    initial = documents(first)
    shared_paths = []
    for stock, (base, document) in initial.items():
        assert len(document["shared_rows"]) == 1
        common = base / "rows" / (document["shared_rows"][0]["sha256"] + ".jsonl")
        shared_paths.append(common.resolve())
        assert rows_at(common) == expected_common
        assert rows_at(base / "rows" / (document["rows"]["sha256"] + ".jsonl")) == expected_own[stock]
        assert read_cached(case, route, stock)[0] == expected_rows[stock]
    assert len(set(shared_paths)) == 1
    assert initial["1101"][1]["shared_rows"] == initial["2330"][1]["shared_rows"]
    shared_path = shared_paths[0]
    assert common_files(initial["1101"][0]) == {shared_path: expected_common}
    original_shared = (shared_path.read_bytes(), shared_path.stat().st_mtime_ns)

    # Add the stock whose real market cutoff differs; its applicable common
    # rows must remain distinct instead of borrowing the TWSE window wholesale.
    all_stocks = prepare(case)
    assert all_stocks["ready"] is True
    final = documents(all_stocks)
    for stock, (base, document) in final.items():
        assert len(document["shared_rows"]) == 1
        assert rows_at(base / "rows" / (document["rows"]["sha256"] + ".jsonl")) == expected_own[stock]
        assert read_cached(case, route, stock)[0] == expected_rows[stock]
    for stock in ("1101", "2330"):
        base, document = final[stock]
        assert (base / "rows" / (document["shared_rows"][0]["sha256"] + ".jsonl")).resolve() == shared_path
    tpex_base, tpex = final["6488"]
    tpex_shared = (tpex_base / "rows" / (tpex["shared_rows"][0]["sha256"] + ".jsonl")).resolve()
    assert tpex_shared != shared_path
    assert common_files(tpex_base) == {shared_path: expected_common, tpex_shared: [payload[1], payload[3], payload[5]]}
    assert (shared_path.read_bytes(), shared_path.stat().st_mtime_ns) == original_shared


def test_corrupt_existing_cache_is_not_silently_rebuilt(case):
    prepare(case)
    path = next((case.cache / "sealed_candidate_rows").rglob("*.jsonl"))
    path.write_bytes(b'{"tampered":true}\n')
    case.state.streams.clear()
    with pytest.raises(runtime.CandidateRuntimeError, match="digest"):
        prepare(case)
    assert not case.state.streams


def test_model_change_at_stream_end_stops_before_route_commit(case):
    def changed(route):
        case.model_path.write_text("changed", encoding="utf-8")

    case.state.after_stream = changed
    with pytest.raises(runtime.CandidateRuntimeError, match="已變更"):
        prepare(case)
    assert not list((case.cache / "sealed_candidate_rows").rglob("*.json"))


def test_invalid_stock_or_frozen_plan_is_rejected_before_scanning(case):
    with pytest.raises(runtime.CandidateRuntimeError, match="普通股"):
        prepare(case, stock_ids=["0050"])
    case.model["metadata"]["runtime_input_contract"]["lookback_calendar_months"] = 12
    with pytest.raises(runtime.CandidateRuntimeError, match="18"):
        prepare(case)
    assert not case.state.streams and not case.state.queries


def test_context_sources_use_verified_side_evidence_and_receipt_has_integrity(case):
    receipt = prepare(case, stock_ids=["2330"])
    assert receipt["all_stocks"] is False and receipt["stock_count"] == 1
    assert not selected._CONTEXT_ROUTES.intersection(case.state.streams)
    saved = json.loads(Path(receipt["receipt_path"]).read_text(encoding="utf-8"))
    integrity = saved.pop("integrity_sha256")
    assert integrity == hashlib.sha256(runtime._canonical(saved)).hexdigest()
    assert saved["window_by_market"]["TWSE"]["start"] == "2025-04-16"


def test_cli_only_calls_prepare_and_returns_nonzero_for_not_ready(case, monkeypatch):
    calls = []

    def fake_prepare(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ready": False, "receipt_path": "fixture-receipt.json"}

    monkeypatch.setattr(selected, "prepare_selected_cache", fake_prepare)
    assert selected.main(["--model-path", str(case.model_path), "--cache-dir", str(case.cache)]) == 2
    assert calls[0][1]["stock_ids"] is None
    assert not case.state.streams and not case.state.queries


def test_all_ordinary_success_writes_bound_index_but_subset_does_not(case):
    prepare(case, stock_ids=["2330"])
    path = case.cache / "selected_cache_preparations" / selected.ALL_ORDINARY_INDEX
    assert not path.exists()
    receipt = prepare(case)
    index = runtime._cache_document(path)
    assert index["head_sha256"] == receipt["head_sha256"]
    assert index["model_sha256"] == receipt["model_sha256"]
    assert index["code_binding"] == receipt["code_binding"]
    assert index["receipt"]["sha256"] == runtime._sha_file(receipt["receipt_path"])


def test_guard_is_reentrant_and_an_existing_guard_file_is_not_stale_lock(case):
    with selected.consumer_cache_guard(case.cache):
        with selected.consumer_cache_guard(case.cache):
            assert (case.cache / ".selected_cache_prepare.guard").is_file()
    with selected.consumer_cache_guard(case.cache):
        pass
    assert (case.cache / ".selected_cache_prepare.guard").is_file()


def test_manual_prepare_obeys_cross_process_byte_range_guard(case):
    script = """import sys
sys.path.insert(0, sys.argv[1])
import stock_daily_selected_cache as cache
try:
    cache.prepare_selected_cache(sys.argv[2], sys.argv[3])
except cache.SelectedCacheBusy:
    sys.exit(3)
sys.exit(9)
"""
    with selected.consumer_cache_guard(case.cache):
        result = subprocess.run([sys.executable, "-c", script, str(Path(selected.__file__).parent),
            str(case.model_path), str(case.cache)], capture_output=True, encoding="utf-8", timeout=30)
    assert result.returncode == 3, result.stderr
    assert not case.state.streams


def test_guard_is_released_by_os_when_child_exits_without_finally(case):
    script = """import os, sys
sys.path.insert(0, sys.argv[1])
import stock_daily_selected_cache as cache
with cache.consumer_cache_guard(sys.argv[2]):
    os._exit(0)
"""
    result = subprocess.run([sys.executable, "-c", script, str(Path(selected.__file__).parent), str(case.cache)],
        capture_output=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    with selected.consumer_cache_guard(case.cache):
        pass
