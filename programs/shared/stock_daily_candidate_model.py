"""Research-only selected-feature models; no active artifact or data I/O.

All learning is confined to purged fit/calibration blocks. Selection locks real
heads before final-holdout evaluation. HGB artifacts contain validated numeric
TreePredictor records, never pickle or executable estimator state.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import sklearn
from scipy.special import expit, logit
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.ensemble._hist_gradient_boosting.common import PREDICTOR_RECORD_DTYPE
from sklearn.ensemble._hist_gradient_boosting.predictor import TreePredictor
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import balanced_accuracy_score, roc_auc_score
from threadpoolctl import threadpool_limits

MODEL_SCHEMA = "tw-stock-daily-selected-candidate-v1"
FEATURE_SCHEMA = "tw-stock-selected-features-v1"
TARGET_DEFINITION = "signal_close_to_Hth_market_session_close_return"
SEVEN_FEATURES = ("return_5", "return_20", "return_60", "return_126", "return_252",
                  "volatility_20", "volatility_60")
SUPPORTED_HORIZONS = (1, 5, 20, 60, 63)
THREAD_LIMIT = 4
_KEYS = ["stock_id", "market", "session_date"]
_LABEL_COLUMNS = {"horizon", "label_end", "target_return", "label_state", "feature_ready"}
_KINDS = ("seven_linear", "expanded_linear", "hist_gradient_boosting")
_HGB_PARAMS = dict(max_iter=120, max_leaf_nodes=15, max_depth=3, learning_rate=0.05,
                   l2_regularization=10.0, min_samples_leaf=40, early_stopping=False,
                   categorical_features=None)
_LIMITATIONS = [
    "候選研究估計，未採用至正式模型，不能作為正式可交付預測。",
    "重疊標籤的訊號日不是獨立樣本；長期報酬估計尤其不穩定。",
    "來源及歷史可得時間由上游來源目錄負責驗證；模型擬合成功不代表來源完整或發布合格。",
    "七特徵組為新缺值規則下的受控參考，不是原啟用模型成品的獨立重驗。",
]


class CandidateModelError(ValueError):
    """Invalid prepared input, unsafe artifact, or incompatible native ABI."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise CandidateModelError(reason)


def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_safe(v) for v in value]
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return None if pd.isna(value) else pd.Timestamp(value).isoformat()
    if isinstance(value, np.generic):
        return _safe(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    raise CandidateModelError("metadata_not_json_serializable:" + type(value).__name__)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _seal(model: dict) -> dict:
    result = _safe(model)
    result.pop("integrity_sha256", None)
    result["integrity_sha256"] = _digest(result)
    return result


def _day(value: Any) -> pd.Timestamp:
    try:
        day = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise CandidateModelError("invalid_calendar_day") from exc
    _require(not pd.isna(day) and day.tzinfo is None and day == day.normalize(),
             "invalid_calendar_day")
    return day


def _iso(value: Any) -> str | None:
    return None if pd.isna(value) else pd.Timestamp(value).strftime("%Y-%m-%d")


def _dates(values: pd.Series) -> pd.Series:
    return values.map(lambda x: pd.NaT if pd.isna(x) else _day(x))


def _numeric(frame: pd.DataFrame, columns: list[str]) -> np.ndarray:
    # Missing optional features remain NaN, never filled from another source.
    values = frame.reindex(columns=columns).apply(pd.to_numeric, errors="raise")
    result = values.to_numpy(dtype=np.float64, na_value=np.nan)
    result[~np.isfinite(result)] = np.nan
    return result


def _prepared(prepared: dict, *, labels_required: bool) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    _require(isinstance(prepared, dict), "prepared_must_be_dict")
    meta = copy.deepcopy(prepared.get("metadata", {}))
    _require(meta.get("feature_schema") == FEATURE_SCHEMA, "feature_schema_mismatch")
    order = meta.get("feature_order")
    _require(isinstance(order, list) and bool(order) and
             all(isinstance(c, str) and c not in _KEYS and c not in _LABEL_COLUMNS for c in order) and
             len(order) == len(set(order)), "invalid_feature_order")
    hs = meta.get("horizons")
    _require(isinstance(hs, (list, tuple)) and bool(hs) and
             all(type(h) is int and h in SUPPORTED_HORIZONS for h in hs) and
             len(hs) == len(set(hs)), "invalid_horizons")
    by_h = meta.get("feature_order_by_horizon")
    _require(isinstance(by_h, dict), "feature_order_by_horizon_missing")
    for h in hs:
        cols = by_h.get(str(h))
        _require(isinstance(cols, list) and len(cols) == len(set(cols)) and
                 set(cols).issubset(order), "invalid_horizon_feature_order")
    _require(isinstance(meta.get("feature_metadata"), (dict, list)), "feature_metadata_missing")
    _require(isinstance(meta.get("source_manifest"), (dict, list)), "source_manifest_missing")
    asofs = meta.get("asof_by_market")
    _require(isinstance(asofs, dict) and bool(asofs), "market_asof_missing")
    asofs = {str(m): _day(v) for m, v in asofs.items()}
    features = prepared.get("features")
    _require(isinstance(features, pd.DataFrame) and set(_KEYS + order + ["feature_ready"]).issubset(features.columns),
             "feature_columns_missing")
    features = features.copy()
    _require(features.columns.is_unique, "duplicate_feature_columns")
    _require(features["stock_id"].map(lambda v: isinstance(v, str) and bool(v)).all(),
             "stock_id_must_be_nonempty_string")
    _require(features["market"].isin(["TWSE", "TPEX"]).all(), "market_invalid")
    _require(features["feature_ready"].map(lambda v: isinstance(v, (bool, np.bool_))).all(),
             "feature_ready_must_be_boolean")
    features["feature_ready"] = features["feature_ready"].astype(bool)
    features["session_date"] = _dates(features["session_date"])
    _require(features["session_date"].notna().all(), "signal_date_missing")
    _require(not features.duplicated(_KEYS).any(), "duplicate_stock_market_session")
    for market, group in features.groupby("market", sort=False):
        _require(market in asofs, "market_asof_missing:" + market)
        _require((group["session_date"] <= asofs[market]).all(), "feature_after_market_asof")
    for col in ("available_time", "available_at", "feature_available_at"):
        if col in features:
            available = pd.to_datetime(features[col], errors="raise", utc=True)
            cutoff = (features["session_date"].dt.tz_localize("Asia/Taipei") +
                      pd.Timedelta(days=1)).dt.tz_convert("UTC")
            _require((available.isna() | (available < cutoff)).all(), "feature_available_after_signal")
    _numeric(features, order)
    labels = prepared.get("labels", pd.DataFrame(columns=_KEYS + ["horizon", "label_end",
                                                                   "target_return", "label_state"]))
    _require(isinstance(labels, pd.DataFrame), "labels_must_be_frame")
    if labels_required:
        _require(set(_KEYS + ["horizon", "label_end", "target_return", "label_state"])
                 .issubset(labels.columns), "label_columns_missing")
        labels = labels.copy()
        _require(labels.columns.is_unique, "duplicate_label_columns")
        labels["session_date"] = _dates(labels["session_date"])
        labels["label_end"] = _dates(labels["label_end"])
        _require(labels["horizon"].isin(hs).all(), "label_horizon_outside_metadata")
        _require(not labels.duplicated(_KEYS + ["horizon"]).any(), "duplicate_label")
        _require(labels[_KEYS].merge(features[_KEYS], how="left", indicator=True)
                 ["_merge"].eq("both").all(), "label_without_feature_row")
        _require((labels["label_end"].isna() |
                  (labels["label_end"] > labels["session_date"])).all(), "label_not_after_signal")
        labels["target_return"] = pd.to_numeric(labels["target_return"], errors="raise")
    meta["asof_by_market"] = {m: _iso(d) for m, d in asofs.items()}
    return features.sort_values(_KEYS).reset_index(drop=True), labels, _safe(meta)


def _horizon_frame(features: pd.DataFrame, labels: pd.DataFrame, meta: dict, h: int) -> pd.DataFrame:
    frame = features.merge(labels.loc[labels["horizon"].eq(h),
                                     _KEYS + ["label_end", "target_return", "label_state"]],
                           how="left", on=_KEYS, validate="one_to_one")
    frame["label_end"] = pd.to_datetime(frame["label_end"])
    asofs = frame["market"].map(meta["asof_by_market"]).map(_day)
    finite = np.isfinite(frame["target_return"].to_numpy(dtype=float, na_value=np.nan))
    observed = frame["label_state"].eq("observed")
    frame["label_eligible"] = observed & finite & frame["label_end"].notna() & (frame["label_end"] <= asofs)
    frame["label_reason"] = np.select(
        [frame["label_state"].isna(), frame["label_end"].gt(asofs), ~observed, ~finite],
        ["label_missing", "label_not_mature_at_market_asof", "label_not_observed", "target_not_finite"],
        default="eligible")
    return frame


def _split(frame: pd.DataFrame, *, min_fit_days: int, selection_days: int,
           test_days: int, calibration: bool) -> dict:
    mature = frame.loc[frame["label_eligible"] & frame["feature_ready"]]
    remaining = mature
    chosen: dict[str, list[pd.Timestamp]] = {}
    next_start = None
    stages = [("test", test_days), ("selection", selection_days)]
    if calibration:
        stages.append(("calibration", 20))
    for name, n in stages:
        available = remaining
        if next_start is not None:
            available = available.loc[(available["session_date"] < next_start) &
                                      (available["label_end"] < next_start)]
        axis = sorted(available["session_date"].unique())
        if len(axis) < n:
            return {"valid": False, "reason": "insufficient_" + name + "_signal_days",
                    "available_days": len(axis), "required_days": n}
        chosen[name] = [pd.Timestamp(d) for d in axis[-n:]]
        next_start = chosen[name][0]
        remaining = available.loc[available["session_date"] < next_start]
    fit = mature.loc[(mature["session_date"] < next_start) & (mature["label_end"] < next_start)]
    chosen["fit"] = [pd.Timestamp(d) for d in sorted(fit["session_date"].unique())]
    if len(chosen["fit"]) < min_fit_days:
        return {"valid": False, "reason": "insufficient_fit_signal_days",
                "available_days": len(chosen["fit"]), "required_days": min_fit_days}
    sequence = ["fit"] + (["calibration"] if calibration else []) + ["selection", "test"]
    masks, blocks = {}, {}
    assignments = pd.Series("outside_effective_blocks", index=frame.index, dtype=object)
    for i, name in enumerate(sequence):
        days = chosen[name]
        # A date belongs to one block across all stocks and markets. Ineligible
        # rows on that date are retained in that block's denominator.
        raw = frame["session_date"].isin(days)
        assignments.loc[raw] = name
        boundary = chosen[sequence[i + 1]][0] if i + 1 < len(sequence) else None
        mask = raw & frame["label_eligible"] & frame["feature_ready"]
        if boundary is not None:
            mask &= frame["label_end"] < boundary
        masks[name] = mask
        valid = frame.loc[mask]
        blocks[name] = {"signal_dates": [_iso(d) for d in days], "start": _iso(days[0]),
                        "end": _iso(days[-1]), "next_signal_start": _iso(boundary),
                        "n_signal_days": len(days), "n_effective_days": valid["session_date"].nunique(),
                        "n_denominator": int(raw.sum()), "n_rows": int(mask.sum()),
                        "n_stocks": len(valid[["stock_id", "market"]].drop_duplicates()),
                        "class_counts": {"up": int(valid["target_return"].gt(0).sum()),
                                         "not_up": int(valid["target_return"].le(0).sum())},
                        "n_excluded": int(raw.sum() - mask.sum()),
                        "by_market": {m: {"n_rows": len(g), "n_effective_days": g["session_date"].nunique(),
                                           "first_label_end": _iso(g["label_end"].min()),
                                           "last_label_end": _iso(g["label_end"].max())}
                                      for m, g in valid.groupby("market")}}
    return {"valid": True, "blocks": blocks, "masks": masks, "assignment": assignments,
            "calibration_enabled": calibration}


def _split_plan(frame: pd.DataFrame, h: int, minimum: int, selection: int, test: int) -> dict:
    plain = _split(frame, min_fit_days=minimum, selection_days=selection,
                   test_days=test, calibration=False)
    if h not in (1, 5, 20):
        plain["calibration_reason_zh"] = "H60/H63 事前固定不校準。"
        return plain
    extra = _split(frame, min_fit_days=minimum, selection_days=selection,
                   test_days=test, calibration=True)
    if extra["valid"]:
        cal_y = frame.loc[extra["masks"]["calibration"], "target_return"].gt(0)
        if cal_y.nunique() == 2:
            extra["calibration_reason_zh"] = "額外獨立 20 校準日與至少 60 訓練資料日成立，於評分前固定校準。"
            return extra
    plain["calibration_reason_zh"] = "額外獨立校準區或雙類別不足，於評分前固定不校準。"
    return plain


def _coverage(frame: pd.DataFrame, columns: list[str], plan: dict) -> dict:
    values = _numeric(frame, columns)
    result = {}
    for j, col in enumerate(columns):
        result[col] = {}
        blocks = {"all": pd.Series(True, index=frame.index)}
        if plan["valid"]:
            blocks.update({name: plan["assignment"].eq(name) for name in plan["blocks"]})
        for name, mask in blocks.items():
            n = int(mask.sum())
            count = int(np.isfinite(values[mask.to_numpy(), j]).sum())
            result[col][name] = {"n_denominator": n, "n_finite": count, "n_missing": n - count,
                                 "coverage": count / n if n else None}
    return result


def _transformer(frame: pd.DataFrame, columns: list[str]) -> dict:
    x = _numeric(frame, columns)
    median, means, scales, validity = [], [], [], {}
    effective, indicators = [], []
    for j, col in enumerate(columns):
        finite = np.isfinite(x[:, j])
        observed = x[finite, j]
        unique = np.unique(observed)
        numeric_effective = len(unique) > 1
        indicator_effective = bool(finite.any() and (~finite).any())
        state = "fit_all_missing" if not len(unique) else ("fit_constant" if len(unique) == 1 else "fit_informative")
        reason = {"fit_all_missing": "訓練資料期間全缺，未學會此欄位的數值或缺值訊號。",
                  "fit_constant": "訓練資料期間觀測值為常數，未學會數值變動訊號。",
                  "fit_informative": "訓練資料期間有數值變動，可參與學習；不代表已證明預測增益。"}[state]
        validity[col] = {"state": state, "reason_zh": reason, "n_fit_rows": len(x),
                         "n_fit_finite": int(finite.sum()), "n_fit_missing": int((~finite).sum()),
                         "n_fit_unique_finite": len(unique), "numeric_effective": numeric_effective,
                         "missing_indicator_effective": indicator_effective}
        med = float(np.median(observed)) if observed.size else 0.0
        imputed = np.where(finite, x[:, j], med) if numeric_effective else np.full(len(x), med)
        mean = float(imputed.mean()) if len(x) else 0.0
        scale = float(imputed.std()) if numeric_effective else 1.0
        median.append(med)
        means.append(mean)
        scales.append(scale if scale > 0 and np.isfinite(scale) else 1.0)
        if numeric_effective:
            effective.append(col)
        if indicator_effective:
            indicators.append(col)
    _require(np.isfinite(median).all() and np.isfinite(means).all() and np.isfinite(scales).all(),
             "fit_transform_statistics_not_finite")
    return {"feature_order": columns, "eligible_feature_order": columns,
            "effective_feature_order": effective, "effective_indicator_order": indicators,
            "feature_validity": validity, "median": median, "mean": means, "scale": scales,
            "missing_policy": "fit_only_median_and_scaler;fit_frozen_missing_indicators;no_future_information",
            "uninformative_policy": "freeze_fit_allmissing_and_constant_numeric_channels_at_fit_values",
            "native_all_missing_policy": "fit_frozen_neutral_constant_no_learned_signal"}


def _transform(frame: pd.DataFrame, transformer: dict, *, native: bool) -> tuple[np.ndarray, np.ndarray]:
    columns = transformer["feature_order"]
    x = _numeric(frame, columns)
    finite = np.isfinite(x)
    used = [j for j, c in enumerate(columns) if c in set(transformer["effective_feature_order"] +
                                                       transformer["effective_indicator_order"])]
    eligible = finite[:, used].any(axis=1) if used else np.zeros(len(x), dtype=bool)
    if "feature_ready" in frame:
        eligible &= frame["feature_ready"].to_numpy(dtype=bool)
    # All missing fit columns stay unlearned even when data first appears later.
    for j, col in enumerate(columns):
        info = transformer["feature_validity"][col]
        if info["state"] == "fit_all_missing":
            # sklearn 1.9's binning cannot take a wholly missing column. Keep
            # this unlearned channel constant in both fit and inference; partial
            # missingness in informative columns still uses native NaN handling.
            x[:, j] = transformer["median"][j] if native else np.nan
        elif not info["numeric_effective"]:
            if info["missing_indicator_effective"]:
                x[finite[:, j], j] = transformer["median"][j]
            else:
                x[:, j] = transformer["median"][j]
    if native:
        return np.ascontiguousarray(x, dtype=np.float64), eligible
    med = np.asarray(transformer["median"])
    numeric = (np.where(np.isfinite(x), x, med) - np.asarray(transformer["mean"])) / np.asarray(transformer["scale"])
    indicator = (~finite).astype(np.float64)
    for j, col in enumerate(columns):
        if col not in transformer["effective_indicator_order"]:
            indicator[:, j] = 0.0
    return np.ascontiguousarray(np.column_stack([numeric, indicator])), eligible


def _dtype_manifest() -> dict:
    dtype = np.dtype(PREDICTOR_RECORD_DTYPE)
    return {"descr": _safe(dtype.descr), "itemsize": dtype.itemsize}


def _encode_array(array: np.ndarray) -> dict:
    data = np.ascontiguousarray(array).tobytes()
    return {"shape": list(array.shape), "dtype": array.dtype.str,
            "data_base64": base64.b64encode(data).decode("ascii"),
            "sha256": hashlib.sha256(data).hexdigest()}


def _decode_array(value: dict, dtype: np.dtype, shape: tuple[int, ...]) -> np.ndarray:
    _require(isinstance(value, dict) and value.get("shape") == list(shape) and
             value.get("dtype") == dtype.str, "native_array_dtype_or_shape_mismatch")
    expected = int(np.prod(shape)) * dtype.itemsize
    encoded = value.get("data_base64")
    _require(isinstance(encoded, str) and len(encoded) == 4 * ((expected + 2) // 3),
             "native_array_size_mismatch")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise CandidateModelError("native_array_base64_invalid") from exc
    _require(len(raw) == expected and hashlib.sha256(raw).hexdigest() == value.get("sha256"),
             "native_array_hash_mismatch")
    return np.frombuffer(raw, dtype=dtype).reshape(shape).copy()


def _validate_nodes(nodes: np.ndarray, n_features: int) -> None:
    n = len(nodes)
    _require(1 <= n <= 29, "native_node_count_invalid")
    for field in ("is_leaf", "missing_go_to_left"):
        _require(np.isin(nodes[field], [0, 1]).all(), "native_node_bits_invalid")
    _require((nodes["is_categorical"] == 0).all() and (nodes["bitset_idx"] == 0).all(),
             "native_categorical_nodes_forbidden")
    _require(((nodes["feature_idx"] >= 0) & (nodes["feature_idx"] < n_features)).all(),
             "native_feature_index_out_of_range")
    _require(((nodes["left"] < n) & (nodes["right"] < n)).all(), "native_child_out_of_range")
    _require(np.isfinite(nodes["value"]).all() and (nodes["count"] > 0).all(), "native_node_values_invalid")
    _require(not np.isnan(nodes["num_threshold"]).any() and not np.isnan(nodes["gain"]).any(),
             "native_node_threshold_or_gain_invalid")
    visited = set()
    stack = [(0, 0)]
    while stack:
        index, depth = stack.pop()
        _require(index not in visited, "native_tree_cycle_or_shared_child")
        visited.add(index)
        _require(int(nodes[index]["depth"]) == depth and depth <= 3, "native_depth_invalid")
        node = nodes[index]
        if not node["is_leaf"]:
            left, right = int(node["left"]), int(node["right"])
            _require(left != right, "native_children_not_distinct")
            stack.extend([(left, depth + 1), (right, depth + 1)])
    _require(len(visited) == n, "native_unreachable_nodes")


def _export_hgb(estimator: Any, task: str) -> dict:
    """Export a fitted binary/regression HGB without executable Python state."""
    _require(task in ("direction", "return") and estimator.n_trees_per_iteration_ == 1,
             "native_only_binary_or_regression")
    if task == "direction":
        _require(np.array_equal(estimator.classes_, [0, 1]), "native_class_order_invalid")
    trees = []
    for iteration in estimator._predictors:
        _require(len(iteration) == 1, "native_iteration_width_invalid")
        predictor = iteration[0]
        _validate_nodes(predictor.nodes, int(estimator.n_features_in_))
        trees.append({"nodes": _encode_array(predictor.nodes),
                      "binned_left_cat_bitsets": _encode_array(predictor.binned_left_cat_bitsets),
                      "raw_left_cat_bitsets": _encode_array(predictor.raw_left_cat_bitsets)})
    return {"format": "sklearn-native-tree-predictor-json-v1", "task": task,
            "sklearn_version": sklearn.__version__, "predictor_record_dtype": _dtype_manifest(),
            "n_features": int(estimator.n_features_in_), "baseline": float(estimator._baseline_prediction[0, 0]),
            "link": "logistic" if task == "direction" else "identity", "trees": trees}


def _native_trees(payload: dict) -> list[TreePredictor]:
    _require(payload.get("format") == "sklearn-native-tree-predictor-json-v1" and
             payload.get("sklearn_version") == sklearn.__version__, "native_format_or_sklearn_version_mismatch")
    _require(payload.get("predictor_record_dtype") == _dtype_manifest(), "native_predictor_record_dtype_mismatch")
    nf = payload.get("n_features")
    _require(type(nf) is int and 1 <= nf <= 10000, "native_n_features_invalid")
    task = payload.get("task")
    _require(task in ("direction", "return") and
             payload.get("link") == ("logistic" if task == "direction" else "identity"), "native_link_invalid")
    _require(isinstance(payload.get("baseline"), (float, int)) and np.isfinite(payload["baseline"]),
             "native_baseline_invalid")
    trees = payload.get("trees")
    _require(isinstance(trees, list) and 1 <= len(trees) <= 120, "native_iterations_invalid")
    result = []
    for tree in trees:
        _require(isinstance(tree, dict) and isinstance(tree.get("nodes"), dict), "native_tree_invalid")
        shape = tree["nodes"].get("shape")
        _require(isinstance(shape, list) and len(shape) == 1 and type(shape[0]) is int and
                 1 <= shape[0] <= 29, "native_node_shape_invalid")
        nodes = _decode_array(tree["nodes"], np.dtype(PREDICTOR_RECORD_DTYPE), (shape[0],))
        _validate_nodes(nodes, nf)
        binned = _decode_array(tree.get("binned_left_cat_bitsets"), np.dtype("uint32"), (0, 8))
        raw = _decode_array(tree.get("raw_left_cat_bitsets"), np.dtype("uint32"), (0, 8))
        result.append(TreePredictor(nodes, binned, raw))
    return result


def _predict_hgb(payload: dict, x: np.ndarray) -> np.ndarray:
    # Validate every node before calling a native kernel with unchecked indexing.
    trees = _native_trees(payload)
    x = np.ascontiguousarray(x, dtype=np.float64)
    _require(x.ndim == 2 and x.shape[1] == payload["n_features"] and
             not np.isinf(x).any(), "native_input_invalid")
    scores = np.full(len(x), payload["baseline"], dtype=np.float64)
    known = np.zeros((0, 8), dtype=np.uint32)
    mapping = np.zeros(payload["n_features"], dtype=np.uint32)
    with threadpool_limits(limits=THREAD_LIMIT):
        for tree in trees:
            scores += tree.predict(x, known, mapping, THREAD_LIMIT)
    return expit(scores) if payload["task"] == "direction" else scores


def _fit_head(kind: str, task: str, frame: pd.DataFrame, transformer: dict, seed: int) -> dict:
    native = kind == "hist_gradient_boosting"
    x, valid = _transform(frame, transformer, native=native)
    y = frame["target_return"].to_numpy(dtype=float)
    valid &= np.isfinite(y)
    _require(valid.any(), "no_informative_fit_rows")
    target = (y[valid] > 0).astype(int) if task == "direction" else y[valid]
    if task == "direction":
        _require(np.unique(target).size == 2, "direction_fit_single_class")
    with threadpool_limits(limits=THREAD_LIMIT):
        if native:
            cls = HistGradientBoostingClassifier if task == "direction" else HistGradientBoostingRegressor
            fitted = cls(**_HGB_PARAMS, random_state=seed).fit(x[valid], target)
            payload = _export_hgb(fitted, task)
        else:
            fitted = (LogisticRegression(C=1.0, max_iter=1000, random_state=seed) if task == "direction"
                      else Ridge(alpha=10.0)).fit(x[valid], target)
            payload = {"format": "linear-coefficients-json-v1", "task": task,
                       "coef": np.asarray(fitted.coef_).reshape(-1).tolist(),
                       "intercept": float(np.asarray(fitted.intercept_).reshape(-1)[0])}
    return {"status": "trained", "kind": kind, "task": task, "payload": payload,
            "n_fit_rows": int(valid.sum()), "n_fit_denominator": len(frame),
            "calibration_status": "uncalibrated", "calibration": None}


def _head_predict(head: dict, frame: pd.DataFrame, transformer: dict) -> np.ndarray:
    result = np.full(len(frame), np.nan)
    if head.get("status") != "trained" or not len(frame):
        return result
    native = head["kind"] == "hist_gradient_boosting"
    x, valid = _transform(frame, transformer, native=native)
    if not valid.any():
        return result
    if native:
        values = _predict_hgb(head["payload"], x[valid])
    else:
        payload = head["payload"]
        with threadpool_limits(limits=THREAD_LIMIT):
            values = x[valid] @ np.asarray(payload["coef"], dtype=float) + payload["intercept"]
        if head["task"] == "direction":
            values = expit(values)
    if head["task"] == "direction" and head.get("calibration") is not None:
        cal = head["calibration"]
        values = expit(cal["coef"] * logit(np.clip(values, 1e-15, 1 - 1e-15)) + cal["intercept"])
    result[valid] = values
    result[~np.isfinite(result)] = np.nan
    return result


def _calibrate(head: dict, frame: pd.DataFrame, transformer: dict, seed: int) -> None:
    p = _head_predict(head, frame, transformer)
    y = frame["target_return"].to_numpy(dtype=float)
    valid = np.isfinite(p) & np.isfinite(y)
    if frame.loc[valid, "session_date"].nunique() < 20 or np.unique(y[valid] > 0).size < 2:
        head["calibration_status"] = "uncalibrated_insufficient_candidate_coverage"
        return
    with threadpool_limits(limits=THREAD_LIMIT):
        fitted = LogisticRegression(C=1.0, max_iter=1000, random_state=seed).fit(
            logit(np.clip(p[valid], 1e-15, 1 - 1e-15)).reshape(-1, 1), (y[valid] > 0).astype(int))
    head["calibration"] = {"kind": "independent_platt", "coef": float(fitted.coef_[0, 0]),
                           "intercept": float(fitted.intercept_[0]), "n_rows": int(valid.sum()),
                           "n_signal_days": frame.loc[valid, "session_date"].nunique()}
    head["calibration_status"] = "calibrated_independent_20_days"


def _metrics(frame: pd.DataFrame, prediction: np.ndarray, task: str, *, by_market: bool = True,
             holdout: bool = False) -> dict:
    y = frame["target_return"].to_numpy(dtype=float, na_value=np.nan)
    labeled = frame["evaluation_eligible"].to_numpy(dtype=bool)
    finite = np.isfinite(prediction)
    valid = labeled & finite & np.isfinite(y)
    n = len(frame)
    result = {name: None for name in ("accuracy", "holdout_accuracy", "balanced_accuracy", "brier", "log_loss", "auc", "mae", "rmse")}
    result.update({"n_denominator": n, "n_labeled": int(labeled.sum()), "n_predicted": int(finite.sum()),
                   "n_evaluated": int(valid.sum()), "n_failed": int((~finite).sum()),
                   "n_unscored": int((~valid).sum()), "coverage": int(finite.sum()) / n if n else None,
                   "evaluation_coverage": int(valid.sum()) / n if n else None,
                   "n_signal_days": frame["session_date"].nunique(),
                   "n_effective_signal_days": frame.loc[valid, "session_date"].nunique(),
                   "n_stocks": len(frame[["stock_id", "market"]].drop_duplicates()),
                   "class_counts": {"up": int((y[valid] > 0).sum()), "not_up": int((y[valid] <= 0).sum())},
                   "reason_zh": "可計算樣本以原分母列示；缺標籤或無估計者不捏造誤差。" if valid.any() else "沒有可計算誤差的有效樣本。",
                   "limitations": list(_LIMITATIONS)})
    if valid.any():
        target, pred = y[valid], prediction[valid]
        if task == "direction":
            binary = (target > 0).astype(int)
            accuracy = float(np.mean((pred >= 0.5) == binary))
            result.update(accuracy=accuracy, holdout_accuracy=accuracy if holdout else None,
                          brier=float(np.mean((pred - binary) ** 2)))
            clipped = np.clip(pred, 1e-15, 1 - 1e-15)
            result["log_loss"] = float(-np.mean(binary * np.log(clipped) + (1 - binary) * np.log1p(-clipped)))
            if np.unique(binary).size == 2:
                result["balanced_accuracy"] = float(balanced_accuracy_score(binary, pred >= 0.5))
                result["auc"] = float(roc_auc_score(binary, pred))
        else:
            error = pred - target
            result.update(mae=float(np.mean(np.abs(error))), rmse=float(np.sqrt(np.mean(error ** 2))))
    if by_market:
        result["by_market"] = {m: _metrics(g, prediction[frame.index.get_indexer(g.index)], task,
                                           by_market=False, holdout=holdout)
                               for m, g in frame.groupby("market", sort=True)}
    return _safe(result)


def _choose(candidates: dict, task: str) -> tuple[str | None, dict]:
    metric = "brier" if task == "direction" else "mae"
    eligible = []
    for kind in _KINDS:
        candidate = candidates[kind]
        score = candidate["selection"][task][metric]
        stats = candidate["selection"][task]
        complete = stats.get("n_labeled", 0) > 0 and stats.get("n_evaluated") == stats["n_labeled"]
        if candidate["heads"][task].get("selection_eligible") and complete and score is not None:
            eligible.append((score, 1 if kind == "hist_gradient_boosting" else 0,
                             _KINDS.index(kind), kind))
    selected = min(eligible)[-1] if eligible else None
    return selected, {"metric": metric, "partition": "selection", "tie_break": "linear_then_seven_reference",
                      "baseline_eligible": False, "selected_kind": selected,
                      "requires_complete_common_labeled_set": True,
                      "reason_zh": ("只依完整共同合格集合的選模資料最小 " +
                                    ("機率均方誤差（Brier）" if task == "direction" else "平均絕對誤差（MAE）") +
                                    " 選真實模型；同分優先線性模型，未看最終保留測試資料。")
                      if selected else "沒有滿足事前訊號日門檻且完整預測共同合格標籤集合的真實模型；未選取模型分支，不以基準頂替。"}


def _ledger(frame: pd.DataFrame, plan: dict) -> list[dict]:
    records = []
    for index, row in frame.iterrows():
        partition = plan["assignment"].loc[index] if plan["valid"] else "not_split"
        eligible = bool(plan["masks"][partition].loc[index]) if partition in plan.get("masks", {}) else False
        records.append({"stock_id": row["stock_id"], "market": row["market"], "session_date": _iso(row["session_date"]),
                        "label_end": _iso(row["label_end"]), "label_state": _safe(row["label_state"]),
                        "target_return": _safe(row["target_return"]), "label_eligible": bool(row["label_eligible"]),
                        "feature_ready": bool(row["feature_ready"]),
                        "label_reason": row["label_reason"], "partition": partition, "effective": eligible,
                        "exclusion_reason": None if eligible else (row["label_reason"] if not row["label_eligible"] else
                            "feature_not_ready" if not row["feature_ready"] else "purged_or_outside_effective_blocks")})
    return records


def _eval_cases(frame: pd.DataFrame, predictions: dict, baselines: dict) -> list[dict]:
    records = []
    for i, (_, row) in enumerate(frame.iterrows()):
        records.append({"stock_id": row["stock_id"], "market": row["market"], "session_date": _iso(row["session_date"]),
                        "label_end": _iso(row["label_end"]), "label_state": _safe(row["label_state"]),
                        "target_return": _safe(row["target_return"]), "evaluation_eligible": bool(row["evaluation_eligible"]),
                        "predictions": {k: {task: _safe(values[task][i]) for task in ("direction", "return")}
                                        for k, values in predictions.items()},
                        "baselines": {task: _safe(v[i]) for task, v in baselines.items()}})
    return records


def fit_candidate(prepared: dict, *, seed: int, min_fit_days: int = 60,
                  selection_days: int = 20, test_days: int = 20) -> dict:
    """Fit research candidates only, returning a fully JSON-safe sealed model.

    Horizons use common date blocks, strict label-end purge, fit-only transforms,
    optional predeclared short-H independent calibration and untouched holdout.
    Individual failures stay in the comparison; baselines can never win heads.
    """
    _require(type(seed) is int and 0 <= seed <= 2**32 - 1, "seed_invalid")
    _require(type(min_fit_days) is int and min_fit_days >= 60 and
             type(selection_days) is int and selection_days >= 20 and
             type(test_days) is int and test_days >= 20, "minimum_signal_days_cannot_be_relaxed")
    features, labels, meta = _prepared(prepared, labels_required=True)
    model = {"schema": MODEL_SCHEMA, "feature_schema": FEATURE_SCHEMA, "research_only": True,
             "target_definition": TARGET_DEFINITION, "seed": seed, "metadata": meta,
             "versions": {"sklearn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__},
             "parameters": {"linear": {"logistic_C": 1, "logistic_max_iter": 1000, "ridge_alpha": 10},
                            "hgb": dict(_HGB_PARAMS, random_state=seed), "thread_limit": THREAD_LIMIT,
                            "min_fit_days": min_fit_days, "selection_days": selection_days,
                            "test_days": test_days, "calibration_days": 20,
                            "split_rule": "global_signal_date_blocks;label_end_strictly_before_next_signal_start",
                            "feature_ready_rule": "upstream_at_least_60_observed_prices_and_valid_signal_close",
                            "candidate_failure_policy": "retain_all_denominators;no_resampling;no_baseline_substitution"},
             "limitations": list(_LIMITATIONS), "horizons": {}}
    for h in meta["horizons"]:
        frame = _horizon_frame(features, labels, meta, h)
        order = meta["feature_order_by_horizon"][str(h)]
        plan = _split_plan(frame, h, min_fit_days, selection_days, test_days)
        item = {"status": "not_trained", "reason_zh": "有效訊號日或必要資料不足，未訓練。",
                "horizon": h, "eligible_feature_order": order, "feature_order": order,
                "source_rules": {"feature_metadata": meta["feature_metadata"], "source_manifest": meta["source_manifest"],
                                 "source_plan": meta.get("source_plan_by_horizon", {}).get(str(h)),
                                 "selection_rule": "upstream_predeclared_horizon_catalog;no_final_holdout_feature_selection"},
                "feature_coverage": _coverage(frame, list(dict.fromkeys(list(SEVEN_FEATURES) + order)), plan),
                "feature_validity": {}, "effective_feature_order": [],
                "denominator": {"n_rows": len(frame), "n_label_eligible": int(frame["label_eligible"].sum()),
                                "n_feature_ready": int(frame["feature_ready"].sum()),
                                "n_stocks": len(frame[["stock_id", "market"]].drop_duplicates()),
                                "n_signal_days": frame["session_date"].nunique(),
                                "by_label_state": frame["label_state"].fillna("missing").value_counts().to_dict()},
                "calibration_status": "uncalibrated", "calibration_reason_zh": plan.get("calibration_reason_zh"),
                "splits": plan.get("blocks", {}), "sample_ledger": _ledger(frame, plan),
                "candidates": {}, "selected": {"direction": None, "return": None},
                "selection_reasons": {}, "evaluation_cases": {}, "baselines": {}, "limitations": list(_LIMITATIONS)}
        model["horizons"][str(h)] = item
        seven_finite = np.isfinite(_numeric(frame, list(SEVEN_FEATURES))).all(axis=1)
        warmup = (pd.to_numeric(frame["priced_window_observations"], errors="coerce").eq(253)
                  if "priced_window_observations" in frame else None)
        item["legacy_seven_feasibility"] = {
            "n_denominator": len(frame), "n_all_seven_finite": int(seven_finite.sum()),
            "n_verified_253_window_and_all_seven_finite": int((warmup & seven_finite).sum()) if warmup is not None else None,
            "original_artifact_read": False, "historical_score_status": "historical_non_independent_not_recomputed",
            "reason_zh": "只報七特徵全有限值的可行性；無 253 日有效價格窗證據時不宣稱原流程可訓練。"}
        if not plan["valid"]:
            item["split_failure"] = {k: v for k, v in plan.items() if k != "calibration_reason_zh"}
            for kind in _KINDS:
                item["candidates"][kind] = {"status": "not_trained", "reason_zh": "共同合法時間切分不足，候選保留但未訓練。",
                                             "n_denominator": len(frame), "split_failure": item["split_failure"]}
            continue
        fit_frame = frame.loc[plan["masks"]["fit"]]
        fit_y = fit_frame["target_return"].to_numpy(dtype=float)
        up_rate = float(np.mean(fit_y > 0)) if len(fit_y) else None
        eval_frames = {}
        for name in ("selection", "test"):
            part = frame.loc[plan["assignment"].eq(name)].copy()
            part["evaluation_eligible"] = plan["masks"][name].loc[part.index]
            eval_frames[name] = part
        all_predictions = {"selection": {}, "test": {}}
        for kind in _KINDS:
            columns = list(SEVEN_FEATURES) if kind == "seven_linear" else order
            try:
                transformer = _transformer(fit_frame, columns)
            except Exception as exc:
                candidate = {"kind": kind, "status": "not_trained", "n_denominator": len(frame),
                             "feature_order": columns, "eligible_feature_order": columns,
                             "effective_feature_order": [], "feature_validity": {},
                             "error": type(exc).__name__ + ":" + str(exc),
                             "reason_zh": "此候選的訓練資料轉換失敗；保留分母，其他候選繼續。",
                             "heads": {task: {"status": "not_trained", "kind": kind, "task": task,
                                               "error": type(exc).__name__ + ":" + str(exc),
                                               "selection_eligible": False} for task in ("direction", "return")},
                             "selection": {}, "test": {}}
                item["candidates"][kind] = candidate
                predictions = {task: np.full(len(eval_frames["selection"]), np.nan) for task in ("direction", "return")}
                for task, values in predictions.items():
                    candidate["selection"][task] = _metrics(eval_frames["selection"], values, task)
                all_predictions["selection"][kind] = predictions
                continue
            candidate = {"kind": kind, "status": "not_trained", "n_denominator": len(frame),
                         "model_name_zh": {"seven_linear": "七特徵邏輯斯迴歸（Logistic）／嶺迴歸（Ridge）受控參考",
                                           "expanded_linear": "擴充邏輯斯迴歸（Logistic）／嶺迴歸（Ridge）",
                                           "hist_gradient_boosting": "直方圖梯度提升（HistGradientBoosting）"}[kind],
                         "feature_order": columns, "eligible_feature_order": columns,
                         "effective_feature_order": transformer["effective_feature_order"],
                         "effective_indicator_order": transformer["effective_indicator_order"],
                         "feature_validity": transformer["feature_validity"],
                         "feature_coverage": _coverage(frame, columns, plan), "transformer": transformer,
                         "heads": {}, "selection": {}, "test": {},
                         "reference_kind": "controlled_new_missing_policy_not_original_artifact" if kind == "seven_linear" else None}
            item["candidates"][kind] = candidate
            for task in ("direction", "return"):
                try:
                    _, informative = _transform(fit_frame, transformer, native=kind == "hist_gradient_boosting")
                    _require(fit_frame.loc[informative, "session_date"].nunique() >= min_fit_days,
                             "insufficient_informative_fit_signal_days")
                    head = _fit_head(kind, task, fit_frame, transformer, seed)
                    if task == "direction" and plan["calibration_enabled"]:
                        try:
                            _calibrate(head, frame.loc[plan["masks"]["calibration"]], transformer, seed)
                        except Exception as exc:
                            head["calibration_status"] = "uncalibrated_calibration_failed"
                            head["calibration_error"] = type(exc).__name__ + ":" + str(exc)
                    candidate["heads"][task] = head
                except Exception as exc:
                    candidate["heads"][task] = {"status": "not_trained", "kind": kind, "task": task,
                                                 "error": type(exc).__name__ + ":" + str(exc),
                                                 "reason_zh": "此候選訓練失敗或資訊不足；保留分母，不替換股票或以基準頂替。",
                                                 "n_fit_denominator": len(fit_frame), "calibration_status": "uncalibrated"}
            candidate["status"] = ("trained" if all(v["status"] == "trained" for v in candidate["heads"].values())
                                   else "partial" if any(v["status"] == "trained" for v in candidate["heads"].values()) else "not_trained")
            # Only selection is scored before heads are locked.
            part = eval_frames["selection"]
            predictions = {}
            for task, head in candidate["heads"].items():
                try:
                    values = _head_predict(head, part, transformer)
                except Exception as exc:
                    values = np.full(len(part), np.nan)
                    head["selection_error"] = type(exc).__name__ + ":" + str(exc)
                predictions[task] = values
                stats = _metrics(part, values, task)
                candidate["selection"][task] = stats
                head["selection_eligible"] = (head["status"] == "trained" and
                                               stats["n_effective_signal_days"] >= selection_days and
                                               stats["n_labeled"] > 0 and stats["n_evaluated"] == stats["n_labeled"])
                head["selection_eligibility_reason_zh"] = (
                    "完整預測共同合格標籤集合，且有效訊號日達事前門檻，可參與自動選模。"
                    if head["selection_eligible"] else
                    "訓練、有效訊號日或共同合格標籤集合完整預測條件未成立；分數及失敗列仍完整保留，不可自動勝出。")
            all_predictions["selection"][kind] = predictions
        item["feature_validity"] = item["candidates"]["expanded_linear"]["feature_validity"]
        item["effective_feature_order"] = item["candidates"]["expanded_linear"]["effective_feature_order"]
        for task in ("direction", "return"):
            selected, why = _choose(item["candidates"], task)
            item["selected"][task], item["selection_reasons"][task] = selected, why
        item["selection_lock_sha256"] = _digest({"selected": item["selected"], "selection_reasons": item["selection_reasons"],
                                                  "selection_scores": {k: v["selection"] for k, v in item["candidates"].items()}})
        # No fitting, source/feature selection, or head switching after this line.
        for kind, candidate in item["candidates"].items():
            part, predictions = eval_frames["test"], {}
            for task, head in candidate["heads"].items():
                try:
                    values = _head_predict(head, part, candidate.get("transformer", {}))
                except Exception as exc:
                    values = np.full(len(part), np.nan)
                    head["test_error"] = type(exc).__name__ + ":" + str(exc)
                predictions[task] = values
                candidate["test"][task] = _metrics(part, values, task, holdout=True)
                candidate["test"][task]["holdout_status"] = (
                    "evaluated" if candidate["test"][task]["n_effective_signal_days"] >= test_days
                    else "insufficient_effective_test_signal_days")
            all_predictions["test"][kind] = predictions
        item["baselines"] = {"direction": {"kind": "fit_up_rate", "value": up_rate},
                              "return": {"kind": "zero_return", "value": 0.0}, "selection_eligible": False}
        item["paired_differences"] = {}
        for name, part in eval_frames.items():
            baseline_predictions = {"direction": np.full(len(part), up_rate if up_rate is not None else np.nan),
                                    "return": np.zeros(len(part))}
            item["baselines"][name] = {task: _metrics(part, v, task, holdout=name == "test") for task, v in baseline_predictions.items()}
            item["evaluation_cases"][name] = _eval_cases(part, all_predictions[name], baseline_predictions)
            item["paired_differences"][name] = {}
            for task, kind in item["selected"].items():
                metric = "brier" if task == "direction" else "mae"
                difference = None
                count = 0
                if kind is not None:
                    pred = all_predictions[name][kind][task]
                    truth = part["target_return"].to_numpy(dtype=float, na_value=np.nan)
                    paired = part["evaluation_eligible"].to_numpy() & np.isfinite(pred) & np.isfinite(truth)
                    count = int(paired.sum())
                    if count:
                        if task == "direction":
                            target = truth[paired] > 0
                            difference = float(np.mean((pred[paired] - target) ** 2 - (up_rate - target) ** 2))
                        else:
                            difference = float(np.mean(np.abs(pred[paired] - truth[paired]) - np.abs(truth[paired])))
                item["paired_differences"][name][task] = {"metric": metric, "model_minus_baseline": difference,
                                                          "n_paired": count, "n_denominator": len(part),
                                                          "worse_than_baseline": difference > 0 if difference is not None else None}
        n_heads = sum(v is not None for v in item["selected"].values())
        item["status"] = "trained" if n_heads == 2 else "partial" if n_heads else "not_trained"
        item["reason_zh"] = ("選模資料已鎖定真實模型；最終保留測試資料僅評估，尚未正式採用。" if n_heads == 2
                             else "部分或全部模型分支無法訓練／選取，缺估計保留，不以基準頂替。")
        direction_kind = item["selected"]["direction"]
        if direction_kind is not None:
            item["calibration_status"] = item["candidates"][direction_kind]["heads"]["direction"]["calibration_status"]
        item["ranking_diagnostics"] = {}
        for task, kind in item["selected"].items():
            vals = all_predictions["test"][kind][task] if kind else np.array([])
            vals = vals[np.isfinite(vals)]
            flat = bool(vals.size > 1 and np.ptp(vals) <= 1e-12)
            item["ranking_diagnostics"][task] = {"no_ranking_discrimination": flat,
                                                   "reason_zh": "真實模型預測無差異，無排序辨識力；未加入噪聲。" if flat else None}
    return _seal(model)


def _validate_transformer(transformer: dict) -> None:
    columns = transformer.get("feature_order")
    _require(isinstance(columns, list) and len(columns) == len(set(columns)) and
             all(isinstance(c, str) for c in columns), "artifact_feature_order_invalid")
    for field in ("median", "mean", "scale"):
        values = np.asarray(transformer.get(field), dtype=float)
        _require(values.shape == (len(columns),) and np.isfinite(values).all(), "artifact_transform_invalid")
        if field == "scale":
            _require((values > 0).all(), "artifact_scaler_invalid")
    for field in ("effective_feature_order", "effective_indicator_order"):
        _require(isinstance(transformer.get(field), list) and set(transformer[field]).issubset(columns),
                 "artifact_effective_features_invalid")
    validity = transformer.get("feature_validity", {})
    _require(set(validity) == set(columns), "artifact_feature_validity_missing")
    for col in columns:
        info = validity[col]
        _require(info.get("state") in ("fit_all_missing", "fit_constant", "fit_informative") and
                 type(info.get("numeric_effective")) is bool and
                 type(info.get("missing_indicator_effective")) is bool, "artifact_feature_validity_invalid")
        _require(info["numeric_effective"] == (col in transformer["effective_feature_order"]) and
                 info["missing_indicator_effective"] == (col in transformer["effective_indicator_order"]),
                 "artifact_feature_validity_binding_mismatch")


def _validate_model(model: dict) -> None:
    _require(isinstance(model, dict) and model.get("schema") == MODEL_SCHEMA and
             model.get("feature_schema") == FEATURE_SCHEMA and model.get("research_only") is True and
             model.get("target_definition") == TARGET_DEFINITION, "candidate_model_schema_mismatch")
    payload = dict(model)
    digest = payload.pop("integrity_sha256", None)
    _require(isinstance(digest, str) and digest == _digest(payload), "candidate_model_integrity_mismatch")
    _require(isinstance(model.get("horizons"), dict), "artifact_horizons_invalid")
    for h, item in model["horizons"].items():
        _require(h in {str(v) for v in SUPPORTED_HORIZONS} and item.get("status") in
                 ("trained", "partial", "not_trained"), "artifact_horizon_invalid")
        candidates = item.get("candidates", {})
        _require(set(candidates) == set(_KINDS), "artifact_comparison_candidates_missing")
        for kind, candidate in candidates.items():
            if "transformer" not in candidate:
                _require(candidate.get("status") == "not_trained", "artifact_transformer_missing")
                continue
            transformer = candidate["transformer"]
            _validate_transformer(transformer)
            for task, head in candidate["heads"].items():
                if head["status"] != "trained":
                    continue
                _require(task in ("direction", "return") and head.get("task") == task and
                         head.get("kind") == kind, "artifact_head_binding_mismatch")
                content = head["payload"]
                if kind == "hist_gradient_boosting":
                    _require(content.get("n_features") == len(transformer["feature_order"]) and
                             content.get("task") == task, "native_feature_or_task_binding_mismatch")
                    _native_trees(content)
                else:
                    coefficients = np.asarray(content.get("coef"), dtype=float)
                    _require(content.get("format") == "linear-coefficients-json-v1" and
                             content.get("task") == task and coefficients.shape == (2 * len(transformer["feature_order"]),) and
                             np.isfinite(coefficients).all() and np.isfinite(content.get("intercept", np.nan)),
                             "artifact_linear_payload_invalid")
                calibration = head.get("calibration")
                if calibration is not None:
                    _require(task == "direction" and calibration.get("kind") == "independent_platt" and
                             np.isfinite(calibration.get("coef", np.nan)) and
                             np.isfinite(calibration.get("intercept", np.nan)) and
                             calibration.get("n_signal_days", 0) >= 20, "artifact_calibration_invalid")
        for task, selected in item.get("selected", {}).items():
            _require(task in ("direction", "return") and (selected is None or
                     (selected in _KINDS and candidates[selected].get("heads", {}).get(task, {}).get("status") == "trained")),
                     "artifact_selected_head_invalid")


def _target_session(prepared: dict, meta: dict, market: str, asof: str, h: int) -> str | None:
    for container in (prepared, meta):
        for key in ("market_sessions", "market_sessions_by_market", "calendar_by_market"):
            axes = container.get(key)
            if not isinstance(axes, dict) or market not in axes:
                continue
            axis = [_day(d) for d in axes[market]]
            _require(axis == sorted(set(axis)), "future_calendar_not_unique_increasing")
            day = _day(asof)
            if day in axis and axis.index(day) + h < len(axis):
                return _iso(axis[axis.index(day) + h])
    return None


def predict_candidate(model: dict, prepared: dict) -> pd.DataFrame:
    """Predict every supplied stock/market at its market asof, without dates guessed.

    Finite model numbers with unknown future calendar remain research estimates;
    stale/missing feature rows remain in the output with explicit null estimates.
    """
    _validate_model(model)
    features, _, meta = _prepared(prepared, labels_required=False)
    _require(meta["feature_order"] == model["metadata"]["feature_order"] and
             meta["feature_order_by_horizon"] == model["metadata"]["feature_order_by_horizon"],
             "prediction_feature_order_changed")
    _require(meta["feature_metadata"] == model["metadata"]["feature_metadata"], "prediction_source_rules_changed")
    declared = {int(h) for h in model["horizons"]}
    requested = meta.get("requested_horizons", sorted(declared))
    _require(isinstance(requested, (list, tuple)) and bool(requested) and
             all(type(h) is int for h in requested) and len(requested) == len(set(requested)) and
             set(requested).issubset(declared), "requested_horizons_must_be_nonempty_artifact_subset")
    output = []
    pairs = features[["stock_id", "market"]].drop_duplicates()
    with threadpool_limits(limits=THREAD_LIMIT):
        for stock_id, market in pairs.itertuples(index=False, name=None):
            asof = meta["asof_by_market"][market]
            row = features.loc[features["stock_id"].eq(stock_id) & features["market"].eq(market) &
                               features["session_date"].eq(_day(asof))]
            for h in requested:
                item = model["horizons"][str(h)]
                target = _target_session(prepared, meta, market, asof, h)
                record = {"stock_id": stock_id, "market": market, "horizon": h, "asof": asof,
                          "signal_close": None, "target_session_date": target,
                          "target_date_status": "verified_calendar" if target else "unverified_future_calendar",
                          "prob_up": None, "expected_return": None, "prediction_status": "not_predicted",
                          "reason_zh": "未取得市場最新資料日的特徵列；不以舊日資料冒充最新預測。",
                          "calibration_status": item.get("calibration_status", "uncalibrated"),
                          "selected_direction_kind": item["selected"]["direction"],
                          "selected_return_kind": item["selected"]["return"],
                          "model_schema": MODEL_SCHEMA, "research_only": True, "deliverable": False,
                          "numeric_prediction_status": "unavailable", "no_ranking_discrimination": None}
                if len(row):
                    for col in ("signal_close", "adjusted_close", "close"):
                        if col in row:
                            close = pd.to_numeric(row[col], errors="coerce").iloc[0]
                            if pd.notna(close) and np.isfinite(close) and close > 0:
                                record["signal_close"] = float(close)
                            break
                    errors = [] if bool(row["feature_ready"].iloc[0]) else ["至少 60 筆有效歷史價格或當日收盤資格不足。"]
                    if item.get("status") == "not_trained":
                        errors.append(item["reason_zh"])
                    for task, field in (("direction", "prob_up"), ("return", "expected_return")):
                        kind = item["selected"][task]
                        if kind is None:
                            errors.append("此預測期限的" + ("方向" if task == "direction" else "報酬") + "模型未訓練或不可選取。")
                            continue
                        candidate = item["candidates"][kind]
                        values = _head_predict(candidate["heads"][task], row, candidate["transformer"])
                        record[field] = float(values[0]) if np.isfinite(values[0]) else None
                        if record[field] is None:
                            errors.append("此股票缺少訓練資料曾學到的有效特徵資訊，未估計。")
                    n = sum(record[f] is not None for f in ("prob_up", "expected_return"))
                    record["numeric_prediction_status"] = "available" if n == 2 else "partial" if n else "unavailable"
                    record["prediction_status"] = "research_estimate" if n == 2 else "partial_research_estimate" if n else "not_predicted"
                    record["reason_zh"] = " ".join(errors + (["候選模型研究估計，尚未正式採用。"] if n else []))
                    if n and target is None:
                        record["reason_zh"] += " 目標市場日未驗證；數值不等於正式可交付預測，未捏造日期。"
                output.append(record)
    columns = ["stock_id", "market", "horizon", "asof", "signal_close", "target_session_date", "prob_up",
               "expected_return", "prediction_status", "reason_zh", "calibration_status", "selected_direction_kind",
               "selected_return_kind", "target_date_status", "model_schema", "research_only", "deliverable",
               "numeric_prediction_status", "no_ranking_discrimination"]
    result = pd.DataFrame(output, columns=columns)
    result["no_ranking_discrimination"] = result["no_ranking_discrimination"].astype(object)
    for _, group in result.groupby(["horizon", "asof"], sort=False):
        comparable_heads = []
        single_sample_head = False
        has_estimate = pd.Series(False, index=group.index)
        for field in ("prob_up", "expected_return"):
            numeric = pd.to_numeric(group[field], errors="coerce")
            has_estimate |= numeric.notna()
            values = numeric.dropna().to_numpy()
            if len(values) >= 2:
                comparable_heads.append(bool(np.all(values == values[0])))
            elif len(values) == 1:
                single_sample_head = True
        if any(not flat for flat in comparable_heads):
            status = False
            reason = " 同一預測期限與資料日，至少一個模型分支的有效估計存在排序差異；不代表預測能力已驗證。"
        elif comparable_heads and not single_sample_head:
            status = True
            reason = " 同一預測期限與資料日的可比較有效估計全部相同，無排序辨識力；未加入假差異。"
        else:
            status = None
            reason = " 同一預測期限與資料日，至少一個模型分支的可比較有效估計不足兩筆，無法判定排序辨識力。"
        estimated_index = group.index[has_estimate]
        result.loc[estimated_index, "no_ranking_discrimination"] = status
        result.loc[estimated_index, "reason_zh"] += reason
        result.loc[group.index[~has_estimate], "reason_zh"] += " 此股票無有效估計，無法判定其排序辨識力。"
    # Object columns preserve literal None for strict JSON consumers.
    return result.astype(object).where(pd.notna(result), None)


def save_candidate(model: dict, path: Path) -> dict:
    """Write candidate JSON and <filename>.sha256; no current pointer is touched."""
    _validate_model(model)
    path = Path(path)
    _require(path.name.lower() != "current.json", "current_pointer_write_forbidden")
    data = _json_bytes(model) + b"\n"
    digest = hashlib.sha256(data).hexdigest()
    sidecar = path.with_suffix(path.suffix + ".sha256")
    path.write_bytes(data)
    sidecar.write_text(digest + "\n", encoding="utf-8", newline="\n")
    return {"path": str(path), "sidecar_path": str(sidecar), "sha256": digest,
            "integrity_sha256": model["integrity_sha256"], "schema": MODEL_SCHEMA}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def load_candidate(path: Path) -> dict:
    """Hash-check and structurally validate JSON before any native prediction."""
    path = Path(path)
    _require(path.stat().st_size <= 512 * 1024 * 1024, "candidate_artifact_too_large")
    data = path.read_bytes()
    expected = path.with_suffix(path.suffix + ".sha256").read_text(encoding="utf-8").strip()
    _require(len(expected) == 64 and all(c in "0123456789abcdef" for c in expected) and
             hashlib.sha256(data).hexdigest() == expected, "candidate_sidecar_hash_mismatch")
    try:
        model = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=lambda token: (_ for _ in ()).throw(CandidateModelError("nonfinite_json_number:" + token)))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateModelError("candidate_json_invalid") from exc
    _validate_model(model)
    return model
