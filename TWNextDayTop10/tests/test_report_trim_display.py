"""報告精簡的條件式顯示：重複內容只寫一次，關鍵警示一定留一處；所有輸出都在 tmp_path。

純 Python 的部分直接呼叫 reporting 的顯示函式；Excel 的部分以 reporting.report 實際執行 excel_report.mjs。
"""
from __future__ import annotations

import csv
from datetime import datetime
import json
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

import preopen_source
import reporting
from test_available_data_display import ASOF, _view, _xlsx_sheet, available_case  # noqa: F401 - fixture


PROJECT = Path(__file__).resolve().parents[1]
NOW = datetime.fromisoformat("2026-10-24T12:00:00+08:00")
TPEX_DAY = "2026-10-21"
TARGET_MISMATCH = "snapshot target_date 2026-10-26 != C0 verified next session 2026-10-23"


def _data_time(stale=True, published=None, training="2026-10-30"):
    return {"analysis_asof": ASOF, "asof_by_market": {"TWSE": ASOF, "TPEX": TPEX_DAY},
            "head": {"published_at": published}, "model": {"training_asof": training}, "price_max_date": ASOF,
            "is_stale": stale, "freshness_status": "stale" if stale else "latest", "latest_expected_session": "2026-10-23",
            "stale_markets": {"TPEX": [TPEX_DAY, "2026-10-23"]} if stale else {}, "calendar_verified_asof": "2026-11-30"}


def _time_payload(context=None, labels=(), stale=True, **data_time):
    labels = [{"code": code} for code in labels]
    return {"data_time_display": reporting.data_time_display(_data_time(stale, **data_time), NOW, labels, ["data_time"]),
            "version_labels": labels, "report_context": context or {}, "meta": {"warnings": []}}


def _top_row(stock, **values):
    return {"stock_id": stock, "market": "TWSE", "name": "合成" + stock, "probability": 0.6, "expected_return": 0.01,
            "display_order": 1, "model_source_text": "開盤前版", "data_date_text": "20261022（快照訊號日）",
            "target_date_text": "20261023", "data_reminder": "快照未提供逐股行情日期", **values}


def _compared(stock, order, status="ok", day=ASOF, notes=("月營收來源狀態 PARTIAL；可讀部分保留，不宣稱完整。",)):
    return {"stock_id": stock, "name": "合成" + stock, "display_order": order,
            "report_comparison": {"status": status, "signal_date": day, "notes": list(notes)},
            "report_comparison_summary": ["月營收年增 1%"],
            "report_comparison_rows": [{"項目": "月營收同比", "資料期間": "2026-09", "本期值": "1", "比較": "+1%",
                                        "解讀": "增加", "資料日期": "2026-10-12"}]}


def _html(tmp_path, payload):
    path = tmp_path / (str(len(list(tmp_path.iterdir()))) + ".html")
    reporting._write_html(path, payload)
    return path.read_text(encoding="utf-8")


def _payload(**overrides):
    row = dict(stock_id="2330", market="TWSE", name="台積電", probability=0.6, expected_return=0.01,
               tradable=True, rank_tradable=1, display_order=1)
    payload = {"model_source": "c0", "predictions": [row], "top10": [row], "coverage": [],
               "meta": {"asof": "20261022", "warnings": []}, "generated_at": None,
               "report_context": {"base_session": "20261022", "target_session": "20261023"}}
    payload.update(overrides)
    return payload


def _sheets(output):
    """研究展示、全股票預測的儲存格，以及版本與資料、資料來源與限制的「A 欄 → B 欄」。"""
    with ZipFile(output) as archive:
        cells = [_xlsx_sheet(archive, f"xl/worksheets/sheet{index}.xml")[1] for index in range(1, 5)]
    pairs = [{sheet["A" + address[1:]]: value for address, value in sheet.items()
              if address.startswith("B") and "A" + address[1:] in sheet} for sheet in cells[2:]]
    return cells[0], cells[1], *pairs


def _report(predictions, coverage, meta, context, root):
    output = reporting.report(PROJECT, predictions, coverage, meta, {}, NOW, namespace_root=root, report_context=context)
    return output, json.loads((output.parent / "report.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- Top10 與歷史比較


def test_top10_common_line_only_for_columns_uniform_across_preopen_rows():
    rows = [_top_row("2330"), _top_row("2454")]
    columns, common = reporting._top10_columns({"model_source": "preopen", "top10": rows})
    # 全部同值：模型來源欄省略（見標題），資料日／本股目標日／資料提醒併成表上方一行；可交易欄只在全部股票表。
    assert columns == reporting.COLUMNS
    assert common == "各檔共同：資料日 20261022（快照訊號日）；本股目標日 20261023；快照未提供逐股行情日期。"
    rows[1].update(data_date_text="20261021", data_reminder="資料落後1交易日，依既有資料分析")
    columns, common = reporting._top10_columns({"model_source": "preopen", "top10": rows})
    assert [key for key, _ in columns[len(reporting.COLUMNS):]] == ["data_date_text", "data_reminder"]
    assert common == "各檔共同：本股目標日 20261023。"


def test_c0_top10_keeps_per_stock_date_columns_even_when_equal():
    rows = [_top_row(stock, model_source_text="C0", data_date_text="20261022", data_reminder="依本股資料日分析")
            for stock in ("2330", "2454")]
    columns, common = reporting._top10_columns({"model_source": "c0", "top10": rows})
    assert [key for key, _ in columns[len(reporting.COLUMNS):]] == ["data_date_text", "target_date_text", "data_reminder"]
    assert common == ""


def test_comparison_states_shared_day_and_notes_once_and_lists_unavailable_codes():
    unavailable = _compared("2454", 3, status="unavailable", notes=["比較不可得：本股未回傳有效歷史比較資料。"])
    fragment = reporting._comparison_html({"top10": [_compared("2330", 1), _compared("6488", 2), unavailable]})
    assert fragment.count("比較基準日：20261022") == 1 and fragment.count("可讀部分保留") == 1
    assert "歷史比較不可得（原預測結果保留）：2454" in fragment and "3. 2454" not in fragment
    # 有一檔的說明或比較基準日不同時，說明留在各檔，該檔另標基準日。
    other = _compared("6488", 2, day=TPEX_DAY, notes=["其他說明"])
    fragment = reporting._comparison_html({"top10": [_compared("2330", 1), other]})
    assert "比較基準日：20261021（與其他股票不同）" in fragment
    assert fragment.count("可讀部分保留") == 1 and "其他說明" in fragment


def test_comparison_all_unavailable_is_one_line():
    rows = [_compared(stock, order, status="unavailable", notes=["比較不可得（X: y）"])
            for order, stock in enumerate(("2330", "6488"), 1)]
    fragment = reporting._comparison_html({"top10": rows})
    assert "本次歷史比較不可得；原預測結果保留。" in fragment and fragment.count("不可得") == 1


# ---------------------------------------------------------------- 資料時間、版本提醒與資料提醒


def test_data_time_rows_keep_only_readable_differences():
    rows = dict(reporting._display_time(_time_payload())["rows"])
    # 流程時間戳、缺漏欄位（見資料來源與限制）、與分析日相同的價格最新日、未宣告的發布時間、沒有版本標籤時都不列。
    assert list(rows) == ["分析資料日", "各市場資料日", "報告產生時間", "日曆已驗證至", "模型訓練資料日", "資料新鮮度"]
    assert "落後市場：上櫃 資料日 20261021，應有 20261023" in rows["資料新鮮度"]
    for published in ("unknown", "UNVERIFIED"):
        assert not any("發布" in label for label, _ in reporting._display_time(_time_payload(published=published))["rows"])
    rows = dict(reporting._display_time(_time_payload(published="2026-10-22T10:00:00Z", training=None))["rows"])
    assert rows["資料發布時間（提供者宣告，未驗證）"] == "20261022 18:00:00 台北" and "模型訓練資料日" not in rows
    # C0 本次摘要已逐市場列出分析日時，表內不再重複各市場資料日；開盤前版（本次摘要沒有各市場日期）照列。
    c0 = _time_payload(context={"base_session_by_market": {"TWSE": ASOF, "TPEX": TPEX_DAY}})
    assert "各市場資料日" not in dict(reporting._display_time(c0)["rows"])


def test_version_labels_are_named_or_counted_and_neutral_about_market_days():
    labels = ("market_asof_differs", "market_asof_differs_per_market_inference", "price_series_vintage_stale",
              "x_future_code", "y_future_code")
    rows = dict(reporting._display_time(_time_payload(labels=labels))["rows"])
    # 同義代碼合併；各市場資料日不寫分析方式（開盤前版以快照訊號日為準）；沒有中文的代碼寫項數。
    assert rows["版本提醒"] == "各市場資料日不同、部分股票價格資料版本較舊、其他版本標示有差異（2 項）"
    assert dict(reporting._display_time(_time_payload(labels=("x_future_code",)))["rows"])["版本提醒"] == "版本標示有差異（1 項）"


def test_upstream_warnings_already_shown_elsewhere_are_listed_once():
    payload = _time_payload(labels=("market_asof_differs_per_market_inference",))
    payload["meta"]["warnings"] = [
        reporting.C0_LIMITATION_WARNING, "非最新資料：分析資料日 2026-10-22，依已驗證日曆最近應有交易日 2026-10-23",
        "落後市場 TPEX：以自身資料日 2026-10-21 分析（應有 2026-10-23）",
        "版本標籤：market_asof_differs_per_market_inference（記錄：2026-10-22；實際：{}）",
        "版本標籤：other_code（記錄：1；實際：2）", "其他上游提醒"]
    display = reporting._display_time(payload)
    # 資料過舊與落後市場留在資料時間表（徽章與資料新鮮度列），版本標籤留在版本提醒列；其餘上游警告照列。
    assert reporting._shown_warnings(payload, display) == ["版本標籤：other_code（記錄：1；實際：2）", "其他上游提醒"]
    assert display["badge"] == "非最新資料" and "落後市場：" in dict(display["rows"])["資料新鮮度"]
    # 資料時間表沒有寫出同一狀況時，上游警告照列。
    fresh = _time_payload(stale=False)
    fresh["meta"]["warnings"] = payload["meta"]["warnings"][1:3]
    assert reporting._shown_warnings(fresh, reporting._display_time(fresh)) == fresh["meta"]["warnings"]


# ---------------------------------------------------------------- HTML 模型來源與本次摘要


def test_fallback_keeps_reason_code_once_and_snapshot_dates_in_chinese(tmp_path):
    note = "開盤前版不可用（preopen_target_mismatch：開盤前版快照的目標日與本次已驗證的下一交易日不同），本次 H1 改用 C0 基準"
    page = _html(tmp_path, _payload(model_source_note=note, preopen_status={
        "status": "unavailable", "reason": "preopen_target_mismatch", "detail": TARGET_MISMATCH}))
    assert page.count("preopen_target_mismatch") == 1 and page.count(note) == 1
    assert "原因詳情：快照目標日 20261026／已驗證下一交易日 20261023" in page and "snapshot target_date" not in page
    page = _html(tmp_path, _payload(model_source_note=note, preopen_status={
        "status": "unavailable", "reason": "preopen_stale", "detail": "快照訊號日 20261021，預期 20261022"}))
    assert "原因詳情：快照訊號日 20261021，預期 20261022" in page


def test_preopen_html_drops_describe_parts_shown_elsewhere_but_keeps_warnings(tmp_path):
    describe = ("開盤前版 H1（HGB，含前一晚美股）｜訊號日 20261022 → 目標日 20261023｜"
                "前一晚美股收盤缺漏（以缺值推論，準確度可能下降）｜美股未收漲（非建議日）｜模型 cccccccccccc")
    payload = _payload(model_source="preopen", preopen_meta={"describe": describe, "run_id": "r"},
                       preopen_gate={"code": "not_recommended_day", "label": "非建議日：歷史 Top10 命中僅約 55%（前一晚美股未收漲）"},
                       model_disclosure={"title": "必要揭露（開盤前版 H1）", "lines": ["x"], "source": "s"})
    payload["report_context"].update(ranking_note=preopen_source.PREOPEN_RANKING_NOTE, c0_ranking_note="基準同值，無個股排序訊號")
    page = _html(tmp_path, payload)
    # 美股漲跌只在 Top10 上方閘門寫一次；訊號日→目標日與本次摘要相同時不重複；「準確度可能下降」照列。
    assert "<p>開盤前版 H1（HGB，含前一晚美股）｜前一晚美股收盤缺漏（以缺值推論，準確度可能下降）</p>" in page
    assert page.count("非建議日") == 1 and "cccccccccccc" not in page and "訊號日 20261022" not in page
    assert "分析日：20261022；下一交易日：20261023。" in page
    # 排序規則已在 Top10 標題；C0 備援的排序狀態在本次摘要寫一次（不帶內部欄名）。
    assert reporting.PREOPEN_RANKING_RULE not in page and "C0 備援排序狀態：基準同值，無個股排序訊號。" in page
    assert "c0_probability" not in page
    # 快照日期和本次摘要不同時（例如沿用較舊快照），訊號日→目標日照列。
    payload["preopen_meta"]["describe"] = describe.replace("訊號日 20261022", "訊號日 20261021")
    assert "訊號日 20261021 → 目標日 20261023" in _html(tmp_path, payload)


# ---------------------------------------------------------------- CSV 與 Excel


def test_csv_marks_the_actual_top10_when_fewer_than_ten_are_tradable(available_case, tmp_path):
    view, coverage = _view(available_case)
    payload = reporting.prepare_payload(view, coverage, available_case.meta, {}, NOW, available_case.context)
    reporting._write_csv(tmp_path / "report.csv", payload)
    with (tmp_path / "report.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["資料類型"] == "全部已分析股票"]
    # 展示序號 3 的不可交易股（1101）不是 Top10；C0 補充分析列也不是。
    assert [row["股票代碼"] for row in rows if row["Top10"] == "是"] == ["2330", "2454"]
    assert next(row for row in rows if row["股票代碼"] == "1101")["展示序號"] == "3" and "研究展示" not in rows[0]


def test_excel_preopen_time_hint_status_column_and_source_reminders(available_case, monkeypatch, tmp_path):
    monkeypatch.setenv("TOP10_VISIBLE_REPORT_ROOT", str(tmp_path / "visible"))
    monkeypatch.delenv("TOP10_EXCEL_VERIFY", raising=False)
    view, coverage = _view(available_case)
    available_case.meta.update(data_time=_data_time(published="unknown"), version_labels=[{"code": "market_asof_differs"}],
        warnings=[reporting.C0_LIMITATION_WARNING, "非最新資料：分析資料日 2026-10-22，依已驗證日曆最近應有交易日 2026-10-23",
                  "版本標籤：market_asof_differs（記錄：2026-10-22；實際：{}）", "其他上游提醒"])
    output, payload = _report(view, coverage, available_case.meta, available_case.context, tmp_path / "a")
    top, everything, notes, lineage = _sheets(output)
    texts = list(top.values())
    # 研究展示唯一的資料時間提示：非最新徽章與開盤前版的各市場資料日（市場落後）；未宣告（unknown）的發布時間不列。
    assert "【非最新資料】各市場資料日 上櫃 20261021／上市 20261022" in texts and not any("資料發布" in text for text in texts)
    # Top10 共同說明與 HTML 同規則；可交易欄只在全股票預測。
    assert reporting._top10_columns(payload)[1] in texts and "可交易名次" not in texts
    assert "可交易名次" in everything.values()
    # 沒有未分析股票時，全股票預測不輸出分析狀態欄。
    assert "分析狀態／排除原因" not in everything.values()
    # 來源提醒只列其他位置沒有的上游警告；資料過舊與版本標籤在資料新鮮度、版本提醒列。
    assert notes["來源提醒"] == "其他上游提醒" and notes["版本提醒"] == "各市場資料日不同"
    assert notes["資料新鮮度"].startswith("非最新資料") and "模型版本" not in "".join(notes)
    # 缺值列（本例沒有當前身分核對日期、C0 排序狀態）不列。
    assert "當前身分核對日期" not in notes and "C0 備援排序狀態" not in notes
    assert lineage["原始報告資料"] == "完整紀錄保留於原始報告資料檔（report.json）。"
    excluded = pd.concat([coverage, pd.DataFrame([{"stock_id": "9999", "market": "TWSE", "name": "合成9999",
                                                   "status": "insufficient_history"}])], ignore_index=True)
    output, _ = _report(view, excluded, available_case.meta, available_case.context, tmp_path / "b")
    _, everything, _, _ = _sheets(output)
    assert "分析狀態／排除原因" in everything.values() and "歷史資料不足" in everything.values()


def test_excel_c0_fallback_detail_and_rows_not_repeated(available_case, monkeypatch, tmp_path):
    monkeypatch.setenv("TOP10_VISIBLE_REPORT_ROOT", str(tmp_path / "visible"))
    monkeypatch.delenv("TOP10_EXCEL_VERIFY", raising=False)
    choice = preopen_source.PreopenChoice("auto", "default", "c0", "unavailable", reason="preopen_target_mismatch",
                                          detail=TARGET_MISMATCH, expected_signal_date=ASOF)
    context = {"effective_profile": "C0", "horizon": 1, "price_basis": "adjusted_close_return",
               "ranking_status": "model_values_displayed_effect_unknown", "ranking_note": "依模型數值展示；排序效果與交易效益未驗",
               "analysis_asof": ASOF, "base_session": ASOF, "target_session": "2026-10-23",
               "base_session_by_market": {"TWSE": ASOF, "TPEX": TPEX_DAY}, **preopen_source.report_fields(choice)}
    c0 = available_case.c0
    output, payload = _report(c0, c0[["stock_id", "market", "name"]].assign(status="predicted"),
                              {"asof": ASOF, "warnings": [], "data_time": _data_time()}, context, tmp_path / "c")
    top, _, notes, lineage = _sheets(output)
    texts = list(top.values())
    # 回退原因（含代碼）只在紅字寫一次；原因詳情改寫成中文日期句。
    assert sum("preopen_target_mismatch" in text for text in texts + list(notes.values())) == 1
    assert "原因詳情：快照目標日 20261026／已驗證下一交易日 20261023" in texts
    # C0 的各市場日期在 D2，資料時間提示只留非最新徽章。
    assert "【非最新資料】" in texts and top["D2"] == "各市場：上櫃 20261021／上市 20261022"
    # 依模型數值展示時，排序狀態與 A4 相同、交易效果與使用限制相同，都不另列；使用限制照列。
    assert "排序狀態" not in notes and "模型與交易效果" not in notes and "現役模型版本" not in notes
    assert "展示順序依模型數值；排序效果與交易效益未驗。" in texts and "交易效益尚未確認" in lineage["使用限制"]
    assert payload["preopen_status"]["detail"] == TARGET_MISMATCH  # 原始資料不改寫
