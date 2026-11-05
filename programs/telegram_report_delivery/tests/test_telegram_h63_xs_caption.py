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
    assert "訊號日：20261016" in lines[1] and "日期未驗證" in lines[1]
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
