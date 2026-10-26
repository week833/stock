"""Shared readers accept both date spellings (YYYYMMDD / YYYY-MM-DD) and compare by value (2026-10-06 directive).

Every reader returns the canonical machine spelling YYYY-MM-DD (cache/identity keys and the argv/env hand-off to
the central updater stay YYYY-MM-DD); a JSON number is never a date; published payloads are never rewritten.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

SHARED_DIR = Path(__file__).resolve().parents[1]
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

import central_publication_reader as reader  # noqa: E402
import finmind_producer  # noqa: E402
import global_market_data as gmd  # noqa: E402
import market_price_adj_store as price_store  # noqa: E402
import market_update_client as client  # noqa: E402
import tw_market_calendar as calendar  # noqa: E402

UTC = timezone.utc


def _load_shared_projection():
    # single_stock_analysis/src has its own, larger central_single_stock_projection.py that shadows this shared
    # copy on the program's sys.path; load the shared file itself under a distinct name.
    import importlib.util

    path = SHARED_DIR / "central_single_stock_projection.py"
    spec = importlib.util.spec_from_file_location("shared_central_single_stock_projection_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


projection = _load_shared_projection()


# ------------------------------------------------------------------------------------------------------------------
# central_publication_reader
# ------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["2026-10-02", "20261002", " 2026-10-02 ", "20261002\n", date(2026, 10, 2)])
def test_publication_dates_accept_both_spellings_and_return_iso(value):
    assert reader._validate_date(value, "expected_asof", "ds", Path("x.json")) == "2026-10-02"


@pytest.mark.parametrize("value", [20261002, 2026.1002, True, None, "", "2026/10/02", "2026-1-2", "2026W401",
                                   "20260230", "2026-10-02T00:00:00", datetime(2026, 10, 2), "1151002"])
def test_publication_dates_refuse_numbers_and_other_formats(value):
    with pytest.raises(reader.CentralPublicationError, match="expected_asof_invalid"):
        reader._validate_date(value, "expected_asof", "ds", Path("x.json"))


def test_two_spellings_of_one_day_are_equal_after_validation():
    path = Path("x.json")
    assert reader._validate_date("20261002", "a", "ds", path) == reader._validate_date("2026-10-02", "b", "ds", path)


def test_environment_handoff_is_canonical_and_invalid_text_still_fails_closed(monkeypatch):
    monkeypatch.setenv(reader.EXPECTED_ASOF_ENV, "20261002")
    assert reader.central_expected_asof() == "2026-10-02"
    monkeypatch.setenv(reader.EXPECTED_ASOF_ENV, " 2026-10-02 ")
    assert reader.central_expected_asof() == "2026-10-02"
    monkeypatch.setenv(reader.EXPECTED_ASOF_ENV, "2026/10/02")
    # not silently dropped (the caller would then fall back to the receipt date): it reaches _validate_date and fails
    assert reader.central_expected_asof() == "2026/10/02"
    with pytest.raises(reader.CentralPublicationError):
        reader._validate_date(reader.central_expected_asof(), "expected_asof", "ds", Path("x.json"))
    monkeypatch.setenv(reader.EXPECTED_ASOF_ENV, "")
    assert reader.central_expected_asof() is None


@pytest.mark.parametrize("value,want", [
    ("2026-08-14", date(2026, 8, 14)), ("20260814", date(2026, 8, 14)),
    ("2026-08-14T23:59:59+08:00", date(2026, 8, 14)), (" 2026-08-14 ", date(2026, 8, 14)),
    (20260814, None), (None, None), ("", None), ("2026/08/14", None), ("20260814T235959", None),
])
def test_finance_deadline_fields_are_calendar_days_of_either_spelling(value, want):
    assert reader._calendar_day_field(value) == want


def test_reader_no_longer_carries_an_iso_only_date_regex():
    source = (SHARED_DIR / "central_publication_reader.py").read_text(encoding="utf-8")
    assert r'_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")' not in source
    assert 're.search(r"(\\d{4})-(\\d{2})-(\\d{2})"' not in source
    assert "date.fromisoformat(" not in source


# ------------------------------------------------------------------------------------------------------------------
# central_single_stock_projection (shared)
# ------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value,want", [
    ("2026-10-02", "2026-10-02"), ("20261002", "2026-10-02"), (date(2026, 10, 2), "2026-10-02"),
    (datetime(2026, 10, 2, 9, 0), "2026-10-02"), ("2026-10-02T15:30:00+08:00", "2026-10-02"),
    (None, None), ("", None), ("  ", None),
])
def test_projection_dates_are_canonical(value, want):
    assert projection._date_text(value, label="row_date") == want


@pytest.mark.parametrize("value", [20261002, True, 1.5, "2026/10/02", "2026-10-02xyz", "20261002T153000", "junk"])
def test_projection_dates_refuse_numbers_and_other_formats(value):
    with pytest.raises(reader.CentralPublicationError, match="projection_row_date_invalid"):
        projection._date_text(value, label="row_date")


def test_projection_rows_of_either_spelling_are_windowed_by_value_and_never_rewritten():
    contract = projection.CentralSingleStockDatasetContract(
        dataset="TaiwanStockPrice", central_file="finmind_TaiwanStockPrice.json", filter_keys=("stock_id",),
        date_keys=("date",))
    rows =({"stock_id": "2330", "date": "20261001"}, {"stock_id": "2330", "date": "2026-10-02"},
            {"stock_id": "2330", "date": "20261005"})
    dataset = projection.CentralProjectedDataset(
        contract=contract, stock_id="2330", expected_asof="2026-10-05", requested_start_date=None,
        requested_end_date="2026-10-05", rows=rows, publication={}, publication_object=None, status="available")
    assert dataset._row_dates() == ["2026-10-01", "2026-10-02", "2026-10-05"]
    assert [row["date"] for row in dataset.payload()["data"]] == ["20261001", "2026-10-02", "20261005"]


# ------------------------------------------------------------------------------------------------------------------
# tw_market_calendar
# ------------------------------------------------------------------------------------------------------------------
class _FakeSession:
    def __init__(self):
        self.expected_asof = "2026-10-02"
        self.receipt_path = Path("receipt.json")
        self.route_contracts = {"official_calendar": {
            "target_relative_path": "official/calendar.json", "schema": "official-calendar-contract-v1",
            "provider": "twse_tpex_official"}}

    def artifact_descriptors(self, route_id):
        assert route_id == "official_calendar"
        return [SimpleNamespace(role="payload", target_relative_path="official/calendar.json")]


def _calendar_payload(rows, *, expected_asof="2026-10-02", next_session="2026-10-05", next_trading_date=None):
    return {
        "dataset_id": "official_calendar", "schema": "official-calendar-contract-v1", "schema_version": 1,
        "provider": "twse_tpex_official", "expected_asof": expected_asof,
        "data": {"calendar": rows, "next_session": next_session,
                 "next_trading_date": next_session if next_trading_date is None else next_trading_date},
    }


def _load_calendar(monkeypatch, payload):
    session = _FakeSession()
    monkeypatch.setattr(calendar, "CentralEvidenceSession", _FakeSession)
    raw = json.dumps(payload).encode("utf-8")
    artifact = SimpleNamespace(
        payload=payload, expected_asof="2026-10-02", path=Path("official/calendar.json"),
        receipt_path=Path("receipt.json"), target_relative_path="official/calendar.json", raw=raw,
        content_sha256="c" * 64, receipt_sha256="r" * 64, artifact_map_sha256="m" * 64)
    monkeypatch.setattr(calendar, "read_central_artifact", lambda *args, **kwargs: artifact)
    return calendar.load_market_calendar(2026, session=session)


def _rows(*items):
    return [{"date": day, "status": status, "is_open": status == "open"} for day, status in items]


def test_calendar_accepts_both_spellings_and_returns_canonical_lookup_keys(monkeypatch):
    rows = _rows(("20261001", "open"), ("2026-10-02", "open"), ("20261003", "closed"), ("2026-10-04", "closed"),
                 ("20261005", "open"))
    payload = _calendar_payload(rows, expected_asof="20261002", next_session="20261005",
                                next_trading_date="2026-10-05")
    before = json.dumps(payload, sort_keys=True)
    result = _load_calendar(monkeypatch, payload)
    assert result["open_dates"] == ["2026-10-01", "2026-10-02", "2026-10-05"]
    assert result["closed_dates"] == ["2026-10-03", "2026-10-04"]
    assert [row["date"] for row in result["calendar"]] == ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04",
                                                           "2026-10-05"]
    assert result["next_session"] == result["next_trading_date"] == "2026-10-05"
    assert json.dumps(payload, sort_keys=True) == before  # the published payload is not modified
    assert calendar._is_trading_day_from_calendar(date(2026, 10, 2), result) is True
    assert calendar._is_trading_day_from_calendar(date(2026, 10, 3), result) is False


def test_calendar_iso_only_payload_gives_the_same_result_as_the_compact_one(monkeypatch):
    iso = _calendar_payload(_rows(("2026-10-01", "open"), ("2026-10-02", "open"), ("2026-10-05", "open")))
    compact = _calendar_payload(_rows(("20261001", "open"), ("20261002", "open"), ("20261005", "open")),
                                expected_asof="20261002", next_session="20261005")
    first, second = _load_calendar(monkeypatch, iso), _load_calendar(monkeypatch, compact)
    for key in ("open_dates", "closed_dates", "calendar", "next_session", "next_trading_date", "expected_asof"):
        assert first[key] == second[key], key


def test_calendar_duplicate_day_in_two_spellings_is_a_duplicate(monkeypatch):
    payload = _calendar_payload(_rows(("20261002", "open"), ("2026-10-02", "open")))
    with pytest.raises(reader.CentralPublicationError, match="calendar_row_duplicate"):
        _load_calendar(monkeypatch, payload)


@pytest.mark.parametrize("bad", [20261002, "2026/10/02", "2026-10-2", "", None, "2026-02-30"])
def test_calendar_row_dates_that_are_not_dates_fail_closed(monkeypatch, bad):
    payload = _calendar_payload([{"date": bad, "status": "open", "is_open": True}])
    with pytest.raises(reader.CentralPublicationError, match="calendar_row_date_invalid"):
        _load_calendar(monkeypatch, payload)


def test_calendar_payload_asof_must_be_the_same_day(monkeypatch):
    rows = _rows(("2026-10-02", "open"))
    with pytest.raises(reader.CentralPublicationError, match="calendar_expected_asof_invalid"):
        _load_calendar(monkeypatch, _calendar_payload(rows, expected_asof=20261002))
    with pytest.raises(reader.CentralPublicationError, match="calendar_expected_asof_invalid"):
        _load_calendar(monkeypatch, _calendar_payload(rows, expected_asof="20261001"))


def test_calendar_next_session_is_compared_by_value_not_by_string(monkeypatch):
    # "20261001" > "2026-10-02" as strings ('1' > '-'); by value it is BEFORE the as-of day and must be refused.
    assert "20261001" > "2026-10-02"
    rows = _rows(("2026-10-01", "open"), ("2026-10-02", "open"))
    with pytest.raises(reader.CentralPublicationError, match="calendar_next_session_contract_invalid"):
        _load_calendar(monkeypatch, _calendar_payload(rows, next_session="20261001"))
    with pytest.raises(reader.CentralPublicationError, match="calendar_next_session_invalid"):
        _load_calendar(monkeypatch, _calendar_payload(rows, next_session=20261005))


@pytest.mark.parametrize("value", ["20261002", "2026-10-02", " 2026-10-02 ", date(2026, 10, 2),
                                   datetime(2026, 10, 2, 13, 0), "2026-10-02T13:00:00+08:00"])
def test_calendar_caller_dates_accept_both_spellings(value):
    assert calendar._date_value(value) == date(2026, 10, 2)


@pytest.mark.parametrize("value", [20261002, "2026/10/02", "2026-1-2", "junk", None])
def test_calendar_caller_dates_refuse_numbers_and_other_formats(value):
    with pytest.raises(ValueError):
        calendar._date_value(value)


def test_parse_schedule_accepts_both_spellings():
    payload = {"dataset_id": "official_calendar", "schema": "official-calendar-contract-v1", "schema_version": 1,
               "provider": "twse_tpex_official",
               "data": {"calendar": _rows(("20261001", "open"), ("2026-10-03", "closed"), ("20251231", "open"))}}
    result = calendar.parse_schedule(payload, 2026)
    assert result["open_dates"] == ["2026-10-01"] and result["closed_dates"] == ["2026-10-03"]
    payload["data"]["calendar"] = [{"date": 20261001, "status": "open", "is_open": True}]
    with pytest.raises(reader.CentralPublicationError, match="calendar_row_contract_invalid"):
        calendar.parse_schedule(payload, 2026)


# ------------------------------------------------------------------------------------------------------------------
# market_update_client
# ------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["20261002", "2026-10-02"])
def test_market_update_expected_asof_accepts_both_spellings(value):
    assert client._valid_expected_asof(value) == "2026-10-02"


@pytest.mark.parametrize("value", [" 20261002", "2026-10-02 ", 20261002, None, "", "2026/10/02", "2026W401",
                                   "20260230"])
def test_market_update_expected_asof_refuses_other_values(value):
    with pytest.raises(client.MarketUpdateError, match="missing or invalid"):
        client._valid_expected_asof(value)


def test_explicit_and_handoff_dates_in_two_spellings_do_not_conflict(monkeypatch):
    monkeypatch.setenv(client.EXPECTED_ASOF_ENV, "2026-10-02")
    assert client._requested_expected_asof("20261002") == "2026-10-02"
    monkeypatch.setenv(client.EXPECTED_ASOF_ENV, "20261002")
    assert client._requested_expected_asof(None) == "2026-10-02"
    with pytest.raises(client.MarketUpdateError, match="conflicts"):
        client._requested_expected_asof("20261005")


def test_central_updater_still_receives_iso_and_handoff_is_iso(monkeypatch, tmp_path):
    receipt_path = tmp_path / "latest_manifest.json"
    receipt_path.write_text(json.dumps({"expected_asof": "20260820"}), encoding="utf-8")
    monkeypatch.setattr(client, "CENTRAL_LATEST_MANIFEST", receipt_path)
    monkeypatch.setattr(client, "CENTRAL_MARKET_UPDATE_CMD", Path(__file__))
    seen = {}

    def validate(receipt, verify_payload, expected_asof):
        seen["asof"] = expected_asof
        return {"central_receipt_run_id": "run-1", "receipt_path": str(receipt_path)}

    monkeypatch.setattr(client, "_validate_central_receipt", validate)
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
            "manifest_schema": "shared-market-data-update-manifest-v1", "operation": "verify",
            "status": "complete", "expected_asof": "20260820"}))

    monkeypatch.delenv(client.EXPECTED_ASOF_ENV, raising=False)
    monkeypatch.delenv(client.CENTRAL_READY_ENV, raising=False)
    result = client.ensure_market_update("20260820", runner=runner)
    assert calls[0][-2:] == ["--expected-asof", "2026-08-20"]  # pinned updater CLI: YYYY-MM-DD only (D5)
    assert result["expected_asof"] == "2026-08-20" and seen["asof"] == "2026-08-20"
    import os

    assert os.environ[client.EXPECTED_ASOF_ENV] == "2026-08-20"  # machine hand-off stays YYYY-MM-DD


def test_receipt_and_verify_dates_compare_by_value():
    manifest = {"manifest_schema": "shared-market-data-update-manifest-v1", "status": "complete",
                "expected_asof": "20261002",
                "datasets": [{"dataset_id": client._OFFICIAL_MARKET_DAILY_DATASET, "status": "complete",
                              "expected_asof": "2026-10-02"}]}
    snapshot = SimpleNamespace(payload=manifest, file_identity={})
    original_read, original_assert = client._read_central_receipt_snapshot, client._assert_receipt_identity_unchanged
    try:
        client._read_central_receipt_snapshot = lambda: snapshot
        client._assert_receipt_identity_unchanged = lambda *args, **kwargs: None
        assert client._central_manifest_confirms_session_close(date(2026, 10, 2)) is True
        assert client._central_manifest_confirms_session_close(date(2026, 10, 1)) is False
        manifest["expected_asof"] = 20261002  # a JSON number is not a date
        assert client._central_manifest_confirms_session_close(date(2026, 10, 2)) is False
    finally:
        client._read_central_receipt_snapshot = original_read
        client._assert_receipt_identity_unchanged = original_assert


def test_validate_central_receipt_accepts_a_compact_verify_date_for_the_same_day():
    with pytest.raises(client.MarketUpdateError) as excinfo:
        client._validate_central_receipt({}, {
            "manifest_schema": "shared-market-data-update-manifest-v1", "operation": "verify",
            "status": "complete", "expected_asof": "20261002", "network": False, "writes": False,
            "control_state": {"ok": False}}, "2026-10-02")
    # the date matched (by value); the first failure is the next check, and the message shows YYYYMMDD
    assert "central_control_state_not_ready" in str(excinfo.value)
    assert "failed closed for 20261002" in str(excinfo.value)
    assert excinfo.value.expected_asof == "2026-10-02"
    with pytest.raises(client.MarketUpdateError, match="verify_expected_asof_mismatch"):
        client._validate_central_receipt({}, {
            "manifest_schema": "shared-market-data-update-manifest-v1", "operation": "verify",
            "status": "complete", "expected_asof": 20261002}, "2026-10-02")


def test_market_update_cli_help_names_the_compact_spelling(capsys):
    with pytest.raises(SystemExit):
        client.main(["ensure", "--help"])
    assert "YYYYMMDD（相容 YYYY-MM-DD）" in capsys.readouterr().out


# ------------------------------------------------------------------------------------------------------------------
# global_market_data
# ------------------------------------------------------------------------------------------------------------------
def test_compact_decision_date_is_the_taipei_1530_decision_not_midnight():
    # Before: "20261002" (8 chars) went to datetime.fromisoformat -> 00:00 Taipei, so the US close of 10/01
    # (available ~04:30 Taipei on 10/02) was treated as not yet available.
    want = datetime(2026, 10, 2, 7, 30, tzinfo=UTC)
    assert gmd._decision_datetime("20261002") == want
    assert gmd._decision_datetime("2026-10-02") == want
    assert gmd._decision_datetime(" 20261002 ") == want
    assert gmd._decision_datetime(date(2026, 10, 2)) == want
    assert gmd._decision_datetime(np.datetime64("2026-10-02")) == want  # unchanged route for numpy days


@pytest.mark.parametrize("value,want", [
    ("2026-10-02T08:30:00+08:00", datetime(2026, 10, 2, 0, 30, tzinfo=UTC)),
    ("2026-10-02T00:30:00Z", datetime(2026, 10, 2, 0, 30, tzinfo=UTC)),
    ("2026-10-02 08:30:00", datetime(2026, 10, 2, 0, 30, tzinfo=UTC)),  # naive -> Taipei
    (datetime(2026, 10, 2, 8, 30, tzinfo=timezone(timedelta(hours=8))), datetime(2026, 10, 2, 0, 30, tzinfo=UTC)),
    (np.datetime64("2026-10-02T08:30:00.000000000"), datetime(2026, 10, 2, 0, 30, tzinfo=UTC)),
])
def test_decision_timestamps_are_used_as_given(value, want):
    assert gmd._decision_datetime(value) == want


@pytest.mark.parametrize("value", [20261002, 1.5, True, "2026/10/02", "2026W401", "20261002T153000", "junk", ""])
def test_decision_values_that_are_not_dates_or_iso_timestamps_fail(value):
    with pytest.raises(gmd.GlobalMarketDataError):
        gmd._decision_datetime(value)


def _gm_row(day, close, available="2026-10-01T21:00:00+00:00"):
    return {"date": day, "close": close, "available_at_utc": available}


def test_history_rows_of_either_spelling_become_canonical_session_dates():
    payload = {"instruments": {"us_sp500": {"rows": [
        _gm_row("20260929", 100.0, "2026-09-29T21:00:00+00:00"),
        _gm_row("2026-09-30", 101.0, "2026-09-30T21:00:00+00:00"),
        _gm_row("20261001", 102.0, "2026-10-01T21:00:00+00:00"),
        _gm_row(20261002, 103.0, "2026-10-02T21:00:00+00:00"),  # JSON number: skipped, never a date
    ]}}}
    prepared = gmd._prepare_history(payload)["us_sp500"]
    assert prepared.session_dates == ("2026-09-29", "2026-09-30", "2026-10-01")
    metrics = gmd._series_metrics(prepared, gmd._decision_datetime("20261002"))
    assert metrics["latest_session_date"] == "2026-10-01"


def _valid_payload(rows):
    instruments = {}
    for spec in gmd.INSTRUMENTS:
        instruments[spec.key] = {"ticker": spec.ticker, "rows": [_gm_row("2026-10-01", 1.0)]}
    instruments["us_sp500"]["rows"] = rows
    return {"schema_version": gmd.SCHEMA_VERSION, "instruments": instruments}


def test_payload_validation_orders_and_dedups_by_value():
    ok = _valid_payload([_gm_row("20260930", 1.0), _gm_row("2026-10-01", 1.0), _gm_row("20261002", 1.0)])
    assert gmd.validate_global_market_payload(ok) == []
    # "20261001" then "2026-10-02": increasing by value, although '1' > '-' as strings
    mixed = _valid_payload([_gm_row("20261001", 1.0), _gm_row("2026-10-02", 1.0)])
    assert gmd.validate_global_market_payload(mixed) == []
    duplicate = _valid_payload([_gm_row("20261001", 1.0), _gm_row("2026-10-01", 1.0)])
    assert gmd.validate_global_market_payload(duplicate) == ["us_sp500: 日期未嚴格遞增或重複"]
    number = _valid_payload([_gm_row(20261001, 1.0)])
    assert gmd.validate_global_market_payload(number) == ["us_sp500: 第 1 筆日期無效"]
    bad_close = _valid_payload([_gm_row("2026-10-01", -1.0)])
    assert gmd.validate_global_market_payload(bad_close) == ["us_sp500: close 無效 (20261001)"]


# ------------------------------------------------------------------------------------------------------------------
# market_price_adj_store (arguments only) and finmind_producer
# ------------------------------------------------------------------------------------------------------------------
def test_price_adj_argument_dates_accept_both_spellings_but_persisted_fields_stay_iso():
    assert price_store._argument_date("20261002") == price_store._argument_date("2026-10-02") == "2026-10-02"
    assert price_store._argument_date(20261002) is None and price_store._argument_date("2026/10/02") is None
    assert price_store.CommittedMarketPriceAdjStore._validate_filter_date("20261002") == "2026-10-02"
    with pytest.raises(price_store.MarketPriceAdjStoreError):
        price_store.CommittedMarketPriceAdjStore._validate_filter_date("2026/10/02")
    # content-addressed persisted values: exact YYYY-MM-DD only (a compact row is different canonical bytes)
    assert price_store._valid_date("2026-10-02") == "2026-10-02"
    assert price_store._valid_date("20261002") is None


def test_finmind_producer_date_key_never_takes_a_json_number():
    assert finmind_producer._date_key("2026-10-02") == "2026-10-02"
    assert finmind_producer._date_key("20261002") == "2026-10-02"
    assert finmind_producer._date_key("2026-10-02T00:00:00") == "2026-10-02"
    for value in (20261002, 20261002.0, True):
        assert finmind_producer._date_key(value) is None
