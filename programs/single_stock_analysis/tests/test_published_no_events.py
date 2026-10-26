"""Small offline cases for positively proved eventless published snapshots."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stock_data_sources import (
    PublishedSources, SourceSpec, published_completion, published_completion_fields, published_no_events)
from published_single_stock_projection import _route_window, _select


DAY = "2026-10-03"
ROUTES = {
    "official_trading_restrictions": (
        "twse_twtawu", "twse_twt85u", "twse_announcement_notice",
        "twse_announcement_punish", "twse_twtbau2", "tpex_cmode",
        "tpex_spendi_today", "tpex_trading_warning_information",
        "tpex_disposal_information", "tpex_intraday_trading_his",
    ),
    "news_and_hot_topics_raw": (
        "topic_enrichment_ranked_feed", "twse_news_list",
        "tpex_historical_announcement", "twse_daily_major_information",
        "tpex_daily_major_information",
    ),
}


def proved_entry(route="official_trading_restrictions", *, date_only=False):
    stamp = DAY + "T14:00:00+08:00"
    receipts = [dict(
        source_id=source, status="SUCCESS", processed=True, http_status=200,
        no_events=True, row_count=0, invalid_row_count=0,
        source_sha256="a" * 64, source_size=2,
        observation_date=DAY, observation_timezone="Asia/Taipei",
        observation_precision="date" if date_only else "datetime",
        observation_basis="cache_saved_date" if date_only else "response_retrieved_at",
        observed_at=None if date_only else stamp,
    ) for source in ROUTES[route]]
    empty_sha = hashlib.sha256(b"[]").hexdigest()
    return dict(route_id=route, record_count=0, status="SUCCESS", path="payload.json",
        sha256=empty_sha, size=2,
        selected_ref=dict(path="payload.json", sha256=empty_sha, size=2), metadata=dict(
        no_events=True, snapshot_complete=True, observed_asof=DAY,
        history_unavailable=True,
        coverage=dict(collection_scope="current_only", snapshot_complete=True,
                      processed=len(receipts), success=len(receipts), total=len(receipts),
                      history_complete=False),
        snapshot_observation=dict(
            schema="twstock-updater.snapshot-observation.v1", timezone="Asia/Taipei",
            all_sources_observed=True, min_date=DAY, max_date=DAY,
            precision="date" if date_only else "datetime",
            observed_at_min=None if date_only else stamp,
            observed_at_max=None if date_only else stamp),
        source_receipts=receipts))


def scope_recovered_entry(route="official_trading_restrictions", *, date_only=False,
                          requested_from_date=DAY):
    entry = proved_entry(route, date_only=date_only)
    entry["metadata"]["partial_recovery"] = dict(
        status="resolved_for_scope",
        requested_scope=dict(requested_from_date=requested_from_date,
                             expected_asof=DAY, collection_scope="current_only"),
        resolved_prior_gaps=[],
        superseded_prior_gaps=[dict(status="history_unavailable")],
        unresolved_prior_gaps=[])
    return entry


class FixtureMixin:
    def sources(self, entry, rows=(), *, declare_identity=True):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        raw = json.dumps(list(rows), ensure_ascii=False).encode("utf-8")
        (root / "payload.json").write_bytes(raw)
        entry = copy.deepcopy(entry)
        entry.update(path="payload.json", sha256=hashlib.sha256(raw).hexdigest(), size=len(raw),
                     record_count=len(rows))
        if not declare_identity:
            entry.pop("sha256")
            entry.pop("size")
        (root / "manifest.json").write_text(json.dumps(dict(
            schema="stock-data-publication-v1", datasets=[entry])), encoding="utf-8")
        return PublishedSources([SourceSpec("manifest_json", root, "manifest.json",
            "shared_market_data" if declare_identity else "fixture")])


class PureProofTests(unittest.TestCase):
    def test_two_whitelisted_routes_and_original_date_precision(self):
        for route in ROUTES:
            for date_only in (False, True):
                with self.subTest(route=route, date_only=date_only):
                    proof = published_no_events(route, proved_entry(route, date_only=date_only),
                                                verified_empty=True, end_date=DAY)
                    self.assertEqual(proof["reason"], "no_events")
                    self.assertEqual(proof["completion_scope"], "current_snapshot")
                    self.assertFalse(proof["history_complete"])
                    self.assertFalse(proof["model_usable"])

    def test_unproved_partial_pending_and_bad_receipts_stay_rejected(self):
        mutations = (
            lambda e: e.update(status="PARTIAL"),
            lambda e: e.update(record_count=False),
            lambda e: e.update(record_count=1),
            lambda e: e.pop("sha256"),
            lambda e: e.update(size=False),
            lambda e: e.pop("selected_ref"),
            lambda e: e["selected_ref"].update(sha256="b" * 64),
            lambda e: e["selected_ref"].update(size=3),
            lambda e: e["metadata"].pop("no_events"),
            lambda e: e["metadata"].update(snapshot_complete=False),
            lambda e: e["metadata"]["coverage"].update(snapshot_complete=False),
            lambda e: e["metadata"]["coverage"].update(collection_scope="historical"),
            lambda e: e["metadata"].update(history_unavailable=False),
            lambda e: e["metadata"]["coverage"].update(history_complete=True),
            lambda e: e["metadata"].update(acquisition_status="WAITING_QUOTA"),
            lambda e: e["metadata"].update(last_attempt={"status": "PARTIAL"}),
            lambda e: e["metadata"].update(pending=True),
            lambda e: e["metadata"].update(pending_dates=[DAY]),
            lambda e: e["metadata"]["coverage"].update(pending_release_dates=[DAY]),
            lambda e: e["metadata"].update(unresolved_prior_gaps=["unknown_gap"]),
            lambda e: e["metadata"].update(stale_snapshot=True),
            lambda e: e["metadata"].update(partial_recovery={"status": "pending"}),
            lambda e: e["metadata"].update(partial_recovery={"status": "resolved", "gaps": ["unresolved"]}),
            lambda e: e["metadata"].update(last_attempt={"status": "SUCCESS", "coverage": {"gaps": ["missing"]}}),
            lambda e: e["metadata"].update(last_attempt={"status": "SUCCESS", "coverage": {"pending_dates": [DAY]}}),
            lambda e: e["metadata"].update(gaps=["missing_source"]),
            lambda e: e["metadata"].update(diagnostics={"stale_sources": ["twse_twtawu"]}),
            lambda e: e["metadata"]["source_receipts"][0].update(http_status=500),
            lambda e: e["metadata"]["source_receipts"][0].update(row_count=False),
            lambda e: e["metadata"]["source_receipts"][0].update(source_sha256="bad"),
            lambda e: e["metadata"]["source_receipts"][0].update(observed_at=DAY + "T14:00:00"),
            lambda e: e["metadata"]["source_receipts"][0].update(not_yet_published=True),
            lambda e: e["metadata"]["source_receipts"][0].update(source_id="unknown_source"),
            lambda e: e["metadata"]["coverage"].update(total=11),
            lambda e: e["metadata"].update(observed_asof="2026-10-02"),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                entry = proved_entry()
                mutate(entry)
                self.assertIsNone(published_no_events(entry["route_id"], entry,
                    verified_empty=True, end_date=DAY))
        entry = proved_entry()
        self.assertIsNone(published_no_events(entry["route_id"], entry, verified_empty=False))
        self.assertIsNone(published_no_events("finmind_price_adj", entry, verified_empty=True))
        entry["metadata"]["partial_recovery"] = {"status": "resolved", "gaps": []}
        self.assertIsNotNone(published_no_events(entry["route_id"], entry, verified_empty=True))

    def test_scope_recovery_accepts_only_current_snapshot_without_history_claim(self):
        for route in ROUTES:
            for date_only in (False, True):
                for first in (DAY, "2021-10-03"):
                    with self.subTest(route=route, date_only=date_only, first=first):
                        entry = scope_recovered_entry(route, date_only=date_only,
                                                      requested_from_date=first)
                        proof = published_no_events(route, entry, verified_empty=True,
                                                    start_date=first, end_date=DAY)
                        self.assertEqual(proof["reason"], "no_events")
                        self.assertEqual(proof["snapshot_asof"], DAY)
                        self.assertEqual(proof["completion_scope"], "current_snapshot")
                        self.assertFalse(proof["history_complete"])
                        self.assertFalse(proof["model_usable"])
                        self.assertFalse(proof["usable"])

    def test_scope_recovery_reads_both_spellings_of_the_snapshot_day(self):
        # date directive 2026-10-06: YYYYMMDD and YYYY-MM-DD of the same day are the same day (by value)
        for route in ROUTES:
            for field in ("requested_from_date", "expected_asof"):
                with self.subTest(route=route, field=field):
                    entry = scope_recovered_entry(route)
                    entry["metadata"]["partial_recovery"]["requested_scope"][field] = "20261003"
                    proof = published_no_events(route, entry, verified_empty=True, end_date="20261003")
                    self.assertEqual(proof["reason"], "no_events")
                    self.assertEqual(proof["snapshot_asof"], DAY)

    def test_scope_recovery_missing_mismatched_and_unresolved_proof_is_rejected(self):
        mutations = (
            lambda r: r.pop("requested_scope"),
            lambda r: r.update(requested_scope=None),
            lambda r: r["requested_scope"].update(collection_scope="requested_range"),
            lambda r: r["requested_scope"].update(collection_scope="source_available"),
            lambda r: r["requested_scope"].pop("requested_from_date"),
            lambda r: r["requested_scope"].update(requested_from_date="2026-10-04"),
            lambda r: r["requested_scope"].update(requested_from_date="2026/10/03"),   # not a supported spelling
            lambda r: r["requested_scope"].update(requested_from_date=None),
            lambda r: r["requested_scope"].update(expected_asof="2026-10-02"),
            lambda r: r["requested_scope"].update(expected_asof=20261003),             # a JSON number is no date
            lambda r: r["requested_scope"].update(expected_asof=None),
            lambda r: r.pop("unresolved_prior_gaps"),
            lambda r: r.update(unresolved_prior_gaps=None),
            lambda r: r.update(unresolved_prior_gaps={}),
            lambda r: r.update(unresolved_prior_gaps=["missing_source"]),
            lambda r: r.update(unresolved_prior_reasons=["unknown_gap"]),
            lambda r: r.update(unresolved_prior_reasons=""),
            lambda r: r.update(prior_gaps=["unresolved"]),
            lambda r: r.update(prior_gaps={}),
            lambda r: r.update(gaps=["missing"]),
            lambda r: r.update(errors=["parse_failed"]),
            lambda r: r.update(pending_release_dates=[DAY]),
            lambda r: r.update(status="unresolved"),
        )
        for route in ROUTES:
            for index, mutate in enumerate(mutations):
                with self.subTest(route=route, mutation=index):
                    entry = scope_recovered_entry(route)
                    mutate(entry["metadata"]["partial_recovery"])
                    self.assertIsNone(published_no_events(route, entry,
                                                         verified_empty=True, end_date=DAY))

    def test_unresolved_prior_evidence_at_each_layer_is_rejected(self):
        for layer in ("entry", "metadata", "coverage"):
            for key in ("prior_gaps", "unresolved_prior_reasons"):
                for value in (["unresolved"], {}, None, ""):
                    with self.subTest(layer=layer, key=key, value=value):
                        entry = scope_recovered_entry()
                        target = (entry if layer == "entry" else entry["metadata"]
                                  if layer == "metadata" else entry["metadata"]["coverage"])
                        target[key] = value
                        self.assertIsNone(published_no_events(entry["route_id"], entry,
                                                             verified_empty=True, end_date=DAY))
                entry = scope_recovered_entry()
                target = (entry if layer == "entry" else entry["metadata"]
                          if layer == "metadata" else entry["metadata"]["coverage"])
                target[key] = []
                self.assertIsNotNone(published_no_events(entry["route_id"], entry,
                                                        verified_empty=True, end_date=DAY))

    def test_query_bounds_cannot_claim_other_date_or_history(self):
        entry = proved_entry()
        self.assertIsNotNone(published_no_events(entry["route_id"], entry, verified_empty=True))
        for query in (dict(end_date="2026-10-02"), dict(start_date="2026-10-04"),
                      dict(before_count=1), dict(formula_lookback_count=1), dict(end_date={})):
            with self.subTest(query=query):
                self.assertIsNone(published_no_events(entry["route_id"], entry,
                                                     verified_empty=True, **query))


class PublishedReaderNoEventsTests(FixtureMixin, unittest.TestCase):
    def test_verified_empty_snapshot_preserves_sources_and_empty_records(self):
        for route in ROUTES:
            with self.subTest(route=route):
                result = self.sources(proved_entry(route)).rows(route, stock_id="2330",
                    start_date="2021-10-03", end_date=DAY)
                self.assertEqual(result["status"], "available")
                self.assertEqual(result["reason"], "no_events")
                self.assertEqual(result["snapshot_asof"], DAY)
                self.assertEqual(result["records"], ())
                self.assertEqual(result["gaps"], ())
                self.assertEqual(len(result["sources"]), 1)

    def test_recovered_snapshot_survives_reader_projection_and_current_window(self):
        for route in ROUTES:
            with self.subTest(route=route):
                entry = scope_recovered_entry(route, requested_from_date="2021-10-03")
                sources = self.sources(entry)
                loaded = sources.rows(route, stock_id="2330",
                                      start_date="2021-10-03", end_date=DAY)
                selected = _select(sources, route, "2330", "2021-10-03", DAY)
                request = dict(start_date="2021-10-03", end_date=DAY, before_count=0,
                               field_map=dict(stock_field="stock_id", date_field="date"))
                window = _route_window(selected, request)
                for item in (loaded, selected, window):
                    self.assertEqual(item["status"], "available")
                    self.assertEqual(item["reason"], "no_events")
                    self.assertEqual(item["records"], ())
                    self.assertEqual(item["gaps"], ())
                    self.assertFalse(item["history_complete"])
                    self.assertFalse(item["model_usable"])
                    self.assertEqual(len(item["sources"]), 1)
                historical = _route_window(selected, dict(request, end_date="2026-10-02"))
                self.assertEqual(historical["status"], "missing")
                self.assertFalse(historical.get("no_events", False))
                unavailable = scope_recovered_entry(route)
                unavailable["metadata"]["partial_recovery"]["unresolved_prior_reasons"] = ["unknown"]
                self.assertEqual(self.sources(unavailable).rows(route, end_date=DAY)["status"], "missing")

    def test_nonempty_body_filter_miss_and_unproved_empty_are_missing(self):
        entry = proved_entry()
        result = self.sources(entry, [dict(stock_id="2317", date=DAY)]).rows(
            entry["route_id"], stock_id="2330", end_date=DAY)
        self.assertEqual(result["status"], "missing")
        entry["metadata"].pop("no_events")
        self.assertEqual(self.sources(entry).rows(entry["route_id"])["status"], "missing")
        entry = proved_entry()
        self.assertEqual(self.sources(entry, declare_identity=False).rows(
            entry["route_id"])["status"], "missing")
        entry.update(route_id="finmind_price_adj")
        self.assertEqual(self.sources(entry).rows(entry["route_id"])["status"], "missing")

    def test_tampered_payload_and_partial_never_become_no_events(self):
        entry = proved_entry()
        sources = self.sources(entry)
        root = sources._sources[0]["root"]
        (root / "payload.json").write_bytes(b"[ ]")
        self.assertEqual(sources.rows(entry["route_id"], end_date=DAY)["status"], "missing")
        entry["status"] = "PARTIAL"
        self.assertEqual(self.sources(entry).rows(entry["route_id"], end_date=DAY)["status"], "missing")


class PublishedProjectionNoEventsTests(FixtureMixin, unittest.TestCase):
    def test_select_uses_verified_global_empty_and_keeps_lineage(self):
        entry = proved_entry()
        result = _select(self.sources(entry), entry["route_id"], "2330", "2021-10-03", DAY)
        self.assertEqual(result["status"], "available")
        self.assertTrue(result["no_events"])
        self.assertFalse(result["usable"])
        self.assertEqual(result["gaps"], ())
        self.assertEqual(len(result["sources"]), 1)

    def test_route_window_preserves_snapshot_only_and_rejects_history(self):
        entry = proved_entry()
        item = _select(self.sources(entry), entry["route_id"], "2330", None, None)
        request = dict(start_date="2021-10-03", end_date=DAY, before_count=0,
                       field_map=dict(stock_field="stock_id", date_field="date"))
        result = _route_window(item, request)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["completion_scope"], "current_snapshot")
        self.assertFalse(result["history_complete"])
        for change in (dict(end_date="2026-10-02"), dict(before_count=1),
                       dict(formula_lookback_count=20), dict(unknown_constraint=True)):
            with self.subTest(change=change):
                result = _route_window(item, dict(request, **change))
                self.assertEqual(result["status"], "missing")
                self.assertFalse(result.get("no_events", False))

    def test_select_does_not_infer_no_events_from_filtered_rows(self):
        entry = proved_entry()
        result = _select(self.sources(entry, [dict(stock_id="2317", date=DAY)]),
                         entry["route_id"], "2330", None, DAY)
        self.assertEqual(result["status"], "missing")
        self.assertIn("no_matching_published_rows", result["gaps"])


def government_completion_entry(*, pending=False, status="SUCCESS"):
    day = DAY if pending else "2023-01-11"
    receipt = dict(schema="finmind-source-availability-v1", provider="finmind",
        route_id="finmind_government_bank", dataset="TaiwanStockGovernmentBankBuySell",
        kind="data_single_day", data_id=None, start=day, date=day, end=None,
        http_status=200, provider_status=200, provider_message="success",
        status="EMPTY", row_count=0, sha256="a" * 64, raw_sha256="a" * 64,
        source_url="https://finmind.github.io/tutor/TaiwanMarket/Chip/",
        request_complete=not pending, data_complete=False, history_complete=False,
        no_data_reason="not_yet_published" if pending else "provider_declared_unavailable")
    coverage = dict(total=1, success=0 if pending else 1,
        completion_scope="source_available", retrievable_history_complete=True,
        data_complete=False, history_complete=False)
    scope = dict(requested_from_date=day, expected_asof=day, source_receipts=[receipt],
                 coverage=coverage, gaps=[], errors=[])
    if pending:
        receipt.update(pending_release=True, no_data_confirmed=False,
            observed_at=day + "T20:00:00+08:00", publication_due_at=day + "T23:30:00+08:00")
        coverage.update(requested_scope=dict(from_date=day, to_date=day, dates=[day]),
            due_scope=dict(dates=[], total=0, success=0, complete=True),
            due_total=0, due_success=0, due_scope_complete=True,
            pending_release_dates=[day], pending_release_evidence=[copy.deepcopy(receipt)])
    else:
        receipt["rule_id"] = "finmind.government_bank.whole_day_unavailable.2023-01-11"
        coverage.update(source_unavailable_dates=[day],
                        source_availability_evidence=[copy.deepcopy(receipt)])
    metadata = copy.deepcopy(scope)
    metadata["last_attempt"] = dict(copy.deepcopy(scope), status="SUCCESS", warnings=[])
    empty_sha = hashlib.sha256(b"[]").hexdigest()
    return dict(route_id="finmind_government_bank", status=status, record_count=0,
        path="payload.json", sha256=empty_sha, size=2,
        selected_ref=dict(path="payload.json", sha256=empty_sha, size=2), metadata=metadata)


def official_no_data_entry():
    day = "2026-09-30"
    requests = (
        ("TWSE", "twse_daily_all", "GET",
         "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20260930&type=ALL&response=json", None),
        ("TPEx", "tpex_mainboard_daily", "GET",
         "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=2026%2F09%2F30&response=json&id=", None),
        ("Emerging", "tpex_emerging_daily", "POST",
         "https://www.tpex.org.tw/www/zh-tw/emerging/des010", b"date=2026%2F09%2F30&response=json"),
    )
    receipts = []
    for market, source, method, url, body in requests:
        stamp = "2026-10-01T00:00:00+00:00"
        evidence = dict(scope="single_market_day", market=market, date=day,
            source_id=source, stat="\u67e5\u7121\u8cc7\u6599", row_count=0,
            not_yet_published=False, observed_at=stamp, observed_taipei_date="2026-10-01",
            publication_availability="past_session_explicit_no_data", body_sha256="b" * 64,
            request_identity=dict(method=method, url=url,
                body_sha256=hashlib.sha256(body).hexdigest() if body is not None else None))
        receipts.append(dict(market=market, source_id=source, date=day, status="EMPTY",
            row_count=0, http_status=200, method=method, url=url, retrieved_at=stamp,
            no_data_confirmed=True, no_data_reason="explicit_official_no_data",
            not_yet_published=False, no_data_evidence=evidence))
    entry = government_completion_entry()
    entry["route_id"] = "official_market_daily"
    scope = dict(requested_from_date=day, expected_asof=day, source_receipts=receipts,
        coverage=dict(total=3, success=3, calendar_verified=True, calendar_gaps=[], units=[]),
        gaps=[], errors=[])
    entry["metadata"] = dict(scope, last_attempt=dict(copy.deepcopy(scope), status="SUCCESS"))
    return entry


class PublishedCompletionTests(FixtureMixin, unittest.TestCase):
    def chain(self, entry, *, rows=(), start=None, end=None, before_count=0):
        day = entry["metadata"].get("last_attempt", {}).get("expected_asof", DAY)
        start, end = start or day, end or day
        sources = self.sources(entry, rows)
        loaded = sources.rows(entry["route_id"], stock_id="2330", start_date=start, end_date=end)
        selected = _select(sources, entry["route_id"], "2330", start, end)
        request = dict(start_date=start, end_date=end, before_count=before_count,
                       field_map=dict(stock_field="stock_id", date_field="date"))
        window = _route_window(selected, request)
        return loaded, selected, window

    def test_confirmed_empty_semantics_survive_full_consumer_chain(self):
        for entry, state in ((government_completion_entry(), "source_unavailable"),
                             (official_no_data_entry(), "confirmed_no_data")):
            with self.subTest(state=state):
                for item in self.chain(entry):
                    self.assertEqual(item["status"], "available")
                    self.assertEqual(item["producer_status"], "SUCCESS")
                    self.assertEqual(item["completion_state"], state)
                    self.assertTrue(item["source_available_complete"])
                    self.assertFalse(item["data_complete"])
                    self.assertFalse(item["history_complete"])
                    self.assertFalse(item["usable"])
                    self.assertFalse(item["model_usable"])
                    self.assertEqual(item["last_attempt"]["status"], "SUCCESS")
                    self.assertEqual(item["last_attempt"]["expected_asof"],
                                     entry["metadata"]["last_attempt"]["expected_asof"])
                    self.assertNotIn("source_receipts", item["last_attempt"])
                    self.assertEqual(item["evidence_scope"]["receipt_count"],
                                     len(entry["metadata"]["last_attempt"]["source_receipts"]))
                    self.assertEqual(item["gaps"], () if "records" in item else [])
                    self.assertEqual(item["producer_gaps"], [])

    def test_warm_history_is_retained_but_exact_empty_scope_is_not_missing(self):
        entry = government_completion_entry()
        rows = [dict(stock_id="2330", date="2023-01-10", buy=7)]
        for item in self.chain(entry, rows=rows):
            self.assertEqual(item["completion_state"], "source_unavailable")
            self.assertEqual(item["status"], "available")
            self.assertFalse(item["usable"])
        sources = self.sources(entry, rows)
        selected = _select(sources, entry["route_id"], "2330", None, None)
        window = _route_window(selected, dict(start_date="2023-01-11", end_date="2023-01-11",
            before_count=0, field_map=dict(stock_field="stock_id", date_field="date")))
        self.assertEqual(window["completion_state"], "source_unavailable")
        self.assertEqual(len(selected["records"]), 1)

    def test_pending_is_not_permanent_empty_or_numeric_completion(self):
        for status in ("SUCCESS", "PARTIAL"):
            entry = government_completion_entry(pending=True, status=status)
            entry["metadata"]["prior_gaps"] = [dict(date="2023-01-11", kind="unknown_gap")]
            for item in self.chain(entry):
                self.assertNotEqual(item["status"], "available")
                self.assertEqual(item["producer_status"], status)
                self.assertEqual(item["completion_state"], "pending_release")
                self.assertFalse(item["source_available_complete"])
                self.assertFalse(item["requested_scope_complete"])
                self.assertTrue(item["due_scope_complete"])
                self.assertFalse(item["data_complete"])
                self.assertFalse(item["model_usable"])
                self.assertEqual(item["producer_gaps"], entry["metadata"]["prior_gaps"])
        entry = government_completion_entry(pending=True)
        for item in self.chain(entry, rows=[dict(stock_id="2330", date=DAY, buy=5)]):
            self.assertEqual(item["status"], "partial")
            self.assertEqual(item["completion_state"], "pending_release")
            self.assertFalse(item["model_usable"])

    def test_partial_producer_is_not_upgraded_by_positive_empty_scope(self):
        entry = government_completion_entry(status="PARTIAL")
        for item in self.chain(entry):
            self.assertEqual(item["producer_status"], "PARTIAL")
            self.assertEqual(item["status"], "partial")
            self.assertEqual(item["completion_state"], "source_unavailable")

    def test_wrong_proofs_scopes_and_stock_filter_misses_stay_unavailable(self):
        mutations = (
            # an attempt without receipts proves nothing, even when the metadata has receipts (cd_final A/F2: an
            # absent copy not listed in same_as_metadata means "this attempt had none", never the metadata's copy)
            lambda e: e["metadata"]["last_attempt"].pop("source_receipts"),
            lambda e: (e["metadata"]["last_attempt"].pop("source_receipts"), e["metadata"].pop("source_receipts")),
            # a listed same_as_metadata field that the metadata does not have is unresolved (fail closed)
            lambda e: (e["metadata"]["last_attempt"].pop("source_receipts"), e["metadata"].pop("source_receipts"),
                       e["metadata"]["last_attempt"].update(same_as_metadata=["source_receipts"])),
            lambda e: e["metadata"]["last_attempt"]["source_receipts"][0].update(raw_sha256="c" * 64),
            lambda e: e["metadata"]["last_attempt"]["source_receipts"][0].update(data_id="2330"),
            lambda e: e["metadata"]["last_attempt"]["source_receipts"][0].update(start=DAY, date=DAY),
            lambda e: e["metadata"]["last_attempt"]["coverage"].update(data_complete=True),
            lambda e: e["metadata"]["last_attempt"].update(errors=["parse_failed"]),
            lambda e: e.update(route_id="tdcc_share_distribution_raw"),
        )
        for mutate in mutations:
            entry = government_completion_entry()
            mutate(entry)
            for item in self.chain(entry):
                self.assertIn(item["status"], ("missing", "partial"))
                self.assertFalse(item["source_available_complete"])
        entry = government_completion_entry()
        for item in self.chain(entry, start="2023-01-10", end="2023-01-11"):
            self.assertNotEqual(item["status"], "available")
        for item in self.chain(entry, before_count=1)[2:]:
            self.assertNotEqual(item["status"], "available")
        for item in self.chain(entry, rows=[dict(stock_id="2317", date="2023-01-11", buy=4)]):
            self.assertEqual(item["status"], "missing")
            self.assertEqual(item["completion_state"], "unknown_empty")

    def test_pin_tampering_and_metadata_only_claims_are_rejected(self):
        entry = government_completion_entry()
        for mutate in (lambda e: e.pop("selected_ref"),
                       lambda e: e["selected_ref"].update(sha256="c" * 64),
                       lambda e: e.update(record_count=False)):
            damaged = copy.deepcopy(entry)
            mutate(damaged)
            proof = published_completion(entry["route_id"], damaged, verified_object=True,
                query_empty=True, global_empty=True, start_date="2023-01-11", end_date="2023-01-11")
            self.assertFalse(proof["source_available_complete"])
        proof = published_completion(entry["route_id"], entry, query_empty=True, global_empty=True)
        self.assertEqual(proof["completion_state"], "unknown_empty")
        sources = self.sources(entry)
        (sources._sources[0]["root"] / "payload.json").write_bytes(b"[ ]")
        self.assertEqual(sources.rows(entry["route_id"])["status"], "missing")

    def test_official_time_request_and_source_set_must_match(self):
        mutations = (
            lambda r: r.pop(),
            lambda r: r[0]["no_data_evidence"].update(observed_at="2026-09-30T12:00:00+08:00"),
            lambda r: r[0]["no_data_evidence"].update(observed_at="2026-10-01T00:00:00"),
            lambda r: r[0]["no_data_evidence"]["request_identity"].update(url="https://invalid.example/"),
            lambda r: r[0].update(no_data_confirmed=False),
            lambda r: r[0].update(source_id="unknown"),
        )
        for mutate in mutations:
            entry = official_no_data_entry()
            mutate(entry["metadata"]["last_attempt"]["source_receipts"])
            for item in self.chain(entry):
                self.assertNotEqual(item["status"], "available")

    def test_conflict_and_last_attempt_are_preserved_without_a_success_waiver(self):
        entry = government_completion_entry(status="PARTIAL")
        entry["route_id"] = "official_sector_observations"
        entry["metadata"] = dict(coverage=dict(total=9, success=9),
            gaps=[dict(kind="unit_conflict", date=DAY)],
            last_attempt=dict(status="SUCCESS", warnings=["source_timestamp_unknown_or_not_newer"]))
        for item in self.chain(entry, rows=[dict(stock_id="2330", date=DAY, value=2)]):
            self.assertEqual(item["status"], "partial")
            self.assertEqual(item["completion_state"], "conflict")
            self.assertEqual(item["producer_status"], "PARTIAL")
            # the attempt is reported as published: fields it does not carry are not filled from the metadata
            # (cd_final A/F2; only fields listed in last_attempt.same_as_metadata read the metadata)
            self.assertEqual(item["last_attempt"], entry["metadata"]["last_attempt"])
            self.assertFalse(item["source_available_complete"])

    def test_existing_no_events_fields_reach_dataset_status(self):
        entry = scope_recovered_entry()
        entry["metadata"]["last_attempt"] = dict(status="EMPTY", expected_asof=DAY)
        for item in self.chain(entry):
            self.assertEqual(item["completion_state"], "no_events")
            self.assertEqual(item["producer_status"], "SUCCESS")
            self.assertTrue(item["no_events"])
            self.assertFalse(item["model_usable"])
            self.assertTrue(item["requested_scope_complete"])
        for item in self.chain(entry, start="2021-10-03", end=DAY):
            self.assertEqual(item["completion_scope"], "current_snapshot")
            self.assertTrue(item["source_available_complete"])
            self.assertFalse(item["history_complete"])
            self.assertFalse(item["requested_scope_complete"])

    def test_nonempty_partial_keeps_scoped_reasons_and_bounded_provenance(self):
        for reason, state in (("duplicate_natural_key_conflict", "conflict"),
                              ("backfill_incomplete", "unknown"),
                              ("unknown_empty", "unknown")):
            with self.subTest(reason=reason):
                entry = government_completion_entry(status="PARTIAL")
                entry["route_id"] = "tdcc_share_distribution_raw"
                entry["metadata"] = dict(
                    gaps=[dict(kind=reason, date="2023-02-03", source_id="tdcc_weekly")],
                    source_states={str(i): {"giant": "x" * 10000} for i in range(100)},
                    partition_fingerprints={str(i): "b" * 64 for i in range(100)},
                    last_attempt=dict(status="SUCCESS", requested_from_date="2023-02-03",
                        expected_asof=DAY, warnings=[reason],
                        source_receipts=[{"body": "x" * 10000} for _ in range(100)]))
                for item in self.chain(entry, rows=[dict(stock_id="2330", date=DAY, value=2)]):
                    self.assertEqual(item["status"], "partial")
                    self.assertEqual(item["producer_status"], "PARTIAL")
                    self.assertEqual(item["completion_state"], state)
                    self.assertFalse(item["source_available_complete"])
                    self.assertFalse(item["model_usable"])
                    self.assertEqual(item["producer_gaps"], entry["metadata"]["gaps"])
                    self.assertEqual(item["last_attempt"]["warnings"], [reason])
                    self.assertNotIn("source_receipts", item["last_attempt"])
                    self.assertNotIn("source_states", item["completion_reasons"]["published"])
                    self.assertNotIn("partition_fingerprints", item["completion_reasons"]["published"])
                    self.assertLess(len(json.dumps({key: item.get(key) for key in (
                        "last_attempt", "producer_gaps", "completion_reasons", "evidence_scope")})), 7000)

    def test_pending_bad_clock_scope_and_receipt_are_not_completed(self):
        mutations = (
            lambda e: e["metadata"]["last_attempt"]["source_receipts"][0].update(
                observed_at=DAY + "T23:31:00+08:00"),
            lambda e: e["metadata"]["last_attempt"]["source_receipts"][0].update(
                observed_at=DAY + "T20:00:00"),
            lambda e: e["metadata"]["last_attempt"]["coverage"]["requested_scope"].update(
                to_date="2026-10-04"),
            lambda e: e["metadata"]["last_attempt"]["coverage"].update(due_total=True),
            lambda e: e["metadata"].update(last_attempt=None),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                entry = government_completion_entry(pending=True)
                mutate(entry)
                proof = published_completion(entry["route_id"], entry, verified_object=True,
                    query_empty=True, global_empty=True, start_date=DAY, end_date=DAY)
                self.assertEqual(proof["completion_state"], "unknown_empty")
                self.assertFalse(proof["source_available_complete"])
                self.assertFalse(proof["due_scope_complete"])
                self.assertFalse(proof["requested_scope_complete"])

    def test_conflicting_rows_do_not_become_numeric_complete(self):
        entry = government_completion_entry()
        entry["route_id"] = "tdcc_share_distribution_raw"
        rows = [dict(stock_id="2330", date=DAY, level=1, value=2),
                dict(stock_id="2330", date=DAY, level=1, value=3)]
        result = self.sources(entry, rows).rows(entry["route_id"], stock_id="2330",
            start_date=DAY, end_date=DAY, key_fields=("stock_id", "date", "level"))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["reason"], "duplicate_natural_key_conflict")
        self.assertEqual(result["completion_state"], "conflict")
        self.assertFalse(result["data_complete"])
        self.assertFalse(result["model_usable"])
        self.assertEqual(len(result["records"]), 1)

    def test_mixed_sources_are_not_evidence_of_a_conflict(self):
        empty = published_completion("tdcc_share_distribution_raw",
            dict(route_id="tdcc_share_distribution_raw", status="SUCCESS"), query_empty=True)
        populated = published_completion("tdcc_share_distribution_raw",
            dict(route_id="tdcc_share_distribution_raw", status="SUCCESS"))
        empty["source_id"], populated["source_id"] = "empty_source", "populated_source"
        result = published_completion_fields([empty, populated])
        self.assertEqual(result["completion_state"], "mixed")
        self.assertEqual(result["reason"], "mixed")
        self.assertFalse(result["source_available_complete"])
        self.assertFalse(result["requested_scope_complete"])
        self.assertFalse(result["model_usable"])
        self.assertEqual([item["completion_state"] for item in result["source_completions"]],
                         ["unknown_empty", "unknown"])
        self.assertEqual([item["source_id"] for item in result["source_completions"]],
                         ["empty_source", "populated_source"])
        conflicted = dict(populated, completion_state="conflict", reason="unit_conflict")
        self.assertEqual(published_completion_fields([empty, conflicted])["completion_state"], "conflict")

    def test_non_scope_routes_do_not_parse_a_date_for_every_row(self):
        for route in ("finmind_trading_daily_report", "tdcc_share_distribution_raw",
                      "official_trading_restrictions"):
            with self.subTest(route=route):
                entry = proved_entry()
                entry["route_id"] = route
                rows = [dict(stock_id="2330", date=DAY, value=i) for i in range(10)]
                if route == "finmind_trading_daily_report":
                    # Order21 rows need their broker/price identity (order21_row_format fails closed without it).
                    for index, row in enumerate(rows):
                        row.update(securities_trader_id=f"B{index:03d}", price=100 + index)
                sources = self.sources(entry, rows)
                with patch("stock_data_sources._completion_day", return_value=DAY) as parse_day:
                    result = sources.rows(route, stock_id="2330", start_date=DAY, end_date=DAY)
                self.assertLessEqual(parse_day.call_count, 2)
                self.assertEqual(len(result["records"]), 10)
                self.assertEqual(result["status"], "available")
                self.assertFalse(result.get("no_events", False))
                missed = sources.rows(route, stock_id="2317", end_date=DAY)
                self.assertEqual(missed["status"], "missing")
                self.assertFalse(missed.get("no_events", False))

    def test_positive_legacy_statuses_do_not_authorize_empty_proof(self):
        for status in ("COMPLETE", "committed", "available", "success"):
            with self.subTest(status=status):
                entry = proved_entry()
                entry["status"] = status
                for item in self.chain(entry, rows=[dict(stock_id="2330", date=DAY, value=2)]):
                    self.assertEqual(item["status"], "available")
                    self.assertEqual(item["producer_status"], status)
                    self.assertFalse(item["source_available_complete"])
                    self.assertFalse(item.get("no_events", False))
                for item in self.chain(entry):
                    self.assertEqual(item["status"], "missing")
                    self.assertFalse(item["source_available_complete"])
                    self.assertFalse(item.get("no_events", False))

    def test_conflict_mentions_are_not_unresolved_conflict_codes(self):
        mentions = ["no_conflicts_found", "resolved_conflict", "conflict_resolved", "no_conflict",
                    "unknown_conflict_explanation"]
        for producer in ("SUCCESS", "PARTIAL"):
            entry = government_completion_entry(status=producer)
            entry["route_id"] = "official_sector_observations"
            entry["metadata"] = dict(last_attempt=dict(status="SUCCESS", warnings=mentions))
            for item in self.chain(entry, rows=[dict(stock_id="2330", date=DAY, value=2)]):
                self.assertEqual(item["completion_state"], "unknown")
                self.assertEqual(item["producer_status"], producer)
                self.assertEqual(item["status"], "available" if producer == "SUCCESS" else "partial")
                self.assertEqual(item["last_attempt"]["warnings"], mentions)
        for code in ("duplicate_natural_key_conflict", "source_timestamp_unknown_or_not_newer",
                     "order21_conflict_unit_quarantined"):
            proof = published_completion("official_sector_observations", dict(status="PARTIAL",
                metadata=dict(last_attempt=dict(status="SUCCESS", warnings=[code]))))
            self.assertEqual(proof["completion_state"], "conflict")
