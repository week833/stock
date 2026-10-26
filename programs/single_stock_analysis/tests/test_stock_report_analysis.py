"""Focused regression cases; fixtures and results stay in memory."""

import copy
import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from stock_report_analysis import build_report_context, build_report_insights, normalize_news_title


STOCK = {"stock_id": "2376", "market": "TWSE", "stock_name": "技嘉", "industry": "電腦及週邊設備業"}
ASOF = "20261022"


def manifest(route, **extra):
    return {"route_id": route, "name_zh": "測試來源", "usage": "context-only", **extra}


def context(rows, route="finmind_price_adj", **metadata):
    return build_report_context({route: pd.DataFrame(rows)}, [manifest(route, **metadata)], stock=STOCK, asof=ASOF)


def metric(identity, value, **extra):
    return {"id": identity, "current_value": value, "資料期間": "20261022", **extra}


def payload(metrics=(), **extra):
    return {"stock": dict(STOCK), "asof": ASOF, "report_comparison": {"metrics": list(metrics)}, **extra}


class ReportContextTests(unittest.TestCase):
    def test_native_json_and_exact_identity_with_compact_history(self):
        rows = [{"stock_id": "2376", "date": f"202610{day:02d}", "close": day, "unit": "元"} for day in range(1, 23)]
        result = context(rows, status="PARTIAL")
        self.assertEqual(result["schema"], "single-stock-report-context-v1")
        self.assertEqual(result["stock_id"], "2376")
        self.assertEqual(result["asof"], ASOF)
        self.assertEqual(result["sources"][0]["availability"], "available")
        series = result["sources"][0]["series"][0]
        self.assertEqual(series["observation_count"], 22)
        self.assertEqual(series["latest"]["value"], 22)
        self.assertEqual(series["previous"]["value"], 21)
        self.assertEqual(len(result["sources"][0]["records"]), 2)
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)
        iso = build_report_context({}, [], stock=STOCK, asof="2026-10-22")
        self.assertEqual(iso["asof"], "2026-10-22")

    def test_filter_before_display_limit_including_seventh_row(self):
        rows = [{"stock_id": "9999", "date": "20261022", "close": n} for n in range(7)]
        rows.append({"stock_id": "2376", "date": "20261022", "close": 349})
        result = context(rows)["sources"][0]
        self.assertEqual(result["observation_count"], 1)
        self.assertEqual(result["series"][0]["latest"]["value"], 349)
        self.assertEqual(result["excluded_counts"]["irrelevant"], 7)

    def test_native_revenue_month_survives_publication_date_projection(self):
        rows = [{"stock_id": "2376", "date": "20261001", "revenue": 63099342000,
                 "month_revenue_year": "2026", "month_revenue_month": "09",
                 "origin_name": "月營收", "unit": "元"}]
        source = context(rows, "finmind_month_revenue")["sources"][0]
        record = source["records"][0]
        self.assertEqual(record["date"], "2026-10-01")
        self.assertEqual(record["revenue_year"], 2026)
        self.assertEqual(record["revenue_month"], 9)
        self.assertEqual(record["month_revenue_year"], 2026)
        self.assertEqual(record["month_revenue_month"], 9)
        self.assertEqual(record["label_zh"], "月營收")
        self.assertEqual(record["unit"], "元")
        self.assertEqual(record["value"], 63099342000)
        self.assertEqual(rows[0]["month_revenue_month"], "09")
        absent = context([{"stock_id": "2376", "date": "20261001", "revenue": 100}], "finmind_month_revenue")
        self.assertNotIn("revenue_month", absent["sources"][0]["records"][0])

    def test_numeric_identifiers_and_metadata_are_not_measurements(self):
        row = {"stock_id": "2376", "date": ASOF, "entity_id": "2376", "data_id": "2376",
               "symbol": "2376", "indicator": "1", "type": "2", "field": "3", "name": "4",
               "source_id": "5", "record_key": "6", "schema_version": "7", "source_size": 1000,
               "published_at": "20261022", "revenue_year": 2026, "revenue_month": 9,
               "revenue": 100, "volume": 500, "origin_name": "可核對觀測", "unit": "元"}
        source = context([row], "finmind_month_revenue")["sources"][0]
        self.assertEqual({item["identity"]["field"] for item in source["series"]}, {"revenue", "volume"})
        self.assertEqual({item["latest"]["value"] for item in source["series"]}, {100, 500})
        self.assertTrue(all(item["identity"]["symbol"] == "2376" for item in source["series"]))
        self.assertTrue(all(item["latest"]["label_zh"] == "可核對觀測" for item in source["series"]))

    def test_every_symbol_and_indicator_gets_own_previous(self):
        rows = [{"symbol": symbol, "indicator": indicator, "date": day, "close": value, "unit": "點"}
                for symbol in ("^GSPC", "^SOX") for indicator in ("price", "total_return")
                for day, value in (("20261021", 100), ("20261022", 105))]
        result = context(rows, "global_market_history")["sources"][0]
        self.assertEqual(len(result["series"]), 4)
        self.assertTrue(all(item["comparison"]["delta"] == 5 for item in result["series"]))

    def test_financial_basis_and_ytd_span_are_not_mixed(self):
        rows = [{"stock_id": "2376", "date": day, "type": "Income", "value": value,
                 "period_basis": basis, "period_start": start, "period_end": day, "unit": "TWD"}
                for day, start, basis, value in (("20250630", "20250101", "year_to_date", 10),
                    ("20260630", "20260101", "year_to_date", 20),
                    ("20260331", "20260101", "year_to_date", 8),
                    ("20260630", "20260401", "quarter", 12))]
        result = context(rows, "finmind_financial_statements")["sources"][0]
        self.assertEqual(len(result["series"]), 3)
        comparable = [item for item in result["series"] if item["comparison"]["comparable"]]
        self.assertEqual(len(comparable), 1)
        self.assertEqual(comparable[0]["comparison"]["delta"], 10)

    def test_unknown_financial_basis_does_not_claim_comparison(self):
        rows = [{"stock_id": "2376", "date": day, "type": "EPS", "value": value}
                for day, value in (("20250630", 3), ("20260630", 9))]
        series = context(rows, "finmind_financial_statements")["sources"][0]["series"][0]
        self.assertFalse(series["comparison"]["comparable"])
        self.assertNotIn("delta", series["comparison"])
        self.assertIn("unknown_unit", series["statuses"])

    def test_future_observation_and_future_publication_excluded(self):
        rows = [{"stock_id": "2376", "date": "20261023", "close": 999},
                {"stock_id": "2376", "date": "20261022", "close": 999, "available_at": "2026-10-23T00:01:00+08:00"},
                {"stock_id": "2376", "date": "20261021", "close": 349, "available_at": "2026-10-22T15:00:00+08:00"}]
        result = context(rows)["sources"][0]
        self.assertEqual(result["excluded_counts"]["future"], 2)
        self.assertEqual(result["series"][0]["latest"]["value"], 349)

    def test_missing_null_and_numeric_zero_are_different(self):
        result = build_report_context({"values": pd.DataFrame([
            {"stock_id": "2376", "date": "20261021", "value": None},
            {"stock_id": "2376", "date": "20261022", "value": 0}])},
            [manifest("values"), manifest("absent")], stock=STOCK, asof=ASOF)
        sources = {item["route_id"]: item for item in result["sources"]}
        self.assertEqual(sources["absent"]["availability"], "missing_source")
        self.assertEqual(sources["values"]["availability"], "partial_observations")
        self.assertEqual(sources["values"]["series"][0]["latest"]["value"], 0)
        self.assertNotIn("confirmed_no_event", sources["values"]["statuses"])

    def test_verified_calendar_and_identity_are_metadata_not_missing_frames(self):
        metadata = [manifest("official_calendar", reader="verified_context", evidence={
            "calendar_verified_by_market": {"TWSE": True}, "historical_axis_verified": True,
            "future_targets_verified": False, "verified_asof": "2026-10-22"}),
            manifest("finmind_stock_info", current_identity_verified=True,
                     evidence={"identity": {"sha256": "existing-identity-binding"}})]
        result = build_report_context({}, metadata, stock=STOCK, asof=ASOF)
        self.assertTrue(all(source["availability"] == "available" for source in result["sources"]))
        self.assertTrue(all(source["metadata_only"] for source in result["sources"]))
        self.assertFalse(any(item["status"] in {"missing_source", "unknown_publication_time", "unmapped_fields"} for item in result["statuses"]))
        result = build_report_insights(payload(report_context=result, prediction={"target_date_status": "unverified_future_calendar"}))
        self.assertNotIn("目標交易日尚未驗證", "".join(result["data_notes"]))

    def test_verified_identity_without_quotation_date_does_not_gain_time_warning(self):
        result = build_report_context({"finmind_stock_info": pd.DataFrame([{"stock_id": "2376", "stock_name": "技嘉"}])},
            [manifest("finmind_stock_info", reader="verified_context", current_identity_verified=True,
                evidence={"verification": "pinned_head_and_identity_raw_receipt_bytes_verified"})], stock=STOCK, asof=ASOF)
        self.assertEqual(result["sources"][0]["availability"], "available")
        self.assertEqual(result["sources"][0]["statuses"], ["available"])
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)

    def test_reporting_deadlines_are_distinct_period_metadata_not_numeric_financial_data(self):
        rows = [
            {"stock_id": "2376", "market": "TWSE", "date": "20260930", "period_id": "2026-Q3", "period_kind": "Q3",
             "period_start": "20260701", "period_end": "20260930", "base_deadline": "20261114",
             "derived_deadline": "20261116", "holiday_adjusted_deadline": "20261116", "status": "NOT_DUE",
             "classification_status": "CONFIRMED", "observed_filed_at": None, "observed_source_status": "UNAVAILABLE"},
            {"stock_id": "2376", "market": "TWSE", "date": "20260930", "period_id": "2026-09", "period_kind": "MONTHLY_REVENUE",
             "period_start": "20260901", "period_end": "20260930", "base_deadline": "20261010",
             "derived_deadline": "20261012", "holiday_adjusted_deadline": "20261012", "status": "DUE",
             "classification_status": "CONFIRMED", "observed_filed_at": None, "observed_source_status": "UNAVAILABLE"},
        ]
        frame = pd.DataFrame(rows)
        before = frame.copy(deep=True)
        result = build_report_context({"official_finance_reporting_deadline": frame,
            "finmind_month_revenue": pd.DataFrame([{"stock_id": "2376", "date": "20261001", "revenue": 100,
                "revenue_year": 2026, "revenue_month": 9}]),
            "finmind_financial_statements": pd.DataFrame([{"stock_id": "2376", "date": "20260630", "type": "Income", "value": 10, "unit": "TWD"}])},
            [manifest("official_finance_reporting_deadline", status="PARTIAL"), manifest("finmind_month_revenue"),
             manifest("finmind_financial_statements")], stock=STOCK, asof=ASOF)
        sources = {source["route_id"]: source for source in result["sources"]}
        schedule = sources["official_finance_reporting_deadline"]
        self.assertEqual(schedule["availability"], "available")
        self.assertTrue(schedule["metadata_only"])
        self.assertEqual(schedule["series"], [])
        self.assertEqual(len(schedule["records"]), 2)
        self.assertFalse({"unknown_unit", "partial_observations", "insufficient_comparable_history"} & set(schedule["statuses"]))
        self.assertIn("unknown_publication_time", schedule["statuses"])
        periods = {record["period_id"]: record for record in schedule["records"]}
        self.assertEqual(periods["2026-Q3"]["derived_deadline"], "20261116")
        self.assertEqual(periods["2026-Q3"]["status"], "NOT_DUE")
        self.assertEqual(periods["2026-09"]["status"], "DUE")
        self.assertIsNone(periods["2026-Q3"]["observed_filed_at"])
        self.assertEqual(sources["finmind_month_revenue"]["availability"], "available")
        self.assertEqual(sources["finmind_financial_statements"]["series"][0]["latest"]["value"], 10)
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)
        pd.testing.assert_frame_equal(frame, before)

    def test_reporting_schedule_keeps_all_periods_beyond_six_without_false_conflicts(self):
        rows = [{"stock_id": "2376", "market": "TWSE", "date": "20260930", "period_id": f"period-{index}",
            "period_kind": "MONTHLY_REVENUE", "derived_deadline": 20261116,
            "status": "NOT_DUE", "classification_status": "CONFIRMED", "observed_filed_at": None,
            "observed_source_status": "UNAVAILABLE"} for index in range(8)]
        schedule = context(rows, "official_finance_reporting_deadline")["sources"][0]
        self.assertEqual(len(schedule["records"]), 8)
        self.assertEqual(schedule["availability"], "available")
        self.assertEqual(schedule["series"], [])

    def test_reporting_schedule_keeps_explicit_stock_scoped_coverage_gaps(self):
        rows = [{"stock_id": "2376", "market": "TWSE", "date": "20260930", "period_id": "2026-Q3",
            "derived_deadline": "20261116", "status": "NOT_DUE", "observed_filed_at": None,
            "observed_source_status": "UNAVAILABLE"}]
        for key, value in (("missing_dates", ["20260630"]), ("missing_periods", ["2026-Q2"]), ("missing_observations", 1)):
            result = context(rows, "official_finance_reporting_deadline", status="PARTIAL",
                coverage={"by_stock": {"2376": {key: value}}})
            source = result["sources"][0]
            self.assertEqual(source["availability"], "partial_observations")
            self.assertEqual(source["series"], [])
            self.assertNotIn("unknown_unit", source["statuses"])
            self.assertNotIn("insufficient_comparable_history", source["statuses"])
            self.assertIn("時程觀測不足", next(item["reason_zh"] for item in result["statuses"] if item["status"] == "partial_observations"))
            self.assertIn("不代表財報數值缺失", "".join(build_report_insights(payload(report_context=result))["data_notes"]))
        clean = context(rows, "official_finance_reporting_deadline", status="PARTIAL",
            coverage={"by_stock": {"2330": {"missing_periods": ["2026-Q2"]}}})
        self.assertEqual(clean["sources"][0]["availability"], "available")

    def test_zero_requires_positive_verified_scoped_evidence(self):
        proof = {"verified": True, "stock_id": "2376", "route_id": "events", "date": ASOF,
                 "outcome": "no_event", "row_count": 0}
        self.assertEqual(context([], "events", zero_evidence=proof)["sources"][0]["availability"], "confirmed_no_event")
        for change in ({"verified": False}, {"stock_id": "2330"}, {"date": "20261021"}, {"pending_release": True}, {"outcome": "empty"}):
            result = context([], "events", zero_evidence={**proof, **change})
            self.assertEqual(result["sources"][0]["availability"], "no_matching_observations_unconfirmed")
        self.assertEqual(context([], "events", availability="legitimate_empty")["sources"][0]["availability"], "no_matching_observations_unconfirmed")

    def test_not_applicable_requires_proof_not_just_label(self):
        proof = {"verified": True, "stock_id": "2376", "route_id": "events", "date": ASOF,
                 "applicable": False, "reason_zh": "此類事件依本股已驗證身分不適用"}
        self.assertEqual(context([], "events", applicability_evidence=proof)["sources"][0]["availability"], "not_applicable")
        self.assertEqual(context([], "events", applicability="not_applicable")["sources"][0]["availability"], "no_matching_observations_unconfirmed")
        self.assertEqual(context([], "events", usage="not-selected")["sources"][0]["availability"], "not_selected")

    def test_industry_and_news_relevance_after_unrelated_rows(self):
        rows = [{"date": "20261022", "title": "無關公司的市場新聞"} for _ in range(7)]
        rows.extend([{"date": "20261022", "title": "技嘉公告新產品"}, {"date": "20261023", "title": "技嘉未來新聞"}])
        source = context(rows, "news_and_hot_topics_raw")["sources"][0]
        self.assertEqual(source["observation_count"], 1)
        self.assertEqual(source["records"][0]["title"], "技嘉公告新產品")
        rows = [{"date": "20261022", "sector": "營建業", "value": 9},
                {"stock_id": "2376", "date": "20261022", "sector": STOCK["industry"], "value": 2}]
        self.assertEqual(context(rows, "official_sector_observations")["sources"][0]["observation_count"], 1)

    def test_verified_profile_fills_missing_industry_before_other_routes_once(self):
        class OnceFrame:
            def __init__(self, rows):
                self.frame = pd.DataFrame(rows)
                self.columns = self.frame.columns
                self.visits = 0

            def itertuples(self, **kwargs):
                self.visits += 1
                if self.visits != 1:
                    raise AssertionError("route visited more than once")
                return self.frame.itertuples(**kwargs)

        stock = {**STOCK, "industry": None}
        before = copy.deepcopy(stock)
        frames = {
            "official_sector_observations": OnceFrame([{"date": ASOF, "sector_key": "computer_peripherals",
                "sector_label": STOCK["industry"], "value": 2, "unit": "%"}]),
            "finmind_industry_chain_flow": OnceFrame([{"date": ASOF, "industry": STOCK["industry"], "value": 3, "unit": "元"}]),
            "official_sector_features": OnceFrame([{"stock_id": "2376", "market": "TWSE",
                "sector": STOCK["industry"], "industry_category": STOCK["industry"], "source_industry": STOCK["industry"],
                "classification_verified": True, "current_ordinary_classification_verified": True,
                "classification_observed_date": "2026-10-22"}]),
        }
        result = build_report_context(frames, [manifest(route) for route in frames], stock=stock, asof=ASOF)
        self.assertEqual(result["stock_profile"], {"stock_id": "2376", "market": "TWSE", "stock_name": "技嘉",
            "industry": STOCK["industry"], "classification_verified": True,
            "classification_observed_date": "2026-10-22", "source_route": "official_sector_features"})
        sources = {item["route_id"]: item for item in result["sources"]}
        self.assertEqual(sources["official_sector_observations"]["availability"], "available")
        self.assertEqual(sources["official_sector_observations"]["observation_count"], 1)
        self.assertNotIn("unmapped_fields", sources["official_sector_observations"]["statuses"])
        self.assertEqual(sources["finmind_industry_chain_flow"]["observation_count"], 1)
        self.assertTrue(all(frame.visits == 1 for frame in frames.values()))
        self.assertEqual([item["route_id"] for item in result["sources"]], sorted(frames))
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)
        self.assertEqual(stock, before)

    def test_unrelated_sector_taxonomy_is_unmapped_not_missing_and_direct_stock_is_usable(self):
        rows = [{"date": ASOF, "sector_key": "construction_property", "sector_label": "營建／房地產", "value": 2}]
        result = context(rows, "official_sector_observations")
        source = result["sources"][0]
        self.assertEqual(source["availability"], "unmapped_fields")
        self.assertEqual(source["unmapped_observation_count"], 1)
        self.assertNotIn("missing_source", source["statuses"])
        self.assertIn("權威對照未建立", result["statuses"][0]["reason_zh"])
        self.assertIn("權威對照未建立", "".join(build_report_insights(payload(report_context=result))["data_notes"]))
        direct = context([{**rows[0], "stock_id": "2376"}], "official_sector_observations")
        self.assertEqual(direct["sources"][0]["availability"], "available")
        self.assertEqual(direct["sources"][0]["observation_count"], 1)

    def test_unverified_future_wrong_market_or_conflicting_classification_is_not_profile(self):
        base = {"stock_id": "2376", "market": "TWSE", "sector": STOCK["industry"],
                "industry_category": STOCK["industry"], "source_industry": STOCK["industry"],
                "classification_verified": True, "current_ordinary_classification_verified": True,
                "classification_observed_date": "2026-10-22"}
        stock = {**STOCK, "industry": None}
        variants = [[{**base, "classification_verified": False}],
                    [{**base, "current_ordinary_classification_verified": False}],
                    [{**base, "classification_observed_date": "2026-10-23"}],
                    [{**base, "market": "TPEx"}],
                    [base, {**base, "sector": "其他分類", "industry_category": "其他分類", "source_industry": "其他分類"}],
                    [{**base, "source_industry": "衝突分類"}]]
        for rows in variants:
            result = build_report_context({"official_sector_features": pd.DataFrame(rows)},
                [manifest("official_sector_features")], stock=stock, asof=ASOF)
            self.assertNotIn("stock_profile", result)
        conflict = build_report_context({"official_sector_features": pd.DataFrame(variants[-1])},
            [manifest("official_sector_features")], stock=stock, asof=ASOF)
        self.assertIn("unmapped_fields", conflict["sources"][0]["statuses"])
        self.assertIsNone(stock["industry"])

    def test_unit_changes_and_conflicting_same_date_do_not_merge(self):
        rows = [{"stock_id": "2376", "date": "20261020", "value": 1, "unit": "股"},
                {"stock_id": "2376", "date": "20261021", "value": 2, "unit": "張"},
                {"stock_id": "2376", "date": "20261022", "value": 3, "unit": "股"},
                {"stock_id": "2376", "date": "20261022", "value": 4, "unit": "股"}]
        first = context(rows, "values")
        second = context(list(reversed(rows)), "values")
        self.assertEqual(first, second)
        series = first["sources"][0]["series"]
        self.assertEqual(len(series), 2)
        self.assertTrue(all(not item["comparison"]["comparable"] for item in series))

    def test_local_missing_dates_not_global_partial_decide_partial(self):
        row = {"stock_id": "2376", "date": ASOF, "close": 349}
        result = context([row], status="PARTIAL", coverage={"by_stock": {"2330": {"missing_dates": ["20261021"]}}})
        self.assertEqual(result["sources"][0]["availability"], "available")
        result = context([row], coverage={"by_stock": {"2376": {"missing_dates": ["20261021"]}}})
        self.assertEqual(result["sources"][0]["availability"], "partial_observations")

    def test_no_io_mutation_or_extra_frame_visits(self):
        class OnceFrame:
            def __init__(self, frame):
                self.frame, self.columns, self.visits = frame, frame.columns, 0

            def itertuples(self, **kwargs):
                self.visits += 1
                if self.visits > 1:
                    raise AssertionError("frame revisited")
                return self.frame.itertuples(**kwargs)

        frame = pd.DataFrame([{"stock_id": "2376", "date": ASOF, "value": 0}])
        original = frame.copy(deep=True)
        metadata = [manifest("values", object={"sha256": "already-verified-binding"})]
        original_metadata = copy.deepcopy(metadata)
        wrapped = OnceFrame(frame)
        with patch("builtins.open", side_effect=AssertionError("unexpected file I/O")), patch.object(Path, "open", side_effect=AssertionError("unexpected file I/O")):
            result = build_report_context({"values": wrapped}, metadata, stock=STOCK, asof=ASOF)
            build_report_insights(payload(report_context=result))
        pd.testing.assert_frame_equal(frame, original)
        self.assertEqual(metadata, original_metadata)
        self.assertEqual(wrapped.visits, 1)


class ReportInsightTests(unittest.TestCase):
    def test_news_title_normalizer_only_removes_explicit_suffix_and_whitespace(self):
        title = "  技嘉(2376-TW) EPS  35.54元，目標價500元\t- Yahoo股市  "
        expected = "技嘉(2376-TW) EPS 35.54元，目標價500元"
        self.assertEqual(normalize_news_title(title, "Yahoo股市"), expected)
        self.assertEqual(normalize_news_title(expected, "Yahoo股市"), expected)
        self.assertEqual(normalize_news_title("技嘉 - Yahoo股市 內文", "Yahoo股市"), "技嘉 - Yahoo股市 內文")
        self.assertEqual(normalize_news_title("技嘉 - Yahoo股市", "鉅亨網"), "技嘉 - Yahoo股市")
        self.assertEqual(normalize_news_title("技嘉 - Yahoo股市"), "技嘉 - Yahoo股市")
        self.assertEqual(normalize_news_title(None, "Yahoo股市"), "")
        with patch("builtins.open", side_effect=AssertionError("unexpected I/O")):
            self.assertEqual(normalize_news_title(title, "Yahoo股市"), expected)

    def test_same_day_normalized_news_deduplicates_before_selecting_two(self):
        headline = "華碩、技嘉、微星強攻OCP峰會 機櫃級液冷與代理式AI成核心技術趨勢"
        records = [{"date": "2026-10-18", "title": headline + " - Yahoo股市", "publisher": "Yahoo股市"},
                   {"date": "2026-10-18", "title": headline, "publisher": "鉅亨網"},
                   {"date": "2026-10-18", "title": headline + " - 鉅亨網", "publisher": "鉅亨網"},
                   {"date": "2026-10-16", "title": "技嘉EPS預估35.54元，目標價500元", "publisher": "鉅亨網"}]
        ctx = {"stock_id": "2376", "asof": ASOF, "sources": [{"route_id": "news_and_hot_topics_raw", "records": records}], "statuses": []}
        original = payload(report_context=ctx)
        before = copy.deepcopy(original)
        text = "".join(build_report_insights(original)["context"])
        self.assertEqual(text.count(headline), 1)
        self.assertIn("2026-10-18", text)
        self.assertIn("2026-10-16", text)
        self.assertIn("35.54元，目標價500元", text)
        self.assertNotIn("Yahoo股市", text)
        self.assertEqual(original, before)
        ctx["sources"][0]["records"] = [{"date": day, "title": headline, "publisher": "Yahoo股市"}
                                        for day in ("2026-10-18", "2026-10-17")]
        self.assertEqual("".join(build_report_insights(payload(report_context=ctx))["context"]).count(headline), 2)

    def test_macro_selection_uses_known_chinese_targets_fixed_priority_and_spot_rates(self):
        def series(symbol="", indicator="", field="close", value=1, day="2026-10-22", unit="unknown", kind="", label="close", currency=""):
            return {"identity": {"symbol": symbol, "indicator": indicator, "type": kind, "field": field, "unit": unit, "currency": currency},
                    "latest": {"date": day, "value": value, "label_zh": label}, "comparison": {"change_ratio": .0017}}

        globals_ = [series(value=999123, label="無名標的"), series("1306.T", "jp_topix", value=420.6),
                    series(indicator="usd_jpy", value=155.66, day="2026-09-04"),
                    series("JPY=X", "usd_jpy", value=158.13),
                    series("^DJI", value=40000, unit="點"), series("^SOX", value=4000, unit="點"),
                    series("^IXIC", value=16000, unit="點"), series("^GSPC", value=5000, unit="點"),
                    series("^TNX", value=5.146, day="2026-10-21")]
        exchange = [series(field=field, value=value, currency="USD") for field, value in (
            ("cash_buy", 32.14), ("cash_sell", 32.81), ("spot_buy", 32.49), ("spot_sell", 32.59))]
        sources = [{"route_id": "global_market_history", "series": globals_},
                   {"route_id": "finmind_exchange_rate", "series": exchange},
                   {"route_id": "finmind_government_bonds_yield", "series": [series(field="value", value=5.09,
                       day="2026-10-16", kind="United States 10-Year", label="value")]}]
        original = payload(report_context={"stock_id": "2376", "asof": ASOF, "sources": sources, "statuses": []})
        before = copy.deepcopy(original)
        result = build_report_insights(original)
        text = "".join(result["context"])
        labels = ("美國標普500指數", "美國那斯達克指數", "美國費城半導體指數", "美國道瓊工業指數")
        self.assertEqual(sorted(labels, key=text.index), list(labels))
        self.assertIn("5,000點", text)
        self.assertIn("美元即期買入於2026-10-22為32.49", text)
        self.assertIn("美元即期賣出於2026-10-22為32.59", text)
        self.assertIn("美國10年期公債殖利率於2026-10-16為5.09", text)
        for unwanted in ("（收盤）", "United States", "USD", "1306.T", "999,123", "2026-09-04", "現金買入", "現金賣出", "32.14", "32.81", "5.09%"):
            self.assertNotIn(unwanted, text)
        self.assertEqual(sum("部分數值單位未核實" in note for note in result["data_notes"]), 1)
        self.assertEqual(original, before)

    def test_macro_known_alias_and_label_deduplicate_to_latest_without_inventing_units(self):
        source = {"route_id": "global_market_history", "series": [
            {"identity": {"indicator": "us_sp500", "field": "close", "unit": "unknown"},
             "latest": {"date": "2026-10-21", "value": 5000, "label_zh": "close"}},
            {"identity": {"symbol": "^GSPC", "field": "close", "unit": "unknown"},
             "latest": {"date": "2026-10-22", "value": 5010, "label_zh": "美國標普500指數"}}]}
        result = build_report_insights(payload(report_context={"stock_id": "2376", "asof": ASOF, "sources": [source], "statuses": []}))
        text = "".join(result["context"])
        self.assertEqual(text.count("美國標普500指數"), 1)
        self.assertIn("2026-10-22為5,010", text)
        self.assertNotIn("點", text)

    def test_unknown_flow_units_are_one_note_but_known_units_stay_in_facts(self):
        items = [metric("institutional_foreign_5", 20, observed_days=5, source_route="flow", unit="單位未核實"),
                 metric("institutional_foreign_20", -10, observed_days=20, source_route="flow", unit="單位未核實"),
                 metric("margin_purchase_balance", 6572, delta=35, unit="單位未核實"),
                 metric("short_sale_balance", 2355, delta=-299, unit="張")]
        result = build_report_insights(payload(items))
        body = "".join(result["flows"])
        self.assertIn("6,572", body)
        self.assertIn("2,355", body)
        self.assertIn("299（張）", body)
        self.assertNotIn("單位未核實", body)
        self.assertEqual(sum("部分數值單位未核實" in text for text in result["data_notes"]), 1)

    def test_notes_merge_all_material_gaps_into_three_topics_without_losing_statuses(self):
        entries = [("units", "單位來源", "unknown_unit"), ("history", "財務比較", "insufficient_comparable_history"),
                   ("official_finance_reporting_deadline", "財報申報期限", "partial_observations"),
                   ("official_trading_restrictions", "交易限制與處置", "no_matching_observations_unconfirmed"),
                   ("official_sector_observations", "產業對照", "unmapped_fields"),
                   ("missing", "未提供來源", "missing_source"), ("timing", "時間來源", "unknown_publication_time"),
                   ("unselected", "未選來源", "not_selected"), ("na", "不適用來源", "not_applicable"),
                   ("empty", "零事件來源", "confirmed_no_event")]
        ctx = {"stock_id": "2376", "asof": ASOF,
               "sources": [{"route_id": route, "name_zh": name} for route, name, status in entries],
               "statuses": [{"route_id": route, "status": status,
                   "reason_zh": "產業分類的權威對照未建立。" if status == "unmapped_fields" else "既有狀態"} for route, name, status in entries]}
        before = copy.deepcopy(ctx)
        result = build_report_insights(payload(report_context=ctx, prediction={"calibration_status": "uncalibrated", "target_date_status": "unverified_future_calendar"}))
        self.assertEqual(len(result["data_notes"]), 3)
        combined = "".join(result["data_notes"])
        for important in ("單位與期間可比性", "觀測與分類缺口", "歷史可用時點與回測限制",
                          "時程觀測不足", "不代表財報數值缺失", "交易限制與處置", "無適格觀測", "權威對照未建立", "未提供來源", "時點偏誤"):
            self.assertIn(important, combined)
        for excluded in ("未選來源", "不適用來源", "零事件來源", "尚未校準", "目標交易日"):
            self.assertNotIn(excluded, combined)
        self.assertEqual(ctx, before)

    def test_revenue_momentum_uses_existing_complete_aligned_metrics(self):
        items = [metric("revenue_monthly", 100, yoy=1.2545, mom=0.3312, 資料期間="202609"),
                 metric("revenue_3m", 200, yoy=0.9444, complete=True, 資料期間="202607～202609"),
                 metric("revenue_ytd", 400, yoy=0.6317, complete=True, 資料期間="202601～202609"),
                 metric("revenue_12m", 500, yoy=0.5752, complete=True, 資料期間="202510～202609")]
        text = "".join(build_report_insights(payload(items))["fundamentals"])
        self.assertIn("+125.45%", text)
        self.assertIn("近期營收同比動能強於較長窗口", text)
        items[-1]["資料期間"] = "202509～202608"
        self.assertNotIn("近期營收同比動能強於較長窗口", "".join(build_report_insights(payload(items))["fundamentals"]))

    def test_foreign_reversal_and_observed_not_assumed_trading_days(self):
        items = [metric("institutional_foreign_5", 2319401, observed_days=5, unit="單位未核實", source_route="flow", 資料期間="20261016～20261022"),
                 metric("institutional_foreign_20", -3261545, observed_days=20, unit="單位未核實", source_route="flow", 資料期間="20260922～20261022")]
        text = "".join(build_report_insights(payload(items))["flows"])
        self.assertIn("短期淨買，中期累計仍淨賣", text)
        self.assertNotIn("回補", text)
        self.assertIn("有資料日", text)
        self.assertNotIn("交易日", text)
        items[0]["observed_days"] = 3
        self.assertNotIn("短期淨買，中期累計仍淨賣", "".join(build_report_insights(payload(items))["flows"]))
        items[0]["unit"] = "張"
        self.assertIn("單位不一致", "".join(build_report_insights(payload(items))["data_notes"]))

    def test_valuation_percentiles_are_self_history_not_peer_valuation(self):
        items = [metric("valuation_per", 13.06, percentile=0.346, historical_count=1227, unit="倍"),
                 metric("valuation_pbr", 3.51, percentile=0.58, historical_count=1227, unit="倍")]
        text = "".join(build_report_insights(payload(items))["fundamentals"])
        self.assertIn("34.6%", text)
        self.assertIn("58.0%", text)
        self.assertIn("自身1227筆正值歷史", text)
        self.assertNotIn("並非同業折價", text)

    def test_cash_profit_comparison_requires_identical_proven_period_and_unit(self):
        common = {"period_start": "20260101", "period_end": "20260630", "period_basis": "year_to_date", "unit": "TWD", "資料期間": "20260630"}
        items = [metric("financial_IncomeAfterTaxes", 10, 項目="稅後淨利", **common),
                 metric("financial_OperatingActivities", -20, 項目="營業活動現金流量", year_ago_value=4, **common)]
        self.assertIn("方向背離", "".join(build_report_insights(payload(items))["fundamentals"]))
        for key, value in (("unit", "單位未核實"), ("period_basis", "quarter"), ("period_start", "20260401")):
            changed = copy.deepcopy(items)
            changed[0][key] = value
            result = build_report_insights(payload(changed))
            self.assertNotIn("方向背離", "".join(result["fundamentals"]))
            self.assertIn("未做盈餘現金實現程度比較", "".join(result["data_notes"]))

    def test_opposing_price_signals_volume_volatility_and_separate_outputs(self):
        features = [{"name": key, "value": value} for key, value in {
            "return_5": .0058, "return_20": -.0516, "return_60": .0355,
            "volume_ratio_20": .63, "volatility_20": .0201, "volatility_60": .0268}.items()]
        for feature in features:
            feature["unit"] = "倍" if feature["name"] == "volume_ratio_20" else "比例"
        prediction = {"prob_up": .4908, "expected_return": .0833, "calibration_status": "uncalibrated"}
        history = [{"date": f"2026-10-{day:02d}", "adjusted_close": 350, "price_basis": "adjusted"} for day in range(1, 22)]
        history.append({"date": "2026-10-22", "adjusted_close": 349, "price_basis": "adjusted"})
        original = payload(feature_values=features, prediction=prediction, price_history=history)
        before = copy.deepcopy(original)
        result = build_report_insights(original)
        self.assertNotIn("不同輸出", "".join(result["data_notes"]))
        self.assertNotIn("尚未校準", "".join(result["data_notes"]))
        self.assertIn("弱量反彈", "".join(result["summary"]))
        self.assertIn("短期反彈與中期回落並存", "".join(result["technical"]))
        self.assertIn("0.63倍", "".join(result["technical"]))
        self.assertIn("短窗口波動較低", "".join(result["technical"]))
        self.assertEqual(original, before)

    def test_at_most_two_distinct_cross_signal_summaries_require_aligned_evidence(self):
        features = [{"name": key, "value": value, "unit": unit, "asof": ASOF} for key, value, unit in (
            ("return_5", .0058, "比例"), ("return_20", -.0516, "比例"), ("volume_ratio_20", .63, "倍"))]
        items = [metric("revenue_3m", 200, yoy=.9444, complete=True, 資料期間="202604～202606"),
                 metric("revenue_12m", 500, yoy=.5752, complete=True, 資料期間="202507～202606"),
                 metric("financial_OperatingActivities", -20, year_ago_value=4, unit="TWD",
                        period_basis="year_to_date", period_start="20260101", period_end="20260630",
                        year_ago_period_start="20250101", year_ago_period_end="20250630", 資料期間="20260630"),
                 metric("institutional_foreign_5", 20, observed_days=5, source_route="flow", unit="股", 資料期間="20261016～20261022"),
                 metric("institutional_foreign_20", -10, observed_days=20, source_route="flow", unit="股", 資料期間="20260922～20261022")]
        original = payload(items, feature_values=features)
        before = copy.deepcopy(original)
        result = build_report_insights(original)
        self.assertEqual(len(result["summary"]), 2)
        self.assertIn("弱量反彈", result["summary"][0])
        self.assertIn("外資短期淨買，中期累計仍淨賣", result["summary"][0])
        self.assertIn("2026-06", result["summary"][1])
        self.assertIn("年初累計營業現金流由正轉負", result["summary"][1])
        self.assertIn("成長與現金流背離", result["summary"][1])
        body = [sentence for key in ("technical", "fundamentals", "flows") for sentence in result[key]]
        self.assertFalse(set(result["summary"]) & set(body))
        self.assertNotIn("因此", "".join(result["summary"]))
        self.assertEqual(original, before)
        for key, value in (("period_end", "20260331"), ("period_basis", "unknown"),
                           ("year_ago_period_start", None), ("unit", "unknown"), ("year_ago_unit", "USD")):
            changed = copy.deepcopy(original)
            changed["report_comparison"]["metrics"][2][key] = value
            self.assertNotIn("成長與現金流背離", "".join(build_report_insights(changed)["summary"]))
        changed = copy.deepcopy(original)
        changed["feature_values"][0]["asof"] = "20261021"
        self.assertNotIn("弱量反彈", "".join(build_report_insights(changed)["summary"]))

    def test_summary_covers_four_price_directions_and_one_times_volume_reference(self):
        for r5, r20, expected in ((.01, .02, "短中期均上漲"), (-.01, -.02, "短中期均回落"),
                                  (.01, -.02, "短期反彈，中期仍回落"), (-.01, .02, "短期回落，中期仍上漲")):
            for volume, activity in ((.63, "低於"), (1, "等於"), (1.5, "高於")):
                features = [{"name": key, "value": value, "unit": unit} for key, value, unit in (
                    ("return_5", r5, "比例"), ("return_20", r20, "比例"), ("volume_ratio_20", volume, "倍"))]
                result = build_report_insights(payload(feature_values=features))
                self.assertEqual(len(result["summary"]), 1)
                direction = "呈弱量反彈" if r5 > 0 > r20 and volume < 1 else expected
                self.assertIn(direction, result["summary"][0])
                self.assertIn(f"量能{activity}20日基準", result["summary"][0])
        for r5, r20, volume in ((0, -.02, .63), (.01, 0, 1), (.01, -.02, 0),
                               (None, -.02, .63), (.01, None, 1), (.01, -.02, None)):
            features = [{"name": key, "value": value, "unit": unit} for key, value, unit in (
                ("return_5", r5, "比例"), ("return_20", r20, "比例"), ("volume_ratio_20", volume, "倍"))]
            self.assertFalse(build_report_insights(payload(feature_values=features))["summary"])

    def test_body_has_facts_without_repeated_disclaimer_tails(self):
        items = [metric("revenue_monthly", 100, yoy=.2, mom=.1, 資料期間="202609"),
                 metric("valuation_per", 13.06, percentile=.346, historical_count=1227, unit="倍"),
                 metric("margin_purchase_balance", 6572, delta=35, unit="單位未核實"),
                 metric("tdcc_13", 2.5, delta=-.52, unit="%", 項目="集保持股分級13", weekly_comparable=False),
                 metric("financial_OperatingActivities", -20, year_ago_value=4, unit="單位未核實", 資料期間="20260630")]
        features = [{"name": key, "value": value, "unit": unit} for key, value, unit in (
            ("return_5", .0058, "比例"), ("return_20", -.0516, "比例"), ("volume_ratio_20", .63, "倍"),
            ("volatility_20", .0201, "比例"), ("volatility_60", .0268, "比例"))]
        result = build_report_insights(payload(items, feature_values=features))
        body = "".join(result[key][index] for key in ("technical", "fundamentals", "flows") for index in range(len(result[key])))
        for phrase in ("不能只用", "不推定", "不是未來價格區間", "不等於股價", "不推論主力", "原因仍須", "未據此推定", "並非同業折價"):
            self.assertNotIn(phrase, body)
        self.assertNotIn("原始單位未核實", body)
        self.assertIn("部分數值單位未核實", "".join(result["data_notes"]))
        self.assertEqual(sum("非完整週比較" in text for text in result["data_notes"]), 1)
        self.assertNotIn("非完整週比較", body)

    def test_nonlimiting_source_states_stay_in_json_not_limitations(self):
        ctx = {"schema": "single-stock-report-context-v1", "stock_id": "2376", "asof": ASOF,
               "sources": [{"route_id": route, "name_zh": name} for route, name in (
                   ("unselected", "未選來源"), ("not_applicable", "不適用來源"), ("events", "零事件來源"))],
               "statuses": [{"route_id": route, "status": status} for route, status in (
                   ("unselected", "not_selected"), ("not_applicable", "not_applicable"), ("events", "confirmed_no_event"))]}
        before = copy.deepcopy(ctx)
        result = build_report_insights(payload(report_context=ctx))
        notes = "".join(result["data_notes"])
        for name in ("未選來源", "不適用來源", "零事件來源"):
            self.assertNotIn(name, notes)
        self.assertEqual(ctx, before)

    def test_unproved_feature_and_valuation_units_do_not_become_percent_or_multiple(self):
        result = build_report_insights(payload([metric("valuation_per", 13, percentile=.3, historical_count=20, unit="unknown")],
            feature_values=[{"name": "return_5", "value": .9, "unit": "unknown"}, {"name": "return_20", "value": -.5, "unit": "unknown"}]))
        self.assertFalse(result["fundamentals"])
        self.assertFalse(result["technical"])
        self.assertIn("價量特徵的單位", "".join(result["data_notes"]))

    def test_zero_is_not_missing_and_status_notes_are_consolidated(self):
        ctx = build_report_context({route: pd.DataFrame([{"stock_id": "2376", "date": ASOF, "value": 0}]) for route in ("a", "b")},
            [manifest("a"), manifest("b")], stock=STOCK, asof=ASOF)
        result = build_report_insights(payload(report_context=ctx))
        self.assertEqual(sum("部分數值單位未核實" in text for text in result["data_notes"]), 1)
        sentences = [text for values in result.values() for text in values]
        self.assertEqual(len(sentences), len(set(sentences)))
        self.assertTrue(all(re.search(r"[\u4e00-\u9fff]", text) for text in sentences))
        self.assertEqual(set(result), {"summary", "technical", "fundamentals", "flows", "context", "data_notes"})

    def test_context_identity_mismatch_and_future_metric_not_used(self):
        ctx = build_report_context({}, [], stock=STOCK, asof="2026-10-22")
        result = build_report_insights(payload([metric("revenue_monthly", 1, yoy=99, 資料期間="202611")], report_context=ctx))
        self.assertFalse(result["fundamentals"])
        self.assertIn("資料日與本報告不一致", "".join(result["data_notes"]))


if __name__ == "__main__":
    unittest.main()
