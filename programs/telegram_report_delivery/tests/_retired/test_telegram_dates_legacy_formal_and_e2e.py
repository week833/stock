# RETIRED TEST MODULE -- not collected (tests/conftest.py collect_ignore = ["_retired"]).
# Moved verbatim from tests/test_telegram_dates.py on 2026-10-12; see tests/_retired/README.md
# for the removed module/feature each test exercised and the evidence that it was intentionally removed.
"""Telegram delivery and the date directive (2026-10-06): dates shown in Telegram are YYYYMMDD; every date the
bot / senders read accepts YYYYMMDD and YYYY-MM-DD (compared by value); JSON numbers are never dates; machine
records (pin ``priceDate``, env hand-offs, delivery evidence, state fingerprints) keep YYYY-MM-DD.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

SUITE_DIR = Path(__file__).resolve().parents[1]
if str(SUITE_DIR) not in sys.path:
    sys.path.insert(0, str(SUITE_DIR))

import mix_report_sender as mix  # noqa: E402
import telegram_command_bot_e2e as e2e  # noqa: E402
import telegram_report_sender as sender  # noqa: E402
import telegram_stock_command_bot as bot  # noqa: E402

STOCK = bot.StockMatch("2330", "台積電", "exact_id", 1.0)


# ------------------------------------------------------------------------------------------------------------------
# command bot: readers
# ------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value,want", [
    ("20261002", "20261002"), ("2026-10-02", "20261002"), (" 2026-10-02 ", "20261002"), (date(2026, 10, 2), "20261002"),
    (20261002, ""), (None, ""), ("", ""), ("20261340", ""), ("2026/10/02", ""), ("2026-10-02T00:00:00", ""),
])
def test_report_date_keys_accept_both_spellings_and_never_a_json_number(value, want):
    assert bot._date_key(value) == want


@pytest.mark.parametrize("value", ["20261002", "2026-10-02", date(2026, 10, 2)])
def test_data_dates_are_canonical_iso(value):
    assert bot._valid_data_date(value) == "2026-10-02"


@pytest.mark.parametrize("value", [20261002, True, None, "", "2026/10/02", "20261340", datetime(2026, 10, 2),
                                   " 2026-10-02"])
def test_data_dates_refuse_other_values(value):
    with pytest.raises(bot.AnalysisProcessError):
        bot._valid_data_date(value)


def test_compact_head_date_is_pinned_as_iso(tmp_path, monkeypatch):
    head = {"schema": "stock-data-publication-v1", "datasets": [
        {"route_id": "finmind_price_adj", "actual_max_date": "20261005"}]}
    raw = json.dumps(head).encode("utf-8")
    manifest = tmp_path / "stock_data_current.json"
    manifest.write_bytes(raw)
    monkeypatch.setattr(bot, "SHARED_RAW_MANIFEST", manifest)
    # before: a compact head date blocked every Telegram report ("價格資料日無效")
    assert bot.read_shared_raw_pin() == {"manifestSha256": hashlib.sha256(raw).hexdigest(), "priceDate": "2026-10-05"}
    head["datasets"][0]["actual_max_date"] = 20261005
    manifest.write_bytes(json.dumps(head).encode("utf-8"))
    with pytest.raises(bot.AnalysisProcessError):
        bot.read_shared_raw_pin()


def test_receipt_date_in_either_spelling_reaches_the_child_as_iso(tmp_path, monkeypatch):
    receipt = {
        "manifest_schema": bot.CENTRAL_RECEIPT_SCHEMA, "status": "complete", "publishable": True,
        "fail_closed": False, "checkpoint": {"status": "complete", "resume_same_expected_asof": True},
        "expected_asof": "20261002", "run_id": "run-1",
        "hash": {"algorithm": "sha256", "all_verified": True},
        "manifest_content_sha256": "a" * 64, "registry_sha256": "b" * 64,
    }
    raw = json.dumps(receipt).encode("utf-8")
    path = tmp_path / "latest_manifest.json"
    path.write_bytes(raw)
    monkeypatch.setattr(bot, "CENTRAL_LATEST_RECEIPT_PATH", path)
    monkeypatch.setattr(bot, "load_env_file", lambda _path: {})
    evidence = {"central_receipt_sha256": hashlib.sha256(raw).hexdigest(), "central_receipt_run_id": "run-1",
                "central_expected_asof": "2026-10-02", "central_orchestration_run_id": "run-1"}
    monkeypatch.setattr(bot, "ensure_central_market_update", lambda: dict(evidence))
    environment = bot.analysis_environment()
    assert environment[bot.CENTRAL_EXPECTED_ASOF_ENV] == "2026-10-02"  # machine hand-off stays YYYY-MM-DD
    assert environment[bot.CENTRAL_READY_ENV] == "1"
    receipt["expected_asof"] = 20261002  # a JSON number is never the receipt date
    raw = json.dumps(receipt).encode("utf-8")
    path.write_bytes(raw)
    evidence["central_receipt_sha256"] = hashlib.sha256(raw).hexdigest()
    assert bot.CENTRAL_EXPECTED_ASOF_ENV not in bot.analysis_environment()


def _formal_payload(**overrides):
    payload = {
        "report_schema_version": bot.FORMAL_SINGLE_REPORT_SCHEMA,
        "central_projection_lineage": {"run_id": "run-1"}, "central_run_id": "run-1",
        "central_receipt_sha256": "c" * 64, "stock": {"stock_id": "2330"},
        "latest_data_date": "20261002", "estimated_for": "20261005", "estimated_for_approx": "20270104",
        "unmet_conditions": [],
    }
    payload.update(overrides)
    return payload


def _write_formal(tmp_path, payload):
    output_dir = tmp_path / "job"
    output_dir.mkdir()
    pdf_path, json_path = output_dir / "result.pdf", output_dir / "result.json"
    pdf_path.write_bytes(b"%PDF-1.7\n%%EOF\n")
    json_path.write_text(json.dumps(dict(payload, output_pdf=str(pdf_path), output_json=str(json_path))),
                         encoding="utf-8")
    return output_dir, pdf_path, json_path


@pytest.mark.parametrize("mode", [bot.MODE_THREE_MONTH, bot.MODE_TOMORROW_OPEN])
def test_formal_report_dates_in_either_spelling_match_the_central_date_by_value(tmp_path, mode):
    output_dir, pdf_path, json_path = _write_formal(tmp_path, _formal_payload())
    _, payload, _, _ = bot._validate_generated_report(mode, STOCK, output_dir, pdf_path, json_path, "2026-10-02",
                                                      "run-1", "c" * 64)
    assert payload["latest_data_date"] == "20261002"  # the report JSON is not rewritten


@pytest.mark.parametrize("overrides,message", [
    ({"latest_data_date": 20261002}, "資料日／預測日缺失"),
    ({"latest_data_date": "20261001"}, "中央 expected_asof 不一致"),
    ({"estimated_for": "20261002"}, "非未來交易日"),
])
def test_formal_report_dates_that_are_not_valid_fail_closed(tmp_path, overrides, message):
    output_dir, pdf_path, json_path = _write_formal(tmp_path, _formal_payload(**overrides))
    with pytest.raises(bot.AnalysisProcessError, match=message):
        bot._validate_generated_report(bot.MODE_TOMORROW_OPEN, STOCK, output_dir, pdf_path, json_path,
                                       "2026-10-02", "run-1", "c" * 64)


@pytest.mark.parametrize("latest,shown", [("2026-10-02", "20261002"), ("20261002", "20261002"),
                                          (20261002, "未確認"), ("junk", "未確認"), (None, "未確認")])
def test_provisional_caption_shows_the_data_date_as_yyyymmdd(latest, shown):
    payload = {"report_class": "provisional", "latest_data_date": latest, "source_gaps": []}
    caption = bot.report_caption(bot.MODE_THREE_MONTH, STOCK, payload)
    assert f"資料日：{shown}｜" in caption


@pytest.mark.parametrize("estimated,shown", [("2026-10-05", "20261005"), ("20261005", "20261005"), (None, "NA"),
                                             ("", "NA"), ("未定", "未定")])
def test_tomorrow_caption_shows_the_target_as_yyyymmdd(estimated, shown):
    payload = {"estimated_for": estimated, "result": {"direction_label": "開高"}}
    caption = bot.report_caption(bot.MODE_TOMORROW_OPEN, STOCK, payload)
    assert caption.splitlines()[1] == f"預估交易日：{shown}"


def test_caption_disclosures_show_dates_as_yyyymmdd_but_leave_timestamps_and_paths():
    payload = {"estimated_for": "2026-10-05", "result": {}, "unmet_conditions": [
        {"reason": "route_stale", "detail": "finmind_per 最新 2026-10-01 < 2026-10-02"},
        {"reason": "cutoff", "detail": "2026-10-02T15:30:00+08:00"},
        r"see D:\TWStockData\x\2026-10-02.json"]}
    caption = bot.report_caption(bot.MODE_TOMORROW_OPEN, STOCK, payload)
    assert "route_stale: finmind_per 最新 20261001 < 20261002" in caption
    assert "cutoff: 2026-10-02T15:30:00+08:00" in caption
    assert r"see D:\TWStockData\x\2026-10-02.json" in caption


def test_report_summary_shows_compact_dates_and_taipei_generation_time():
    metadata = {"forecastTargetDate": "20260713", "dataDate": "2026-07-09",
                "generatedAt": "2026-07-12T06:28:46+00:00"}
    text = sender.build_summary_text(metadata, "")
    assert "預測交易日：20260713" in text and "資料日：20260709" in text
    assert "產生時間：20260712 14:28:46" in text
    assert "NA" not in text.splitlines()[3]
    assert "產生時間：NA" in sender.build_summary_text({}, "")


def test_required_target_today_matches_either_spelling(monkeypatch):
    bundle = SimpleNamespace(fingerprint="f", metadata={"forecastTargetDate": "2026-07-13"},
                             manifest_path=Path("m.json"))
    monkeypatch.setattr(sender, "load_report_bundle", lambda path: bundle)
    assert sender.wait_for_stable_bundle(Path("m.json"), 0, 0, stable_seconds=0,
                                         required_target_date="20260713") is bundle
    with pytest.raises(sender.ReportDeferredError, match="最新報告預測日是 20260713，等待 20260714 的最後一版"):
        sender.wait_for_stable_bundle(Path("m.json"), 0, 0, stable_seconds=0, required_target_date="20260714")


def _mix_fixture(tmp_path):
    import test_mix_report_sender as fixtures

    return fixtures.make_fixture(tmp_path)


def _rewrite_status(paths, **fields):
    for name in ("current_run_status.json", "last_run_status.json"):
        path = paths.status_root / name
        status = json.loads(path.read_text(encoding="utf-8"))
        status.update(fields.get(name, fields.get("both", {})))
        path.write_text(json.dumps(status), encoding="utf-8")


def test_mix_calendar_receipt_dates_in_either_spelling(tmp_path):
    paths, files = _mix_fixture(tmp_path)
    receipt = json.loads(files["central"].read_text(encoding="utf-8"))
    dataset = next(item for item in receipt["datasets"] if item.get("dataset_id") == mix.CENTRAL_CALENDAR_DATASET_ID)
    dataset["expected_asof"] = "20260821"
    dataset["coverage"]["expected_asof"] = "20260821"
    dataset["coverage"]["next_session"] = "20260824"
    calendar = mix._validate_central_calendar(paths, receipt, "2026-08-21")
    assert calendar["next_session"] == "2026-08-24" and calendar["expected_asof"] == "2026-08-21"
    dataset["coverage"]["next_session"] = "20260821"  # not after the as-of day (by value)
    with pytest.raises(mix.MixReportIntegrityError, match="必須晚於 expected_asof"):
        mix._validate_central_calendar(paths, receipt, "2026-08-21")
    dataset["coverage"]["next_session"] = "20260824"
    dataset["expected_asof"] = 20260821
    with pytest.raises(mix.MixReportIntegrityError, match="schema/status 不相容"):
        mix._validate_central_calendar(paths, receipt, "2026-08-21")


def test_e2e_lineage_accepts_a_compact_receipt_date_and_compares_by_value(tmp_path, monkeypatch):
    receipt = {"manifest_schema": e2e.RECEIPT_SCHEMA, "status": "complete", "publishable": True,
               "fail_closed": False, "checkpoint": {"status": "complete", "resume_same_expected_asof": True},
               "expected_asof": "20260824", "run_id": "run-1", "hash": {"algorithm": "sha256", "all_verified": True},
               "manifest_content_sha256": "a" * 64, "registry_sha256": "b" * 64}
    path = tmp_path / "latest_manifest.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(e2e, "CENTRAL_LATEST_RECEIPT_PATH", path)
    lineage = e2e.load_central_lineage()
    assert lineage["expected_asof"] == "2026-08-24"
    record = {"centralReceiptRunId": "run-1", "centralReceiptSha256": lineage["receipt_sha256"],
              "centralExpectedAsOf": "2026-08-24"}
    e2e.verify_lineage_fields(record, lineage)
    e2e.verify_lineage_fields(dict(record, centralExpectedAsOf="20260824"), lineage)
    with pytest.raises(e2e.E2EFailure):
        e2e.verify_lineage_fields(dict(record, centralExpectedAsOf="20260825"), lineage)
    receipt["expected_asof"] = 20260824
    path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(e2e.E2EFailure, match="未達 complete/publishable/current"):
        e2e.load_central_lineage()
