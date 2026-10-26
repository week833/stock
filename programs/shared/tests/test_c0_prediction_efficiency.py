"""Focused inference parity and work-reuse checks for the C0 baseline."""
from __future__ import annotations

import copy
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SHARED_DIR = Path(__file__).resolve().parents[1]
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

import stock_daily_linear as linear  # noqa: E402

PAIRS = (("1111", "TPEX"), ("1111", "TWSE"), ("2222", "TWSE"))
HORIZONS = (1, 5, 20, 60)
BINDING = {
    "profile_id": "C0", "requirements_version": "prediction-efficiency-fixture",
    "feature_version": "tw-stock-core-features-v1",
    "policy_sha256": "a" * 64, "requirements_raw_sha256": "b" * 64,
}


def _candidate(scale):
    width = len(linear.FEATURE_ORDER)
    return {
        "imputer_medians": [scale * (i + 1) / 100 for i in range(width)],
        "scaler_mean": [scale / 1000] * width,
        "scaler_scale": [1.0 + i / 10 for i in range(width)],
        "direction_coef": [scale * (i + 1) / 10 for i in range(width)],
        "direction_intercept": -scale / 4,
        "return_coef": [scale * (i + 1) / 100 for i in range(width)],
        "return_intercept": scale / 100,
        "calibrator": {"coef": 0.9, "intercept": -0.05},
        "calibration_status": "calibrated",
        "fit_up_rate": 0.55 + scale / 100,
    }


def _evidence():
    return {
        "current_ordinary_verified": True, "price_scope_verified": True,
        "snapshot_verified": True, "known_future_information": False,
        "calendar_verified_by_market": {"TWSE": True, "TPEX": True},
        "eligible_stock_market_pairs": [list(pair) for pair in PAIRS],
        "historical_membership_status": "unknown", "pit_status": "unknown",
        "corporate_actions_status": "unknown",
        "source_binding": {"synthetic_fixture": "prediction-efficiency"},
    }


def _case(*, missing_price=False):
    days = pd.bdate_range("2023-01-02", periods=340).strftime("%Y-%m-%d").tolist()
    asof_index = 270
    records = []
    for k, (stock, market) in enumerate(PAIRS):
        for i, day in enumerate(days[:asof_index + 1]):
            close = 100 * np.exp(0.05 * np.sin(i / (11 + k)) + i / 20000 + k / 20)
            if missing_price and (stock, market) == PAIRS[-1] and (i == asof_index or missing_price == "all"):
                close = np.nan
            records.append({"stock_id": stock, "market": market,
                            "session_date": day, "adjusted_close": float(close)})
    evidence = _evidence()
    prepared = linear.build_panel(
        pd.DataFrame(records), {"TWSE": days, "TPEX": days},
        asof=days[asof_index], horizons=list(HORIZONS),
        evidence=evidence, policy_binding=BINDING,
    )
    models = {str(h): _candidate(i + 1) for i, h in enumerate(HORIZONS)}
    models["5"] = {
        "direction_kind": "logistic", "return_kind": "ridge",
        "direction_candidate": "first", "return_candidate": "second",
        "candidate_heads": {"first": _candidate(1.5), "second": _candidate(2.5)},
        "calibration_status": "calibrated",
    }
    models["20"] = {
        "direction_kind": "fit_up_rate_baseline", "return_kind": "zero_return_baseline",
        "direction_candidate": "first", "return_candidate": "second",
        "candidate_heads": {"first": _candidate(3.5), "second": _candidate(4.5)},
        "calibration_status": "calibrated",
    }
    model = {"schema": linear.MODEL_SCHEMA, "metadata": copy.deepcopy(prepared["metadata"]),
             "models_by_horizon": models}
    return model, prepared


def _reseal(prepared):
    for name in ("features", "labels"):
        prepared["metadata"][name + "_sha256"] = linear._frame_hash(prepared[name])


def _raw_array_counter(monkeypatch):
    arrays = []
    original = linear.np.asarray

    def tracked(value, *args, **kwargs):
        result = original(value, *args, **kwargs)
        if (isinstance(value, list) and len(value) == 1
                and isinstance(value[0], list)
                and len(value[0]) == len(linear.FEATURE_ORDER)):
            arrays.append(result)
        return result

    monkeypatch.setattr(linear.np, "asarray", tracked)
    return arrays


def test_prediction_reuses_label_index_and_one_raw_feature_array_per_stock(monkeypatch):
    model, prepared = _case()
    label_reads, checked = Counter(), []
    active = False
    original_getitem = pd.DataFrame.__getitem__
    original_check = linear._check_prepared

    def track_getitem(frame, key):
        if active and frame is prepared["labels"] and isinstance(key, str):
            label_reads[key] += 1
        return original_getitem(frame, key)

    def check(panel):
        nonlocal active
        checked.append(panel)
        original_check(panel)
        active = True

    monkeypatch.setattr(pd.DataFrame, "__getitem__", track_getitem)
    monkeypatch.setattr(linear, "_check_prepared", check)
    arrays = _raw_array_counter(monkeypatch)
    result = linear.predict_baseline(model, prepared)
    assert len(result) == len(PAIRS) * len(HORIZONS)
    assert len(checked) == 1 and checked[0] is prepared
    assert label_reads["session_date"] == 1
    assert not any(label_reads[name] for name in ("stock_id", "market", "horizon"))
    assert len(arrays) == len(PAIRS)
    assert all(array.shape == (1, len(linear.FEATURE_ORDER)) for array in arrays)
    assert list(zip(result.stock_id, result.market, result.horizon)) == [
        (*pair, horizon) for pair in PAIRS for horizon in HORIZONS
    ]
    assert result["horizon"].dtype == np.dtype("int64")
    assert result["prob_up"].dtype == np.dtype("float64")
    assert result["in_sample_lookahead"].dtype == np.dtype("bool")
    assert result["prediction_status"].eq("predicted").all()


@pytest.mark.parametrize("value", [np.nan, np.inf])
def test_each_head_retains_its_own_numeric_path_and_missing_feature_notes(value):
    model, prepared = _case()
    selected = ((prepared["features"]["stock_id"] == PAIRS[0][0])
                & (prepared["features"]["market"] == PAIRS[0][1])
                & (prepared["features"]["session_date"] == prepared["metadata"]["asof"]))
    prepared["features"].loc[selected, linear.FEATURE_ORDER[0]] = value
    _reseal(prepared)
    result = linear.predict_baseline(model, prepared)
    rows = result.loc[(result.stock_id == PAIRS[0][0]) & (result.market == PAIRS[0][1])]
    if np.isinf(value):
        assert rows["reason"].eq("model_prediction_unavailable").all()
        assert rows["prediction_status"].eq("unavailable").all()
        assert rows["prob_up"].isna().all()
        return
    raw = prepared["features"].loc[selected, list(linear.FEATURE_ORDER)].to_numpy(dtype=float)
    for _, row in rows.iterrows():
        head = model["models_by_horizon"][str(row["horizon"])]
        probability, expected, uncalibrated = linear._apply(head, raw)
        assert (row["prob_up"], row["expected_return"], row["prob_up_uncalibrated"]) == (
            float(probability[0]), float(expected[0]), float(uncalibrated[0]))
        assert row["data_quality_assessment"]["imputed_features"] == [linear.FEATURE_ORDER[0]]
        for name, candidate in head.get("candidate_heads", {}).items():
            cp, cr, cu = linear._apply_candidate(candidate, raw)
            assert row["candidate_predictions"][name] == {
                "prob_up": float(cp[0]), "expected_return": float(cr[0]),
                "prob_up_uncalibrated": float(cu[0]),
                "calibration_status": candidate["calibration_status"],
            }
    assessments = rows["data_quality_assessment"].tolist()
    assert len({id(item["imputed_features"]) for item in assessments}) == len(HORIZONS)
    assessments[0]["imputed_features"].append("test-only")
    assert all("test-only" not in item["imputed_features"] for item in assessments[1:])


@pytest.mark.parametrize("price_state", ["last_only", "no_valid_price"])
def test_missing_heads_and_old_price_do_not_construct_unusable_raw_features(monkeypatch, price_state):
    model, prepared = _case(missing_price="all" if price_state == "no_valid_price" else True)
    del model["models_by_horizon"]["60"]
    selected = ((prepared["features"]["stock_id"] == PAIRS[-1][0])
                & (prepared["features"]["market"] == PAIRS[-1][1])
                & (prepared["features"]["session_date"] == prepared["metadata"]["asof"]))
    for name in linear.FEATURE_ORDER:
        prepared["features"][name] = prepared["features"][name].astype(object)
        prepared["features"].loc[selected, name] = "not-converted-without-current-price"
    _reseal(prepared)
    arrays = _raw_array_counter(monkeypatch)
    result = linear.predict_baseline(model, prepared)
    assert len(arrays) == len(PAIRS) - (price_state == "no_valid_price")
    untrained = result.loc[result.horizon == 60]
    assert untrained["reason"].eq("horizon_not_trained").all()
    old = result.loc[(result.stock_id == PAIRS[-1][0]) & (result.market == PAIRS[-1][1])
                     & (result.horizon != 60)]
    if price_state == "no_valid_price":
        assert old["reason"].eq("stock_price_missing").all()
        assert old["prediction_status"].eq("unavailable").all()
        assert old["feature_data_date"].isna().all()
    else:
        assert old["prediction_status"].eq("predicted").all()
        assert old["reason"].isna().all()
        assert old["data_lagged"].eq(True).all()
        assert old["data_lag_sessions"].eq(1).all()
        assert old["session_date"].eq(old["feature_data_date"]).all()
        assert (old["feature_data_date"] < old["analysis_asof"]).all()


def test_duplicate_label_uses_first_original_position_with_duplicate_frame_index():
    model, prepared = _case()
    expected = linear.predict_baseline(model, prepared)
    selector = ((prepared["labels"]["stock_id"] == PAIRS[0][0])
                & (prepared["labels"]["market"] == PAIRS[0][1])
                & (prepared["labels"]["session_date"] == prepared["metadata"]["asof"])
                & (prepared["labels"]["horizon"] == HORIZONS[0]))
    duplicate = prepared["labels"].loc[selector].iloc[[0]].copy()
    duplicate["label_end"] = "2099-01-01"
    prepared["labels"] = pd.concat([duplicate, prepared["labels"]], ignore_index=True)
    prepared["labels"].index = [7] * len(prepared["labels"])
    _reseal(prepared)
    expected.loc[0, "target_session_date"] = "2099-01-01"
    actual = linear.predict_baseline(model, prepared)
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)


@pytest.mark.parametrize("name", ["stock_id", "market", "session_date", "horizon"])
def test_null_extra_label_keys_never_match(name):
    model, prepared = _case()
    expected = linear.predict_baseline(model, prepared)
    extra = prepared["labels"].loc[
        prepared["labels"]["session_date"] == prepared["metadata"]["asof"]
    ].iloc[[0]].copy()
    extra[name] = None
    extra["label_end"] = "2099-01-01"
    prepared["labels"] = pd.concat([extra, prepared["labels"]], ignore_index=True)
    _reseal(prepared)
    actual = linear.predict_baseline(model, prepared)
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)


@pytest.mark.parametrize("horizon", [None, np.nan, pd.NA])
def test_null_horizon_does_not_match_a_null_label(horizon):
    model, prepared = _case()
    extra = prepared["labels"].loc[
        prepared["labels"]["session_date"] == prepared["metadata"]["asof"]
    ].iloc[[0]].copy()
    extra["horizon"] = None
    prepared["labels"] = pd.concat([extra, prepared["labels"]], ignore_index=True)
    prepared["metadata"]["horizons"] = [horizon]
    _reseal(prepared)
    with pytest.raises(IndexError, match="single positional indexer is out-of-bounds"):
        linear.predict_baseline(model, prepared)


def test_missing_label_still_fails_closed():
    model, prepared = _case()
    selector = ((prepared["labels"]["session_date"] == prepared["metadata"]["asof"])
                & (prepared["labels"]["stock_id"] == PAIRS[0][0])
                & (prepared["labels"]["market"] == PAIRS[0][1])
                & (prepared["labels"]["horizon"] == HORIZONS[0]))
    prepared["labels"] = prepared["labels"].loc[~selector].copy()
    _reseal(prepared)
    with pytest.raises(IndexError, match="single positional indexer is out-of-bounds"):
        linear.predict_baseline(model, prepared)


def test_label_integrity_validation_precedes_lookup():
    model, prepared = _case()
    prepared["labels"].loc[0, "label_end"] = "2099-01-01"
    with pytest.raises(linear.DailyLinearError, match="prepared_panel_changed"):
        linear.predict_baseline(model, prepared)


def _label_case():
    days = pd.bdate_range("2024-01-02", periods=12).strftime("%Y-%m-%d").tolist()
    asof = days[6]
    axes = {"TWSE": days, "TPEX": days[:10]}
    records = []
    for k, (stock, market) in enumerate(PAIRS):
        start = (2, 0, 1)[k]
        for i in range(start, 7):
            if (k == 0 and i == 4) or (k == 2 and i == 3):
                continue
            close = np.nan if k == 2 and i == 6 else float(100 + 7 * k + 2 * i)
            records.append({"stock_id": stock, "market": market,
                            "session_date": days[i], "adjusted_close": close})
    return pd.DataFrame(records[::-1]), axes, asof


def test_build_panel_label_values_dtypes_order_and_calendar_edges_are_unchanged():
    panel, axes, asof = _label_case()
    prepared = linear.build_panel(panel, axes, asof=asof, horizons=list(HORIZONS),
                                  evidence=_evidence(), policy_binding=BINDING)
    expected_rows = []
    for stock, market in PAIRS:
        source = panel.loc[(panel.stock_id == stock) & (panel.market == market)]
        prices = dict(zip(source.session_date, source.adjusted_close))
        axis = axes[market]
        for day in axis:
            if not source.session_date.min() <= day <= asof:
                continue
            for horizon in HORIZONS:
                position = axis.index(day) + horizon
                target = axis[position] if position < len(axis) else None
                signal_close = prices.get(day, np.nan)
                endpoint_close = prices.get(target, np.nan)
                observed = (target is not None and target <= asof
                            and np.isfinite(signal_close) and np.isfinite(endpoint_close))
                state = ("observed" if observed else
                         "target_calendar_unavailable" if target is None else
                         "not_mature" if target > asof else "unobserved_missing_close")
                value = (np.float64(endpoint_close) / np.float64(signal_close) - 1.0
                         if observed else np.nan)
                expected_rows.append({
                    "stock_id": stock, "market": market, "session_date": day,
                    "horizon": horizon, "label_end": target,
                    "target_return": value, "label_state": state,
                })
    expected = pd.DataFrame(expected_rows).sort_values(
        ["stock_id", "market", "session_date", "horizon"]
    ).reset_index(drop=True)
    pd.testing.assert_frame_equal(prepared["labels"], expected, check_exact=True)
    assert prepared["metadata"]["labels_sha256"] == linear._frame_hash(expected)
    assert set(expected.label_state) == {
        "observed", "not_mature", "target_calendar_unavailable", "unobserved_missing_close",
    }


@pytest.mark.parametrize("change, reason", [
    ("future_price", "input_price_after_asof"),
    ("duplicate_price", "duplicate_stock_market_session"),
    ("duplicate_calendar", "market_calendar_must_be_unique_increasing_TWSE"),
    ("outside_calendar", "price_outside_declared_market_calendar"),
])
def test_build_panel_keeps_invalid_input_and_calendar_failures(change, reason):
    panel, axes, asof = _label_case()
    if change == "future_price":
        future = panel.iloc[[0]].copy()
        future["session_date"] = axes["TWSE"][7]
        panel = pd.concat([panel, future], ignore_index=True)
    elif change == "duplicate_price":
        panel = pd.concat([panel, panel.iloc[[0]]], ignore_index=True)
    elif change == "duplicate_calendar":
        axes["TWSE"] = axes["TWSE"][:3] + [axes["TWSE"][2]] + axes["TWSE"][3:]
    else:
        axes["TWSE"] = axes["TWSE"][:2] + axes["TWSE"][3:]
    with pytest.raises(linear.DailyLinearError, match=reason):
        linear.build_panel(panel, axes, asof=asof, horizons=list(HORIZONS),
                           evidence=_evidence(), policy_binding=BINDING)
