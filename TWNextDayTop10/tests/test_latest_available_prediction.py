"""Latest available C0 features keep real dates and the existing model rules."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SHARED = Path(__file__).resolve().parents[2] / "programs" / "shared"
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))

import stock_daily_linear as linear  # noqa: E402


@pytest.fixture
def case():
    axis = pd.bdate_range("2025-01-02", periods=260).strftime("%Y-%m-%d").tolist()
    asof_index = 252
    counts = {"1516": 247, "1589": 116, "4804": 121, "6142": 247,
              "9990": 253, "9999": 253}
    markets = {stock: "TPEX" if stock == "4804" else "TWSE" for stock in counts}
    records = []
    for stock, count in counts.items():
        for index in range(count):
            close = np.nan if stock == "9999" else 100.0 + index * 0.2 + np.sin(index / 5.0)
            records.append({"stock_id": stock, "market": markets[stock],
                            "session_date": axis[index], "adjusted_close": close})
    binding = {"profile_id": "C0", "requirements_version": "latest-feature-fixture",
               "feature_version": linear.FEATURE_SCHEMA, "policy_sha256": "a" * 64,
               "requirements_raw_sha256": "b" * 64}
    evidence = {"current_ordinary_verified": True, "price_scope_verified": True,
        "snapshot_verified": True, "known_future_information": False,
        "calendar_verified_by_market": {"TWSE": True, "TPEX": True},
        "eligible_stock_market_pairs": [[stock, markets[stock]] for stock in counts],
        "historical_membership_status": "unknown", "pit_status": "unknown",
        "corporate_actions_status": "unknown", "source_binding": {"synthetic_fixture": True}}
    prepared = linear.build_panel(pd.DataFrame(records), {"TWSE": axis, "TPEX": axis},
        asof=axis[asof_index], horizons=[1], evidence=evidence, policy_binding=binding)
    metadata = copy.deepcopy(prepared["metadata"])
    metadata["asof"] = axis[10]
    head = {"imputer_medians": [0.01] * len(linear.FEATURE_ORDER),
        "scaler_mean": [0.0] * len(linear.FEATURE_ORDER),
        "scaler_scale": [1.0] * len(linear.FEATURE_ORDER),
        "direction_coef": [0.1] * len(linear.FEATURE_ORDER), "direction_intercept": -0.2,
        "return_coef": [0.001] * len(linear.FEATURE_ORDER), "return_intercept": 0.002,
        "calibrator": None, "calibration_status": "synthetic_no_training"}
    model = {"schema": linear.MODEL_SCHEMA, "metadata": metadata, "models_by_horizon": {"1": head}}
    return {"axis": axis, "asof_index": asof_index, "counts": counts, "markets": markets,
            "prepared": prepared, "model": model}


def _prediction(case, stock):
    rows = linear.predict_baseline(case["model"], case["prepared"])
    return rows.loc[rows.stock_id == stock].iloc[0]


def _expected_values(case, stock, date):
    features = case["prepared"]["features"]
    row = features.loc[(features.stock_id == stock) & (features.session_date == date)].iloc[0]
    vector = np.asarray([[row[name] for name in linear.FEATURE_ORDER]], dtype=float)
    return vector, linear._apply(case["model"]["models_by_horizon"]["1"], vector)


@pytest.mark.parametrize("stock", ["1516", "1589", "4804", "6142"])
def test_stale_stock_uses_its_actual_latest_features_and_original_target(case, stock):
    actual = _prediction(case, stock)
    last_index = case["counts"][stock] - 1
    feature_day = case["axis"][last_index]
    analysis_day = case["axis"][case["asof_index"]]
    vector, (probability, expected, raw) = _expected_values(case, stock, feature_day)
    asof_vector, _ = _expected_values(case, stock, analysis_day)
    assert np.isnan(asof_vector).all()
    assert np.isfinite(vector).sum() >= 5
    assert actual.prediction_status == "predicted" and actual.reason is None
    assert actual.session_date == actual.data_asof == actual.feature_data_date == feature_day
    assert actual.analysis_asof == analysis_day
    assert actual.stock_last_price_date == feature_day
    assert bool(actual.data_lagged) is True
    assert actual.data_lag_sessions == case["asof_index"] - last_index
    assert actual.target_session_date == case["axis"][last_index + 1]
    assert actual.target_session_date != case["axis"][case["asof_index"] + 1]
    assert actual.prob_up == pytest.approx(probability[0])
    assert actual.expected_return == pytest.approx(expected[0])
    assert actual.prob_up_uncalibrated == pytest.approx(raw[0])
    assert bool(actual.in_sample_lookahead) is False
    quality = actual.data_quality_assessment
    assert quality["status"] == "partial"
    assert quality["imputed_features"] == [name for name, value in zip(linear.FEATURE_ORDER, vector[0])
                                           if np.isnan(value)]
    assert any(feature_day in note and analysis_day in note and "落後" in note
               for note in actual.stock_data_notes)


def test_current_stock_keeps_current_dates_and_native_model_values(case):
    actual = _prediction(case, "9990")
    day = case["axis"][case["asof_index"]]
    vector, (probability, expected, raw) = _expected_values(case, "9990", day)
    assert np.isfinite(vector).all()
    assert actual.prediction_status == "predicted" and actual.reason is None
    assert actual.session_date == actual.data_asof == actual.feature_data_date == actual.analysis_asof == day
    assert actual.target_session_date == case["axis"][case["asof_index"] + 1]
    assert bool(actual.data_lagged) is False and actual.data_lag_sessions == 0
    assert actual.prob_up == pytest.approx(probability[0])
    assert actual.expected_return == pytest.approx(expected[0])
    assert actual.prob_up_uncalibrated == pytest.approx(raw[0])
    assert not any("落後" in note for note in actual.stock_data_notes)


def test_in_sample_flag_uses_effective_feature_day(case):
    case["model"]["metadata"]["asof"] = case["axis"][248]
    stale = _prediction(case, "1516")
    current = _prediction(case, "9990")
    assert bool(stale.in_sample_lookahead) is True
    assert stale.data_asof < stale.model_training_asof < stale.analysis_asof
    assert bool(current.in_sample_lookahead) is False


@pytest.mark.parametrize("failure", ["no_valid_price", "model_numeric_error"])
def test_actual_missing_features_or_numeric_failure_remains_unavailable(case, monkeypatch, failure):
    if failure == "no_valid_price":
        actual = _prediction(case, "9999")
        assert actual.stock_last_price_date is None
        assert actual.feature_data_date is None and actual.data_asof is None
        assert actual.reason == "stock_price_missing"
    else:
        def fail(head, vector):
            raise ValueError("synthetic_numeric_failure")

        monkeypatch.setattr(linear, "_apply", fail)
        actual = _prediction(case, "1516")
        assert actual.reason == "model_prediction_unavailable"
        assert actual.feature_data_date == case["axis"][246]
        assert bool(actual.data_lagged) is True
    assert actual.prediction_status == "unavailable"
    assert pd.isna(actual.prob_up) and pd.isna(actual.expected_return)
