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
import telegram_report_sender as sender  # noqa: E402
import telegram_stock_command_bot as bot  # noqa: E402

# The live e2e harness (telegram_command_bot_e2e.py) and the three_month / tomorrow_open / provisional
# bot paths were removed by the C0 redesign; their date tests are in _retired/.

STOCK = bot.StockMatch("2330", "台積電", "exact_id", 1.0)


def _c0_estimate_payload(**overrides):
    payload = {
        "report_schema_version": bot.C0_SINGLE_REPORT_SCHEMA,
        "horizon_sessions": 1,
        "latest_data_date": "2026-10-02",
        "target_session_date": "2026-10-05",
        "prediction": {"prob_up": 0.55, "expected_return": 0.004, "calibration_status": "base_only"},
        "model_artifact_sha256": "a" * 64,
        "requirements_version": "req-fixture-1",  # an identifier (the real one embeds an ISO day), not a date
        "data_time": {"analysis_asof": "2026-10-02", "read_at_utc": "2026-10-02T08:00:00+00:00",
                      "head": {"published_at": "2026-10-02T07:40:00+00:00"}},
        "published_snapshot": {"snapshot_fingerprint": "f" * 64},
    }
    payload.update(overrides)
    return payload


# ------------------------------------------------------------------------------------------------------------------
# command bot: what the user sees
# ------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("signal,target,shown_signal,shown_target", [
    ("2026-10-02", "2026-10-05", "20261002", "20261005"),
    ("20261002", "20261005", "20261002", "20261005"),
    ("2026-10-02", None, "20261002", "尚未驗證日期"),
])
def test_c0_caption_shows_signal_and_target_dates_as_yyyymmdd(signal, target, shown_signal, shown_target):
    caption = bot.report_caption(bot.MODE_H1, STOCK, _c0_estimate_payload(latest_data_date=signal,
                                                                         target_session_date=target))
    assert caption.splitlines()[1] == f"訊號收盤日：{shown_signal}；到期日：{shown_target}"
    assert "2026-10-0" not in caption


def test_c0_caption_data_time_line_is_yyyymmdd_with_taipei_clock():
    caption = bot.report_caption(bot.MODE_H1, STOCK, _c0_estimate_payload())
    # 07:40Z = 15:40 Taipei, same day as the 08:00Z (16:00 Taipei) read.
    assert "資料：20261002（發布 15:40，讀取 20261002 16:00）" in caption
    newer = {"current_binding": {"asof": "2026-10-05", "snapshot_fingerprint": "e" * 64,
                                 "published_sources": [{"published_at": "2026-10-05T07:40:00+00:00"}]}}
    caption = bot.report_caption(bot.MODE_H1, STOCK, _c0_estimate_payload(), newer)
    assert "執行期間已有較新資料 20261005（發布 20261005 15:40）；本報告仍使用上列資料。" in caption


def test_no_estimate_caption_shows_the_stock_last_price_day_as_yyyymmdd():
    payload = _c0_estimate_payload(report_class=bot.C0_NO_ESTIMATE_REPORT_CLASS, prediction=None,
                                   no_estimate_reason="stock_price_older_than_asof",
                                   stock_last_price_date="2026-09-30")
    caption = bot.report_caption(bot.MODE_H1, STOCK, payload)
    assert "該股最後價格日：20260930；分析資料日：20261002" in caption
    assert "（stock_price_older_than_asof）" in caption
    assert "2026-09-30" not in caption and "2026-10-02" not in caption


def test_logs_and_messages_use_the_compact_date():
    source = (SUITE_DIR / "telegram_stock_command_bot.py").read_text(encoding="utf-8")
    assert '"%Y%m%d %H:%M:%S"' in source and '"%Y-%m-%d %H:%M:%S"' not in source
    assert 'r"\\d{4}-\\d{2}-\\d{2}"' not in source  # no ISO-only date validation left in the bot


# ------------------------------------------------------------------------------------------------------------------
# telegram_report_sender (shared client helpers; the D-stock full-market report CLI was retired)
# ------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value,want", [("20260713", "20260713"), ("2026-07-13", "20260713"), (" 20260713 ", "20260713"),
                                        (None, "NA"), ("", "NA"), ("junk", "junk")])
def test_report_sender_displays_yyyymmdd_not_yyyy_mm_dd(value, want):
    # until 2026-10-06 this helper turned 20260713 INTO 2026-07-13 for display
    assert sender._format_yyyymmdd(value) == want


@pytest.mark.parametrize("value,want", [("20260713", date(2026, 7, 13)), ("2026-07-13", date(2026, 7, 13)),
                                        (20260713, None), ("2026/07/13", None), ("", None), (None, None)])
def test_report_sender_manifest_dates_accept_both_spellings(value, want):
    assert sender._manifest_day(value) == want


# ------------------------------------------------------------------------------------------------------------------
# mix_report_sender (MIX ALL pre-open report)
# ------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["20260821", "2026-08-21", " 2026-08-21 ", date(2026, 8, 21)])
def test_mix_dates_accept_both_spellings(value):
    assert mix._require_date(value, "central expected_asof") == "2026-08-21"


@pytest.mark.parametrize("value", ["20260230", "2026-02-30", 20260821, "2026/08/21", None, ""])
def test_mix_dates_refuse_other_values(value):
    # One message for every refused value (impossible calendar day, JSON number, other spelling, empty).
    with pytest.raises(mix.MixReportIntegrityError, match=r"central expected_asof 必須是有效 YYYYMMDD（相容 YYYY-MM-DD）："):
        mix._require_date(value, "central expected_asof")


def test_mix_published_dates_compare_by_value():
    assert mix._same_date("20260821", "2026-08-21") and mix._same_date(" 2026-08-21", "2026-08-21")
    assert not mix._same_date(20260821, "2026-08-21") and not mix._same_date(None, "2026-08-21")


def test_mix_bundle_from_compact_status_dates_is_identical_and_shows_yyyymmdd(tmp_path, monkeypatch):
    # The MIX sender now reads the C0 pointer/manifest/payload chain (no central status files);
    # its own dates may be written in either spelling and are compared by value.
    import test_mix_report_sender as fixtures

    paths, _ = fixtures.make_c0_fixture(tmp_path / "iso", monkeypatch)
    iso = mix.load_mix_bundle(paths)
    assert "報告資料日：20260821" in iso.summary_text
    assert "2026-08-2" not in iso.summary_text

    def compact(document):
        document["as_of_date"] = "20260821"

    paths, _ = fixtures.make_c0_fixture(tmp_path / "compact", monkeypatch, pointer_update=compact,
                                        manifest_update=compact, payload_update=compact)
    bundle = mix.load_mix_bundle(paths)
    assert bundle.expected_asof == "2026-08-21"  # state keys stay ISO
    assert "報告資料日：20260821" in bundle.summary_text
    # the current data day in the other spelling is the same day, not a newer one
    fixtures.use_current(monkeypatch, asof="20260821")
    assert "newer_data_available" not in [item["code"] for item in mix.load_mix_bundle(paths).version_labels]
    # explicit CLI values in either spelling
    assert mix.load_mix_bundle(paths, expected_asof="2026-08-21", target="20260824").target == "2026-08-24"
    assert mix.load_mix_bundle(paths, expected_asof="20260821", target="2026-08-24").target == "2026-08-24"


def test_mix_cli_help_names_the_compact_spelling(capsys):
    with pytest.raises(SystemExit):
        mix._parser().parse_args(["--help"])
    out = capsys.readouterr().out
    assert "YYYYMMDD（相容 YYYY-MM-DD）" in out


# ------------------------------------------------------------------------------------------------------------------
# all senders
# ------------------------------------------------------------------------------------------------------------------
def test_dates_are_not_parsed_with_iso_only_patterns_any_more():
    assert not (SUITE_DIR / "telegram_command_bot_e2e.py").exists()  # removed with the three_month bot
    for name in ("mix_report_sender.py", "telegram_report_sender.py", "telegram_stock_command_bot.py"):
        source = (SUITE_DIR / name).read_text(encoding="utf-8")
        assert r'r"^\d{4}-\d{2}-\d{2}$"' not in source and r'r"\d{4}-\d{2}-\d{2}\Z"' not in source, name
        assert '.replace("-", "")' not in source, name
    assert os.path.basename(sender.SHARED_DIR) == "shared"
