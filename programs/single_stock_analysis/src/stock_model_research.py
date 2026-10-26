"""Isolated H63 current-vintage retrospective research, never model promotion.

The data adapter owns read-only source access. This runner owns only temporal
selection, compact candidate outputs, and final weights in one fixed directory.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import secrets
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True

import numpy as np
import pandas as pd
import sklearn
from scipy.optimize import minimize
from scipy.special import expit, logit
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import roc_auc_score
from threadpoolctl import threadpool_limits

OUTPUT_ROOT = Path(r"D:\TWStockData\program_data\dstock_single\candidates\current_data_optimization_20261025")
SCHEMA = "single-stock-h63-isolated-research-v1"
KEYS = ["stock_id", "market", "session_date"]
HORIZON = 63
FOLD_DAYS = 42
OUTER_DAYS = 126
DEVELOPMENT_DAYS = 126
MIN_FIT_DAYS = 60
MIN_CALIBRATION_DAYS = 63
CALIBRATION_DAYS = 252
MODEL_SEED = 20261025


class ResearchError(ValueError):
    """A research boundary or required temporal evidence is unavailable."""


@dataclass(frozen=True)
class Recipe:
    name: str
    task: str
    window: int
    family: str = "linear"
    c: float = 1.0
    alpha: float = 10.0


@dataclass(frozen=True)
class Fold:
    phase: str
    number: int
    dates: tuple
    score_before: pd.Timestamp | None = None

    @property
    def start(self):
        return pd.Timestamp(self.dates[0])


def require(condition, reason):
    if not condition:
        raise ResearchError(reason)


def clean(value):
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.ndarray):
        return clean(value.tolist())
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def digest(value):
    raw = json.dumps(clean(value), sort_keys=True, ensure_ascii=False,
                     allow_nan=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def frame_digest(frame, columns):
    hashes = pd.util.hash_pandas_object(frame[columns], index=False).to_numpy(dtype="uint64")
    return hashlib.sha256(hashes.tobytes()).hexdigest()


def versions():
    return {"numpy": np.__version__, "pandas": pd.__version__, "sklearn": sklearn.__version__}


def file_sha256(path):
    path = Path(path)
    before = path.stat()
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    after = path.stat()
    require((before.st_size, before.st_mtime_ns, before.st_ino) ==
            (after.st_size, after.st_mtime_ns, after.st_ino), "algorithm_source_changed_while_hashing:" + str(path))
    return hasher.hexdigest()


def capture_algorithm_sources(paths=None):
    if paths is None:
        shared = Path(__file__).resolve().parents[2] / "shared"
        paths = {"runner": Path(__file__),
            "data_adapter": Path(__file__).with_name("stock_model_research_data.py"),
            "candidate_model": Path(_core().__file__),
            "feature_catalog": shared / "stock_daily_feature_catalog.py",
            "price_quality": shared / "stock_daily_price_quality.py",
            "candidate_runtime": shared / "stock_daily_candidate_runtime.py",
            "contract": shared / "stock_daily_contract.py",
            "query_projection": Path(__file__).with_name("stock_query_projection.py")}
    return {name: {"path": str(Path(path).resolve()), "sha256": file_sha256(path)}
            for name, path in paths.items()}


def verify_algorithm_sources(start):
    current = capture_algorithm_sources({name: Path(ref["path"]) for name, ref in start.items()})
    changed = [name for name in start if current[name] != start[name]]
    require(not changed, "algorithm_sources_changed_during_research:" + ",".join(changed))
    return current


def compact_metadata(value):
    if isinstance(value, pd.DataFrame):
        return {"dataframe_summary": True, "rows": len(value), "columns": list(value.columns)}
    if isinstance(value, pd.Series):
        return {"series_summary": True, "rows": len(value), "name": str(value.name)}
    if isinstance(value, dict):
        return {str(k): compact_metadata(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        if len(value) > 100:
            normal = clean(value)
            return {"items": len(value), "sha256": digest(normal), "preview": normal[:10], "truncated": True}
        return [compact_metadata(v) for v in value]
    return clean(value)


def log(event, **details):
    print(json.dumps(clean({"event": event, **details}), ensure_ascii=False, allow_nan=False), flush=True)


def adapter_progress(*args, **kwargs):
    log("source_progress", message=compact_metadata(list(args)), details=compact_metadata(kwargs))


def _core():
    shared = Path(__file__).resolve().parents[2] / "shared"
    if str(shared) not in sys.path:
        sys.path.insert(0, str(shared))
    import stock_daily_candidate_model
    return stock_daily_candidate_model


def _dates(values, name):
    parsed = pd.to_datetime(values, errors="raise")
    require(getattr(parsed.dt, "tz", None) is None, name + "_timezone_unsupported")
    require((parsed.dropna() == parsed.dropna().dt.normalize()).all(), name + "_not_market_date")
    return parsed


def _order(mapping):
    return list(mapping.get("63", mapping.get(63, [])))


def prepare_inputs(inputs):
    required = {"features", "labels", "feature_order_by_horizon", "baseline_feature_order_by_horizon",
                "feature_metadata", "market_sessions", "asof_by_market", "source_audit", "bindings",
                "coverage", "timings"}
    require(isinstance(inputs, dict) and required <= set(inputs), "research_input_contract_missing")
    require(bool(inputs["bindings"]), "pinned_source_bindings_required")
    features, labels = inputs["features"].copy(), inputs["labels"].copy()
    columns = _order(inputs["feature_order_by_horizon"])
    baseline_columns = _order(inputs["baseline_feature_order_by_horizon"])
    require(columns and baseline_columns and len(set(columns)) == len(columns), "feature_order_invalid")
    legacy_seven = {"return_5", "return_20", "return_60", "return_126", "return_252", "volatility_20", "volatility_60"}
    require(set(baseline_columns) != legacy_seven, "legacy_seven_is_not_current_expanded_baseline")
    require(len(set(baseline_columns)) == len(baseline_columns), "baseline_feature_order_duplicates")
    require(set(baseline_columns) <= set(columns), "baseline_columns_not_in_research_matrix")
    require(set(KEYS + ["signal_close", "feature_ready"] + columns) <= set(features), "feature_columns_missing")
    require(set(KEYS + ["horizon", "label_end", "target_return", "label_state"]) <= set(labels), "label_columns_missing")
    labels = labels.loc[pd.to_numeric(labels.horizon, errors="raise").eq(HORIZON)].copy()
    for table in (features, labels):
        table["stock_id"] = table.stock_id.astype(str).str.strip()
        table["market"] = table.market.astype(str).str.upper()
        table["session_date"] = _dates(table.session_date, "session_date")
        require(not table[KEYS].isna().any().any() and table.stock_id.ne("").all(), "empty_feature_key")
        require(not table.duplicated(KEYS).any(), "duplicate_stock_market_date")
    require(not features.empty and not labels.empty, "H63_inputs_empty")
    labels["label_end"] = _dates(labels.label_end, "label_end")
    labels["target_return"] = pd.to_numeric(labels.target_return, errors="raise")
    asofs = {str(k).upper(): pd.Timestamp(v).normalize() for k, v in inputs["asof_by_market"].items()}
    sessions = {}
    for market, values in inputs["market_sessions"].items():
        axis = pd.DatetimeIndex(pd.to_datetime(list(values)))
        require(not axis.empty and axis.is_unique and axis.is_monotonic_increasing
                and axis.tz is None, "market_sessions_invalid")
        sessions[str(market).upper()] = axis
    for market, part in features.groupby("market", sort=False):
        require(market in asofs and market in sessions and asofs[market] in sessions[market], "market_asof_missing")
        require((part.session_date <= asofs[market]).all(), "future_feature_row")
        require(part.session_date.isin(sessions[market]).all(), "feature_date_not_market_session")
    for market, part in labels.groupby("market", sort=False):
        require(market in sessions, "label_market_unknown")
        axis = sessions[market]
        positions = axis.get_indexer(part.session_date)
        require((positions >= 0).all(), "label_signal_not_market_session")
        targets = positions + HORIZON
        known = targets < len(axis)
        observed = part.label_state.eq("observed").to_numpy()
        require(not (observed & ~known).any(), "observed_endpoint_not_in_calendar")
        if observed.any():
            expected = pd.DatetimeIndex(axis[targets[observed]])
            actual = pd.DatetimeIndex(part.loc[part.label_state.eq("observed"), "label_end"])
            require(actual.equals(expected), "label_endpoint_mismatch")
    frame = features.merge(labels[KEYS + ["label_end", "target_return", "label_state"]],
                           on=KEYS, how="left", validate="one_to_one")
    frame.sort_values(["session_date", "market", "stock_id"], inplace=True)
    frame.reset_index(drop=True, inplace=True)
    frame["row_id"] = np.arange(len(frame), dtype=np.int64)
    require(frame.feature_ready.dropna().isin([True, False]).all(), "feature_ready_not_boolean")
    frame["feature_ready"] = frame.feature_ready.fillna(False).astype(bool)
    frame["label_eligible"] = (frame.label_state.eq("observed") & frame.label_end.notna()
        & np.isfinite(frame.target_return) & (frame.label_end <= frame.market.map(asofs)))
    for col in columns:
        frame[col] = pd.to_numeric(frame[col], errors="raise").replace([np.inf, -np.inf], np.nan)
    metadata = {"bindings": compact_metadata(inputs["bindings"]), "binding_sha256": digest(inputs["bindings"]),
        "feature_order": columns, "baseline_feature_order": baseline_columns,
        "model_feature_order": baseline_columns,
        "feature_policy": "same_baseline_column_order_for_all_windows_and_families;full_adapter_matrix_retained_in_memory",
        "feature_metadata": compact_metadata(inputs["feature_metadata"]),
        "source_audit": compact_metadata(inputs["source_audit"]), "coverage": compact_metadata(inputs["coverage"]),
        "input_metadata": compact_metadata(inputs.get("metadata", {})),
        "adapter_timings": compact_metadata(inputs["timings"]), "asof_by_market": clean(asofs),
        "market_session_counts": {k: len(v) for k, v in sessions.items()},
        "rows": len(frame), "stocks": frame.stock_id.nunique(),
        "feature_keys_sha256": frame_digest(frame, KEYS),
        "matrix_and_label_sha256": frame_digest(frame, KEYS + columns + ["label_end", "target_return", "label_state"])}
    return frame, metadata


def plan_folds(frame):
    mature_dates = pd.DatetimeIndex(sorted(frame.loc[frame.label_eligible, "session_date"].unique()))
    require(len(mature_dates) >= OUTER_DAYS + DEVELOPMENT_DAYS, "insufficient_mature_signal_dates")
    outer = mature_dates[-OUTER_DAYS:]
    development_rows = frame.label_eligible & (frame.label_end < outer[0]) & (frame.session_date < outer[0])
    development_dates = pd.DatetimeIndex(sorted(frame.loc[development_rows, "session_date"].unique()))
    require(len(development_dates) >= DEVELOPMENT_DAYS, "insufficient_purged_development_dates")
    development = development_dates[-DEVELOPMENT_DAYS:]
    indices, _ = fit_indices(frame, development[0], 80)
    require(frame.loc[indices, "session_date"].nunique() >= MIN_FIT_DAYS, "insufficient_initial_fit_days")
    folds = []
    for phase, days, boundary in (("development", development, outer[0]), ("outer", outer, None)):
        for i in range(0, len(days), FOLD_DAYS):
            fold_days = tuple(pd.Timestamp(d) for d in days[i:i + FOLD_DAYS])
            require(len(fold_days) == FOLD_DAYS, "incomplete_fixed_fold")
            folds.append(Fold(phase, i // FOLD_DAYS, fold_days, boundary))
    summary = {"development_start": development[0].isoformat(), "development_end": development[-1].isoformat(),
        "outer_start": outer[0].isoformat(), "outer_end": outer[-1].isoformat(),
        "development_signal_days": len(development), "outer_signal_days": len(outer),
        "fold_days": FOLD_DAYS, "folds_per_phase": 3,
        "development_label_end_rule": "strictly_before_outer_start",
        "fit_label_end_rule": "strictly_before_each_fold_start",
        "outer_policy": "report_only;selection_locked_before_outer;predeclared_rolling_refits_use_only_then_mature_labels",
        "vintage": "current-vintage retrospective;not prospective OOS",
        "folds": [{"phase": f.phase, "fold": f.number, "start": f.start.isoformat(),
                   "end": f.dates[-1].isoformat(), "signal_days": len(f.dates)} for f in folds]}
    return folds, summary


def plan_oof_warmup(frame, development_start, outer_start):
    days = pd.DatetimeIndex(sorted(frame.loc[frame.session_date < pd.Timestamp(development_start),
                                           "session_date"].unique()))[-126:]
    folds = [Fold("oof_warmup", i // FOLD_DAYS, tuple(pd.Timestamp(d) for d in days[i:i + FOLD_DAYS]),
                  pd.Timestamp(outer_start)) for i in range(0, len(days), FOLD_DAYS)]
    return folds, {"requested_signal_days": 126, "available_signal_days": len(days),
        "requested_folds": 3, "actual_folds": len(folds), "selected_recipes_only": True,
        "fit_minimum_days": MIN_FIT_DAYS, "per_fold_insufficiency_retained_not_relaxed": True,
        "start": days[0].isoformat() if len(days) else None,
        "end": days[-1].isoformat() if len(days) else None,
        "raw_warmup_rows_saved": False}


def fit_indices(frame, decision, window):
    decision = pd.Timestamp(decision)
    require(type(window) is int and window >= MIN_FIT_DAYS, "invalid_fit_window")
    past = frame.session_date < decision
    mature = past & frame.label_eligible & (frame.label_end < decision)
    ready = mature & frame.feature_ready
    available_dates = pd.DatetimeIndex(sorted(frame.loc[ready, "session_date"].unique()))
    chosen = available_dates[-window:]
    selected_dates = frame.session_date.isin(chosen)
    indices = frame.index[ready & selected_dates].to_numpy(dtype=np.int64)
    identity = hashlib.sha256(indices.tobytes()).hexdigest()
    audit = {"decision_date": decision.isoformat(), "requested_fit_days": window,
        "effective_fit_days": len(chosen), "available_mature_ready_days": len(available_dates),
        "requested_window_fulfilled": len(chosen) == window, "past_rows": int(past.sum()),
        "mature_past_rows": int(mature.sum()), "purged_or_unmatured_rows": int((past & ~mature).sum()),
        "not_feature_ready_rows": int((mature & ~frame.feature_ready).sum()),
        "window_denominator_rows": int((past & selected_dates).sum()), "fit_rows": len(indices),
        "fit_stocks": int(frame.loc[indices, "stock_id"].nunique()),
        "fit_first_signal": chosen[0].isoformat() if len(chosen) else None,
        "fit_last_signal": chosen[-1].isoformat() if len(chosen) else None,
        "maximum_fit_label_end": clean(frame.loc[indices, "label_end"].max()),
        "fit_keys_sha256": identity, "hash_semantics": "ordered_row_ids_bound_to_run_matrix_sha256"}
    return indices, audit


def window_recipes():
    return [Recipe(f"{task}_linear_w{window}", task, window)
            for window in (80, 252, 504, 756) for task in ("direction", "return")]


def hgb_parameters():
    return {"max_iter": 120, "learning_rate": .05, "max_leaf_nodes": 15, "max_depth": 3,
        "min_samples_leaf": 500, "l2_regularization": 10.0, "early_stopping": False,
        "categorical_features": None}


def fit_head(training, columns, recipe, seed=MODEL_SEED, transformer=None, matrix=None, threads=4):
    require(recipe.task in ("direction", "return") and recipe.family in ("linear", "hgb"), "recipe_invalid")
    require(training.session_date.nunique() >= MIN_FIT_DAYS, "insufficient_effective_fit_days")
    core = _core()
    transformer = transformer if transformer is not None else core._transformer(training, columns)
    native = recipe.family == "hgb"
    x, valid = matrix if matrix is not None else core._transform(training, transformer, native=native)
    target = training.target_return.to_numpy(dtype=float)
    valid = valid & np.isfinite(target)
    require(valid.any(), "no_informative_fit_rows")
    y = (target[valid] > 0).astype(int) if recipe.task == "direction" else target[valid]
    require(recipe.task != "direction" or len(np.unique(y)) == 2, "direction_fit_single_class")
    with threadpool_limits(limits=threads):
        if native:
            cls = HistGradientBoostingClassifier if recipe.task == "direction" else HistGradientBoostingRegressor
            params = {**hgb_parameters(), "random_state": seed}
            if recipe.task == "return":
                params["loss"] = "absolute_error"
            estimator = cls(**params).fit(x[valid], y)
            payload = core._export_hgb(estimator, recipe.task)
            discriminating = any((~p.nodes["is_leaf"].astype(bool)).any()
                                 for iteration in estimator._predictors for p in iteration)
        else:
            estimator = (LogisticRegression(C=recipe.c, max_iter=1000, random_state=seed)
                         if recipe.task == "direction" else Ridge(alpha=recipe.alpha)).fit(x[valid], y)
            payload = {"format": "linear-coefficients-json-v1", "task": recipe.task,
                "coef": np.asarray(estimator.coef_).reshape(-1).tolist(),
                "intercept": float(np.asarray(estimator.intercept_).reshape(-1)[0])}
            discriminating = bool(np.any(np.asarray(estimator.coef_) != 0))
    head = {"status": "trained", "kind": "hist_gradient_boosting" if native else "expanded_linear",
        "task": recipe.task, "payload": payload, "calibration": None,
        "n_fit_rows": int(valid.sum()), "n_fit_denominator": len(training),
        "n_fit_signal_days": int(training.loc[valid, "session_date"].nunique()),
        "effective_fit_row_ids_sha256": hashlib.sha256(training.loc[valid, "row_id"].to_numpy(dtype=np.int64).tobytes()).hexdigest(),
        "individual_signal_learned": discriminating}
    return {"head": head, "transformer": transformer, "recipe": asdict(recipe),
            "estimator": estimator}


def predict_head(fitted, rows, threads=4):
    core = _core()
    require("feature_ready" in rows, "prediction_feature_readiness_required")
    with threadpool_limits(limits=threads):
        result = core._head_predict(fitted["head"], rows, fitted["transformer"])
    result[~rows.feature_ready.fillna(False).to_numpy(dtype=bool)] = np.nan
    return result


def predict_saved_head(saved, rows, threads=4):
    """Use the existing validated numeric-head replay; never load a pickle."""
    return predict_head(saved, rows, threads)


def candidate_replay_check(fitted, rows, calibration, use_calibration, sample_stocks, threads=4):
    task = fitted["recipe"]["task"]
    saved = json.loads(json.dumps(clean({key: fitted[key] for key in ("head", "transformer", "recipe")})))
    raw = predict_saved_head(saved, rows, threads)
    x, valid = _core()._transform(rows, fitted["transformer"], native=fitted["recipe"]["family"] == "hgb")
    expected = np.full(len(rows), np.nan)
    if valid.any():
        with threadpool_limits(limits=threads):
            estimator = fitted["estimator"]
            expected[valid] = estimator.predict_proba(x[valid])[:, 1] if task == "direction" else estimator.predict(x[valid])
    require(np.allclose(raw, expected, rtol=1e-10, atol=1e-12, equal_nan=True), "final_weight_replay_disagrees_with_estimator:" + task)
    calibrated = apply_calibration(calibration, raw, task)
    if calibration.get("status") in ("fitted", "constant_comparison_baseline"):
        transformed = calibration["intercept"] + calibration["slope"] * (
            logit(np.clip(expected, 1e-12, 1 - 1e-12)) if task == "direction" else expected)
        expected_calibrated = expit(transformed) if task == "direction" else transformed
    else:
        expected_calibrated = expected
    require(np.allclose(calibrated, expected_calibrated, rtol=1e-10, atol=1e-12, equal_nan=True),
            "final_calibration_replay_disagrees:" + task)
    chosen = calibrated if use_calibration else raw
    sample = rows[KEYS + ["feature_ready"]].copy()
    sample["raw"], sample["calibrated"], sample["chosen"] = raw, calibrated, chosen
    sample = sample.loc[sample.stock_id.isin(sample_stocks)]
    finite = np.isfinite(raw) & np.isfinite(expected)
    return {"status": "replayed_against_fitted_estimator_in_memory", "no_retraining": True,
        "rows": len(rows), "ready_rows": int(rows.feature_ready.sum()), "predicted_rows": int(np.isfinite(raw).sum()),
        "maximum_raw_absolute_delta": float(np.max(np.abs(raw[finite] - expected[finite]))) if finite.any() else None,
        "keys_sha256": frame_digest(rows, KEYS), "raw_sha256": digest(raw),
        "calibrated_sha256": digest(calibrated), "chosen_sha256": digest(chosen),
        "use_calibration": bool(use_calibration), "sample50_predictions": clean(sample.to_dict("records"))}


def evaluate_recipes(frame, folds, recipes, columns, *, threads=4, progress=log):
    parts = {r.name: [] for r in recipes}
    audits, first_seconds = [], None
    core = _core()
    for fold_position, fold in enumerate(folds):
        started = time.monotonic()
        evaluation = frame.loc[frame.session_date.isin(fold.dates)].copy()
        if fold.score_before is not None:
            evaluation["label_eligible"] &= evaluation.label_end < fold.score_before
        groups = {}
        for recipe in recipes:
            indices, audit = fit_indices(frame, fold.start, recipe.window)
            groups.setdefault(audit["fit_keys_sha256"], {"indices": indices, "recipes": []})["recipes"].append((recipe, audit))
        for fit_hash, group in groups.items():
            training = frame.loc[group["indices"]]
            transform_started = time.monotonic()
            transformer = core._transformer(training, columns) if len(training) else None
            transformer_seconds = time.monotonic() - transform_started
            predictions_cache = {}
            for family in ("linear", "hgb"):
                members = [(r, a) for r, a in group["recipes"] if r.family == family]
                if not members:
                    continue
                transform_started = time.monotonic()
                matrix = core._transform(training, transformer, native=family == "hgb") if transformer is not None else None
                matrix_seconds = time.monotonic() - transform_started
                for member_number, (recipe, audit) in enumerate(members):
                    head_started = time.monotonic()
                    cache_key = (recipe.task, recipe.family, recipe.c, recipe.alpha)
                    reused = cache_key in predictions_cache
                    if reused:
                        prediction, details = predictions_cache[cache_key]
                    else:
                        try:
                            fitted = fit_head(training, columns, recipe, transformer=transformer,
                                              matrix=matrix, threads=threads)
                            prediction = predict_head(fitted, evaluation, threads)
                            details = {"fit_status": "trained", "individual_signal_learned": fitted["head"]["individual_signal_learned"],
                                "effective_numeric_features": len(transformer["effective_feature_order"]),
                                "effective_missing_indicators": len(transformer["effective_indicator_order"]),
                                "n_informative_fit_rows": fitted["head"]["n_fit_rows"],
                                "head_effective_fit_days": fitted["head"].get("n_fit_signal_days", audit["effective_fit_days"]),
                                "head_effective_fit_keys_sha256": fitted["head"].get("effective_fit_row_ids_sha256", fit_hash)}
                            del fitted
                        except (ValueError, RuntimeError, FloatingPointError) as exc:
                            prediction = np.full(len(evaluation), np.nan)
                            details = {"fit_status": "failed", "error": type(exc).__name__ + ":" + str(exc),
                                       "individual_signal_learned": False, "head_effective_fit_days": 0,
                                       "n_informative_fit_rows": 0}
                        predictions_cache[cache_key] = (prediction, details)
                    prediction = prediction.copy()
                    prediction[~evaluation.feature_ready.to_numpy(dtype=bool)] = np.nan
                    out = evaluation[KEYS + ["row_id", "label_end", "target_return", "label_eligible", "feature_ready"]].copy()
                    out["evaluation_eligible"] = out.label_eligible & out.feature_ready
                    out["prediction_status"] = np.where(~out.feature_ready, "unavailable_price_warmup_lt60",
                        np.where(np.isfinite(prediction), "predicted", "unavailable_model_or_features"))
                    out["prediction"], out["phase"], out["fold"] = prediction, fold.phase, fold.number
                    parts[recipe.name].append(out)
                    head_seconds = 0.0 if reused else time.monotonic() - head_started
                    shared_seconds = (matrix_seconds + transformer_seconds) if member_number == 0 else 0.0
                    audits.append({"phase": fold.phase, "fold": fold.number, "recipe": recipe.name,
                        **audit, **details, "reused_same_fit_result": reused,
                        "selected_mature_ready_fit_days": audit["effective_fit_days"],
                        "effective_fit_days": details["head_effective_fit_days"],
                        "requested_window_fulfilled": details["head_effective_fit_days"] == recipe.window,
                        "uninformative_fit_rows": audit["fit_rows"] - details["n_informative_fit_rows"],
                        "recipe_seconds": head_seconds + shared_seconds,
                        "shared_transform_seconds": shared_seconds,
                        "timing_policy": "fit_predict_wall_seconds;shared_transform_charged_to_first_member;reused_head_zero",
                        "evaluation_denominator_rows": len(evaluation),
                        "evaluation_mature_label_rows": int(evaluation.label_eligible.sum()),
                        "evaluation_not_ready_rows": int((~evaluation.feature_ready).sum()),
                        "evaluation_eligible_rows": int((evaluation.label_eligible & evaluation.feature_ready).sum()),
                        "evaluation_keys_sha256": frame_digest(evaluation, KEYS)})
                del matrix
                transformer_seconds = 0.0
            del training, predictions_cache
        elapsed = time.monotonic() - started
        if first_seconds is None:
            first_seconds = elapsed
            progress("first_fold_eta", phase=fold.phase, first_fold_seconds=elapsed,
                     remaining_phase_seconds_estimate=elapsed * (len(folds) - 1),
                     note="same-stage estimate;dedup and HGB cost can differ")
        progress("fold_finished", phase=fold.phase, fold=fold.number, seconds=elapsed,
                 completed_folds=fold_position + 1, total_folds=len(folds))
    return {name: pd.concat(values, ignore_index=True) for name, values in parts.items()}, audits


def daily_metrics(predictions, task, evaluation_mask=None):
    frame = predictions.copy()
    finite = np.isfinite(frame.prediction) & np.isfinite(frame.target_return)
    require("feature_ready" in frame, "metric_feature_readiness_required")
    frame["scored"] = frame.label_eligible & frame.feature_ready & finite
    if evaluation_mask is not None:
        require(len(evaluation_mask) == len(frame), "metric_mask_length_mismatch")
        frame["scored"] &= np.asarray(evaluation_mask, dtype=bool)
    rows = []
    for day, part in frame.groupby("session_date", sort=True):
        scored = part.loc[part.scored]
        y, p = scored.target_return.to_numpy(float), scored.prediction.to_numpy(float)
        row = {"session_date": day, "n_denominator": len(part), "n_label_eligible": int(part.label_eligible.sum()),
               "n_feature_ready": int(part.feature_ready.sum()),
               "n_unscored_not_ready": int((~part.feature_ready).sum()),
               "n_predicted": int(np.isfinite(part.prediction).sum()), "n_evaluated": len(scored),
               "n_unscored": len(part) - len(scored)}
        if task == "direction":
            require(not len(p) or ((p >= 0) & (p <= 1)).all(), "probability_outside_unit_interval")
            binary = y > 0
            both = len(np.unique(binary)) == 2
            row.update(accuracy=float(np.mean((p >= .5) == binary)) if len(p) else np.nan,
                balanced_accuracy=(float(((p[binary] >= .5).mean() + (p[~binary] < .5).mean()) / 2) if both else np.nan),
                auc=float(roc_auc_score(binary, p)) if both else np.nan,
                brier=float(np.mean((p - binary) ** 2)) if len(p) else np.nan,
                naive_down_accuracy=float(np.mean(~binary)) if len(p) else np.nan)
        else:
            row.update(mae=float(np.mean(np.abs(p - y))) if len(p) else np.nan,
                       mae_zero=float(np.mean(np.abs(y))) if len(p) else np.nan)
        rows.append(row)
    return pd.DataFrame(rows)


def summarise_daily(daily):
    result = {"weighting": "equal_signal_date", "signal_days": len(daily)}
    for column in daily:
        if column == "session_date":
            continue
        values = pd.to_numeric(daily[column], errors="coerce")
        if column.startswith("n_"):
            result[column] = int(values.sum())
        else:
            result[column] = float(values.mean()) if values.notna().any() else None
            result[column + "_dates"] = int(values.notna().sum())
    return result


def selection_metrics(predictions, reference, task):
    require(np.array_equal(predictions.row_id.to_numpy(), reference.row_id.to_numpy()), "selection_row_keys_differ")
    reference_scored = reference.label_eligible & reference.feature_ready & np.isfinite(reference.prediction) & np.isfinite(reference.target_return)
    missing = reference_scored & ~np.isfinite(predictions.prediction)
    summary = summarise_daily(daily_metrics(predictions, task, reference_scored))
    summary.update(selection_reference_rows=int(reference_scored.sum()),
                   missing_on_reference_rows=int(missing.sum()),
                   selection_keys_sha256=frame_digest(reference.loc[reference_scored], KEYS))
    if missing.any():
        summary["selection_unavailable_reason"] = "candidate_missing_predictions_on_common_reference_keys"
    return summary


def choose_recipe(recipes, metrics, task):
    def key(recipe):
        item = metrics[recipe.name]
        require(recipe.task == task, "selection_task_mismatch")
        if item.get("selection_unavailable_reason"):
            return (float("inf"),) * 4
        if task == "direction":
            if item.get("balanced_accuracy") is None or item.get("brier") is None:
                return (float("inf"),) * 4
            return (-item["balanced_accuracy"], item["brier"], recipe.window, recipe.family != "linear")
        if item.get("mae") is None:
            return (float("inf"),) * 4
        return (item["mae"], recipe.window, recipe.family != "linear", recipe.name)
    require(bool(recipes), "no_recipes_to_select")
    winner = min(recipes, key=key)
    require(np.isfinite(key(winner)[0]), "development_selection_unavailable:" + task)
    return winner


def calibration_pool(predictions, decision):
    decision = pd.Timestamp(decision)
    eligible = (predictions.label_eligible & predictions.feature_ready & (predictions.session_date < decision)
        & (predictions.label_end < decision) & np.isfinite(predictions.prediction)
        & np.isfinite(predictions.target_return))
    pool = predictions.loc[eligible].copy()
    days = sorted(pool.session_date.unique())[-CALIBRATION_DAYS:]
    pool = pool.loc[pool.session_date.isin(days)]
    return pool, {"decision_date": decision.isoformat(), "mature_oof_days": len(days), "mature_oof_rows": len(pool),
        "maximum_oof_label_end": clean(pool.label_end.max()), "keys_sha256": frame_digest(pool, KEYS)}


def fit_calibration(pool, task):
    days = pool.session_date.nunique()
    if days < MIN_CALIBRATION_DAYS:
        return {"status": "unavailable", "reason": "insufficient_prior_mature_oof_days", "mature_oof_days": days}
    x, y = pool.prediction.to_numpy(float), pool.target_return.to_numpy(float)
    counts = pool.groupby("session_date").session_date.transform("size").to_numpy(float)
    weights = 1 / counts
    weights *= len(weights) / weights.sum()
    if task == "direction":
        if len(np.unique(y > 0)) < 2:
            return {"status": "unavailable", "reason": "oof_single_direction_class", "mature_oof_days": days}
        model = LogisticRegression(C=1.0, max_iter=1000, random_state=MODEL_SEED).fit(
            logit(np.clip(x, 1e-12, 1 - 1e-12)).reshape(-1, 1), y > 0, sample_weight=weights)
        slope, intercept = float(model.coef_[0, 0]), float(model.intercept_[0])
        method = "mature_oof_platt"
    else:
        # Unbounded estimated LAD slope/intercept: no cap, forced shrink, or clipping of returns.
        if np.ptp(y) == 0 or np.ptp(x) == 0:
            order = np.argsort(y, kind="stable")
            midpoint = np.searchsorted(np.cumsum(weights[order]), weights.sum() / 2)
            intercept, slope = float(y[order[midpoint]]), 0.0
        else:
            objective = lambda coefficients: np.average(np.abs(y - coefficients[0] - coefficients[1] * x), weights=weights)
            fitted = minimize(objective, np.array([np.median(y - x), 1.0]), method="Powell",
                options={"maxiter": 200, "xtol": 1e-8, "ftol": 1e-9})
            if not fitted.success or not np.isfinite(fitted.x).all():
                return {"status": "unavailable", "reason": "lad_optimizer_failed", "mature_oof_days": days}
            intercept, slope = map(float, fitted.x)
        method = "mature_oof_unbounded_lad"
    constant = bool(np.isclose(slope, 0.0, rtol=0, atol=1e-12))
    return {"status": "constant_comparison_baseline" if constant else "fitted", "method": method,
        "slope": slope, "intercept": intercept, "mature_oof_days": days, "mature_oof_rows": len(pool),
        "individual_signal_preserved": not constant, "weighting": "equal_signal_date",
        "not_a_return_cap": True}


def apply_calibration(calibration, values, task):
    values = np.asarray(values, dtype=float)
    if calibration.get("status") not in ("fitted", "constant_comparison_baseline"):
        return values.copy()
    z = logit(np.clip(values, 1e-12, 1 - 1e-12)) if task == "direction" else values
    transformed = calibration["intercept"] + calibration["slope"] * z
    return expit(transformed) if task == "direction" else transformed


def calibrated_predictions(predictions, history, task):
    result = predictions.copy()
    result["calibrated_flag"] = False
    result["constant_calibration_flag"] = False
    audits = []
    for fold, part in predictions.groupby("fold", sort=True):
        pool, audit = calibration_pool(history, part.session_date.min())
        calibration = fit_calibration(pool, task)
        idx = part.index
        result.loc[idx, "prediction"] = apply_calibration(calibration, part.prediction, task)
        result.loc[idx, "calibrated_flag"] = calibration.get("status") == "fitted"
        result.loc[idx, "constant_calibration_flag"] = calibration.get("status") == "constant_comparison_baseline"
        audits.append({"fold": int(fold), **audit, "calibration": calibration})
    return result, audits


def choose_calibration(task, raw, calibrated, calibrated_days, calibration=None):
    usable = calibrated_days > 0 and (calibration is None or calibration.get("status") == "fitted")
    if task == "direction":
        usable = usable and all(raw.get(k) is not None and calibrated.get(k) is not None for k in ("accuracy", "brier"))
        improve = usable and calibrated["accuracy"] >= raw["accuracy"] - 1e-12 and calibrated["brier"] <= raw["brier"] + 1e-12
    else:
        usable = usable and raw.get("mae") is not None and calibrated.get("mae") is not None
        improve = usable and calibrated["mae"] <= raw["mae"] + 1e-12
    return {"use_calibrated": bool(improve), "development_calibrated_signal_days": int(calibrated_days),
        "reason": "development_nonworsening_on_mature_oof" if improve else "raw_retained_no_eligible_nonworsening_development_calibration",
        "selection_data": "development_only", "not_an_adoption_gate": True}


def block_interval(values, block_days=63, repetitions=1000, seed=MODEL_SEED):
    require(block_days >= HORIZON, "bootstrap_block_shorter_than_H63")
    values = np.asarray(values, dtype=float)
    n = len(values)
    result = {"signal_days": n, "finite_metric_days": int(np.isfinite(values).sum()), "block_days": block_days,
        "effective_full_blocks": n // block_days, "effective_blocks_fractional": n / block_days,
        "repetitions": repetitions, "interval": None,
        "interpretation": "exploratory_date_blocks;overlapping_H63_labels;not_stock_row_iid"}
    if n < 2 * block_days or not np.isfinite(values).any():
        result["reason"] = "fewer_than_two_complete_date_blocks_or_no_metric"
        return result
    rng = np.random.default_rng(seed)
    samples = []
    for _ in range(repetitions):
        starts = rng.integers(0, n - block_days + 1, size=int(np.ceil(n / block_days)))
        sample = np.concatenate([values[i:i + block_days] for i in starts])[:n]
        if np.isfinite(sample).any():
            samples.append(float(np.nanmean(sample)))
    if samples:
        result["interval"] = np.quantile(samples, [.025, .975]).tolist()
    return result


def paired_summary(candidate, reference, task, repetitions=1000):
    require(np.array_equal(candidate.row_id.to_numpy(), reference.row_id.to_numpy()), "paired_row_keys_differ")
    common = (candidate.label_eligible & reference.label_eligible & candidate.feature_ready & reference.feature_ready & np.isfinite(candidate.target_return)
              & np.isfinite(candidate.prediction) & np.isfinite(reference.prediction))
    cand_daily = daily_metrics(candidate, task, common)
    ref_daily = daily_metrics(reference, task, common)
    require(cand_daily.session_date.equals(ref_daily.session_date), "paired_dates_differ")
    metrics = ("accuracy", "balanced_accuracy", "auc", "brier") if task == "direction" else ("mae",)
    intervals = {name: {"candidate_minus_reference": clean((cand_daily[name] - ref_daily[name]).mean()),
        **block_interval((cand_daily[name] - ref_daily[name]).to_numpy(), repetitions=repetitions)} for name in metrics}
    return {"n_full_denominator": len(candidate), "n_common_evaluated": int(common.sum()),
        "common_keys_sha256": frame_digest(candidate.loc[common], KEYS), "metrics": intervals}


def presentation_sample(frame, seed=None):
    seed = secrets.randbits(32) if seed is None else int(seed)
    stocks = sorted(frame.stock_id.unique())
    chosen = sorted(np.random.default_rng(seed).choice(stocks, min(50, len(stocks)), replace=False).tolist())
    return {"seed": seed, "stock_ids": chosen, "population_stocks": len(stocks),
            "purpose": "presentation_only", "training_population": "all_adapter_qualified_ordinary_stocks"}


def reserve_output(root=OUTPUT_ROOT, *, attempt=None):
    root = Path(root).absolute()
    require(root == Path(OUTPUT_ROOT).absolute(), "output_must_use_fixed_candidate_directory")
    if attempt is not None:
        require(isinstance(attempt, str) and re.fullmatch(r"attempt_[0-9]{2}_[a-z0-9_]{1,40}", attempt), "attempt_name_invalid")
        require(root.is_dir() and (root / "performance_failure.json").is_file(), "attempt_requires_preserved_performance_receipt")
        with (root / "run_started.json").open(encoding="utf-8") as handle:
            require(json.load(handle).get("schema") == SCHEMA, "attempt_parent_not_owned_research_root")
        root = root / attempt
    for part in [root, *root.parents]:
        if part.exists() or part.is_symlink():
            attrs = getattr(part.lstat(), "st_file_attributes", 0)
            require(not part.is_symlink() and not (attrs & 0x400), "output_link_or_reparse_point_refused")
    require(not root.exists() or root.is_dir(), "output_is_not_directory")
    require(not root.exists() or not any(root.iterdir()), "output_nonempty_refused_no_overwrite")
    root.mkdir(parents=True, exist_ok=True)
    # Exclusive claim also prevents two fresh runners from accepting the same empty directory.
    with (root / "run_started.json").open("x", encoding="utf-8", newline="\n") as handle:
        json.dump({"schema": SCHEMA, "started_at": datetime.now(timezone.utc).isoformat(),
                   "pid": os.getpid(), "research_only": True, "promotion": False}, handle)
    return root


def write_json(root, name, value):
    require(Path(name).name == name, "output_filename_invalid")
    with (root / name).open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(clean(value), handle, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2)
        handle.write("\n")


def _stage_metrics(results, reference, task):
    return {name: selection_metrics(pred, reference, task) for name, pred in results.items()}


def _presentation_predictions(sample, selected, baseline, calibrated):
    output = None
    for task in ("direction", "return"):
        chosen = selected[task]
        part = chosen.loc[chosen.stock_id.isin(sample["stock_ids"]),
            KEYS + ["row_id", "phase", "fold", "label_end", "target_return", "label_eligible", "feature_ready", "evaluation_eligible"]].copy()
        selected_index = chosen.stock_id.isin(sample["stock_ids"])
        part[task + "_raw"] = chosen.loc[selected_index, "prediction"].to_numpy()
        part[task + "_prediction_status"] = chosen.loc[selected_index, "prediction_status"].to_numpy()
        part[task + "_calibrated"] = calibrated[task].loc[selected_index, "prediction"].to_numpy()
        part[task + "_baseline_80"] = baseline[task].loc[selected_index, "prediction"].to_numpy()
        part[task + "_calibration_available"] = calibrated[task].loc[selected_index, "calibrated_flag"].to_numpy()
        part[task + "_constant_calibration_baseline"] = calibrated[task].loc[selected_index, "constant_calibration_flag"].to_numpy()
        if output is None:
            output = part
        else:
            extras = [c for c in part if c not in output]
            output = output.merge(part[["row_id", "phase"] + extras], on=["row_id", "phase"], how="left", validate="one_to_one")
    return output


def _report(result):
    def number(value, percent=False):
        if value is None or not np.isfinite(value):
            return "無可評資料"
        return f"{100 * value:.2f}%" if percent else f"{value:.4f}"

    def points(value):
        return "無可評資料" if value is None else f"{100 * value:.3f}"

    names = {"baseline": "80日滾動基準", "raw": "選定模型未校正", "calibrated": "選定模型校正比較"}
    lines = ["# H63 個股隔離模型研究", "", "狀態：研究完成，未正式採用。", "",
        "本結果是現行版本資料的回溯比較，不是新前瞻樣本外成績；原2026年研究窗口已曝光。",
        "方向與報酬模型分別只依開發期選擇。外層只報告，不回饋配方、訓練期間或校正選擇。",
        "所有合格普通股均保留；隨機50股僅供呈現，不替代完整母體訓練或指標。", "", "## 選定配方"]
    for task, label in (("direction", "方向"), ("return", "報酬")):
        recipe = result["selection"][task]
        family = "擴充線性模型" if recipe["family"] == "linear" else "直方圖梯度提升模型"
        calibration = "採用" if result["calibration_selection"][task]["use_calibrated"] else "保留未校正結果"
        lines.append(f"- {label}：{family}，要求{recipe['window']}個成熟訓練訊號日；校正：{calibration}。")
    lines += ["", "## 資料期間消融", "",
        "以下均為相同外層日期、相同股票列鍵及相同線性參數的回溯比較；不足要求日數時明列實際天數。",
        "| 要求訓練日數 | 實際方向／報酬訓練日數範圍 | 普通準確率 | 報酬MAE（百分點） | 訓練與推論耗時（秒） |",
        "|---:|---|---:|---:|---:|"]
    for window in (80, 252, 504, 756):
        ranges, elapsed = [], 0.0
        for task in ("direction", "return"):
            rows = [a for a in result["requested_effective_fit_days"][f"{task}_linear_w{window}"] if a["phase"] == "outer"]
            days = [a["effective_fit_days"] for a in rows]
            ranges.append("無資料" if not days else f"{min(days)}至{max(days)}")
            elapsed += sum(a["recipe_seconds"] for a in rows)
        direction = result["all_recipe_outer_metrics"][f"direction_linear_w{window}"]
        returns = result["all_recipe_outer_metrics"][f"return_linear_w{window}"]
        lines.append(f"| {window} | {'／'.join(ranges)} | {number(direction.get('accuracy'), True)} | {points(returns.get('mae'))} | {elapsed:.2f} |")
    lines += ["", "同一實際訓練集合會重用模型；重用部分不重複計時，共享轉換耗時計入第一個使用者。此表不含共用來源組裝時間。",
        "資料越長不必然越好；這些外層數字只供描述，不用它們重新選模型，也不構成長期間必要性的前瞻證據。", "",
        "## 方向外層結果", "",
        "| 輸出 | 普通準確率 | 平衡準確率 | AUC（辨識曲線面積） | Brier（機率均方誤差） | 可評列／原分母 |",
        "|---|---:|---:|---:|---:|---:|"]
    for variant, label in names.items():
        item = result["outer"]["direction"][variant]
        lines.append(f"| {label} | {number(item.get('accuracy'), True)} | {number(item.get('balanced_accuracy'), True)} | {number(item.get('auc'))} | {number(item.get('brier'))} | {item['n_evaluated']}/{item['n_denominator']} |")
    lines += ["", "## 報酬外層結果", "",
        "| 輸出 | MAE（平均絕對誤差，報酬百分點） | 猜零比較MAE（報酬百分點） | 可評列／原分母 |",
        "|---|---:|---:|---:|"]
    for variant, label in names.items():
        item = result["outer"]["return"][variant]
        lines.append(f"| {label} | {points(item.get('mae'))} | {points(item.get('mae_zero'))} | {item['n_evaluated']}/{item['n_denominator']} |")
    lines += ["", "普通準確率與平衡準確率分開呈現；各指標先按日期計算再等權平均。單一類別日無法計算的指標不捏造。",
        "不足60筆實際價格的列仍保留原分母，標記暖機不足、不可預測且不計分；所有模型使用相同可評資格。",
        "校正比較完整保留未校正數值及係數；不可校正時沿用未校正值。斜率退化為零者只列無個股辨識力比較基準，不套為候選模型。",
        "猜零僅是誤差比較基準，不冒充預測；不設固定報酬上限，也不強迫收縮至零。",
        "區間以63個訊號日為區塊；126日外層僅約兩個完整區塊，探索性區間不能視為大量獨立股票樣本的顯著證據。", "",
        "## 期間與封存限制", "",
        "252／504／756是成熟訓練訊號日數，不是單次推論需要讀取的歷史長度。本輪不修改單次推論特徵回看期間。",
        "校正另使用選定配方的前置時間外預測暖機，最多126日；每次只納入當時已成熟的標籤，不借外層成績挑校正。",
        "最終權重以開發期選定規格及至今成熟資料重新擬合。表列指標屬滾動配方回溯結果，新權重尚無前瞻成績。",
        "H1正式主要來源為盤前第二版；本工具不以個股備援模型冒充它。沒有正式採用、中央寫入或外部下載。",
        "完整來源雜湊、實際訓練日數、切分排除分母與去重證據保留於既有JSON摘要，不另增大型附錄。"]
    return "\n".join(lines) + "\n"


def run_research(*, horizons=(63,), threads=4, builder=None, sample_seed=None,
                 bootstrap_repetitions=1000, progress=log, attempt=None, expected_head_sha256=None):
    require(tuple(horizons) == (63,), "H63_only_H1_primary_requires_separate_design")
    require(type(threads) is int and 1 <= threads <= 4, "threads_must_be_between_1_and_4")
    require(type(bootstrap_repetitions) is int and bootstrap_repetitions >= 20, "bootstrap_repetitions_too_small")
    require(expected_head_sha256 is None or re.fullmatch(r"[0-9a-f]{64}", expected_head_sha256), "expected_head_sha256_invalid")
    root = reserve_output(OUTPUT_ROOT, attempt=attempt)
    started = time.monotonic()
    try:
        if builder is None:
            from stock_model_research_data import build_research_inputs
            builder = build_research_inputs
        with threadpool_limits(limits=threads):
            algorithm_sources = capture_algorithm_sources()
            write_json(root, "algorithm_sources_start.json", {"schema": SCHEMA,
                "versions": versions(), "sources": algorithm_sources, "expected_head_sha256": expected_head_sha256})
            build_arguments = dict(horizons=(63,), threads=threads, progress=adapter_progress)
            if expected_head_sha256 is not None:
                build_arguments["expected_head_sha256"] = expected_head_sha256
            raw_inputs = builder(**build_arguments)
            frame, metadata = prepare_inputs(raw_inputs)
            metadata["algorithm_sources_at_start"] = algorithm_sources
            metadata["versions"] = versions()
            del raw_inputs
            sample = presentation_sample(frame, sample_seed)
            write_json(root, "source_summary.json", metadata)
            folds, split_summary = plan_folds(frame)
            development_folds = [f for f in folds if f.phase == "development"]
            outer_folds = [f for f in folds if f.phase == "outer"]
            columns = metadata["model_feature_order"]
            recipes = window_recipes()
            development, audits = evaluate_recipes(frame, development_folds, recipes, columns, threads=threads, progress=progress)
            baseline = {task: development[f"{task}_linear_w80"] for task in ("direction", "return")}
            metrics = {}
            window_selection, selected = {}, {}
            for task in ("direction", "return"):
                task_recipes = [r for r in recipes if r.task == task]
                metrics.update(_stage_metrics({r.name: development[r.name] for r in task_recipes}, baseline[task], task))
                for recipe in task_recipes:
                    relevant = [a for a in audits if a["recipe"] == recipe.name]
                    if relevant and not any(a.get("individual_signal_learned") for a in relevant):
                        metrics[recipe.name]["selection_unavailable_reason"] = "constant_comparison_baseline_no_individual_signal"
                window_selection[task] = choose_recipe([r for r in task_recipes if r.window != 80], metrics, task)
            extra = []
            for task in ("direction", "return"):
                window = window_selection[task].window
                extra.extend([Recipe(f"{task}_strong_w{window}", task, window, "linear", .1, 100.),
                              Recipe(f"{task}_hgb_w{window}", task, window, "hgb")])
            more, more_audits = evaluate_recipes(frame, development_folds, extra, columns, threads=threads, progress=progress)
            development.update(more)
            audits.extend(more_audits)
            for task in ("direction", "return"):
                contenders = [Recipe(f"{task}_linear_w80", task, 80), window_selection[task]] + [r for r in extra if r.task == task]
                metrics.update(_stage_metrics({r.name: development[r.name] for r in contenders}, baseline[task], task))
                # Constant fitted heads remain reported, but cannot impersonate individual predictions.
                for recipe in contenders:
                    relevant = [a for a in audits if a["recipe"] == recipe.name]
                    if relevant and not any(a.get("individual_signal_learned") for a in relevant):
                        metrics[recipe.name]["selection_unavailable_reason"] = "constant_comparison_baseline_no_individual_signal"
                selected[task] = choose_recipe(contenders, metrics, task)
            warmup_folds, warmup_summary = plan_oof_warmup(frame, split_summary["development_start"],
                                                          split_summary["outer_start"])
            if warmup_folds:
                warmup, warmup_audits = evaluate_recipes(frame, warmup_folds, list(selected.values()),
                    columns, threads=threads, progress=progress)
            else:
                warmup = {recipe.name: development[recipe.name].iloc[:0].copy() for recipe in selected.values()}
                warmup_audits = []
            audits.extend(warmup_audits)
            split_summary["oof_warmup"] = warmup_summary
            warmup_summary["by_task"] = {}
            calibration_histories = {}
            for task, recipe in selected.items():
                before = warmup[recipe.name]
                calibration_histories[task] = pd.concat([before, development[recipe.name]], ignore_index=True)
                warmup_summary["by_task"][task] = {"recipe": asdict(recipe),
                    "prediction_rows_in_memory": len(before),
                    "finite_predictions": int(np.isfinite(before.prediction).sum()),
                    "keys_sha256": frame_digest(before, KEYS),
                    "daily_metrics": summarise_daily(daily_metrics(before, task)) if len(before) else None}
            calibration_selection, dev_calibrated, calibration_audits = {}, {}, {}
            for task in ("direction", "return"):
                raw = development[selected[task].name]
                calibrated, cal_audit = calibrated_predictions(raw, calibration_histories[task], task)
                dev_calibrated[task], calibration_audits["development_" + task] = calibrated, cal_audit
                available = calibrated.calibrated_flag.to_numpy()
                comparable_raw = summarise_daily(daily_metrics(raw, task, available))
                comparable_cal = summarise_daily(daily_metrics(calibrated, task, available))
                calibration_selection[task] = choose_calibration(task, comparable_raw, comparable_cal,
                    calibrated.loc[calibrated.calibrated_flag, "session_date"].nunique())
                calibration_selection[task].update(raw_on_calibrated_dates=comparable_raw,
                    calibrated_on_calibrated_dates=comparable_cal,
                    full_development_raw=summarise_daily(daily_metrics(raw, task)),
                    full_development_calibrated=summarise_daily(daily_metrics(calibrated, task)))
            locked_selection = {"recipes": {k: asdict(v) for k, v in selected.items()},
                "window_selection": {k: asdict(v) for k, v in window_selection.items()},
                "calibration": calibration_selection, "development_metrics": metrics,
                "outer_not_read_for_selection": True}
            selection_sha = digest(locked_selection)
            progress("development_selection_locked", sha256=selection_sha, recipes=locked_selection["recipes"])
            # Every compared recipe receives the identical report-only outer dates.
            # No outer metric is supplied to choose_recipe or choose_calibration.
            outer_recipes = {r.name: r for r in [*recipes, *extra]}
            outer, outer_audits = evaluate_recipes(frame, outer_folds, list(outer_recipes.values()), columns, threads=threads, progress=progress)
            audits.extend(outer_audits)
            require(digest(locked_selection) == selection_sha, "selection_changed_during_outer")
            result = {"schema": SCHEMA, "research_only": True, "promotion": False,
                "vintage": "current-vintage retrospective", "selection": locked_selection["recipes"],
                "window_selection": locked_selection["window_selection"], "selection_sha256": selection_sha,
                "selection_rules": {"direction": "date_equal_balanced_accuracy_desc;brier_asc;shorter_window",
                    "return": "date_equal_mae_asc;shorter_window", "outer": "report_only"},
                "development_metrics": metrics, "calibration_selection": calibration_selection,
                "sample50": sample, "outer": {}, "paired_outer": {}, "per_market_outer": {},
                "all_recipe_outer_metrics": {}, "requested_effective_fit_days": {},
                "oof_warmup_summary": warmup_summary}
            for recipe in [*recipes, *extra]:
                result["all_recipe_outer_metrics"][recipe.name] = summarise_daily(daily_metrics(outer[recipe.name], recipe.task))
                result["requested_effective_fit_days"][recipe.name] = [{
                    key: audit[key] for key in ("phase", "fold", "requested_fit_days", "effective_fit_days",
                        "requested_window_fulfilled", "fit_rows", "fit_keys_sha256", "reused_same_fit_result", "recipe_seconds")}
                    for audit in audits if audit["recipe"] == recipe.name]
            daily_outputs, selected_predictions, baseline_predictions, calibrated_outputs = [], {}, {}, {}
            final_calibrations = {}
            final_decision = frame.session_date.max() + pd.Timedelta(days=1)
            for task in ("direction", "return"):
                raw = outer[selected[task].name]
                ref = outer[f"{task}_linear_w80"]
                dev = development[selected[task].name]
                cal, audit = calibrated_predictions(raw, calibration_histories[task], task)
                calibration_audits["outer_" + task] = audit
                result["outer"][task] = {}
                for variant, predictions in (("baseline", ref), ("raw", raw), ("calibrated", cal)):
                    daily = daily_metrics(predictions, task)
                    result["outer"][task][variant] = summarise_daily(daily)
                    daily["task"], daily["variant"], daily["phase"] = task, variant, "outer"
                    daily_outputs.append(daily)
                    result["per_market_outer"][task + "_" + variant] = {
                        m: summarise_daily(daily_metrics(g, task)) for m, g in predictions.groupby("market")}
                result["paired_outer"][task] = {"raw_vs_rolling_80": paired_summary(raw, ref, task, bootstrap_repetitions),
                    "calibrated_vs_raw": paired_summary(cal, raw, task, bootstrap_repetitions)}
                selected_predictions[task] = pd.concat([dev, raw], ignore_index=True)
                baseline_predictions[task] = pd.concat([baseline[task], ref], ignore_index=True)
                calibrated_outputs[task] = pd.concat([dev_calibrated[task], cal], ignore_index=True)
                for recipe in [r for r in [*recipes, *extra] if r.task == task]:
                    daily = daily_metrics(development[recipe.name], task)
                    daily["task"], daily["variant"], daily["phase"] = task, recipe.name, "development"
                    daily_outputs.append(daily)
                    daily = daily_metrics(outer[recipe.name], task)
                    daily["task"], daily["variant"], daily["phase"] = task, recipe.name, "outer"
                    daily_outputs.append(daily)
                final_history = pd.concat([warmup[selected[task].name], selected_predictions[task]], ignore_index=True)
                pool, final_audit = calibration_pool(final_history, final_decision)
                final_calibrations[task] = fit_calibration(pool, task)
                calibration_audits["final_" + task] = {**final_audit, "calibration": final_calibrations[task]}
            # These weights are new; none of the retrospective scores above belong to them prospectively.
            artifact = {"schema": SCHEMA + "-candidate-model", "research_only": True, "promotion": False,
                "deliverable": False, "performance_scope": "new_final_weights_have_no_prospective_results",
                "reader": "stock_model_research.predict_saved_head;not_the_formal_selected_model_loader",
                "source_binding_sha256": metadata["binding_sha256"], "matrix_sha256": metadata["matrix_and_label_sha256"],
                "selection_sha256": selection_sha, "asof_by_market": metadata["asof_by_market"],
                "feature_order": columns, "heads": {}, "versions": versions(),
                "algorithm_sources": algorithm_sources, "final_decision_date": final_decision.isoformat()}
            final_audits = {}
            latest_rows = frame.loc[frame.session_date.eq(frame.market.map(
                {market: pd.Timestamp(day) for market, day in metadata["asof_by_market"].items()}))]
            for task, recipe in selected.items():
                indices, audit = fit_indices(frame, final_decision, recipe.window)
                fitted = fit_head(frame.loc[indices], columns, recipe, threads=threads)
                require(fitted["head"]["individual_signal_learned"], "final_head_constant_comparison_only:" + task)
                audit = {**audit, "selected_mature_ready_fit_days": audit["effective_fit_days"],
                    "effective_fit_days": fitted["head"].get("n_fit_signal_days", audit["effective_fit_days"]),
                    "effective_fit_rows": fitted["head"]["n_fit_rows"],
                    "head_effective_fit_keys_sha256": fitted["head"].get("effective_fit_row_ids_sha256", audit["fit_keys_sha256"])}
                audit["requested_window_fulfilled"] = audit["effective_fit_days"] == recipe.window
                calibration = final_calibrations[task]
                use_calibration = calibration_selection[task]["use_calibrated"] and calibration.get("status") == "fitted"
                artifact["heads"][task] = {"recipe": asdict(recipe), "head": fitted["head"],
                    "transformer": fitted["transformer"], "output_calibration": calibration if use_calibration else None,
                    "raw_preserved": True, "final_fit": audit,
                    "replay_check": candidate_replay_check(fitted, latest_rows, calibration,
                        use_calibration, sample["stock_ids"], threads)}
                final_audits[task] = audit
                del fitted
            verified_sources = verify_algorithm_sources(algorithm_sources)
            artifact["algorithm_sources_verified_before_output"] = True
            artifact["integrity_sha256"] = digest(artifact)
            result["final_refit"] = {"fit_audits": final_audits,
                "prospective_results": None, "calibrations": final_calibrations,
                "final_decision_date": final_decision.isoformat()}
            result["algorithm_sources"] = verified_sources
            result["versions"] = versions()
            result["elapsed_seconds"] = time.monotonic() - started
            write_json(root, "candidate_model.json", artifact)
            write_json(root, "split_summary.json", {**split_summary, "matrix_sha256": metadata["matrix_and_label_sha256"],
                "fit_audits": audits, "calibration_audits": calibration_audits,
                "final_fit_audits": final_audits, "row_ledger_saved": False})
            write_json(root, "metrics_summary.json", result)
            with (root / "daily_comparison.csv").open("x", encoding="utf-8", newline="") as handle:
                pd.concat(daily_outputs, ignore_index=True).to_csv(handle, index=False, lineterminator="\n")
            sample_predictions = _presentation_predictions(sample, selected_predictions, baseline_predictions, calibrated_outputs)
            with gzip.open(root / "sample50_predictions.csv.gz", "xt", encoding="utf-8", newline="") as handle:
                sample_predictions.to_csv(handle, index=False, lineterminator="\n")
            with (root / "RESULTS.md").open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(_report(result))
            write_json(root, "run_manifest.json", {"schema": SCHEMA, "status": "completed_research_not_promoted",
                "research_only": True, "promotion": False, "threads": threads,
                "source_binding_sha256": metadata["binding_sha256"], "selection_sha256": selection_sha,
                "artifact_sha256": artifact["integrity_sha256"], "elapsed_seconds": result["elapsed_seconds"],
                "artifact_file_sha256": file_sha256(root / "candidate_model.json"),
                "algorithm_sources": verified_sources, "algorithm_sources_verified_before_output": True,
                "versions": versions(), "final_decision_date": final_decision.isoformat(),
                "outputs": sorted(p.name for p in root.iterdir()) + ["run_manifest.json"]})
            progress("research_finished", seconds=result["elapsed_seconds"], output_dir=str(root), promotion=False)
            return result
    except Exception as exc:
        write_json(root, "run_failure.json", {"schema": SCHEMA, "status": "blocked_or_failed_not_promoted",
            "error": type(exc).__name__ + ":" + str(exc), "research_only": True, "promotion": False,
            "elapsed_seconds": time.monotonic() - started, "partial_outputs_retained": True,
            "split_summary": locals().get("split_summary"), "fit_audits": locals().get("audits", []),
            "development_metrics": locals().get("metrics", {}), "partial_result": locals().get("result")})
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description="H63 個股隔離訓練與歷史長度消融；不採用正式模型", allow_abbrev=False)
    parser.add_argument("--threads", type=int, choices=range(1, 5), default=4)
    parser.add_argument("--sample-seed", type=int, default=None,
                        help="僅控制呈現50股；省略時產生並記錄新隨機seed，不影響全股訓練")
    parser.add_argument("--bootstrap-repetitions", type=int, default=1000)
    parser.add_argument("--attempt", default=None, help="在固定根目錄保留效能失敗紀錄後建立新的明確嘗試子目錄")
    parser.add_argument("--expected-head-sha256", default=None, help="必須等於啟動時現行中央head，不接受舊head回退")
    args = parser.parse_args(argv)
    try:
        run_research(threads=args.threads, sample_seed=args.sample_seed,
                     bootstrap_repetitions=args.bootstrap_repetitions, attempt=args.attempt,
                     expected_head_sha256=args.expected_head_sha256)
        return 0
    except (ValueError, OSError, ImportError, RuntimeError) as exc:
        print("研究受阻，未採用正式模型：" + type(exc).__name__ + ": " + str(exc), file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
