"""A per-stock row cache and a full strict read of the same pinned payload give the same completion scope.

``published_single_stock_projection._select`` proves an empty all-stock window (official_market_daily
``confirmed_no_data``, finmind_government_bank ``source_unavailable`` / ``pending_release``, an empty artifact) from
the scope of every row of the artifact.  A stock read from the consumer row cache only sees that stock's rows, so the
cache records the all-stock scope (rows, non-object rows, invalid dates, distinct strict ISO dates) while the whole
payload is streamed, and ``_select`` uses it.  A cache written before this record exists stays conservative.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import stock_data_sources as sources
import published_single_stock_projection as projection


def market_rows(days, stocks=("2330", "2317")):
    return [{"stock_id": stock, "date": day, "market": "TWSE", "open": 100, "high": 110, "low": 90,
             "close": 105, "volume": 1000} for day in days for stock in stocks]


class CompletionScopeCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "published"
        self.root.mkdir()
        self.cache = Path(temporary.name) / "cache"
        self.specs = [sources.SourceSpec("manifest_json", self.root, "manifest.json", "fixture")]

    def publish(self, raw, route_id, status):
        (self.root / "payload.json").write_bytes(raw)
        ref = dict(route_id=route_id, path="payload.json", sha256=hashlib.sha256(raw).hexdigest(),
                   size=len(raw), status=status)
        (self.root / "manifest.json").write_bytes(json.dumps(dict(
            schema="stock-data-publication-v1", datasets=[ref])).encode("utf-8"))

    def reader(self, cache=True, cache_dir=None):
        return sources.PublishedSources(self.specs, row_cache_dir=(cache_dir or self.cache) if cache else None)

    def scopes(self, route_id, start, end, *, rows=None, raw=None, status="SUCCESS"):
        raw = raw if raw is not None else json.dumps({"data": rows}, separators=(",", ":")).encode("utf-8")
        self.publish(raw, route_id=route_id, status=status)
        full = projection._select(self.reader(False), route_id, "2330", start, end)
        cold = projection._select(self.reader(), route_id, "2330", start, end)
        warm = projection._select(self.reader(), route_id, "2330", start, end)
        return full, cold, warm

    def assert_same(self, full, *others, gaps=True):
        # gaps=False: an invalid date of ANOTHER stock is reported as an 'invalid_date' gap by a per-stock cache
        # (its quality counts every row, like a sealed projection) but not by the full read; that reporting
        # difference predates this change and does not affect the completion scope compared here.
        for other in others:
            self.assertEqual(other["_completion_inputs"][0]["scope"], full["_completion_inputs"][0]["scope"])
            self.assertEqual(other["_completion_inputs"][0]["global_empty"], full["_completion_inputs"][0]["global_empty"])
            for key in ("status", "completion_state", "source_available_complete", "completion_evidence_valid",
                        "records") + (("gaps",) if gaps else ()):
                self.assertEqual(other[key], full[key], key)

    def test_many_dates_and_windows_match_the_full_read(self):
        days = [f"2026-{month:02d}-{day:02d}" for month in (7, 8, 9) for day in range(1, 29)]
        rows = market_rows(days) + market_rows(["2026-09-30"], stocks=("2317",))
        for start, end in ((None, "2026-09-30"), ("2026-09-30", "2026-09-30"), ("2026-10-01", "2026-10-02"),
                           ("2026-06-01", "2026-06-30"), ("2026-08-29", "2026-08-31"), (None, None)):
            with self.subTest(start=start, end=end):
                full, cold, warm = self.scopes("official_market_daily", start, end, rows=rows, status="PARTIAL")
                scope = full["_completion_inputs"][0]["scope"]
                self.assertTrue(scope["complete"])
                # C14: the complete date set is kept (formerly None above 64 distinct dates)
                self.assertEqual(scope["dates"], tuple(sorted(set(days) | {"2026-09-30"})))
                self.assertEqual((scope["minimum"], scope["maximum"]), ("2026-07-01", "2026-09-30"))
                self.assert_same(full, cold, warm)
        full, cold, _ = self.scopes("official_market_daily", "2026-09-30", "2026-09-30", rows=rows, status="PARTIAL")
        self.assertFalse(full["_completion_inputs"][0]["scope"]["query_empty"])     # 2317 traded, 2330 did not
        self.assertEqual(full["records"], ())
        self.assert_same(full, cold)

    def test_more_than_8192_dates_keep_the_exact_window_proof(self):
        # C14: neither the full read (formerly 64 dates) nor the row cache's all-stock scope (formerly 8192 dates)
        # summarizes the date set, so an empty window inside the date range is proved in every read path, as is a
        # non-empty one.
        from datetime import date, timedelta
        first = date(2000, 1, 3)
        gap = (first + timedelta(days=5000)).isoformat()
        days = [day for day in ((first + timedelta(days=offset)).isoformat() for offset in range(9000)) if day != gap]
        rows = [{"stock_id": "2317", "date": day, "buy": 1} for day in days]
        for start, end, empty in ((gap, gap, True), (days[4999], days[4999], False), (gap, days[5000], False)):
            with self.subTest(start=start, end=end):
                full, cold, warm = self.scopes("finmind_government_bank", start, end, rows=rows)
                for item in (full, cold, warm):
                    scope = item["_completion_inputs"][0]["scope"]
                    self.assertTrue(scope["complete"])
                    self.assertEqual(len(scope["dates"]), 8999)
                    self.assertIs(projection._completion_query_empty(scope, start, end, "date"), empty)
                    # re-scoped to another window, the private proof is still exact (formerly min/max only)
                    self.assertIs(projection._completion_query_empty(scope, gap, gap, "date"), True)
                    self.assertIs(projection._completion_query_empty(scope, days[0], days[0], "date"), False)
                self.assert_same(full, cold, warm)

    def test_few_dates_invalid_rows_and_government_bank_match_the_full_read(self):
        rows = market_rows(["2026-09-17", "2026-09-18"]) + [{"stock_id": "2317", "date": "2026/09/18"}, None]
        for route_id in ("official_market_daily", "finmind_government_bank"):
            with self.subTest(route_id=route_id):
                full, cold, warm = self.scopes(route_id, None, "2026-09-20", rows=rows)
                scope = full["_completion_inputs"][0]["scope"]
                self.assertFalse(scope["complete"])                         # invalid rows of another stock
                self.assertEqual(scope["dates"], ("2026-09-17", "2026-09-18"))
                self.assert_same(full, cold, warm, gaps=False)
                self.assertIn("non_object_row", cold["gaps"])
        clean = market_rows(["2026-09-17", "2026-09-18"])
        full, cold, warm = self.scopes("finmind_government_bank", "2026-09-19", "2026-09-20", rows=clean)
        self.assertTrue(full["_completion_inputs"][0]["scope"]["complete"])
        self.assertTrue(projection._completion_query_empty(full["_completion_inputs"][0]["scope"],
                                                           "2026-09-19", "2026-09-20", "date"))
        self.assert_same(full, cold, warm)

    def test_empty_artifact_of_a_non_date_route_is_globally_empty_in_both_paths(self):
        full, cold, warm = self.scopes("finmind_trading_daily_report", None, None, raw=b'{"data":[]}')
        for item in (full, cold, warm):
            self.assertTrue(item["_completion_inputs"][0]["global_empty"])
            self.assertTrue(item["_completion_inputs"][0]["scope"]["complete"])
        self.assert_same(full, cold, warm)
        full, cold, warm = self.scopes("finmind_trading_daily_report", None, None,
            rows=[{"stock_id": "2317", "date": "2026-09-18", "securities_trader_id": "A", "price": 1, "buy": 1,
                   "sell": 0}])
        self.assertFalse(full["_completion_inputs"][0]["scope"]["complete"])
        self.assert_same(full, cold, warm)

    def test_cache_written_without_the_scope_record_stays_conservative(self):
        rows = market_rows(["2026-09-17", "2026-09-18"])
        raw = json.dumps({"data": rows}, separators=(",", ":")).encode("utf-8")
        self.publish(raw, route_id="official_market_daily", status="PARTIAL")
        with patch.object(sources._RowCollector, "finish", lambda collector: None):
            old = projection._select(self.reader(), "official_market_daily", "2330", None, "2026-09-20")
        reread = projection._select(self.reader(), "official_market_daily", "2330", None, "2026-09-20")
        full = projection._select(self.reader(False), "official_market_daily", "2330", None, "2026-09-20")
        for item in (old, reread):
            self.assertFalse(item["_completion_inputs"][0]["scope"]["complete"])
            self.assertEqual(item["records"], full["records"])
        self.assertTrue(full["_completion_inputs"][0]["scope"]["complete"])

    def test_batch_single_and_reader_caches_record_the_same_scope(self):
        rows = market_rows(["2026-09-17", "2026-09-18"]) + [{"stock_id": "2317", "date": "bad"}]
        raw = json.dumps({"data": rows}, separators=(",", ":")).encode("utf-8")
        self.publish(raw, route_id="official_market_daily", status="PARTIAL")
        produced = {}
        for label in ("batch", "single", "reader"):
            cache_dir = self.root.parent / f"scope-{label}"
            reader = self.reader(cache_dir=cache_dir)
            if label == "batch":
                self.assertFalse(reader.prewarm_stock_rows(["2330", "2317"], ["official_market_daily"])["gaps"])
            elif label == "single":
                self.assertFalse(reader.prewarm_stock_rows(["2330"], ["official_market_daily"],
                                                           single_stock_prepare=True)["gaps"])
            else:
                for _, stock_rows in reader.iter_stock_artifact_rows("official_market_daily", "2330"):
                    list(stock_rows)
            cached = reader.cached_stock_artifact_rows("official_market_daily", "2330",
                                                       pinned_snapshot_fingerprint=reader.fingerprint)
            produced[label] = cached["artifacts"][0][0].metadata["_projection_global_scope"]
        self.assertEqual(produced["batch"], produced["single"])
        self.assertEqual(produced["batch"], produced["reader"])
        self.assertEqual(produced["batch"], dict(schema="published-global-row-scope-v1", rows=5, non_object_rows=0,
            date_field="date", invalid_date_rows=1, dates=["2026-09-17", "2026-09-18"]))

    def test_scope_recorded_for_another_date_field_or_malformed_is_ignored(self):
        scope = dict(schema="published-global-row-scope-v1", rows=2, non_object_rows=0, date_field="date",
                     invalid_date_rows=0, dates=["2026-09-18"])
        metadata = {"_projection_global_scope": scope}
        self.assertEqual(projection._cached_global_row_scope(metadata, "official_market_daily", "date", None, None),
                         (2, False, {"2026-09-18"}, "2026-09-18", "2026-09-18", 1))
        self.assertIsNone(projection._cached_global_row_scope(metadata, "official_market_daily",
                                                              "observation_date", None, None))
        # a recorded YYYYMMDD day is the same day (date directive 2026-10-06)
        self.assertEqual(projection._cached_global_row_scope(
            {"_projection_global_scope": dict(scope, dates=["20260918"])}, "official_market_daily", "date", None,
            None), (2, False, {"2026-09-18"}, "2026-09-18", "2026-09-18", 1))
        for broken in (dict(scope, schema="other"), dict(scope, rows=-1), dict(scope, dates=None),
                       dict(scope, dates=["2026-09-18", "2026-09-17"]), dict(scope, dates=["2026/09/18"]),
                       dict(scope, non_object_rows=3), dict(scope, rows=True)):
            with self.subTest(broken=broken):
                self.assertIsNone(projection._cached_global_row_scope(
                    {"_projection_global_scope": broken}, "official_market_daily", "date", None, None))
        self.assertEqual(projection._cached_global_row_scope(
            {"_projection_global_scope": dict(scope, date_field=None, dates=None)}, "finmind_trading_daily_report",
            "date", None, None), (2, False, set(), None, None, 0))


    def test_default_spool_root_uses_current_path_contract(self):
        from types import SimpleNamespace
        calls = []

        def single(key):
            calls.append(key)
            return self.cache

        current_contract = SimpleNamespace(CONTRACT=SimpleNamespace(single=single))
        reader = SimpleNamespace(row_cache_dir=None, _sources=[{'root': self.root}])
        with patch.dict('sys.modules', {'path_contract': current_contract}):
            root = projection._consumer_spool_root(reader)
        self.assertEqual(root, (self.cache / 'published_rows' / 'query_spool').resolve())
        self.assertEqual(calls, ['cache'])


if __name__ == "__main__":
    unittest.main()
