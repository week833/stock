"""日期契約（2026-10-06 使用者指示）：使用者看得到的日期一律 YYYYMMDD，舊的 YYYY-MM-DD 仍可輸入；資料裡兩種拼寫視為同一天。

前半段逐條對應「收集資料工具」D:/collect data tool/tests/test_date_format.py 的接受／拒絕表（核心語意必須相同），
並在收集資料工具在本機時，拿它的 app/date_format.py 對同一批值逐一比對（只讀原始碼執行，不寫 __pycache__）。
後半段是本程式的補充函式（INVENTORY.md 第 3 節）：calendar_day_of、iso_*、cli_day、display_clock、display_text、
find_day_token 與 pandas 輔助（to_timestamp_day、to_datetime_days、canonical_date_tokens）。
"""
from __future__ import annotations

import argparse
import sys
import time
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SHARED_DIR = Path(__file__).resolve().parents[1]
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

import date_format as cd  # noqa: E402
from date_format import (day_before, display_day, format_clock, format_day, format_iso, latest_day,  # noqa: E402
                         parse_day, same_day, to_day)

COLLECT_TOOL_MODULE = Path(r"D:\collect data tool\app\date_format.py")
FULLWIDTH = "\uff12\uff10\uff12\uff14\uff10\uff12\uff12\uff19"  # ２０２４０２２９


# ------------------------------------------------------------------------------------------------------------------
# 收集資料工具的表（逐條相同）
# ------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value,want_day,want_compact,want_iso", [
    (date(2024, 2, 29), date(2024, 2, 29), "20240229", "2024-02-29"),
    ("20240229", date(2024, 2, 29), "20240229", "2024-02-29"),
    ("2024-02-29", date(2024, 2, 29), "20240229", "2024-02-29"),
    ("00010101", date(1, 1, 1), "00010101", "0001-01-01"),
    ("0001-01-01", date(1, 1, 1), "00010101", "0001-01-01"),
])
def test_entry_accepts_both_spellings_and_formats_without_changing_the_day(value, want_day, want_compact, want_iso):
    assert parse_day(value) == want_day
    assert format_day(value) == want_compact
    assert format_iso(value) == want_iso
    assert cd.iso_day(value) == want_iso


@pytest.mark.parametrize("value", [datetime(2024, 2, 29), datetime(2024, 2, 29, tzinfo=timezone.utc),
                                   20240229, True, None, b"20240229",
                                   pd.Timestamp("2024-02-29"), np.datetime64("2024-02-29"), 20240229.0])
def test_entry_rejects_timestamps_numbers_and_non_dates(value):
    with pytest.raises(TypeError):
        parse_day(value)
    with pytest.raises(TypeError):
        cd.iso_day(value)


@pytest.mark.parametrize("value", [
    "", "2024/02/29", "2024-2-29", "202402", "2024-02", "1130229", "20230229", "2024-02-30", "00000101",
    "20241301", "2024-W09-4", "2024-02-29T00:00:00+08:00", "20240229\n", " 20240229", "20240229 ",
    FULLWIDTH, "2024-02-29\n",
    # 本程式另外列出的（INVENTORY 0.2 / D12）：ISO 週、點分隔、民國年斜線、時間戳、全形減號
    "2026W401", "2026-W40-1", "2024.02.29", "113/02/29", "20240229T000000", "2024-02-29 00:00:00", "2024\uff0d02\uff0d29",
    "+20240229", "-2024-02-29", "2024-02-290", "120240229",
])
def test_entry_rejects_other_formats_periods_roc_years_and_impossible_days(value):
    with pytest.raises(ValueError):
        parse_day(value)
    with pytest.raises(ValueError):
        cd.iso_day(value)


def test_entry_error_messages_name_the_two_accepted_spellings():
    with pytest.raises(ValueError, match="日期格式要是 YYYYMMDD（相容 YYYY-MM-DD），收到 '2024/02/29'"):
        parse_day("2024/02/29")
    with pytest.raises(ValueError, match="不是有效的日曆日期：'20230229'"):
        parse_day("20230229")


@pytest.mark.parametrize("value", [20260105, True, None, "2026-01-05T10:00:00", "2026-01-05 10:00:00",
                                   datetime(2026, 1, 5), "202601", "1150105", 1.5, ["20260105"],
                                   np.int64(20260105), float("nan"), pd.NaT, pd.Timestamp("2026-01-05"), b"20260105"])
def test_stored_values_that_are_not_pure_dates_are_not_dates(value):
    # JSON 數字 20260105 不可被當成日期（更不可被當成 1970 年）；時間戳也不壓成日期。
    assert to_day(value) is None
    assert cd.iso_day_or_none(value) is None


def test_stored_values_in_either_spelling_are_the_same_day():
    assert to_day("20261005") == to_day("2026-10-05") == date(2026, 10, 5)
    assert same_day("20261005", "2026-10-05") and not same_day("20261005", "2026-10-06")
    assert not same_day("20261005", 20261005) and not same_day(None, None)
    assert same_day(date(2026, 10, 5), "20261005")
    # 時間戳不是純日期（同收集資料工具）
    assert not same_day("2026-10-05T00:00:00", "2026-10-05")


def test_latest_day_compares_values_not_strings():
    # 字串比較 max(["2026-10-03", "20261001"]) 是 "20261001"（'1' > '-'），比日期值才是 10/03。
    assert max(["2026-10-03", "20261001"]) == "20261001"
    assert latest_day(["2026-10-03", "20261001", None, 20261009, "junk"]) == date(2026, 10, 3)
    assert latest_day([None, 20261009]) is None and latest_day([]) is None


def test_display_shows_compact_dates_and_leaves_timestamps_and_text_alone():
    assert display_day("2026-10-05") == "20261005"
    assert display_day("20261005") == "20261005"
    assert display_day(date(2026, 10, 5)) == "20261005"
    assert display_day("2026-10-05T23:38:07+00:00") == "2026-10-05T23:38:07+00:00"
    assert display_day("2026-10-05 10:00:00") == "2026-10-05 10:00:00"
    assert display_day("not a date") == "not a date"
    assert display_day(None) == "-" and display_day("") == "-"


def test_display_takes_the_callers_empty_text():
    # 本程式各處的空值文字（未取得、未確認、NA）
    assert display_day(None, empty="NA") == "NA" and display_day("", empty="未確認") == "未確認"
    assert display_day("2026-10-05", empty="NA") == "20261005"
    # JSON 數字不是日期：原樣顯示，不被改成任何日子
    assert display_day(20261005, empty="NA") == "20261005" and display_day(0) == "0"


def test_boundaries_compare_date_values_not_strings():
    # 字串比較時 '-'（0x2D）排在數字前面，混用拼寫會比錯。
    assert "2026-10-05" < "20261005" and not day_before("2026-10-05", "20261005")
    assert day_before("20261004", "2026-10-05") and not day_before("20261006", "2026-10-05")
    assert day_before("2026-10-05", "20261006")
    # 時間戳型欄位維持原樣，但邊界比它所在的那一天：截止日當天的時間戳不算早於截止日，兩種拼寫的截止日結果相同。
    assert day_before("2026-10-05 09:00:00", "2026-10-06") is True
    for cutoff in ("2026-10-06", "20261006"):
        assert day_before("2026-10-06 09:00:00", cutoff) is False
        assert day_before("2026-10-05T23:59:59+08:00", cutoff) is True
    # 什麼日期都不是才退回字串比較；壞掉的時間戳不被當成日期
    assert day_before("2026-10-05 25:00:00", "2026-10-06") is True and day_before("abc", "abd") is True


@pytest.mark.parametrize("bad", [None, 20261006, True, 1.5, b"2026-10-06", ["2026-10-06"], pd.NaT,
                                 np.datetime64("2026-10-06")])
def test_a_boundary_that_is_neither_a_date_nor_a_string_is_an_error_not_a_guess(bad):
    # 不可拿 str(None)、str(20261006)、"NaT" 去比字串：判成「早於截止日」會讓呼叫端把列當成依規則排除而放過。
    with pytest.raises(TypeError):
        day_before("2026-03-01", bad)
    with pytest.raises(TypeError):
        day_before(bad, "2026-03-01")


def test_a_datetime_boundary_compares_by_the_day_it_falls_on():
    assert day_before(datetime(2026, 10, 5, 23, 59), "20261006") is True
    assert day_before("2026-10-06", datetime(2026, 10, 6, 0, 1)) is False
    assert day_before(date(2026, 10, 5), "2026-10-06") is True
    assert day_before(pd.Timestamp("2026-10-05 23:59"), "20261006") is True


def test_generated_times_shown_to_people_use_the_compact_date():
    assert format_clock(datetime(2026, 10, 6, 20, 59, 5)) == "20261006 20:59:05"
    assert format_clock(datetime(2026, 10, 6, 20, 59, 5, tzinfo=timezone.utc)) == "20261006 20:59:05"
    assert format_clock(pd.Timestamp("2026-10-06 20:59:05")) == "20261006 20:59:05"
    with pytest.raises(TypeError):
        format_clock(date(2026, 10, 6))
    with pytest.raises(TypeError):
        format_clock("2026-10-06 20:59:05")


# ------------------------------------------------------------------------------------------------------------------
# 與收集資料工具的實作逐值比對（核心函式結果必須完全相同）
# ------------------------------------------------------------------------------------------------------------------
_PROBES = [
    date(2024, 2, 29), date(1, 1, 1), date(9999, 12, 31), datetime(2024, 2, 29), datetime(2024, 2, 29, 23, 59),
    datetime(2024, 2, 29, tzinfo=timezone.utc), "20240229", "2024-02-29", "00010101", "0001-01-01", "99991231",
    "", " ", "2024/02/29", "2024-2-29", "202402", "2024-02", "1130229", "20230229", "2024-02-30", "00000101",
    "20241301", "2024-W09-4", "2026W401", "2024-02-29T00:00:00+08:00", "2024-02-29 10:00:00", "2024-02-29 25:00:00",
    "2024-02-29T", "20240229T101010", "20240229\n", " 20240229", "20240229 ", FULLWIDTH, "2024-02-29\n", "abc",
    20240229, 0, True, False, None, 1.5, b"20240229", ["20240229"], ("2024-02-29",),
]


def _collect_tool_module():
    if not COLLECT_TOOL_MODULE.is_file():
        pytest.skip("收集資料工具不在本機")
    source = COLLECT_TOOL_MODULE.read_text(encoding="utf-8-sig")
    module = types.ModuleType("collect_tool_date_format_reference")
    module.__file__ = str(COLLECT_TOOL_MODULE)
    exec(compile(source, str(COLLECT_TOOL_MODULE), "exec"), module.__dict__)  # 只讀原始碼，不產生 .pyc
    return module


def _outcome(function, *args):
    try:
        return ("ok", function(*args))
    except Exception as exc:  # noqa: BLE001 - the exception type is the compared outcome
        return ("raise", type(exc).__name__)


def test_core_functions_match_the_collect_tool_value_by_value():
    reference = _collect_tool_module()
    for name in ("parse_day", "to_day", "format_day", "format_iso", "display_day"):
        ours, theirs = getattr(cd, name), getattr(reference, name)
        for value in _PROBES:
            assert _outcome(ours, value) == _outcome(theirs, value), (name, value)
    for left in _PROBES:
        for right in ("2024-02-29", "20240229", "2024-03-01", "20240228", "2024-02-29 09:00:00", "abc",
                      datetime(2024, 2, 29, 1), date(2024, 2, 29)):
            assert _outcome(cd.same_day, left, right) == _outcome(reference.same_day, left, right), (left, right)
            assert _outcome(cd.day_before, left, right) == _outcome(reference.day_before, left, right), (left, right)
            assert _outcome(cd.day_before, right, left) == _outcome(reference.day_before, right, left), (right, left)
    assert cd.latest_day(_PROBES[:12] + ["20240301"]) == reference.latest_day(_PROBES[:12] + ["20240301"])
    for stamp in (datetime(2026, 10, 6, 20, 59, 5), datetime(2026, 10, 6, 20, 59, 5, tzinfo=timezone.utc)):
        assert cd.format_clock(stamp) == reference.format_clock(stamp)


# ------------------------------------------------------------------------------------------------------------------
# 本程式的補充：calendar_day_of、iso_*、cli_day
# ------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value,want", [
    ("20261002", date(2026, 10, 2)),
    ("2026-10-02", date(2026, 10, 2)),
    (date(2026, 10, 2), date(2026, 10, 2)),
    (datetime(2026, 10, 2, 23, 59), date(2026, 10, 2)),
    # 有時區的時間戳取它自己的牆上日期，不換時區（需要台北日期的呼叫端先換成台北時間）
    (datetime(2026, 10, 2, 23, 30, tzinfo=timezone.utc), date(2026, 10, 2)),
    (pd.Timestamp("2026-10-02 13:00"), date(2026, 10, 2)),
    (pd.Timestamp("2026-10-02 13:00", tz="Asia/Taipei"), date(2026, 10, 2)),
    (np.datetime64("2026-10-02T12:00"), date(2026, 10, 2)),
    (np.datetime64("2026-10-02", "D"), date(2026, 10, 2)),
    ("2026-10-02T15:30:00+08:00", date(2026, 10, 2)),
    ("2026-10-02T15:30:00Z", date(2026, 10, 2)),
    ("2026-10-02 15:30:00", date(2026, 10, 2)),
    ("2026-10-02T15:30", date(2026, 10, 2)),
    # 不是日期也不是 ISO 時間戳 -> None
    ("20261002T15:30:00", None), ("20261002 15:30:00", None), ("2026-10-02 garbage", None),
    ("2026-10-02 25:00:00", None), ("2026-10-02X15:30", None), ("2026W401", None), ("2026/10/02", None),
    ("2026-02-30T00:00:00", None), (20261002, None), (True, None), (None, None), (1.5, None), ("", None),
    (pd.NaT, None), (np.datetime64("NaT", "ns"), None), (float("nan"), None), (b"20261002", None),
])
def test_calendar_day_of_takes_the_day_of_dates_and_iso_timestamps_only(value, want):
    assert cd.calendar_day_of(value) == want
    assert cd.iso_calendar_day(value) == (None if want is None else want.isoformat())


def test_iso_readers_return_the_canonical_machine_spelling():
    assert cd.iso_day("20261002") == cd.iso_day("2026-10-02") == cd.iso_day(date(2026, 10, 2)) == "2026-10-02"
    assert cd.iso_day_or_none("20261002") == "2026-10-02" and cd.iso_day_or_none("2026-10-02T00:00:00") is None
    assert cd.iso_calendar_day("2026-10-02T23:00:00+08:00") == "2026-10-02"
    for bad in (20261002, "2026/10/02", None):
        assert cd.iso_day_or_none(bad) is None and cd.iso_calendar_day(bad) is None


def test_cli_day_accepts_both_spellings_and_returns_iso_for_the_code_downstream():
    assert cd.cli_day("20261002") == "2026-10-02" and cd.cli_day("2026-10-02") == "2026-10-02"
    for bad in ("2026/10/02", "20260230", "2026-1-2", "", " 20261002", "2026W401", "1151002"):
        with pytest.raises(argparse.ArgumentTypeError, match=r"日期格式要是 YYYYMMDD（相容 YYYY-MM-DD）："):
            cd.cli_day(bad)
    assert cd.DATE_HELP == "YYYYMMDD（相容 YYYY-MM-DD）"


def test_cli_day_inside_argparse_reports_a_usage_error(capsys):
    parser = argparse.ArgumentParser(prog="demo")
    parser.add_argument("--end-date", type=cd.cli_day, help=f"結束日 {cd.DATE_HELP}")
    assert parser.parse_args(["--end-date", "20261002"]).end_date == "2026-10-02"
    assert parser.parse_args(["--end-date", "2026-10-02"]).end_date == "2026-10-02"
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--end-date", "2026/10/02"])
    assert excinfo.value.code == 2
    assert "日期格式要是 YYYYMMDD（相容 YYYY-MM-DD）" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------------------------------
# display_clock：給人看的「日期 時間」
# ------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value,want", [
    (datetime(2026, 10, 2, 7, 30, 5), "20261002 07:30:05"),  # naive：照原本的牆上時間
    (datetime(2026, 10, 1, 23, 30, tzinfo=timezone.utc), "20261002 07:30:00"),  # 有時區：換成台北時間
    (pd.Timestamp("2026-10-01T23:30:00Z"), "20261002 07:30:00"),
    ("2026-10-01T23:30:00+00:00", "20261002 07:30:00"),
    ("2026-10-02T15:30:00+08:00", "20261002 15:30:00"),
    ("2026-10-02 15:30:00", "20261002 15:30:00"),
    ("2026-10-02", "20261002"),
    ("20261002", "20261002"),
    (date(2026, 10, 2), "20261002"),
    ("20261002T153000", "20261002T153000"),  # 不是 ISO 時間戳：原樣
    ("not a time", "not a time"),
    (20261002, "20261002"),
])
def test_display_clock_prints_compact_date_and_taipei_wall_time(value, want):
    assert cd.display_clock(value) == want


def test_display_clock_empty_values():
    assert cd.display_clock(None) == "-" and cd.display_clock("") == "-"
    assert cd.display_clock(pd.NaT, empty="未取得") == "未取得" and cd.display_clock(None, empty="NA") == "NA"


# ------------------------------------------------------------------------------------------------------------------
# display_text（D7）：自由文字裡獨立的 ISO 日期只在顯示時改成 YYYYMMDD
# ------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,want", [
    # INVENTORY D7 列出的機器字樣（JSON 裡維持 ISO；顯示時改）
    ("official_calendar: invalid next_session for 2026-10-02", "official_calendar: invalid next_session for 20261002"),
    ("start_date_gap:ds:2026-01-02~2026-01-05", "start_date_gap:ds:20260102~20260105"),
    ("verified_calendar_retention_prefix_removed:cutoff=2026-01-02",
     "verified_calendar_retention_prefix_removed:cutoff=20260102"),
    ("canonical=2026-01-02..2026-01-05", "canonical=20260102..20260105"),
    ("declared_cutoff=2026-01-02;calendar_first_known=2026-01-05",
     "declared_cutoff=20260102;calendar_first_known=20260105"),
    ("official_calendar: conflicting session 2026-10-02", "official_calendar: conflicting session 20261002"),
    ("xs_route_stale:finmind_per:2026-10-02<2026-10-05", "xs_route_stale:finmind_per:20261002<20261005"),
    # 中文句子裡（中文字前後相連也算獨立）
    ("【暫時版本】分析日 2026-10-02（資料端 2026-10-05 資料不完整）",
     "【暫時版本】分析日 20261002（資料端 20261005 資料不完整）"),
    ("分析日2026-10-02，資料端2026-10-05。", "分析日20261002，資料端20261005。"),
    ("缺 2026-09-28、2026-09-29、2026-09-30", "缺 20260928、20260929、20260930"),
    ("(2026-10-02)", "(20261002)"),
    ('"2026-10-02"', '"20261002"'),
    ("end 2026-10-02.", "end 20261002."),
    ("2026-10-02", "20261002"),
    ("a\n2026-10-02\tb", "a\n20261002\tb"),
    # 時間戳不動
    ("published at 2026-10-02T15:30:00+08:00 ok", "published at 2026-10-02T15:30:00+08:00 ok"),
    ("2026-10-02 15:30:00 開盤", "2026-10-02 15:30:00 開盤"),
    ("2026-10-02 9:30", "2026-10-02 9:30"),
    ("2026-10-02:15", "2026-10-02:15"),
    # 網址、路徑、檔名不動
    ("see https://example.com/data/2026-10-02/x.json now", "see https://example.com/data/2026-10-02/x.json now"),
    ("https://api.example.com/v4/data?date=2026-10-02&id=2330", "https://api.example.com/v4/data?date=2026-10-02&id=2330"),
    (r"D:\TWStockData\shared\2026-10-02\x.json", r"D:\TWStockData\shared\2026-10-02\x.json"),
    (r"path=D:\stock\out\report_2026-10-02.pdf", r"path=D:\stock\out\report_2026-10-02.pdf"),
    ("D:/stock/2026-10-02.json", "D:/stock/2026-10-02.json"),
    ("blocks/2026/2026-10-02__abc.jsonl", "blocks/2026/2026-10-02__abc.jsonl"),
    ("2026-10-02/next", "2026-10-02/next"),
    ("summary_2026-10-02.json", "summary_2026-10-02.json"),
    ("2026-10-02.json", "2026-10-02.json"),
    (r"C:\a\2026-01-02\b and 2026-01-03", r"C:\a\2026-01-02\b and 20260103"),
    # 與數字或其他字相連、無效日期、已是 8 位數字：不動
    ("12026-10-02", "12026-10-02"), ("2026-10-023", "2026-10-023"), ("run-2026-10-02", "run-2026-10-02"),
    ("2026-10-02-01", "2026-10-02-01"), ("v1.2026-10-02", "v1.2026-10-02"), ("x2026-10-02", "x2026-10-02"),
    ("2026-02-30", "2026-02-30"), ("2026-13-01", "2026-13-01"), ("20261002", "20261002"),
    ("2026-1-2", "2026-1-2"), ("2026/10/02", "2026/10/02"), ("", ""), ("no dates here", "no dates here"),
])
def test_display_text_rewrites_standalone_iso_dates_only(text, want):
    assert cd.display_text(text) == want
    assert cd.display_text(want) == want  # 冪等：再套一次不再改


def test_display_text_leaves_non_strings_alone():
    for value in (None, 20261002, date(2026, 10, 2), ["2026-10-02"]):
        assert cd.display_text(value) is value


# ------------------------------------------------------------------------------------------------------------------
# find_day_token：契約文字裡的日期
# ------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,want", [
    ("expected_asof must be on or after 2026-03-31 (Q1 deadline)", date(2026, 3, 31)),
    ("expected asof is 20261002 (Taipei)", date(2026, 10, 2)),
    ("first 2026-02-30 then 2026-05-15", date(2026, 5, 15)),  # 無效的跳過
    ("id 12345678 then 20261002", date(2026, 10, 2)),  # 8 位數字但不是日期的跳過
    ("123456789 2026-10-02", date(2026, 10, 2)),  # 9 位數字不是獨立的 8 位
    ("deadline 2026-08-14T23:59:59+08:00", date(2026, 8, 14)),
    ("no date", None), ("", None), ("2026/10/02", None), ("12026-10-02", None),
    # C4（日期審查）：有 ISO 日期時一律取 ISO；編號、版本號、與字母相連的 8 位數字都不算
    ("rule 20260814 rev; period ending 2026-06-30", date(2026, 6, 30)),
    ("contract id 20250101-A, period ending 2026-06-30", date(2026, 6, 30)),
    ("rev20260814 period 2026-06-30", date(2026, 6, 30)),
    ("rev20260814 only", None), ("20260814rev only", None), ("id 20250101-A only", None),
    ("x2026-06-30 then 2026-09-30", date(2026, 9, 30)), ("2026-06-30x then 20261002", date(2026, 10, 2)),
    ("period_2026-06-30", None), ("(20261002)", date(2026, 10, 2)), ("as of 20261002.", date(2026, 10, 2)),
])
def test_find_day_token_returns_the_first_valid_standalone_date(text, want):
    assert cd.find_day_token(text) == want


def test_find_day_token_keeps_the_pre_change_reading_of_iso_rule_prose():
    # 改版前（central_publication_reader）：re.search(r"(\d{4})-(\d{2})-(\d{2})") 的第一個符合處。
    # 一般契約文字（獨立的 ISO 日期，前後可有其他 8 位數字）結果必須相同。
    import re
    for text in ("latest statements for period ending 2026-06-30", "rule 20260814 rev; period ending 2026-06-30",
                 "Q2 2026 deadline 2026-08-14T23:59:59+08:00 (id 20260101)"):
        old = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
        assert cd.find_day_token(text) == date(int(old.group(1)), int(old.group(2)), int(old.group(3)))


def test_display_day_and_same_day_are_strict_about_timestamps_by_design():
    # C5（日期審查）：與收集資料工具相同，只有 date 與兩種拼寫的字串算純日期；時間戳型別原樣顯示、same_day 為 False。
    # 要顯示或比較時間戳所在的日子，先用 calendar_day_of。
    import numpy as np
    import pandas as pd
    stamp = pd.Timestamp("2026-10-02")
    assert cd.display_day(stamp) == "2026-10-02 00:00:00"
    assert cd.display_day(datetime(2026, 10, 2, 15, 30)) == "2026-10-02 15:30:00"
    assert cd.display_day(np.datetime64("2026-10-02")) == "2026-10-02"
    assert not cd.same_day(stamp, "20261002") and not cd.same_day(datetime(2026, 10, 2), "2026-10-02")
    for value in (stamp, datetime(2026, 10, 2, 15, 30), np.datetime64("2026-10-02")):
        assert cd.display_day(cd.calendar_day_of(value)) == "20261002"
        assert cd.same_day(cd.calendar_day_of(value), "20261002")
    assert cd.display_clock(datetime(2026, 10, 2, 15, 30)) == "20261002 15:30:00"


def test_find_day_token_ignores_non_strings():
    assert cd.find_day_token(None) is None and cd.find_day_token(20261002) is None


# ------------------------------------------------------------------------------------------------------------------
# pandas 輔助
# ------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["20261002", "2026-10-02", date(2026, 10, 2), datetime(2026, 10, 2, 13, 5),
                                   pd.Timestamp("2026-10-02 13:05"), np.datetime64("2026-10-02T13:05"),
                                   "2026-10-02T13:05:00+08:00"])
def test_to_timestamp_day_gives_naive_midnight_of_the_calendar_day(value):
    stamp = cd.to_timestamp_day(value)
    assert stamp == pd.Timestamp("2026-10-02") and stamp.tzinfo is None


@pytest.mark.parametrize("value", [None, "", float("nan"), pd.NaT, np.datetime64("NaT", "ns"), np.float32("nan"),
                                   pd.NA])
def test_to_timestamp_day_maps_missing_values_to_nat(value):
    assert cd.to_timestamp_day(value) is pd.NaT


@pytest.mark.parametrize("value", [20261002, 0, True, 1.5, np.int64(20261002), "2026/10/02", "2026W401", "junk",
                                   "20261002T153000", " 20261002", b"20261002"])
def test_to_timestamp_day_refuses_numbers_and_other_formats(value):
    # pd.Timestamp(20261002) 是 1970-01-01 00:00:00.020261002：這裡一定是錯誤，不會變成 1970 年
    with pytest.raises(ValueError):
        cd.to_timestamp_day(value)


def test_to_datetime_days_mixed_spellings_keep_every_row_and_the_series_shape():
    raw = pd.Series(["2026-10-01", "20261002", None, "", float("nan"), "2026-10-02T09:00:00+08:00"],
                    index=[5, 6, 7, 8, 9, 10], name="date")
    # pandas 自己推論格式：第二種拼寫在 coerce 下變 NaT（會被 dropna 靜默丟掉），這正是要取代的寫法
    assert pd.to_datetime(raw.iloc[:2], errors="coerce").isna().tolist() == [False, True]
    out = cd.to_datetime_days(raw)
    assert isinstance(out, pd.Series) and list(out.index) == [5, 6, 7, 8, 9, 10] and out.name == "date"
    assert str(out.dtype) == "datetime64[ns]"
    assert out.tolist()[:2] == [pd.Timestamp("2026-10-01"), pd.Timestamp("2026-10-02")]
    assert out.iloc[2:5].isna().all() and out.iloc[5] == pd.Timestamp("2026-10-02")
    assert raw.tolist()[:2] == ["2026-10-01", "20261002"]  # 原物件不改寫


@pytest.mark.parametrize("bad", [20261002, True, "2026/10/02", "junk", 1.5, "00010101", "99991231", "16770921",
                                 "22620412"])
def test_to_datetime_days_refuses_numbers_formats_and_out_of_range_days(bad):
    values = pd.Series(["2026-10-01", bad], dtype=object)
    with pytest.raises(ValueError):
        cd.to_datetime_days(values)
    coerced = cd.to_datetime_days(values, errors="coerce")
    assert coerced.isna().tolist() == [False, True]


def test_to_datetime_days_never_wraps_out_of_range_days_into_other_years():
    # numpy 的單位換算會把 0001-01-01 靜默變成 1754-08-30；這裡一定是錯誤或 NaT
    assert cd.to_datetime_days(["16770922", "2262-04-11"]).tolist() == [pd.Timestamp("1677-09-22"),
                                                                         pd.Timestamp("2262-04-11")]
    with pytest.raises(ValueError):
        cd.to_datetime_days(["00010101"])
    assert cd.to_datetime_days(["00010101"], errors="coerce").isna().all()


def test_to_datetime_days_passes_datetime64_through_unchanged():
    aware = pd.DatetimeIndex(["2026-10-01 15:30"], tz="Asia/Taipei")
    assert cd.to_datetime_days(aware) is aware
    series = pd.Series(pd.to_datetime(["2026-10-01 15:30", "2026-10-02 00:00"]), index=[3, 4])
    assert cd.to_datetime_days(series) is series
    array = np.array(["2026-10-01T15:30"], dtype="datetime64[ns]")
    assert cd.to_datetime_days(array).equals(pd.DatetimeIndex(array))


def test_to_datetime_days_list_index_and_ndarray_inputs():
    listed = cd.to_datetime_days(["2026-10-01", "20261002", None])
    assert isinstance(listed, pd.DatetimeIndex) and listed[:2].tolist() == [pd.Timestamp("2026-10-01"),
                                                                             pd.Timestamp("2026-10-02")]
    assert pd.isna(listed[2])
    named = cd.to_datetime_days(pd.Index(["2026-10-01", "20261002"], name="session"))
    assert isinstance(named, pd.DatetimeIndex) and named.name == "session"
    array = cd.to_datetime_days(np.array(["2026-10-01", "20261002"]))
    assert array.tolist() == [pd.Timestamp("2026-10-01"), pd.Timestamp("2026-10-02")]
    objects = cd.to_datetime_days([date(2026, 10, 1), pd.Timestamp("2026-10-02 13:00"), np.datetime64("2026-10-03")])
    assert objects.tolist() == [pd.Timestamp("2026-10-01"), pd.Timestamp("2026-10-02"), pd.Timestamp("2026-10-03")]
    strings = cd.to_datetime_days(pd.Series(["2026-10-01", pd.NA, "20261002"], dtype="string"))
    assert strings.isna().tolist() == [False, True, False]
    categories = cd.to_datetime_days(pd.Series(["2026-10-01", "20261002"], dtype="category"))
    assert categories.tolist() == [pd.Timestamp("2026-10-01"), pd.Timestamp("2026-10-02")]
    assert len(cd.to_datetime_days([])) == 0
    with pytest.raises(ValueError):
        cd.to_datetime_days(np.array([20261002, 20261003]))  # 整數陣列（JSON 數字）不是日期
    assert cd.to_datetime_days(np.array([np.nan, np.nan])).isna().all()


def test_to_datetime_days_rejects_scalars_matrices_and_unknown_error_modes():
    for scalar in ("2026-10-02", 20261002, None):
        with pytest.raises(TypeError):
            cd.to_datetime_days(scalar)
    with pytest.raises(TypeError):
        cd.to_datetime_days(np.array([["2026-10-01"], ["2026-10-02"]]))
    with pytest.raises(ValueError):
        cd.to_datetime_days(["2026-10-01"], errors="ignore")


def test_to_datetime_days_on_iso_only_data_equals_pandas_byte_for_byte():
    # 零差異閘門：今天全是 ISO 的資料，換成這個函式之後結果與原本的 pd.to_datetime 完全相同
    iso = pd.Series(["2026-10-01", "2026-10-02", "2026-10-05"] * 4, index=range(100, 112), name="date")
    assert cd.to_datetime_days(iso).equals(pd.to_datetime(iso))
    assert cd.to_datetime_days(pd.Index(iso)).equals(pd.DatetimeIndex(pd.to_datetime(iso)))


def test_to_datetime_days_million_rows_is_fast_and_value_mapped():
    days = [f"2026-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)]
    compact = [day.replace("-", "") for day in days]
    big = pd.Series(np.array((days + compact) * 1500, dtype=object)[:1_000_000])
    started = time.perf_counter()
    out = cd.to_datetime_days(big)
    elapsed = time.perf_counter() - started
    assert len(out) == 1_000_000 and not out.isna().any()
    assert out.iloc[0] == pd.Timestamp("2026-01-01") and out.iloc[len(days)] == pd.Timestamp("2026-01-01")
    assert elapsed < 10.0, elapsed  # 唯一值對照：實測約 0.1 秒；放寬到 10 秒只防退化成逐列解析


def test_canonical_date_tokens_rewrites_pure_dates_only():
    values = ["20261002", "2026-10-02", "2026-10-02T15:30:00+08:00", None, "", "junk", float("nan")]
    out = cd.canonical_date_tokens(values)
    assert isinstance(out, list)
    assert out[:6] == ["2026-10-02", "2026-10-02", "2026-10-02T15:30:00+08:00", None, "", "junk"]
    assert out[6] != out[6]  # NaN 原樣
    assert values[0] == "20261002"  # 原物件不改寫


def test_canonical_date_tokens_series_and_index_keep_their_shape():
    series = pd.Series(["20261002", "2026-10-02 15:30:00"], index=[7, 9], name="available_at")
    out = cd.canonical_date_tokens(series)
    assert isinstance(out, pd.Series) and list(out.index) == [7, 9] and out.name == "available_at"
    assert out.tolist() == ["2026-10-02", "2026-10-02 15:30:00"]
    assert series.tolist() == ["20261002", "2026-10-02 15:30:00"]
    index = cd.canonical_date_tokens(pd.Index(["20261002"], name="d"))
    assert isinstance(index, pd.Index) and index.tolist() == ["2026-10-02"] and index.name == "d"
    stamps = pd.Series(pd.to_datetime(["2026-10-02"]))
    assert cd.canonical_date_tokens(stamps) is stamps


def test_canonical_date_tokens_then_pandas_parses_both_spellings_and_never_makes_1970_from_numbers():
    mixed = pd.Series(["2026-10-01T15:30:00+08:00", "20261002", "2026-10-03"])
    parsed = pd.to_datetime(cd.canonical_date_tokens(mixed), format="mixed", errors="coerce", utc=True)
    assert not parsed.isna().any()
    numbers = pd.Series(["2026-10-01", 20261002, np.int64(5), True], dtype=object)
    tokens = cd.canonical_date_tokens(numbers)
    assert tokens.tolist()[0] == "2026-10-01" and all(str(item).startswith("<not a date:") for item in tokens[1:])
    coerced = pd.to_datetime(tokens, format="mixed", errors="coerce")
    assert coerced.isna().tolist() == [False, True, True, True]
    with pytest.raises((ValueError, TypeError)):
        pd.to_datetime(tokens, format="mixed", errors="raise")


def test_helper_imports_only_the_standard_library_at_import_time():
    # 共用模組在 Telegram 服務與 CLI 啟動時就會匯入：匯入時不可以拉進 pandas／numpy
    source = (SHARED_DIR / "date_format.py").read_text(encoding="utf-8")
    header = source.split("def parse_day", 1)[0]
    assert "import pandas" not in header and "import numpy" not in header
    assert set(cd.__all__) >= {
        "parse_day", "to_day", "format_day", "format_iso", "display_day", "format_clock", "same_day", "latest_day",
        "day_before", "calendar_day_of", "iso_day", "iso_day_or_none", "iso_calendar_day", "cli_day",
        "display_clock", "display_text", "find_day_token", "to_timestamp_day", "to_datetime_days",
        "canonical_date_tokens"}


def test_taipei_offset_is_fixed_plus_eight():
    assert cd._TAIPEI.utcoffset(None) == timedelta(hours=8)
