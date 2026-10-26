"""Top10 report-only comparison integration; all source reads use a fake API.

The JSON/HTML render test writes only tmp_path and never invokes app.predict,
report(), the model, a forward ledger, Excel, or any formal latest pointer.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from html.parser import HTMLParser
import json

import pandas as pd
import pytest

import reporting


HEAD = "a" * 64
NEW_HEAD = "b" * 64
SIGNAL_DATE = "2026-10-16"


class ComparisonAPI:
    def __init__(self):
        self.calls = []
        self.rows_calls = []
        self.summary_calls = []
        self.omit_ids = set()
        self.failure = None
        self.source_head = None
        self.blocks = {}

    def build_comparisons(self, stock_ids, signal_date, *, central_root=None, head_sha=None):
        self.calls.append((list(stock_ids), signal_date, central_root, head_sha))
        if self.failure == "build":
            raise RuntimeError("synthetic_comparison_read_failure")
        self.blocks = {stock_id: {
            "schema": "synthetic_stock_report_comparison_v1", "stock_id": stock_id,
            "signal_date": signal_date, "source_head_sha256": self.source_head or head_sha or NEW_HEAD,
            "metrics": [
                {"row": {"項目": "月營收同比", "資料期間": "2026-09 對 2025-09", "本期值": "1200 萬元",
                         "比較": "+20.0%", "解讀": "營收較去年同期增加 <可算指標>", "資料日期": "2026-10-12"}},
                {"row": {"項目": "月營收月比", "資料期間": "2026-09 對 2026-08", "本期值": "1200 萬元",
                         "比較": "+9.1%", "解讀": "營收較上月增加", "資料日期": "2026-10-12"}},
            ],
            "notes": ["舊資料保留原資料日期，歷史變化不代表未來報酬。"],
        } for stock_id in stock_ids if stock_id not in self.omit_ids}
        return self.blocks

    def comparison_rows(self, block):
        self.rows_calls.append(block["stock_id"])
        if self.failure == "rows":
            raise ValueError("synthetic_rows_failure")
        return [dict(metric["row"]) for metric in block["metrics"]]

    def comparison_summary(self, block, max_items=3):
        self.summary_calls.append((block["stock_id"], max_items))
        if self.failure == "summary":
            raise ValueError("synthetic_summary_failure")
        return ["2026-09 營收較去年同期增加 20.0%，較上月增加 9.1%。", "資料日 2026-10-12。"][:max_items]


@pytest.fixture
def api(monkeypatch):
    value = ComparisonAPI()
    monkeypatch.setattr(reporting, "_comparison_module", lambda: value)
    return value


@pytest.fixture
def case(tmp_path):
    frame = pd.DataFrame([
        {"stock_id": str(1100 + index), "market": "TWSE", "name": "合成股票<測試>",
         "probability": 0.5 + index / 100, "expected_return": (index - 5) / 1000,
         "p_up": 0.5 + index / 100, "exp_ret": (index - 5) / 1000}
        for index in range(12)
    ])
    meta = {"asof": SIGNAL_DATE, "warnings": [], "data_time": {
        "analysis_asof": SIGNAL_DATE, "head": {"sha256": HEAD}},
        "shared_input_descriptor": {"sources": [{"root": str(tmp_path / "never_read_central"),
                                                   "manifest_sha256": HEAD}]}}
    context = {"effective_profile": "C0", "horizon": 1, "price_basis": "adjusted_close_return",
               "ranking_status": "model_values_displayed_effect_unknown", "model_source": "c0"}
    return frame, meta, context


def _payload(case):
    frame, meta, context = case
    return reporting.prepare_payload(frame, pd.DataFrame(), meta, {},
        datetime(2026, 10, 18, 12, tzinfo=reporting.TAIPEI), report_context=context)


def _assert_predictions_preserved(payload, frame):
    expected = frame.sort_values(["probability", "expected_return", "market", "stock_id"],
                                 ascending=[False, False, True, True], kind="mergesort")
    for key in ("stock_id", "probability", "expected_return", "p_up", "exp_ret"):
        assert [row[key] for row in payload["predictions"]] == expected[key].tolist()
    by_id = frame.set_index("stock_id")
    for row in payload["top10"]:
        for key in ("probability", "expected_return", "p_up", "exp_ret"):
            assert row[key] == by_id.loc[row["stock_id"], key]


def test_top10_batch_json_and_isolated_html_preserve_predictions(case, api, tmp_path):
    frame, meta, _ = case
    original_frame, original_meta = frame.copy(deep=True), deepcopy(meta)
    payload = _payload(case)
    ids = list(reversed(frame.stock_id.tolist()))[:10]
    assert api.calls == [(ids, SIGNAL_DATE, meta["shared_input_descriptor"]["sources"][0]["root"], HEAD)]
    assert api.rows_calls == ids
    assert api.summary_calls == [(stock_id, 3) for stock_id in ids]
    assert set(payload["meta"]["report_comparison"]) == set(ids)
    assert payload["report_comparison"] == payload["meta"]["report_comparison"]
    _assert_predictions_preserved(payload, frame)
    pd.testing.assert_frame_equal(frame, original_frame)
    assert meta == original_meta
    for row in payload["top10"]:
        assert row["report_comparison"]["signal_date"] == SIGNAL_DATE
        assert row["report_comparison"]["analysis_head_match"] is True
        assert row["report_comparison"]["metrics"] == api.blocks[row["stock_id"]]["metrics"]
    assert api.blocks[ids[0]]["notes"] == ["舊資料保留原資料日期，歷史變化不代表未來報酬。"]

    output = tmp_path / "isolated_report"
    output.mkdir()
    reporting._write_new_json(output / "report.json", payload)
    reporting._write_html(output / "report.html", payload)
    saved = json.loads((output / "report.json").read_text(encoding="utf-8"))
    page = (output / "report.html").read_text(encoding="utf-8")
    assert saved["meta"]["report_comparison"][ids[0]]["source_head_sha256"] == HEAD
    assert "前十檔個股歷史比較與中文解讀" in page
    for label in ("項目", "資料期間", "本期值", "比較", "解讀", "資料日期"):
        assert "<th>" + label + "</th>" in page
    assert "202609 對 202509" in page and "20261012" in page
    assert "月營收同比" in page and "月營收月比" in page
    assert "營收較去年同期增加 20.0%" in page
    assert "比較基準日：20261016" in page
    assert saved["meta"]["report_comparison"][ids[0]]["signal_date"] == SIGNAL_DATE
    assert saved["meta"]["report_comparison"][ids[0]]["metrics"][0]["row"]["資料日期"] == "2026-10-12"
    assert payload["top10"][0]["report_comparison_summary"][0].startswith("2026-09 ")
    assert "&lt;可算指標&gt;" in page and "<可算指標>" not in page
    assert len(api.calls) == 1  # Rendering must use saved rows, never reread Central.
    assert not list(tmp_path.rglob("latest.json"))


def test_absent_analysis_head_passes_none_and_discloses_new_pin(case, api):
    case[1]["data_time"].pop("head")
    case[1].pop("shared_input_descriptor")
    payload = _payload(case)
    assert api.calls[0][1:] == (SIGNAL_DATE, None, None)
    block = payload["top10"][0]["report_comparison"]
    assert block["source_head_sha256"] == NEW_HEAD
    assert block["analysis_source_head_sha256"] is None
    assert block["analysis_head_match"] is None
    assert block["source_pin_mode"] == "new_comparison_pin"
    assert any("另行釘選" in note and "不宣稱與模型同 head" in note for note in block["notes"])


def test_descriptor_supplies_pinned_head_when_data_time_has_none(case, api):
    case[1]["data_time"].pop("head")
    case[1]["asof"] = "20261016"
    payload = _payload(case)
    assert api.calls[0][1] == SIGNAL_DATE
    assert api.calls[0][3] == HEAD
    assert payload["top10"][0]["report_comparison"]["analysis_head_match"] is True


@pytest.mark.parametrize("failure", ["import", "build"])
def test_comparison_unavailable_keeps_predictions_and_renders_notice(case, api, monkeypatch, tmp_path, failure):
    if failure == "import":
        def unavailable():
            raise ModuleNotFoundError("synthetic_missing_comparison_module")
        monkeypatch.setattr(reporting, "_comparison_module", unavailable)
    else:
        api.failure = failure
    payload = _payload(case)
    _assert_predictions_preserved(payload, case[0])
    assert len(payload["top10"]) == 10
    assert all(row["report_comparison"]["status"] == "unavailable" for row in payload["top10"])
    reporting._write_html(tmp_path / "unavailable.html", payload)
    page = (tmp_path / "unavailable.html").read_text(encoding="utf-8")
    assert "比較不可得" in page
    assert "原預測結果保留" in page


@pytest.mark.parametrize("failure", ["rows", "summary"])
def test_presentation_failure_preserves_existing_comparison_metrics(case, api, tmp_path, failure):
    api.failure = failure
    payload = _payload(case)
    _assert_predictions_preserved(payload, case[0])
    block = payload["top10"][0]["report_comparison"]
    assert len(block["metrics"]) == 2
    assert any("原始比較資料保留" in note for note in block["notes"])
    reporting._write_html(tmp_path / "partial.html", payload)
    assert "原始比較資料保留" in (tmp_path / "partial.html").read_text(encoding="utf-8")
    assert len(api.calls) == 1


def test_one_missing_stock_does_not_discard_other_stock_data(case, api):
    api.omit_ids = {"1111"}
    payload = _payload(case)
    assert payload["top10"][0]["report_comparison"]["status"] == "unavailable"
    assert len(payload["top10"][1]["report_comparison"]["metrics"]) == 2
    _assert_predictions_preserved(payload, case[0])


def test_preopen_compares_only_displayed_tradable_top10_without_reordering(case, api):
    frame = case[0].iloc[:4].copy()
    frame["tradable"] = [True, False, True, False]
    frame["preopen_display_rank"] = [2, 3, 1, 4]
    context = dict(case[2], model_source="preopen", ranking_status="preopen_model_ranking",
                   preopen_meta={"run_id": "synthetic", "model_sha256": "c" * 64, "snapshot_sha256": "d" * 64})
    payload = _payload((frame, case[1], context))
    assert [row["stock_id"] for row in payload["top10"]] == ["1102", "1100"]
    assert [row["stock_id"] for row in payload["predictions"]] == ["1102", "1100", "1101", "1103"]
    assert api.calls[0][0] == ["1102", "1100"]
    for row in payload["predictions"]:
        source = frame.loc[frame.stock_id.eq(row["stock_id"])].iloc[0]
        assert row["probability"] == source.probability
        assert row["expected_return"] == source.expected_return
        assert row["p_up"] == source.p_up and row["exp_ret"] == source.exp_ret


def test_missing_analysis_day_does_not_substitute_computer_today(case, api):
    case[1].pop("asof")
    case[1]["data_time"].pop("analysis_asof")
    payload = _payload(case)
    assert api.calls == []
    assert payload["top10"][0]["report_comparison"]["signal_date"] is None
    assert payload["top10"][0]["report_comparison"]["status"] == "unavailable"
    _assert_predictions_preserved(payload, case[0])


def test_different_returned_head_is_labeled_without_claiming_a_match(case, api):
    api.source_head = NEW_HEAD
    payload = _payload(case)
    block = payload["top10"][0]["report_comparison"]
    assert block["analysis_head_match"] is False
    assert block["source_head_sha256"] == NEW_HEAD
    assert any("未證實與本次分析 head 相同" in note for note in block["notes"])
    _assert_predictions_preserved(payload, case[0])

def test_visible_comparison_dates_and_chinese_notes_preserve_machine_data(case, api):
    case[1]["data_time"].pop("head")
    case[1].pop("shared_input_descriptor")
    payload = _payload(case)
    row = payload["top10"][0]
    block = row["report_comparison"]
    row["report_comparison_rows"][0]["資料期間"] = "2024、2025、2026 年 09 月"
    row["report_comparison_rows"].append({
        "項目": "歷史比較", "資料期間": "截至分析日 2026-10-16", "本期值": "未提供", "比較": "比較不可得",
        "解讀": "來源有缺；歷史比較不可得：ValueError source_sha256 mismatch", "資料日期": "未提供",
    })
    block["notes"].extend([
        "分析來源未提供；比較另行釘選中央最新來源 " + NEW_HEAD + "，不宣稱與分析或模型同一來源。",
        "月營收來源狀態 PARTIAL；可讀部分保留，不宣稱完整。",
        r"歷史比較不可得：ValueError: source_sha256 mismatch at D:\private\head.json",
        "中文摘要不可得（RuntimeError: source_file_unavailable）；原始比較資料保留。",
    ])
    before = deepcopy(payload)
    fragment = reporting._comparison_html(payload)
    assert "比較基準日：20261016" in fragment
    assert "202409、202509、202609" in fragment
    assert reporting._comparison_display_text("2026-09～2026-10") == "202609～202610"
    assert reporting._comparison_display_text("2026-10-12～2026-10-16") == "20261012～20261016"
    assert "202609 營收" in fragment and "20261012" in fragment
    assert "2026-10-16" not in fragment and "2026-10-12" not in fragment
    assert "2026-09" not in fragment
    assert "部分完成" in fragment
    assert "分析資料來源未能核對" in fragment
    assert "原始比較資料保留" in fragment
    visible_chunks = []

    class TextOnly(HTMLParser):
        def handle_data(self, data):
            visible_chunks.append(data)

    parser = TextOnly()
    parser.feed(fragment)
    parser.close()
    visible_text = " ".join(visible_chunks)
    for implementation_text in (HEAD, NEW_HEAD, "head", "source", "sha256", "ValueError", "RuntimeError", "PARTIAL", "D:\\private"):
        assert implementation_text.lower() not in visible_text.lower()
    assert payload == before
    assert block["source_head_sha256"] == NEW_HEAD
    assert block["signal_date"] == SIGNAL_DATE
