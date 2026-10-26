"""開盤前版 H1 取代 C0 顯示（2026-10-18 使用者決定）：auto／preopen／c0 開關、新鮮度回退、報告揭露。

2026-10-21：必要揭露、閘門與「開盤買進」文字依快照 recipe.name 選取（沒有 recipe → v1 原文、h1pre_v2_ens → v2、
其他 → 中性文字）；共用 fixture 不寫 recipe，配方別測試在 latest.json 補上。

沿用 test_per_market_asof 的合成兩市場 C0 流程（TWSE 最新日 D、TPEX 落後到 T），
開盤前版快照以共用 stock_daily_preopen_fixture 寫在暫存目錄（STOCK_DAILY_PREOPEN_ROOT）。
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import pandas as pd
import pytest

import test_per_market_asof as top10_case  # noqa: E402  (also puts PROJECT / shared tests on sys.path)
from test_c0_per_market_asof import loaded_scope, model  # noqa: E402,F401  (pytest fixtures)
import app  # noqa: E402
import preopen_source  # noqa: E402
import reporting  # noqa: E402

fx = top10_case.fx
D, T, NEXT_D = top10_case.D, top10_case.T, top10_case.NEXT_D
# 合成 C0 的四檔（1111/2222 上市、3333/4444 上櫃）加上 C0 普通股池以外的十檔，讓 Top10 之後還有股票。
EXTRA = [str(5000 + 101 * index) for index in range(10)]
STOCK_IDS = [stock for stock, _ in fx.STOCKS] + EXTRA
MARKETS = [market if market == "TWSE" else "TPEx" for _, market in fx.STOCKS] + ["TWSE"] * 5 + ["TPEx"] * 5
DISCLOSURE_NUMBERS = ("59.0%", "55.9%", "AUC 0.608", "63.5%", "55.6%", "−0.04%", "校正報告_20261018.md")
# 配方 v2 的揭露數字；v1 專屬字樣在 v2／未知配方的報告中不得出現。
V2_DISCLOSURE_NUMBERS = ("60.0%", "56.1%", "AUC 0.619", "65.3%", "49.1%", "57.1%", "+0.01%", "2.71%",
                         "H1改良研究報告_20261021.md")
V1_ONLY_TEXTS = ("AUC 0.608", "（約 −0.04%／日", "平均報酬為負", "保留期平均為負", "校正報告_20261018.md",
                 "命中 63.5%", "歷史 Top10 命中僅約 55%")
V1_RECIPE = {"name": "h1pre_final_nogc_xs", "frozen_sha256": "e53c2416b63d8273162c3618564813cd13145052e5ac30f5c3c375fb48c3b28f"}
V2_RECIPE = {"name": "h1pre_v2_ens", "frozen_sha256": "84343f31a101ac30c04a2ec997d89f4517ccc2623a3843918a93ca0671409882"}
UNKNOWN_RECIPE = {"name": "h1pre_v9_future", "frozen_sha256": "0" * 64}
# 配方 v1 今日原文（鎖定：v1 文字一字不改）。
V1_LINES = [
    "開盤前版預測的是「下一交易日收盤相對訊號日收盤」的方向（調整後收盤），不是開盤價、盤中價或可成交報酬的預測。",
    "保留期（2026-01～10，模型未看過的資料）方向準確率 59.0%（一律猜跌為 55.9%），AUC 0.608。",
    "只在前一晚美股收漲日選出的可交易 Top10 命中率 63.5%；不分日子全部計算為 55.6%。",
    "若在隔日開盤買進，Top10 平均報酬為負（約 −0.04%／日，未扣交易成本）；命中率不代表交易獲利。",
    "可交易母體與驗證時相同：20 日均成交金額 ≥ 2,000 萬元且訊號日收盤 ≥ 10 元。",
]
V1_SOURCE = r"資料出處：D:\TWStockData\program_data\stock_daily_c0\candidates\calibration_20261018\report\校正報告_20261018.md"
V1_GATE = {True: ("recommended_day", "建議日：前一晚美股收漲（保留期此條件下 Top10 命中 63.5%）"),
           False: ("not_recommended_day", "非建議日：歷史 Top10 命中僅約 55%（前一晚美股未收漲）"),
           None: ("unknown", "前一晚美股漲跌未知：無法判定是否為建議日")}
_ABSENT = object()


def _write(preopen_fixture, recipe=_ABSENT, **overrides):
    kwargs = dict(signal_date=D, target_date=NEXT_D, stock_ids=STOCK_IDS, markets=MARKETS, seed=7)
    kwargs.update(overrides)
    meta = preopen_fixture(**kwargs)
    if recipe is not _ABSENT:
        # 共用 fixture 寫的是沒有 recipe 的舊格式；在 latest.json 補上 recipe（不影響快照雜湊）。
        latest = preopen_fixture.root / "predictions" / "latest.json"
        meta = dict(json.loads(latest.read_text(encoding="utf-8")), recipe=recipe)
        latest.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def _snapshot(root):
    reader, _ = preopen_source.prepared_reader()
    return reader.load_latest(root=root)


def _run(scope, model, monkeypatch, tmp_path, source=None):
    if source is not None:
        monkeypatch.setenv(preopen_source.MODEL_SOURCE_ENV, source)
    captured, payload, html, output = top10_case._run_predict(scope, model, monkeypatch, tmp_path)
    manifest = json.loads((output.parent / "manifest.json").read_text(encoding="utf-8"))
    return captured, payload, html, output, manifest


def _xlsx_strings(path):
    """所有工作表 XML（含 inline 字串）與共用字串表的文字。"""
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist()
                 if name.startswith("xl/worksheets/") and name.endswith(".xml") or name == "xl/sharedStrings.xml"]
        return "\n".join(archive.read(name).decode("utf-8") for name in names)


# ---------------------------------------------------------------- switch and reader


def test_model_source_precedence_cli_then_env_then_auto():
    assert preopen_source.resolve_model_source(None, {}) == ("auto", "default")
    assert preopen_source.resolve_model_source(None, {"TOP10_MODEL_SOURCE": "C0"}) == ("c0", "env")
    assert preopen_source.resolve_model_source("preopen", {"TOP10_MODEL_SOURCE": "c0"}) == ("preopen", "cli")
    with pytest.raises(ValueError):
        preopen_source.resolve_model_source("mix", {})
    with pytest.raises(SystemExit):
        app.main(["predict", "--model-source", "mix"])


def test_choose_reports_each_unavailable_reason(preopen_fixture):
    root = preopen_fixture.root
    missing = preopen_source.choose("auto", "default", D)
    assert (missing.model_source, missing.status, missing.reason) == ("c0", "unavailable", "preopen_no_latest")
    meta = _write(preopen_fixture)
    fresh = preopen_source.choose("auto", "default", D)
    assert fresh.active and fresh.status == "available" and fresh.engine in {"pyarrow", "fastparquet", "duckdb"}
    stale = preopen_source.choose("auto", "default", NEXT_D)
    assert stale.reason == "preopen_stale" and not stale.active
    with pytest.raises(preopen_source.PreopenRequiredError, match="preopen_required_unavailable:preopen_stale"):
        preopen_source.choose("preopen", "cli", NEXT_D)
    snapshot = root / meta["snapshot"]
    snapshot.write_bytes(snapshot.read_bytes() + b"tampered")
    tampered = preopen_source.choose("auto", "default", D)
    assert tampered.reason == "preopen_snapshot_sha_mismatch"
    assert "開盤前版不可用（preopen_snapshot_sha_mismatch" in tampered.note()


def test_forced_c0_does_not_read_the_snapshot(preopen_fixture, monkeypatch):
    _write(preopen_fixture)
    monkeypatch.setattr(preopen_source, "load_reader", lambda *a, **k: pytest.fail("c0 must not read the snapshot"))
    choice = preopen_source.choose("c0", "env", D)
    assert (choice.model_source, choice.status, choice.reason) == ("c0", "not_requested", "model_source_forced_c0")
    assert preopen_source.report_fields(choice)["preopen_meta"] is None


def test_duckdb_parquet_bridge_reads_through_the_shared_reader(preopen_fixture, monkeypatch):
    _write(preopen_fixture)
    monkeypatch.setattr(preopen_source, "_parquet_engine", lambda: "duckdb")
    choice = preopen_source.choose("auto", "default", D)
    assert choice.active and choice.engine == "duckdb"
    expected = _snapshot(preopen_fixture.root).top(10)["stock_id"].tolist()
    assert choice.snapshot.top(10)["stock_id"].tolist() == expected


def test_consumer_checks_reject_inconsistent_tradable_rank(preopen_fixture):
    meta = _write(preopen_fixture)
    path = preopen_fixture.root / meta["snapshot"]
    frame = pd.read_parquet(path) if preopen_source._parquet_engine() != "duckdb" else \
        preopen_source._DuckdbParquetPandas.read_parquet(path)
    top = frame.loc[frame.rank_tradable.eq(1)].index
    frame.loc[top, "rank_tradable"] = 99.0
    frame.to_parquet(path, index=False)
    latest = preopen_fixture.root / "predictions" / "latest.json"
    info = json.loads(latest.read_text(encoding="utf-8"))
    import hashlib
    info["snapshot_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    latest.write_text(json.dumps(info), encoding="utf-8")
    assert preopen_source.choose("auto", "default", D).reason == "preopen_rank_inconsistent"


# ---------------------------------------------------------------- end-to-end predict


def test_predict_shows_preopen_top10_when_snapshot_is_fresh(loaded_scope, model, monkeypatch, tmp_path, preopen_fixture):
    meta = _write(preopen_fixture)
    expected_top = _snapshot(preopen_fixture.root).top(10)
    scope = loaded_scope()  # TPEX 落後（T）、TWSE 最新（D）
    captured, payload, html, output, manifest = _run(scope, model, monkeypatch, tmp_path)
    # C0 推論與前瞻帳冊照常執行（備援）。
    assert sorted(set(zip(captured["forecast"].stock_id, captured["forecast"].market))) == sorted(fx.STOCKS)
    assert payload["model_source"] == "preopen" and payload["requested_model_source"] == "auto"
    context = payload["report_context"]
    assert context["model_source"] == "preopen"
    assert context["ranking_status"] == "preopen_model_ranking"
    assert context["ranking_status"] not in reporting.NO_STOCK_RANKING_STATUSES
    assert payload["ranking_policy"] == "preopen_tradable_top10_p_up_then_exp_ret"
    assert context["no_stock_specific_model_signal"] is False
    assert context["c0_ranking_status"] in reporting.NO_STOCK_RANKING_STATUSES | {"model_values_displayed_effect_unknown"}
    # Top10 = 開盤前版可交易母體依 p_up→exp_ret 前 10 名（與 snapshot.top(10) 完全一致）。
    assert [row["stock_id"] for row in payload["top10"]] == expected_top["stock_id"].tolist()
    assert all(row["tradable"] is True for row in payload["top10"])
    assert [row["rank_tradable"] for row in payload["top10"]] == list(range(1, len(expected_top) + 1))
    for row, (_, snap) in zip(payload["top10"], expected_top.iterrows()):
        assert row["probability"] == pytest.approx(snap["p_up"]) and row["expected_return"] == pytest.approx(snap["exp_ret"])
    # 其餘股票依同排序列在後面。
    rest = payload["predictions"][len(payload["top10"]):]
    keys = [(-row["probability"], -row["expected_return"], row["market"], row["stock_id"]) for row in rest]
    assert keys == sorted(keys)
    assert len(payload["predictions"]) == len(STOCK_IDS)
    assert [row["display_order"] for row in payload["predictions"]] == list(range(1, len(STOCK_IDS) + 1))
    # C0 數值保留在每列（備援追溯）。
    c0_rows = {row["stock_id"]: row for row in payload["predictions"] if row["in_c0_universe"]}
    assert sorted(c0_rows) == sorted(stock for stock, _ in fx.STOCKS)
    assert all(row["c0_probability"] is not None for row in c0_rows.values())
    assert {stock: row["c0_market"] for stock, row in c0_rows.items()} == {stock: market for stock, market in fx.STOCKS}
    # 識別：payload、context、manifest 都帶 model_source 與 preopen_meta（run_id、model_sha256、snapshot_sha256）。
    for container in (payload, context, manifest):
        info = container["preopen_meta"]
        assert container["model_source"] == "preopen"
        assert (info["run_id"], info["model_sha256"], info["snapshot_sha256"]) == (
            meta["run_id"], meta["model_sha256"], meta["snapshot_sha256"])
    assert payload["preopen_meta"]["signal_date"] == D  # 封存原值，不轉日期格式
    assert payload["preopen_gate"]["code"] == "recommended_day"
    # 基準日以開盤前版訊號日為準；C0 各市場分析日改記為 c0_*。
    assert context["base_session"] == D.replace("-", "")
    assert context["target_session"] == NEXT_D.replace("-", "")
    assert "base_session_by_market" not in context
    assert context["c0_base_session_by_market"] == {"TPEX": T, "TWSE": D}
    assert context["preopen_status"]["expected_signal_date"] == D.replace("-", "")
    # HTML：模型來源、describe、建議日、必要揭露。
    describe = preopen_source.prepared_reader()[0].describe(meta)
    assert html.count(describe) >= 1 and "H1 模型來源：開盤前版" in html
    assert "建議日：前一晚美股收漲" in html and "非建議日" not in html
    assert "開盤前版可交易 Top10" in html and "開盤前版不可用" not in html
    for text in DISCLOSURE_NUMBERS:
        assert text in html
    assert "命中率不代表交易獲利" in html
    # 沒有 recipe 欄位的舊快照 → 配方 v1 原文（揭露、閘門、開盤買進字樣都不變）。
    assert "recipe" not in payload["preopen_meta"]
    disclosure = payload["model_disclosure"]
    assert disclosure["lines"] == V1_LINES and disclosure["source"] == V1_SOURCE
    assert (disclosure["recipe"], disclosure["recipe_entry"]) == (None, "h1pre_final_nogc_xs")
    assert payload["preopen_gate"]["label"] == V1_GATE[True][1]
    assert context["trading_effect_status"] == "開盤買進 Top10 保留期平均為負；交易效益未證"
    assert "命中率指收盤報價方向，不代表交易獲利（隔日開盤買進 Top10 保留期平均為負）。" in html
    assert "命中率指收盤報價方向，不代表交易獲利；隔日開盤買進 Top10 保留期平均為負。" in html
    # Excel：揭露與閘門文字寫入儲存格，可交易欄位存在。
    strings = _xlsx_strings(output)
    for text in ("必要揭露（開盤前版 H1）", "建議日：前一晚美股收漲", "可交易名次", "AUC 0.608", meta["run_id"]):
        assert text in strings
    assert "保留期驗證見必要揭露；命中率不代表交易獲利；隔日開盤買進 Top10 保留期平均為負。" in strings
    assert "命中率指收盤報價方向，不代表交易獲利；隔日開盤買進 Top10 保留期平均為負。" in strings
    visible = tmp_path / "visible" / (NEXT_D.replace("-", "") + ".xlsx")
    assert visible.is_file()
    csv_text = (output.parent / "report.csv").read_text(encoding="utf-8-sig")
    assert "可交易名次" in csv_text and "模型來源與必要揭露" in csv_text
    latest = json.loads((tmp_path / "ns" / "reports" / "latest.json").read_text(encoding="utf-8"))
    assert latest["model_source"] == "preopen" and latest["preopen_run_id"] == meta["run_id"]


@pytest.mark.parametrize("recipe,gate,us_status,code,label", [
    (_ABSENT, False, "ok", "not_recommended_day", "非建議日：歷史 Top10 命中僅約 55%"),
    (_ABSENT, None, "missing", "unknown", "前一晚美股漲跌未知"),
    (V2_RECIPE, False, "ok", "not_recommended_day", "非建議日：保留期此條件下 Top10 命中約 49.1%（前一晚美股未收漲）"),
    (UNKNOWN_RECIPE, False, "ok", "not_recommended_day", "非建議日：前一晚美股未收漲（配方未知：此條件下的保留期命中率未驗證）"),
], ids=["us_down_not_recommended", "us_missing_unknown", "v2_us_down_not_recommended", "unknown_recipe_us_down"])
def test_predict_labels_non_recommended_and_unknown_days(loaded_scope, model, monkeypatch, tmp_path, preopen_fixture,
                                                         recipe, gate, us_status, code, label):
    _write(preopen_fixture, recipe=recipe, gate_us_up=gate, us_status=us_status)
    _, payload, html, output, _ = _run(loaded_scope(tpex_index=fx.D_INDEX), model, monkeypatch, tmp_path)
    assert payload["model_source"] == "preopen"
    assert payload["preopen_gate"]["code"] == code
    assert label in html and label in _xlsx_strings(output)
    assert "建議日：前一晚美股收漲" not in html


def test_predict_discloses_recipe_v2_numbers(loaded_scope, model, monkeypatch, tmp_path, preopen_fixture):
    _write(preopen_fixture, recipe=V2_RECIPE)
    _, payload, html, output, manifest = _run(loaded_scope(), model, monkeypatch, tmp_path)
    context = payload["report_context"]
    assert payload["model_source"] == "preopen" and context["ranking_status"] == "preopen_model_ranking"
    for container in (payload, context, manifest):
        assert container["preopen_meta"]["recipe"] == V2_RECIPE
        assert (container["model_disclosure"]["recipe"], container["model_disclosure"]["recipe_entry"]) == (
            "h1pre_v2_ens", "h1pre_v2_ens")
    assert payload["model_disclosure"]["lines"] == list(preopen_source.DISCLOSURE_V2)
    assert payload["model_disclosure"]["source"] == "資料出處：" + str(preopen_source.H1_IMPROVE_REPORT)
    assert payload["preopen_gate"] == {"gate_us_up": True, "code": "recommended_day", "recommended": True,
                                       "label": "建議日：前一晚美股收漲（保留期此條件下 Top10 命中 65.3%）"}
    assert context["trading_effect_status"] == "開盤買進 Top10 保留期平均約為零（約 +0.01%／日）；交易效益未證"
    strings = _xlsx_strings(output)
    for text in (*V2_DISCLOSURE_NUMBERS, "建議日：前一晚美股收漲（保留期此條件下 Top10 命中 65.3%）",
                 "命中率指收盤報價方向，不代表交易獲利；隔日開盤買進 Top10 保留期平均約為零（約 +0.01%／日）。"):
        assert text in html and text in strings
    assert "不代表交易獲利（隔日開盤買進 Top10 保留期平均約為零（約 +0.01%／日））。" in html
    assert "保留期驗證見必要揭露；命中率不代表交易獲利；隔日開盤買進 Top10 保留期平均約為零（約 +0.01%／日）。" in strings
    for text in V1_ONLY_TEXTS:
        assert text not in html and text not in strings


def test_predict_explicit_recipe_v1_keeps_todays_text(loaded_scope, model, monkeypatch, tmp_path, preopen_fixture):
    _write(preopen_fixture, recipe=V1_RECIPE)
    _, payload, html, output, manifest = _run(loaded_scope(), model, monkeypatch, tmp_path)
    assert payload["preopen_meta"]["recipe"] == manifest["preopen_meta"]["recipe"] == V1_RECIPE
    assert payload["model_disclosure"]["lines"] == V1_LINES and payload["model_disclosure"]["source"] == V1_SOURCE
    assert payload["model_disclosure"]["recipe"] == "h1pre_final_nogc_xs"
    assert payload["preopen_gate"]["label"] == V1_GATE[True][1]
    assert payload["report_context"]["trading_effect_status"] == "開盤買進 Top10 保留期平均為負；交易效益未證"
    for text in DISCLOSURE_NUMBERS:
        assert text in html
    assert "隔日開盤買進 Top10 保留期平均為負。" in _xlsx_strings(output)


def test_predict_uses_neutral_disclosure_for_unknown_recipe(loaded_scope, model, monkeypatch, tmp_path, preopen_fixture):
    _write(preopen_fixture, recipe=UNKNOWN_RECIPE)
    _, payload, html, output, manifest = _run(loaded_scope(), model, monkeypatch, tmp_path)
    context = payload["report_context"]
    # 未知配方只改揭露文字；模型來源選擇、排序與閘門狀態碼不變。
    assert payload["model_source"] == "preopen" and context["ranking_status"] == "preopen_model_ranking"
    assert payload["preopen_meta"]["recipe"] == UNKNOWN_RECIPE
    disclosure = payload["model_disclosure"]
    assert (disclosure["recipe"], disclosure["recipe_entry"]) == ("h1pre_v9_future", "unknown")
    assert disclosure["title"] == "必要揭露（開盤前版 H1）"
    assert payload["preopen_gate"]["code"] == "recommended_day"
    assert payload["preopen_gate"]["label"] == "建議日：前一晚美股收漲（配方未知：此條件下的保留期命中率未驗證）"
    assert context["trading_effect_status"] == "開盤買進 Top10 保留期報酬未知（配方未知）；交易效益未證"
    strings = _xlsx_strings(output)
    for text in ("「h1pre_v9_future」", "不在本程式已知的配方清單", "資料出處：無（配方「h1pre_v9_future」沒有對應的驗證報告）",
                 "命中率指收盤報價方向，不代表交易獲利；隔日開盤買進 Top10 保留期報酬未知（配方未知）。"):
        assert text in html and text in strings
    for text in (*V1_ONLY_TEXTS, "AUC 0.619", "命中 65.3%", "約為零", "H1改良研究報告_20261021.md"):
        assert text not in html and text not in strings


def test_predict_auto_falls_back_to_c0_when_snapshot_is_stale(loaded_scope, model, monkeypatch, tmp_path, preopen_fixture):
    _write(preopen_fixture, signal_date=T, target_date=D)
    captured, payload, html, output, manifest = _run(loaded_scope(tpex_index=fx.D_INDEX), model, monkeypatch, tmp_path)
    context = payload["report_context"]
    assert payload["model_source"] == manifest["model_source"] == context["model_source"] == "c0"
    assert payload["preopen_status"]["status"] == "unavailable"
    assert payload["preopen_status"]["reason"] == manifest["preopen_status"]["reason"] == "preopen_stale"
    assert payload["preopen_meta"] is None and payload["model_disclosure"] is None
    assert context["ranking_status"] != "preopen_model_ranking"
    assert context["ranking_status"] == context["c0_ranking_status"]
    banner = "開盤前版不可用（preopen_stale：開盤前版快照的訊號日不是本次台股最新交易日），本次 H1 改用 C0 基準"
    assert banner in html and re.search(r'class="fallback">開盤前版不可用（preopen_stale', html)
    assert banner in _xlsx_strings(output)
    assert "研究展示（最多十檔）" in html and "可交易名次" not in html
    assert len(payload["predictions"]) == len(fx.STOCKS)
    assert payload["meta"]["warnings"][0].startswith("開盤前版不可用（preopen_stale")


def test_predict_preopen_required_fails_before_forward_ledger(loaded_scope, model, monkeypatch, tmp_path):
    scope = loaded_scope()
    # preopen 模式不可用時，在 C0 推論與前瞻帳冊寫入之前就失敗。
    monkeypatch.setattr(fx.contract, "predict_c0_by_market", lambda *a, **k: pytest.fail("C0 forecast must not run"))
    with pytest.raises(preopen_source.PreopenRequiredError, match="preopen_required_unavailable:preopen_no_latest"):
        _run(scope, model, monkeypatch, tmp_path, source="preopen")
    assert not (tmp_path / "ns").exists()


def test_predict_forced_c0_keeps_old_behaviour_with_fresh_snapshot(loaded_scope, model, monkeypatch, tmp_path,
                                                                   preopen_fixture):
    _write(preopen_fixture)
    monkeypatch.setattr(preopen_source, "load_reader",
                        lambda *a, **k: pytest.fail("forced C0 must not read the preopen snapshot"))
    captured, payload, html, _, manifest = _run(loaded_scope(), model, monkeypatch, tmp_path, source="c0")
    context = payload["report_context"]
    assert payload["model_source"] == "c0" and payload["requested_model_source"] == "c0"
    assert payload["preopen_status"]["status"] == "not_requested"
    assert payload["preopen_meta"] is None
    assert context["base_session_by_market"] == {"TPEX": T, "TWSE": D}
    assert context["ranking_status"] == context["c0_ranking_status"]
    assert "開盤前版不可用" not in html and "H1 模型來源：C0（依設定強制使用 C0" in html
    assert len(payload["predictions"]) == len(fx.STOCKS)
    c0_rows = {(row.market, row.stock_id): row for row in captured["forecast"].itertuples()}
    assert {(row["market"], row["stock_id"]) for row in payload["predictions"]} == {(market, stock) for stock, market in fx.STOCKS}
    for row in payload["predictions"]:
        expected = c0_rows[(row["market"], row["stock_id"])]
        assert row["probability"] == pytest.approx(expected.prob_up)
        assert row["expected_return"] == pytest.approx(expected.expected_return)
    no_signal = (context["ranking_status"] in reporting.NO_STOCK_RANKING_STATUSES
                 or context["no_stock_specific_model_signal"] is True)
    expected_order = sorted(payload["predictions"], key=(
        (lambda row: (row["market"], row["stock_id"])) if no_signal else
        (lambda row: (-row["probability"], -row["expected_return"], row["market"], row["stock_id"]))))
    assert payload["predictions"] == expected_order
    # Top10 alone receives the existing historical comparison presentation fields.
    comparison_fields = {"report_comparison", "report_comparison_rows", "report_comparison_summary"}
    assert len(payload["top10"]) == min(10, len(payload["predictions"]))
    for top_row, prediction in zip(payload["top10"], payload["predictions"][:10]):
        assert comparison_fields.isdisjoint(prediction)
        assert set(top_row) == set(prediction) | comparison_fields
        assert {key: value for key, value in top_row.items() if key not in comparison_fields} == prediction


def test_predict_falls_back_when_snapshot_target_differs_from_verified_session(loaded_scope, model, monkeypatch,
                                                                              tmp_path, preopen_fixture):
    _write(preopen_fixture, target_date=fx.DATES[fx.D_INDEX + 2])
    _, payload, html, _, _ = _run(loaded_scope(), model, monkeypatch, tmp_path)
    assert payload["model_source"] == "c0"
    assert payload["preopen_status"]["reason"] == "preopen_target_mismatch"
    assert "開盤前版不可用（preopen_target_mismatch" in html


def test_predict_preopen_required_target_mismatch_raises(loaded_scope, model, monkeypatch, tmp_path, preopen_fixture):
    _write(preopen_fixture, target_date=fx.DATES[fx.D_INDEX + 2])
    with pytest.raises(preopen_source.PreopenRequiredError, match="preopen_target_mismatch"):
        _run(loaded_scope(), model, monkeypatch, tmp_path, source="preopen")


# ---------------------------------------------------------------- report contract units


def _preopen_context(**overrides):
    context = dict(effective_profile="C0", horizon=1, price_basis="adjusted_close_return", model_source="preopen",
                   ranking_status="preopen_model_ranking",
                   preopen_meta={"run_id": "r", "model_sha256": "m" * 64, "snapshot_sha256": "s" * 64})
    context.update(overrides)
    return context


def test_report_contract_requires_preopen_identity_and_ranking():
    with pytest.raises(ValueError, match="current_report_preopen_meta_missing"):
        reporting._current_context(_preopen_context(preopen_meta={"run_id": "r"}), {}, {})
    with pytest.raises(ValueError, match="current_report_preopen_ranking_status_mismatch"):
        reporting._current_context(_preopen_context(ranking_status="all_tied_baseline"), {}, {})
    with pytest.raises(ValueError, match="current_report_preopen_ranking_without_preopen_source"):
        reporting._current_context(_preopen_context(model_source="c0"), {}, {})
    with pytest.raises(ValueError, match="current_report_model_source_invalid"):
        reporting._current_context(_preopen_context(model_source="mix"), {}, {})
    context = reporting._current_context(_preopen_context(), {}, {})
    assert context["trading_effect_status"].startswith("開盤買進 Top10 保留期平均為負")


@pytest.mark.parametrize("meta,expected", [
    (None, "開盤買進 Top10 保留期平均為負；交易效益未證"),
    ({"recipe": V2_RECIPE}, "開盤買進 Top10 保留期平均約為零（約 +0.01%／日）；交易效益未證"),
    ({"recipe": UNKNOWN_RECIPE}, "開盤買進 Top10 保留期報酬未知（配方未知）；交易效益未證"),
], ids=["v1_absent_recipe", "v2", "unknown_recipe"])
def test_trading_effect_status_follows_disclosed_recipe(meta, expected):
    disclosed = _preopen_context(model_disclosure=preopen_source.disclosure_record(meta))
    assert reporting._current_context(disclosed, {}, {})["trading_effect_status"] == expected
    # C0 來源不受配方影響。
    c0 = _preopen_context(model_source="c0", ranking_status="model_values_displayed_effect_unknown",
                          model_disclosure=preopen_source.disclosure_record(meta))
    assert reporting._current_context(c0, {}, {})["trading_effect_status"] == "交易效益未驗"


@pytest.mark.parametrize("meta,entry,declared", [
    (None, "h1pre_final_nogc_xs", None),
    ({}, "h1pre_final_nogc_xs", None),
    ({"recipe": None}, "h1pre_final_nogc_xs", None),
    ({"recipe": V1_RECIPE}, "h1pre_final_nogc_xs", "h1pre_final_nogc_xs"),
    ({"recipe": V2_RECIPE}, "h1pre_v2_ens", "h1pre_v2_ens"),
    ({"recipe": UNKNOWN_RECIPE}, "unknown", "h1pre_v9_future"),
    ({"recipe": {"frozen_sha256": "0" * 64}}, "unknown", ""),
    ({"recipe": "h1pre_v2_ens"}, "unknown", ""),
], ids=["meta_none", "recipe_key_absent", "recipe_null", "v1", "v2", "unknown_name", "recipe_without_name",
        "recipe_not_a_dict"])
def test_disclosure_table_is_keyed_by_snapshot_recipe_name(meta, entry, declared):
    record = preopen_source.disclosure_record(meta)
    assert (record["recipe"], record["recipe_entry"]) == (declared, entry)
    assert record["title"] == "必要揭露（開盤前版 H1）"
    text = "\n".join(record["lines"]) + "\n" + record["source"]
    if entry == "h1pre_final_nogc_xs":
        assert record["lines"] == V1_LINES and record["source"] == V1_SOURCE
        assert record["trading_effect"] == "開盤買進 Top10 保留期平均為負"
        labels = {gate: label for gate, (_, label) in V1_GATE.items()}
    elif entry == "h1pre_v2_ens":
        for number in V2_DISCLOSURE_NUMBERS:
            assert number in text
        assert record["trading_effect"] == "開盤買進 Top10 保留期平均約為零（約 +0.01%／日）"
        labels = {True: "建議日：前一晚美股收漲（保留期此條件下 Top10 命中 65.3%）",
                  False: "非建議日：保留期此條件下 Top10 命中約 49.1%（前一晚美股未收漲）",
                  None: V1_GATE[None][1]}
    else:
        assert "「" + (declared or "（未提供名稱）") + "」" in text and "不在本程式已知的配方清單" in text
        assert record["trading_effect"] == "開盤買進 Top10 保留期報酬未知（配方未知）"
        assert not re.search(r"AUC 0\.\d|為負|約為零|\d+\.\d%", text)
        labels = {True: "建議日：前一晚美股收漲（配方未知：此條件下的保留期命中率未驗證）",
                  False: "非建議日：前一晚美股未收漲（配方未知：此條件下的保留期命中率未驗證）",
                  None: V1_GATE[None][1]}
    if entry != "h1pre_final_nogc_xs":
        for marker in V1_ONLY_TEXTS:
            assert marker not in text
    # 閘門狀態碼不因配方改變；文字依配方。
    for gate, code in ((True, "recommended_day"), (False, "not_recommended_day"), (None, "unknown"), ("x", "unknown")):
        record_gate = preopen_source.gate_record(gate, meta)
        key = gate if gate in (True, False) else None
        assert record_gate == {"gate_us_up": key, "code": code, "label": labels[key], "recommended": gate is True}


def test_v1_constants_and_single_argument_gate_record_are_unchanged():
    assert list(preopen_source.DISCLOSURE) == V1_LINES and preopen_source.DISCLOSURE_SOURCE == V1_SOURCE
    assert preopen_source.GATE == V1_GATE
    for gate, (code, label) in V1_GATE.items():
        assert preopen_source.gate_record(gate) == {"gate_us_up": gate, "code": code, "label": label,
                                                    "recommended": gate is True}
    long_name = "x" * 200
    shown = preopen_source.disclosure_record({"recipe": {"name": long_name}})
    assert shown["recipe"] == long_name and "「" + "x" * preopen_source.RECIPE_NAME_SHOWN_MAX + "」" in shown["lines"][1]


def test_preopen_top10_holds_only_tradable_rows_when_fewer_than_ten():
    rows = []
    for index, (probability, tradable) in enumerate([(0.7, True), (0.9, False), (0.6, True), (0.5, False), (0.4, True)]):
        rows.append(dict(stock_id=str(1000 + index), market="TWSE", probability=probability, expected_return=0.001,
                         tradable=tradable))
    frame = pd.DataFrame(rows)
    tradable = frame[frame.tradable].sort_values("probability", ascending=False)
    rest = frame[~frame.tradable].sort_values("probability", ascending=False)
    ordered = pd.concat([tradable, rest])
    frame["preopen_display_rank"] = [list(ordered.index).index(index) + 1 for index in frame.index]
    ordered_rows, top10 = reporting._preopen_rows(frame)
    assert [row["stock_id"] for row in top10] == ["1000", "1002", "1004"]
    assert [row["stock_id"] for row in ordered_rows][3:] == ["1001", "1003"]
    broken = frame.assign(preopen_display_rank=[1, 2, 3, 4, 5])
    with pytest.raises(ValueError, match="current_report_preopen_top10_not_tradable_first"):
        reporting._preopen_rows(broken)


def test_html_shows_snapshot_declared_target_only_when_calendar_unverified(tmp_path):
    row = dict(stock_id="2330", market="TWSE", name="台積電", probability=0.6, expected_return=0.01,
               tradable=True, rank_tradable=1, display_order=1)
    payload = {"model_source": "preopen", "predictions": [row], "top10": [row], "coverage": [],
               "meta": {"asof": "20261016", "warnings": []}, "generated_at": None,
               "preopen_meta": {"target_date": "2026-10-19", "describe": "d", "run_id": "r"},
               "preopen_gate": {"code": "recommended_day", "label": "建議日：前一晚美股收漲"},
               "model_disclosure": {"title": "必要揭露（開盤前版 H1）", "lines": ["x"], "source": "s"},
               "report_context": {"target_session": None, "base_session": "20261016"}}
    unverified = tmp_path / "unverified.html"
    reporting._write_html(unverified, payload)
    assert "未驗證；保留空值（開盤前版快照宣告目標日 20261019，未經本程式日曆驗證）" in unverified.read_text(encoding="utf-8")
    payload["report_context"]["target_session"] = "20261019"
    verified = tmp_path / "verified.html"
    reporting._write_html(verified, payload)
    assert "快照宣告目標日" not in verified.read_text(encoding="utf-8")


# ---------------------------------------------------------------- CHECK_ENV


def test_check_preopen_reports_availability_and_fails_only_when_required(preopen_fixture, capsys):
    _write(preopen_fixture)
    summary = app.check_preopen(D)
    assert summary["status"] == "available" and summary["preopen_meta"]["run_id"].startswith("fixture_")
    assert "開盤前版快照可用" in capsys.readouterr().out
    stale = app.check_preopen(NEXT_D, "auto", "default")
    assert stale["reason"] == "preopen_stale" and "auto 模式執行時 H1 會改用 C0 基準" in stale["line"]
    with pytest.raises(RuntimeError, match="preopen_required_unavailable:preopen_stale"):
        app.check_preopen(NEXT_D, "preopen", "cli")
    # 檢查不寫任何檔案。
    files = sorted(path.relative_to(preopen_fixture.root).as_posix() for path in preopen_fixture.root.rglob("*") if path.is_file())
    stem = "predictions/" + D + "/fixture_" + D.replace("-", "")
    assert files == [stem + ".csv", stem + ".parquet", "predictions/latest.json"]


@pytest.mark.parametrize("recipe,label", [
    (_ABSENT, "建議日：前一晚美股收漲（保留期此條件下 Top10 命中 63.5%）"),
    (V2_RECIPE, "建議日：前一晚美股收漲（保留期此條件下 Top10 命中 65.3%）"),
    (UNKNOWN_RECIPE, "建議日：前一晚美股收漲（配方未知：此條件下的保留期命中率未驗證）"),
], ids=["v1_absent_recipe", "v2", "unknown_recipe"])
def test_check_preopen_gate_text_follows_snapshot_recipe(preopen_fixture, capsys, recipe, label):
    _write(preopen_fixture, recipe=recipe)
    summary = app.check_preopen(D)
    assert summary["status"] == "available" and summary["gate"]["code"] == "recommended_day"
    assert summary["gate"]["label"] == label
    if recipe is _ABSENT:
        assert "recipe" not in summary["preopen_meta"]
    else:
        assert summary["preopen_meta"]["recipe"] == recipe
    assert "開盤前版快照可用" in capsys.readouterr().out
