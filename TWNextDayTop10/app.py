"""H1 次日報告：C0 推論與前瞻帳冊照常執行；開盤前版快照可用且新鮮時 H1 顯示開盤前版，否則回退 C0。

不在本機訓練任何模型；開盤前版只透過共用唯讀 reader 讀取（preopen_source.py）。
"""
from __future__ import annotations

import argparse
from datetime import datetime
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import webbrowser
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from data_reader import check_inputs, load_c0_inputs
from daily_contract import MODEL_ROUTES, load_c0_model, runtime_report_bindings
from paths import ENGINE_ID, runtime_paths
import preopen_source
from progress import progress_session, stage
from reporting import excel_runtime, flat_report_path, report

PROJECT = Path(__file__).resolve().parent
TAIPEI = ZoneInfo("Asia/Taipei")


def now():
    return datetime.now(TAIPEI)

def predict(project=None, namespace_root=None, no_open=False, *, c0_model=None, c0_forward_db=None, model_source=None):
    """Shared C0 coefficients predict H1 adjusted-close returns; no fitting occurs.

    ``model_source``：auto（預設；開盤前版可用且新鮮才用，否則回退 C0）、preopen（必須用開盤前版）、
    c0（強制舊行為）。未指定時讀環境變數 TOP10_MODEL_SOURCE。C0 推論與前瞻帳冊在三種模式都照常執行。
    """
    from daily_contract import (load_c0_model, bind_daily_snapshot,
        shared_module, DEFAULT_CONTRACT_MODULE)
    requested_source, source_origin = preopen_source.resolve_model_source(model_source)
    project = Path(project or PROJECT)
    paths = runtime_paths(project, namespace_root)
    with stage("讀取共用 C0 模型"):
        module, model, artifact, active = load_c0_model(c0_model)
    contract = shared_module(DEFAULT_CONTRACT_MODULE, "top10_shared_daily_contract")
    with stage("讀取已發布 C0 普通股及253交易日行情"):
        inputs = load_c0_inputs(project, runtime_binding=active)
        axes, universe, meta = inputs["market_sessions"], inputs["universe"], inputs["metadata"]
        evidence, binding = inputs["evidence"], inputs["policy_binding"]
    # 新鮮度：以本次 C0 輸入的台股最新交易日為 expected_signal_date；preopen 模式不可用時在寫入前瞻帳冊前失敗。
    with stage("讀取開盤前版 H1 快照（唯讀）"):
        choice = preopen_source.choose(requested_source, source_origin, meta["asof"])
    with stage("共用 C0 次日方向與報酬預測"):
        # Each market is built with its OWN analysed day (a lagging market is analysed,
        # not dropped); one common day is exactly the previous single build_panel call.
        _, forecast = contract.predict_c0_by_market(model, inputs, horizons=[1], policy_binding=binding, batch_pairs=64)
    predicted = forecast.loc[forecast.prediction_status.eq("predicted") & forecast.prob_up.notna()].copy()
    if predicted.empty:
        raise RuntimeError("affected_output:C0_no_valid_model_predictions")
    market_days = {str(market).upper(): str(day)[:10] for market, day in (inputs.get("asof_by_market") or {}).items()}
    base_by_market, target_by_market = market_sessions(predicted, market_days, meta["asof"])
    targets = sorted({value for value in target_by_market.values() if value is not None})
    verified_targets = all(value is not None for value in target_by_market.values())
    target = targets[0] if len(targets) == 1 and verified_targets else None
    # Every market's own analysed day, including a market none of whose rows was predicted
    # (its input window still ends on its own day, never on another market's later day).
    base_by_market = {**market_days, **base_by_market}
    per_market = len(set(base_by_market.values())) > 1
    if per_market and target is None and verified_targets:
        # Markets analysed on different days: each H1 target is its own verified next
        # session; the report is filed under the latest of them (all are shown).
        target = max(target_by_market.values())
    future_status = "verified_declared_session" if target is not None else "not_yet_verified"
    # 已驗證的下一交易日存在時，開盤前版目標日必須一致（auto 回退 C0；preopen 失敗）。
    choice = preopen_source.confirm_target(choice, target)
    print(choice.note(), flush=True)
    statuses = {(row.market, row.stock_id): row.prediction_status for row in forecast.itertuples()}
    exclusions = {(str(item["market"]).upper(), str(item["stock_id"])): item["reason"]
                  for item in meta["c0_scope_coverage"]["excluded"]}
    coverage = universe.copy()
    coverage["status"] = [statuses.get((row.market, row.stock_id), exclusions.get((row.market, row.stock_id)))
                          for row in coverage.itertuples()]
    if coverage["status"].isna().any():
        raise RuntimeError("affected_output:C0_forecast_pool_accounting_incomplete")
    with stage("共用 C0 前瞻觀測與校準版本"):
        forward = contract.process_c0_forward(forecast, inputs, active, db_path=c0_forward_db)
        forecast = pd.DataFrame(forward["predictions"])
    from daily_contract import version_warnings
    forward_labels = []
    for label in (forward.get("forward_binding") or {}).get("version_labels") or []:
        if isinstance(label, dict) and label not in meta.setdefault("version_labels", []) and label not in forward_labels:
            forward_labels.append(label)
    meta["version_labels"].extend(forward_labels)
    meta.setdefault("warnings", []).extend(line for line in version_warnings(forward_labels)
                                           if line not in meta["warnings"])
    predicted = forecast.loc[forecast.prediction_status.eq("predicted") & forecast.prob_up.notna()].copy()
    predictions = predicted.rename(columns={"session_date": "date", "prob_up": "probability"})
    predictions["model_source"] = "c0"

    names = universe.set_index(["market", "stock_id"])["name"].to_dict()
    predictions["name"] = [names.get((row.market, row.stock_id)) for row in predictions.itertuples()]
    predictions["engine_id"] = ENGINE_ID
    predictions["model_schema"] = model["schema"]
    predictions["base_model_version"] = active["artifact_binding"]["sha256"]
    predictions["data_snapshot"] = meta["snapshot_fingerprint"]
    predictions["contribution_mode"] = "report_only"
    head = model.get("models_by_horizon", {}).get("1", {})
    selected_direction = head.get("direction_kind", "logistic")
    selected_return = head.get("return_kind", "ridge")
    direction_no_discrimination = predictions.probability.nunique() <= 1
    return_no_discrimination = predictions.expected_return.nunique() <= 1
    all_tied = len(predictions) > 1 and direction_no_discrimination and return_no_discrimination
    baseline_selected = "baseline" in str(selected_direction) or "baseline" in str(selected_return)
    baseline_only = selected_direction == "fit_up_rate_baseline" and selected_return == "zero_return_baseline"
    ranking_status = "all_tied_baseline" if all_tied and baseline_selected else "market_baseline_no_stock_discrimination" if baseline_only else "all_tied_predictions" if all_tied else "model_values_displayed_effect_unknown"
    ranking_note = "基準同值，無個股排序訊號" if ranking_status == "all_tied_baseline" else "市場基準率不同，無個股排序訊號" if baseline_only else "預測同值，無個股排序訊號" if all_tied else "依模型數值展示；排序效果與交易效益未驗"
    # C0 自身的排序狀態保留為 c0_*（備援與追溯）；開盤前版啟用時，報告的排序欄位改為開盤前版。
    c0_ranking = dict(c0_ranking_status=ranking_status, c0_ranking_note=ranking_note,
        c0_selected_direction_kind=selected_direction, c0_selected_return_kind=selected_return,
        c0_direction_no_discrimination=direction_no_discrimination,
        c0_return_no_discrimination=return_no_discrimination, c0_no_stock_specific_model_signal=baseline_only)
    report_predictions, report_coverage = predictions, coverage
    display_direction, display_return = selected_direction, selected_return
    warnings = meta.setdefault("warnings", [])
    if choice.active:
        report_predictions, report_coverage = preopen_source.build_view(
            choice.snapshot, predictions, universe, coverage, engine_id=ENGINE_ID)
        ranking_status, ranking_note = preopen_source.PREOPEN_RANKING_STATUS, preopen_source.PREOPEN_RANKING_NOTE
        display_direction, display_return = preopen_source.PREOPEN_DIRECTION_KIND, preopen_source.PREOPEN_RETURN_KIND
        direction_no_discrimination = report_predictions.probability.nunique() <= 1
        return_no_discrimination = report_predictions.expected_return.nunique() <= 1
        baseline_only = False
        warnings.append(choice.note())
        warnings.append("C0 備援排序狀態：" + c0_ranking["c0_ranking_note"])
    else:
        if choice.status == "unavailable":
            warnings.insert(0, choice.note())
        warnings.append(ranking_note)
    model_fields = preopen_source.report_fields(choice)
    qa_overrides = dict(model=bool(active["artifact_binding"]["qa_override"]),
        namespace_root=str(paths.root) if namespace_root is not None else None,
        forward_db=str(c0_forward_db) if c0_forward_db is not None else None,
        visible_report_root=os.environ.get("TOP10_VISIBLE_REPORT_ROOT"),
        policy_path=os.environ.get("STOCK_DAILY_REQUIREMENTS_PATH"),
        policy_module=os.environ.get("STOCK_DAILY_POLICY_MODULE_PATH"),
        preopen_root=os.environ.get(preopen_source.PREOPEN_ROOT_ENV))
    meta.update(qa_overrides=qa_overrides, qa_override=any(qa_overrides.values()))
    moment = now()
    current_bindings = runtime_report_bindings(active)
    verified_calendar = all(evidence["calendar_verified_by_market"].get(str(market).upper()) is True for market in axes)
    metadata = dict(model_id=artifact.stem, model_version=active["artifact_binding"]["sha256"], model_schema=model["schema"],
        model_artifact=str(artifact), model_artifact_sha256=active["artifact_binding"]["sha256"],
        feature_schema=model["metadata"]["feature_schema"], feature_columns=model["metadata"]["feature_order"],
        target_definition=model["metadata"]["target_definition"],
        training_scope=model["metadata"]["source_evidence"],
        training_by_horizon=model["metadata"].get("horizon_training"), metrics=model.get("metrics"),
        selection_by_horizon=model["metadata"].get("selection_by_horizon"),
        selected_direction_kind=selected_direction, selected_return_kind=selected_return,
        active_model_binding=active["artifact_binding"], forward_binding=forward["forward_binding"],
        training_pit_qualified=False, formal_qualified=False)
    metadata.update(current_bindings, qa_overrides=qa_overrides, qa_override=meta["qa_override"])
    demand = dict(effective_profile="C0", requested_profile="C0", requirements_profile_id="C0",
        requirements_profile_scope="actual_model_input_policy", c0_model_compatible=True,
        model_identity=dict(model_id=metadata["model_id"], model_schema=model["schema"],
            artifact_sha256=metadata["model_artifact_sha256"], feature_schema=metadata["feature_schema"]))
    if per_market:
        meta.update(analysis_asof_by_market=dict(base_by_market), target_session_by_market=dict(target_by_market))
    daily = bind_daily_snapshot(meta, demand, meta["asof"], asof_by_market=base_by_market if per_market else None)
    daily["actual_model_inputs"] = dict(feature_columns=list(module.FEATURE_ORDER),
        required_routes=list(MODEL_ROUTES),
        input_windows={market: {"sessions": 253, "end_date": base_by_market.get(str(market).upper(), meta["asof"])}
                       for market in axes},
        feature_schema=metadata["feature_schema"], model_identity=demand["model_identity"])
    training_pairs = model["metadata"]["source_evidence"].get("eligible_stock_market_pairs", [])
    meta.update(daily_requirements_binding=daily, actual_model_inputs=daily["actual_model_inputs"],
        next_session=target, calendar_verified=target is not None,
        historical_calendar_verified=verified_calendar, future_calendar_status=future_status,
        next_session_status=future_status,
        target_status="H1研究預測；目標日期未驗證" if target is None else "未來交易日研究預測",
        c0_inference_rows=forecast.to_dict("records"),
        ranking_status=ranking_status, ranking_note=ranking_note,
        direction_no_discrimination=direction_no_discrimination, return_no_discrimination=return_no_discrimination,
        no_stock_specific_model_signal=baseline_only, forward_binding=forward["forward_binding"],
        **c0_ranking, **model_fields,
        c0_training_sample_limit=dict(stock_count=len(training_pairs), stock_market_pairs=training_pairs,
            effect_status="unknown", scope="shared_model_training_sample_only"),
        historical_membership_status="unknown", pit_status="unknown", corporate_actions_status="unknown")
    calibrated = predicted.calibration_status.eq("independent_calibration_fitted_reliability_unverified").all()
    overlay_versions = sorted(set(str(value) for value in forecast.get("calibration_version", []) if str(value) != "none"))
    context = dict(engine_id=ENGINE_ID, run_id="run_" + moment.strftime("%Y%m%dT%H%M%S%f") + "_" + uuid4().hex[:8],
        target_session=target, target_date=target, base_session=meta["asof"], analysis_asof=meta["asof"],
        future_calendar_status=future_status,
        ranking_status=ranking_status, ranking_note=ranking_note,
        selected_direction_kind=display_direction, selected_return_kind=display_return,
        direction_no_discrimination=direction_no_discrimination, return_no_discrimination=return_no_discrimination,
        no_stock_specific_model_signal=baseline_only, **c0_ranking,
        forward_binding=forward["forward_binding"], forward_calibration_versions=overlay_versions,
        data_time=meta.get("data_time"), version_labels=meta.get("version_labels", []),
        inference_run_time=meta["inference_run_time"], model_id=metadata["model_id"], model_version=metadata["model_version"],
        model_artifact_sha256=metadata["model_artifact_sha256"], snapshot_fingerprint=meta["snapshot_fingerprint"],
        price_basis="adjusted_close_return", target_definition=metadata["target_definition"],
        horizon=1, horizon_unit="market_trading_session", effective_profile="C0",
        daily_requirements_binding=daily, actual_model_inputs=daily["actual_model_inputs"],
        ordinary_identity_verified_asof=meta["ordinary_identity_verified_asof"],
        ordinary_identity_observed_at=meta["ordinary_identity_observed_at"],
        calibration_qualified=False, calibration_status="前瞻校準已套用（可靠度未證）" if overlay_versions else "已校準（可靠度未證）" if calibrated else "未校準",
        effect_status="效果未定", trading_effect_status="交易效益未驗", formal_qualified=False,
        calendar_verified=target is not None, historical_calendar_verified=verified_calendar,
        universe_verified=True, pit_verified=False, price_rules_verified=False,
        qualification_reasons=["historical_membership_unknown", "model_training_pit_unknown", "corporate_actions_unknown", "effects_unknown"])
    if per_market:
        # 各市場自己的分析日只描述 C0 輸入；開盤前版啟用時改記為 c0_*，基準日以開盤前版訊號日為準。
        prefix = "c0_" if choice.active else ""
        context.update({prefix + "base_session_by_market": dict(base_by_market),
                        prefix + "target_session_by_market": dict(target_by_market),
                        prefix + "market_asof_mode": "each_market_own_asof"})
    context.update(model_fields)
    if choice.active:
        preopen_meta = model_fields["preopen_meta"]
        context.update(calibration_status=preopen_source.PREOPEN_CALIBRATION_STATUS,
            c0_calibration_status=context["calibration_status"],
            h1_model_version=preopen_meta.get("model_sha256"), h1_model_id=preopen_meta.get("model_id"),
            h1_target_definition=preopen_source.PREOPEN_TARGET_DEFINITION)
    meta.update(current_bindings)
    context.update(current_bindings, qa_overrides=qa_overrides, qa_override=meta["qa_override"])
    label = "開盤前版 H1 Top10" if choice.active else "C0 次日方向與報酬"
    with stage("產生" + label + "報告"):
        path = report(project, report_predictions, report_coverage, meta, metadata, moment, namespace_root=paths.root,
            report_context=context, performance_state={"status": "research_effect_unknown"})
    visible_report = flat_report_path(project, context["target_session"], analysis_asof=meta["asof"], profile="C0")
    if choice.active:
        print("開盤前版 H1 可交易 Top10 研究報告已產生（C0 前瞻紀錄照常保留為備援）：" + str(visible_report), flush=True)
    else:
        print("C0 H1 方向與調整後收盤報酬研究報告已產生：" + str(visible_report), flush=True)
    if not no_open:
        os.startfile(visible_report) if os.name == "nt" else webbrowser.open(visible_report.as_uri())
    return path

def market_sessions(predicted, asof_by_market, asof):
    """Keep analysis days separate from actual feature days and historical H1 targets."""
    base, targets = {}, {}
    for market, rows in predicted.groupby(predicted["market"].astype(str).str.upper(), sort=True):
        expected = asof_by_market.get(market, asof)
        analysis = rows["analysis_asof"] if "analysis_asof" in rows else rows["session_date"]
        days = sorted({str(value)[:10] for value in analysis})
        if days != [expected]:
            raise RuntimeError("affected_output:C0_market_base_session_differs_from_market_asof:" + market)
        signal_days = rows["session_date"].astype(str).str[:10]
        feature = rows["feature_data_date"].fillna(rows["session_date"]) if "feature_data_date" in rows else rows["session_date"]
        feature_days = feature.astype(str).str[:10]
        if feature_days.gt(expected).any() or signal_days.gt(expected).any():
            raise RuntimeError("affected_output:C0_feature_after_market_asof:" + market)
        base[market] = expected
        current = rows.loc[feature_days.eq(expected) & signal_days.eq(expected)]
        if "data_lagged" in current:
            current = current.loc[~current["data_lagged"].eq(True)]
        values = set(current["target_session_date"].dropna())
        targets[market] = (next(iter(values)) if len(values) == 1 and current["target_session_date"].notna().all()
                           else None)
    return base, targets


def check(namespace_root=None, model_source=None):
    """Validate the canonical C0 artifact and six-route context without inference.

    另以唯讀方式檢查開盤前版快照是否可用且新鮮（不寫任何檔案）；model source = preopen 且不可用時失敗。
    """
    requested_source, source_origin = preopen_source.resolve_model_source(model_source)
    print("Python：" + sys.version.split()[0] + "；" + sys.executable, flush=True)
    expected = PROJECT / ".venv" / "Scripts" / "python.exe"
    if Path(sys.executable).resolve() != expected.resolve():
        raise RuntimeError("請使用本專案.venv執行CHECK_ENV.cmd。")
    for package in ("pandas", "numpy", "scikit-learn", "duckdb"):
        print(package + " " + importlib.metadata.version(package), flush=True)
    excel_runtime(PROJECT)
    _, model, artifact, active = load_c0_model()
    checked = check_inputs(PROJECT, runtime_binding=active)
    summary = dict(model_schema=model["schema"], artifact=str(artifact),
        artifact_binding=active["artifact_binding"], pointer_binding=active["pointer_binding"],
        current_bindings=runtime_report_bindings(active), asof=checked["asof"],
        published_input_descriptor=checked["descriptor"],
        required_route_evidence=checked["required_route_evidence"], readiness=checked["readiness"],
        data_time=checked.get("data_time"), version_labels=checked.get("version_labels", []))
    print(json.dumps(summary, ensure_ascii=False, default=str), flush=True)
    print("共用 C0 已驗證；來源原始狀態保留，未進行預測、訓練或前瞻帳冊寫入。", flush=True)
    summary["preopen"] = check_preopen(checked["asof"], requested_source, source_origin)
    return summary


def check_preopen(expected_signal_date, requested_source="auto", source_origin="default"):
    """CHECK_ENV 的開盤前版快照可用性檢查（唯讀）。"""
    preopen = preopen_source.check_summary(requested_source, source_origin, expected_signal_date)
    print(json.dumps({"preopen": preopen}, ensure_ascii=False, default=str), flush=True)
    print(preopen["line"], flush=True)
    if preopen["required_unavailable"]:
        raise RuntimeError("affected_output:preopen_required_unavailable:" + str(preopen["reason"]))
    return preopen


def main(argv=None):
    parser = argparse.ArgumentParser(description="普通股次日（H1）收盤方向與報酬分析：開盤前版優先，C0 備援")
    parser.add_argument("command", choices=["predict", "check"], nargs="?", default="predict")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--c0-model", help="隔離 QA 模型 override；正常預設使用 shared active pointer")
    parser.add_argument("--c0-forward-db", help="隔離 QA forward DB override；正常預設使用 canonical ledger")
    parser.add_argument("--namespace-root", help="隔離 QA 產物根；不改正常資料／模型來源")
    parser.add_argument("--model-source", choices=preopen_source.MODEL_SOURCES, default=None,
        help="H1 模型來源：auto（預設；開盤前版可用且新鮮才用，否則改用 C0）、preopen（必須用開盤前版，"
             "不可用即失敗）、c0（強制舊行為）；未指定時讀環境變數 " + preopen_source.MODEL_SOURCE_ENV)
    args = parser.parse_args(argv)
    try:
        preopen_source.resolve_model_source(args.model_source)
    except ValueError as exc:
        parser.error(str(exc))
    with progress_session():
        if args.command == "check":
            if any((args.c0_model, args.c0_forward_db)):
                parser.error("check 只驗正式 shared C0，不接受 QA 模型／forward override")
            with stage("檢查共用 C0、已發布來源與開盤前版快照"):
                check(args.namespace_root, model_source=args.model_source)
        else:
            with stage("執行次日預測"):
                predict(namespace_root=args.namespace_root, no_open=args.no_open,
                    c0_model=args.c0_model,
                    c0_forward_db=args.c0_forward_db,
                    model_source=args.model_source)


if __name__ == "__main__":
    main()
