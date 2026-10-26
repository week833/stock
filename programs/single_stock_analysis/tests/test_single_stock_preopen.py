"""H1 開盤前版疊加：新鮮度、回退原因、來源開關、C0 與選定模型兩條路徑的封存與呈現。

所有快照都由 shared\\stock_daily_preopen_fixture.write_fixture 寫在 tmp_path；不讀正式快照根目錄。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

import single_stock_preopen as preopen
import stock_candidate_report as report
import tw_single_stock_c0 as single
from stock_daily_preopen_fixture import write_fixture

# The real renderer, captured before any fixture replaces it with a byte stub.
REAL_C0_PDF = single._pdf

STOCK_6488 = {"stock_id": "6488", "stock_name": "環球晶", "market": "TPEx"}
STOCK_2330 = {"stock_id": "2330", "stock_name": "台積電", "market": "TWSE"}

from test_single_current_c0 import current_case  # noqa: E402,F401  (shared C0 adapter fixture)
from test_stock_candidate_report import formal_payload, payload  # noqa: E402,F401


@pytest.fixture
def snapshot_root(tmp_path, monkeypatch):
    root = tmp_path / "preopen"
    monkeypatch.setenv("STOCK_DAILY_PREOPEN_ROOT", str(root))
    return root


def _native_json(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


# 快照 manifest（latest.json）的 recipe 欄位：舊快照沒有（_ABSENT）＝ v1；暫存產製程式寫 {"name", "frozen_sha256"}。
_ABSENT = object()
V1_RECIPE = {"name": "h1pre_final_nogc_xs", "frozen_sha256": "e53c2416b63d8273162c3618564813cd13145052e5ac30f5c3c375fb48c3b28f"}
V2_RECIPE = {"name": "h1pre_v2_ens", "frozen_sha256": "84343f31a101ac30c04a2ec997d89f4517ccc2623a3843918a93ca0671409882"}
UNKNOWN_RECIPE = {"name": "h1pre_v9_future", "frozen_sha256": "0" * 64}
V1_NEEDLES = ("59.0%", "55.9%", "0.608", "63.5%", "55.6%", "-0.04%", "平均報酬為負", "校正報告_20261018.md")
V2_NEEDLES = ("60.0%", "56.1%", "0.619", "65.3%", "49.1%", "57.1%", "+0.01%", "2.71%", "191 個交易日",
              "H1改良研究報告_20261021.md")


def _set_recipe(root, recipe):
    """改寫 latest.json 的 recipe 欄位（_ABSENT：移除，即舊快照）；snapshot_sha256 只涵蓋 parquet，不受影響。"""
    latest = root / "predictions" / "latest.json"
    meta = json.loads(latest.read_text(encoding="utf-8"))
    meta.pop("recipe", None)
    if recipe is not _ABSENT:
        meta["recipe"] = recipe
    latest.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


# ---------------------------------------------------------------- source switch


def test_source_switch_precedence_and_validation(monkeypatch):
    monkeypatch.delenv(preopen.SOURCE_ENV, raising=False)
    assert preopen.resolve_source() == "auto"
    monkeypatch.setenv(preopen.SOURCE_ENV, " C0 ")
    assert preopen.resolve_source() == "c0"
    assert preopen.resolve_source("preopen") == "preopen"  # 命令列優先
    with pytest.raises(ValueError, match="h1_source_invalid"):
        preopen.resolve_source("mix")
    monkeypatch.setenv(preopen.SOURCE_ENV, "bogus")
    with pytest.raises(ValueError, match="h1_source_invalid:bogus") as caught:
        preopen.resolve_source()
    assert str(caught.value) == "H1 來源設定無效：bogus（僅接受 auto、preopen、c0）：h1_source_invalid:bogus"


# ---------------------------------------------------------------- block contents and fallbacks


def test_fresh_snapshot_row_is_used_with_ranks_gate_describe_and_disclosure(snapshot_root):
    meta = write_fixture(snapshot_root, signal_date="2026-10-16", target_date="2026-10-19")
    block = preopen.build_h1_block(STOCK_6488, "2026-10-16", base_target_date="2026-10-19")
    import stock_daily_preopen_reader as reader
    snap = reader.load_latest(snapshot_root, expected_signal_date="2026-10-16")
    row = snap.row("6488")
    assert block["status"] == "used" and block["display_source"] == "preopen" and block["reason"] is None
    assert block["p_up"] == pytest.approx(row["p_up"]) and block["exp_ret"] == pytest.approx(row["exp_ret"])
    assert block["rank"] == int(row["rank"]) and block["universe_rows"] == len(snap.frame)
    assert block["rank_tradable"] == int(row["rank_tradable"])
    assert block["tradable_rows"] == int(snap.frame["tradable"].sum())
    assert block["in_tradable_top10"] is (row["rank_tradable"] <= 10)
    assert block["gate_us_up"] is True and block["gate_us_up_zh"] == "前一晚美股收漲（建議日）"
    assert block["us_overnight_status"] == "ok"
    assert block["describe"] == reader.describe(meta)
    assert block["meta"]["run_id"] == meta["run_id"] and block["meta"]["snapshot_sha256"] == meta["snapshot_sha256"]
    assert block["target_date"] == "2026-10-19" and block["target_date_consistent"] is True
    assert block["fixture"] is True
    facts = block["disclosure"]
    assert (facts["holdout_direction_accuracy"], facts["always_down_accuracy"], facts["holdout_auc"]) == (0.590, 0.559, 0.608)
    assert (facts["us_up_day_tradable_top10_hit_rate"], facts["all_days_tradable_top10_hit_rate"]) == (0.635, 0.556)
    assert facts["next_open_buy_top10_mean_daily_return"] < 0
    assert "校正報告_20261018.md" in facts["source"]
    text = json.dumps(block["disclosure_rows"], ensure_ascii=False)
    for needle in ("59.0%", "55.9%", "0.608", "63.5%", "55.6%", "-0.04%", "命中率不代表交易獲利", "下一交易日收盤相對訊號日收盤"):
        assert needle in text
    assert _native_json(block) == block  # 封存摘要需要原生 JSON 樹


# ---------------------------------------------------------------- disclosure by snapshot recipe


def test_v1_disclosure_is_todays_exact_text():
    """v1（含沒有 recipe 欄位的舊快照）的揭露與改版前逐字相同。"""
    assert (preopen.RECIPE_V1, preopen.RECIPE_V2) == ("h1pre_final_nogc_xs", "h1pre_v2_ens")
    assert preopen.DISCLOSURE == {
        "target_zh": "下一交易日收盤相對訊號日收盤的方向（收盤對收盤）",
        "holdout_period": "2026-01～2026-10",
        "holdout_direction_accuracy": 0.590,
        "always_down_accuracy": 0.559,
        "holdout_auc": 0.608,
        "us_up_day_tradable_top10_hit_rate": 0.635,
        "all_days_tradable_top10_hit_rate": 0.556,
        "next_open_buy_top10_mean_daily_return": -0.0004,
        "source": str(preopen.CALIBRATION_REPORT),
    }
    assert preopen.DISCLOSURE_ROWS == (
        ("預測目標", "下一交易日收盤相對訊號日收盤的方向（收盤對收盤），不是開盤價、盤中價格或可成交報酬。"),
        ("保留期方向準確率", "2026-01～10 保留期 59.0%（一律猜跌 55.9%），AUC 0.608。"),
        ("可交易 Top10 命中率", "只在前一晚美股收漲日選出的可交易 Top10 命中 63.5%；不分日子為 55.6%。"),
        ("交易提醒", "若在隔日開盤買進，Top10 平均報酬為負（約 -0.04%／日）；命中率不代表交易獲利。"),
        ("資料出處", str(preopen.CALIBRATION_REPORT)),
    )
    assert preopen.CAPTION_DISCLOSURE == ("揭露：預測下一交易日收盤相對訊號日收盤的方向；保留期（2026-01～10）方向準確率59.0%"
                                          "（一律猜跌55.9%）、AUC 0.608；美股收漲日可交易Top10命中63.5%，全部日子55.6%；"
                                          "隔日開盤買進Top10平均約-0.04%／日，命中率不代表交易獲利。")
    assert str(preopen.CALIBRATION_REPORT).endswith("calibration_20261018\\report\\校正報告_20261018.md")
    assert preopen.DISCLOSURES[preopen.RECIPE_V1] == {
        "facts": preopen.DISCLOSURE, "rows": preopen.DISCLOSURE_ROWS, "caption": preopen.CAPTION_DISCLOSURE}


@pytest.mark.parametrize("meta,recipe,known", [
    ({}, "h1pre_final_nogc_xs", True),                                    # 舊快照：沒有 recipe 欄位
    ({"recipe": None}, "h1pre_final_nogc_xs", True),
    ({"recipe": V1_RECIPE}, "h1pre_final_nogc_xs", True),
    ({"recipe": V2_RECIPE}, "h1pre_v2_ens", True),
    ({"recipe": UNKNOWN_RECIPE}, "h1pre_v9_future", False),
    ({"recipe": {"frozen_sha256": "0" * 64}}, "未提供", False),            # 缺 name
    ({"recipe": {"name": " "}}, "未提供", False),
    ({"recipe": "h1pre_v2_ens"}, "h1pre_v2_ens", False),                  # 不是 dict：格式不符，不猜
    ({"recipe": {"name": "配方\n\x00x"}}, "????x", False),                # 只顯示可列印 ASCII
    ({"recipe": {"name": "v" * 100}}, "v" * 64, False),                   # 最長 64 字
], ids=["absent", "null", "v1", "v2", "unknown", "no_name", "blank", "not_dict", "non_ascii", "long"])
def test_disclosure_for_picks_the_entry_by_recipe_name(meta, recipe, known):
    chosen = preopen.disclosure_for(meta)
    assert chosen["recipe"] == recipe and chosen["known"] is known
    text = json.dumps([chosen["rows"], chosen["caption"]], ensure_ascii=False)
    if recipe == preopen.RECIPE_V1:
        assert (chosen["facts"], chosen["rows"], chosen["caption"]) == (
            preopen.DISCLOSURE, preopen.DISCLOSURE_ROWS, preopen.CAPTION_DISCLOSURE)
    elif known:
        assert (chosen["facts"], chosen["rows"], chosen["caption"]) == (
            preopen.DISCLOSURE_V2, preopen.DISCLOSURE_ROWS_V2, preopen.CAPTION_DISCLOSURE_V2)
        for needle in ("60.0%", "56.1%", "0.619", "65.3%", "49.1%", "57.1%", "+0.01%"):
            assert needle in chosen["caption"]
    else:
        assert chosen["facts"] == {"target_zh": preopen.DISCLOSURE["target_zh"], "recipe": recipe, "recipe_known": False}
        assert f"配方「{recipe}」" in chosen["rows"][1][1] and "配方未知" in chosen["rows"][1][1]
        assert f"配方「{recipe}」未知" in chosen["caption"] and "命中率不代表交易獲利" in chosen["caption"]
        for needle in (*V1_NEEDLES, *V2_NEEDLES):
            assert needle not in text  # 不顯示也不沿用任何配方的數字
        assert recipe == "未提供" or (len(recipe) <= 64 and all(" " <= ch <= "~" for ch in recipe))


@pytest.mark.parametrize("case", ["absent", "v1", "v2", "unknown"])
def test_block_disclosure_follows_the_snapshot_recipe(snapshot_root, case):
    """揭露依快照 manifest 的 recipe.name 選擇；除揭露與 meta.recipe 外，區塊內容完全不變。"""
    recipe = {"absent": _ABSENT, "v1": V1_RECIPE, "v2": V2_RECIPE, "unknown": UNKNOWN_RECIPE}[case]
    write_fixture(snapshot_root, signal_date="2026-10-16", target_date="2026-10-19")
    baseline = preopen.build_h1_block(STOCK_6488, "2026-10-16", base_target_date="2026-10-19")
    _set_recipe(snapshot_root, recipe)
    block = preopen.build_h1_block(STOCK_6488, "2026-10-16", base_target_date="2026-10-19")
    assert block["status"] == "used" and block["display_source"] == "preopen" and block["reason"] is None

    def rest(item):
        return {key: value for key, value in item.items() if key not in ("disclosure", "disclosure_rows", "meta")}

    assert rest(block) == rest(baseline)
    assert {k: v for k, v in block["meta"].items() if k != "recipe"} == {
        k: v for k, v in baseline["meta"].items() if k != "recipe"}
    if recipe is _ABSENT:
        assert "recipe" not in block["meta"]  # 舊快照：meta 與改版前相同
    else:
        assert block["meta"]["recipe"] == recipe  # _META_KEYS 保留 recipe
    facts, text = block["disclosure"], json.dumps(block["disclosure_rows"], ensure_ascii=False)
    if case in ("absent", "v1"):
        assert facts == preopen.DISCLOSURE and block["disclosure_rows"] == [list(row) for row in preopen.DISCLOSURE_ROWS]
        assert facts["next_open_buy_top10_mean_daily_return"] < 0
        present, missing = V1_NEEDLES, V2_NEEDLES
    elif case == "v2":
        assert facts == preopen.DISCLOSURE_V2
        assert block["disclosure_rows"] == [list(row) for row in preopen.DISCLOSURE_ROWS_V2]
        assert (facts["holdout_direction_accuracy"], facts["always_down_accuracy"], facts["holdout_auc"]) == (0.600, 0.561, 0.619)
        assert (facts["us_up_day_tradable_top10_hit_rate"], facts["other_days_tradable_top10_hit_rate"],
                facts["all_days_tradable_top10_hit_rate"]) == (0.653, 0.491, 0.571)
        assert facts["next_open_buy_top10_mean_daily_return"] >= 0  # 約 +0.01%／日：約為零，不是負值
        assert (facts["tradable_top10_mae"], facts["holdout_sessions"], facts["holdout_period"]) == (
            0.0271, 191, "2026-01-02～2026-10-19")
        assert facts["recipe"] == "h1pre_v2_ens" and "H1改良研究報告_20261021.md" in facts["source"]
        present, missing = V2_NEEDLES, V1_NEEDLES
    else:
        assert facts == {"target_zh": preopen.DISCLOSURE["target_zh"], "recipe": "h1pre_v9_future", "recipe_known": False}
        assert "配方「h1pre_v9_future」" in text and "配方未知" in text
        present, missing = (), (*V1_NEEDLES, *V2_NEEDLES)
    for needle in (*present, "命中率不代表交易獲利", "下一交易日收盤相對訊號日收盤"):
        assert needle in text
    for needle in missing:
        assert needle not in text
    assert block["target_definition"] == preopen.DISCLOSURE["target_zh"]
    assert _native_json(block) == block


def test_stale_fallback_keeps_the_observed_recipe(snapshot_root):
    write_fixture(snapshot_root, signal_date="2026-10-15")
    _set_recipe(snapshot_root, V2_RECIPE)
    block = preopen.build_h1_block(STOCK_6488, "2026-10-16")
    assert block["status"] == "fallback" and block["reason"] == "preopen_stale"
    assert block["observed_meta"]["recipe"] == V2_RECIPE
    assert "disclosure" not in block and "disclosure_rows" not in block  # 回退不帶揭露（與改版前相同）


def test_non_tradable_row_has_no_tradable_rank(snapshot_root):
    write_fixture(snapshot_root, signal_date="2026-10-16", stock_ids=["2330", "9999"], markets=["TWSE", "TWSE"])
    import pandas as pd
    import stock_daily_preopen_reader as reader
    meta = reader.read_latest_meta(snapshot_root)
    path = snapshot_root / meta["snapshot"]
    frame = pd.read_parquet(path)
    frame.loc[frame["stock_id"] == "9999", ["tradable", "rank_tradable"]] = [False, float("nan")]
    frame.to_parquet(path, index=False)
    meta["snapshot_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (snapshot_root / "predictions" / "latest.json").write_text(json.dumps(meta), encoding="utf-8")
    block = preopen.build_h1_block({"stock_id": "9999", "market": "TWSE"}, "2026-10-16")
    assert block["status"] == "used" and block["tradable"] is False
    assert block["rank_tradable"] is None and block["in_tradable_top10"] is False


@pytest.mark.parametrize("case,reason", [
    ("missing_root", "preopen_no_latest"),
    ("stale", "preopen_stale"),
    ("not_in_snapshot", "preopen_stock_not_in_snapshot"),
    ("market_mismatch", "preopen_market_mismatch"),
    ("sha_mismatch", "preopen_snapshot_sha_mismatch"),
    ("corrupt_pointer", "preopen_read_error"),
    ("asof_unknown", "preopen_asof_unknown"),
])
def test_unavailable_snapshot_falls_back_to_c0_with_reason(snapshot_root, case, reason):
    stock, asof = dict(STOCK_6488), "2026-10-16"
    if case != "missing_root":
        write_fixture(snapshot_root, signal_date="2026-10-15" if case == "stale" else "2026-10-16")
    if case == "not_in_snapshot":
        stock = {"stock_id": "1234", "market": "TWSE"}
    elif case == "market_mismatch":
        stock["market"] = "TWSE"
    elif case == "sha_mismatch":
        parquet = next((snapshot_root / "predictions").rglob("*.parquet"))
        parquet.write_bytes(parquet.read_bytes() + b"tamper")
    elif case == "corrupt_pointer":
        (snapshot_root / "predictions" / "latest.json").write_text("{not json", encoding="utf-8")
    elif case == "asof_unknown":
        asof = None
    block = preopen.build_h1_block(stock, asof)
    assert block["status"] == "fallback" and block["display_source"] == "c0" and block["reason"] == reason
    assert block["notice"] == f"開盤前版不可用（{reason}），本次 H1 改用 C0 基準"
    assert block["reason_zh"]
    # 主控台一行：原因代碼之後一定有中文說明
    assert preopen.display_lines(block) == [f"H1 來源：{block['notice']}。原因：{block['reason_zh']}"]
    if case == "stale":
        assert block["observed_meta"]["signal_date"] == "2026-10-15"
    assert _native_json(block) == block


def test_required_preopen_raises_instead_of_falling_back(snapshot_root):
    with pytest.raises(preopen.PreopenRequiredError, match="preopen_required_unavailable:preopen_no_latest") as caught:
        preopen.build_h1_block(STOCK_6488, "2026-10-16", source="preopen")
    message = str(caught.value)
    assert message.startswith("已指定 --h1-source preopen，但開盤前版不可用（尚無開盤前版快照）：preopen_required_unavailable:preopen_no_latest")
    assert message.endswith("；未產生任何報告")
    write_fixture(snapshot_root, signal_date="2026-10-16")
    assert preopen.build_h1_block(STOCK_6488, "2026-10-16", source="preopen")["status"] == "used"


def test_forced_c0_never_reads_the_snapshot(snapshot_root, monkeypatch):
    import stock_daily_preopen_reader as reader
    write_fixture(snapshot_root, signal_date="2026-10-16")
    monkeypatch.setattr(reader, "load_latest", lambda *a, **k: pytest.fail("c0 mode must not read"))
    block = preopen.build_h1_block(STOCK_6488, "2026-10-16", source="c0")
    assert block["status"] == "disabled" and block["reason"] == "h1_source_forced_c0"
    assert block["notice"] == "依設定不使用開盤前版（h1_source_forced_c0），本次 H1 改用 C0 基準"
    assert preopen.display_lines(block) == ["H1 來源：依設定不使用開盤前版（h1_source_forced_c0），本次 H1 改用 C0 基準。原因：依設定強制使用 C0 基準"]


def test_unexpected_reader_failure_in_auto_mode_is_only_a_fallback(snapshot_root, monkeypatch):
    import stock_daily_preopen_reader as reader
    write_fixture(snapshot_root, signal_date="2026-10-16")
    class Abort(BaseException):
        pass

    monkeypatch.setattr(reader, "load_latest", lambda *a, **k: (_ for _ in ()).throw(Abort("stop")))
    with pytest.raises(Abort):  # 非 Exception（中斷、結束）不被吞掉
        preopen.build_h1_block(STOCK_6488, "2026-10-16")
    monkeypatch.setattr(reader, "load_latest", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    block = preopen.build_h1_block(STOCK_6488, "2026-10-16")
    assert block["reason"] == "preopen_read_error" and "OSError" in block["reason_detail"]


# ---------------------------------------------------------------- C0 path


def _sealed(path):
    stored = json.loads(Path(path).read_text(encoding="utf-8"))
    unsigned = dict(stored)
    digest = unsigned.pop("estimate_binding_sha256")
    assert hashlib.sha256(single._canonical(unsigned)).hexdigest() == digest
    return stored


def test_c0_h1_uses_fresh_preopen_row_and_keeps_c0_fields_for_validation(current_case, snapshot_root, capsys):
    write_fixture(snapshot_root, signal_date="2026-10-08", target_date="2026-10-09")
    reports = single.generate_estimate("H63", "6488", output_dir=current_case["output"], both_horizons=True)
    h1, h63 = reports
    assert h1["h1_display_source"] == "preopen" and h1["preopen_h1"]["status"] == "used"
    assert h1["preopen_h1"]["expected_signal_date"] == "2026-10-08" and h1["preopen_h1"]["consumer"] == "single_c0"
    # C0 machine fields stay the C0 baseline so validate_c0_estimate still reproduces them.
    assert h1["prob_up"] == 0.5 and h1["prediction"]["prob_up"] == 0.5 and h1["expected_return"] == 0.0
    assert "preopen_h1" not in h63 and "h1_display_source" not in h63
    stored = _sealed(h1["output_json"])
    assert stored["preopen_h1"] == h1["preopen_h1"] and stored["verification"]["status"] == "verified"
    assert ("validate", 1) in current_case["calls"] and ("validate", 63) in current_case["calls"]
    assert "H1 來源：開盤前版（訊號日 20261008，目標日 20261009，" in capsys.readouterr().out  # 顯示日期一律 YYYYMMDD


def test_c0_h1_stale_snapshot_falls_back_with_notice(current_case, snapshot_root):
    write_fixture(snapshot_root, signal_date="2026-10-07", target_date="2026-10-08")
    h1 = single.generate_estimate("H1", "6488", output_dir=current_case["output"])
    block = h1["preopen_h1"]
    assert h1["h1_display_source"] == "c0" and block["reason"] == "preopen_stale"
    assert block["notice"] == "開盤前版不可用（preopen_stale），本次 H1 改用 C0 基準"
    assert block["observed_meta"]["signal_date"] == "2026-10-07"
    assert _sealed(h1["output_json"])["preopen_h1"]["reason"] == "preopen_stale"


@pytest.mark.parametrize("recipe", [V2_RECIPE, UNKNOWN_RECIPE], ids=["v2", "unknown"])
def test_c0_h1_seals_the_recipe_disclosure(current_case, snapshot_root, recipe):
    write_fixture(snapshot_root, signal_date="2026-10-08", target_date="2026-10-09")
    _set_recipe(snapshot_root, recipe)
    h1 = single.generate_estimate("H1", "6488", output_dir=current_case["output"])
    block = h1["preopen_h1"]
    chosen = preopen.disclosure_for({"recipe": recipe})
    assert block["status"] == "used" and h1["h1_display_source"] == "preopen"
    assert block["disclosure"] == chosen["facts"] and block["disclosure_rows"] == [list(row) for row in chosen["rows"]]
    assert h1["prob_up"] == 0.5 and h1["expected_return"] == 0.0  # C0 機器欄位不變
    stored = _sealed(h1["output_json"])
    assert stored["preopen_h1"] == block and stored["preopen_h1"]["meta"]["recipe"] == recipe
    assert stored["verification"]["status"] == "verified"


def test_c0_required_preopen_stops_before_any_file(current_case, snapshot_root, capsys):
    rc = single.main("H1", ["--stock", "6488", "--output-dir", str(current_case["output"]), "--h1-source", "preopen"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "preopen_required_unavailable:preopen_no_latest" in err
    assert "已指定 --h1-source preopen，但開盤前版不可用（尚無開盤前版快照）" in err
    assert not current_case["output"].exists() or not any(current_case["output"].iterdir())


def test_c0_source_switch_from_environment_and_cli(current_case, snapshot_root, monkeypatch):
    write_fixture(snapshot_root, signal_date="2026-10-08")
    monkeypatch.setenv(preopen.SOURCE_ENV, "c0")
    old_behaviour = single.generate_estimate("H1", "6488", output_dir=current_case["output"])
    assert "preopen_h1" not in old_behaviour and "h1_display_source" not in old_behaviour  # 與改版前相同
    assert single.main("H1", ["--stock", "6488", "--output-dir", str(current_case["output"]), "--h1-source", "auto"]) == 0
    latest = max(current_case["output"].glob("*.json"), key=lambda path: path.stat().st_mtime_ns)
    assert json.loads(latest.read_text(encoding="utf-8"))["preopen_h1"]["status"] == "used"
    monkeypatch.setenv(preopen.SOURCE_ENV, "bogus")
    assert single.main("H1", ["--stock", "6488", "--output-dir", str(current_case["output"])]) == 2


def test_c0_h63_never_resolves_h1_source(current_case, monkeypatch):
    monkeypatch.setenv(preopen.SOURCE_ENV, "bogus")

    def forbidden(*args, **kwargs):
        pytest.fail("H63 must not resolve H1 source or enter its overlay")

    monkeypatch.setattr(preopen, "resolve_source", forbidden)
    monkeypatch.setattr(preopen, "selected_h1_overlay", forbidden)
    item = single.generate_estimate(63, "6488", output_dir=current_case["output"])
    assert item["horizon_sessions"] == 63 and "preopen_h1" not in item
    assert current_case["calls"].count(("validate", 63)) == 1


@pytest.mark.parametrize(("horizon", "both_horizons"), [(1, False), (1, True), (63, True)])
def test_c0_h1_and_both_keep_source_validation(current_case, monkeypatch, horizon, both_horizons):
    monkeypatch.setenv(preopen.SOURCE_ENV, "bogus")
    with pytest.raises(ValueError, match="h1_source_invalid:bogus"):
        single.generate_estimate(horizon, "6488", output_dir=current_case["output"],
                                 both_horizons=both_horizons)
    assert current_case["calls"] == []


def test_c0_no_estimate_h1_still_carries_the_preopen_row(current_case, snapshot_root, monkeypatch):
    write_fixture(snapshot_root, signal_date="2026-10-08")
    excluded = json.dumps([{"stock_id": "6488", "reason": "stock_price_older_than_asof"}])

    def exclusion(*args, **kwargs):
        raise single.contract.C0IntegrityError("c0_no_stock_passed_current_price_scope_quality:" + excluded)

    monkeypatch.setattr(single.contract, "load_c0_published_panel", exclusion)
    monkeypatch.setattr(single, "_stock_last_price_date", lambda *a, **k: "2026-10-01")
    # An excluded stock has no price view; the byte stub only stands in for reportlab here.
    monkeypatch.setattr(single, "_pdf", lambda path, payload, report_view=None: path.write_bytes(b"%PDF-1.4\n%%EOF\n"))
    h1 = single.generate_estimate("H1", "6488", output_dir=current_case["output"])
    assert h1["estimate_status"] == "no_estimate" and h1["prob_up"] is None and h1["prediction"] is None
    assert h1["preopen_h1"]["status"] == "used" and h1["h1_display_source"] == "preopen"
    assert _sealed(h1["output_json"])["preopen_h1"]["p_up"] == h1["preopen_h1"]["p_up"]


def test_candidate_research_rejects_h1_source():
    with pytest.raises(SystemExit) as exc:
        single.main(1, ["--stock", "2330", "--candidate-model", "candidate.json", "--h1-source", "auto"])
    assert exc.value.code == 2


@pytest.mark.skipif(os.name != "nt", reason="Windows 中文字型")
@pytest.mark.parametrize("status", ["used", "fallback"])
def test_c0_pdf_renders_the_preopen_summary_or_notice(snapshot_root, tmp_path, status):
    pytest.importorskip("reportlab")
    write_fixture(snapshot_root, signal_date="2026-10-16" if status == "used" else "2026-10-15")
    block = preopen.build_h1_block(STOCK_6488, "2026-10-16")
    payload = {"stock": dict(STOCK_6488), "horizon_sessions": 1, "estimate_status": "estimated",
               "latest_data_date": "2026-10-16", "prediction": {"prob_up": 0.5, "expected_return": 0.0},
               "preopen_h1": block, "data_time": {}}
    target = tmp_path / f"{status}.pdf"
    REAL_C0_PDF(target, payload, None)
    raw = target.read_bytes()
    assert raw.startswith(b"%PDF-") and raw.rstrip().endswith(b"%%EOF")
    rows = single._preopen_rows(block) if status == "used" else []
    assert (status == "used") == any(name == "可交易母體名次" for name, _ in rows)


@pytest.mark.skipif(os.name != "nt", reason="Windows 中文字型")
@pytest.mark.parametrize("recipe", [V2_RECIPE, UNKNOWN_RECIPE], ids=["v2", "unknown"])
def test_c0_pdf_renders_the_recipe_disclosure(snapshot_root, tmp_path, recipe):
    pytest.importorskip("reportlab")
    write_fixture(snapshot_root, signal_date="2026-10-16")
    _set_recipe(snapshot_root, recipe)
    block = preopen.build_h1_block(STOCK_6488, "2026-10-16")
    assert block["status"] == "used" and block["disclosure_rows"][1][0] == "模型配方"
    payload = {"stock": dict(STOCK_6488), "horizon_sessions": 1, "estimate_status": "estimated",
               "latest_data_date": "2026-10-16", "prediction": {"prob_up": 0.5, "expected_return": 0.0},
               "preopen_h1": block, "data_time": {}}
    target = tmp_path / f"{recipe['name']}.pdf"
    REAL_C0_PDF(target, payload, None)
    raw = target.read_bytes()
    assert raw.startswith(b"%PDF-") and raw.rstrip().endswith(b"%%EOF")


# ---------------------------------------------------------------- selected (formal) path


def _sign(item):
    import stock_daily_selected_deployment as selected
    item["runtime_payload_sha256"] = selected._digest({k: v for k, v in item.items() if k != "runtime_payload_sha256"})
    return item


def _stub_rendering(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(report, "_converter", lambda: lambda text: text)
    monkeypatch.setattr(report, "_load_fonts", lambda: SimpleNamespace(check=lambda texts: None))
    monkeypatch.setattr(report, "_check_output_path", lambda *args: None)
    monkeypatch.setattr(report, "_render_pdf", lambda path, *args: path.write_bytes(b"mock preflight fixture"))
    monkeypatch.setattr(report, "_inspect_structure", lambda *args: {"pages": 1, "embedded_truetype_subsets": 1})
    monkeypatch.setattr(report, "_extract_if_available", lambda *args: {"status": "pending_external"})


def test_selected_h1_payload_gains_sealed_block_and_valid_digest(formal_payload, snapshot_root, tmp_path, monkeypatch):
    import stock_daily_selected_deployment as selected
    _stub_rendering(monkeypatch)
    write_fixture(snapshot_root, signal_date="2026-10-16", target_date="2026-10-19")
    item = _sign(copy.deepcopy(formal_payload))
    with preopen.selected_h1_overlay("auto"):
        result = report.write_candidate_report(item, tmp_path / "reports")
    block = item["preopen_h1"]
    assert block["status"] == "used" and block["consumer"] == "single_selected"
    assert "單股已啟用選定模型" in block["fallback_label"]
    # Same check as validate_selected_report: the recorded digest covers the added block.
    unsigned = {k: v for k, v in item.items() if k != "runtime_payload_sha256"}
    assert item["runtime_payload_sha256"] == selected._digest(unsigned)
    assert result["validation"]["valid"] is True
    sidecar = json.loads(Path(result["json_path"]).read_text(encoding="utf-8"))
    assert sidecar["payload"]["preopen_h1"] == block
    assert report.validate_candidate_report(item, Path(result["pdf_path"]))["valid"] is True
    text = "\n".join(report._visible_strings(sidecar["sections"]))
    for needle in ("H1 開盤前版預測（主要顯示）", "可交易母體名次", "前一晚美股收漲（建議日）", "開盤前版必要揭露",
                   "59.0%", "63.5%", "命中率不代表交易獲利", "資料出處", "備援對照：C0 基準，單股已啟用選定模型"):
        assert needle in text
    assert report._attach_preopen_h1 and report._PREOPEN_H1_ATTACHER.get() is None


@pytest.mark.parametrize("recipe,present,missing", [
    (V2_RECIPE, V2_NEEDLES, V1_NEEDLES),
    (UNKNOWN_RECIPE, ("配方「h1pre_v9_future」", "配方未知"), (*V1_NEEDLES, *V2_NEEDLES)),
], ids=["v2", "unknown"])
def test_selected_h1_report_discloses_the_snapshot_recipe(formal_payload, snapshot_root, tmp_path, monkeypatch,
                                                          recipe, present, missing):
    _stub_rendering(monkeypatch)
    write_fixture(snapshot_root, signal_date="2026-10-16", target_date="2026-10-19")
    _set_recipe(snapshot_root, recipe)
    item = _sign(copy.deepcopy(formal_payload))
    with preopen.selected_h1_overlay("auto"):
        result = report.write_candidate_report(item, tmp_path / "reports")
    assert result["validation"]["valid"] is True
    assert item["preopen_h1"]["status"] == "used" and item["preopen_h1"]["meta"]["recipe"] == recipe
    sidecar = json.loads(Path(result["json_path"]).read_text(encoding="utf-8"))
    assert sidecar["payload"]["preopen_h1"] == item["preopen_h1"]
    tables = [table for section in sidecar["sections"] for table in section.get("tables") or []
              if table.get("title") == "開盤前版必要揭露"]
    assert len(tables) == 1
    text = json.dumps(tables[0]["rows"], ensure_ascii=False)
    for needle in (*present, "命中率不代表交易獲利"):
        assert needle in text
    for needle in missing:
        assert needle not in text
    assert report.validate_candidate_report(item, Path(result["pdf_path"]))["valid"] is True


def test_selected_fallback_notice_and_h63_untouched(formal_payload, snapshot_root, tmp_path, monkeypatch):
    _stub_rendering(monkeypatch)
    write_fixture(snapshot_root, signal_date="2026-10-15")
    h1 = _sign(copy.deepcopy(formal_payload))
    h63 = _sign(dict(copy.deepcopy(formal_payload), horizon_sessions=63))
    original_h63 = copy.deepcopy(h63)
    with preopen.selected_h1_overlay("auto"):
        report.write_candidate_report(h1, tmp_path / "reports")
        report.write_candidate_report(h63, tmp_path / "reports")
    assert h63 == original_h63
    assert h1["preopen_h1"]["reason"] == "preopen_stale"
    summary = report.build_report_sections(h1)[0]
    assert summary["paragraphs"][0].startswith("【注意】開盤前版不可用（preopen_stale），本次 H1 改用 C0 基準，單股已啟用選定模型。原因：")
    assert summary["tables"][0]["title"] == "本股預測（C0 基準，單股已啟用選定模型）"  # 不再有巢狀括號


def test_selected_required_preopen_stops_before_rendering(formal_payload, snapshot_root, tmp_path, monkeypatch):
    _stub_rendering(monkeypatch)
    item = _sign(copy.deepcopy(formal_payload))
    with preopen.selected_h1_overlay("preopen"), pytest.raises(preopen.PreopenRequiredError):
        report.write_candidate_report(item, tmp_path / "reports")
    assert not (tmp_path / "reports").exists() or not any((tmp_path / "reports").iterdir())


def test_without_overlay_reports_are_unchanged(formal_payload, payload, snapshot_root, tmp_path, monkeypatch):
    _stub_rendering(monkeypatch)
    write_fixture(snapshot_root, signal_date="2026-10-16")
    for item in (copy.deepcopy(formal_payload), copy.deepcopy(payload)):
        original = copy.deepcopy(item)
        report.write_candidate_report(item, tmp_path / "reports")
        assert item == original and "preopen_h1" not in item
        sections = report.build_report_sections(item)
        assert len(sections) == 6 and sections[0]["tables"][0]["title"] == "本股預測"
        assert "開盤前版" not in "\n".join(report._visible_strings(sections))


def test_block_on_wrong_horizon_or_malformed_is_rejected(formal_payload):
    item = copy.deepcopy(formal_payload)
    item["preopen_h1"] = {"schema": preopen.BLOCK_SCHEMA, "status": "fallback", "notice": "x"}
    item["horizon_sessions"] = 63
    with pytest.raises(report.CandidateReportError) as caught:
        report.build_report_sections(item)
    assert caught.value.code == "preopen_block_invalid"
    item["horizon_sessions"] = 1
    item["preopen_h1"] = {"schema": preopen.BLOCK_SCHEMA, "status": "used", "p_up": 1.5, "exp_ret": 0.0, "stock_id": "2330"}
    with pytest.raises(report.CandidateReportError):
        report.build_report_sections(item)


@pytest.mark.skipif(os.name != "nt", reason="Windows 字型與轉繁 API 預檢")
def test_real_fonts_cover_every_preopen_string(formal_payload, snapshot_root):
    pytest.importorskip("reportlab")
    write_fixture(snapshot_root, signal_date="2026-10-16")
    for recipe in (_ABSENT, V2_RECIPE, UNKNOWN_RECIPE):  # 各配方的揭露文字（含「」）都要有字型
        _set_recipe(snapshot_root, recipe)
        for signal in ("2026-10-16", "2026-10-17"):
            item = copy.deepcopy(formal_payload)
            item["preopen_h1"] = preopen.build_h1_block(STOCK_2330, signal, base_label=preopen.SELECTED_BASE_LABEL)
            sections = report.build_report_sections(item)
            report._load_fonts().check(report._visible_strings(sections))


@pytest.mark.parametrize(("horizon", "both_horizons", "expected_horizons"), [
    (1, False, (1,)), (1, True, (1, 63)), (63, True, (1, 63)),
])
def test_main_wraps_selected_generator_with_overlay_only_during_the_call(
        monkeypatch, tmp_path, horizon, both_horizons, expected_horizons):
    seen = []
    resolved, overlays = [], []
    native_resolve, native_overlay = preopen.resolve_source, preopen.selected_h1_overlay
    monkeypatch.setenv(preopen.SOURCE_ENV, "auto")
    binding = {"schema": "single-stock-selected-runtime-binding-v1", "deployment_binding": {"activation_id": "fixture"}}
    monkeypatch.setattr(single.selected, "analysis_binding", lambda: binding)

    def resolve(value=None, environ=None):
        resolved.append(value)
        return native_resolve(value, environ)

    def overlay(source=None):
        overlays.append(source)
        return native_overlay(source)

    def generate(*args, **kwargs):
        seen.append((report._PREOPEN_H1_ATTACHER.get() is not None, kwargs["horizons"]))

    monkeypatch.setattr(preopen, "resolve_source", resolve)
    monkeypatch.setattr(preopen, "selected_h1_overlay", overlay)
    monkeypatch.setattr(single.selected, "generate_selected_reports", generate)
    args = ["--stock", "2330", "--output-dir", str(tmp_path)]
    if both_horizons:
        args.append("--both-horizons")
    assert single.main(horizon, args) == 0
    assert single.main(horizon, [*args, "--h1-source", "c0"]) == 0
    assert seen == [(True, expected_horizons), (False, expected_horizons)]
    assert overlays == ["auto", "c0"]
    assert resolved == [None, "auto", "c0", "c0"]
    assert report._PREOPEN_H1_ATTACHER.get() is None


@pytest.mark.parametrize("source_args", [[], ["--h1-source", "preopen"]])
def test_selected_h63_never_resolves_h1_source_or_enters_overlay(monkeypatch, tmp_path, source_args):
    monkeypatch.setenv(preopen.SOURCE_ENV, "bogus")
    binding = {"schema": "single-stock-selected-runtime-binding-v1", "deployment_binding": {"activation_id": "fixture"}}
    monkeypatch.setattr(single.selected, "analysis_binding", lambda: binding)
    seen = []

    def forbidden(*args, **kwargs):
        pytest.fail("H63 must not resolve H1 source or enter its overlay")

    def generate(query, **kwargs):
        seen.append((query, kwargs["horizons"], kwargs["activation_binding"]))

    monkeypatch.setattr(preopen, "resolve_source", forbidden)
    monkeypatch.setattr(preopen, "selected_h1_overlay", forbidden)
    monkeypatch.setattr(single.selected, "generate_selected_reports", generate)
    args = ["--stock", "2330", "--output-dir", str(tmp_path), *source_args]
    assert single.main(63, args) == 0
    assert seen == [("2330", (63,), binding)]
    assert report._PREOPEN_H1_ATTACHER.get() is None


@pytest.mark.parametrize(("horizon", "both_horizons"), [(1, False), (1, True), (63, True)])
def test_selected_h1_and_both_reject_invalid_h1_source_environment(
        monkeypatch, tmp_path, capsys, horizon, both_horizons):
    monkeypatch.setenv(preopen.SOURCE_ENV, "bogus")

    def forbidden(*args, **kwargs):
        pytest.fail("invalid H1 source must fail before binding, overlay or generation")

    monkeypatch.setattr(single.selected, "analysis_binding", forbidden)
    monkeypatch.setattr(single.selected, "generate_selected_reports", forbidden)
    monkeypatch.setattr(preopen, "selected_h1_overlay", forbidden)
    args = ["--stock", "2330", "--output-dir", str(tmp_path)]
    if both_horizons:
        args.append("--both-horizons")
    assert single.main(horizon, args) == 2
    assert "h1_source_invalid:bogus" in capsys.readouterr().err


def test_selected_forced_c0_leaves_formal_payload_unchanged(formal_payload, snapshot_root, tmp_path, monkeypatch):
    _stub_rendering(monkeypatch)
    write_fixture(snapshot_root, signal_date="2026-10-16")
    item = _sign(copy.deepcopy(formal_payload))
    original = copy.deepcopy(item)
    with preopen.selected_h1_overlay("c0"):
        report.write_candidate_report(item, tmp_path / "reports")
    assert item == original


def test_chips_note_follows_reader_text_and_meta_keeps_freshness(snapshot_root):
    """t 日籌碼未更新／不完整時，區塊多一段 chips_note_zh（與讀取器 describe 一致）；新鮮或舊快照為 None。"""
    import stock_daily_preopen_reader as reader
    meta = write_fixture(snapshot_root, signal_date="2026-10-16", target_date="2026-10-19", stale_routes=["finmind_margin"])
    block = preopen.build_h1_block(STOCK_6488, "2026-10-16", base_target_date="2026-10-19")
    assert block["status"] == "used"
    assert block["chips_note_zh"] == reader.chips_freshness_text(meta["chips_freshness"])
    assert block["chips_note_zh"] == "t 日籌碼未更新：融資券最新到 20261015（以舊值推論，準確度可能下降）"
    assert block["chips_note_zh"] in block["describe"]
    assert block["meta"]["chips_freshness"]["stale_routes"] == ["finmind_margin"] and block["meta"]["degraded"] is True
    assert _native_json(block) == block
    # 中央標示不完整：只多說明，status 仍 used、degraded 仍 False（使用者裁示：有資料就可用，不阻擋）
    write_fixture(snapshot_root, partial_routes=["finmind_institutional"])
    block = preopen.build_h1_block(STOCK_6488, "2026-10-16")
    assert block["chips_note_zh"] == "t 日籌碼資料不完整（中央回報成功/應有）：法人 100/1215（缺的以舊值推論，準確度可能下降）"
    assert block["status"] == "used" and block["meta"]["degraded"] is False
    # 新鮮且完整：沒有加註
    write_fixture(snapshot_root)
    block = preopen.build_h1_block(STOCK_6488, "2026-10-16")
    assert block["status"] == "used" and block["chips_note_zh"] is None
    assert block["meta"]["chips_freshness"]["status"] == "ok"
    # 舊快照（沒有 chips_freshness／degraded）：照常可用，沒有加註、meta 也沒有這兩鍵
    latest = snapshot_root / "predictions" / "latest.json"
    old = json.loads(latest.read_text(encoding="utf-8"))
    old.pop("chips_freshness")
    old.pop("degraded")
    latest.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    block = preopen.build_h1_block(STOCK_6488, "2026-10-16")
    assert block["status"] == "used" and block["chips_note_zh"] is None
    assert "chips_freshness" not in block["meta"] and "degraded" not in block["meta"]
