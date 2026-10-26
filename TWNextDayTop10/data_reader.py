"""Thin current-only adapter for pinned shared C0 inputs; never train or fetch."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from daily_contract import (
    DEFAULT_CONTRACT_MODULE, prepare_c0_reader, runtime_report_bindings, shared_module, version_warnings,
)

READER_VERSION = "top10-current-c0-shared-v1"
TAIPEI = ZoneInfo("Asia/Taipei")


def check_inputs(project_dir=None, *, runtime_binding=None):
    """Check six declared routes/current proofs without building price panels."""
    prepare_c0_reader()
    contract = shared_module(DEFAULT_CONTRACT_MODULE, "top10_shared_daily_contract")
    return contract.load_c0_published_context(consumer_id="top10", runtime_binding=runtime_binding)


def load_c0_inputs(project_dir=None, *, runtime_binding=None):
    """Use one shared pin and current identity; no local historical-data fallback."""
    prepare_c0_reader()
    contract = shared_module(DEFAULT_CONTRACT_MODULE, "top10_shared_daily_contract")
    loaded = contract.load_c0_published_panel(None, consumer_id="top10",
        runtime_binding=runtime_binding, top10_single_build=True)
    identity = loaded["current_identity"]
    if identity.get("verified") is not True:
        raise ValueError("affected_output:current_official_identity_unverified")
    records = identity["records"]
    universe = pd.DataFrame([dict(
        stock_id=str(record["stock_id"]), market=str(record["market"]).upper(),
        name=record.get("stock_name"), industry=record.get("source_industry"),
        ordinary_share_verified=True, eligibility_status="verified_ordinary_share",
        classification_source=record["classification_source"],
        classification_asof=record["classification_verified_asof"],
        classification_observed_at=record["classification_observed_at"],
        classification_effective_date=None, classification_history_verified=False,
    ) for record in records])
    if universe.empty or universe.duplicated(["market", "stock_id"]).any():
        raise ValueError("affected_output:current_ordinary_pool_empty_or_conflicting")
    coverage = loaded["coverage"]
    if coverage["requested"] != len(universe):
        raise ValueError("affected_output:current_ordinary_scope_accounting_mismatch")
    if coverage["eligible"] + len(coverage["excluded"]) != coverage["requested"]:
        raise ValueError("affected_output:current_ordinary_exclusions_accounting_mismatch")
    panel, axes = loaded["panel"], loaded["market_sessions"]
    if panel.empty:
        raise ValueError("affected_output:C0_no_qualified_published_price_scope")
    # Version labels from the shared reader/model (and Top10's own) travel to meta.warnings.
    labels = [dict(label) for label in (loaded.get("version_labels") or []) if isinstance(label, dict)]
    for label in (runtime_binding or {}).get("version_labels") or []:
        if isinstance(label, dict) and label not in labels:
            labels.append(dict(label))
    bindings = runtime_report_bindings(loaded, labels)
    descriptor, source_binding = loaded["descriptor"], loaded["source_binding"]
    identity_asof = identity["classification_verified_asof"]
    meta = dict(
        asof=loaded["asof"], inference_run_time=datetime.now(TAIPEI).isoformat(),
        snapshot_fingerprint=descriptor["snapshot_fingerprint"],
        shared_input_descriptor=descriptor, c0_source_binding=source_binding,
        c0_scope_coverage=coverage, ordinary_universe_status="verified",
        universe_asof=identity_asof, universe_basis="immutable_current_official_positive_records",
        universe_count=len(universe), price_rows=len(panel),
        raw_status=source_binding["route_statuses"]["finmind_price_adj"],
        required_route_evidence=source_binding["required_route_evidence"],
        readiness=source_binding["readiness"],
        ordinary_identity_binding=identity["binding"],
        ordinary_identity_verified_asof=identity_asof,
        ordinary_identity_observed_at=identity["classification_observed_at"],
        historical_universe_verified=False, historical_universe_status="not_verified",
        excluded_universe=coverage["excluded"],
        unsupported_calendar_markets=sorted(set(universe.market) - {str(market).upper() for market in axes}),
        warnings=["C0 僅調整後收盤方向與報酬；歷史身分、PIT、公司行動與交易效益未驗。", *_data_time_warnings(loaded.get("data_time")), *version_warnings(labels)],
        data_time=loaded.get("data_time"), version_labels=labels,
        qa_override=bool((runtime_binding or {}).get("artifact_binding", {}).get("qa_override")),
        input_override=None, reader_version=READER_VERSION, **bindings,
    )
    return dict(loaded, universe=universe, metadata=meta)


def _data_time_warnings(data_time):
    """A stale or older-than-requested analysis is stated, never hidden."""
    data_time = data_time or {}
    lines = []
    if data_time.get("is_stale"):
        lines.append("非最新資料：分析資料日 " + str(data_time.get("analysis_asof")) +
                     "，依已驗證日曆最近應有交易日 " + str(data_time.get("latest_expected_session")))
        for market, pair in sorted((data_time.get("stale_markets") or {}).items()):
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                # The lagging market is analysed with its own day, not dropped.
                lines.append("落後市場 " + str(market) + "：以自身資料日 " + str(pair[0]) +
                             " 分析（應有 " + str(pair[1]) + "）")
    if data_time.get("is_older_than_requested"):
        lines.append("資料日早於要求日：分析 " + str(data_time.get("analysis_asof")) +
                     "；要求 " + str(data_time.get("requested_asof")))
    if data_time.get("freshness_status") == "calendar_unverified_after":
        lines.append("日曆已驗證至 " + str(data_time.get("calendar_verified_asof")) + " 之後的交易日未驗證，無法判定是否最新")
    return lines
