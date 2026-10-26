"""Small C0 close-to-close baseline over caller-verified market-session panels.

This module neither reads market data nor applies a live model.  Historical
evidence is recorded independently from numerical fitting and never inferred
from a successful fit.  Artifacts contain JSON coefficients, not pickle code.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

MODEL_SCHEMA = "tw-stock-daily-linear-c0-v1"
FEATURE_SCHEMA = "tw-stock-c0-price-seven-v1"
FEATURE_ORDER = ("return_5", "return_20", "return_60", "return_126",
                 "return_252", "volatility_20", "volatility_60")
SUPPORTED_HORIZONS = (1, 5, 20, 60, 63)
FLAT_TOLERANCE = 1e-12
TARGET_DEFINITION = "signal_close_to_Hth_market_session_close_return"
_KEYS = ["stock_id", "market", "session_date"]


class DailyLinearError(ValueError):
    """An input, evidence, or model binding does not satisfy this API."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise DailyLinearError(reason)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _day(value: Any) -> pd.Timestamp:
    _require(isinstance(value, str) and len(value) == 10,
             "date_must_be_iso_calendar_day")
    try:
        result = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise DailyLinearError("date_must_be_iso_calendar_day") from exc
    _require(not pd.isna(result) and result.tz is None and
             result.strftime("%Y-%m-%d") == value, "date_must_be_iso_calendar_day")
    return result


def _frame_hash(frame: pd.DataFrame) -> str:
    # Runtime versions are recorded; this is exact supplied table identity,
    # rather than proof of publication or point-in-time qualification.
    hashes = pd.util.hash_pandas_object(frame, index=False).to_numpy(dtype="uint64")
    return hashlib.sha256(_json_bytes(list(frame.columns)) + hashes.tobytes()).hexdigest()


def _policy(binding: Mapping[str, Any]) -> dict:
    _require(isinstance(binding, Mapping), "policy_binding_missing")
    result = copy.deepcopy(dict(binding))
    _require(result.get("profile_id") == "C0", "model_profile_must_be_C0")
    for name in ("requirements_version", "feature_version", "policy_sha256",
                 "requirements_raw_sha256"):
        _require(isinstance(result.get(name), str) and bool(result[name]),
                 "policy_binding_missing_" + name)
    for name in ("policy_sha256", "requirements_raw_sha256"):
        _require(len(result[name]) == 64 and all(c in "0123456789abcdef" for c in result[name]),
                 "policy_binding_invalid_" + name)
    _json_bytes(result)
    return result


def _policy_identity(binding: Mapping[str, Any]) -> dict:
    # Caller names and path hints do not change a shared model's feature policy.
    # Preserve the complete binding separately, including its own exact hash.
    return {name: binding[name] for name in ("profile_id", "requirements_version",
        "feature_version", "policy_sha256", "requirements_raw_sha256")}


def _evidence(evidence: Mapping[str, Any], pairs: set, markets: set) -> dict:
    _require(isinstance(evidence, Mapping), "caller_evidence_missing")
    result = copy.deepcopy(dict(evidence))
    for name in ("current_ordinary_verified", "price_scope_verified", "snapshot_verified"):
        _require(result.get(name) is True, "caller_evidence_unverified_" + name)
    _require(result.get("known_future_information") is False,
             "known_future_information_or_unknown_leakage_gate")
    calendars = result.get("calendar_verified_by_market")
    _require(isinstance(calendars, Mapping) and all(calendars.get(m) is True for m in markets),
             "market_calendar_applicability_unverified")
    eligible = result.get("eligible_stock_market_pairs")
    _require(isinstance(eligible, (list, tuple)) and
             all(isinstance(x, (list, tuple)) and len(x) == 2 for x in eligible),
             "ordinary_share_scope_missing")
    _require(pairs.issubset({(str(x[0]), str(x[1])) for x in eligible}),
             "stock_outside_verified_ordinary_share_scope")
    for name in ("historical_membership_status", "pit_status", "corporate_actions_status"):
        _require(result.get(name) in ("verified", "unknown"),
                 "known_invalid_or_missing_" + name)
    _require(isinstance(result.get("source_binding"), Mapping) and bool(result["source_binding"]),
             "source_snapshot_binding_missing")
    _json_bytes(result)
    return result


def build_panel(panel: pd.DataFrame, market_sessions: Mapping[str, Sequence[str]], *,
                asof: str, horizons: Sequence[int], evidence: Mapping[str, Any],
                policy_binding: Mapping[str, Any]) -> dict:
    """Build fixed features and long-form labels without filling missing prices.

    Input columns: stock_id (string), market (TWSE/TPEX), session_date (ISO day),
    adjusted_close (positive comparable close or missing).  An explicit official
    calendar is required for every input market; it may include future sessions.
    Dates after asof are never accepted as input prices.  A usable feature row
    requires all 253 closes in its exact trailing market-session window.
    """
    _require(isinstance(panel, pd.DataFrame) and not panel.empty, "price_panel_empty")
    _require(set(_KEYS + ["adjusted_close"]).issubset(panel.columns), "price_panel_columns_missing")
    asof_day = _day(asof)
    hs = tuple(sorted(set(horizons)))
    _require(bool(hs) and all(type(h) is int and h in SUPPORTED_HORIZONS for h in hs),
             "unsupported_horizon")
    frame = panel[_KEYS + ["adjusted_close"]].copy()
    _require(frame["stock_id"].map(lambda x: isinstance(x, str) and bool(x)).all(),
             "stock_id_must_be_nonempty_string")
    _require(frame["market"].isin(["TWSE", "TPEX"]).all(), "ordinary_market_invalid")
    frame["session_date"] = frame["session_date"].map(_day)
    _require((frame["session_date"] <= asof_day).all(), "input_price_after_asof")
    _require(not frame.duplicated(_KEYS).any(), "duplicate_stock_market_session")
    try:
        frame["adjusted_close"] = pd.to_numeric(frame["adjusted_close"], errors="raise")
    except (ValueError, TypeError) as exc:
        raise DailyLinearError("adjusted_close_not_numeric") from exc
    known = frame["adjusted_close"].notna()
    _require((np.isfinite(frame.loc[known, "adjusted_close"]) &
              (frame.loc[known, "adjusted_close"] > 0)).all(), "adjusted_close_invalid")
    frame = frame.sort_values(_KEYS).reset_index(drop=True)
    pairs = set(frame[["stock_id", "market"]].itertuples(index=False, name=None))
    markets = set(frame["market"])
    verified = _evidence(evidence, pairs, markets)
    binding = _policy(policy_binding)
    axes = {}
    for market in sorted(markets):
        _require(market in market_sessions, "market_calendar_missing_" + market)
        axis = pd.DatetimeIndex([_day(d) for d in market_sessions[market]])
        _require(len(axis) > 0 and axis.is_unique and axis.is_monotonic_increasing,
                 "market_calendar_must_be_unique_increasing_" + market)
        _require(asof_day in axis, "asof_not_declared_market_session_" + market)
        axes[market] = axis
    feature_frames, label_frames = [], []
    for (stock_id, market), group in frame.groupby(["stock_id", "market"], sort=True):
        axis = axes[market]
        _require(group["session_date"].isin(axis).all(), "price_outside_declared_market_calendar")
        history_axis = axis[axis <= asof_day]
        close = group.set_index("session_date")["adjusted_close"].reindex(history_axis)
        features = pd.DataFrame(index=history_axis)
        for n in (5, 20, 60, 126, 252):
            features["return_" + str(n)] = close / close.shift(n) - 1.0
        daily = close / close.shift(1) - 1.0
        for n in (20, 60):
            features["volatility_" + str(n)] = daily.rolling(n, min_periods=n).std(ddof=1)
        features["priced_observations"] = close.notna().cumsum().astype(int)
        features["priced_window_observations"] = close.rolling(253, min_periods=1).count().astype(int)
        features["feature_ready"] = ((features["priced_window_observations"] == 253) &
                                     np.isfinite(features[list(FEATURE_ORDER)]).all(axis=1))
        features["stock_id"], features["market"] = stock_id, market
        features["session_date"] = history_axis
        # Every declared market day is retained after this stock's first row,
        # including suspension/asof missing-close rows with unavailable features.
        features = features.loc[features.index >= group["session_date"].min()].reset_index(drop=True)
        feature_frames.append(features)
        base_positions = axis.get_indexer(features["session_date"])
        signal_close = close.reindex(pd.DatetimeIndex(features["session_date"])).to_numpy()
        for h in hs:
            positions = base_positions + h
            endpoints = pd.Series(pd.NaT, index=features.index, dtype="datetime64[ns]")
            available = positions < len(axis)
            endpoints.loc[available] = axis[positions[available]]
            endpoint_close = close.reindex(pd.DatetimeIndex(endpoints)).to_numpy()
            observed = (endpoints.notna().to_numpy() & (endpoints <= asof_day).to_numpy() &
                        np.isfinite(endpoint_close) & np.isfinite(signal_close))
            target = np.where(observed, endpoint_close / signal_close - 1.0, np.nan)
            labels = features[_KEYS].copy()
            labels["horizon"] = h
            labels["label_end"] = endpoints
            labels["target_return"] = target
            labels["label_state"] = np.where(observed, "observed",
                np.where(endpoints.isna(), "target_calendar_unavailable",
                    np.where(endpoints > asof_day, "not_mature", "unobserved_missing_close")))
            label_frames.append(labels)
    features = pd.concat(feature_frames, ignore_index=True).sort_values(_KEYS).reset_index(drop=True)
    labels = pd.concat(label_frames, ignore_index=True).sort_values(_KEYS + ["horizon"]).reset_index(drop=True)
    for table in (features, labels):
        table["session_date"] = table["session_date"].dt.strftime("%Y-%m-%d")
    labels["label_end"] = labels["label_end"].dt.strftime("%Y-%m-%d").where(labels["label_end"].notna(), None)
    historical_verified = all(verified[name] == "verified" for name in
        ("historical_membership_status", "pit_status", "corporate_actions_status"))
    metadata = {"schema": MODEL_SCHEMA, "feature_schema": FEATURE_SCHEMA,
        "feature_order": list(FEATURE_ORDER), "effective_profile": "C0", "asof": asof,
        "horizons": list(hs), "target_definition": TARGET_DEFINITION,
        "policy_binding": binding, "policy_binding_sha256": _digest(_policy_identity(binding)),
        "caller_policy_binding_sha256": _digest(binding),
        "source_evidence": verified, "source_evidence_sha256": _digest(verified),
        "historical_pit_verified": verified["pit_status"] == "verified",
        "historical_membership_verified": verified["historical_membership_status"] == "verified",
        "corporate_actions_verified": verified["corporate_actions_status"] == "verified",
        "historical_qualification": "caller_verified" if historical_verified else "effect_unknown",
        "formal_model_qualified": False, "trading_effect": "unverified",
        "market_calendar_sha256": {m: _digest([d.strftime("%Y-%m-%d") for d in ax]) for m, ax in axes.items()},
        "features_sha256": _frame_hash(features), "labels_sha256": _frame_hash(labels),
        "pandas_version": pd.__version__, "numpy_version": np.__version__,
        "module_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    return {"features": features, "labels": labels, "metadata": metadata}


def _check_prepared(prepared: dict) -> None:
    metadata = prepared["metadata"]
    binding = _policy(metadata["policy_binding"])
    _require(metadata["policy_binding_sha256"] == _digest(_policy_identity(binding)) and
             metadata["caller_policy_binding_sha256"] == _digest(binding), "prepared_policy_binding_changed")
    pairs = set(prepared["features"][["stock_id", "market"]].itertuples(index=False, name=None))
    verified = _evidence(metadata["source_evidence"], pairs, {p[1] for p in pairs})
    _require(metadata["source_evidence_sha256"] == _digest(verified), "prepared_source_evidence_changed")
    _require(metadata["features_sha256"] == _frame_hash(prepared["features"]) and
             metadata["labels_sha256"] == _frame_hash(prepared["labels"]), "prepared_panel_changed")
    # The program sha in metadata is a provenance label, never a gate (plan 2.3).


def _current_module_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def module_identity(model: Mapping[str, Any]) -> dict:
    """Training-time program sha versus the program now loaded (a label, never a gate)."""
    recorded = ((model or {}).get("metadata") or {}).get("module_sha256")
    current = _current_module_sha256()
    return {"model_module_sha256": recorded, "current_module_sha256": current,
            "loaded_linear_path": str(Path(__file__).resolve()), "differs": recorded != current}


def _label(labels, code, *, recorded=None, actual=None, detail=None) -> None:
    if labels is None:
        return
    item = {"code": code, "recorded": recorded, "actual": actual, "detail": detail}
    if item not in labels:
        labels.append(item)


def _sigmoid(values: np.ndarray) -> np.ndarray:
    z = np.clip(np.asarray(values, dtype=float), -700.0, 700.0)
    return 1.0 / (1.0 + np.exp(-z))


def _apply_candidate(head: dict, x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    filled = np.where(np.isnan(x), np.asarray(head["imputer_medians"]), x)
    scaled = (filled - np.asarray(head["scaler_mean"])) / np.asarray(head["scaler_scale"])
    logit = scaled @ np.asarray(head["direction_coef"]) + head["direction_intercept"]
    raw = _sigmoid(logit)
    calibration = head.get("calibrator")
    probability = (_sigmoid(logit * calibration["coef"] + calibration["intercept"])
                   if calibration is not None else raw)
    expected = scaled @ np.asarray(head["return_coef"]) + head["return_intercept"]
    return probability, expected, raw


def _apply(head: dict, x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if "candidate_heads" not in head:
        return _apply_candidate(head, x)
    _require(head.get("direction_kind") in ("logistic", "fit_up_rate_baseline"), "selected_direction_kind_invalid")
    _require(head.get("return_kind") in ("ridge", "zero_return_baseline"), "selected_return_kind_invalid")
    direction = head["candidate_heads"][head["direction_candidate"]]
    returns = head["candidate_heads"][head["return_candidate"]]
    if head["direction_kind"] == "fit_up_rate_baseline":
        probability = np.full(len(x), direction["fit_up_rate"], dtype=float)
        raw = probability.copy()
    else:
        probability, _, raw = _apply_candidate(direction, x)
    if head["return_kind"] == "zero_return_baseline":
        expected = np.zeros(len(x), dtype=float)
    else:
        _, expected, _ = _apply_candidate(returns, x)
    return probability, expected, raw


def _metrics(rows: pd.DataFrame, probability: np.ndarray, expected: np.ndarray,
             fit_up_rate: float) -> dict:
    y = rows["target_return"].to_numpy(dtype=float)
    nonflat = np.abs(y) > FLAT_TOLERANCE
    actual_up = y > 0
    predicted_up = probability >= 0.5
    directional_count = int(nonflat.sum())
    return {"observed_count": len(rows), "nonflat_direction_count": directional_count,
        "flat_count": int((~nonflat).sum()), "flat_tolerance": FLAT_TOLERANCE,
        "direction_accuracy": float((predicted_up[nonflat] == actual_up[nonflat]).mean()) if directional_count else None,
        "mae": float(np.abs(expected - y).mean()) if len(rows) else None,
        "brier": float(np.square(probability - actual_up.astype(float)).mean()) if len(rows) else None,
        "brier_count": len(rows), "zero_return_mae": float(np.abs(y).mean()) if len(rows) else None,
        "fit_up_rate": fit_up_rate,
        "fit_up_rate_brier": float(np.square(fit_up_rate - actual_up.astype(float)).mean()) if len(rows) else None,
        "fit_up_rate_direction_accuracy": float(((fit_up_rate >= 0.5) == actual_up[nonflat]).mean()) if directional_count else None}


def _split(rows: pd.DataFrame, fit_days: int, cal_days: int, eval_days: int) -> tuple[dict, dict]:
    dates = sorted(rows["session_date"].unique())
    # One finite candidate only.  Short histories retain an explicit limitation;
    # they never trigger a download or manufacture 126 evaluation days.
    evaluation = min(eval_days, max(20, len(dates) // 5)) if len(dates) >= 60 else 0
    calibration = min(cal_days, max(20, len(dates) // 5)) if len(dates) >= 60 else 0
    evaluation_dates = dates[-evaluation:] if evaluation else []

    def before_boundary(boundary):
        if boundary is None:
            return rows
        return rows.loc[(rows["session_date"] < boundary) & (rows["label_end"] < boundary)]

    # Select requested *usable* signal days after the label-end purge, rather
    # than charging H buffer sessions to the fit/calibration window.
    calibration_pool = before_boundary(evaluation_dates[0] if evaluation_dates else None)
    calibration_dates = sorted(calibration_pool["session_date"].unique())[-calibration:] if calibration else []
    next_fit_start = (calibration_dates or evaluation_dates)
    fit_pool = before_boundary(next_fit_start[0] if next_fit_start else None)
    fit_dates = sorted(fit_pool["session_date"].unique())[-fit_days:]
    block_dates = {"fit": fit_dates, "calibration": calibration_dates, "evaluation": evaluation_dates}
    blocks, summary = {}, {}
    for block, selected in block_dates.items():
        part = rows.loc[rows["session_date"].isin(selected)].copy()
        if block == "fit":
            next_dates = calibration_dates or evaluation_dates
        elif block == "calibration":
            next_dates = evaluation_dates
        else:
            next_dates = []
        before = len(part)
        boundary_gap_rows = 0
        if next_dates:
            part = part.loc[part["label_end"] < next_dates[0]].copy()
            if selected:
                gap = rows.loc[(rows["session_date"] > selected[-1]) &
                    (rows["session_date"] < next_dates[0]) & (rows["label_end"] >= next_dates[0])]
                boundary_gap_rows = len(gap)
        blocks[block] = part
        summary[block] = {"requested_signal_days": fit_days if block == "fit" else cal_days if block == "calibration" else eval_days,
            "signal_days": int(part["session_date"].nunique()), "rows": len(part),
            "signal_start": part["session_date"].min() if len(part) else None,
            "signal_end": part["session_date"].max() if len(part) else None,
            "max_label_end": part["label_end"].max() if len(part) else None,
            "purged_rows": before - len(part) + boundary_gap_rows,
            "boundary_gap_rows": boundary_gap_rows,
            "next_signal_start": next_dates[0] if next_dates else None}
    return blocks, summary


def _fit_candidate(prepared: dict, *, fit_signal_days: int | None = None,
                   calibration_signal_days: int = 126, evaluation_signal_days: int = 126,
                   require_full_fit_window: bool = False) -> dict:
    """Fit one fixed Logistic/Ridge candidate per horizon; do not live-apply.

    All stocks on a calendar date share a block.  Labels extending to the next
    block are purged.  Unknown historical evidence yields research limitations,
    not PIT/OOS qualification.  Calibration uses an independent mature block;
    its reliability remains unverified until sufficient forward evidence exists.
    """
    from sklearn import __version__ as sklearn_version
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.preprocessing import StandardScaler

    for value in (calibration_signal_days, evaluation_signal_days):
        _require(type(value) is int and 0 <= value <= 126, "calibration_evaluation_days_invalid")
    if fit_signal_days is not None:
        _require(type(fit_signal_days) is int and 20 <= fit_signal_days <= 756, "fit_signal_days_invalid")
    _check_prepared(prepared)
    metadata = copy.deepcopy(prepared["metadata"])
    _require(metadata["feature_order"] == list(FEATURE_ORDER), "feature_schema_mismatch")
    joined = prepared["labels"].merge(prepared["features"], on=_KEYS, how="left", validate="many_to_one")
    models, metrics, summaries = {}, {}, {}
    for h in metadata["horizons"]:
        requested_fit = fit_signal_days or (252 if h <= 20 else 504)
        all_rows = joined.loc[joined["horizon"] == h]
        rows = all_rows.loc[(all_rows["label_state"] == "observed") & all_rows["feature_ready"]].copy()
        blocks, split = _split(rows, requested_fit, calibration_signal_days, evaluation_signal_days)
        fit = blocks["fit"]
        summary = {"status": "not_trained", "split": split,
            "observed_ready_rows": len(rows), "missing_label_rows": int((all_rows["label_state"] != "observed").sum()),
            "feature_unavailable_rows": int((~all_rows["feature_ready"]).sum()),
            "limited_history": any(split[b]["signal_days"] < split[b]["requested_signal_days"] for b in split),
            "candidate_count": 1, "evaluation_role": "comparison_sample_not_untouched_holdout"}
        summaries[str(h)] = summary
        if require_full_fit_window and fit["session_date"].nunique() < requested_fit:
            summary["reason"] = "insufficient_requested_fit_window_with_same_evaluation"
            continue
        if fit["session_date"].nunique() < 20 or len(fit) < 40:
            summary["reason"] = "insufficient_independent_fit_after_purge"
            continue
        y = fit["target_return"].to_numpy(dtype=float)
        direction = (y > 0).astype(int)
        if np.unique(direction).size != 2:
            summary["reason"] = "direction_fit_has_one_class"
            continue
        x = fit[list(FEATURE_ORDER)].to_numpy(dtype=float)
        imputer = SimpleImputer(strategy="median", keep_empty_features=True)
        scaler = StandardScaler()
        scaled = scaler.fit_transform(imputer.fit_transform(x))
        logistic = LogisticRegression(C=1.0, solver="lbfgs", max_iter=500, random_state=0).fit(scaled, direction)
        _require(int(logistic.n_iter_[0]) < 500, "direction_solver_not_converged")
        ridge = Ridge(alpha=10.0, solver="cholesky").fit(scaled, y)
        head = {"imputer_medians": imputer.statistics_.tolist(), "scaler_mean": scaler.mean_.tolist(),
            "scaler_scale": scaler.scale_.tolist(), "direction_coef": logistic.coef_[0].tolist(),
            "direction_intercept": float(logistic.intercept_[0]), "return_coef": ridge.coef_.tolist(),
            "return_intercept": float(ridge.intercept_), "fit_up_rate": float(direction.mean()),
            "calibrator": None, "calibration_status": "uncalibrated_no_independent_mature_block"}
        cal = blocks["calibration"]
        if cal["session_date"].nunique() >= 20 and len(cal) >= 50 and (cal["target_return"] > 0).nunique() == 2:
            probability, _, _ = _apply(head, cal[list(FEATURE_ORDER)].to_numpy(dtype=float))
            logits = np.log(np.clip(probability, 1e-12, 1-1e-12) / np.clip(1-probability, 1e-12, 1))
            calibrator = LogisticRegression(C=1.0, solver="lbfgs", max_iter=500, random_state=0).fit(
                logits.reshape(-1, 1), (cal["target_return"] > 0).astype(int))
            head["calibrator"] = {"coef": float(calibrator.coef_[0, 0]), "intercept": float(calibrator.intercept_[0])}
            head["calibration_status"] = "independent_calibration_fitted_reliability_unverified"
        summary.update(status="trained", calibration_status=head["calibration_status"],
            training_sha256=_frame_hash(fit[_KEYS + ["label_end", "target_return"] + list(FEATURE_ORDER)]),
            logistic_iterations=int(logistic.n_iter_[0]))
        head["split"] = split
        models[str(h)] = head
        evaluation = blocks["evaluation"]
        summary["evaluation_sha256"] = _frame_hash(evaluation[_KEYS + ["label_end", "target_return"]])
        horizon_metrics = {"split": split, "missing_label_rows": summary["missing_label_rows"],
            "feature_unavailable_rows": summary["feature_unavailable_rows"], "by_market": {},
            "coverage_by_market": {market: {
                "total_signal_rows": len(part),
                "missing_label_rows": int((part["label_state"] != "observed").sum()),
                "feature_unavailable_rows": int((~part["feature_ready"]).sum()),
                "observed_ready_rows": int(((part["label_state"] == "observed") & part["feature_ready"]).sum())}
                for market, part in all_rows.groupby("market", sort=True)}}
        if len(evaluation):
            probability, expected, raw = _apply(head, evaluation[list(FEATURE_ORDER)].to_numpy(dtype=float))
            horizon_metrics["all"] = _metrics(evaluation, probability, expected, head["fit_up_rate"])
            horizon_metrics["all"]["uncalibrated_brier"] = _metrics(evaluation, raw, expected, head["fit_up_rate"])["brier"]
            for market in sorted(evaluation["market"].unique()):
                mask = evaluation["market"].to_numpy() == market
                horizon_metrics["by_market"][market] = _metrics(evaluation.loc[mask], probability[mask], expected[mask], head["fit_up_rate"])
        else:
            horizon_metrics["all"] = None
        metrics[str(h)] = horizon_metrics
    metadata.update(model_kind="fixed_logistic_ridge", sklearn_version=sklearn_version,
        fixed_parameters={"logistic_C": 1.0, "ridge_alpha": 10.0, "max_candidates_per_horizon": 1},
        horizon_training=summaries, model_status="trained" if models else "not_trained",
        evaluation_role="comparison_sample_not_untouched_holdout",
        calibration_reliability_verified=False, accuracy_enable_gate=False)
    result = {"schema": MODEL_SCHEMA, "models_by_horizon": models, "metadata": metadata, "metrics": metrics}
    _json_bytes(result)
    return result


def _choose_option(options: list[dict], metric: str) -> dict:
    finite = [o for o in options if o.get(metric) is not None and np.isfinite(o[metric])]
    _require(bool(finite), "independent_comparison_metric_unavailable")
    # Exact equal scores favour the simpler baseline, then the shorter fit.
    return min(finite, key=lambda o: (o[metric], 0 if o["kind"].endswith("baseline") else 1,
                                     o["fit_signal_days_cap"]))


def fit_baseline(prepared: dict, *, fit_signal_days: int | None = None,
                 calibration_signal_days: int = 126, evaluation_signal_days: int = 126,
                 compare_windows: bool = True) -> dict:
    """Compare bounded linear candidates and explicitly select simple baselines.

    Default budget: 252 and, only if fully available, 504 usable fit days for
    H<=20; one capped-504 compatible fit for H=60/63.  Each head is selected on
    the same evaluation dates: Brier for direction, MAE for return.  Evaluation
    is therefore a comparison sample, not an untouched holdout.  Every original
    fitted head and its metrics remain in the artifact.  No automatic retraining
    or live model application is performed.
    """
    _require(type(compare_windows) is bool, "compare_windows_must_be_boolean")
    first = _fit_candidate(prepared, fit_signal_days=fit_signal_days,
        calibration_signal_days=calibration_signal_days, evaluation_signal_days=evaluation_signal_days)
    short_horizons = [h for h in prepared["metadata"]["horizons"] if h <= 20]
    second = None
    if compare_windows and fit_signal_days is None and short_horizons:
        short_prepared = {**prepared, "metadata": copy.deepcopy(prepared["metadata"])}
        short_prepared["metadata"]["horizons"] = short_horizons
        second = _fit_candidate(short_prepared, fit_signal_days=504,
            calibration_signal_days=calibration_signal_days, evaluation_signal_days=evaluation_signal_days,
            require_full_fit_window=True)
    joined = prepared["labels"].merge(prepared["features"], on=_KEYS, how="left", validate="many_to_one")
    result = copy.deepcopy(first)
    selections, candidate_metrics, candidate_training = {}, {}, {}
    for h in prepared["metadata"]["horizons"]:
        key = str(h)
        if key not in first["models_by_horizon"]:
            continue
        cap = fit_signal_days or (252 if h <= 20 else 504)
        first_name = "fit_" + str(cap)
        candidates = {first_name: first["models_by_horizon"][key]}
        metrics_by_candidate = {first_name: first["metrics"][key]}
        training_by_candidate = {first_name: first["metadata"]["horizon_training"][key]}
        skipped = []
        if second is not None and h <= 20:
            second_training = second["metadata"]["horizon_training"][key]
            if key in second["models_by_horizon"]:
                _require(second_training["evaluation_sha256"] == training_by_candidate[first_name]["evaluation_sha256"],
                         "candidate_evaluation_samples_do_not_match")
                candidates["fit_504"] = second["models_by_horizon"][key]
                metrics_by_candidate["fit_504"] = second["metrics"][key]
                training_by_candidate["fit_504"] = second_training
            else:
                skipped.append({"candidate": "fit_504", "reason": second_training.get("reason"),
                                "available_fit_signal_days": second_training["split"]["fit"]["signal_days"]})
        if h >= 60:
            mature = joined.loc[(joined.horizon == h) & joined.feature_ready & (joined.label_state == "observed")]
            _, proposed = _split(mature, 756, calibration_signal_days, evaluation_signal_days)
            skipped.append({"candidate": "fit_756", "reason": "insufficient_requested_fit_window_with_same_evaluation"
                if proposed["fit"]["signal_days"] < 756 else "outside_authorized_short_candidate_comparison_budget",
                "available_fit_signal_days": proposed["fit"]["signal_days"], "fit_executed": False})
        direction_options, return_options = [], []
        for name, metric in metrics_by_candidate.items():
            total = metric.get("all")
            if total is None:
                continue
            fit_cap = int(name.split("_")[1])
            direction_options.extend([
                {"candidate": name, "kind": "logistic", "brier": total["brier"], "fit_signal_days_cap": fit_cap},
                {"candidate": name, "kind": "fit_up_rate_baseline", "brier": total["fit_up_rate_brier"], "fit_signal_days_cap": fit_cap}])
            return_options.append({"candidate": name, "kind": "ridge", "mae": total["mae"], "fit_signal_days_cap": fit_cap})
        # Zero-return is parameter-free; its evaluation population is exactly
        # the same, independent of which candidate's transform fitted the data.
        first_total = metrics_by_candidate[first_name].get("all")
        if first_total is not None:
            return_options.append({"candidate": first_name, "kind": "zero_return_baseline",
                                   "mae": first_total["zero_return_mae"], "fit_signal_days_cap": cap})
        if not direction_options or not return_options:
            result["metadata"]["horizon_training"][key]["selection_status"] = "unavailable_independent_comparison"
            result["models_by_horizon"].pop(key, None)
            continue
        selected_direction = _choose_option(direction_options, "brier")
        selected_return = _choose_option(return_options, "mae")
        selected = copy.deepcopy(candidates[first_name])
        selected.update(candidate_heads=candidates, direction_kind=selected_direction["kind"],
            return_kind=selected_return["kind"], direction_candidate=selected_direction["candidate"],
            return_candidate=selected_return["candidate"],
            selected_fit_up_rate=candidates[selected_direction["candidate"]]["fit_up_rate"],
            original_candidate_fields_reference=first_name,
            calibration_status=("uncalibrated_constant_fit_up_rate" if selected_direction["kind"] == "fit_up_rate_baseline"
                                else candidates[selected_direction["candidate"]]["calibration_status"]))
        result["models_by_horizon"][key] = selected
        split = first["metrics"][key]["split"]["evaluation"]
        evaluation = joined.loc[(joined.horizon == h) & joined.feature_ready & (joined.label_state == "observed") &
            (joined.session_date >= split["signal_start"]) & (joined.session_date <= split["signal_end"])].copy()
        probability, expected, raw = _apply(selected, evaluation[list(FEATURE_ORDER)].to_numpy(dtype=float))
        prior = candidates[selected_direction["candidate"]]["fit_up_rate"]
        selected_metrics = copy.deepcopy(first["metrics"][key])
        selected_metrics["all"] = _metrics(evaluation, probability, expected, prior)
        selected_metrics["all"]["uncalibrated_brier"] = _metrics(evaluation, raw, expected, prior)["brier"]
        selected_metrics["by_market"] = {}
        for market in sorted(evaluation["market"].unique()):
            mask = evaluation.market.to_numpy() == market
            selected_metrics["by_market"][market] = _metrics(evaluation.loc[mask], probability[mask], expected[mask], prior)
        selected_metrics["selection_population"] = "all_observed_feature_ready_stocks_same_evaluation_dates"
        result["metrics"][key] = selected_metrics
        selections[key] = {"direction": selected_direction, "return": selected_return,
            "direction_options": direction_options, "return_options": return_options,
            "skipped_candidates": skipped, "evaluation_role": "comparison_sample_not_untouched_holdout",
            "selection_rule": "minimum_all_stock_brier_for_direction_and_mae_for_return; exact_ties_prefer_baseline_then_shorter_fit",
            "evaluation_sha256": training_by_candidate[first_name]["evaluation_sha256"],
            "baseline_probability_scope": "pooled_fit_block_empirical_up_rate_including_flat_as_not_up",
            "stock_ranking_ties": {"prob_up_constant": selected_direction["kind"] == "fit_up_rate_baseline",
                                   "expected_return_zero": selected_return["kind"] == "zero_return_baseline"}}
        candidate_metrics[key], candidate_training[key] = metrics_by_candidate, training_by_candidate
        summary = result["metadata"]["horizon_training"][key]
        summary.update(candidate_count=len(candidates), selected_direction_kind=selected_direction["kind"],
            selected_return_kind=selected_return["kind"], selected_direction_candidate=selected_direction["candidate"],
            selected_return_candidate=selected_return["candidate"], calibration_status=selected["calibration_status"],
            selected_direction_fit_signal_days=training_by_candidate[selected_direction["candidate"]]["split"]["fit"]["signal_days"],
            selected_return_fit_signal_days=(0 if selected_return["kind"] == "zero_return_baseline" else
                training_by_candidate[selected_return["candidate"]]["split"]["fit"]["signal_days"]))
    result["metadata"].update(model_kind="finite_linear_and_explicit_simple_baselines",
        model_status="trained" if result["models_by_horizon"] else "not_trained",
        selection_by_horizon=selections, candidate_metrics_by_horizon=candidate_metrics,
        candidate_training_by_horizon=candidate_training,
        fixed_parameters={"logistic_C": 1.0, "ridge_alpha": 10.0,
                          "max_candidates_short_horizon": 2, "max_candidates_long_horizon": 1},
        evaluation_used_for_head_selection=True,
        candidate_calibration_scope="pooled_markets_per_horizon_independent_date_block")
    _json_bytes(result)
    return result


def predict_baseline(model: dict, prepared: dict) -> pd.DataFrame:
    """Predict exact asof rows; an unknown future target date is disclosed.

    H always means the Hth future market session.  Forecasting this horizon does
    not manufacture a session date when the declared calendar stops earlier.
    Mature label building and subsequent settlement still require exact dates.
    """
    _require(model.get("schema") == MODEL_SCHEMA, "model_schema_mismatch")
    _check_prepared(prepared)
    metadata, incoming = model["metadata"], prepared["metadata"]
    # Program version: a label on every row (model built by a different program
    # version), never a gate.  Feature semantics stay guarded below.
    current_module = _current_module_sha256()
    module_sha_mismatch = not (metadata.get("module_sha256") == incoming.get("module_sha256") == current_module)
    _require(metadata.get("target_definition") == incoming.get("target_definition") == TARGET_DEFINITION and
             metadata.get("effective_profile") == incoming.get("effective_profile") == "C0",
             "model_target_profile_mismatch")
    _require(metadata["feature_order"] == incoming["feature_order"] == list(FEATURE_ORDER) and
             metadata["feature_schema"] == incoming["feature_schema"] == FEATURE_SCHEMA,
             "model_feature_schema_mismatch")
    # Caller policy identity differing from the model's training identity is a label.
    policy_differs = metadata["policy_binding_sha256"] != incoming["policy_binding_sha256"]
    _require(metadata["policy_binding_sha256"] == _digest(_policy_identity(_policy(metadata["policy_binding"]))) and
             metadata["caller_policy_binding_sha256"] == _digest(metadata["policy_binding"]),
             "model_policy_binding_mismatch")
    # Each prediction retains its actual feature day, including stale inputs.
    # Forward recording excludes in-sample and explicitly lagged display rows.
    current = prepared["features"].loc[prepared["features"]["session_date"] == incoming["asof"]]
    labels = prepared["labels"]
    asof_labels = labels["session_date"] == incoming["asof"]
    label_positions = {}
    for position, key in zip(np.flatnonzero(asof_labels.to_numpy()),
            labels.loc[asof_labels, _KEYS + ["horizon"]].itertuples(index=False, name=None)):
        # Equality lookup never matches a missing key; retain that behaviour.
        if not any(pd.isna(value) for value in key):
            label_positions.setdefault(key, int(position))
    features = prepared["features"]
    priced = features.loc[features["priced_observations"] > 0]
    if len(priced):
        firsts = priced.loc[priced.groupby(["stock_id", "market"])["priced_observations"].transform("max")
                            == priced["priced_observations"]]
        last_price = firsts.groupby(["stock_id", "market"])["session_date"].min().to_dict()
    else:
        last_price = {}
    quality = {(str(item["stock_id"]), str(item["market"])): item
        for item in incoming["source_evidence"].get("stock_data_quality_assessments", [])}
    feature_names = {"return_5": "5日報酬", "return_20": "20日報酬", "return_60": "60日報酬",
        "return_126": "126日報酬", "return_252": "252日報酬",
        "volatility_20": "20日波動率", "volatility_60": "60日波動率"}
    output = []
    for row in current.to_dict("records"):
        stock_last = last_price.get((row["stock_id"], row["market"]))
        feature_date = stock_last if stock_last == incoming["asof"] else None
        data_lagged, lag_sessions = False, 0
        if stock_last is not None and stock_last < incoming["asof"]:
            pair_features = features.loc[(features["stock_id"] == row["stock_id"]) &
                                         (features["market"] == row["market"])]
            available = pair_features.loc[pair_features["session_date"] == stock_last]
            if len(available) == 1:
                row = available.iloc[0].to_dict()
                feature_date = stock_last
                data_lagged = True
                lag_sessions = int(((pair_features["session_date"] > feature_date) &
                                    (pair_features["session_date"] <= incoming["asof"])).sum())
        in_sample = (feature_date or incoming["asof"]) < metadata["asof"]
        raw_x, imputed_features = None, None
        for h in incoming["horizons"]:
            position = label_positions.get(tuple(row[key] for key in _KEYS) + (h,))
            if position is None and feature_date is not None:
                matching = labels.loc[(labels["stock_id"] == row["stock_id"]) &
                                      (labels["market"] == row["market"]) &
                                      (labels["session_date"] == feature_date) & (labels["horizon"] == h)]
                label = matching.iloc[0]
            else:
                label = labels.iloc[position] if position is not None else labels.iloc[:0].iloc[0]
            head = model["models_by_horizon"].get(str(h))
            result = {key: row[key] for key in _KEYS}
            result.update(horizon=h, horizon_sessions=h, target_session_date=label["label_end"],
                future_calendar_status="verified_declared_session" if label["label_end"] is not None else "not_yet_verified", prob_up=None,
                expected_return=None, prob_up_uncalibrated=None, effective_profile="C0",
                target_definition=TARGET_DEFINITION, calibration_status=head["calibration_status"] if head else "not_trained",
                prediction_status="unavailable", reason=None,
                historical_qualification=metadata["historical_qualification"],
                formal_model_qualified=False, trading_effect="unverified")
            result.update(selected_direction_kind=head.get("direction_kind", "logistic") if head else None,
                selected_return_kind=head.get("return_kind", "ridge") if head else None,
                probability_constant_across_stocks=head.get("direction_kind") == "fit_up_rate_baseline" if head else False,
                expected_return_zero_baseline=head.get("return_kind") == "zero_return_baseline" if head else False,
                candidate_predictions={})
            assessment = copy.deepcopy(quality.get((row["stock_id"], row["market"]), {}))
            observed = int(row.get("priced_window_observations", row["priced_observations"]))
            assessment.setdefault("schema", "stock-data-quality-assessment-v1")
            assessment.setdefault("stock_id", row["stock_id"])
            assessment.setdefault("market", row["market"])
            assessment.setdefault("stock_name", row["stock_id"])
            assessment.setdefault("expected_sessions", 253)
            assessment.setdefault("observed_sessions", observed)
            assessment.setdefault("missing_sessions", max(0, 253 - observed))
            assessment.setdefault("first_price_date", None)
            assessment.setdefault("last_price_date", stock_last)
            notes = list(assessment.get("notes") or [])
            if assessment["missing_sessions"] and not notes:
                notes.append(f"價格資料 {observed}／253 個交易日，缺 {assessment['missing_sessions']} 日；分析可信度較低。")
            assessment["notes"] = notes
            assessment.setdefault("reason_codes", [])
            assessment.setdefault("status", "partial" if assessment["missing_sessions"] else "complete")
            assessment["imputed_features"] = []
            if data_lagged:
                assessment["status"] = "partial"
                target_note = label["label_end"] or "尚未確認"
                notes.append(f"分析日 {incoming['asof']}；資料與特徵日期 {feature_date}，落後 {lag_sessions} 個交易日；"
                             f"以下為該資料日基準的 {h} 交易日推論，目標日 {target_note}，不是分析日的當期報價預測。")
            result.update(stock_name=assessment["stock_name"], stock_data_notes=notes,
                data_quality_assessment=assessment)
            result.update(data_asof=feature_date, feature_data_date=feature_date,
                analysis_asof=incoming["asof"], data_lagged=data_lagged, data_lag_sessions=lag_sessions,
                model_training_asof=metadata["asof"],
                stock_last_price_date=stock_last, in_sample_lookahead=in_sample,
                module_sha_mismatch=module_sha_mismatch, model_policy_binding_differs=policy_differs)
            if head is None:
                result["reason"] = "horizon_not_trained"
            elif stock_last is None or feature_date != stock_last:
                result["reason"] = "stock_price_missing" if stock_last is None else "stock_feature_row_missing"
                assessment["status"] = "unavailable"
                notes.append("沒有有效價格，無法取得可推論的資料日。" if stock_last is None else
                             "最後有效價格日缺少唯一可用的特徵列；預測無法計算，仍保留其他資訊。")
            else:
                if raw_x is None:
                    raw_x = np.asarray([[row[name] for name in FEATURE_ORDER]], dtype=float)
                    imputed_features = [name for name, value in zip(FEATURE_ORDER, raw_x[0]) if np.isnan(value)]
                x, imputed = raw_x, list(imputed_features)
                if imputed:
                    assessment["imputed_features"] = imputed
                    assessment["status"] = "partial"
                    notes.append("模型特徵「" + "、".join(feature_names[name] for name in imputed)
                        + "」缺值，沿用模型既有缺值處理；分析可信度較低，原始指標仍標示缺值。")
                try:
                    if np.isinf(x).any():
                        raise ValueError("model_features_invalid")
                    probability, expected, raw = _apply(head, x)
                    if not np.isfinite([probability[0], expected[0], raw[0]]).all():
                        raise ValueError("model_prediction_unavailable")
                except (ArithmeticError, KeyError, TypeError, ValueError):
                    result["reason"] = "model_prediction_unavailable"
                    assessment["status"] = "unavailable"
                    notes.append("模型無法以現有資料完成計算；預測數值未提供，仍保留其他可用資訊。")
                else:
                    result.update(prob_up=float(probability[0]), expected_return=float(expected[0]),
                        prob_up_uncalibrated=float(raw[0]), prediction_status="predicted")
                    for candidate_name, candidate_head in head.get("candidate_heads", {}).items():
                        try:
                            cp, cr, cu = _apply_candidate(candidate_head, x)
                            if not np.isfinite([cp[0], cr[0], cu[0]]).all():
                                raise ValueError("model_prediction_unavailable")
                        except (ArithmeticError, KeyError, TypeError, ValueError):
                            result["candidate_predictions"][candidate_name] = {"prob_up": None,
                                "expected_return": None, "prob_up_uncalibrated": None,
                                "calibration_status": candidate_head.get("calibration_status"),
                                "reason": "model_prediction_unavailable"}
                        else:
                            result["candidate_predictions"][candidate_name] = {"prob_up": float(cp[0]),
                                "expected_return": float(cr[0]), "prob_up_uncalibrated": float(cu[0]),
                                "calibration_status": candidate_head["calibration_status"]}
            if result["reason"] is not None:
                assessment["reason_codes"] = sorted(set(assessment["reason_codes"] + [result["reason"]]))
            output.append(result)
    return pd.DataFrame(output)


def _atomic_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _require(not path.exists(), "artifact_target_already_exists")
    temporary = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _require(not path.exists(), "artifact_target_already_exists")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def save_artifact(model: dict, path: str | Path) -> dict:
    """Write a new JSON coefficient artifact and exact-hash metadata sidecar."""
    _require(model.get("schema") == MODEL_SCHEMA, "model_schema_mismatch")
    selected = Path(path)
    sidecar = selected.with_name(selected.name + ".metadata.json")
    _require(not selected.exists() and not sidecar.exists(), "artifact_target_already_exists")
    payload = _json_bytes(model) + b"\n"
    metadata = {"schema": MODEL_SCHEMA, "artifact_sha256": hashlib.sha256(payload).hexdigest(),
        "artifact_byte_size": len(payload), "feature_schema": FEATURE_SCHEMA,
        "feature_order": list(FEATURE_ORDER), "policy_binding_sha256": model["metadata"]["policy_binding_sha256"],
        "module_sha256": model["metadata"]["module_sha256"],
        "training_by_horizon": model["metadata"]["horizon_training"], "formal_model_qualified": False}
    _atomic_new(selected, payload)
    _atomic_new(sidecar, _json_bytes(metadata) + b"\n")
    return {"artifact_path": str(selected), "metadata_path": str(sidecar), **metadata}


def load_artifact(path: str | Path, expected_policy_binding: Mapping[str, Any] | None = None,
                  labels: list | None = None) -> dict:
    """Verify a JSON artifact's bytes, schema, features and self-consistent policy.

    The artifact must agree with its own sidecar (integrity).  A current
    program differing from the training program, or a caller policy differing
    from the training policy, is appended to ``labels`` (never raised).
    """
    selected = Path(path)
    payload = selected.read_bytes()
    sidecar = json.loads(selected.with_name(selected.name + ".metadata.json").read_text(encoding="utf-8"))
    _require(sidecar.get("artifact_sha256") == hashlib.sha256(payload).hexdigest() and
             sidecar.get("artifact_byte_size") == len(payload), "model_artifact_hash_mismatch")
    model = json.loads(payload)
    _require(model.get("schema") == sidecar.get("schema") == MODEL_SCHEMA, "model_schema_mismatch")
    _require(model["metadata"]["feature_order"] == sidecar["feature_order"] == list(FEATURE_ORDER) and
             model["metadata"]["feature_schema"] == sidecar["feature_schema"] == FEATURE_SCHEMA,
             "model_feature_schema_mismatch")
    _require(model["metadata"]["module_sha256"] == sidecar["module_sha256"], "model_module_hash_mismatch")
    identity = module_identity(model)
    if identity["differs"]:
        _label(labels, "inference_code_differs_from_training", recorded=identity["model_module_sha256"],
               actual=identity["current_module_sha256"],
               detail={"loaded_linear_path": identity["loaded_linear_path"],
                       "meaning": "model_built_by_a_different_program_version"})
    _require(model["metadata"]["policy_binding_sha256"] == sidecar["policy_binding_sha256"] ==
             _digest(_policy_identity(_policy(model["metadata"]["policy_binding"]))), "model_policy_binding_mismatch")
    _require(model["metadata"]["caller_policy_binding_sha256"] ==
             _digest(model["metadata"]["policy_binding"]), "model_caller_policy_binding_mismatch")
    if expected_policy_binding is not None:
        expected = _digest(_policy_identity(_policy(expected_policy_binding)))
        if expected != sidecar["policy_binding_sha256"]:
            _label(labels, "model_policy_binding_differs", recorded=sidecar["policy_binding_sha256"], actual=expected)
    return model
