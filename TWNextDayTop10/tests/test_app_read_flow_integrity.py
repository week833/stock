"""Synthetic app/report boundaries; never read a model, Central data, or a ledger."""
from __future__ import annotations

import csv
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import app
import daily_contract
import preopen_source
import reporting


ASOF = "2026-10-16"
TARGET = "2026-10-19"


@pytest.fixture
def flow(monkeypatch, tmp_path):
    universe = pd.DataFrame([{"stock_id": "2330", "market": "TWSE", "name": "合成股票"}])
    forecast = pd.DataFrame([{
        "stock_id": "2330", "market": "TWSE", "session_date": ASOF,
        "target_session_date": TARGET, "prediction_status": "predicted",
        "prob_up": 0.6, "expected_return": 0.01, "calibration_status": "uncalibrated",
    }])
    inputs = {
        "market_sessions": {"TWSE": [ASOF, TARGET]}, "asof_by_market": {"TWSE": ASOF},
        "universe": universe, "evidence": {"calendar_verified_by_market": {"TWSE": True}},
        "policy_binding": {}, "metadata": {
            "asof": ASOF, "snapshot_fingerprint": "synthetic", "version_labels": [], "warnings": [],
            "c0_scope_coverage": {"excluded": []}, "data_time": {},
            "inference_run_time": "2026-10-18T12:00:00+08:00",
            "ordinary_identity_verified_asof": ASOF, "ordinary_identity_observed_at": ASOF,
        },
    }
    model = {"schema": "synthetic", "models_by_horizon": {"1": {}}, "metadata": {
        "feature_schema": "synthetic", "feature_order": ["return_5"],
        "target_definition": "synthetic H1", "source_evidence": {"eligible_stock_market_pairs": [["2330", "TWSE"]]},
    }}
    active = {"artifact_binding": {"sha256": "a" * 64, "qa_override": True}}
    state = SimpleNamespace(forecast=forecast, inputs=inputs, forward_calls=[], reports=[], snapshot_target=None)

    def choose(requested, origin, expected):
        if state.snapshot_target is not None:
            return preopen_source.PreopenChoice(requested, origin, "preopen", "available",
                expected_signal_date=expected, describe_text="synthetic snapshot",
                snapshot=SimpleNamespace(meta={"target_date": state.snapshot_target}))
        return preopen_source.PreopenChoice(requested, origin, "c0", "not_requested",
            reason="model_source_forced_c0", expected_signal_date=expected)

    def forward(frame, source_inputs, runtime, *, db_path=None):
        state.forward_calls.append(frame.copy(deep=True))
        calibrated = frame.copy(deep=True)
        calibrated["prob_up"] = 0.75
        calibrated["calibration_version"] = "forward-synthetic"
        return {"predictions": calibrated.to_dict("records"), "forward_binding": {"synthetic": True}}

    def capture_report(project, predictions, coverage, meta, metadata, moment, **kwargs):
        state.reports.append({"predictions": predictions.copy(deep=True), "coverage": coverage.copy(deep=True),
            "meta": meta, "context": kwargs["report_context"]})
        return tmp_path / "report.xlsx"

    contract = SimpleNamespace(predict_c0_by_market=lambda *a, **k: (None, state.forecast.copy(deep=True)),
                               process_c0_forward=forward)
    monkeypatch.setattr(daily_contract, "load_c0_model", lambda path=None:
        (SimpleNamespace(FEATURE_ORDER=("return_5",)), model, tmp_path / "model.json", active))
    monkeypatch.setattr(daily_contract, "shared_module", lambda *a: contract)
    monkeypatch.setattr(daily_contract, "bind_daily_snapshot", lambda *a, **k: {})
    monkeypatch.setattr(app, "runtime_report_bindings", lambda *a, **k: {})
    monkeypatch.setattr(app, "load_c0_inputs", lambda *a, **k: inputs)
    monkeypatch.setattr(app, "report", capture_report)
    monkeypatch.setattr(preopen_source, "choose", choose)
    return state


def test_required_target_mismatch_never_calls_forward(flow, tmp_path):
    flow.snapshot_target = "2026-10-20"
    with pytest.raises(preopen_source.PreopenRequiredError, match="preopen_target_mismatch"):
        app.predict(namespace_root=tmp_path / "output", no_open=True, model_source="preopen")
    assert flow.forward_calls == []
    assert flow.reports == []
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("failure,reason", [
    ("base", "C0_market_base_session_differs_from_market_asof"),
    ("empty", "C0_no_valid_model_predictions"),
    ("coverage", "C0_forecast_pool_accounting_incomplete"),
])
def test_invalid_forecast_scope_fails_before_forward(flow, tmp_path, failure, reason):
    flow.snapshot_target = TARGET
    if failure == "base":
        flow.forecast.loc[0, "session_date"] = "2026-10-15"
    elif failure == "empty":
        flow.forecast.loc[0, "prediction_status"] = "blocked"
    else:
        flow.inputs["universe"] = pd.concat([flow.inputs["universe"], pd.DataFrame([
            {"stock_id": "2454", "market": "TWSE", "name": "另一合成股票"}])], ignore_index=True)
    with pytest.raises(RuntimeError, match=reason):
        app.predict(namespace_root=tmp_path / "output", no_open=True, model_source="preopen")
    assert flow.forward_calls == []
    assert flow.reports == []


def test_auto_prints_only_final_fallback_and_keeps_calibrated_values(flow, tmp_path, capsys):
    flow.snapshot_target = "2026-10-20"
    app.predict(namespace_root=tmp_path / "output", no_open=True, model_source="auto")
    output = capsys.readouterr().out
    assert "preopen_target_mismatch" in output
    assert "H1 模型來源：開盤前版" not in output
    assert len(flow.forward_calls) == 1
    result = flow.reports[0]
    assert result["predictions"].probability.tolist() == [0.75]
    assert result["predictions"].expected_return.tolist() == [0.01]
    assert result["context"]["base_session"] == ASOF
    assert result["context"]["target_session"] == TARGET
    assert result["context"]["model_source"] == "c0"


@pytest.fixture
def report_case():
    frame = pd.DataFrame([
        {"stock_id": "2330", "market": "TWSE", "name": "=合成股票", "probability": 0.625, "expected_return": -0.0125},
        {"stock_id": "2454", "market": "TWSE", "name": None, "probability": 0.5, "expected_return": 0.002},
    ])
    meta = {"asof": ASOF, "warnings": []}
    context = {"effective_profile": "C0", "horizon": 1, "price_basis": "adjusted_close_return",
        "ranking_status": "model_values_displayed_effect_unknown", "model_source": "c0",
        "run_id": "synthetic_flow", "base_session": ASOF, "target_session": TARGET}
    return frame, meta, context


def test_report_context_checked_once_before_any_output_creation(report_case, monkeypatch, tmp_path):
    frame, meta, context = report_case
    checked = []
    validate = reporting._current_context

    def counted(*args):
        checked.append(True)
        return validate(*args)

    class OutputBoundary(RuntimeError):
        pass

    def stop_before_mkdir(*args, **kwargs):
        raise OutputBoundary("output creation boundary")

    monkeypatch.setattr(reporting, "_current_context", counted)
    monkeypatch.setattr(reporting, "excel_runtime", lambda project: (Path("node.exe"), Path("builder.mjs")))
    monkeypatch.setattr(Path, "mkdir", stop_before_mkdir)
    with pytest.raises(OutputBoundary):
        reporting.report(tmp_path, frame, pd.DataFrame(), meta, {}, datetime.fromisoformat("2026-10-18T12:00:00+08:00"),
            namespace_root=tmp_path / "output", report_context=context)
    assert checked == [True]
    assert not (tmp_path / "output").exists()


def test_invalid_report_context_cannot_create_output(report_case, monkeypatch, tmp_path):
    frame, meta, context = report_case
    context["effective_profile"] = "invalid"
    monkeypatch.setattr(reporting, "excel_runtime", lambda project: (Path("node.exe"), Path("builder.mjs")))
    with pytest.raises(ValueError, match="current_report_requires_C0_H1"):
        reporting.report(tmp_path, frame, pd.DataFrame(), meta, {}, datetime.fromisoformat("2026-10-18T12:00:00+08:00"),
            namespace_root=tmp_path / "output", report_context=context)
    assert not (tmp_path / "output").exists()


def test_csv_preserves_numbers_escaping_and_one_shared_row_context(report_case, monkeypatch, tmp_path):
    frame, meta, context = report_case
    payload = reporting.prepare_payload(frame, pd.DataFrame(), meta, {},
        datetime.fromisoformat("2026-10-18T12:00:00+08:00"), report_context=context)
    serialize = reporting.json.dumps
    row_context_calls = []

    def counted(value, *args, **kwargs):
        if isinstance(value, dict) and set(value) == {"model", "model_source", "preopen_run_id",
                "preopen_model_sha256", "target_session", "return_unit", "ranking_policy"}:
            row_context_calls.append(True)
        return serialize(value, *args, **kwargs)

    monkeypatch.setattr(reporting.json, "dumps", counted)
    destination = tmp_path / "report.csv"
    reporting._write_csv(destination, payload)
    with destination.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    predictions = [row for row in rows if row["資料類型"] in {"研究展示", "全部已分析股票"}]
    assert len(predictions) == 4
    assert [row["下一交易日收漲機率（小數）"] for row in predictions] == ["0.625", "0.5"] * 2
    assert [row["下一交易日調整後收盤報酬（小數）"] for row in predictions] == ["-0.0125", "0.002"] * 2
    assert [row["股票名稱"] for row in predictions] == ["'=合成股票", "未提供"] * 2
    assert len({row["原始脈絡資料"] for row in predictions}) == 1
    assert json.loads(predictions[0]["原始脈絡資料"])["target_session"] == "20261019"
    assert row_context_calls == [True]
