"""Focused in-memory cases; execution is deferred to the delegated QA worker."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import pickle
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
import stock_model_research_data as research


def _snapshot(n=610):
    days = pd.bdate_range("2023-01-02", periods=n + 65).delete([17, 40])
    days = days[:n + 63]
    observed = days[:n]
    price, board = [], []
    for stock in ("1101", "2330"):
        previous = 100.0
        for i, day in enumerate(observed):
            close = 100.0 + i * 0.1
            price.append(dict(stock_id=stock, date=day, open=close,
                close=close, max=close + 1, min=close - 1,
                spread=close - previous, Trading_Volume=1000,
                _shared_collected_at="2026-10-25T00:00:00+00:00"))
            board.append(dict(stock_id=stock, date=day, market="TWSE",
                open=close, high=close + 1, low=close - 1, close=close,
                volume=1000, amount=close * 1000, transactions=10))
            previous = close
    plan = research.catalog.select_source_plan([1, 63])
    non_current = research.RETIRED_ROUTES | {"finmind_futures_institutional", "finmind_government_bank",
        "finmind_block_trade", "finmind_loan_collateral", "finmind_margin_maintenance",
        "finmind_crude_oil_prices", "finmind_gold_price"}
    current = set(research.catalog.SOURCE_CATALOG) - non_current
    audit = [dict(item, current=item["route_id"] in current,
        classification="current" if item["route_id"] in current else "not_current",
        payload_verification="fixture_only", publication_status="PARTIAL") for item in plan]
    return dict(frames={"finmind_price_adj": pd.DataFrame(price),
            "official_market_daily": pd.DataFrame(board)},
        records=[dict(stock_id=s, market="TWSE") for s in ("1101", "2330", "9999")],
        market_sessions={"TWSE": days}, asof_by_market={"TWSE": observed[-1]},
        source_audit=audit, source_manifest=plan,
        bindings={"head_sha256": "fixture-only", "historical_membership_verified": False},
        timings={}, read_statistics={}, calendar_evidence={"fixture_only": True})


def test_complete_history_is_not_clipped_to_last_eighteen_months():
    snapshot = _snapshot()
    result = research._assemble(snapshot, (1, 63), threads=1, progress=None)
    frame = result["features"]
    assert frame.session_date.min() == snapshot["market_sessions"]["TWSE"][0]
    assert frame.session_date.max() == snapshot["asof_by_market"]["TWSE"]
    assert frame.session_date.min() < frame.session_date.max() - pd.DateOffset(months=18)
    assert set(frame.stock_id) == {"1101", "2330", "9999"}
    assert len(result["feature_metadata"]) == 38
    assert set(result["feature_order_by_horizon"]) == {"1", "63"}
    assert "return_252" in result["baseline_feature_order_by_horizon"]["63"]
    assert "return_252" not in result["baseline_feature_order_by_horizon"]["1"]
    assert result["baseline_feature_order_by_horizon"] == result["feature_order_by_horizon"]
    assert "revenue_yoy" in result["baseline_feature_order_by_horizon"]["63"]
    assert "revenue_yoy" not in result["legacy_reference_feature_order"]["63"]
    assert not frame.loc[frame.stock_id.eq("9999"), "feature_ready"].any()
    assert frame.loc[frame.stock_id.eq("9999"), "signal_close"].isna().all()
    assert len(result["source_audit"]) == 36
    assert result["coverage"]["claims_five_year_history"] is False


def test_overlapping_windows_preserve_warmup_instead_of_restarting_returns():
    snapshot = _snapshot()
    result = research._assemble(snapshot, (63,), threads=1, progress=None)
    frame = result["features"].loc[lambda f: f.stock_id.eq("2330")].set_index("session_date")
    axis = snapshot["market_sessions"]["TWSE"]
    expected = pd.Series(100 + np.arange(610) * 0.1, index=axis[:610])
    expected = expected / expected.shift(252) - 1
    pd.testing.assert_series_equal(frame["return_252"], expected,
        check_names=False, check_freq=False)
    assert not result["features"].duplicated(research.KEYS).any()


def test_exact_labels_do_not_skip_a_missing_price_or_use_a_weekday_calendar():
    days = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-07", "2026-01-08"])
    features = pd.DataFrame(dict(stock_id=["2330"] * 3, market=["TWSE"] * 3,
        session_date=days[:3], signal_close=[100.0, np.nan, 110.0]))
    labels = research._exact_labels(features, {"TWSE": days},
        {"TWSE": days[2]}, (1,))
    assert labels.loc[0, "label_end"] == days[1]
    assert labels.loc[0, "label_state"] == "missing_target_price"
    assert pd.isna(labels.loc[0, "target_return"])
    assert labels.loc[1, "label_state"] == "missing_signal_price"
    assert labels.loc[2, "label_state"] == "unmatured"
    assert labels.loc[2, "label_end"] == days[3]
    unknown = research._exact_labels(features.iloc[[2]], {"TWSE": days[:3]},
        {"TWSE": days[2]}, (63,))
    assert unknown.iloc[0].label_state == "unknown_target_session"


def test_unknown_pit_and_units_remain_missing_not_zero():
    snapshot = _snapshot(90)
    dates = snapshot["market_sessions"]["TWSE"][:90]
    snapshot["frames"]["finmind_month_revenue"] = pd.DataFrame([
        dict(stock_id="2330", date=dates[0], revenue_year=2023,
             revenue_month=1, revenue=100, revenue_yoy=0.5, revenue_yoy_unit="ratio")])
    snapshot["frames"]["finmind_shareholding"] = pd.DataFrame([
        dict(stock_id="2330", date=day, holding_percentage=30.0) for day in dates])
    result = research._assemble(snapshot, (63,), threads=1, progress=None)
    frame = result["features"]
    assert frame["revenue_yoy"].isna().all()
    assert frame["foreign_holding_ratio"].isna().all()
    assert "revenue_yoy" in result["feature_order_by_horizon"]["63"]
    assert result["metadata"]["historical_PIT_unverified"] is True


def test_projection_removes_raw_payload_without_truncating_rows():
    stats = research._new_read_statistics()
    rows = [dict(stock_id="2330", date="2026-10-22", close=str(i + 1),
                 raw_payload="do-not-retain" * 100, units={"close": "TWD"}) for i in range(200)]
    projected = list(research._project_rows(iter(rows), "finmind_price_adj", {"2330"}, stats))
    assert len(projected) == 200
    assert stats["rows_seen"] == 200
    assert all("raw_payload" not in row for row in projected)
    assert projected[-1]["close"] == "200"


def test_query_is_existing_index_only_and_uses_no_spool(monkeypatch):
    calls = []
    closed = []
    def query(_root, _descriptor, _route, **kwargs):
        calls.append(kwargs)
        def iter_rows(**kw):
            calls.append(kw)
            yield dict(stock_id="2330", date="2026-10-22", close=10)
        return SimpleNamespace(iter_rows=iter_rows,
            close=lambda: closed.append(True), cache_key="index", predicate={},
            lineage={}, metrics={"cache_hit": True}, index_manifest_path=Path("index.json"),
            completion_path=Path("complete.json"))
    monkeypatch.setattr(research, "_query_rows", query)
    monkeypatch.setattr(research, "_proof_file", lambda path: dict(path=str(path), sha256="proof", size=1))
    item = dict(root="fixture", artifact_descriptor=dict(sha256="f" * 64, size=1),
        parent_identity="e" * 64, source_identity={})
    frame, binding, stats = research._read_route(None, item, "finmind_price_adj", {"2330"})
    assert calls[0]["allow_build"] is False
    assert calls[0]["consumer_temp_root"] is None
    assert calls[1] == {"order": "stock_shard"}
    assert binding["reader"] == "existing_verified_projection"
    assert closed == [True]
    assert len(frame) == stats["rows_kept"] == 1


def test_integrity_failure_never_becomes_stream_fallback(monkeypatch):
    def broken(*args, **kwargs):
        raise research.query.ProjectionIntegrityError("projection_hash_mismatch")
    monkeypatch.setattr(research, "_query_rows", broken)
    item = dict(root="fixture", artifact_descriptor={}, parent_identity="f" * 64)
    with pytest.raises(research.query.ProjectionIntegrityError):
        research._read_route(None, item, "finmind_price_adj", {"2330"})


def test_head_defaults_to_current_and_explicit_pin_never_accepts_an_old_head(monkeypatch):
    raw = json.dumps(dict(schema="stock-data-publication-v1", datasets=[], run_id="current")).encode("utf-8")
    monkeypatch.setattr(research, "_stable_bytes", lambda _path: raw)
    manifest, digest = research._current_manifest()
    assert manifest["run_id"] == "current"
    assert digest == hashlib.sha256(raw).hexdigest()
    assert research._current_manifest(digest)[1] == digest
    with pytest.raises(research.ResearchInputError, match="does_not_match_expected"):
        research._current_manifest("0" * 64)


def test_new_published_route_is_audited_without_inventing_an_adapter():
    plan = research._source_plan({"new_official_route": {}}, (63,))
    assert len(plan) == 37
    entry = next(item for item in plan if item["route_id"] == "new_official_route")
    assert entry["usage"] == "context-only"
    assert entry["category"] == "unmapped"


def test_explicit_aliases_choose_latest_clock_before_financial_pit_filter():
    stats = research._new_read_statistics()
    original = dict(stock_id="2330", date="2026-06-30", type="Revenue", value=10,
        available_time="2026-08-01T09:00:00+08:00", available_at_utc="2026-08-01T02:00:00Z",
        published_at_utc="2026-08-01T03:00:00Z", unit="TWD", units={"value": "TWD"},
        currency_unit="TWD", percentage_unit="percent", volume_unit="shares", lot_size=1000,
        raw_payload="must not survive")
    rows = list(research._project_rows(iter([original]), "finmind_financial_statements", {"2330"}, stats))
    assert len(rows) == 1
    row = rows[0]
    assert pd.Timestamp(row["available_time"]) == pd.Timestamp("2026-08-01T11:00:00+08:00")
    assert row["available_at_utc"] == original["available_at_utc"]
    assert row["published_at_utc"] == original["published_at_utc"]
    assert row["_research_original_available_time"] == original["available_time"]
    assert set(row["_research_available_time_fields"]) == {"available_time", "available_at_utc", "published_at_utc"}
    assert all(row[key] == original[key] for key in (
        "unit", "units", "currency_unit", "percentage_unit", "volume_unit", "lot_size"))
    assert "raw_payload" not in row
    assert stats["unverified_publication_time"] == 0
    assert original["available_time"] == "2026-08-01T09:00:00+08:00"


def test_collection_or_naive_time_never_becomes_pit():
    stats = research._new_read_statistics()
    rows = [dict(stock_id="2330", date="2026-06-30", type="Revenue", value=10,
                 available_at="2026-08-01 10:00:00", retrieved_at="2026-08-01T15:00:00+08:00",
                 _shared_collected_at="2026-08-01T15:00:00+08:00")]
    assert list(research._project_rows(iter(rows), "finmind_financial_statements", {"2330"}, stats)) == []
    assert stats["unverified_publication_time"] == 1


def test_normalised_financial_alias_cannot_signal_before_latest_publication():
    snapshot = _snapshot(90)
    days = snapshot["market_sessions"]["TWSE"]
    publication = days[50].strftime("%Y-%m-%d")
    rows = [dict(stock_id="2330", date=days[0].strftime("%Y-%m-%d"), type="Revenue", value=10,
                 available_at=publication + "T15:00:00+08:00", currency_unit="TWD"),
            dict(stock_id="2330", date=days[0].strftime("%Y-%m-%d"), type="OperatingIncome", value=3,
                 published_at_utc=publication + "T03:00:00Z", currency_unit="TWD")]
    projected = list(research._project_rows(iter(rows), "finmind_financial_statements", {"2330"},
        research._new_read_statistics()))
    snapshot["frames"]["finmind_financial_statements"] = research._normalise_frame(
        projected, "finmind_financial_statements")
    result = research._assemble(snapshot, (63,), threads=1, progress=None)
    frame = result["features"].loc[lambda f: f.stock_id.eq("2330")].set_index("session_date")
    assert pd.isna(frame.loc[days[50], "financial_operating_margin"])
    assert frame.loc[days[51], "financial_operating_margin"] == pytest.approx(0.3)


def test_labels_use_each_markets_own_axis_and_cutoff():
    twse = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-07"])
    tpex = pd.to_datetime(["2026-01-02", "2026-01-06", "2026-01-07"])
    frame = pd.DataFrame([dict(stock_id="2330", market="TWSE", session_date=twse[0], signal_close=100),
        dict(stock_id="2330", market="TWSE", session_date=twse[1], signal_close=102),
        dict(stock_id="6488", market="TPEX", session_date=tpex[0], signal_close=10),
        dict(stock_id="6488", market="TPEX", session_date=tpex[1], signal_close=11)])
    labels = research._exact_labels(frame, {"TWSE": twse, "TPEX": tpex},
        {"TWSE": twse[-1], "TPEX": tpex[1]}, (1,))
    first = labels.loc[labels.session_date.eq(twse[0])].set_index("market")
    assert first.loc["TWSE", "label_end"] == twse[1]
    assert first.loc["TPEX", "label_end"] == tpex[1]
    assert first.loc["TPEX", "target_return"] == pytest.approx(0.1)
    assert labels.loc[labels.session_date.eq(tpex[1]), "label_state"].item() == "unmatured"


def test_empty_price_stock_and_partial_source_do_not_disappear():
    snapshot = _snapshot(80)
    snapshot["frames"]["finmind_price_adj"] = snapshot["frames"]["finmind_price_adj"].loc[
        lambda f: f.stock_id.eq("2330")].copy()
    result = research._assemble(snapshot, (63,), threads=2, progress=None)
    frame = result["features"]
    assert set(frame.stock_id) == {"1101", "2330", "9999"}
    assert frame.loc[frame.stock_id.eq("1101"), "signal_close"].isna().all()
    assert any(row["publication_status"] == "PARTIAL" for row in result["source_audit"])
    assert result["coverage"]["ordinary_stock_market_pairs"] == 3


def test_assembly_does_not_mutate_input_frames_or_source_manifest():
    snapshot = _snapshot(65)
    before = {route: frame.copy(deep=True) for route, frame in snapshot["frames"].items()}
    plan = copy.deepcopy(snapshot["source_manifest"])
    research._assemble(snapshot, (63,), threads=1, progress=None)
    for route, frame in before.items():
        pd.testing.assert_frame_equal(snapshot["frames"][route], frame)
    assert snapshot["source_manifest"] == plan


def _late_financial_snapshot():
    snapshot = _snapshot(210)
    revenue = [dict(stock_id="2330", date="2022-01-01", revenue_year=2021, revenue_month=12,
                    revenue=100, available_at="2022-01-10T11:00:00+08:00"),
               dict(stock_id="2330", date="2023-01-01", revenue_year=2022, revenue_month=12,
                    revenue=150, available_at="2023-06-30T11:00:00+08:00")]
    cashflow = [dict(stock_id="2330", date="2021-12-31", period_start="2021-01-01", period_end="2021-12-31",
                     period_basis="annual", value=100, type="CashFlowsFromUsedInOperatingActivities",
                     published_at_utc="2022-03-31T03:00:00Z"),
                dict(stock_id="2330", date="2022-12-31", period_start="2022-01-01", period_end="2022-12-31",
                     period_basis="annual", value=160, type="CashFlowsFromUsedInOperatingActivities",
                     published_at_utc="2023-06-30T03:00:00Z")]
    for route, rows in (("finmind_month_revenue", revenue), ("finmind_cash_flows_statement", cashflow)):
        projected = list(research._project_rows(iter(rows), route, {"2330"}, research._new_read_statistics()))
        snapshot["frames"][route] = research._normalise_frame(projected, route)
    return snapshot


def _reference_features_at(snapshot, signal):
    market = "TWSE"
    axis = snapshot["market_sessions"][market]
    signal_axis = axis[axis <= signal]
    price = snapshot["frames"]["finmind_price_adj"].loc[lambda frame: frame.stock_id.eq("2330")]
    board = snapshot["frames"]["official_market_daily"].loc[lambda frame: frame.stock_id.eq("2330")]
    panel, _ = research._price_panel(price, board, dict(stock_id="2330", market=market), signal_axis, signal)
    return research.catalog.build_candidate_features(panel, {market: axis},
        asof_by_market={market: signal.strftime("%Y-%m-%d")}, horizons=[63],
        source_frames=snapshot["frames"], source_manifest=snapshot["source_manifest"])["features"]


@pytest.mark.parametrize("output_sessions", [35, 84])
def test_all_38_columns_match_signal_asof_across_financial_chunk_and_expiry_boundaries(monkeypatch, output_sessions):
    snapshot = _late_financial_snapshot()
    monkeypatch.setattr(research, "_OUTPUT_SESSIONS", output_sessions)
    result = research._assemble(snapshot, (63,), threads=1, progress=None)
    names = list(result["feature_metadata"])
    for signal in pd.to_datetime(["2023-06-29", "2023-06-30", "2023-07-03"]):
        expected = _reference_features_at(snapshot, signal)
        expected_days = pd.to_datetime(expected.session_date)
        expected_row = expected.loc[expected_days.eq(signal), names].iloc[0]
        actual_row = result["features"].loc[lambda frame:
            frame.stock_id.eq("2330") & frame.session_date.eq(signal), names].iloc[0]
        pd.testing.assert_series_equal(actual_row, expected_row, check_names=False)
        if signal == pd.Timestamp("2023-06-30"):
            assert actual_row["revenue_yoy"] == pytest.approx(0.5)
            assert actual_row["cashflow_operating_change_ratio"] == pytest.approx(0.6)
        else:
            assert pd.isna(actual_row["revenue_yoy"])
            assert pd.isna(actual_row["cashflow_operating_change_ratio"])


def test_financial_recalculation_is_bounded_by_events_not_every_signal_date(monkeypatch):
    snapshot = _late_financial_snapshot()
    axis = snapshot["market_sessions"]["TWSE"][:210]
    evaluations = []
    original = research._financial_state
    def counted(events, route, lower, cutoff):
        evaluations.append((route, lower, cutoff))
        return original(events, route, lower, cutoff)
    monkeypatch.setattr(research, "_financial_state", counted)
    history, count = research._financial_history(snapshot["frames"], snapshot["source_manifest"],
        "2330", "TWSE", axis)
    assert len(history) == len(axis)
    assert count == len(evaluations)
    assert count <= 2 * 4 + 2
    assert count < len(axis)


def test_sparse_price_readiness_uses_signal_window_not_chunk_endpoint():
    snapshot = _snapshot(610)
    dates = snapshot["market_sessions"]["TWSE"][:610]
    snapshot["frames"]["finmind_price_adj"] = snapshot["frames"]["finmind_price_adj"].loc[
        lambda frame: frame.date.isin(dates[::6])].copy()
    signal = dates[420]
    result = research._assemble(snapshot, (63,), threads=1, progress=None)
    row = result["features"].loc[lambda frame: frame.stock_id.eq("2330") & frame.session_date.eq(signal)].iloc[0]
    expected = _reference_features_at(snapshot, signal)
    reference = expected.loc[pd.to_datetime(expected.session_date).eq(signal)].iloc[0]
    assert row.feature_ready == reference.feature_ready
    assert bool(row.feature_ready) is True


def _sparse_relative_snapshot(mixed_units=False):
    snapshot = _snapshot(210)
    prefix = pd.bdate_range("2022-01-03", "2022-12-30")
    snapshot["market_sessions"]["TWSE"] = prefix.append(snapshot["market_sessions"]["TWSE"])
    previous = pd.bdate_range("2022-01-03", periods=20, freq="17B")
    rows = [dict(stock_id="2330", date=day, foreign_investors_net=float(i + 1),
                 foreign_investors_net_unit="lots" if mixed_units and i % 2 else "shares",
                 available_time=day.strftime("%Y-%m-%d") + "T13:00:00+08:00")
            for i, day in enumerate(previous)]
    rows.append(dict(stock_id="2330", date=pd.Timestamp("2023-06-29"), foreign_investors_net=100.0,
        foreign_investors_net_unit="shares", available_time="2023-06-30T10:00:00+08:00"))
    snapshot["frames"]["finmind_institutional"] = pd.DataFrame(rows)
    return snapshot


@pytest.mark.parametrize("output_sessions", [35, 84])
def test_sparse_relative20_fallback_matches_direct_asof_without_full_date_recomputation(monkeypatch, output_sessions):
    snapshot = _sparse_relative_snapshot()
    monkeypatch.setattr(research, "_OUTPUT_SESSIONS", output_sessions)
    result = research._assemble(snapshot, (63,), threads=1, progress=None)
    signal = pd.Timestamp("2023-06-30")
    expected = _reference_features_at(snapshot, signal)
    names = list(result["feature_metadata"])
    expected_row = expected.loc[pd.to_datetime(expected.session_date).eq(signal), names].iloc[0]
    actual = result["features"].loc[lambda frame:
        frame.stock_id.eq("2330") & frame.session_date.eq(signal), names].iloc[0]
    pd.testing.assert_series_equal(actual, expected_row, check_names=False)
    assert actual.foreign_net_relative_20 == pytest.approx(100 / 10.5)
    statistics = result["metadata"]["sparse_state_fallback"]
    assert 0 < statistics["rows"] < 210
    assert statistics["catalog_calls"] == 1
    assert statistics["elapsed_seconds"] >= 0


@pytest.mark.parametrize("output_sessions", [35, 84])
def test_sparse_macro_predecessor_fallback_is_shared_across_stocks(monkeypatch, output_sessions):
    snapshot = _snapshot(210)
    snapshot["frames"]["global_market_history"] = pd.DataFrame([
        dict(instrument="us_sp500", date=pd.Timestamp("2022-01-03"), close=100.0,
             available_time="2022-01-03T23:00:00+08:00"),
        dict(instrument="us_sp500", date=pd.Timestamp("2023-01-03"), close=150.0,
             available_time="2023-06-30T10:00:00+08:00")])
    monkeypatch.setattr(research, "_OUTPUT_SESSIONS", output_sessions)
    result = research._assemble(snapshot, (63,), threads=1, progress=None)
    signal = pd.Timestamp("2023-06-30")
    expected = _reference_features_at(snapshot, signal)
    value = expected.loc[pd.to_datetime(expected.session_date).eq(signal), "global_sp500_return_1"].item()
    rows = result["features"].loc[lambda frame: frame.session_date.eq(signal)]
    assert np.allclose(rows.global_sp500_return_1, value)
    assert value == pytest.approx(0.5)
    statistics = result["metadata"]["sparse_state_fallback"]
    assert statistics["rows"] >= 3
    assert statistics["catalog_calls"] < statistics["rows"]
    assert statistics["macro_cache_hits"] > 0


def test_dense_relative20_does_not_trigger_fallback():
    snapshot = _snapshot(610)
    days = snapshot["market_sessions"]["TWSE"][:610]
    snapshot["frames"]["finmind_institutional"] = pd.DataFrame([
        dict(stock_id="2330", date=day, foreign_investors_net=float(i + 1),
             foreign_investors_net_unit="shares",
             available_time=day.strftime("%Y-%m-%d") + "T13:00:00+08:00") for i, day in enumerate(days)])
    result = research._assemble(snapshot, (63,), threads=1, progress=None)
    statistics = result["metadata"]["sparse_state_fallback"]
    assert statistics["rows"] == statistics["catalog_calls"] == 0


def test_sparse_history_without_twenty_same_unit_predecessors_does_not_fallback():
    result = research._assemble(_sparse_relative_snapshot(mixed_units=True), (63,), threads=1, progress=None)
    statistics = result["metadata"]["sparse_state_fallback"]
    assert statistics["rows"] == statistics["catalog_calls"] == 0
    assert result["features"]["foreign_net_relative_20"].isna().all()


def test_full_date_fallback_stops_before_calling_catalog(monkeypatch):
    days = pd.to_datetime(["2023-06-29", "2023-06-30"])
    features = pd.DataFrame(dict(stock_id=["2330"] * 2, session_date=days))
    plans = {("2330", day): {"foreign_net_relative_20": 1.0} for day in days}
    def forbidden(*args, **kwargs):
        raise AssertionError("full-date catalog recomputation must not run")
    monkeypatch.setattr(research, "_direct_sparse_features", forbidden)
    with pytest.raises(research.ResearchInputError, match="recompute_every_signal"):
        research._apply_sparse_fallback(features, pd.DataFrame(), {}, [], days, "TWSE", (63,), plans, {})


def test_lost_seed_with_unchanged_output_does_not_plan_fallback():
    signal = pd.Timestamp("2023-06-30")
    at = pd.Timestamp("2023-06-30T10:00:00+08:00").value
    sequence = dict(events=[(at, pd.Timestamp("2023-06-29").value,
        pd.Timestamp("2022-01-03").value, 1.2)], times=np.array([at], dtype=np.int64), max_age=1)
    features = pd.DataFrame([dict(stock_id="2330", session_date=signal, foreign_net_relative_20=1.2)])
    axis = pd.bdate_range("2022-01-03", signal)
    plans = research._plan_sparse_fallback(features, {"2330": {"foreign_net_relative_20": sequence}},
        {}, axis, pd.Timestamp("2022-02-01"), {})
    assert plans == {}


@pytest.mark.parametrize("output_sessions", [35, 84])
def test_macro_reuse_is_per_market_window_across_batches_and_matches_catalog(monkeypatch, output_sessions):
    snapshot = _snapshot(210)
    axis = snapshot["market_sessions"]["TWSE"]
    tpex = axis.delete(50)
    snapshot["market_sessions"]["TPEX"] = tpex
    snapshot["asof_by_market"]["TPEX"] = snapshot["asof_by_market"]["TWSE"]
    snapshot["records"][0]["market"] = "TPEX"
    for route in ("finmind_price_adj", "official_market_daily"):
        frame = snapshot["frames"][route]
        frame = frame.loc[~(frame.stock_id.eq("1101") & ~frame.date.isin(tpex))].copy()
        if "market" in frame:
            frame.loc[frame.stock_id.eq("1101"), "market"] = "TPEX"
        snapshot["frames"][route] = frame
    days = axis[:210]
    snapshot["frames"]["global_market_history"] = pd.DataFrame([
        dict(instrument=symbol, date=day, close=100 + i + offset,
             available_time=day.strftime("%Y-%m-%d") + "T13:00:00+08:00")
        for offset, symbol in enumerate(("us_sp500", "us_nasdaq", "us_semiconductor"))
        for i, day in enumerate(days)])
    snapshot["frames"]["finmind_exchange_rate"] = pd.DataFrame([
        dict(currency="USD", date=day, spot_buy=30 + i / 100,
             available_time=day.strftime("%Y-%m-%d") + "T13:00:00+08:00") for i, day in enumerate(days)])
    snapshot["frames"]["finmind_government_bonds_yield"] = pd.DataFrame([
        dict(instrument="TW10Y", date=day, value=1 + i / 1000,
             available_time=day.strftime("%Y-%m-%d") + "T14:00:00+08:00") for i, day in enumerate(days)])
    for item in snapshot["source_manifest"]:
        if item["route_id"] == "finmind_government_bonds_yield":
            item.update(selected_instrument="TW10Y", instrument_verified=True)
    monkeypatch.setattr(research, "_OUTPUT_SESSIONS", output_sessions)
    original = research.catalog.build_candidate_features
    macro_calls = []
    def counted(*args, **kwargs):
        if set(kwargs.get("source_frames", {})) & research._MACRO:
            macro_calls.append((tuple(kwargs["market_sessions"] if "market_sessions" in kwargs else args[1]),
                                tuple(kwargs["asof_by_market"].values())))
        return original(*args, **kwargs)
    monkeypatch.setattr(research.catalog, "build_candidate_features", counted)
    monkeypatch.setattr(research, "_BATCH_STOCKS", 1)
    small = research._assemble(snapshot, (63,), threads=2, progress=None)
    first_count = len(macro_calls)
    expected_calls = sum(len(list(research._feature_windows(pd.DatetimeIndex(values)[
        pd.DatetimeIndex(values) <= snapshot["asof_by_market"][market]])))
        for market, values in snapshot["market_sessions"].items())
    assert first_count == expected_calls
    assert small["metadata"]["macro_reuse"]["market_histories"] == 2
    assert small["metadata"]["macro_reuse"]["batch_cache_hits"] == 3
    assert small["metadata"]["worker_execution"]["mode"] == "bounded_spawn_processes"
    assert all(pid != os.getpid() for pid in small["metadata"]["worker_execution"]["worker_pids"])
    macro_calls.clear()
    monkeypatch.setattr(research, "_BATCH_STOCKS", 8)
    large = research._assemble(snapshot, (63,), threads=1, progress=None)
    assert large["metadata"]["worker_execution"]["mode"] == "serial_in_memory"
    assert len(macro_calls) == first_count
    pd.testing.assert_frame_equal(small["features"], large["features"])
    columns = [name for name, definition in research.catalog._definitions().items()
               if definition["route"] in research._MACRO]
    for stock, market in (("2330", "TWSE"), ("1101", "TPEX")):
        source_axis = snapshot["market_sessions"][market]
        for signal in (days[0], days[83], days[-1]):
            own_axis = source_axis[source_axis <= signal]
            price = snapshot["frames"]["finmind_price_adj"].loc[lambda f: f.stock_id.eq(stock)]
            board = snapshot["frames"]["official_market_daily"].loc[lambda f: f.stock_id.eq(stock)]
            panel, _ = research._price_panel(price, board, dict(stock_id=stock, market=market), own_axis, signal)
            expected = original(panel, {market: source_axis}, asof_by_market={market: signal.strftime("%Y-%m-%d")},
                horizons=[63], source_frames=snapshot["frames"], source_manifest=snapshot["source_manifest"])["features"]
            expected = expected.loc[pd.to_datetime(expected.session_date).eq(signal), columns].iloc[0]
            actual = small["features"].loc[lambda f: f.stock_id.eq(stock) & f.session_date.eq(signal), columns].iloc[0]
            pd.testing.assert_series_equal(actual, expected, check_names=False)
    assert small["metadata"]["macro_reuse"]["unique_macro_fallback_rows"] == 0


def test_macro_worker_payload_is_small_pickleable_and_has_no_source_handles(monkeypatch):
    snapshot = _snapshot(90)
    axis = snapshot["market_sessions"]["TWSE"][:90]
    indices = {route: research._group_index(frame) for route, frame in snapshot["frames"].items()}
    record = snapshot["records"][1]
    panel, _ = research._price_panel(research._stock_rows(snapshot["frames"]["finmind_price_adj"], indices["finmind_price_adj"], "2330"),
        research._stock_rows(snapshot["frames"]["official_market_daily"], indices["official_market_daily"], "2330"),
        record, axis, snapshot["asof_by_market"]["TWSE"])
    shared = dict(macro_series={}, macro_values={}, lock=research.threading.Lock())
    macro, _ = research._macro_history(panel, snapshot, axis, "TWSE", (63,), shared, None)
    payload = research._worker_payload([record], snapshot, indices, axis, (63,), macro)
    assert set(payload) == {"records", "frames", "market_sessions", "asof_by_market", "source_manifest", "axis", "horizons", "macro", "output_sessions"}
    assert not set(payload["frames"]) & research._MACRO
    assert all(set(frame.stock_id) == {"2330"} for frame in payload["frames"].values())
    pickle.loads(pickle.dumps(payload))
    def forbidden(*args, **kwargs):
        raise AssertionError("child must not read sources or create a cache")
    monkeypatch.setattr(research, "_load_snapshot", forbidden)
    monkeypatch.setattr(research, "_read_route", forbidden)
    monkeypatch.setattr(research.runtime, "_load_context", forbidden)
    result = research._process_feature_batch(payload)
    assert len(result[0]) == 90 and set(result[0].stock_id) == {"2330"}


def test_macro_child_guard_denies_source_reads_disk_writes_and_network():
    with pytest.raises(research.ResearchInputError, match="source_io"):
        research._deny_child_io("open", (r"D:\TWStockData\shared_market_data\object.json", "r", 0))
    with pytest.raises(research.ResearchInputError, match="filesystem_write"):
        research._deny_child_io("open", (r"C:\Temp\cache.bin", "wb", os.O_WRONLY))
    with pytest.raises(research.ResearchInputError, match="network"):
        research._deny_child_io("socket.connect", (None, None))
    research._deny_child_io("open", (__file__, "r", 0))


def _bond_identity_case(monkeypatch, *, published=False, damage=None):
    route, name = "finmind_government_bonds_yield", "United States 10-Year"
    declared = dict(catalog_id="declared_bond_series_catalog", provider="finmind",
        route_id=route, dataset="GovernmentBondsYield",
        selector_mapping=dict(normalized_field="name", provider_field="name", request_parameter="data_id"),
        items=[dict(series_id="us10y", provider_data_id=name, selector=dict(data_id=name))])
    if damage == "catalog_missing":
        document = {}
    else:
        if damage == "selector_conflict":
            declared["items"][0]["selector"]["data_id"] = "us10y"
        document = dict(capability_catalog=dict(series_catalogs=dict(catalogs={
            "declared_bond_series_catalog": declared})))
    raw = json.dumps(document).encode("utf-8")
    monkeypatch.setattr(research, "_stable_bytes", lambda path: raw if path == research.REQUIREMENTS_PATH else b"")
    receipt = dict(dataset="GovernmentBondsYield", data_id=name, sha256="c" * 64, size=100,
        retrieved_at="2026-01-08T12:00:00+08:00", source_scope=dict(route_id=route,
            request_identity=dict(dataset="GovernmentBondsYield", data_id=name)))
    entry = dict(route_id=route, sha256="b" * 64, size=200,
        metadata=dict(route_window_policy=dict(registry_binding=dict(
            sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))), source_receipts=[receipt]))
    if damage == "receipts_missing":
        entry["metadata"]["source_receipts"] = []
    elif damage == "receipt_conflict":
        entry["metadata"]["source_receipts"].append(dict(receipt, data_id="Other Bond"))
    elif damage == "nested_conflict":
        receipt["source_scope"]["request_identity"]["data_id"] = "Other Bond"
    elif damage == "registry_mismatch":
        entry["metadata"]["route_window_policy"]["registry_binding"]["sha256"] = "0" * 64
    elif damage == "receipt_proof_missing":
        receipt.pop("sha256")
    days = pd.bdate_range("2026-01-02", periods=4)
    rows = [dict(name=name, date=day.strftime("%Y-%m-%d"), value=4 + i / 10) for i, day in enumerate(days)]
    if published:
        for row in rows:
            row["available_time"] = row["date"] + "T08:00:00+08:00"
    if damage == "raw_name_mismatch":
        rows[0]["name"] = "us10y"
    elif damage == "raw_precedence_conflict":
        for row in rows:
            row["symbol"] = "us10y"
    frame = research._normalise_frame(rows, route)
    selection = next(item for item in research.catalog.select_source_plan([63]) if item["route_id"] == route)
    item = research.runtime._availability(selection, dict(artifact_descriptor=dict(entry, status="SUCCESS"),
        source_identity={}), frame, {}, [])
    return item, entry, frame, "a" * 64, 7


def test_bond_identity_binds_declared_raw_name_receipt_and_contract_without_mutation(monkeypatch):
    args = _bond_identity_case(monkeypatch)
    original, frame = copy.deepcopy(args[0]), args[2].copy(deep=True)
    bound = research._bind_bond_identity(*args)
    assert bound["selected_instrument"] == "United States 10-Year"
    assert bound["instrument_verified"] is True
    proof = bound["instrument_identity_binding"]
    assert proof["series_id"] == "us10y"
    assert proof["contract"]["json_pointer"] == research._BOND_CATALOG_POINTER
    assert proof["head"]["sha256"] == "a" * 64
    assert proof["source_receipts"][0]["json_pointer"] == "/datasets/7/metadata/source_receipts/0"
    assert proof["publication_time_verified_by_identity"] is False
    assert not {"available_time", "available_at", "unit", "units"} & set(bound)
    assert args[0] == original
    pd.testing.assert_frame_equal(args[2], frame)


@pytest.mark.parametrize("damage", ["catalog_missing", "receipts_missing", "receipt_conflict",
    "nested_conflict", "registry_mismatch", "receipt_proof_missing", "selector_conflict",
    "raw_name_mismatch", "raw_precedence_conflict"])
def test_bond_identity_missing_or_conflicting_evidence_fails_closed(monkeypatch, damage):
    with pytest.raises(research.ResearchInputError, match="bond_identity_"):
        research._bind_bond_identity(*_bond_identity_case(monkeypatch, damage=damage))


@pytest.mark.parametrize("published", [False, True])
def test_bond_identity_preserves_publication_gate_and_exact_catalog_name_match(monkeypatch, published):
    args = _bond_identity_case(monkeypatch, published=published)
    bound = research._bind_bond_identity(*args)
    frame = args[2]
    days = pd.DatetimeIndex(frame.date)
    panel = pd.concat([pd.DataFrame(dict(stock_id=stock, market=market, session_date=days,
        adjusted_close=100.0)) for stock, market in (("2330", "TWSE"), ("6488", "TPEX"))], ignore_index=True)
    result = research.catalog.build_candidate_features(panel, {"TWSE": days, "TPEX": days},
        asof_by_market={market: days[-1].strftime("%Y-%m-%d") for market in ("TWSE", "TPEX")},
        horizons=[63], source_frames={bound["route_id"]: frame}, source_manifest=[bound])
    values = result["features"].bond_yield_change_1
    codes = {row["code"] for row in result["metadata"]["limitations"] if row["route_id"] == bound["route_id"]}
    assert "instrument_selection_required" not in codes
    if published:
        assert values.notna().any()
        assert np.allclose(values.dropna(), .1)
    else:
        assert values.isna().all()
        assert "availability_time_unknown" in codes
        assert "available_time" not in frame
