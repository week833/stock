"""Pure-memory contract tests; execution is delegated to the validation worker."""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stock_daily_feature_catalog import (  # noqa: E402
    FEATURE_SCHEMA,
    SOURCE_CATALOG,
    build_candidate_features,
    select_source_plan,
)


def fixture_panel(count=100, market="TWSE"):
    axis = pd.bdate_range("2024-01-02", periods=count + 70)
    panel = pd.DataFrame({
        "stock_id": "2330", "market": market,
        "session_date": axis[:count].strftime("%Y-%m-%d"),
        "adjusted_close": np.arange(count, dtype=float) + 100,
        "volume": 2000.0, "volume_unit": "股", "amount": 100000.0,
        "open": 100.0, "high": 105.0, "low": 95.0,
        "raw_open": 100.0, "raw_high": 105.0, "raw_low": 95.0, "raw_close": 101.0,
    })
    return panel, {market: axis.strftime("%Y-%m-%d").tolist()}, {
        market: axis[count - 1].strftime("%Y-%m-%d")}


def build(panel=None, calendars=None, asofs=None, **kwargs):
    if panel is None:
        panel, calendars, asofs = fixture_panel()
    return build_candidate_features(
        panel, calendars, asof_by_market=asofs,
        horizons=kwargs.pop("horizons", [1, 63]), **kwargs)


def test_catalog_and_selection_keep_all_routes_without_requiring_all_sources():
    plan = select_source_plan([1])
    assert len(SOURCE_CATALOG) == len(plan) == 36
    assert set(item["route_id"] for item in plan) == set(SOURCE_CATALOG)
    assert {item["usage"] for item in plan} == {"used", "context-only", "not-selected"}
    assert all(item["name_zh"] and item["reason_zh"] for item in plan)
    assert all("小朋友" not in item["name_zh"] for item in plan)
    short = {item["route_id"]: item for item in plan}
    long = {item["route_id"]: item for item in select_source_plan([63], "塑化")}
    assert short["finmind_financial_statements"]["usage"] == "context-only"
    assert long["finmind_financial_statements"]["usage"] == "used"
    assert long["finmind_crude_oil_prices"]["usage"] == "used"
    plan[0]["reason_zh"] = "外部修改"
    assert select_source_plan([1])[0]["reason_zh"] != "外部修改"


def test_short_core_ready_without_253_warmup_and_seven_features_preserved():
    result = build()
    rows = result["features"]
    assert result["metadata"]["feature_schema"] == FEATURE_SCHEMA
    assert rows.iloc[-1]["feature_ready"]
    assert rows["return_252"].isna().all()
    assert rows["return_126"].isna().all()
    assert len(result["metadata"]["legacy_feature_order"]) == 7
    assert "return_252" not in result["metadata"]["feature_order_by_horizon"]["1"]
    assert "return_252" in result["metadata"]["feature_order_by_horizon"]["63"]
    assert all(name in rows for name in result["metadata"]["feature_order"])
    assert result["metadata"]["coverage"]["features"]["return_252"]["available_rows"] == 0


def test_readiness_counts_actual_prices_without_requiring_contiguous_253_days():
    panel, calendars, asofs = fixture_panel()
    panel.loc[[5, 10, 15], "adjusted_close"] = np.nan
    rows = build(panel, calendars, asofs)["features"]
    assert not rows.iloc[61].feature_ready
    assert rows.iloc[62].feature_ready
    assert rows.iloc[62].priced_observations == 60
    assert rows.iloc[62].signal_close == panel.iloc[62].adjusted_close


def test_eighteen_calendar_months_and_own_market_asof():
    panel, calendars, asofs = fixture_panel(500)
    other = panel.copy()
    other["stock_id"], other["market"] = "6488", "TPEx"
    other = other.iloc[:-5]
    calendars["TPEx"] = calendars["TWSE"]
    asofs["TPEx"] = other.iloc[-1]["session_date"]
    result = build(pd.concat([panel, other]), calendars, asofs)
    for market, day in result["metadata"]["asof_by_market"].items():
        rows = result["features"].loc[result["features"].market == market]
        assert rows.session_date.max() == day
        assert pd.Timestamp(rows.session_date.min()) >= pd.Timestamp(day) - pd.DateOffset(months=18)


def test_macro_predecessor_stays_inside_each_markets_own_eighteen_month_window():
    panel, calendars, asofs = fixture_panel(500)
    other = panel.iloc[:-5].copy()
    other["stock_id"], other["market"] = "6488", "TPEX"
    calendars["TPEX"] = calendars["TWSE"]
    asofs["TPEX"] = other.iloc[-1].session_date
    cutoff = pd.Timestamp(asofs["TWSE"]) - pd.DateOffset(months=18)
    days = pd.DatetimeIndex(calendars["TWSE"])
    previous = days[days < cutoff][-1].strftime("%Y-%m-%d")
    current = days[days >= cutoff][0].strftime("%Y-%m-%d")
    source = pd.DataFrame([{"date": day, "currency": "USD", "spot_buy": value,
                            "available_time": day + "T10:00:00+08:00"}
                           for day, value in [(previous, 30), (current, 33)]])
    result = build(pd.concat([panel, other]), calendars, asofs,
                   source_frames={"finmind_exchange_rate": source})["features"]
    assert result.loc[result.market == "TWSE", "exchange_rate_change_1"].isna().all()
    assert result.loc[result.market == "TPEX", "exchange_rate_change_1"].notna().any()


def test_missing_market_day_is_not_filled_or_used_as_a_later_endpoint():
    panel, calendars, asofs = fixture_panel()
    signal = panel.iloc[10]["session_date"]
    missing = panel.iloc[11]["session_date"]
    result = build(panel.drop(index=11), calendars, asofs, horizons=[1])
    row = result["labels"].loc[result["labels"].session_date == signal].iloc[0]
    assert row.label_end == missing
    assert row.label_state == "missing_target_price"
    assert pd.isna(row.target_return)
    assert pd.isna(result["features"].loc[result["features"].session_date == missing, "return_1"]).all()


@pytest.mark.parametrize("problem", ["future", "duplicate", "off_calendar", "nonpositive"])
def test_invalid_price_input_is_rejected(problem):
    panel, calendars, asofs = fixture_panel()
    if problem == "future":
        panel.loc[len(panel)] = panel.iloc[-1]
        panel.loc[len(panel) - 1, "session_date"] = calendars["TWSE"][100]
    elif problem == "duplicate":
        panel = pd.concat([panel, panel.iloc[:1]])
    elif problem == "off_calendar":
        panel.loc[0, "session_date"] = "2024-01-06"
    else:
        panel.loc[0, "adjusted_close"] = 0
    with pytest.raises(ValueError):
        build(panel, calendars, asofs)


def test_date_only_daily_data_is_lagged_one_verified_session_and_disclosed():
    panel, calendars, asofs = fixture_panel()
    day, next_day = panel.iloc[10:12].session_date
    source = pd.DataFrame([{"stock_id": "2330", "date": day, "per": 12, "pbr": 2}])
    result = build(panel, calendars, asofs, source_frames={"finmind_per": source})
    rows = result["features"].set_index("session_date")
    assert pd.isna(rows.loc[day, "valuation_per"])
    assert rows.loc[next_day, "valuation_per"] == 12
    manifest = {item["route_id"]: item for item in result["metadata"]["source_manifest"]}
    assert manifest["finmind_per"]["historical_PIT_unverified"] is True
    assert manifest["finmind_per"]["availability_assumption"]


def test_institutional_and_daytrade_ratios_require_compatible_quantity_units():
    panel, calendars, asofs = fixture_panel()
    day, next_day = panel.iloc[10:12].session_date
    institutional = pd.DataFrame([{"stock_id": "2330", "date": day,
                                   "foreign_investors_net": 500, "volume_unit": "股"}])
    daytrade = pd.DataFrame([{"stock_id": "2330", "date": day,
                             "day_trade_volume": 1, "volume_unit": "張"}])
    result = build(panel, calendars, asofs, source_frames={
        "finmind_institutional": institutional, "finmind_day_trading": daytrade})
    row = result["features"].set_index("session_date").loc[next_day]
    assert row.foreign_net_volume_ratio == pytest.approx(0.25)
    assert pd.isna(row.day_trade_volume_ratio)
    converted = build(panel, calendars, asofs, source_frames={"finmind_day_trading": daytrade},
                      source_manifest=[{"route_id": "finmind_day_trading", "lot_size": 1000}])
    assert converted["features"].set_index("session_date").loc[next_day, "day_trade_volume_ratio"] == 0.5


def test_unknown_quantity_units_still_allow_same_source_standardization():
    panel, calendars, asofs = fixture_panel()
    source = pd.DataFrame([{"stock_id": "2330", "date": panel.iloc[i].session_date,
                            "foreign_investors_net": i + 1} for i in range(25)])
    result = build(panel, calendars, asofs, source_frames={"finmind_institutional": source})
    rows = result["features"].set_index("session_date")
    assert rows["foreign_net_volume_ratio"].isna().all()
    assert rows.loc[panel.iloc[21].session_date, "foreign_net_relative_20"] == pytest.approx(21 / 10.5)
    assert any(item["code"] == "quantity_unit_unproved" for item in result["metadata"]["limitations"])


def test_ratios_use_source_date_volume_not_next_day_volume():
    panel, calendars, asofs = fixture_panel()
    day, next_day = panel.iloc[10:12].session_date
    panel.loc[11, "volume"] = 10000
    source = pd.DataFrame([{"stock_id": "2330", "date": day,
                           "foreign_investors_net": 500, "volume_unit": "股"}])
    result = build(panel, calendars, asofs, source_frames={"finmind_institutional": source})
    assert result["features"].set_index("session_date").loc[next_day, "foreign_net_volume_ratio"] == 0.25


def test_holdings_and_margin_ratios_have_explicit_units_and_no_hidden_fill():
    panel, calendars, asofs = fixture_panel()
    day, next_day = panel.iloc[10:12].session_date
    frames = {
        "finmind_shareholding": pd.DataFrame([{"stock_id": "2330", "date": day,
            "holding_percentage": 25, "units": {"holding_percentage": "%"}}]),
        "finmind_margin": pd.DataFrame([{"stock_id": "2330", "date": day,
            "MarginPurchaseTodayBalance": 110, "MarginPurchaseYesterdayBalance": 100}]),
    }
    row = build(panel, calendars, asofs, source_frames=frames)["features"].set_index("session_date").loc[next_day]
    assert row.foreign_holding_ratio == 0.25
    assert row.margin_balance_change_ratio == pytest.approx(0.1)


def test_zero_or_negative_valuation_is_not_a_low_valuation_signal_but_zero_yield_is_valid():
    panel, calendars, asofs = fixture_panel()
    day, next_day = panel.iloc[10:12].session_date
    source = pd.DataFrame([{"stock_id": "2330", "date": day, "per": 0, "pbr": -1,
                            "dividend_yield": 0, "units": {"dividend_yield": "%"}}])
    result = build(panel, calendars, asofs, source_frames={"finmind_per": source})
    row = result["features"].set_index("session_date").loc[next_day]
    assert pd.isna(row.valuation_per) and pd.isna(row.valuation_pbr)
    assert row.valuation_dividend_yield == 0
    context = next(item for item in result["analysis_context"]["2330"]["sources"]
                   if item["route_id"] == "finmind_per")
    assert context["records"][0]["per"] == 0
    assert context["records"][0]["pbr"] == -1
    assert "本期無可用正估值倍數" in context["notes_zh"][0]


def test_invalid_valuation_revision_clears_previously_known_positive_multiple():
    panel, calendars, asofs = fixture_panel()
    day = panel.iloc[10].session_date
    next_day = panel.iloc[11].session_date
    source = pd.DataFrame([
        {"stock_id": "2330", "date": day, "per": 10, "pbr": 2,
         "available_time": day + "T10:00:00+08:00"},
        {"stock_id": "2330", "date": day, "per": 0, "pbr": 0,
         "available_time": day + "T16:00:00+08:00"},
    ])
    rows = build(panel, calendars, asofs, source_frames={"finmind_per": source})["features"].set_index("session_date")
    assert rows.loc[day, "valuation_per"] == 10
    assert pd.isna(rows.loc[next_day, "valuation_per"])


def test_unknown_financial_time_remains_context_and_is_not_historical_feature():
    panel, calendars, asofs = fixture_panel()
    source = pd.DataFrame([
        {"stock_id": "2330", "date": panel.iloc[10].session_date, "type": "Revenue", "value": 100},
        {"stock_id": "2330", "date": panel.iloc[10].session_date, "type": "OperatingIncome", "value": 20},
    ])
    result = build(panel, calendars, asofs, source_frames={"finmind_financial_statements": source})
    assert result["features"]["financial_operating_margin"].isna().all()
    contexts = result["analysis_context"]["2330"]["sources"]
    assert next(item for item in contexts if item["route_id"] == "finmind_financial_statements")["records"]


def test_latest_financial_context_preserves_more_than_six_line_items():
    panel, calendars, asofs = fixture_panel()
    latest = panel.iloc[50].session_date
    source = pd.DataFrame([{"stock_id": "2330", "date": latest, "type": "科目" + str(i), "value": i}
                           for i in range(10)] + [
                               {"stock_id": "2330", "date": panel.iloc[10].session_date,
                                "type": "Revenue", "value": 5}])
    result = build(panel, calendars, asofs, source_frames={"finmind_financial_statements": source})
    context = next(item for item in result["analysis_context"]["2330"]["sources"]
                   if item["route_id"] == "finmind_financial_statements")
    assert len(context["records"]) == 10
    assert context["latest_data_date"] == latest
    assert {row["date"] for row in context["records"]} == {latest}


def test_announced_financial_ratio_becomes_available_only_after_announcement():
    panel, calendars, asofs = fixture_panel()
    published = panel.iloc[30].session_date
    period = panel.iloc[10].session_date
    source = pd.DataFrame([dict(stock_id="2330", date=period, type=kind, value=value,
                               available_time=published + "T16:00:00+08:00", units={"value": "TWD"})
                           for kind, value in [("Revenue", 100), ("OperatingIncome", 20)]])
    result = build(panel, calendars, asofs, source_frames={"finmind_financial_statements": source})
    rows = result["features"].set_index("session_date")
    assert pd.isna(rows.loc[published, "financial_operating_margin"])
    assert rows.loc[panel.iloc[31].session_date, "financial_operating_margin"] == pytest.approx(0.2)


def test_overseas_date_only_never_uses_same_day_or_arbitrary_lag():
    panel, calendars, asofs = fixture_panel()
    source = pd.DataFrame([{"date": panel.iloc[i].session_date, "symbol": "^GSPC", "close": 100 + i}
                           for i in range(10, 15)])
    result = build(panel, calendars, asofs, source_frames={"global_market_history": source})
    assert result["features"]["global_sp500_return_1"].isna().all()
    source["source_close_at"] = source["date"] + "T21:00:00Z"
    later = build(panel, calendars, asofs, source_frames={"global_market_history": source})
    assert later["features"]["global_sp500_return_1"].notna().any()


def test_macro_instrument_selection_does_not_mix_currencies_or_guess_tenors():
    panel, calendars, asofs = fixture_panel()
    day1, day2, day3 = panel.iloc[10:13].session_date
    fx = pd.DataFrame([
        {"date": day1, "currency": "USD", "spot_buy": 30, "available_time": day1 + "T10:00:00+08:00"},
        {"date": day2, "currency": "USD", "spot_buy": 33, "available_time": day2 + "T10:00:00+08:00"},
        {"date": day3, "currency": "JPY", "spot_buy": 0.2, "available_time": day3 + "T10:00:00+08:00"},
    ])
    bonds = pd.DataFrame([{"date": day1, "name": "未核對期限", "value": 3,
                           "available_time": day1 + "T10:00:00+08:00"}])
    result = build(panel, calendars, asofs, source_frames={"finmind_exchange_rate": fx,
                                                        "finmind_government_bonds_yield": bonds})
    rows = result["features"].set_index("session_date")
    assert rows.loc[day2, "exchange_rate_change_1"] == pytest.approx(0.1)
    assert rows.loc[day3, "exchange_rate_change_1"] == pytest.approx(0.1)
    assert rows["bond_yield_change_1"].isna().all()
    assert any(item["code"] == "instrument_selection_required" for item in result["metadata"]["limitations"])


def test_overseas_normalization_cannot_use_a_previous_value_not_yet_available():
    panel, calendars, asofs = fixture_panel()
    earlier, later = panel.iloc[10].session_date, panel.iloc[20].session_date
    source = pd.DataFrame([
        {"date": earlier, "symbol": "^GSPC", "close": 100,
         "source_close_at": later + "T21:00:00Z"},
        {"date": panel.iloc[11].session_date, "symbol": "^GSPC", "close": 200,
         "source_close_at": panel.iloc[11].session_date + "T21:00:00Z"},
    ])
    result = build(panel, calendars, asofs, source_frames={"global_market_history": source})
    assert result["features"]["global_sp500_return_1"].isna().all()


def test_raw_ohlc_is_not_divided_by_adjusted_close():
    panel, calendars, asofs = fixture_panel()
    panel["adjusted_close"] *= 0.5
    panel["high"], panel["low"] = 52.5, 47.5
    result = build(panel, calendars, asofs)
    assert result["features"].iloc[-1]["intraday_range_ratio"] == pytest.approx(105 / 95 - 1)
    assert result["features"].iloc[-1]["raw_close"] == 101


def test_unknown_high_low_cannot_impersonate_official_raw_prices():
    panel, calendars, asofs = fixture_panel()
    panel = panel.drop(columns=["raw_high", "raw_low"])
    result = build(panel, calendars, asofs)
    assert result["features"]["intraday_range_ratio"].isna().all()
    assert any(item["code"] == "official_raw_price_missing" for item in result["metadata"]["limitations"])


def test_financial_rows_without_a_security_are_never_broadcast_to_all_stocks():
    panel, calendars, asofs = fixture_panel()
    day = panel.iloc[10].session_date
    source = pd.DataFrame([{"date": day, "type": kind, "value": value,
                            "available_time": day + "T16:00:00+08:00", "units": {"value": "TWD"}}
                           for kind, value in [("Revenue", 100), ("OperatingIncome", 20)]])
    result = build(panel, calendars, asofs, source_frames={"finmind_financial_statements": source})
    assert result["features"]["financial_operating_margin"].isna().all()
    assert any(item["code"] == "security_market_unmatched" for item in result["metadata"]["limitations"])


def test_not_selected_and_partial_are_preserved_and_empty_requires_proof():
    result = build(source_manifest=[
        {"route_id": "finmind_trading_daily_report", "status": "PARTIAL", "reason": "kept"},
        {"route_id": "finmind_margin", "availability": "legitimate_empty"},
    ])
    manifest = {item["route_id"]: item for item in result["metadata"]["source_manifest"]}
    assert manifest["finmind_trading_daily_report"]["usage"] == "not-selected"
    assert manifest["finmind_trading_daily_report"]["status"] == "PARTIAL"
    assert manifest["finmind_trading_daily_report"]["availability"] != "missing"
    assert manifest["finmind_margin"]["availability"] != "legitimate_empty"
    proof = {"route_id": "finmind_margin", "legitimate_empty": True,
             "no_data_evidence": {"source_scope_complete": True, "sha256": "a" * 64}}
    proven = build(source_manifest=[proof])
    assert next(item for item in proven["metadata"]["source_manifest"]
                if item["route_id"] == "finmind_margin")["availability"] == "legitimate_empty"


def test_news_is_context_with_title_and_time_not_a_sentiment_feature():
    panel, calendars, asofs = fixture_panel()
    source = pd.DataFrame([{"stock_id": "2330", "date": panel.iloc[-1].session_date,
                           "title": "公司重大訊息", "available_time": asofs["TWSE"] + "T10:00:00+08:00"}])
    result = build(panel, calendars, asofs, source_frames={"news_and_hot_topics_raw": source})
    assert not any("sentiment" in item for item in result["metadata"]["feature_order"])
    news = next(item for item in result["analysis_context"]["2330"]["sources"]
                if item["route_id"] == "news_and_hot_topics_raw")
    assert news["records"][0]["title"] == "公司重大訊息"
    assert news["records"][0]["available_time"]
    json.dumps(result["analysis_context"], ensure_ascii=False, allow_nan=False)
    json.dumps(result["metadata"], ensure_ascii=False, allow_nan=False)


def test_feature_order_is_independent_of_missing_source_payload():
    first = build()
    panel, calendars, asofs = fixture_panel()
    source = pd.DataFrame([{"stock_id": "2330", "date": panel.iloc[10].session_date, "per": 10}])
    second = build(panel, calendars, asofs, source_frames={"finmind_per": source})
    assert first["metadata"]["feature_order"] == second["metadata"]["feature_order"]
    assert first["metadata"]["feature_order_by_horizon"] == second["metadata"]["feature_order_by_horizon"]
    assert all(item["label_zh"] and item["unit"] and item["category"] and item["route"]
               for item in first["metadata"]["feature_metadata"].values())


def test_schema_and_per_horizon_order_ignore_union_source_manifest():
    first = build(horizons=[1])
    second = build(horizons=[1, 63], source_manifest=[
        {"route_id": "finmind_financial_statements", "usage": "used"},
        {"route_id": "finmind_per", "usage": "not-selected"},
    ])
    assert first["metadata"]["feature_order"] == second["metadata"]["feature_order"]
    assert first["metadata"]["feature_order_by_horizon"]["1"] == second["metadata"]["feature_order_by_horizon"]["1"]
    assert first["metadata"]["feature_metadata"] == second["metadata"]["feature_metadata"]
    assert "financial_operating_margin" not in second["metadata"]["feature_order_by_horizon"]["1"]
    assert "financial_operating_margin" in second["metadata"]["feature_order_by_horizon"]["63"]
    manifests = {item["route_id"]: item for item in second["metadata"]["source_manifest"]}
    assert not manifests["finmind_financial_statements"]["required_for_analysis"]
    assert manifests["finmind_stock_info"]["required_for_analysis"]
    assert all(item["source_route"] == item["route"] for item in second["metadata"]["feature_metadata"].values())
