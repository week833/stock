"""Synthetic focused tests for the isolated selected-feature research engine.

These tests never read central prices or an active artifact. Native fitting only
uses tiny synthetic arrays, and artifact writes are confined to pytest tmp_path.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits

SHARED_DIR = Path(__file__).resolve().parents[1]
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

import stock_daily_candidate_model as engine  # noqa: E402


def _prepared(n=368, horizons=(63,)):
    axis = pd.bdate_range("2025-04-14", periods=n)
    features, labels = [], []
    order = list(engine.SEVEN_FEATURES) + ["volume_signal", "finance_unverified", "constant_column"]
    for k, (stock, market) in enumerate((("1111", "TWSE"), ("2222", "TWSE"), ("3333", "TPEX"))):
        t = np.arange(n, dtype=float)
        close = 100 * np.exp(0.11 * np.sin(t / (11 + k) + k) + 0.0002 * t)
        for i, day in enumerate(axis):
            row = {"stock_id": stock, "market": market, "session_date": day.strftime("%Y-%m-%d"),
                   "feature_ready": i >= 59, "adjusted_close": close[i],
                   "volume_signal": np.cos(i / 8 + k), "finance_unverified": np.nan, "constant_column": 1.0}
            for h in (5, 20, 60, 126, 252):
                row["return_" + str(h)] = close[i] / close[i - h] - 1 if i >= h else np.nan
            for h in (20, 60):
                row["volatility_" + str(h)] = 0.01 + 0.002 * np.sin(i / (h / 2) + k) if i >= h else np.nan
            features.append(row)
            for h in horizons:
                observed = i + h < n
                labels.append({"stock_id": stock, "market": market, "session_date": day.strftime("%Y-%m-%d"),
                               "horizon": h, "label_end": axis[i + h].strftime("%Y-%m-%d") if observed else None,
                               "target_return": close[i + h] / close[i] - 1 if observed else np.nan,
                               "label_state": "observed" if observed else "target_calendar_unavailable"})
    return {"features": pd.DataFrame(features), "labels": pd.DataFrame(labels),
            "metadata": {"feature_schema": engine.FEATURE_SCHEMA, "feature_order": order,
                         "feature_order_by_horizon": {str(h): order for h in horizons},
                         "feature_metadata": {c: {"route": "synthetic", "pit_status": "fixture"} for c in order},
                         "horizons": list(horizons), "asof_by_market": {m: axis[-1].strftime("%Y-%m-%d") for m in ("TWSE", "TPEX")},
                         "source_manifest": {"snapshot": "synthetic-only", "optional_finance": "unknown_availability"},
                         "source_plan_by_horizon": {str(h): {"synthetic": "used"} for h in horizons},
                         "analysis_context": {"purpose": "test", "sample_seed": 7}}}


def _linear_only(prepared):
    original = engine._fit_head

    def fit(kind, *args, **kwargs):
        if kind == "hist_gradient_boosting":
            raise RuntimeError("synthetic individual HGB failure")
        return original(kind, *args, **kwargs)

    with mock.patch.object(engine, "_fit_head", side_effect=fit):
        return engine.fit_candidate(prepared, seed=7)


@pytest.fixture(scope="module")
def linear_case():
    prepared = _prepared()
    return prepared, _linear_only(prepared)


@pytest.fixture(scope="module")
def multi_case():
    prepared = _prepared(horizons=(1, 63))
    return prepared, _linear_only(prepared)


def test_18_month_h63_strict_purge_with_60_20_20_days(linear_case):
    prepared, model = linear_case
    item = model["horizons"]["63"]
    assert item["status"] == "trained"
    assert item["calibration_status"] == "uncalibrated"
    assert "calibration" not in item["splits"]
    assert item["splits"]["fit"]["n_effective_days"] >= 60
    assert item["splits"]["selection"]["n_effective_days"] == 20
    assert item["splits"]["test"]["n_effective_days"] == 20
    ledger = pd.DataFrame(item["sample_ledger"])
    assert len(ledger) == len(prepared["features"])
    assert ledger.groupby("session_date")["partition"].nunique().max() == 1
    for name in ("fit", "selection"):
        rows = ledger.loc[ledger["partition"].eq(name) & ledger["effective"]]
        assert rows["label_end"].max() < item["splits"][name]["next_signal_start"]
        assert rows["feature_ready"].all()
        assert rows["label_state"].eq("observed").all()
    for market, asof in prepared["metadata"]["asof_by_market"].items():
        rows = ledger.loc[ledger["market"].eq(market) & ledger["effective"]]
        assert rows["label_end"].max() <= asof


def test_h63_insufficient_days_is_not_trained_without_shrinking_purge():
    prepared = _prepared(n=320)
    with mock.patch.object(engine, "_fit_head", side_effect=AssertionError("must not fit")):
        model = engine.fit_candidate(prepared, seed=7)
    item = model["horizons"]["63"]
    assert item["status"] == "not_trained"
    assert item["split_failure"]["reason"] == "insufficient_fit_signal_days"
    assert set(item["candidates"]) == set(engine._KINDS)
    assert item["selected"] == {"direction": None, "return": None}
    assert len(item["sample_ledger"]) == len(prepared["features"])
    assert all(c["n_denominator"] == len(prepared["features"]) for c in item["candidates"].values())
    assert item["feature_coverage"]["finance_unverified"]["all"]["coverage"] == 0


def test_fit_unseen_252_feature_never_becomes_learned_at_inference(linear_case):
    prepared, model = linear_case
    item = model["horizons"]["63"]
    assert "return_252" in item["eligible_feature_order"]
    assert item["feature_validity"]["return_252"]["state"] == "fit_all_missing"
    assert "return_252" not in item["effective_feature_order"]
    assert item["feature_coverage"]["return_252"]["fit"]["n_finite"] == 0
    assert item["feature_coverage"]["return_252"]["test"]["n_finite"] > 0
    assert item["feature_validity"]["constant_column"]["state"] == "fit_constant"
    assert "constant_column" not in item["effective_feature_order"]
    assert "finance_unverified" in item["source_rules"]["feature_metadata"]
    original = engine.predict_candidate(model, prepared)
    changed = copy.deepcopy(prepared)
    changed["features"]["return_252"] = 1.0e10
    changed["features"]["finance_unverified"] = 999.0
    changed["features"]["constant_column"] = -999.0
    predicted = engine.predict_candidate(model, changed)
    np.testing.assert_allclose(original["prob_up"].astype(float), predicted["prob_up"].astype(float), atol=0, rtol=0)
    np.testing.assert_allclose(original["expected_return"].astype(float), predicted["expected_return"].astype(float), atol=0, rtol=0)


def test_candidate_failure_and_bad_cases_keep_full_denominators(linear_case):
    prepared, model = linear_case
    item = model["horizons"]["63"]
    broken = item["candidates"]["hist_gradient_boosting"]
    assert broken["status"] == "not_trained"
    assert "synthetic individual HGB failure" in broken["heads"]["direction"]["error"]
    assert item["selected"]["direction"] in ("seven_linear", "expanded_linear")
    assert item["selected"]["return"] in ("seven_linear", "expanded_linear")
    assert broken["n_denominator"] == item["denominator"]["n_rows"] == len(prepared["features"])
    for name in ("selection", "test"):
        n = item["splits"][name]["n_denominator"]
        cases = item["evaluation_cases"][name]
        assert len(cases) == n
        assert set(c["stock_id"] for c in cases) == {"1111", "2222", "3333"}
        for task in ("direction", "return"):
            assert broken[name][task]["n_denominator"] == n
            assert broken[name][task]["n_evaluated"] == 0
            assert broken[name][task]["coverage"] == 0
            assert broken[name][task]["brier" if task == "direction" else "mae"] is None
            assert all(c["predictions"]["hist_gradient_boosting"][task] is None for c in cases)


def test_final_holdout_targets_and_features_cannot_change_fits_or_selection(linear_case):
    prepared, original = linear_case
    changed = copy.deepcopy(prepared)
    test_dates = original["horizons"]["63"]["splits"]["test"]["signal_dates"]
    changed["labels"].loc[changed["labels"]["session_date"].isin(test_dates), "target_return"] = 9.0
    changed["features"].loc[changed["features"]["session_date"].isin(test_dates), "volume_signal"] = 123456.0
    modified = _linear_only(changed)
    old, new = original["horizons"]["63"], modified["horizons"]["63"]
    assert new["selected"] == old["selected"]
    assert new["selection_lock_sha256"] == old["selection_lock_sha256"]
    for kind in ("seven_linear", "expanded_linear"):
        assert new["candidates"][kind]["transformer"] == old["candidates"][kind]["transformer"]
        assert new["candidates"][kind]["heads"] == old["candidates"][kind]["heads"]
        assert new["candidates"][kind]["selection"] == old["candidates"][kind]["selection"]
    assert new["candidates"]["expanded_linear"]["test"]["return"]["mae"] != old["candidates"]["expanded_linear"]["test"]["return"]["mae"]


def test_baseline_never_wins_and_linear_wins_exact_ties():
    candidates = {}
    for kind in engine._KINDS:
        candidates[kind] = {"heads": {task: {"selection_eligible": True} for task in ("direction", "return")},
                            "selection": {task: {"brier": 0.9, "mae": 9.0, "n_labeled": 40, "n_evaluated": 40}
                                          for task in ("direction", "return")}}
    # Even an explicitly supplied perfect baseline is not an eligible real head.
    candidates["fit_up_rate"] = {"selection": {"direction": {"brier": 0.0}}}
    candidates["zero_return"] = {"selection": {"return": {"mae": 0.0}}}
    for task in ("direction", "return"):
        selected, reason = engine._choose(candidates, task)
        assert selected == "seven_linear"
        assert reason["baseline_eligible"] is False
    candidates["expanded_linear"]["selection"]["direction"]["brier"] = 0.8
    assert engine._choose(candidates, "direction")[0] == "expanded_linear"
    candidates["hist_gradient_boosting"]["selection"]["direction"]["brier"] = 0.7
    assert engine._choose(candidates, "direction")[0] == "hist_gradient_boosting"


def test_short_horizon_calibration_is_independent_and_decided_before_scores(multi_case):
    _, model = multi_case
    short, long = model["horizons"]["1"], model["horizons"]["63"]
    assert "calibration" in short["splits"]
    assert short["splits"]["calibration"]["n_effective_days"] == 20
    assert short["splits"]["fit"]["n_effective_days"] >= 60
    assert short["calibration_status"] == "calibrated_independent_20_days"
    for a, b in (("fit", "calibration"), ("calibration", "selection"), ("selection", "test")):
        assert short["splits"][a]["next_signal_start"] == short["splits"][b]["start"]
        for counts in short["splits"][a]["by_market"].values():
            assert counts["last_label_end"] < short["splits"][b]["start"]
    assert "calibration" not in long["splits"]
    assert long["calibration_status"] == "uncalibrated"


def test_unknown_future_calendar_keeps_numbers_but_not_deliverability(linear_case):
    prepared, model = linear_case
    prediction = engine.predict_candidate(model, prepared)
    assert len(prediction) == 3
    assert prediction["prob_up"].notna().all() and prediction["expected_return"].notna().all()
    assert prediction["target_session_date"].isna().all()
    assert prediction["target_date_status"].eq("unverified_future_calendar").all()
    assert prediction["prediction_status"].eq("research_estimate").all()
    assert prediction["deliverable"].eq(False).all()
    assert prediction["reason_zh"].str.contains("目標市場日未驗證").all()
    json.dumps(prediction.to_dict(orient="records"), allow_nan=False)


def test_latest_unready_stock_and_stale_stock_remain_in_output(linear_case):
    prepared, model = linear_case
    changed = copy.deepcopy(prepared)
    latest = changed["features"]["session_date"].eq(changed["metadata"]["asof_by_market"]["TWSE"])
    changed["features"].loc[latest & changed["features"]["stock_id"].eq("1111"), "feature_ready"] = False
    changed["features"] = changed["features"].loc[~(latest & changed["features"]["stock_id"].eq("2222"))].copy()
    prediction = engine.predict_candidate(model, changed).set_index("stock_id")
    assert set(prediction.index) == {"1111", "2222", "3333"}
    for stock in ("1111", "2222"):
        assert prediction.loc[stock, "prob_up"] is None
        assert prediction.loc[stock, "expected_return"] is None
        assert prediction.loc[stock, "prediction_status"] == "not_predicted"
    assert "60" in prediction.loc["1111", "reason_zh"]
    assert "市場最新資料日" in prediction.loc["2222", "reason_zh"]


def test_requested_h1_only_does_not_predict_other_heads_or_relax_schema(multi_case):
    prepared, model = multi_case
    changed = copy.deepcopy(prepared)
    changed["metadata"]["requested_horizons"] = [1]
    # Complete sealed schema remains present; not-requested source values may be missing.
    changed["features"]["return_252"] = np.nan
    called = []
    original = engine._head_predict

    def predict(head, frame, transformer):
        called.append(head)
        return original(head, frame, transformer)

    with mock.patch.object(engine, "_head_predict", side_effect=predict):
        result = engine.predict_candidate(model, changed)
    assert result["horizon"].tolist() == [1, 1, 1]
    short = model["horizons"]["1"]
    expected = [short["candidates"][short["selected"][task]]["heads"][task] for task in ("direction", "return")]
    assert len(called) == 6 and all(any(h is e for e in expected) for h in called)
    for field, value in (("feature_schema", "wrong-schema"), ("feature_order", ["volume_signal"]),
                         ("feature_order_by_horizon", {"1": ["volume_signal"], "63": []}),
                         ("feature_metadata", {})):
        bad = copy.deepcopy(changed)
        bad["metadata"][field] = value
        with pytest.raises(engine.CandidateModelError):
            engine.predict_candidate(model, bad)
    for horizons in ([], [5], [1, 63, 63], ["1"], [True]):
        bad = copy.deepcopy(changed)
        bad["metadata"]["requested_horizons"] = horizons
        with pytest.raises(engine.CandidateModelError, match="requested_horizons"):
            engine.predict_candidate(model, bad)


def test_known_not_trained_horizon_is_retained_while_unknown_horizon_is_rejected():
    prepared = _prepared(n=320, horizons=(1, 63))
    model = _linear_only(prepared)
    assert model["horizons"]["1"]["status"] == "trained"
    assert model["horizons"]["63"]["status"] == "not_trained"
    assert model["horizons"]["63"]["selected"] == {"direction": None, "return": None}
    prepared["metadata"]["requested_horizons"] = [1, 63]
    predicted = engine.predict_candidate(model, prepared)
    assert len(predicted) == 6
    missing = predicted.loc[predicted["horizon"].eq(63)]
    assert len(missing) == 3 and set(missing["stock_id"]) == {"1111", "2222", "3333"}
    assert missing["prediction_status"].eq("not_predicted").all()
    assert missing["prob_up"].isna().all() and missing["expected_return"].isna().all()
    assert missing["selected_direction_kind"].isna().all() and missing["selected_return_kind"].isna().all()
    assert missing["reason_zh"].str.contains("不足").all()
    assert predicted.loc[predicted["horizon"].eq(1), "prediction_status"].eq("research_estimate").all()
    prepared["metadata"]["requested_horizons"] = [63]
    only_untrained = engine.predict_candidate(model, prepared)
    assert only_untrained["horizon"].tolist() == [63, 63, 63]
    assert only_untrained["prediction_status"].eq("not_predicted").all()
    prepared["metadata"].pop("requested_horizons")
    assert set(engine.predict_candidate(model, prepared)["horizon"]) == {1, 63}
    prepared["metadata"]["requested_horizons"] = [1, 5]
    with pytest.raises(engine.CandidateModelError, match="requested_horizons"):
        engine.predict_candidate(model, prepared)


def test_per_market_asof_and_explicit_calendar_are_not_business_day_guesses(linear_case):
    prepared, model = linear_case
    changed = copy.deepcopy(prepared)
    axis = pd.bdate_range("2025-04-14", periods=450).strftime("%Y-%m-%d").tolist()
    tpex_asof = axis[366]
    changed["features"] = changed["features"].loc[~(changed["features"]["market"].eq("TPEX") &
        changed["features"]["session_date"].gt(tpex_asof))].copy()
    changed["metadata"]["asof_by_market"]["TPEX"] = tpex_asof
    changed["market_sessions"] = {"TWSE": axis, "TPEX": axis}
    result = engine.predict_candidate(model, changed).set_index("stock_id")
    assert result.loc["3333", "asof"] == tpex_asof
    assert result.loc["3333", "target_session_date"] == axis[366 + 63]
    assert result.loc["1111", "target_session_date"] == axis[367 + 63]
    assert result["deliverable"].eq(False).all()


def test_future_features_and_available_times_are_rejected_before_fit():
    prepared = _prepared()
    day = prepared["metadata"]["asof_by_market"]["TWSE"]
    bad = copy.deepcopy(prepared)
    bad["features"].loc[0, "session_date"] = (pd.Timestamp(day) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    with pytest.raises(engine.CandidateModelError, match="feature_after_market_asof"):
        engine.fit_candidate(bad, seed=7)
    bad = copy.deepcopy(prepared)
    bad["features"]["available_time"] = None
    bad["features"].loc[0, "available_time"] = "2027-01-01T12:00:00+08:00"
    with pytest.raises(engine.CandidateModelError, match="feature_available_after_signal"):
        engine.fit_candidate(bad, seed=7)
    bad = copy.deepcopy(prepared)
    bad["labels"].loc[0, "label_end"] = bad["labels"].loc[0, "session_date"]
    with pytest.raises(engine.CandidateModelError, match="label_not_after_signal"):
        engine.fit_candidate(bad, seed=7)
    bad = copy.deepcopy(prepared)
    bad["metadata"]["feature_order"].append("target_return")
    with pytest.raises(engine.CandidateModelError, match="invalid_feature_order"):
        engine.fit_candidate(bad, seed=7)


def test_iso_and_timestamp_inputs_normalize_identically(linear_case):
    prepared, model = linear_case
    changed = copy.deepcopy(prepared)
    changed["features"]["session_date"] = pd.to_datetime(changed["features"]["session_date"])
    changed["labels"]["session_date"] = pd.to_datetime(changed["labels"]["session_date"])
    changed["labels"]["label_end"] = pd.to_datetime(changed["labels"]["label_end"])
    predicted = engine.predict_candidate(model, changed)
    pd.testing.assert_frame_equal(predicted, engine.predict_candidate(model, prepared))
    features, labels, meta = engine._prepared(changed, labels_required=True)
    frame = engine._horizon_frame(features, labels, meta, 63)
    plan = engine._split_plan(frame, 63, 60, 20, 20)
    assert plan["blocks"] == model["horizons"]["63"]["splits"]


def test_label_purge_excludes_equality_and_unmatured_labels():
    prepared = _prepared()
    features, labels, meta = engine._prepared(prepared, labels_required=True)
    frame = engine._horizon_frame(features, labels, meta, 63)
    plan = engine._split_plan(frame, 63, 60, 20, 20)
    boundary = pd.Timestamp(plan["blocks"]["selection"]["start"])
    fit_raw = frame["session_date"].lt(boundary)
    equal = fit_raw & frame["label_end"].eq(boundary)
    assert equal.any()
    assert not (equal & plan["masks"]["fit"]).any()
    labels = labels.copy()
    ready = features.loc[features["feature_ready"], engine._KEYS].iloc[0]
    target = labels["stock_id"].eq(ready["stock_id"]) & labels["session_date"].eq(ready["session_date"])
    labels.loc[target, "label_end"] = pd.Timestamp("2027-01-01")
    bad_frame = engine._horizon_frame(features, labels, meta, 63)
    index = bad_frame["stock_id"].eq(ready["stock_id"]) & bad_frame["session_date"].eq(ready["session_date"])
    assert not bad_frame.loc[index, "label_eligible"].any()
    assert bad_frame.loc[index, "label_reason"].eq("label_not_mature_at_market_asof").all()


def test_strict_json_save_load_and_tamper_fail_closed(tmp_path, linear_case):
    prepared, model = linear_case
    path = tmp_path / "candidate.json"
    receipt = engine.save_candidate(model, path)
    assert receipt["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert engine.load_candidate(path) == model
    loaded = engine.load_candidate(path)
    pd.testing.assert_frame_equal(engine.predict_candidate(loaded, prepared), engine.predict_candidate(model, prepared))
    assert b"NaN" not in path.read_bytes() and b"Infinity" not in path.read_bytes()
    assert b"null" in path.read_bytes()
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(engine.CandidateModelError, match="sidecar_hash"):
        engine.load_candidate(path)
    engine.save_candidate(model, path)
    modified = json.loads(path.read_text(encoding="utf-8"))
    modified["horizons"]["63"]["candidates"]["expanded_linear"]["heads"]["return"]["payload"]["intercept"] += 1
    data = json.dumps(modified, ensure_ascii=False, allow_nan=False).encode("utf-8")
    path.write_bytes(data)
    path.with_suffix(".json.sha256").write_text(hashlib.sha256(data).hexdigest(), encoding="utf-8")
    with pytest.raises(engine.CandidateModelError, match="integrity"):
        engine.load_candidate(path)
    with pytest.raises(engine.CandidateModelError, match="current_pointer"):
        engine.save_candidate(model, tmp_path / "current.json")


@pytest.fixture(scope="module")
def native_case():
    rng = np.random.default_rng(17)
    x = rng.normal(size=(240, 3))
    x[::7, 0] = np.nan
    x[::11, 1] = np.nan
    y = np.nan_to_num(x[:, 0], nan=-1) + 0.7 * np.nan_to_num(x[:, 1]) - 0.3 * x[:, 2]
    fitted = {}
    with threadpool_limits(limits=4):
        for task, cls in (("direction", HistGradientBoostingClassifier), ("return", HistGradientBoostingRegressor)):
            target = (y > 0).astype(int) if task == "direction" else y / 20
            estimator = cls(**engine._HGB_PARAMS, random_state=7).fit(x, target)
            truth = estimator.predict_proba(x)[:, 1] if task == "direction" else estimator.predict(x)
            fitted[task] = (engine._export_hgb(estimator, task), truth)
    return x, fitted


@pytest.mark.parametrize("task", ["direction", "return"])
def test_hgb_native_json_roundtrip_matches_sklearn_including_nan(native_case, task):
    x, fitted = native_case
    payload, truth = fitted[task]
    restored = json.loads(json.dumps(payload, allow_nan=False))
    assert restored["sklearn_version"] == engine.sklearn.__version__
    assert restored["predictor_record_dtype"] == engine._dtype_manifest()
    np.testing.assert_allclose(engine._predict_hgb(restored, x), truth, rtol=0, atol=2e-14)
    assert np.isnan(x).any()


@pytest.mark.parametrize("mutation", ["version", "dtype", "shape", "hash", "feature", "child", "cycle", "leaf", "missing_bit", "category", "bitset"])
def test_unsafe_native_nodes_rejected_before_kernel(native_case, mutation):
    _, fitted = native_case
    payload = copy.deepcopy(fitted["direction"][0])
    if mutation == "version":
        payload["sklearn_version"] = "0.0.0"
    elif mutation == "dtype":
        payload["predictor_record_dtype"]["itemsize"] += 1
    elif mutation == "shape":
        payload["trees"][0]["nodes"]["shape"] = [999]
    elif mutation == "hash":
        payload["trees"][0]["nodes"]["sha256"] = "0" * 64
    elif mutation == "bitset":
        payload["trees"][0]["raw_left_cat_bitsets"]["shape"] = [1, 8]
    else:
        encoded = payload["trees"][0]["nodes"]
        nodes = engine._decode_array(encoded, np.dtype(engine.PREDICTOR_RECORD_DTYPE), tuple(encoded["shape"]))
        assert not nodes[0]["is_leaf"]
        field, value = {"feature": ("feature_idx", payload["n_features"]), "child": ("left", len(nodes)),
                        "cycle": ("left", 0), "leaf": ("is_leaf", 2),
                        "missing_bit": ("missing_go_to_left", 2), "category": ("is_categorical", 1)}[mutation]
        nodes[0][field] = value
        payload["trees"][0]["nodes"] = engine._encode_array(nodes)
    with mock.patch.object(engine.TreePredictor, "predict", side_effect=AssertionError("unsafe native kernel called")):
        with pytest.raises(engine.CandidateModelError):
            engine._predict_hgb(payload, np.zeros((1, 3)))


def test_full_candidate_hgb_save_load_roundtrip(tmp_path):
    prepared = _prepared(n=200, horizons=(1,))
    model = engine.fit_candidate(prepared, seed=7)
    item = model["horizons"]["1"]
    candidate = item["candidates"]["hist_gradient_boosting"]
    assert candidate["status"] == "trained", candidate["heads"]
    for column in ("return_252", "finance_unverified"):
        assert candidate["feature_validity"][column]["state"] == "fit_all_missing"
        assert column not in candidate["effective_feature_order"]
    path = tmp_path / "native_candidate.json"
    engine.save_candidate(model, path)
    restored = engine.load_candidate(path)
    features, _, _ = engine._prepared(prepared, labels_required=False)
    features = features.tail(20).copy()
    features.loc[features.index[::3], "volume_signal"] = np.nan
    original, loaded = item["candidates"]["hist_gradient_boosting"], restored["horizons"]["1"]["candidates"]["hist_gradient_boosting"]
    for task in ("direction", "return"):
        np.testing.assert_allclose(engine._head_predict(original["heads"][task], features, original["transformer"]),
                                   engine._head_predict(loaded["heads"][task], features, loaded["transformer"]),
                                   rtol=0, atol=0, equal_nan=True)


def test_native_all_missing_channel_is_constant_and_stays_unlearned():
    frame = pd.DataFrame({"feature_ready": [True, True, True], "signal": [-1.0, 0.0, 1.0],
                          "optional": [np.nan, np.nan, np.nan], "partial": [1.0, np.nan, 3.0]})
    transformer = engine._transformer(frame, ["signal", "optional", "partial"])
    native, eligible = engine._transform(frame, transformer, native=True)
    assert eligible.all()
    np.testing.assert_array_equal(native[:, 1], np.zeros(3))
    assert np.isnan(native[1, 2])
    assert transformer["feature_validity"]["optional"]["n_fit_finite"] == 0
    assert transformer["feature_validity"]["optional"]["state"] == "fit_all_missing"
    assert "optional" not in transformer["effective_feature_order"]
    assert "optional" not in transformer["effective_indicator_order"]
    changed = frame.copy()
    changed["optional"] = [999.0, -999.0, 10.0]
    later, later_eligible = engine._transform(changed, transformer, native=True)
    np.testing.assert_array_equal(later, native)
    np.testing.assert_array_equal(later_eligible, eligible)


def _flat_model(model):
    flat = copy.deepcopy(model)
    item = flat["horizons"]["63"]
    for task, kind in item["selected"].items():
        payload = item["candidates"][kind]["heads"][task]["payload"]
        payload["coef"] = [0.0] * len(payload["coef"])
        payload["intercept"] = 0.0
    return engine._seal(flat)


def test_genuine_flat_model_is_flagged_without_noise(linear_case):
    prepared, model = linear_case
    flat = _flat_model(model)
    predicted = engine.predict_candidate(flat, prepared)
    assert predicted["asof"].nunique() == 1
    assert predicted["market"].nunique() == 2
    assert predicted["prob_up"].eq(0.5).all()
    assert predicted["expected_return"].eq(0.0).all()
    assert predicted["no_ranking_discrimination"].eq(True).all()
    assert predicted["reason_zh"].str.contains("無排序辨識力").all()


def test_single_comparable_estimate_has_unknown_ranking_not_false(linear_case):
    prepared, model = linear_case
    single = copy.deepcopy(prepared)
    single["features"] = single["features"].loc[single["features"]["stock_id"].eq("3333")].copy()
    predicted = engine.predict_candidate(_flat_model(model), single)
    assert len(predicted) == 1
    assert predicted.iloc[0]["prob_up"] == 0.5
    assert predicted.iloc[0]["expected_return"] == 0.0
    assert predicted.iloc[0]["no_ranking_discrimination"] is None
    assert "無法判定排序辨識力" in predicted.iloc[0]["reason_zh"]


def test_different_market_asof_is_not_used_to_prove_flatness(linear_case):
    prepared, model = linear_case
    changed = copy.deepcopy(prepared)
    dates = sorted(changed["features"]["session_date"].unique())
    changed["metadata"]["asof_by_market"]["TPEX"] = dates[-2]
    changed["features"] = changed["features"].loc[~(changed["features"]["market"].eq("TPEX") &
        changed["features"]["session_date"].eq(dates[-1]))].copy()
    predicted = engine.predict_candidate(_flat_model(model), changed)
    assert predicted["prob_up"].eq(0.5).all()
    assert predicted["expected_return"].eq(0.0).all()
    assert predicted.loc[predicted["market"].eq("TWSE"), "no_ranking_discrimination"].eq(True).all()
    lagging = predicted.loc[predicted["market"].eq("TPEX")].iloc[0]
    assert lagging["no_ranking_discrimination"] is None
    assert "無法判定排序辨識力" in lagging["reason_zh"]


def test_different_estimates_in_one_branch_prove_observed_ranking_variation(linear_case):
    prepared, model = linear_case
    varying = _flat_model(model)
    item = varying["horizons"]["63"]
    candidate = item["candidates"][item["selected"]["return"]]
    index = candidate["feature_order"].index("return_5")
    candidate["heads"]["return"]["payload"]["coef"][index] = 1.0
    predicted = engine.predict_candidate(engine._seal(varying), prepared)
    assert predicted["prob_up"].eq(0.5).all()
    assert predicted["expected_return"].nunique() >= 2
    assert predicted["no_ranking_discrimination"].eq(False).all()
    assert predicted["reason_zh"].str.contains("存在排序差異").all()


def test_null_metrics_for_unscorable_cases_keep_market_counts():
    frame = pd.DataFrame({"stock_id": ["1111", "2222"], "market": ["TWSE", "TPEX"],
                          "session_date": pd.to_datetime(["2026-10-15", "2026-10-16"]),
                          "target_return": [np.nan, 0.0], "evaluation_eligible": [False, True]})
    metrics = engine._metrics(frame, np.array([np.nan, 0.0]), "return")
    assert metrics["n_denominator"] == 2 and metrics["n_evaluated"] == 1
    assert metrics["coverage"] == 0.5 and metrics["mae"] == 0.0
    assert metrics["by_market"]["TWSE"]["mae"] is None
    assert metrics["by_market"]["TWSE"]["n_denominator"] == 1
    assert metrics["by_market"]["TPEX"]["rmse"] == 0.0
    json.dumps(metrics, allow_nan=False)


@pytest.mark.parametrize("task", ["direction", "return"])
def test_perfect_remaining_rows_cannot_win_by_omitting_difficult_stocks(task):
    days = pd.bdate_range("2026-07-01", periods=20)
    frame = pd.DataFrame({"stock_id": [s for _ in days for s in ("1111", "2222")],
                          "market": ["TWSE"] * 40, "session_date": np.repeat(days, 2),
                          "target_return": [0.10, -0.10] * 20, "evaluation_eligible": [True] * 40})
    perfect_but_missing = np.array([1.0, np.nan] * 20) if task == "direction" else np.array([0.1, np.nan] * 20)
    complete = np.full(40, 0.6 if task == "direction" else 0.0)
    complete_metrics = engine._metrics(frame, complete, task)
    incomplete_metrics = engine._metrics(frame, perfect_but_missing, task)
    metric = "brier" if task == "direction" else "mae"
    assert incomplete_metrics[metric] == 0.0 < complete_metrics[metric]
    assert incomplete_metrics["n_effective_signal_days"] == 20
    assert incomplete_metrics["n_evaluated"] == 20 < incomplete_metrics["n_labeled"] == 40
    assert incomplete_metrics["coverage"] == 0.5
    candidates = {}
    for kind in engine._KINDS:
        stats = complete_metrics if kind == "seven_linear" else incomplete_metrics
        candidates[kind] = {"heads": {task: {"selection_eligible": True}}, "selection": {task: stats}}
    selected, reason = engine._choose(candidates, task)
    assert selected == "seven_linear"
    assert reason["requires_complete_common_labeled_set"] is True
    candidates["seven_linear"]["selection"][task] = incomplete_metrics
    selected, reason = engine._choose(candidates, task)
    assert selected is None
    assert "完整預測共同合格標籤集合" in reason["reason_zh"]
