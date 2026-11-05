"""H63 caption（ADR-0009）：帶 U7 區塊時顯示「跑贏同期多數股票的機率」與 80% 區間；沒有區塊時與舊 caption 相同。

區塊由 single_stock_analysis\\src\\single_stock_h63_xs.py 依 shared 測試快照實際產生（兩元件之間的契約測試）。
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

SUITE_DIR = Path(__file__).resolve().parents[1]
if str(SUITE_DIR) not in sys.path:
    sys.path.insert(0, str(SUITE_DIR))

import telegram_stock_command_bot as bot  # noqa: E402

if str(bot.ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(bot.ANALYSIS_DIR))

import single_stock_h63_xs as h63xs  # noqa: E402
from stock_h63_xs_fixture import write_fixture  # noqa: E402

STOCK = bot.StockMatch("2330", "台積電", "exact_id", 1.0)
TARGET = "目標：之後第63個交易日收盤（約三個月，日期未驗證）"
OLDER_NOTE = "沿用較舊快照（訊號日早於本次分析資料日，落後 1 個平日）"


@pytest.fixture
def xs_root(tmp_path, monkeypatch):
    root = tmp_path / "h63_xs"
    monkeypatch.setenv("STOCK_H63_XS_ROOT", str(root))
    monkeypatch.delenv(h63xs.SOURCE_ENV, raising=False)
    return root


def _payload(block=None, horizon=63):
    payload = {"schema": bot.SELECTED_SINGLE_REPORT_SCHEMA, "research_only": False, "promotion": True,
               "stock": {"stock_id": "2330", "market": "TWSE"}, "horizon_sessions": horizon, "asof": "2026-10-16",
               "prediction": {"prob_up": 0.6123, "expected_return": 0.1234}, "target_session_date": None}
    if block is not None:
        payload["h63_xs"] = block
    return payload


def test_used_caption_replaces_the_retired_estimate(xs_root):
    write_fixture(xs_root, signal_date="2026-10-16")
    block = h63xs.build_h63_block({"stock_id": "2330", "market": "TWSE"}, "2026-10-16")
    caption = bot.selected_report_caption(bot.MODE_H63, STOCK, _payload(block))
    lines = caption.splitlines()
    assert lines[0] == "2330 台積電｜H63 三個月相對強弱"
    # U7 訊號日＝資料日：同一天只寫一次（資料時間併入資料日那一行），不另寫「訊號日」與「資料：」
    assert lines[1].startswith("資料日：20261016（發布 ") and lines[1].endswith(f"）；{TARGET}") and "日期未驗證" in lines[1]
    assert caption.count("20261016") == 1 and "訊號日" not in caption
    assert not any(line.startswith("資料：") for line in lines)
    assert "跑贏同期多數股票的機率：58.4%（相對偏強）" in caption
    assert "三個月報酬80%區間：-21.1%～+33.2%（不是預測報酬）" in caption
    assert bot_disclosure(block) in caption
    assert "上漲機率" not in caption and "預估報酬" not in caption
    assert caption.endswith(bot.REPORT_CAPTION_DISCLAIMER) and len(caption) <= bot.TELEGRAM_CAPTION_BUDGET


def bot_disclosure(block):
    return block["caption_disclosure"]


def test_disclosure_survives_the_caption_budget(xs_root, monkeypatch):
    write_fixture(xs_root, signal_date="2026-10-16")
    block = h63xs.build_h63_block({"stock_id": "2330", "market": "TWSE"}, "2026-10-16")
    monkeypatch.setattr(bot, "_comparison_caption_lines", lambda payload: ["歷史比較" * 80] * 3)
    caption = bot.selected_report_caption(bot.MODE_H63, STOCK, _payload(block))
    assert len(caption) <= bot.TELEGRAM_CAPTION_BUDGET and block["caption_disclosure"] in caption


def test_fallback_and_disabled_captions_show_no_numbers(xs_root):
    fallback = h63xs.build_h63_block({"stock_id": "2330", "market": "TWSE"}, "2026-10-16")
    caption = bot.selected_report_caption(bot.MODE_H63, STOCK, _payload(fallback))
    assert caption.splitlines()[1].startswith("三個月相對強弱暫不可用（尚未產生三個月相對強弱快照）")
    assert "%" not in caption.split(bot.REPORT_CAPTION_DISCLAIMER)[0].replace("80%", "")
    disabled = h63xs.build_h63_block({"stock_id": "2330", "market": "TWSE"}, "2026-10-16", source="off")
    assert "已暫停顯示" in bot.selected_report_caption(bot.MODE_H63, STOCK, _payload(disabled)).splitlines()[1]


def _timed_payload(block, asof="2026-10-16", analysis_asof=None):
    """資料日 asof 的報告：快照 16:00 發布、20261020 08:30 讀取（台北）；analysis_asof 給定時資料時間落在另一天（非最新資料）。"""
    payload = dict(_payload(block), asof=asof, generated_at="2026-10-20T00:30:00+00:00",
                   snapshot_binding={"snapshot_fingerprint": "f" * 64,
                                     "descriptor": {"sources": [], "published_at": asof + "T08:00:00+00:00"}})
    if analysis_asof is not None:
        payload["data_time"] = {"analysis_asof": analysis_asof, "is_stale": True}
    return payload


@pytest.mark.parametrize("analysis_asof", [None, "2026-10-15"], ids=["same_day", "data_time_differs"])
def test_used_caption_writes_the_data_day_once(xs_root, analysis_asof):
    """U7 訊號日＝資料日時不另寫訊號日；資料時間的資料日相同時發布與讀取時間併入資料日那一行，不同時保留兩行。"""
    write_fixture(xs_root, signal_date="2026-10-16")
    block = h63xs.build_h63_block({"stock_id": "2330", "market": "TWSE"}, "2026-10-16")
    caption = bot.selected_report_caption(bot.MODE_H63, STOCK, _timed_payload(block, analysis_asof=analysis_asof))
    lines = caption.splitlines()
    if analysis_asof is None:
        assert lines[1].startswith("資料日：20261016（發布 20261016 16:00，讀取 ") and lines[1].endswith(f"）；{TARGET}")
        assert not any(line.startswith("資料：") for line in lines)
    else:
        assert lines[1] == f"資料日：20261016；{TARGET}"
        assert lines[2].startswith("資料：20261015（發布 20261016 16:00，讀取 ") and lines[2].endswith("［非最新資料］")
    assert lines[2 if analysis_asof is None else 3] == "跑贏同期多數股票的機率：58.4%（相對偏強）"
    assert "訊號日" not in caption and "沿用較舊快照" not in caption
    assert caption.endswith(bot.REPORT_CAPTION_DISCLAIMER) and len(caption) <= bot.TELEGRAM_CAPTION_BUDGET


def test_used_caption_with_an_older_u7_snapshot_lists_the_signal_day_once(xs_root):
    """沿用較舊 U7 快照：訊號日另外列在資料日之後；註記保留「沿用較舊快照」與落後平日數，不再重複兩個日期。"""
    write_fixture(xs_root, signal_date="2026-10-16")
    block = h63xs.build_h63_block({"stock_id": "2330", "market": "TWSE"}, "2026-10-19")
    # 封存區塊的原文不變，只在 caption 顯示時改寫
    assert block["notes_zh"][0] == "沿用較舊快照：訊號日 20261016；本次分析資料日 20261019（落後 1 個平日）"
    caption = bot.selected_report_caption(bot.MODE_H63, STOCK, _timed_payload(block, asof="2026-10-19"))
    lines = caption.splitlines()
    assert lines[1].startswith("資料日：20261019（發布 20261019 16:00，讀取 ")
    assert lines[1].endswith(f"）；訊號日：20261016；{TARGET}")
    assert OLDER_NOTE in lines and caption.count("沿用較舊快照") == 1
    assert caption.count("20261016") == 1 and "本次分析資料日 20261019" not in caption
    assert not any(line.startswith("資料：") for line in lines)
    assert block["caption_disclosure"] in caption
    assert caption.endswith(bot.REPORT_CAPTION_DISCLAIMER) and len(caption) <= bot.TELEGRAM_CAPTION_BUDGET
    # 資料時間又落在另一天：資料日、資料時間兩行都保留，訊號日仍只寫一次，［非最新資料］照列
    lines = bot.selected_report_caption(
        bot.MODE_H63, STOCK, _timed_payload(block, asof="2026-10-19", analysis_asof="2026-10-15")).splitlines()
    assert lines[1] == f"資料日：20261019；訊號日：20261016；{TARGET}"
    assert lines[2].startswith("資料：20261015（發布 20261019 16:00，讀取 ") and lines[2].endswith("［非最新資料］")
    assert OLDER_NOTE in lines


def test_older_u7_snapshot_warning_is_never_dropped(xs_root, monkeypatch):
    """單股層改用其他寫法時照原文顯示；caption 預算截斷時警示與揭露仍在。"""
    write_fixture(xs_root, signal_date="2026-10-16")
    block = h63xs.build_h63_block({"stock_id": "2330", "market": "TWSE"}, "2026-10-19")
    other = copy.deepcopy(block)
    other["notes_zh"][0] = "沿用較舊快照：訊號日 20261016（其他寫法）"
    lines = bot.selected_report_caption(bot.MODE_H63, STOCK, _timed_payload(other, asof="2026-10-19")).splitlines()
    assert other["notes_zh"][0] in lines and OLDER_NOTE not in lines
    monkeypatch.setattr(bot, "_comparison_caption_lines", lambda payload: ["歷史比較" * 80] * 3)
    caption = bot.selected_report_caption(bot.MODE_H63, STOCK, _timed_payload(block, asof="2026-10-19"))
    assert len(caption) <= bot.TELEGRAM_CAPTION_BUDGET and caption.endswith(bot.REPORT_CAPTION_DISCLAIMER)
    assert OLDER_NOTE in caption.splitlines() and block["caption_disclosure"] in caption


@pytest.mark.parametrize("analysis_asof", [None, "2026-10-15"], ids=["same_day", "data_time_differs"])
def test_fallback_caption_writes_the_data_day_once(xs_root, analysis_asof):
    """不顯示數字時：資料日與資料時間同一天併成一行；不同時保留兩行（［非最新資料］照列）。"""
    fallback = h63xs.build_h63_block({"stock_id": "2330", "market": "TWSE"}, "2026-10-16")
    caption = bot.selected_report_caption(bot.MODE_H63, STOCK, _timed_payload(fallback, analysis_asof=analysis_asof))
    lines = caption.splitlines()
    assert lines[1] == "三個月相對強弱暫不可用（尚未產生三個月相對強弱快照），本次不顯示 H63 數字（h63xs_no_latest）。"
    if analysis_asof is None:
        assert lines[2].startswith("資料日：20261016（發布 20261016 16:00，讀取 ") and lines[2].endswith("）")
        assert not any(line.startswith("資料：") for line in lines)
    else:
        assert lines[2] == "資料日：20261016"
        assert lines[3].startswith("資料：20261015（發布 20261016 16:00，讀取 ") and lines[3].endswith("［非最新資料］")
    assert "%" not in caption.split(bot.REPORT_CAPTION_DISCLAIMER)[0]
    assert caption.endswith(bot.REPORT_CAPTION_DISCLAIMER) and len(caption) <= bot.TELEGRAM_CAPTION_BUDGET


def test_without_block_the_previous_caption_is_unchanged():
    caption = bot.selected_report_caption(bot.MODE_H63, STOCK, _payload())
    assert caption.splitlines()[0] == "2330 台積電｜第63交易日收盤估計"
    assert "上漲機率：61.23%；預估報酬：+12.34%" in caption


@pytest.mark.parametrize("mutate", [
    lambda b: b.update(p=1.5), lambda b: b.update(stock_id="2317"), lambda b: b.update(schema="other"),
    lambda b: b["band"].update(q10=0.05), lambda b: b.update(caption_disclosure=""),
])
def test_malformed_blocks_never_deliver(xs_root, mutate):
    write_fixture(xs_root, signal_date="2026-10-16")
    block = h63xs.build_h63_block({"stock_id": "2330", "market": "TWSE"}, "2026-10-16")
    bad = copy.deepcopy(block)
    mutate(bad)
    with pytest.raises(bot.AnalysisProcessError):
        bot.selected_report_caption(bot.MODE_H63, STOCK, _payload(bad))


def test_h1_caption_ignores_the_h63_block(xs_root):
    write_fixture(xs_root, signal_date="2026-10-16")
    block = h63xs.build_h63_block({"stock_id": "2330", "market": "TWSE"}, "2026-10-16")
    plain = bot.selected_report_caption(bot.MODE_H1, STOCK, _payload(horizon=1))
    with_block = bot.selected_report_caption(bot.MODE_H1, STOCK, _payload(block, horizon=1))
    assert plain == with_block


def test_help_texts_name_the_relative_strength_product():
    assert "H63 三個月相對強弱" in bot.HELP_TEXT and "不是上漲機率" in bot.HELP_TEXT
    assert "H63三個月相對強弱" in bot.READY_TEXT
