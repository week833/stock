"""Top10 逐市場 asof（2026-10-12 使用者指示 3b）：落後市場以自己的資料日推論，不被排除、不擋下整次 run。

沿用 shared 測試的合成兩市場 fixture（TWSE 最新日 D、TPEX 落後到 T，TPEX 在 D 有一筆毒收盤）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT = Path(__file__).resolve().parents[1]
SHARED_TESTS = Path(r"D:\stock\programs\shared\tests")
for entry in (str(PROJECT), str(SHARED_TESTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import test_c0_per_market_asof as fx  # noqa: E402
from test_c0_per_market_asof import loaded_scope, model  # noqa: E402,F401  (pytest fixtures)
import app  # noqa: E402
import daily_contract  # noqa: E402
import reporting  # noqa: E402

D, T = fx.D, fx.T
NEXT_D = fx.DATES[fx.D_INDEX + 1]


def _active():
    active = fx._active()
    active["original_model_feature_contract"] = {"profile_id": "C0", "synthetic": True}
    return active


def _predicted(scope, model):
    _, forecast = fx.contract.predict_c0_by_market(model, scope, horizons=[1])
    return forecast.loc[forecast.prediction_status.eq("predicted") & forecast.prob_up.notna()].copy()


def test_market_sessions_gives_each_market_its_own_base_and_target(loaded_scope, model):
    scope = loaded_scope()
    base, targets = app.market_sessions(_predicted(scope, model), scope["asof_by_market"], scope["asof"])
    assert base == {"TPEX": T, "TWSE": D}
    assert targets == {"TPEX": D, "TWSE": NEXT_D}


def test_market_sessions_rejects_a_market_predicted_after_its_own_asof(loaded_scope, model):
    """Anti-lookahead KEEP: one global asof over a lagging market must still raise."""
    scope = loaded_scope()
    predicted = _predicted(scope, model)
    predicted.loc[predicted.market.eq("TPEX"), "session_date"] = D
    with pytest.raises(RuntimeError, match="C0_market_base_session_differs_from_market_asof:TPEX"):
        app.market_sessions(predicted, scope["asof_by_market"], scope["asof"])


def _run_predict(scope, model, monkeypatch, tmp_path, *, blocked_market=None):
    """app.predict with the synthetic scope; the real report()/Excel builder writes under tmp_path."""
    active = _active()
    active["artifact_binding"] = dict(active["artifact_binding"], qa_override=True, path=str(tmp_path / "synthetic.json"))
    active["version_labels"] = []
    active["pointer_binding"] = {"synthetic": True}
    universe = pd.DataFrame([{"stock_id": stock, "market": market, "name": "S" + stock} for stock, market in fx.STOCKS])
    meta = dict(asof=scope["asof"], version_labels=list(scope["version_labels"]), warnings=[],
        snapshot_fingerprint="synthetic-fingerprint", c0_scope_coverage=scope["coverage"],
        inference_run_time="2026-10-12T00:00:00+08:00", ordinary_identity_verified_asof=D,
        ordinary_identity_observed_at="2026-10-12T00:00:00+08:00", data_time=scope["data_time"],
        raw_status="COMPLETE", **daily_contract.runtime_report_bindings(active))
    inputs = dict(scope, universe=universe, metadata=meta)
    captured = {}

    if blocked_market:
        predict = fx.contract.predict_c0_by_market

        def blocked_predictions(*args, **kwargs):
            prepared, rows = predict(*args, **kwargs)
            rows = rows.copy()
            blocked = rows.market.eq(blocked_market)
            rows.loc[blocked, "prediction_status"] = "blocked_synthetic"
            rows.loc[blocked, ["prob_up", "expected_return"]] = None
            return prepared, rows

        # Forward calibration preserves the inference status already decided here.
        monkeypatch.setattr(fx.contract, "predict_c0_by_market", blocked_predictions)

    def forward(forecast, scope_inputs, runtime, *, db_path):
        rows = pd.DataFrame(forecast)
        captured["forecast"] = rows
        return {"predictions": rows.to_dict("records"), "forward_binding": {"synthetic": True}}

    def bind(checked, demand, asof, *, asof_by_market=None):
        captured["bind"] = {"asof": asof, "asof_by_market": asof_by_market}
        return dict(requirements={}, snapshot={}, version_labels=[], **daily_contract.runtime_report_bindings(checked))

    monkeypatch.setattr(daily_contract, "load_c0_model", lambda path=None: (fx.linear, model, tmp_path / "synthetic.json", active))
    monkeypatch.setattr(daily_contract, "shared_module", lambda path, name: fx.contract)
    monkeypatch.setattr(daily_contract, "bind_daily_snapshot", bind)
    monkeypatch.setattr(fx.contract, "process_c0_forward", forward)
    monkeypatch.setattr(app, "load_c0_inputs", lambda project, runtime_binding=None: inputs)
    monkeypatch.setenv("TOP10_VISIBLE_REPORT_ROOT", str(tmp_path / "visible"))
    output = app.predict(namespace_root=tmp_path / "ns", no_open=True)
    payload = json.loads((Path(output).parent / "report.json").read_text(encoding="utf-8"))
    html = (Path(output).parent / "report.html").read_text(encoding="utf-8")
    return captured, payload, html, Path(output)


def test_predict_keeps_the_lagging_market_and_shows_each_market_day(loaded_scope, model, monkeypatch, tmp_path):
    scope = loaded_scope()
    captured, payload, html, output = _run_predict(scope, model, monkeypatch, tmp_path)
    forecast = captured["forecast"]
    # The lagging TPEX stocks are analysed (not dropped) on TPEX's own day.
    assert sorted(set(zip(forecast.stock_id, forecast.market))) == sorted(fx.STOCKS)
    assert set(forecast.loc[forecast.market.eq("TPEX"), "session_date"]) == {T}
    assert set(forecast.loc[forecast.market.eq("TWSE"), "session_date"]) == {D}
    assert len(payload["predictions"]) == len(fx.STOCKS)
    context = payload["report_context"]
    assert context["base_session_by_market"] == {"TPEX": T, "TWSE": D}
    assert context["target_session_by_market"] == {"TPEX": D, "TWSE": NEXT_D}
    assert context["target_session"] == NEXT_D.replace("-", "")
    assert context["future_calendar_status"] == "verified_declared_session"
    assert context["actual_model_inputs"]["input_windows"] == {"TPEX": {"sessions": 253, "end_date": T},
                                                             "TWSE": {"sessions": 253, "end_date": D}}
    assert captured["bind"] == {"asof": D, "asof_by_market": {"TPEX": T, "TWSE": D}}
    assert payload["meta"]["analysis_asof_by_market"] == {"TPEX": T, "TWSE": D}
    assert "各市場：上櫃 " + T.replace("-", "") + "／上市 " + D.replace("-", "") in html
    rows = dict(payload["data_time_display"]["rows"])
    assert rows["各市場資料日"] == "上櫃 " + T.replace("-", "") + "／上市 " + D.replace("-", "")
    assert output.is_file() and output.stat().st_size > 0
    assert (tmp_path / "visible" / (NEXT_D.replace("-", "") + ".xlsx")).is_file()


def test_predict_with_one_common_asof_is_unchanged(loaded_scope, model, monkeypatch, tmp_path):
    scope = loaded_scope(tpex_index=fx.D_INDEX)
    batch_calls = []
    original_predict = fx.contract.predict_c0_by_market

    def record_predict(*args, **kwargs):
        batch_calls.append(kwargs.get("batch_pairs"))
        return original_predict(*args, **kwargs)

    monkeypatch.setattr(fx.contract, "predict_c0_by_market", record_predict)
    captured, payload, html, _ = _run_predict(scope, model, monkeypatch, tmp_path)
    assert batch_calls == [64]
    context = payload["report_context"]
    for key in ("base_session_by_market", "target_session_by_market", "market_asof_mode"):
        assert key not in context
    assert "analysis_asof_by_market" not in payload["meta"]
    assert captured["bind"] == {"asof": D, "asof_by_market": None}
    assert context["target_session"] == NEXT_D.replace("-", "")
    assert context["actual_model_inputs"]["input_windows"] == {"TPEX": {"sessions": 253, "end_date": D},
                                                             "TWSE": {"sessions": 253, "end_date": D}}
    assert "各市場：" not in html
    # Identical predictions to the previous single build_panel call.
    single = fx.linear.predict_baseline(model, fx.linear.build_panel(scope["panel"], scope["market_sessions"],
        asof=scope["asof"], horizons=[1], evidence=scope["evidence"], policy_binding=fx.BINDING))
    pd.testing.assert_frame_equal(captured["forecast"].reset_index(drop=True)[single.columns],
                                  single.reset_index(drop=True), check_dtype=False)


def test_bind_daily_snapshot_records_per_market_days_only_when_given(monkeypatch):
    calls = []

    class Policy:
        @staticmethod
        def load_policy():
            return {}

        @staticmethod
        def requirements_for(policy, profile, consumer_id):
            return {}

        @staticmethod
        def bind_snapshot(policy, profile, descriptor):
            calls.append(dict(descriptor))
            return descriptor

    active = _active()
    checked = dict(daily_contract.runtime_report_bindings(active), shared_input_descriptor={"x": 1}, version_labels=[])
    monkeypatch.setattr(daily_contract, "policy_module", lambda: Policy)
    monkeypatch.setattr(daily_contract, "c0_policy_binding", lambda **k: dict(active["current_policy_binding"]))
    checked["current_policy_binding"] = dict(active["current_policy_binding"])
    demand = {"model_identity": {"model_id": "m"}}
    daily_contract.bind_daily_snapshot(dict(checked), demand, D)
    daily_contract.bind_daily_snapshot(dict(checked), demand, D, asof_by_market={"TWSE": D, "TPEX": T})
    assert "analysis_asof_by_market" not in calls[0]
    assert calls[1]["analysis_asof_by_market"] == {"TWSE": D, "TPEX": T}


def test_data_time_display_names_the_lagging_market():
    data_time = {"analysis_asof": D, "asof_by_market": {"TWSE": D, "TPEX": T}, "is_stale": True,
                 "freshness_status": "stale", "latest_expected_session": D, "stale_markets": {"TPEX": [T, D]}}
    rows = dict(reporting.data_time_display(data_time, None, [], [])["rows"])
    assert "落後市場：上櫃 資料日 " + T.replace("-", "") + "，應有 " + D.replace("-", "") in rows["資料新鮮度"]
    fresh = dict(reporting.data_time_display(dict(data_time, is_stale=False, freshness_status="latest",
                                                  stale_markets={}), None, [], [])["rows"])
    assert "落後市場" not in fresh["資料新鮮度"]


def test_session_text_adds_market_days_only_when_present():
    assert reporting._session_text({"base_session": "20261012"}, "base_session", None) == "20261012"
    text = reporting._session_text({"base_session": "20261012", "base_session_by_market": {"TWSE": "2026-10-12",
                                    "TPEX": "2026-10-08"}}, "base_session", None)
    assert text == "20261012（各市場：上櫃 20261008／上市 20261012）"


def test_market_without_predicted_rows_keeps_its_own_input_window_day(loaded_scope, model, monkeypatch, tmp_path):
    """A lagging market none of whose rows was predicted: its window still ends on its own day."""
    scope = loaded_scope()
    captured, payload, html, _ = _run_predict(scope, model, monkeypatch, tmp_path, blocked_market="TPEX")
    context = payload["report_context"]
    blocked = captured["forecast"].loc[lambda rows: rows.market.eq("TPEX")]
    assert set(blocked.prediction_status) == {"blocked_synthetic"}
    assert blocked.prob_up.isna().all()
    assert {row["market"] for row in payload["predictions"]} == {"TWSE"}
    assert context["actual_model_inputs"]["input_windows"] == {"TPEX": {"sessions": 253, "end_date": T},
                                                             "TWSE": {"sessions": 253, "end_date": D}}
    assert context["base_session_by_market"] == {"TPEX": T, "TWSE": D}
    assert context["target_session_by_market"] == {"TWSE": NEXT_D}
    assert captured["bind"] == {"asof": D, "asof_by_market": {"TPEX": T, "TWSE": D}}
