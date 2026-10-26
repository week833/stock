from __future__ import annotations

import json
import numpy as np
import pandas as pd
import pytest

import stock_model_research as research


def inputs(days=800, stocks=3):
    axis = pd.bdate_range("2021-01-04", periods=days)
    features, labels = [], []
    for stock in range(stocks):
        for i, day in enumerate(axis):
            features.append({"stock_id": str(1000 + stock), "market": "TWSE",
                "session_date": day, "signal_close": 10.0 + i / 100,
                "feature_ready": i >= 59, "return_5": np.sin(i / 15 + stock),
                "return_252": np.nan if i < 252 else np.cos(i / 20)})
            observed = i + 63 < days
            labels.append({"stock_id": str(1000 + stock), "market": "TWSE",
                "session_date": day, "horizon": 63,
                "label_end": axis[i + 63] if observed else None,
                "target_return": np.sin(i / 13 + stock) / 10 if observed else np.nan,
                "label_state": "observed" if observed else "unknown_target_session"})
    return {"features": pd.DataFrame(features), "labels": pd.DataFrame(labels),
        "feature_order_by_horizon": {"63": ["return_5", "return_252"]},
        "baseline_feature_order_by_horizon": {"63": ["return_5", "return_252"]},
        "feature_metadata": {}, "market_sessions": {"TWSE": list(axis)},
        "asof_by_market": {"TWSE": axis[-1].strftime("%Y-%m-%d")},
        "source_audit": {"research_only": True}, "bindings": {"head_sha256": "a" * 64},
        "coverage": {}, "timings": {}}


def prepared(days=800, stocks=3):
    return research.prepare_inputs(inputs(days, stocks))


def prediction_frame(probabilities, targets, dates=None):
    if dates is None:
        dates = pd.bdate_range("2024-01-02", periods=len(targets))
    return pd.DataFrame({"row_id": np.arange(len(targets)),
        "stock_id": ["1000"] * len(targets), "market": "TWSE", "session_date": dates,
        "label_end": pd.DatetimeIndex(dates) + pd.Timedelta(days=90),
        "target_return": targets, "label_eligible": True, "prediction": probabilities,
        "feature_ready": True, "evaluation_eligible": True,
        "fold": 0, "phase": "development"})


def test_three_development_and_outer_folds_keep_126_mature_days_and_purge():
    frame, metadata = prepared()
    folds, summary = research.plan_folds(frame)
    assert [f.phase for f in folds] == ["development"] * 3 + ["outer"] * 3
    assert all(len(f.dates) == 42 for f in folds)
    assert summary["outer_signal_days"] == summary["development_signal_days"] == 126
    boundary = pd.Timestamp(summary["outer_start"])
    for fold in folds[:3]:
        scored = frame.session_date.isin(fold.dates) & frame.label_eligible
        assert (frame.loc[scored, "label_end"] < boundary).all()
    for fold in folds:
        indices, audit = research.fit_indices(frame, fold.start, 504)
        assert (frame.loc[indices, "label_end"] < fold.start).all()
        assert (frame.loc[indices, "session_date"] < fold.start).all()
        assert audit["effective_fit_days"] <= 504


def test_insufficient_dates_fail_without_shortening_outer_or_purge():
    frame, _ = prepared(days=350)
    with pytest.raises(research.ResearchError, match="insufficient"):
        research.plan_folds(frame)


def test_exact_endpoint_and_unique_keys_are_required():
    value = inputs()
    value["labels"].loc[0, "label_end"] = value["market_sessions"]["TWSE"][64]
    with pytest.raises(research.ResearchError, match="endpoint"):
        research.prepare_inputs(value)
    value = inputs()
    value["features"] = pd.concat([value["features"], value["features"].iloc[:1]])
    with pytest.raises(research.ResearchError, match="duplicate"):
        research.prepare_inputs(value)


def test_future_or_missing_label_is_retained_but_never_fit():
    value = inputs()
    value["labels"].loc[0, "target_return"] = np.nan
    frame, _ = research.prepare_inputs(value)
    assert len(frame) == len(value["features"])
    assert not frame.iloc[0].label_eligible
    indices, _ = research.fit_indices(frame, frame.session_date.max(), 756)
    assert frame.loc[indices, "label_eligible"].all()
    assert (frame.loc[indices, "label_end"] < frame.session_date.max()).all()


def test_effective_window_and_fit_hash_deduplicate_short_history():
    frame, _ = prepared()
    decision = sorted(frame.session_date.unique())[400]
    first, a = research.fit_indices(frame, decision, 504)
    second, b = research.fit_indices(frame, decision, 756)
    np.testing.assert_array_equal(first, second)
    assert a["fit_keys_sha256"] == b["fit_keys_sha256"]
    assert a["effective_fit_days"] == b["effective_fit_days"] < 504
    assert not a["requested_window_fulfilled"] and not b["requested_window_fulfilled"]
    assert a["requested_fit_days"] == 504 and b["requested_fit_days"] == 756


def test_fit_excludes_labels_equal_to_decision():
    frame, _ = prepared()
    decision = sorted(frame.session_date.unique())[400]
    indices, audit = research.fit_indices(frame, decision, 252)
    equal = frame.index[frame.label_end.eq(decision)]
    assert not set(indices) & set(equal)
    assert audit["purged_or_unmatured_rows"] > 0


def test_metrics_report_ordinary_and_balanced_accuracy_separately():
    dates = np.repeat(pd.bdate_range("2024-01-02", periods=2), 10)
    pred = prediction_frame([0.1] * 20, [-0.1] * 18 + [0.1] * 2, dates)
    daily = research.daily_metrics(pred, "direction")
    assert "accuracy" in daily and "balanced_accuracy" in daily and "auc" in daily
    assert research.summarise_daily(daily)["accuracy"] == pytest.approx(0.9)
    assert research.summarise_daily(daily)["balanced_accuracy"] == pytest.approx(0.5)
    assert research.summarise_daily(daily)["balanced_accuracy_dates"] == 1


def test_direction_selection_prefers_balanced_then_brier_then_short_window():
    recipes = [research.Recipe("a", "direction", 252), research.Recipe("b", "direction", 504)]
    metrics = {"a": {"balanced_accuracy": 0.55, "brier": 0.20},
               "b": {"balanced_accuracy": 0.56, "brier": 0.24}}
    assert research.choose_recipe(recipes, metrics, "direction").name == "b"
    metrics["b"] = dict(metrics["a"])
    assert research.choose_recipe(recipes, metrics, "direction").name == "a"
    metrics["b"]["brier"] = 0.19
    assert research.choose_recipe(recipes, metrics, "direction").name == "b"


def test_return_selection_uses_mae_then_shorter_window():
    recipes = [research.Recipe("a", "return", 252), research.Recipe("b", "return", 504)]
    assert research.choose_recipe(recipes, {"a": {"mae": 0.2}, "b": {"mae": 0.1}}, "return").name == "b"
    assert research.choose_recipe(recipes, {"a": {"mae": 0.1}, "b": {"mae": 0.1}}, "return").name == "a"


def test_calibration_cannot_win_by_harming_accuracy_or_mae():
    assert not research.choose_calibration("direction", {"accuracy": .6, "brier": .23},
        {"accuracy": .59, "brier": .22}, 63)["use_calibrated"]
    assert not research.choose_calibration("direction", {"accuracy": .6, "brier": .23},
        {"accuracy": .6, "brier": .24}, 63)["use_calibrated"]
    assert research.choose_calibration("direction", {"accuracy": .6, "brier": .23},
        {"accuracy": .61, "brier": .22}, 63)["use_calibrated"]
    assert not research.choose_calibration("return", {"mae": .1}, {"mae": .11}, 63)["use_calibrated"]
    assert not research.choose_calibration("return", {"mae": .1}, {"mae": .09}, 0)["use_calibrated"]
    assert not research.choose_calibration("return", {"mae": .1}, {"mae": .09}, 63,
        {"status": "constant_comparison_baseline", "slope": 0})["use_calibrated"]


def test_calibration_only_reads_prior_mature_oof():
    dates = pd.bdate_range("2023-01-02", periods=200)
    pred = prediction_frame(np.linspace(.1, .9, 200), np.sin(np.arange(200)) / 10, dates)
    pred["label_end"] = pd.bdate_range(dates[0], periods=263)[63:]
    cut = dates[150]
    pool, audit = research.calibration_pool(pred, cut)
    assert (pool.label_end < cut).all()
    assert (pool.session_date < cut).all()
    assert audit["mature_oof_days"] == 87
    original = research.fit_calibration(pool, "direction")
    modified = pred.copy()
    modified.loc[modified.label_end >= cut, "target_return"] = 1000
    newer, _ = research.calibration_pool(modified, cut)
    assert original == research.fit_calibration(newer, "direction")


def test_lad_return_calibration_is_not_a_hard_cap_or_forced_shrink():
    dates = pd.bdate_range("2022-01-03", periods=100)
    x = np.linspace(-.3, .3, 100)
    pred = prediction_frame(x, .02 + 2.4 * x, dates)
    calibration = research.fit_calibration(pred, "return")
    assert calibration["slope"] == pytest.approx(2.4, abs=.02)
    values = research.apply_calibration(calibration, np.array([1., -1.]), "return")
    assert values[0] > 2 and values[1] < -2


def test_constant_lad_is_a_comparison_baseline_not_an_individual_head():
    dates = pd.bdate_range("2022-01-03", periods=100)
    pred = prediction_frame(np.linspace(-.3, .3, 100), np.full(100, .02), dates)
    calibration = research.fit_calibration(pred, "return")
    assert calibration["status"] == "constant_comparison_baseline"
    assert not calibration["individual_signal_preserved"]


def test_date_block_bootstrap_reports_effective_blocks_not_stock_rows():
    values = np.linspace(-.1, .1, 126)
    interval = research.block_interval(values, block_days=63, repetitions=30, seed=7)
    assert interval["signal_days"] == 126 and interval["effective_full_blocks"] == 2
    assert interval["block_days"] == 63
    assert research.block_interval(values[:62], 63, 30, 7)["interval"] is None
    with pytest.raises(research.ResearchError, match="block"):
        research.block_interval(values, 20, 30, 7)


def test_output_is_fixed_and_unknown_existing_content_is_never_overwritten(tmp_path, monkeypatch):
    root = tmp_path / "candidate"
    monkeypatch.setattr(research, "OUTPUT_ROOT", root)
    root.mkdir()
    unknown = root / "existing.txt"
    unknown.write_text("keep", encoding="utf-8")
    with pytest.raises(research.ResearchError, match="nonempty"):
        research.reserve_output(root)
    assert unknown.read_text(encoding="utf-8") == "keep"
    with pytest.raises(research.ResearchError, match="fixed"):
        research.reserve_output(tmp_path / "other")


def test_output_refuses_symlink(tmp_path, monkeypatch):
    target = tmp_path / "real"
    target.mkdir()
    root = tmp_path / "candidate"
    try:
        root.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation is unavailable")
    monkeypatch.setattr(research, "OUTPUT_ROOT", root)
    with pytest.raises(research.ResearchError, match="link"):
        research.reserve_output(root)


def test_output_attempt_preserves_failure_and_refuses_reuse(tmp_path, monkeypatch):
    root = tmp_path / "candidate"
    monkeypatch.setattr(research, "OUTPUT_ROOT", root)
    research.reserve_output(root)
    failure = root / "performance_failure.json"
    failure.write_text('{"status":"performance_aborted_not_promoted"}', encoding="utf-8")
    first = research.reserve_output(root, attempt="attempt_02_macro_reuse")
    assert first.parent == root and (root / "run_started.json").is_file()
    assert failure.read_text(encoding="utf-8") == '{"status":"performance_aborted_not_promoted"}'
    with pytest.raises(research.ResearchError, match="nonempty"):
        research.reserve_output(root, attempt="attempt_02_macro_reuse")
    with pytest.raises(research.ResearchError, match="attempt_name"):
        research.reserve_output(root, attempt="../other")


def test_algorithm_source_change_is_detected_without_overwriting_source(tmp_path):
    source = tmp_path / "algorithm.py"
    source.write_text("value = 1\n", encoding="utf-8")
    starting = research.capture_algorithm_sources({"runner": source})
    assert research.verify_algorithm_sources(starting) == starting
    source.write_text("value = 2\n", encoding="utf-8")
    with pytest.raises(research.ResearchError, match="algorithm_sources_changed.*runner"):
        research.verify_algorithm_sources(starting)
    assert source.read_text(encoding="utf-8") == "value = 2\n"


def test_all_windows_share_column_order_and_requested_hgb_is_conservative():
    recipes = research.window_recipes()
    assert {r.window for r in recipes} == {80, 252, 504, 756}
    assert {r.task for r in recipes} == {"direction", "return"}
    params = research.hgb_parameters()
    assert params["max_iter"] == 120 and params["max_leaf_nodes"] == 15
    assert params["early_stopping"] is False and params["categorical_features"] is None


def test_outer_label_changes_cannot_change_split_or_fit_before_outer():
    frame, _ = prepared()
    folds, summary = research.plan_folds(frame)
    boundary = pd.Timestamp(summary["outer_start"])
    changed = frame.copy()
    changed.loc[changed.session_date >= boundary, "target_return"] = 1000
    other, later = research.plan_folds(changed)
    assert summary == later and folds == other
    for fold in folds[:3]:
        a, x = research.fit_indices(frame, fold.start, 252)
        b, y = research.fit_indices(changed, fold.start, 252)
        np.testing.assert_array_equal(a, b)
        assert x == y
        pd.testing.assert_frame_equal(frame.loc[a], changed.loc[b])


def test_sample50_is_presentation_only_and_fresh_seed_is_recorded():
    frame, _ = prepared(stocks=55)
    sample = research.presentation_sample(frame, seed=123)
    assert len(sample["stock_ids"]) == 50
    assert sample["population_stocks"] == 55 and sample["purpose"] == "presentation_only"
    assert len(frame.stock_id.unique()) == 55
    assert sample == research.presentation_sample(frame, seed=123)


def test_compact_metadata_does_not_serialize_raw_dataframes():
    cleaned = research.compact_metadata({"frame": pd.DataFrame({"value": range(1000)})})
    text = json.dumps(cleaned)
    assert "dataframe_summary" in text and len(text) < 500


def test_linear_transform_is_fit_only_and_final_weights_are_numeric():
    frame, metadata = prepared(days=450)
    decision = sorted(frame.session_date.unique())[350]
    indices, _ = research.fit_indices(frame, decision, 80)
    training = frame.loc[indices]
    columns = metadata["model_feature_order"]
    fitted = research.fit_head(training, columns, research.Recipe("linear", "return", 80), seed=7)
    before = research.predict_head(fitted, frame.iloc[:4])
    changed = frame.copy()
    changed.loc[changed.session_date >= decision, "return_5"] = 1e10
    same = research.fit_head(changed.loc[indices], columns, research.Recipe("linear", "return", 80), seed=7)
    assert fitted["transformer"] == same["transformer"]
    assert fitted["head"] == same["head"]
    np.testing.assert_allclose(before, research.predict_head(same, frame.iloc[:4]), equal_nan=True)


def test_120_tree_weights_replay_with_core_validator_and_estimator(monkeypatch):
    frame, metadata = prepared(days=450)
    decision = sorted(frame.session_date.unique())[350]
    indices, _ = research.fit_indices(frame, decision, 80)
    parameters = {**research.hgb_parameters(), "min_samples_leaf": 5}
    monkeypatch.setattr(research, "hgb_parameters", lambda: parameters)
    recipe = research.Recipe("hgb", "return", 80, "hgb")
    fitted = research.fit_head(frame.loc[indices], metadata["model_feature_order"], recipe)
    rows = frame.loc[frame.session_date >= decision].iloc[:20]
    saved = {k: v for k, v in fitted.items() if k != "estimator"}
    assert len(saved["head"]["payload"]["trees"]) == 120
    x, valid = research._core()._transform(rows, fitted["transformer"], native=True)
    expected = np.full(len(rows), np.nan)
    expected[valid] = fitted["estimator"].predict(x[valid])
    np.testing.assert_allclose(research.predict_head(fitted, rows), expected, rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(research.predict_head(fitted, rows),
        research.predict_saved_head(saved, rows), rtol=1e-12, atol=1e-12)


def test_new_listing_warmup_rows_remain_in_denominator_and_unscored():
    value = inputs(days=450)
    axis = value["market_sessions"]["TWSE"]
    features = value["features"]
    value["features"] = features.loc[~((features.stock_id == "1002") & (features.session_date < axis[250]))].copy()
    new = value["features"].stock_id == "1002"
    value["features"].loc[new, "feature_ready"] = np.arange(new.sum()) >= 59
    frame, metadata = research.prepare_inputs(value)
    fold = research.Fold("development", 0, tuple(axis[250:292]))
    recipes = [research.Recipe("baseline", "return", 80), research.Recipe("longer", "return", 252)]
    outputs, audits = research.evaluate_recipes(frame, [fold], recipes,
        metadata["model_feature_order"], progress=lambda *args, **kwargs: None)
    for output in outputs.values():
        new_stock = output.loc[output.stock_id == "1002"]
        assert len(new_stock) == 42 and new_stock.label_eligible.all()
        assert not new_stock.feature_ready.any() and not new_stock.evaluation_eligible.any()
        assert new_stock.prediction.isna().all()
        assert set(new_stock.prediction_status) == {"unavailable_price_warmup_lt60"}
        daily = research.daily_metrics(output, "return")
        assert daily.n_denominator.sum() == 126
        assert daily.n_unscored_not_ready.sum() == 42
        assert daily.n_evaluated.sum() == 84
    assert all(a["evaluation_not_ready_rows"] == 42 for a in audits)
    assert outputs["baseline"].evaluation_eligible.equals(outputs["longer"].evaluation_eligible)


def test_legacy_seven_cannot_impersonate_current_expanded_baseline():
    value = inputs()
    seven = ["return_5", "return_20", "return_60", "return_126", "return_252", "volatility_20", "volatility_60"]
    for col in seven:
        if col not in value["features"]:
            value["features"][col] = np.nan
    value["feature_order_by_horizon"]["63"] = seven
    value["baseline_feature_order_by_horizon"]["63"] = seven
    with pytest.raises(research.ResearchError, match="legacy_seven"):
        research.prepare_inputs(value)


def test_unsupported_h1_primary_is_not_substituted_with_selected_backup():
    with pytest.raises(research.ResearchError, match="H63"):
        research.run_research(horizons=(1,), builder=lambda **kwargs: pytest.fail("must not build"))


def test_compact_runner_keeps_all_stocks_locks_selection_and_saves_only_final_weights(tmp_path, monkeypatch):
    values = inputs(days=800, stocks=4)
    full_axis = pd.bdate_range(values["market_sessions"]["TWSE"][0], periods=863)
    values["market_sessions"]["TWSE"] = list(full_axis)
    missing = values["labels"].label_end.isna()
    positions = full_axis.get_indexer(values["labels"].loc[missing, "session_date"])
    values["labels"].loc[missing, "label_end"] = full_axis[positions + 63]
    values["labels"].loc[missing, "label_state"] = "unmatured"
    calls = []

    def fit(training, columns, recipe, seed=research.MODEL_SEED, transformer=None, matrix=None, threads=4):
        if transformer is None:
            transformer = research._core()._transformer(training, columns)
        calls.append((recipe.name, training.label_end.max(), training.session_date.max()))
        return {"recipe": {"name": recipe.name, "task": recipe.task, "window": recipe.window,
                    "family": recipe.family, "c": recipe.c, "alpha": recipe.alpha},
            "transformer": transformer, "head": {"status": "trained", "individual_signal_learned": True,
                "n_fit_rows": len(training), "payload": {"coef": [0.1], "intercept": 0.0}}}

    def predict(fitted, rows, threads=4):
        signal = rows.return_5.to_numpy(float)
        return 1 / (1 + np.exp(-signal)) if fitted["recipe"]["task"] == "direction" else .01 * signal

    monkeypatch.setattr(research, "fit_head", fit)
    monkeypatch.setattr(research, "predict_head", predict)
    monkeypatch.setattr(research, "candidate_replay_check", lambda *args: {"status": "synthetic_integration_fixture"})
    root = tmp_path / "first"
    monkeypatch.setattr(research, "OUTPUT_ROOT", root)
    first = research.run_research(builder=lambda **kwargs: values, sample_seed=123,
                                 bootstrap_repetitions=20, progress=lambda *args, **kwargs: None)
    assert first["sample50"]["population_stocks"] == 4
    assert first["selection"]["direction"]["window"] == 80
    assert first["selection"]["return"]["window"] == 80
    assert not any(p.suffix in {".parquet", ".pkl", ".pickle"} for p in root.iterdir())
    assert not any(p.is_dir() for p in root.iterdir())
    assert len(list(root.glob("*model*"))) == 1
    assert (root / "sample50_predictions.csv.gz").is_file()
    split = json.loads((root / "split_summary.json").read_text(encoding="utf-8"))
    for audit in split["fit_audits"]:
        assert pd.Timestamp(audit["maximum_fit_label_end"]) < pd.Timestamp(audit["decision_date"])
    artifact = json.loads((root / "candidate_model.json").read_text(encoding="utf-8"))
    assert artifact["performance_scope"] == "new_final_weights_have_no_prospective_results"
    assert artifact["versions"]["sklearn"] == research.sklearn.__version__
    assert {"runner", "data_adapter", "candidate_model", "feature_catalog", "price_quality"} <= set(artifact["algorithm_sources"])
    assert artifact["algorithm_sources_verified_before_output"] is True
    assert (root / "algorithm_sources_start.json").is_file()
    expected_final_decision = (values["features"].session_date.max() + pd.Timedelta(days=1)).isoformat()
    assert artifact["final_decision_date"] == expected_final_decision
    assert split["calibration_audits"]["final_direction"]["decision_date"] == expected_final_decision
    assert artifact["heads"]["return"]["final_fit"]["decision_date"] == expected_final_decision
    unsigned = {k: v for k, v in artifact.items() if k != "integrity_sha256"}
    assert artifact["integrity_sha256"] == research.digest(unsigned)
    report = (root / "RESULTS.md").read_text(encoding="utf-8")
    assert "## 方向外層結果" in report and "## 報酬外層結果" in report
    assert "報酬MAE（百分點）" in report and "實際方向／報酬訓練日數範圍" in report
    assert all(f"| {window} |" in report for window in (252, 504, 756))
    assert not any(word in report for word in ("head", "variant", "requested fit", "lookback", "N/A"))
    assert calls and (root / "run_manifest.json").is_file()
    changed = {**values, "labels": values["labels"].copy()}
    boundary = pd.Timestamp(split["outer_start"])
    changed["labels"].loc[changed["labels"].session_date >= boundary, "target_return"] *= -2
    monkeypatch.setattr(research, "OUTPUT_ROOT", tmp_path / "second")
    second = research.run_research(builder=lambda **kwargs: changed, sample_seed=123,
                                  bootstrap_repetitions=20, progress=lambda *args, **kwargs: None)
    assert first["selection"] == second["selection"]
    assert first["window_selection"] == second["window_selection"]
    assert first["selection_sha256"] == second["selection_sha256"]


def test_insufficient_run_saves_reason_without_fitting_or_relaxing_plan(tmp_path, monkeypatch):
    root = tmp_path / "blocked"
    monkeypatch.setattr(research, "OUTPUT_ROOT", root)
    monkeypatch.setattr(research, "fit_head", lambda *args, **kwargs: pytest.fail("must not fit"))
    with pytest.raises(research.ResearchError, match="insufficient"):
        research.run_research(builder=lambda **kwargs: inputs(days=350), bootstrap_repetitions=20)
    failure = json.loads((root / "run_failure.json").read_text(encoding="utf-8"))
    assert failure["status"] == "blocked_or_failed_not_promoted"
    assert not (root / "candidate_model.json").exists()


def test_candidate_replay_uses_final_weights_without_refitting():
    frame, metadata = prepared(days=450)
    indices, _ = research.fit_indices(frame, sorted(frame.session_date.unique())[350], 80)
    fitted = research.fit_head(frame.loc[indices], metadata["model_feature_order"], research.Recipe("return", "return", 80))
    rows = frame.loc[frame.session_date == frame.session_date.max()]
    calibration = {"status": "fitted", "intercept": .02, "slope": 2.4}
    result = research.candidate_replay_check(fitted, rows, calibration, True, ["1000"])
    assert result["no_retraining"] and result["maximum_raw_absolute_delta"] < 1e-12
    item = result["sample50_predictions"][0]
    assert item["chosen"] == pytest.approx(item["raw"] * 2.4 + .02)


@pytest.mark.parametrize("days, expect_fitted", [(800, True), (570, False)])
def test_selected_recipe_oof_warmup_enables_long_history_and_keeps_short_history_raw(days, expect_fitted):
    frame, metadata = prepared(days=days)
    folds, split = research.plan_folds(frame)
    dev_folds = [f for f in folds if f.phase == "development"]
    warm_folds, summary = research.plan_oof_warmup(frame, split["development_start"], split["outer_start"])
    recipe = research.Recipe("selected_direction", "direction", 252)
    dev, dev_audits = research.evaluate_recipes(frame, dev_folds, [recipe],
        metadata["model_feature_order"], progress=lambda *args, **kwargs: None)
    warm, warm_audits = research.evaluate_recipes(frame, warm_folds, [recipe],
        metadata["model_feature_order"], progress=lambda *args, **kwargs: None)
    history = pd.concat([warm[recipe.name], dev[recipe.name]], ignore_index=True)
    calibrated, audits = research.calibrated_predictions(dev[recipe.name], history, "direction")
    assert summary["available_signal_days"] <= 126 and summary["actual_folds"] <= 3
    for audit in [*dev_audits, *warm_audits]:
        if audit["fit_status"] == "trained":
            assert pd.Timestamp(audit["maximum_fit_label_end"]) < pd.Timestamp(audit["decision_date"])
    if expect_fitted:
        assert audits[0]["mature_oof_days"] >= 63
        assert any(a["calibration"]["status"] == "fitted" for a in audits)
        assert calibrated.calibrated_flag.any()
    else:
        assert not calibrated.calibrated_flag.any()
        assert all(a["calibration"]["status"] == "unavailable" for a in audits)
        assert any(a["fit_status"] == "failed" and "insufficient" in a["error"] for a in warm_audits)
        np.testing.assert_allclose(calibrated.prediction, dev[recipe.name].prediction, equal_nan=True)

