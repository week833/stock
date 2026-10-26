"""Available older data stay visible with their real dates; all outputs use tmp_path."""
from __future__ import annotations

import csv
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import numpy as np
import pandas as pd
import pytest

import app
import preopen_source
import reporting
from test_app_read_flow_integrity import flow  # noqa: F401 - isolated app fixture


PROJECT = Path(__file__).resolve().parents[1]
ASOF = "2026-10-22"
NEXT = "2026-10-23"
OLDER = [
    ("1516", "TWSE", "2026-10-14", "2026-10-15", 6, False),
    ("1589", "TWSE", "2026-04-02", "2026-04-07", 137, True),
    ("4804", "TPEX", "2026-04-13", "2026-04-14", 132, True),
    ("6142", "TWSE", "2026-10-14", "2026-10-15", 6, False),
]


@pytest.fixture
def available_case():
    c0 = pd.DataFrame([{
        "stock_id": stock, "market": market, "name": "合成" + stock,
        "probability": 0.6 + index / 100, "expected_return": 0.01,
        "prediction_status": "predicted", "model_source": "c0",
        "session_date": feature, "date": feature, "data_asof": feature,
        "feature_data_date": feature, "stock_last_price_date": feature,
        "analysis_asof": ASOF, "target_session_date": target,
        "data_lagged": True, "data_lag_sessions": lag, "in_sample_lookahead": in_sample,
    } for index, (stock, market, feature, target, lag, in_sample) in enumerate(OLDER)])
    frame = pd.DataFrame([
        {"stock_id": "2330", "market": "TWSE", "p_up": 0.55, "exp_ret": 0.02, "tradable": True, "rank": 2, "rank_tradable": 1},
        {"stock_id": "2454", "market": "TWSE", "p_up": 0.45, "exp_ret": 0.01, "tradable": True, "rank": 3, "rank_tradable": 2},
        {"stock_id": "1101", "market": "TWSE", "p_up": 0.95, "exp_ret": 0.03, "tradable": False, "rank": 1, "rank_tradable": None},
    ])
    frame["signal_date"], frame["target_date"] = ASOF, NEXT
    snapshot = SimpleNamespace(frame=frame, meta={"signal_date": ASOF, "target_date": NEXT,
        "run_id": "synthetic_available", "model_sha256": "a" * 64, "snapshot_sha256": "b" * 64,
        "snapshot_schema": "tw-stock-preopen-snapshot-v1"})
    snapshot.top = lambda count: snapshot.frame.loc[snapshot.frame.tradable.eq(True).fillna(False)].sort_values(
        ["p_up", "exp_ret", "market", "stock_id"], ascending=[False, False, True, True], kind="mergesort").head(count)
    universe = pd.DataFrame([{"stock_id": row.stock_id, "market": row.market, "name": "合成" + row.stock_id}
        for row in pd.concat([c0[["stock_id", "market"]], frame[["stock_id", "market"]]]).itertuples()])
    coverage = universe.assign(status="predicted")
    choice = preopen_source.PreopenChoice("auto", "default", "preopen", "available", snapshot=snapshot,
        expected_signal_date=ASOF, describe_text="合成快照")
    context = {"effective_profile": "C0", "horizon": 1, "price_basis": "adjusted_close_return",
        "ranking_status": preopen_source.PREOPEN_RANKING_STATUS, "analysis_asof": ASOF,
        "base_session": ASOF, "target_session": None, "calendar_verified": False,
        **preopen_source.report_fields(choice)}
    return SimpleNamespace(c0=c0, snapshot=snapshot, universe=universe, coverage=coverage,
        context=context, meta={"asof": ASOF, "warnings": []})


def _view(case):
    return preopen_source.build_view(case.snapshot, case.c0, case.universe, case.coverage, engine_id="tw_nextday_top10")


def test_stale_forecast_does_not_block_report_or_invent_current_target(flow, tmp_path):
    flow.inputs["metadata"]["asof"] = ASOF
    flow.inputs["asof_by_market"] = {"TWSE": ASOF}
    flow.inputs["market_sessions"] = {"TWSE": ["2026-10-14", "2026-10-15", ASOF, NEXT]}
    flow.forecast = flow.forecast.assign(session_date="2026-10-14", feature_data_date="2026-10-14",
        data_asof="2026-10-14", stock_last_price_date="2026-10-14", analysis_asof=ASOF,
        target_session_date="2026-10-15", data_lagged=True, data_lag_sessions=6)
    app.predict(namespace_root=tmp_path / "isolated", no_open=True, model_source="c0")
    assert len(flow.reports) == len(flow.forward_calls) == 1
    result = flow.reports[0]
    assert result["context"]["base_session"] == ASOF
    assert result["context"]["target_session"] is None
    assert result["predictions"].date.tolist() == ["2026-10-14"]
    assert result["predictions"].target_session_date.tolist() == ["2026-10-15"]
    assert result["predictions"].probability.tolist() == [0.75]


def test_stale_target_is_excluded_from_current_market_target(available_case):
    fresh = available_case.c0.iloc[[0]].assign(stock_id="2330", session_date=ASOF,
        feature_data_date=ASOF, data_asof=ASOF, target_session_date=NEXT, data_lagged=False)
    rows = pd.concat([available_case.c0, fresh], ignore_index=True)
    base, targets = app.market_sessions(rows, {"TWSE": ASOF, "TPEX": ASOF}, ASOF)
    assert base == {"TPEX": ASOF, "TWSE": ASOF}
    assert targets == {"TPEX": None, "TWSE": NEXT}


def test_c0_supplements_keep_preopen_top10_order_and_values(available_case):
    view, coverage = _view(available_case)
    payload = reporting.prepare_payload(view, coverage, available_case.meta, {},
        datetime.fromisoformat("2026-10-24T12:00:00+08:00"), available_case.context)
    expected = available_case.snapshot.top(10)
    assert [row["stock_id"] for row in payload["top10"]] == expected.stock_id.tolist()
    assert [row["probability"] for row in payload["top10"]] == expected.p_up.tolist()
    assert [row["expected_return"] for row in payload["top10"]] == expected.exp_ret.tolist()
    assert [row["stock_id"] for row in payload["predictions"][:3]] == ["2330", "2454", "1101"]
    supplements = payload["predictions"][3:]
    assert len(supplements) == 4
    assert all(row["model_source"] == "c0" and row["tradable"] is None and row["rank_tradable"] is None for row in supplements)
    assert len({(row["market"], row["stock_id"]) for row in payload["predictions"]}) == 7
    assert coverage.status.tolist().count("c0_supplement_predicted") == 4
    by_stock = {row["stock_id"]: row for row in supplements}
    for stock, _, feature, target, lag, in_sample in OLDER:
        row = by_stock[stock]
        assert row["feature_data_date"] == feature.replace("-", "")
        assert row["analysis_asof"] == ASOF.replace("-", "")
        assert row["target_session_date"] == target.replace("-", "")
        assert row["data_date_text"] == feature.replace("-", "")
        assert row["target_date_text"] == target.replace("-", "") + "（歷史）"
        assert f"資料落後{lag}交易日" in row["data_reminder"]
        assert ("歷史資料試算／模型訓練晚於資料日" in row["data_reminder"]) is in_sample


@pytest.mark.parametrize("unknown", [None, np.nan, pd.NA, "unknown"])
def test_unknown_tradability_is_not_truthy_or_in_top10(available_case, unknown):
    available_case.snapshot.frame["tradable"] = available_case.snapshot.frame.tradable.astype(object)
    available_case.snapshot.frame.loc[2, "tradable"] = unknown
    view, _ = _view(available_case)
    rows, top = reporting._preopen_rows(view)
    assert [row["stock_id"] for row in top] == ["2330", "2454"]
    assert next(row for row in rows if row["stock_id"] == "1101")["tradable"] is None
    assert all(row["tradable"] is None for row in rows if row["model_source"] == "c0")


def _xlsx_sheet(archive, name):
    namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    shared = []
    if "xl/sharedStrings.xml" in archive.namelist():
        shared = ["".join(item.itertext()) for item in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
    root = ET.fromstring(archive.read(name))
    cells = {}
    for cell in root.findall(".//s:sheetData/s:row/s:c", namespace):
        raw = cell.find("s:v", namespace)
        value = raw.text if raw is not None else "".join(cell.find("s:is", namespace).itertext()) if cell.find("s:is", namespace) is not None else ""
        cells[cell.attrib["r"]] = shared[int(value)] if cell.attrib.get("t") == "s" else value
    return root, cells


def test_all_formats_share_actual_dates_and_isolated_report_stays_publishable(available_case, monkeypatch, tmp_path):
    view, coverage = _view(available_case)
    monkeypatch.setenv("TOP10_VISIBLE_REPORT_ROOT", str(tmp_path / "visible"))
    monkeypatch.delenv("TOP10_EXCEL_VERIFY", raising=False)
    output = reporting.report(PROJECT, view, coverage, available_case.meta, {},
        datetime.fromisoformat("2026-10-24T12:00:00+08:00"), namespace_root=tmp_path / "reports_only",
        report_context=available_case.context)
    folder = output.parent
    payload = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    html = (folder / "report.html").read_text(encoding="utf-8")
    with (folder / "report.csv").open(encoding="utf-8-sig", newline="") as handle:
        csv_rows = {row["股票代碼"]: row for row in csv.DictReader(handle) if row["資料類型"] == "全部已分析股票"}
    assert json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["status"] == "complete"
    assert payload["report_context"]["target_session"] is None
    assert "分析日：20261022" in html
    assert "快照訊號日" in payload["top10"][0]["data_date_text"]
    assert "快照未提供逐股行情日期" in payload["top10"][0]["data_reminder"]
    with ZipFile(output) as archive:
        assert not any(name.startswith("xl/tables/") for name in archive.namelist())
        top_root, top_cells = _xlsx_sheet(archive, "xl/worksheets/sheet1.xml")
        _, cells = _xlsx_sheet(archive, "xl/worksheets/sheet2.xml")
        assert top_cells["A2"] == "分析日"
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        styles = ET.fromstring(archive.read("xl/styles.xml"))
        formats = {item.attrib["numFmtId"]: item.attrib["formatCode"] for item in styles.find("s:numFmts", ns)}
        c2 = top_root.find(".//s:c[@r='C2']", ns)
        xfs = styles.find("s:cellXfs", ns)
        assert formats[xfs[int(c2.attrib["s"])].attrib["numFmtId"]] == "yyyymmdd"
        header_row = next(int(address[1:]) for address, value in top_cells.items() if address.startswith("A") and value == "展示序號")
        first_row = header_row + 1
        assert float(top_cells[f"E{first_row}"]) == pytest.approx(0.55)
        assert float(top_cells[f"F{first_row}"]) == pytest.approx(0.02)
        for column, code in (("E", "0.00%"), ("F", "+0.00%;-0.00%;0.00%")):
            cell = top_root.find(f".//s:c[@r='{column}{first_row}']", ns)
            format_id = xfs[int(cell.attrib["s"])].attrib["numFmtId"]
            assert formats.get(format_id, {"9": "0%", "10": "0.00%"}.get(format_id)) == code
        columns = {value: address.rstrip("0123456789") for address, value in cells.items() if address.rstrip("0123456789") + "1" == address}
        for stock, _, _, _, _, _ in OLDER:
            row = next(row for row in payload["predictions"] if row["stock_id"] == stock)
            excel_row = next(address[1:] for address, value in cells.items() if address.startswith("B") and value == stock)
            for raw, label in reporting.DATA_COLUMNS:
                expected = row[raw]
                assert expected in html
                assert csv_rows[stock][label] == expected
                assert cells[columns[label] + excel_row] == expected
