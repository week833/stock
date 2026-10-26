"""H1 caption 顯示新來源：開盤前版（主要）＋ C0 基準（備援），或回退原因；H63 與舊報告不變。

區塊由 single_stock_analysis\\src\\single_stock_preopen.py 依 shared 假快照實際產生（兩元件之間的契約測試）。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

SUITE_DIR = Path(__file__).resolve().parents[1]
if str(SUITE_DIR) not in sys.path:
    sys.path.insert(0, str(SUITE_DIR))

import telegram_stock_command_bot as bot  # noqa: E402

if str(bot.ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(bot.ANALYSIS_DIR))

import single_stock_preopen as preopen  # noqa: E402
from stock_daily_preopen_fixture import write_fixture  # noqa: E402

STOCK = bot.StockMatch("2330", "台積電", "exact_id", 1.0)
HEAD_SHA = "d" * 64


@pytest.fixture
def snapshot_root(tmp_path, monkeypatch):
    root = tmp_path / "preopen"
    monkeypatch.setenv("STOCK_DAILY_PREOPEN_ROOT", str(root))
    monkeypatch.delenv(preopen.SOURCE_ENV, raising=False)
    return root


def _block(snapshot_root, *, signal="2026-10-16", expected="2026-10-16", label=preopen.C0_BASE_LABEL, gate=True):
    write_fixture(snapshot_root, signal_date=signal, target_date="2026-10-19", gate_us_up=gate)
    return preopen.build_h1_block({"stock_id": "2330", "market": "TWSE"}, expected, base_label=label)


def _c0_payload(**overrides):
    payload = {
        "report_schema_version": bot.C0_SINGLE_REPORT_SCHEMA, "report_class": bot.C0_ESTIMATE_REPORT_CLASS,
        "stock": {"stock_id": "2330", "stock_name": "台積電"}, "horizon_sessions": 1,
        "latest_data_date": "2026-10-16", "target_session_date": "2026-10-19",
        "prediction": {"prediction_status": "predicted", "prob_up": 0.5, "expected_return": 0.0,
                       "probability_constant_across_stocks": True, "expected_return_zero_baseline": True},
        "comparison_metrics": {"direction_accuracy": 0.559},
        "data_time": {"analysis_asof": "2026-10-16", "read_at_utc": "2026-10-17T00:35:00+00:00",
                      "head": {"published_at": "2026-10-16T08:00:00+00:00"}},
        "published_snapshot": {"snapshot_fingerprint": "f" * 64, "sources": [{"manifest_sha256": HEAD_SHA}]},
        "qa_override": False,
    }
    payload.update(overrides)
    return payload


def _selected_payload(horizon=1, **overrides):
    payload = {
        "schema": bot.SELECTED_SINGLE_REPORT_SCHEMA, "research_only": False, "promotion": True,
        "stock": {"stock_id": "2330", "stock_name": "台積電", "market": "TWSE"},
        "horizon_sessions": horizon, "asof": "2026-10-16", "target_session_date": "2026-10-19",
        "prediction": {"status": "predicted", "prob_up": 0.6123, "expected_return": 0.0123},
        "snapshot_binding": {"head_sha256": HEAD_SHA, "snapshot_fingerprint": "f" * 64, "descriptor": {"sources": []}},
        "generated_at": "2026-10-17T00:30:00+00:00",
    }
    payload.update(overrides)
    return payload


def _assert_disclosure(caption):
    for needle in ("揭露：預測下一交易日收盤相對訊號日收盤的方向", "方向準確率59.0%", "一律猜跌55.9%", "AUC 0.608",
                   "美股收漲日可交易Top10命中63.5%", "全部日子55.6%", "-0.04%／日", "命中率不代表交易獲利"):
        assert needle in caption, needle


# v1（含沒有 recipe 欄位的舊快照）的揭露行：與改版前逐字相同。
V1_DISCLOSURE_LINE = ("揭露：預測下一交易日收盤相對訊號日收盤的方向；保留期（2026-01～2026-10）方向準確率59.0%（一律猜跌55.9%）、"
                      "AUC 0.608；美股收漲日可交易Top10命中63.5%，全部日子55.6%；隔日開盤買進Top10平均約-0.04%／日（為負），"
                      "命中率不代表交易獲利。")
V2_RECIPE = {"name": "h1pre_v2_ens", "frozen_sha256": "84343f31a101ac30c04a2ec997d89f4517ccc2623a3843918a93ca0671409882"}
V1_RECIPE = {"name": "h1pre_final_nogc_xs", "frozen_sha256": "e53c2416b63d8273162c3618564813cd13145052e5ac30f5c3c375fb48c3b28f"}
V2_NEEDLES = ("揭露：預測下一交易日收盤相對訊號日收盤的方向", "保留期（20260102～20261019，191個交易日）方向準確率60.0%",
              "一律猜跌56.1%", "AUC 0.619", "美股收漲日可交易Top10命中65.3%", "其他日子49.1%", "全部日子57.1%",
              "Top10預估報酬誤差（MAE）2.71%", "隔日開盤買進Top10平均約+0.01%／日（約為零）", "命中率不代表交易獲利")


def _without_recipe(block):
    """舊快照（manifest 沒有 recipe）：區塊的 meta 摘要與 disclosure 都沒有 recipe。"""
    block = copy.deepcopy(block)
    for holder in ("meta", "disclosure"):
        if isinstance(block.get(holder), dict):
            block[holder].pop("recipe", None)
    return block


def _with_recipe(block, recipe, where="meta"):
    """模擬改版後的單股區塊：把快照 manifest 的 recipe 只帶進 meta 摘要（或只帶進 disclosure）。"""
    block = _without_recipe(block)
    block.setdefault(where, {})["recipe"] = recipe
    return block


def _disclosure_line(caption):
    return next(line for line in caption.splitlines() if line.startswith("揭露："))


def _assert_v2_disclosure(caption):
    for needle in V2_NEEDLES:
        assert needle in caption, needle
    for v1_only in ("59.0%", "55.9%", "0.608", "63.5%", "55.6%", "-0.04%", "為負", "2026-01-02", "2026-10-19"):
        assert v1_only not in caption, v1_only


def test_c0_h1_caption_shows_preopen_as_primary_and_c0_as_backup(snapshot_root):
    block = _block(snapshot_root)
    caption = bot.report_caption(bot.MODE_H1, STOCK, _c0_payload(preopen_h1=block, h1_display_source="preopen"))
    lines = caption.splitlines()
    assert lines[0] == "2330 台積電｜H1 收盤估計（開盤前版）"
    assert lines[1] == "訊號收盤日：20261016；到期日：20261019"
    assert lines[2].startswith("資料：20261016")
    assert f"開盤前版：上漲機率 {100 * block['p_up']:.2f}%；預估收盤報酬 {100 * block['exp_ret']:+.2f}%" in caption
    assert f"名次：全市場 {block['rank']}/{block['universe_rows']}；可交易 {block['rank_tradable']}/{block['tradable_rows']}" in caption
    assert "前一晚美股收漲（建議日）；前一晚美股收盤已納入" in caption
    assert "【測試用假快照" in caption
    _assert_disclosure(caption)
    assert "C0 基準（備援）：上漲機率 50.00%；預估報酬 +0.00%" in caption
    assert "C0 基準比較方向率低於60%" in caption
    assert caption.endswith(bot.REPORT_CAPTION_DISCLAIMER) and len(caption) <= bot.TELEGRAM_CAPTION_BUDGET


def test_c0_h1_caption_fallback_notice_is_right_under_the_title(snapshot_root):
    block = _block(snapshot_root, signal="2026-10-15")
    caption = bot.report_caption(bot.MODE_H1, STOCK, _c0_payload(preopen_h1=block, h1_display_source="c0"))
    lines = caption.splitlines()
    assert lines[0] == "2330 台積電｜C0 H1 收盤估計"
    # 原因代碼後補中文說明（代碼：中文）
    assert lines[1] == "開盤前版不可用（preopen_stale：開盤前版快照的訊號日與本次分析資料日不同），本次 H1 改用 C0 基準"
    assert "上漲機率：50.00%；預估報酬：+0.00%" in caption
    assert "開盤前版：" not in caption and "揭露：" not in caption


def test_selected_h1_caption_used_and_fallback(snapshot_root):
    block = _block(snapshot_root, label=preopen.SELECTED_BASE_LABEL, gate=False)
    caption = bot.report_caption(bot.MODE_H1, STOCK, _selected_payload(preopen_h1=block))
    assert caption.splitlines()[0] == "2330 台積電｜第1交易日收盤估計（開盤前版）"
    assert "前一晚美股未收漲（非建議日）" in caption
    assert "C0 基準備援（選定模型）：上漲機率 61.23%；預估報酬 +1.23%" in caption
    _assert_disclosure(caption)
    assert len(caption) <= bot.TELEGRAM_CAPTION_BUDGET
    stale = _block(snapshot_root, signal="2026-10-15", label=preopen.SELECTED_BASE_LABEL)
    caption = bot.report_caption(bot.MODE_H1, STOCK, _selected_payload(preopen_h1=stale))
    lines = caption.splitlines()
    assert lines[0] == "2330 台積電｜第1交易日收盤估計"
    assert lines[1] == "開盤前版不可用（preopen_stale：開盤前版快照的訊號日與本次分析資料日不同），本次 H1 改用 C0 基準，單股已啟用選定模型"
    assert "上漲機率：61.23%；預估報酬：+1.23%" in caption


def test_no_estimate_h1_caption_with_preopen_row(snapshot_root):
    block = _block(snapshot_root)
    payload = _c0_payload(report_class=bot.C0_NO_ESTIMATE_REPORT_CLASS, prediction=None,
                          no_estimate_reason="stock_price_older_than_asof", stock_last_price_date="2026-10-01",
                          preopen_h1=block)
    caption = bot.report_caption(bot.MODE_H1, STOCK, payload)
    assert caption.splitlines()[0] == "2330 台積電｜H1 收盤估計（開盤前版；C0 基準無法估計）"
    assert "C0 基準無法估計，原因：該股價格資料停在分析資料日之前（stock_price_older_than_asof）" in caption
    assert "開盤前版：上漲機率" in caption and "不含任何估計值" not in caption
    _assert_disclosure(caption)


@pytest.mark.parametrize("reason,expected", [
    ("prediction_unavailable", "現有模型未產生有效估計（prediction_unavailable）"),
    ("some_future_code", "資料或模型條件未滿足（some_future_code）"),
    ("stock_price_older_than_asof,some_future_code",
     "該股價格資料停在分析資料日之前、資料或模型條件未滿足（stock_price_older_than_asof,some_future_code）"),
])
def test_no_estimate_reason_always_has_chinese_text(snapshot_root, reason, expected):
    payload = _c0_payload(report_class=bot.C0_NO_ESTIMATE_REPORT_CLASS, prediction=None,
                          no_estimate_reason=reason, stock_last_price_date="2026-10-01")
    caption = bot.report_caption(bot.MODE_H1, STOCK, payload)
    assert f"原因：{expected}" in caption


def test_forced_c0_caption_states_the_setting(snapshot_root, monkeypatch):
    monkeypatch.setenv(preopen.SOURCE_ENV, "c0")
    block = preopen.build_h1_block({"stock_id": "2330", "market": "TWSE"}, "2026-10-16")
    caption = bot.report_caption(bot.MODE_H1, STOCK, _c0_payload(preopen_h1=block))
    assert caption.splitlines()[1] == "依設定不使用開盤前版（h1_source_forced_c0：依設定強制使用 C0 基準），本次 H1 改用 C0 基準"


@pytest.mark.parametrize("block", [
    None, "used", {"schema": "other", "status": "used"},
    {"schema": bot.PREOPEN_H1_SCHEMA, "status": "used", "p_up": 1.5, "exp_ret": 0.0},
    {"schema": bot.PREOPEN_H1_SCHEMA, "status": "fallback", "notice": " "},
])
def test_malformed_preopen_block_never_delivers(block):
    with pytest.raises(bot.AnalysisProcessError, match="開盤前版"):
        bot.report_caption(bot.MODE_H1, STOCK, _c0_payload(preopen_h1=block))
    with pytest.raises(bot.AnalysisProcessError, match="開盤前版"):
        bot.report_caption(bot.MODE_H1, STOCK, _selected_payload(preopen_h1=block))


def test_h63_and_reports_without_block_keep_previous_captions(snapshot_root):
    block = _block(snapshot_root)
    h63 = _selected_payload(63, preopen_h1=block)
    caption = bot.report_caption(bot.MODE_H63, STOCK, h63)
    assert caption.splitlines()[0] == "2330 台積電｜第63交易日收盤估計"
    assert "開盤前版" not in caption
    legacy = bot.report_caption(bot.MODE_H1, STOCK, _c0_payload())
    assert legacy.splitlines()[0] == "2330 台積電｜C0 H1 收盤估計" and "開盤前版" not in legacy


def test_h1_caption_shows_chip_freshness_note_only_when_present(snapshot_root):
    """籌碼未更新或不完整時 caption 多一行（最新到幾號／成功/應有數量）；新鮮時沒有這一行；舊區塊照常傳送。"""
    write_fixture(snapshot_root, signal_date="2026-10-16", target_date="2026-10-19", stale_routes=["finmind_margin"])
    block = preopen.build_h1_block({"stock_id": "2330", "market": "TWSE"}, "2026-10-16", base_label=preopen.C0_BASE_LABEL)
    caption = bot.report_caption(bot.MODE_H1, STOCK, _c0_payload(preopen_h1=block, h1_display_source="preopen"))
    lines = caption.splitlines()
    us_line = next(i for i, line in enumerate(lines) if line.startswith("前一晚美股收漲（建議日）；"))
    assert lines[us_line + 1] == "t 日籌碼未更新：融資券最新到 20261015（以舊值推論，準確度可能下降）"
    assert "C0 基準（備援）" in caption and "命中率不代表交易獲利" in caption
    assert caption.endswith(bot.REPORT_CAPTION_DISCLAIMER) and len(caption) <= bot.TELEGRAM_CAPTION_BUDGET
    write_fixture(snapshot_root, signal_date="2026-10-16", target_date="2026-10-19", partial_routes=["finmind_institutional"])
    block = preopen.build_h1_block({"stock_id": "2330", "market": "TWSE"}, "2026-10-16", base_label=preopen.C0_BASE_LABEL)
    caption = bot.report_caption(bot.MODE_H1, STOCK, _c0_payload(preopen_h1=block, h1_display_source="preopen"))
    assert "t 日籌碼資料不完整（中央回報成功/應有）：法人 100/1215（缺的以舊值推論，準確度可能下降）" in caption
    assert "C0 基準（備援）" in caption and len(caption) <= bot.TELEGRAM_CAPTION_BUDGET
    fresh = _block(snapshot_root)
    caption = bot.report_caption(bot.MODE_H1, STOCK, _c0_payload(preopen_h1=fresh, h1_display_source="preopen"))
    assert "籌碼" not in caption
    # 改版前封存的區塊沒有 chips_note_zh 鍵：照常傳送、不加行
    legacy = dict(fresh)
    legacy.pop("chips_note_zh", None)
    caption = bot.report_caption(bot.MODE_H1, STOCK, _c0_payload(preopen_h1=legacy, h1_display_source="preopen"))
    assert "籌碼" not in caption and "開盤前版：上漲機率" in caption


# ------------------------------------------------------------------------------------------------------------------
# 揭露依快照配方（manifest recipe.name）選擇：沒有 recipe＝v1（逐字不變）、v2 新數字、未知配方中性說明
# ------------------------------------------------------------------------------------------------------------------
def test_disclosure_table_is_keyed_by_recipe_name():
    assert set(bot.PREOPEN_DISCLOSURE_BY_RECIPE) == {"h1pre_final_nogc_xs", "h1pre_v2_ens"}
    assert bot.PREOPEN_RECIPE_V1 == V1_RECIPE["name"] and bot.PREOPEN_RECIPE_V2 == V2_RECIPE["name"]
    assert bot.PREOPEN_DISCLOSURE_BY_RECIPE[bot.PREOPEN_RECIPE_V2]["facts"]["source"].endswith("H1改良研究報告_20261021.md")


@pytest.mark.parametrize("where,recipe", [(None, None), ("meta", None), ("meta", V1_RECIPE),
                                          ("disclosure", "h1pre_final_nogc_xs"), ("disclosure", V1_RECIPE)])
def test_v1_disclosure_is_unchanged_without_or_with_v1_recipe(snapshot_root, where, recipe):
    """沒有 recipe 欄位（或為 null）的舊快照與明確 v1 快照：揭露行與改版前逐字相同（數字仍取自封存區塊）。"""
    block = _without_recipe(_block(snapshot_root)) if where is None else _with_recipe(_block(snapshot_root), recipe, where)
    for payload in (_c0_payload(preopen_h1=block, h1_display_source="preopen"), _selected_payload(preopen_h1=block)):
        caption = bot.report_caption(bot.MODE_H1, STOCK, payload)
        assert _disclosure_line(caption) == V1_DISCLOSURE_LINE
        assert "不顯示保留期驗證數字" not in caption and len(caption) <= bot.TELEGRAM_CAPTION_BUDGET


@pytest.mark.parametrize("where,recipe", [("meta", V2_RECIPE), ("disclosure", V2_RECIPE), ("disclosure", "h1pre_v2_ens")])
def test_v2_recipe_shows_v2_numbers_and_no_negative_wording(snapshot_root, where, recipe):
    block = _with_recipe(_block(snapshot_root), recipe, where=where)
    caption = bot.report_caption(bot.MODE_H1, STOCK, _c0_payload(preopen_h1=block, h1_display_source="preopen"))
    _assert_v2_disclosure(caption)
    # 揭露變長後其餘行仍在 caption 預算內（C0 備援與比較方向率警語不被截掉）
    assert "C0 基準（備援）：上漲機率 50.00%；預估報酬 +0.00%" in caption and "C0 基準比較方向率低於60%" in caption
    assert caption.endswith(bot.REPORT_CAPTION_DISCLAIMER) and len(caption) <= bot.TELEGRAM_CAPTION_BUDGET
    caption = bot.report_caption(bot.MODE_H1, STOCK, _selected_payload(preopen_h1=block))
    _assert_v2_disclosure(caption)
    assert "C0 基準備援（選定模型）：上漲機率 61.23%；預估報酬 +1.23%" in caption and "歷史時點完整性與交易效益未驗證。" in caption
    assert caption.endswith(bot.REPORT_CAPTION_DISCLAIMER) and len(caption) <= bot.TELEGRAM_CAPTION_BUDGET


def test_v2_recipe_on_no_estimate_caption(snapshot_root):
    block = _with_recipe(_block(snapshot_root), V2_RECIPE)
    payload = _c0_payload(report_class=bot.C0_NO_ESTIMATE_REPORT_CLASS, prediction=None,
                          no_estimate_reason="stock_price_older_than_asof", stock_last_price_date="2026-10-01",
                          preopen_h1=block)
    caption = bot.report_caption(bot.MODE_H1, STOCK, payload)
    _assert_v2_disclosure(caption)
    assert "歷史PIT／公司行動／歷史普通股資格未驗證。" in caption and len(caption) <= bot.TELEGRAM_CAPTION_BUDGET


def test_v2_uses_its_own_numbers_even_if_the_block_still_carries_v1_facts(snapshot_root):
    """配方是 v2 時一律用 v2 的數字（區塊 disclosure 若仍是 v1 數字也不混用）。"""
    block = _with_recipe(_block(snapshot_root), V2_RECIPE)
    assert block["disclosure"]["holdout_direction_accuracy"] == 0.590  # 未改版的單股區塊
    _assert_v2_disclosure(bot.report_caption(bot.MODE_H1, STOCK, _c0_payload(preopen_h1=block)))


def test_meta_recipe_wins_over_disclosure_recipe(snapshot_root):
    block = _with_recipe(_block(snapshot_root), V1_RECIPE)
    block["disclosure"]["recipe"] = V2_RECIPE
    caption = bot.report_caption(bot.MODE_H1, STOCK, _c0_payload(preopen_h1=block))
    assert _disclosure_line(caption) == V1_DISCLOSURE_LINE


@pytest.mark.parametrize("where,recipe,shown", [
    ("meta", {"name": "h1pre_v3_future", "frozen_sha256": "0" * 64}, "h1pre_v3_future"),
    ("meta", "h1pre_v3_future", "h1pre_v3_future"),
    ("meta", "h1pre_v2_ens", "h1pre_v2_ens"),       # manifest 的 recipe 必須是 {"name": ...}；字串視為格式不符
    ("meta", {"name": "  bad\nname\x00x  "}, "bad?name?x"),
    ("meta", {"name": "配方二"}, "???"),
    ("meta", {"name": "x" * 100}, "x" * 64),
    ("meta", {"frozen_sha256": "0" * 64}, "未提供"),
    ("meta", {"name": 3}, "3"),
    ("meta", {"name": ""}, "未提供"),
    ("meta", ["h1pre_v2_ens"], "['h1pre_v2_ens']"),
    ("disclosure", "h1pre_v9", "h1pre_v9"),
    ("disclosure", {"name": "h1pre_v9"}, "h1pre_v9"),
])
def test_unknown_recipe_shows_neutral_text_with_the_name(snapshot_root, where, recipe, shown):
    block = _with_recipe(_block(snapshot_root), recipe, where)
    for payload in (_c0_payload(preopen_h1=block, h1_display_source="preopen"), _selected_payload(preopen_h1=block)):
        caption = bot.report_caption(bot.MODE_H1, STOCK, payload)
        assert _disclosure_line(caption) == (
            "揭露：預測下一交易日收盤相對訊號日收盤的方向；"
            f"開盤前版配方「{shown}」未知，不顯示保留期驗證數字；命中率不代表交易獲利。")
        for number in ("方向準確率", "AUC", "Top10命中", "隔日開盤買進", "為負", "59.0%", "60.0%"):
            assert number not in _disclosure_line(caption), number
        assert caption.endswith(bot.REPORT_CAPTION_DISCLAIMER) and len(caption) <= bot.TELEGRAM_CAPTION_BUDGET


@pytest.mark.parametrize("value,expected", [
    (-0.0004, "約-0.04%／日（為負），"),   # 與 v1 紀錄相同：照舊
    (-0.0002, "約-0.02%／日（為負），"),
    (0.0, "約0.00%／日（約為零），"),
    (0.0001, "約0.01%／日（為正），"),
    (0.0003, "約0.03%／日（為正），"),
    (None, "約未提供／日（為負），"),     # 未提供：與改版前相同
])
def test_open_entry_wording_follows_the_sign_of_the_number(snapshot_root, value, expected):
    """v1／舊快照的區塊數字若不是負的，不再固定寫「（為負）」。"""
    block = _block(snapshot_root)
    if value is None:
        block["disclosure"].pop("next_open_buy_top10_mean_daily_return")
    else:
        block["disclosure"]["next_open_buy_top10_mean_daily_return"] = value
    caption = bot.report_caption(bot.MODE_H1, STOCK, _c0_payload(preopen_h1=block))
    assert "隔日開盤買進Top10平均" + expected + "命中率不代表交易獲利。" in caption
    if value is not None and value >= 0:
        assert "為負" not in caption


def _block_from_manifest(snapshot_root, recipe):
    """假快照的 latest.json 加上 recipe（None＝不加，即舊快照），再由單股 build_h1_block 實際產生區塊。"""
    write_fixture(snapshot_root, signal_date="2026-10-16", target_date="2026-10-19")
    if recipe is not None:
        pointer = snapshot_root / "predictions" / "latest.json"
        meta = json.loads(pointer.read_text(encoding="utf-8"))
        meta["recipe"] = recipe
        pointer.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return preopen.build_h1_block({"stock_id": "2330", "market": "TWSE"}, "2026-10-16", base_label=preopen.C0_BASE_LABEL)


@pytest.mark.parametrize("recipe,kind", [(None, "v1"), (V1_RECIPE, "v1"), (V2_RECIPE, "v2"),
                                         ({"name": "h1pre_v3_future", "frozen_sha256": "0" * 64}, "unknown")])
def test_end_to_end_manifest_recipe_through_single_stock_block(snapshot_root, recipe, kind):
    """快照 manifest 的 recipe 經單股 build_h1_block 帶進區塊後的 caption；單股尚未帶出 recipe（未部署）時略過。"""
    block = _block_from_manifest(snapshot_root, recipe)
    assert block["status"] == "used"
    if recipe is not None and (block.get("meta") or {}).get("recipe") != recipe:
        pytest.skip("single_stock_preopen 尚未把 manifest 的 recipe 帶進區塊 meta（單股配方揭露尚未部署）")
    for payload in (_c0_payload(preopen_h1=block, h1_display_source="preopen"), _selected_payload(preopen_h1=block)):
        caption = bot.report_caption(bot.MODE_H1, STOCK, payload)
        if kind == "v1":
            assert _disclosure_line(caption) == V1_DISCLOSURE_LINE
        elif kind == "v2":
            _assert_v2_disclosure(caption)
        else:
            assert _disclosure_line(caption).endswith("開盤前版配方「h1pre_v3_future」未知，不顯示保留期驗證數字；命中率不代表交易獲利。")
        assert caption.endswith(bot.REPORT_CAPTION_DISCLAIMER) and len(caption) <= bot.TELEGRAM_CAPTION_BUDGET


def test_fallback_blocks_never_show_a_disclosure_whatever_the_recipe(snapshot_root):
    block = _block(snapshot_root, signal="2026-10-15")
    block["observed_meta"] = {**block.get("observed_meta", {}), "recipe": V2_RECIPE}
    caption = bot.report_caption(bot.MODE_H1, STOCK, _c0_payload(preopen_h1=block, h1_display_source="c0"))
    assert "揭露：" not in caption and "配方" not in caption
