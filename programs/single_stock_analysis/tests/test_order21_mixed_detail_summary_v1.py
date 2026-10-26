"""Order21 consumer support for the updater's mixed detail / daily-broker-summary format (mixed_detail_summary_v1).

The updater publishes Order21 (TaiwanStockTradingDailyReport) either as broker x price detail rows or, per stock-day,
as one sufficient-statistics row per broker (schema twstock-updater.order21-daily-broker-summary.v1, no price).
These literal fixtures cover every consumer path codex deployed on 10-04 without tests:
``order21_row_format`` / ``order21_summary_hint`` (strict statistics), the per-stock row-cache filter (v3 semantics,
invalid rows broadcast to every stock), ``PublishedSources.rows`` and ``published_single_stock_projection._select``
(summary natural key, same stock-day mixed formats excluded, conflicts and malformed rows as gaps, cache path equal
to the full read) and the broker features of ``tw_single_stock_feature_sources`` (summary rows give the same features
as the detail rows they summarize; a mixed stock-day is rejected).  The SUMMARY rows are hand-computed from DETAIL.
No live input, network or model execution.
"""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import stock_data_sources as sources
from stock_data_sources import (ORDER21_SUMMARY_SCHEMA, PublishedSources, SourceSpec, order21_row_format,
                                order21_summary_hint)
import published_single_stock_projection as projection

ROUTE = "finmind_trading_daily_report"
# The Order21 row-cache filter identity moved from v3 to v4 when the minimal summary rows
# (twstock-updater.order21-daily-broker-summary.v2) became readable; the filter still differs from every other route.
ORDER21_FILTER_SCHEMA = "published-stock-row-filter-v4-order21-minimal-summary"
SESSIONS = ("2026-09-24", "2026-09-25", "2026-09-28", "2026-09-29")
DETAIL = [
    {"date": "2026-09-24", "stock_id": "2330", "securities_trader_id": "A", "price": 10, "buy": 10, "sell": 0},
    {"date": "2026-09-24", "stock_id": "2330", "securities_trader_id": "A", "price": 20, "buy": 0, "sell": 5},
    {"date": "2026-09-24", "stock_id": "2330", "securities_trader_id": "B", "price": 15, "buy": 5, "sell": 0},
    {"date": "2026-09-25", "stock_id": "2330", "securities_trader_id": "A", "price": 10, "buy": 8, "sell": 2},
    {"date": "2026-09-25", "stock_id": "2330", "securities_trader_id": "B", "price": 15, "buy": 0, "sell": 4},
    {"date": "2026-09-29", "stock_id": "2330", "securities_trader_id": "A", "price": 12, "buy": 3, "sell": 0},
]


def summary(day, broker, *, buy, sell, buy_amount, sell_amount, levels, pos, neg, pos_amount, neg_amount):
    return {"date": day, "stock_id": "2330", "securities_trader_id": broker, "buy": buy, "sell": sell,
            "buy_amount": buy_amount, "sell_amount": sell_amount, "price_level_count": levels,
            "source_row_count": levels, "positive_net_volume": pos, "negative_net_volume": neg,
            "positive_net_price_amount": pos_amount, "negative_net_price_amount": neg_amount}


# Per stock-day-broker sums over DETAIL's price levels: buy/sell volumes and amounts (volume x price), and the
# positive / negative net volume and net amount of each level (09-24 A: +10 at 10, -5 at 20).
SUMMARY = [
    summary("2026-09-24", "A", buy=10, sell=5, buy_amount="100", sell_amount="100", levels=2,
            pos=10, neg=5, pos_amount="100", neg_amount="100"),
    summary("2026-09-24", "B", buy=5, sell=0, buy_amount="75", sell_amount="0", levels=1,
            pos=5, neg=0, pos_amount="75", neg_amount="0"),
    summary("2026-09-25", "A", buy=8, sell=2, buy_amount="80", sell_amount="20", levels=1,
            pos=6, neg=0, pos_amount="60", neg_amount="0"),
    summary("2026-09-25", "B", buy=0, sell=4, buy_amount="0", sell_amount="60", levels=1,
            pos=0, neg=4, pos_amount="0", neg_amount="60"),
    summary("2026-09-29", "A", buy=3, sell=0, buy_amount="36", sell_amount="0", levels=1,
            pos=3, neg=0, pos_amount="36", neg_amount="0"),
]
# Hand-computed broker features (sessions 09-24, 09-25, 09-29; 09-28 has no report, so 09-29 restarts runs).
EXPECTED = {
    "trading_daily_broker_count": [2, 2, 1],
    "trading_daily_broker_net_flow": [10, 2, 3],
    "trading_daily_broker_concentration_hhi": [0.625, 0.52, 1.0],
    "trading_daily_broker_continuous_buying": [1, 2, 1],
    "trading_daily_broker_continuous_selling": [0, 1, 0],
    "trading_daily_broker_average_price_spread": [175 / 15 - 20, 10 - 15, None],
}


def frame(rows):
    value = pd.DataFrame(copy.deepcopy(rows))
    value.index = pd.to_datetime(value["date"])
    return value


class Order21RowFormatTests(unittest.TestCase):
    def assert_invalid(self, row, message, *, dataframe=False):
        with self.assertRaises(ValueError) as caught:
            order21_row_format(row, dataframe=dataframe)
        self.assertEqual(str(caught.exception), message)

    def test_detail_and_summary_are_classified_from_statistics_not_from_a_missing_price(self):
        self.assertEqual(ORDER21_SUMMARY_SCHEMA, "twstock-updater.order21-daily-broker-summary.v1")
        for row in DETAIL:
            self.assertFalse(order21_summary_hint(row))
            self.assertEqual(order21_row_format(row), "detail")
        for row in SUMMARY:
            self.assertTrue(order21_summary_hint(row))
            self.assertEqual(order21_row_format(row), "summary")
        self.assertEqual(order21_row_format(dict(DETAIL[0], price=0)), "detail")      # a real zero price
        self.assertTrue(order21_summary_hint({"price_level_count": 1}))
        self.assertFalse(order21_summary_hint({"buy": 1, "sell": 0, "buy_amount": "1", "sell_amount": "0"}))
        no_price = dict(DETAIL[0])
        no_price.pop("price")
        self.assert_invalid(no_price, "order21_identity_missing:price")
        self.assert_invalid(dict(DETAIL[0], price=""), "order21_identity_missing:price")

    def test_identity_fields_fail_closed_for_both_formats(self):
        self.assert_invalid(["not", "a", "row"], "order21_identity_missing")
        for base in (DETAIL[0], SUMMARY[0]):
            for field in ("stock_id", "date", "securities_trader_id"):
                for value in (None, ""):
                    with self.subTest(format=order21_row_format(base), field=field, value=value):
                        self.assert_invalid(dict(base, **{field: value}), "order21_identity_missing:" + field)

    def test_summary_sufficient_statistics_are_checked_exactly(self):
        mutations = (
            ("summary_has_price", lambda row: row.update(price=0)),
            ("positive_net_volume", lambda row: row.pop("positive_net_volume")),
            ("negative_net_volume", lambda row: row.update(negative_net_volume=-1)),
            ("buy", lambda row: row.update(buy=True)),
            ("buy", lambda row: row.update(buy=10.0)),
            ("buy_amount", lambda row: row.update(buy_amount=100)),
            ("buy_amount", lambda row: row.update(buy_amount="100.0")),
            ("buy_amount", lambda row: row.update(buy_amount="0100")),
            ("date", lambda row: row.update(date=20260924)),
            ("volume_or_count_consistency", lambda row: row.update(positive_net_volume=9)),
            ("volume_or_count_consistency", lambda row: row.update(source_row_count=1)),
            ("volume_or_count_consistency", lambda row: row.update(price_level_count=0, source_row_count=0)),
            ("amount_consistency", lambda row: row.update(positive_net_price_amount="99")),
        )
        for reason, mutate in mutations:
            with self.subTest(reason=reason):
                row = copy.deepcopy(SUMMARY[0])
                mutate(row)
                self.assert_invalid(row, "order21_summary_invalid:" + reason)
        with self.assertRaisesRegex(ValueError, "^order21_summary_invalid:"):    # the ISO parser's own message
            order21_row_format(dict(SUMMARY[0], date="2026/09/24"))
        # A side with no volume cannot carry an amount (amounts otherwise consistent).
        row = dict(SUMMARY[1], sell_amount="5", positive_net_price_amount="70")
        self.assert_invalid(row, "order21_summary_invalid:zero_volume_amount")

    def test_optional_side_price_statistics_are_all_or_nothing_and_bounded(self):
        sides = dict(buy_price_weight=10, buy_price_amount="120", sell_price_weight=5, sell_price_amount="105")
        self.assertEqual(order21_row_format(dict(SUMMARY[0], **sides)), "summary")
        self.assertEqual(order21_row_format(dict(SUMMARY[1], buy_price_weight=5, buy_price_amount="90",
                                                 sell_price_weight=0, sell_price_amount=None)), "summary")
        self.assert_invalid(dict(SUMMARY[0], buy_price_weight=10),
                            "order21_summary_invalid:side_statistics_incomplete")
        self.assert_invalid(dict(SUMMARY[0], **dict(sides, buy_price_weight=11)),
                            "order21_summary_invalid:side_weight")
        self.assert_invalid(dict(SUMMARY[0], **dict(sides, sell_price_weight=0)),
                            "order21_summary_invalid:zero_side_weight_amount")
        self.assert_invalid(dict(SUMMARY[0], **dict(sides, buy_price_amount="120.50")),
                            "order21_summary_invalid:buy_price_amount")

    def test_dataframe_mode_accepts_only_pandas_integral_coercion(self):
        coerced = dict(SUMMARY[0], buy=10.0, sell=5.0, positive_net_volume=10.0, negative_net_volume=5.0,
                       price_level_count=2.0, source_row_count=2.0, price=float("nan"))
        self.assertEqual(order21_row_format(coerced, dataframe=True), "summary")
        self.assertEqual(order21_row_format(dict(SUMMARY[1], buy_price_weight=5.0, buy_price_amount="90",
                                                 sell_price_weight=0.0, sell_price_amount=float("nan")),
                                            dataframe=True), "summary")
        self.assert_invalid(dict(coerced, buy=10.5), "order21_summary_invalid:buy", dataframe=True)
        self.assert_invalid(dict(SUMMARY[0], buy=10.0), "order21_summary_invalid:buy")
        self.assertTrue(order21_summary_hint({"price_level_count": float("nan"), "buy_price_weight": 1.0},
                                             dataframe=True))
        self.assertFalse(order21_summary_hint({"price_level_count": float("nan")}, dataframe=True))


class Order21RowCacheFilterTests(unittest.TestCase):
    def test_only_order21_uses_the_v3_filter_and_invalid_rows_reach_every_stock_cache(self):
        self.assertEqual(sources._stock_cache_filter_semantics(route_id="finmind_price_adj")["schema"],
                         "published-stock-row-filter-v2")
        self.assertEqual(sources._stock_cache_filter_semantics(route_id=ROUTE)["schema"],
                         ORDER21_FILTER_SCHEMA)
        select = sources._row_selected_for_stock_cache
        self.assertTrue(select(SUMMARY[0], "2330", ROUTE))
        self.assertFalse(select(SUMMARY[0], "2317", ROUTE))
        self.assertFalse(select(DETAIL[0], "2317", ROUTE))
        malformed = dict(SUMMARY[0])
        malformed.pop("negative_net_volume")
        no_broker = dict(DETAIL[0], securities_trader_id="")
        for row in (malformed, no_broker):
            self.assertTrue(select(row, "2317", ROUTE))                 # its gap must reach every stock
            self.assertFalse(select(row, "2317", "finmind_price_adj"))


class PublishedFixture:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "published"
        self.root.mkdir()
        self.cache = Path(temporary.name) / "cache"

    def reader(self, rows, *, cache=False, status="SUCCESS", metadata=None):
        raw = json.dumps(rows, separators=(",", ":")).encode("utf-8")
        (self.root / "rows.json").write_bytes(raw)
        entry = dict(route_id=ROUTE, path="rows.json", sha256=hashlib.sha256(raw).hexdigest(), size=len(raw),
                     record_count=len(rows), status=status, metadata=dict(metadata or {}))
        (self.root / "manifest.json").write_text(json.dumps(dict(schema="stock-data-publication-v1",
                                                                 datasets=[entry])), encoding="utf-8")
        return PublishedSources([SourceSpec("manifest_json", self.root, "manifest.json", "fixture")],
                                row_cache_dir=self.cache if cache else None)


class Order21GenericReaderTests(PublishedFixture, unittest.TestCase):
    def test_summary_rows_use_the_broker_natural_key_without_price(self):
        item = self.reader(SUMMARY).rows(ROUTE, stock_id="2330",
                                         key_fields=("stock_id", "date", "securities_trader_id", "price"))
        self.assertEqual(list(item["records"]), SUMMARY)
        self.assertEqual(item["status"], "available")
        self.assertFalse(item["gaps"])

    def test_same_stockday_mixed_formats_are_excluded_as_a_consumer_gap(self):
        item = self.reader(DETAIL[:3] + SUMMARY).rows(ROUTE, stock_id="2330")
        self.assertIn("order21_mixed_detail_summary:2330|2026-09-24", item["gaps"])
        self.assertEqual([row["date"] for row in item["records"]], [SESSIONS[1], SESSIONS[1], SESSIONS[3]])
        self.assertEqual(item["status"], "partial")
        self.assertFalse(item["source_available_complete"])
        self.assertFalse(item["model_usable"])

    def test_identity_gaps_duplicates_and_conflicts(self):
        item = self.reader([dict(DETAIL[0], securities_trader_id=None)] + DETAIL[1:3]).rows(ROUTE, stock_id="2330")
        self.assertTrue(any(gap.endswith("order21_identity_missing:securities_trader_id") for gap in item["gaps"]))
        self.assertEqual(len(item["records"]), 2)
        self.assertFalse(item["source_available_complete"])
        same = self.reader(SUMMARY + [dict(SUMMARY[0])]).rows(ROUTE, stock_id="2330")
        self.assertEqual(list(same["records"]), SUMMARY)
        self.assertFalse(same["gaps"])
        changed = self.reader(SUMMARY + [dict(SUMMARY[0], securities_trader="other name")]).rows(
            ROUTE, stock_id="2330")
        self.assertEqual(list(changed["records"]), SUMMARY)
        self.assertIn("natural key conflicts: 1; first source retained", changed["gaps"])
        self.assertEqual(changed["completion_state"], "conflict")


class Order21ProjectionTests(PublishedFixture, unittest.TestCase):
    def select(self, rows, **options):
        return projection._select(self.reader(rows, **options), ROUTE, "2330", None, None)

    def test_summary_identity_without_price_survives_the_pinned_projection(self):
        for cache in (False, True):
            with self.subTest(cache=cache):
                item = self.select(SUMMARY, cache=cache, metadata={"order21_summary_schema": ORDER21_SUMMARY_SCHEMA})
                self.assertEqual(list(item["records"]), SUMMARY)
                self.assertFalse(item["gaps"])
                self.assertEqual(item["unknown_publication_time_rows"], len(SUMMARY))

    def test_mixed_stockday_conflict_and_malformed_summary_are_gaps_in_both_read_paths(self):
        malformed = copy.deepcopy(SUMMARY)
        malformed[0].pop("negative_net_price_amount")
        foreign = dict(SUMMARY[0], stock_id="2317", positive_net_volume=9)   # another stock's malformed row
        cases = (
            (DETAIL[:3] + SUMMARY, "order21_mixed_detail_summary:2330|2026-09-24",
             [SESSIONS[1], SESSIONS[1], SESSIONS[3]]),
            (SUMMARY + [dict(SUMMARY[0], securities_trader="other name")],
             "trading_daily_report_natural_key_conflict", [row["date"] for row in SUMMARY]),
            (malformed, "order21_summary_invalid:negative_net_price_amount", [row["date"] for row in SUMMARY[1:]]),
            (SUMMARY + [foreign], "order21_summary_invalid:volume_or_count_consistency",
             [row["date"] for row in SUMMARY]),
        )
        for rows, gap, dates in cases:
            results = []
            for cache in (False, True):
                with self.subTest(gap=gap, cache=cache):
                    item = self.select(rows, cache=cache)
                    self.assertIn(gap, item["gaps"])
                    self.assertEqual([row["date"] for row in item["records"]], dates)
                    self.assertEqual(item["status"], "partial")
                    self.assertFalse(item["model_usable"])
                    results.append(item)
            self.assertEqual([list(item["records"]) for item in results][0], list(results[1]["records"]))
            self.assertEqual(results[0]["gaps"], results[1]["gaps"])

    def test_partial_or_waiting_quota_evidence_is_not_made_complete(self):
        item = self.select(SUMMARY, status="PARTIAL", metadata={"gaps": ["unresolved"],
                                                                "acquisition_status": "WAITING_QUOTA"})
        self.assertEqual(item["status"], "partial")
        self.assertIn("fixture:WAITING_QUOTA", item["gaps"])
        self.assertIn("fixture:publication_gaps", item["gaps"])

    def test_row_cache_is_written_with_the_v3_identity_and_read_back_unchanged(self):
        reader = self.reader(DETAIL[3:] + SUMMARY[:2], cache=True)
        first = projection._select(reader, ROUTE, "2330", None, None)
        self.assertEqual(len(first["records"]), 5)
        cached = reader.cached_stock_artifact_rows(ROUTE, "2330", pinned_snapshot_fingerprint=reader.fingerprint)
        self.assertIsNotNone(cached)
        rows = [row for _, items in cached["artifacts"] for row in items]
        self.assertEqual(sorted(json.dumps(row, sort_keys=True) for row in rows),
                         sorted(json.dumps(row, sort_keys=True) for row in DETAIL[3:] + SUMMARY[:2]))
        [document] = list(self.cache.glob("*.json"))
        identity = json.loads(document.read_text(encoding="utf-8"))["identity"]
        self.assertEqual(identity["filter_semantics"]["schema"], ORDER21_FILTER_SCHEMA)


if __name__ == "__main__":
    unittest.main()
