"""Current-head selected-38 research inputs, assembled in memory only.

This module never builds projections, downloads data, saves data frames, or
reads research/fixture row caches. Existing readers retain their normal
short-lived lifecycle leases. ``progress`` receives one dictionary per event.
Historical inputs are not clipped to the single-prediction lookback window.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import multiprocessing
import os
import re
import sys
import threading
import time
from bisect import bisect_left, insort
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

SHARED = Path(__file__).resolve().parents[2] / "shared"
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))

import stock_daily_contract as contract
import stock_daily_candidate_runtime as runtime
import stock_daily_feature_catalog as catalog
import stock_daily_price_quality as price_quality
import stock_query_projection as query

PUBLISHED_ROOT = Path(r"D:\TWStockData\shared_market_data")
REQUIREMENTS_PATH = Path(r"D:\TWStockData\contracts\shared_market_data\requirements_v1.json")
_BOND_CATALOG_POINTER = "/capability_catalog/series_catalogs/catalogs/declared_bond_series_catalog"
KEYS = ["stock_id", "market", "session_date"]
RETIRED_ROUTES = frozenset({"finmind_options_institutional", "finmind_trading_daily_report",
    "official_active_etf_holding", "official_active_etf_change"})
_FINANCIAL = frozenset({"finmind_month_revenue", "finmind_financial_statements",
    "finmind_balance_sheet", "finmind_cash_flows_statement"})
_MACRO = frozenset({"global_market_history", "finmind_exchange_rate", "finmind_government_bonds_yield"})
_BATCH_STOCKS = 8
_OUTPUT_SESSIONS = 84
_WARMUP_SESSIONS = 252
_CHILD_IO_GUARD_INSTALLED = False
_COMMON = frozenset({"stock_id", "data_id", "code", "market", "date", "session_date",
    "observation_date", "report_date", "date_id", "period_end", "available_time",
    "available_at", "available_at_utc", "published_at", "published_at_utc", "announcement_time",
    "source_close_at", "unit", "units", "lot_size", "volume_unit", "percentage_unit", "currency_unit",
    "_research_available_time_fields", "_research_original_available_time"})
_PUBLICATION_CLOCKS = ("available_time", "available_at", "available_at_utc", "published_at",
    "published_at_utc", "announcement_time", "source_close_at")
_FIELDS = {
    "finmind_price_adj": {"open", "close", "spread", "Trading_Volume", "high", "low", "max", "min", "_shared_collected_at"},
    "official_market_daily": {"open", "high", "low", "close", "volume", "amount", "transactions"},
    "finmind_institutional": {"foreign_investors_net", "investment_trust_net", "dealer_net",
        "Foreign_Investor_buy", "Foreign_Investor_sell", "Investment_Trust_buy", "Investment_Trust_sell", "Dealer_buy", "Dealer_sell"},
    "finmind_margin": {"MarginPurchaseTodayBalance", "MarginPurchaseYesterdayBalance", "margin_purchase_balance",
        "margin_purchase_change", "ShortSaleTodayBalance", "ShortSaleYesterdayBalance", "short_sale_balance", "short_sale_change"},
    "finmind_day_trading": {"day_trade_volume", "Volume"},
    "finmind_shareholding": {"holding_percentage", "ForeignInvestmentSharesRatio"},
    "finmind_per": {"per", "PER", "pbr", "PBR", "dividend_yield"},
    "finmind_month_revenue": {"revenue_year", "revenue_month", "month_revenue_year", "month_revenue_month", "revenue", "revenue_yoy"},
    "finmind_financial_statements": {"type", "value"},
    "finmind_balance_sheet": {"type", "value"},
    "finmind_cash_flows_statement": {"type", "value", "cash_flow_value", "period_basis", "period_start", "period_end"},
    "global_market_history": {"symbol", "instrument", "adj_close", "close"},
    "finmind_exchange_rate": {"symbol", "instrument", "currency", "name", "spot_buy"},
    "finmind_government_bonds_yield": {"symbol", "instrument", "currency", "name", "value"},
}
_TEXT_FIELDS = _COMMON | {"type", "period_basis", "period_start", "symbol", "instrument",
    "currency", "name", "_shared_collected_at"}
_CONCEPTS = {
    "finmind_financial_statements": {"Revenue", "OperatingIncome"},
    "finmind_balance_sheet": {"TotalAssets", "TotalLiabilities", "Assets", "Liabilities"},
    "finmind_cash_flows_statement": {"ifrs-full:CashFlowsFromUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities"},
}
_FINANCIAL_FEATURES = {
    "finmind_month_revenue": "revenue_yoy",
    "finmind_financial_statements": "financial_operating_margin",
    "finmind_balance_sheet": "balance_debt_ratio",
    "finmind_cash_flows_statement": "cashflow_operating_change_ratio",
}
_SPARSE_FEATURE_ROUTES = {
    "foreign_net_relative_20": "finmind_institutional",
    "trust_net_relative_20": "finmind_institutional",
    "dealer_net_relative_20": "finmind_institutional",
    "day_trade_relative_20": "finmind_day_trading",
    "global_sp500_return_1": "global_market_history",
    "global_nasdaq_return_1": "global_market_history",
    "global_sox_return_1": "global_market_history",
    "exchange_rate_change_1": "finmind_exchange_rate",
    "bond_yield_change_1": "finmind_government_bonds_yield",
}


class ResearchInputError(ValueError):
    """Inputs cannot be assembled without inventing their identity or meaning."""


def _notify(progress, stage, **values):
    if progress is not None:
        progress(dict(stage=stage, **values))


def _proof_file(path):
    raw = _stable_bytes(path)
    return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))


def _stable_bytes(path):
    return contract._stable_file_bytes(path)


def _current_manifest(expected_head_sha256=None):
    raw = _stable_bytes(PUBLISHED_ROOT / "_metadata" / "stock_data_current.json")
    digest = hashlib.sha256(raw).hexdigest()
    if expected_head_sha256 is not None:
        expected = str(expected_head_sha256).lower()
        if re.fullmatch("[0-9a-f]{64}", expected) is None:
            raise ValueError("expected_head_sha256 must be a SHA-256 digest")
        if expected != digest:
            raise ResearchInputError("current_head_does_not_match_expected:" + digest)
    manifest = contract._identity_json(raw)
    if manifest.get("schema") not in {"stock-data-publication-v1", "stock-data-publication-v2"}:
        raise ResearchInputError("current_publication_schema_unsupported")
    return manifest, digest


def _bind_bond_identity(item, entry, frame, head_sha256, entry_index):
    """Bind identity only; receipt retrieval clocks are never publication clocks."""
    route = "finmind_government_bonds_yield"

    def check(condition, reason):
        if not condition:
            raise ResearchInputError("bond_identity_" + reason)

    check(item.get("route_id") == entry.get("route_id") == route, "route_mismatch")
    check(re.fullmatch(r"[0-9a-f]{64}", str(head_sha256)) is not None
          and type(entry_index) is int and entry_index >= 0, "head_binding_missing")
    obj = item.get("object", {})
    check(all(obj.get(key) == entry.get(key) and entry.get(key) is not None
              for key in ("sha256", "size")), "artifact_binding_mismatch")
    try:
        raw = _stable_bytes(REQUIREMENTS_PATH)
    except OSError as exc:
        raise ResearchInputError("bond_identity_contract_unavailable") from exc
    digest = hashlib.sha256(raw).hexdigest()
    metadata = entry.get("metadata", {})
    registry = metadata.get("route_window_policy", {}).get("registry_binding", {})
    check(registry.get("sha256") == digest and registry.get("size") == len(raw),
          "contract_head_binding_mismatch")
    document = contract._identity_json(raw)
    declared = document
    for key in _BOND_CATALOG_POINTER.strip("/").split("/"):
        check(isinstance(declared, dict) and key in declared, "catalog_missing")
        declared = declared[key]
    check(isinstance(declared, dict) and declared.get("catalog_id") == "declared_bond_series_catalog"
          and declared.get("route_id") == route and declared.get("provider") == "finmind"
          and declared.get("dataset") == "GovernmentBondsYield", "catalog_identity_mismatch")
    mapping = declared.get("selector_mapping", {})
    check(all(mapping.get(key) == value for key, value in dict(normalized_field="name",
          provider_field="name", request_parameter="data_id").items()), "selector_mapping_mismatch")
    receipts = metadata.get("source_receipts")
    check(isinstance(receipts, list) and bool(receipts), "receipts_missing")
    identities, receipt_proofs = set(), []
    for number, receipt in enumerate(receipts):
        check(isinstance(receipt, dict) and receipt.get("dataset") == declared["dataset"]
              and isinstance(receipt.get("data_id"), str) and bool(receipt["data_id"]),
              "receipt_identity_missing")
        check(re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("sha256"))) is not None
              and type(receipt.get("size")) is int and receipt["size"] > 0, "receipt_proof_missing")
        scope = receipt.get("source_scope", {})
        check(isinstance(scope, dict) and (not scope or scope.get("route_id") == route),
              "receipt_route_mismatch")
        request = scope.get("request_identity", {})
        check(isinstance(request, dict), "receipt_identity_missing")
        for identity in (receipt, request):
            check(all(key not in identity or identity[key] == receipt[key]
                      for key in ("dataset", "data_id")), "receipt_identity_conflict")
        identities.add(receipt["data_id"])
        receipt_proofs.append(dict(json_pointer=f"/datasets/{entry_index}/metadata/source_receipts/{number}",
            dataset=receipt["dataset"], data_id=receipt["data_id"],
            sha256=receipt["sha256"], size=receipt["size"]))
    check(len(identities) == 1, "receipt_series_conflict")
    selected = next(iter(identities))
    matches = [series for series in declared.get("items", []) if isinstance(series, dict)
               and series.get("provider_data_id") == selected]
    check(len(matches) == 1 and bool(matches[0].get("series_id"))
          and matches[0].get("selector", {}).get("data_id") == selected, "declared_series_mismatch")
    check("name" in frame and set(frame["name"].astype(str)) == {selected}, "raw_name_mismatch")
    # Match the catalog's exact symbol/instrument/currency/name precedence.
    symbols = {str(row.get("symbol", row.get("instrument", row.get("currency", row.get("name", "")))))
               for row in frame.to_dict("records")}
    check(symbols == {selected}, "raw_series_mismatch")
    result = copy.deepcopy(item)
    result.update(selected_instrument=selected, instrument_verified=True,
        instrument_identity_binding=dict(series_id=matches[0]["series_id"], provider_data_id=selected,
            raw_match_field="name", publication_time_verified_by_identity=False,
            contract=dict(path=str(REQUIREMENTS_PATH), sha256=digest, size=len(raw),
                          json_pointer=_BOND_CATALOG_POINTER),
            head=dict(path=str(PUBLISHED_ROOT / "_metadata" / "stock_data_current.json"),
                      sha256=head_sha256, json_pointer=f"/datasets/{entry_index}"),
            source_receipts=receipt_proofs))
    return result


def _source_plan(entries, horizons):
    plan = catalog.select_source_plan(list(horizons))
    known = {item["route_id"] for item in plan}
    for route in sorted(set(entries) - known):
        plan.append(dict(route_id=route, name_zh=route, category="unmapped", usage="context-only",
            reason_zh="Current published route has no selected-38 numeric adapter."))
    return plan


def _query_rows(*args, **kwargs):
    return query.query_published_rows(*args, **kwargs)


def _new_read_statistics():
    return dict(rows_seen=0, rows_kept=0, other_stocks=0, unused_concepts=0,
        unverified_publication_time=0, unused_instruments=0, publication_clock_fields={})


def _normalise_publication_clock(row):
    clocks = []
    for field in _PUBLICATION_CLOCKS:
        value = row.get(field)
        if value is None or re.search(r"[T ]\d{2}:\d{2}", str(value)) is None:
            continue
        try:
            stamp = pd.Timestamp(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if not pd.isna(stamp) and stamp.tzinfo is not None:
            clocks.append((field, stamp.tz_convert("Asia/Taipei")))
    result = dict(row)
    if clocks:
        if "available_time" in row:
            result["_research_original_available_time"] = row["available_time"]
        result["available_time"] = max(stamp for _, stamp in clocks).isoformat()
        result["_research_available_time_fields"] = tuple(field for field, _ in clocks)
    return result


def _project_rows(rows, route, stock_ids, statistics):
    allowed = _COMMON | _FIELDS[route]
    for row in rows:
        statistics["rows_seen"] += 1
        if not isinstance(row, dict):
            raise ResearchInputError("source_row_not_object:" + route)
        stock = row.get("stock_id", row.get("data_id", row.get("code")))
        if route not in _MACRO and (stock is None or str(stock) not in stock_ids):
            statistics["other_stocks"] += 1
            continue
        if route in _CONCEPTS and row.get("type") not in _CONCEPTS[route]:
            statistics["unused_concepts"] += 1
            continue
        row = _normalise_publication_clock(row)
        for field in row.get("_research_available_time_fields", ()):
            counts = statistics["publication_clock_fields"]
            counts[field] = counts.get(field, 0) + 1
        if route in _FINANCIAL and catalog._timestamp(row) is None:
            statistics["unverified_publication_time"] += 1
            continue
        if route == "global_market_history":
            symbol = str(row.get("symbol", row.get("instrument", "")))
            if symbol not in {"^GSPC", "us_sp500", "^IXIC", "us_nasdaq", "^SOX", "us_semiconductor"}:
                statistics["unused_instruments"] += 1
                continue
        projected = {name: value for name, value in row.items()
            if name in allowed or name.endswith("_unit") and name[:-5] in allowed | {"volume", "percentage", "currency"}}
        statistics["rows_kept"] += 1
        yield projected


def _normalise_frame(rows, route):
    frame = runtime._normalise_frame(rows)
    if frame.empty:
        return pd.DataFrame(columns=sorted(_COMMON | _FIELDS[route]))
    if "date" in frame:
        frame["date"] = pd.to_datetime(frame["date"].map(runtime._day), errors="coerce")
    for name in _FIELDS[route] - _TEXT_FIELDS:
        if name in frame:
            frame[name] = pd.to_numeric(frame[name], errors="coerce").replace([np.inf, -np.inf], np.nan)
    return frame


def _read_route(sources, item, route, stock_ids):
    """Read one full-history route once; no consumer cache or index creation."""
    statistics = _new_read_statistics()
    selected = None
    try:
        selected = _query_rows(item["root"], item["artifact_descriptor"], route,
            stock_ids=None if route in _MACRO else sorted(stock_ids), start_date=None, end_date=None,
            source_identity=item.get("source_identity"), parent_identity=item.get("parent_identity"),
            allow_build=False, consumer_temp_root=None)
    except (query.UnsupportedProjectionError, query.ProjectionUnavailableError):
        pass
    if selected is not None:
        try:
            proofs = {"index": _proof_file(selected.index_manifest_path),
                "complete": _proof_file(selected.completion_path)}
            with contextlib.closing(selected.iter_rows(order="stock_shard")) as iterator:
                frame = _normalise_frame(list(_project_rows(iterator, route, stock_ids, statistics)), route)
            for name, path in (("index", selected.index_manifest_path), ("complete", selected.completion_path)):
                if _proof_file(path) != proofs[name]:
                    raise ResearchInputError("query_proof_changed:" + route)
            binding = dict(reader="existing_verified_projection", cache_key=selected.cache_key,
                predicate=selected.predicate, lineage=selected.lineage, files=proofs, metrics=dict(selected.metrics))
        finally:
            selected.close()
        return frame, binding, statistics
    descriptor = item["artifact_descriptor"]
    frame, artifacts = None, []
    with contextlib.closing(sources.iter_artifact_rows(route)) as stream:
        for artifact, rows in stream:
            with contextlib.closing(rows):
                if artifact.content_sha256 != descriptor["sha256"]:
                    raise ResearchInputError("stream_source_hash_mismatch:" + route)
                frame = _normalise_frame(list(_project_rows(rows, route, stock_ids, statistics)), route)
                reference = (artifact.metadata or {}).get("selected_ref") or {}
                if reference.get("sha256") != descriptor["sha256"] or reference.get("size") != descriptor["size"]:
                    raise ResearchInputError("stream_source_size_or_hash_unverified:" + route)
                artifacts.append(dict(sha256=artifact.content_sha256, size=reference["size"]))
    if len(artifacts) != 1 or frame is None:
        raise ResearchInputError("stream_source_missing_or_ambiguous:" + route)
    return frame, dict(reader="verified_full_stream", artifacts=artifacts), statistics


def _load_snapshot(stack, horizons, progress, expected_head_sha256=None):
    started = time.monotonic()
    head_path = PUBLISHED_ROOT / "_metadata" / "stock_data_current.json"
    manifest, digest = _current_manifest(expected_head_sha256)
    _notify(progress, "source_head_pinned", head_sha256=digest)
    entries = {entry["route_id"]: entry for entry in manifest["datasets"]}
    if len(entries) != len(manifest["datasets"]) or not runtime.CORE_ROUTES.issubset(entries):
        raise ResearchInputError("pinned_route_duplicate_or_core_route_missing")
    context = runtime._load_context("single", pinned_head_sha256=digest)
    sources = context["sources"]
    stack.callback(sources.close)
    identity = contract.load_current_ordinary_identity(sources, decision_asof=context["asof"])
    records = identity["records"]
    ids = {str(row["stock_id"]) for row in records}
    if not ids:
        raise ResearchInputError("verified_current_ordinary_universe_empty")
    asofs = {market.upper(): pd.Timestamp(day).normalize() for market, day in
        (context.get("analysis_asof_by_market") or context["asof_by_market"]).items()}
    axes = {market.upper(): pd.DatetimeIndex(days) for market, days in context["market_sessions"].items()}
    verified = context["calendar_evidence"]["calendar_verified_by_market"]
    if any(verified.get(str(row["market"]).upper()) is not True for row in records):
        raise ResearchInputError("ordinary_market_calendar_unverified")
    plan = _source_plan(entries, horizons)
    code_bindings = {name: _proof_file(path) for name, path in {
        "research_adapter": __file__, "feature_catalog": catalog.__file__,
        "candidate_reader": runtime.__file__, "published_contract": contract.__file__,
        "price_quality": price_quality.__file__, "query_reader": query.__file__,
    }.items()}
    frames, audits, manifests, route_times, read_statistics = {}, [], [], {}, {}
    for number, selection in enumerate(plan, 1):
        route = selection["route_id"]
        entry = entries.get(route)
        audit = dict(route_id=route, current=entry is not None, usage=selection["usage"],
            classification="current" if entry is not None else "retired" if route in RETIRED_ROUTES else "not_current",
            publication_status=entry.get("status") if entry is not None else "not_current",
            actual_min_date=entry.get("actual_min_date") if entry is not None else None,
            actual_max_date=entry.get("actual_max_date") if entry is not None else None,
            declared_rows=entry.get("record_count") if entry is not None else None,
            artifact={name: entry[name] for name in ("path", "sha256", "size")} if entry is not None else None,
            payload_verification="not_read", reason=selection["reason_zh"])
        if entry is None:
            audit.update(usage="not-selected", reason="not_in_current_pinned_contract; no historical signal invented")
            manifests.append(dict(selection, usage="not-selected", status="not_current"))
        elif route in {"official_calendar", "finmind_stock_info"}:
            evidence = context["calendar_evidence"] if route == "official_calendar" else identity["binding"]
            audit.update(payload_verification="verified_context_evidence", evidence=evidence)
            manifests.append(dict(selection, object=audit["artifact"], status=entry["status"]))
        elif selection["usage"] != "used":
            audit.update(reason="current metadata audited; selected-38 has no qualified historical numeric adapter")
            manifests.append(dict(selection, object=audit["artifact"], status=entry["status"]))
        else:
            items = tuple(sources.projection_artifacts(route))
            if (len(items) != 1 or items[0].get("parent_identity") != digest
                    or Path(items[0]["root"]).resolve() != PUBLISHED_ROOT.resolve()):
                raise ResearchInputError("route_not_bound_to_current_head:" + route)
            begin = time.monotonic()
            _notify(progress, "read", route_id=route, position=number, total=len(plan))
            frame, binding, statistics = _read_route(sources, items[0], route, ids)
            route_times[route] = time.monotonic() - begin
            frames[route] = frame
            read_statistics[route] = statistics
            feature_manifest = runtime._availability(selection, items[0], frame, binding, sorted(ids))
            if route == "finmind_government_bonds_yield":
                feature_manifest = _bind_bond_identity(feature_manifest, entry, frame, digest,
                    manifest["datasets"].index(entry))
                audit["instrument_identity_binding"] = feature_manifest["instrument_identity_binding"]
            manifests.append(feature_manifest)
            audit.update(payload_verification=binding["reader"], read_binding=binding,
                read_statistics=statistics, projected_rows=len(frame), usage=feature_manifest["usage"],
                historical_available_time=feature_manifest["historical_available_time"], elapsed_seconds=route_times[route])
        audits.append(audit)
    return dict(frames=frames, records=records, market_sessions=axes, asof_by_market=asofs,
        source_audit=audits, source_manifest=manifests, read_statistics=read_statistics,
        calendar_evidence=context["calendar_evidence"], sources=sources,
        bindings=dict(head_sha256=digest, head_path=str(head_path), publication_run_id=manifest["run_id"],
            published_at=manifest["published_at"], current_identity=identity["binding"],
            calendar=context["calendar_evidence"]["binding"], retention=context["application_scope"]["binding"],
            historical_membership_verified=False, historical_pit_verified=False,
            input_policy="complete_current_pinned_history_no_derived_panel_reuse",
            current_route_ids=sorted(entries), expected_head_sha256=expected_head_sha256,
            code=code_bindings, libraries=dict(pandas=pd.__version__, numpy=np.__version__),
            retained_inputs_used=False, derived_panel_reuse=False,
            storage_policy="no_raw_parquet_index_or_permanent_cache_writes; existing_reader_leases_only"),
        timings=dict(read_seconds=time.monotonic() - started, routes=route_times))


def _group_index(frame):
    if frame is None or frame.empty or "stock_id" not in frame:
        return {}
    return frame.groupby("stock_id", sort=False).indices


def _stock_rows(frame, positions, stock):
    if frame is None:
        return pd.DataFrame()
    indices = positions.get(stock)
    return frame.iloc[indices].copy() if indices is not None else frame.iloc[:0].copy()


def _price_panel(price, board, record, axis, asof):
    """Use the existing integrity checks, retaining unavailable stocks as NaN."""
    stock, market = str(record["stock_id"]), str(record["market"]).upper()
    required = {"open", "close", "spread", "Trading_Volume", "_shared_collected_at", "date", "stock_id"}
    if not price.empty and not required.issubset(price):
        raise ResearchInputError("price_quality_columns_missing:" + stock)
    if not board.empty and not {"date", "stock_id", "market", "close", "volume"}.issubset(board):
        raise ResearchInputError("official_quality_columns_missing:" + stock)
    price, board = price.copy(), board.copy()
    for frame in (price, board):
        if not frame.empty:
            frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
            if frame["date"].isna().any():
                raise ResearchInputError("price_business_date_unknown:" + stock)
            if frame.duplicated(["stock_id", "date"]).any():
                raise ResearchInputError("duplicate_price_or_official_key:" + stock)
    if "market" in board:
        board = board.loc[board["market"].astype(str).str.upper().eq(market)]
    reasons, warnings = [], []
    if price.empty:
        reasons.append("price_data_unavailable")
    if board.empty:
        reasons.append("official_price_comparison_unavailable")
    for frame in (price, board):
        if not frame.empty:
            within = frame["date"].between(axis[0], asof)
            if (~frame.loc[within, "date"].isin(axis)).any():
                raise ResearchInputError("price_outside_verified_market_sessions:" + stock)
    price = price.loc[price["date"].isin(axis)] if "date" in price else price
    board = board.loc[board["date"].isin(axis)] if "date" in board else board
    if price.empty and "price_data_unavailable" not in reasons:
        reasons.append("price_data_unavailable")
    if board.empty and "official_price_comparison_unavailable" not in reasons:
        reasons.append("official_price_comparison_unavailable")
    if not reasons:
        try:
            check = price_quality.series_checks(price, board, asof, expected_sessions=axis)
            reasons.extend(code for code in check["reasons"].get(stock, []) if code != "missing_rows")
            warnings = check.get("version_labels") or []
        except price_quality.PriceSeriesQualityError as exc:
            reasons.append(str(exc))
    out = pd.DataFrame(index=axis)
    out["stock_id"], out["market"], out["session_date"] = stock, market, axis
    px = price.set_index("date").reindex(axis) if "date" in price else pd.DataFrame(index=axis)
    off = board.set_index("date").reindex(axis) if "date" in board else pd.DataFrame(index=axis)
    for target, aliases in {"adjusted_close": ("close",), "open": ("open",),
            "high": ("high", "max"), "low": ("low", "min")}.items():
        field = next((name for name in aliases if name in px), None)
        values = pd.to_numeric(px[field], errors="coerce") if field is not None else np.nan
        out[target] = np.nan if reasons else values
    invalid = out.adjusted_close.notna() & (~np.isfinite(out.adjusted_close) | out.adjusted_close.le(0))
    if invalid.any():
        raise ResearchInputError("adjusted_close_not_finite_positive:" + stock)
    for field in ("volume", "amount", "transactions"):
        out[field] = pd.to_numeric(off[field], errors="coerce") if field in off else np.nan
    for field in ("open", "high", "low", "close"):
        out["raw_" + field] = pd.to_numeric(off[field], errors="coerce") if field in off else np.nan
    out["volume_unit"] = "shares"
    return out.reset_index(drop=True), dict(stock_id=stock, market=market,
        reason_codes=sorted(set(reasons)), version_labels=warnings,
        observed_sessions=int(out.adjusted_close.notna().sum()), expected_sessions=len(axis),
        missing_prices_retained=True, imputed_prices=False)


def _feature_windows(axis, output_sessions=None):
    """Each output date appears once, with sufficient independent warmup."""
    start = 0
    while start < len(axis):
        end = min(start + (output_sessions or _OUTPUT_SESSIONS) - 1, len(axis) - 1)
        warmup = axis[max(0, start - _WARMUP_SESSIONS)]
        while end > start and axis[end] - pd.DateOffset(months=18) > warmup:
            end -= 1
        yield start, end
        start = end + 1


def _financial_events(frame, item, stock, market):
    """Prepare only four low-frequency concepts; never rewrite source dates."""
    route = item["route_id"]
    events = []
    if frame is None or frame.empty or item["usage"] != "used":
        return events
    for row in frame.loc[frame.stock_id.astype(str).eq(stock)].to_dict("records"):
        row_market = str(row.get("market", "")).upper()
        if row_market not in {"", "NAN", "NONE", "<NA>", market}:
            continue
        at = catalog._timestamp(row)
        try:
            day = catalog._day(row.get("date", row.get("session_date")))
        except ValueError:
            continue
        if at is None or day > at.tz_localize(None).normalize():
            continue
        event = dict(day=day.value, at=at.value, year=day.year)
        if route == "finmind_month_revenue":
            year = catalog._value(row, "revenue_year", "month_revenue_year")
            month = catalog._value(row, "revenue_month", "month_revenue_month")
            event.update(period=(int(year), int(month)) if np.isfinite(year) and np.isfinite(month) else None,
                value=catalog._value(row, "revenue"), direct=catalog._fraction(row, item, "revenue_yoy"))
        elif route in {"finmind_financial_statements", "finmind_balance_sheet"}:
            event.update(concept=str(row.get("type", "")), value=catalog._value(row, "value"),
                unit=catalog._unit(row, item, "value", "currency"))
        else:
            if str(row.get("type", "")) not in _CONCEPTS[route]:
                continue
            try:
                start, end = row.get("period_start"), row.get("period_end")
                span = (catalog._day(start).strftime("%m-%d"), catalog._day(end).strftime("%m-%d")) if start is not None and end is not None else None
            except ValueError:
                span = None
            event.update(basis=str(row.get("period_basis", "")), span=span,
                value=catalog._value(row, "cash_flow_value", "value"))
        events.append(event)
    return sorted(events, key=lambda event: (event["at"], event["day"]))


def _financial_state(events, route, lower, cutoff):
    """Evaluate catalog financial formulas on one exact signal-day window."""
    state, latest = {}, np.nan
    latest_at = np.iinfo(np.int64).min
    for event in events:
        if event["at"] > cutoff:
            break
        if event["day"] < lower:
            continue
        value, event_at = np.nan, event["at"]
        if route == "finmind_month_revenue":
            value = event["direct"]
            period = event["period"]
            if period is not None and np.isfinite(event["value"]):
                previous = state.get((period[0] - 1, period[1]))
                if previous is not None:
                    value = catalog._ratio(event["value"] - previous, abs(previous))
                state[period] = event["value"]
        elif route == "finmind_cash_flows_statement":
            key = (event["basis"], event["span"], event["year"] - 1)
            if event["basis"] and event["span"] is not None:
                if key in state:
                    value = catalog._ratio(event["value"] - state[key], abs(state[key]))
                if np.isfinite(event["value"]):
                    state[(event["basis"], event["span"], event["year"])] = event["value"]
        else:
            period = state.setdefault(event["day"], {})
            if np.isfinite(event["value"]):
                period[event["concept"]] = (event["value"], event["unit"], event["at"])
            pairs = [("Revenue", "OperatingIncome")] if route == "finmind_financial_statements" else [
                ("TotalAssets", "TotalLiabilities"), ("Assets", "Liabilities")]
            for denominator, numerator in pairs:
                if numerator in period and denominator in period:
                    top, bottom = period[numerator], period[denominator]
                    if top[1] and top[1] == bottom[1] and top[1] not in {"%", "percent"}:
                        value = catalog._ratio(top[0], bottom[0])
                        event_at = max(top[2], bottom[2])
        # Catalog ignores non-finite financial events; it does not zero/reset them.
        if np.isfinite(value) and event_at >= latest_at:
            latest, latest_at = value, event_at
    return latest


def _financial_history(frames, manifests, stock, market, axis):
    """Recompute only at explicit publication or 18-month expiry boundaries."""
    history = pd.DataFrame(np.nan, index=axis, columns=list(_FINANCIAL_FEATURES.values()))
    cuts = (axis + pd.Timedelta(hours=13, minutes=30)).tz_localize("Asia/Taipei").asi8
    lower = (axis - pd.DateOffset(months=18)).asi8
    manifest_map = {item["route_id"]: item for item in manifests}
    evaluations = 0
    for route, column in _FINANCIAL_FEATURES.items():
        item = manifest_map.get(route, dict(route_id=route, usage="not-selected"))
        events = _financial_events(frames.get(route), item, stock, market)
        if not events:
            continue
        boundaries = {0, len(axis)}
        for event in events:
            boundaries.add(int(cuts.searchsorted(event["at"], side="left")))
            boundaries.add(int(lower.searchsorted(event["day"], side="right")))
        boundaries = sorted(boundaries)
        for start, stop in zip(boundaries, boundaries[1:]):
            if start == stop:
                continue
            value = _financial_state(events, route, lower[start], cuts[start])
            history.iloc[start:stop, history.columns.get_loc(column)] = value
            evaluations += 1
    return history, evaluations


def _sparse_series(frames, manifests, stock=None, market=None, axis=None):
    """Keep finite events and the oldest required predecessor, not raw copies."""
    result = {}
    manifest_map = {item["route_id"]: item for item in manifests}
    routes = _MACRO if stock is None else {"finmind_institutional", "finmind_day_trading"}
    for route in routes:
        frame, item = frames.get(route), manifest_map.get(route, {})
        if frame is None or frame.empty or item.get("usage") != "used":
            continue
        selected = frame if stock is None else frame.loc[frame.stock_id.astype(str).eq(stock)]
        instrument = item.get("selected_instrument")
        if route == "finmind_exchange_rate" and not instrument:
            instrument = "USD"
        if (route in _MACRO - {"global_market_history"} and
                not (route == "finmind_exchange_rate" and instrument == "USD") and
                item.get("instrument_verified") is not True):
            instrument = None
        lag = item.get("availability_lag_sessions", 1)
        lag = lag if type(lag) is int and lag >= 1 else 1
        prepared = []
        for row in selected.to_dict("records"):
            if stock is not None and str(row.get("market", "")).upper() not in {"", "NAN", "NONE", "<NA>", market}:
                continue
            try:
                day = catalog._day(row.get("date", row.get("session_date")))
            except ValueError:
                continue
            at = catalog._timestamp(row)
            if at is None and stock is not None:
                position = axis.get_indexer([day])[0]
                if position >= 0 and position + lag < len(axis):
                    at = (axis[position + lag] + pd.Timedelta(hours=13, minutes=30)).tz_localize("Asia/Taipei")
            if at is not None and day <= at.tz_localize(None).normalize():
                prepared.append((at.value, day.value, row))
        prior = {}
        for at, day, row in sorted(prepared, key=lambda item: (item[0], item[1])):
            measures = []
            if route == "finmind_institutional":
                for prefix, field, buy, sell in (
                        ("foreign", "foreign_investors_net", "Foreign_Investor_buy", "Foreign_Investor_sell"),
                        ("trust", "investment_trust_net", "Investment_Trust_buy", "Investment_Trust_sell"),
                        ("dealer", "dealer_net", "Dealer_buy", "Dealer_sell")):
                    value = catalog._value(row, field)
                    if not np.isfinite(value):
                        value = catalog._value(row, buy) - catalog._value(row, sell)
                    unit = catalog._unit(row, item, field)
                    measures.append((prefix + "_net_relative_20", (prefix, unit), value, 20))
            elif route == "finmind_day_trading":
                field = "day_trade_volume" if "day_trade_volume" in row else "Volume"
                measures.append(("day_trade_relative_20", (field, catalog._unit(row, item, field)),
                    catalog._value(row, field), 20))
            else:
                symbol = str(row.get("symbol", row.get("instrument", row.get("currency", row.get("name", "")))))
                if route == "global_market_history":
                    name = {"^GSPC": "global_sp500_return_1", "us_sp500": "global_sp500_return_1",
                        "^IXIC": "global_nasdaq_return_1", "us_nasdaq": "global_nasdaq_return_1",
                        "^SOX": "global_sox_return_1", "us_semiconductor": "global_sox_return_1"}.get(symbol)
                    field = "adj_close" if "adj_close" in frame else "close"
                else:
                    if not instrument or symbol != instrument:
                        continue
                    name, field = {"finmind_exchange_rate": ("exchange_rate_change_1", "spot_buy"),
                        "finmind_government_bonds_yield": ("bond_yield_change_1", "value")}[route]
                if name is not None:
                    measures.append((name, (symbol, field), catalog._value(row, field), 1))
            for name, key, value, count in measures:
                if not np.isfinite(value):
                    continue
                days, values = prior.setdefault(key, ([], {}))
                end = bisect_left(days, day)
                if end >= count:
                    needed_days = days[end - count:end]
                    previous = [values[old] for old in needed_days]
                    if count == 20:
                        estimate = catalog._ratio(value, np.mean(np.abs(previous)))
                    else:
                        estimate = value - previous[-1] if route == "finmind_government_bonds_yield" else catalog._ratio(value, previous[-1]) - 1
                    if np.isfinite(estimate):
                        series = result.setdefault(name, dict(events=[], max_age=lag if stock is not None else None))
                        series["events"].append((at, day, needed_days[0], estimate))
                if day not in values:
                    insort(days, day)
                values[day] = value
    for series in result.values():
        series["times"] = np.array([event[0] for event in series["events"]], dtype=np.int64)
    return result


def _same_numeric(left, right):
    return bool(np.isclose(left, right, rtol=1e-12, atol=1e-14, equal_nan=True))


def _sparse_expected(series, lower, cutoff, signal_position, positions):
    end = int(series["times"].searchsorted(cutoff, side="right"))
    for number in range(end - 1, -1, -1):
        event = series["events"][number]
        if event[2] < lower:
            continue
        value = event[3]
        if series["max_age"] is not None and signal_position - positions.get(event[1], -1) > series["max_age"]:
            value = np.nan
        return value, event[2]
    return None


def _plan_sparse_fallback(features, sequences, macro_sequences, axis, chunk_lower, macro_expected):
    """A candidate requires an actual value change and a proven lost seed."""
    positions = {day.value: i for i, day in enumerate(axis)}
    plans = {}
    for row in features.itertuples(index=False):
        signal, stock = row.session_date, row.stock_id
        lower = (signal - pd.DateOffset(months=18)).value
        cutoff = (signal + pd.Timedelta(hours=13, minutes=30)).tz_localize("Asia/Taipei").value
        for name, series in {**macro_sequences, **sequences.get(stock, {})}.items():
            cache_key = (name, signal.value)
            if name in macro_sequences:
                if cache_key not in macro_expected:
                    macro_expected[cache_key] = _sparse_expected(series, lower, cutoff, positions[signal.value], positions)
                expected = macro_expected[cache_key]
            else:
                expected = _sparse_expected(series, lower, cutoff, positions[signal.value], positions)
            if expected is None or expected[1] >= chunk_lower.value or _same_numeric(expected[0], getattr(row, name)):
                continue
            plans.setdefault((stock, signal), {})[name] = expected[0]
    return plans


def _direct_sparse_features(panel, frames, manifests, axis, market, signal, horizons, columns):
    routes = {_SPARSE_FEATURE_ROUTES[name] for name in columns}
    selected_frames = {route: frame for route, frame in frames.items() if route in routes}
    prepared = catalog.build_candidate_features(panel, {market: axis},
        asof_by_market={market: signal.strftime("%Y-%m-%d")}, horizons=list(horizons),
        source_frames=selected_frames, source_manifest=manifests)
    features = prepared["features"]
    row = features.loc[pd.to_datetime(features.session_date).eq(signal)]
    if len(row) != 1:
        raise ResearchInputError("sparse_fallback_signal_row_missing_or_ambiguous")
    return {name: float(row.iloc[0][name]) for name in columns}


def _apply_sparse_fallback(features, panel, frames, manifests, axis, market, horizons, plans, shared):
    started = time.monotonic()
    statistics = dict(candidate_rows=len(plans), rows=0, overwritten_cells=0, catalog_calls=0,
        macro_cache_hits=0, catalog_seconds=0.0)
    for stock in {key[0] for key in plans}:
        if sum(key[0] == stock for key in plans) == int(features.stock_id.eq(stock).sum()):
            raise ResearchInputError("sparse_fallback_would_recompute_every_signal:" + stock)
    for (stock, signal), expected in plans.items():
        stock_panel = panel.loc[panel.stock_id.eq(stock) & panel.session_date.between(
            signal - pd.DateOffset(months=18), signal)].copy()
        actual = {}
        macro_columns = {name for name in expected if _SPARSE_FEATURE_ROUTES[name] in _MACRO}
        if macro_columns:
            with shared["lock"]:
                key = (market, signal)
                cached = shared["macro_values"].setdefault(key, {})
                missing = macro_columns - set(cached)
                if missing:
                    begin = time.monotonic()
                    cached.update(_direct_sparse_features(stock_panel, frames, manifests, axis, market,
                        signal, horizons, missing))
                    statistics["catalog_seconds"] += time.monotonic() - begin
                    statistics["catalog_calls"] += 1
                else:
                    statistics["macro_cache_hits"] += 1
                actual.update({name: cached[name] for name in macro_columns})
        relative_columns = set(expected) - macro_columns
        if relative_columns:
            selected_frames = {route: frame.loc[frame.stock_id.astype(str).eq(stock)]
                for route, frame in frames.items() if route in {_SPARSE_FEATURE_ROUTES[name] for name in relative_columns}}
            begin = time.monotonic()
            actual.update(_direct_sparse_features(stock_panel, selected_frames, manifests, axis, market,
                signal, horizons, relative_columns))
            statistics["catalog_seconds"] += time.monotonic() - begin
            statistics["catalog_calls"] += 1
        if any(not _same_numeric(actual[name], value) for name, value in expected.items()):
            raise ResearchInputError("sparse_state_guard_disagrees_with_direct_catalog")
        selected = features.stock_id.eq(stock) & features.session_date.eq(signal)
        for name, value in actual.items():
            features.loc[selected, name] = value
        statistics["rows"] += 1
        statistics["overwritten_cells"] += len(actual)
    statistics["apply_seconds"] = time.monotonic() - started
    return statistics


def _macro_manifest(manifests, macro):
    return [dict(item, usage=item["usage"] if ((item["route_id"] in _MACRO) == macro)
                 else "not-selected") for item in manifests]


def _macro_history(panel, snapshot, axis, market, horizons, shared, progress):
    """One catalog evaluation per market/window, independent of security prices."""
    key = (market, axis[0].value, axis[-1].value, tuple(horizons), _OUTPUT_SESSIONS)
    with shared["lock"]:
        cache = shared.setdefault("macro_history", {})
        owner = key not in cache
        if owner:
            cache[key] = Future()
        future = cache[key]
    if not owner:
        _notify(progress, "macro_history_wait", market=market)
        return future.result(), True
    started = time.monotonic()
    try:
        stock = str(panel.stock_id.iloc[0])
        one_stock = panel.loc[panel.stock_id.eq(stock)].copy()
        frames = {route: frame for route, frame in snapshot["frames"].items() if route in _MACRO}
        manifests = _macro_manifest(snapshot["source_manifest"], True)
        columns = [name for name, definition in catalog._definitions().items() if definition["route"] in _MACRO]
        source_axis = pd.DatetimeIndex(snapshot["market_sessions"][market])
        windows = list(_feature_windows(axis))
        parts, plans, expected, limits, assumptions = [], {}, {}, {}, {}
        guard_seconds = 0.0
        _notify(progress, "macro_history_start", market=market, windows=len(windows),
            source_rows={route: len(frame) for route, frame in frames.items()})
        for number, (first, last) in enumerate(windows, 1):
            begin = time.monotonic()
            cutoff = axis[last]
            lower = cutoff - pd.DateOffset(months=18)
            window = one_stock.loc[one_stock.session_date.between(lower, cutoff)]
            prepared = catalog.build_candidate_features(window, {market: source_axis},
                asof_by_market={market: cutoff.strftime("%Y-%m-%d")}, horizons=list(horizons),
                source_frames=frames, source_manifest=manifests)
            feature = prepared["features"]
            days = pd.to_datetime(feature.session_date)
            feature = feature.loc[days.between(axis[first], cutoff), KEYS + columns].copy()
            feature["session_date"] = pd.to_datetime(feature.session_date)
            guard_begin = time.monotonic()
            plans.update(_plan_sparse_fallback(feature, {}, shared["macro_series"],
                source_axis, lower, expected))
            guard_seconds += time.monotonic() - guard_begin
            parts.append(feature)
            for item in prepared["metadata"]["limitations"]:
                if item["route_id"] in _MACRO:
                    limits.setdefault((item["route_id"], item["code"]), item)
            for item in prepared["metadata"].get("availability_assumptions", []):
                if item["route_id"] in _MACRO:
                    assumptions[item["route_id"]] = item
            _notify(progress, "macro_window_finished", market=market, completed_windows=number,
                total_windows=len(windows), window_seconds=time.monotonic() - begin,
                elapsed_seconds=time.monotonic() - started)
        features = pd.concat(parts, ignore_index=True)
        sparse = _apply_sparse_fallback(features, one_stock, frames, manifests, source_axis,
            market, horizons, plans, shared)
        sparse.update(prepare_seconds=0.0, guard_seconds=guard_seconds,
            elapsed_seconds=guard_seconds + sparse["apply_seconds"])
        result = dict(values=features.set_index("session_date")[columns],
            limitations=list(limits.values()), assumptions=list(assumptions.values()),
            statistics=dict(market=market, catalog_windows=len(windows),
                elapsed_seconds=time.monotonic() - started, sparse=sparse))
        future.set_result(result)
        _notify(progress, "macro_history_finished", **result["statistics"])
        return result, False
    except BaseException as exc:
        future.set_exception(exc)
        raise


def _feature_batch(records, snapshot, indices, axes, horizons, sparse_shared=None, progress=None,
                   precomputed_macro=None, output_sessions=None):
    batch_started = time.monotonic()
    ids = {str(record["stock_id"]) for record in records}
    market = str(records[0]["market"]).upper()
    axis, asof = axes[market], snapshot["asof_by_market"][market]
    _notify(progress, "feature_batch_start", market=market, stocks=len(records),
        first_stock=str(records[0]["stock_id"]))
    frames = {}
    for route, frame in snapshot["frames"].items():
        if route in {"finmind_price_adj", "official_market_daily"}:
            continue
        if route in _MACRO:
            frames[route] = frame
        elif "stock_id" in frame:
            selected = [indices[route][stock] for stock in ids if stock in indices[route]]
            positions = np.sort(np.concatenate(selected)) if selected else np.array([], dtype=np.int64)
            frames[route] = frame.iloc[positions]
    price, board = (snapshot["frames"].get(route) for route in ("finmind_price_adj", "official_market_daily"))
    panels, assessments = [], []
    for record in records:
        stock = str(record["stock_id"])
        panel, assessment = _price_panel(_stock_rows(price, indices.get("finmind_price_adj", {}), stock),
            _stock_rows(board, indices.get("official_market_daily", {}), stock), record, axis, asof)
        panels.append(panel)
        assessments.append(assessment)
    panel = pd.concat(panels, ignore_index=True)
    begin = time.monotonic()
    if sparse_shared is None:
        sparse_shared = dict(macro_series=_sparse_series(frames, snapshot["source_manifest"]),
            macro_values={}, lock=threading.Lock())
    if precomputed_macro is None:
        macro, macro_reused = _macro_history(panel, snapshot, axis, market, horizons, sparse_shared, progress)
    else:
        macro, macro_reused = precomputed_macro, True
    _notify(progress, "feature_batch_macro_ready", market=market, reused=macro_reused,
        elapsed_seconds=time.monotonic() - batch_started)
    frames = {route: frame for route, frame in frames.items() if route not in _MACRO}
    stock_manifests = _macro_manifest(snapshot["source_manifest"], False)
    begin = time.monotonic()
    source_axis = pd.DatetimeIndex(snapshot["market_sessions"][market])
    sequences = {stock: _sparse_series(frames, snapshot["source_manifest"], stock, market, source_axis) for stock in ids}
    sparse_prepare_seconds = time.monotonic() - begin
    sparse_plans, macro_expected = {}, {}
    sparse_guard_seconds = 0.0
    parts, limitations, assumptions = [], {}, {}
    for first, last in _feature_windows(axis, output_sessions):
        cutoff = axis[last]
        window = panel.loc[panel.session_date.between(cutoff - pd.DateOffset(months=18), cutoff)]
        prepared = catalog.build_candidate_features(window, {market: snapshot["market_sessions"][market]},
            asof_by_market={market: cutoff.strftime("%Y-%m-%d")}, horizons=list(horizons),
            source_frames=frames, source_manifest=stock_manifests)
        feature = prepared["features"]
        days = pd.to_datetime(feature["session_date"])
        feature = feature.loc[days.between(axis[first], cutoff)].copy()
        feature["session_date"] = pd.to_datetime(feature["session_date"])
        feature.loc[:, macro["values"].columns] = macro["values"].reindex(
            pd.DatetimeIndex(feature.session_date)).to_numpy()
        begin = time.monotonic()
        sparse_plans.update(_plan_sparse_fallback(feature, sequences, {},
            source_axis, cutoff - pd.DateOffset(months=18), macro_expected))
        sparse_guard_seconds += time.monotonic() - begin
        parts.append(feature[KEYS + ["signal_close", "feature_ready"] + prepared["metadata"]["feature_order"]])
        for limitation in prepared["metadata"]["limitations"]:
            key = (limitation["route_id"], limitation["code"])
            entry = limitations.setdefault(key, dict(limitation, window_evaluations=0))
            entry["window_evaluations"] += 1
        for assumption in prepared["metadata"].get("availability_assumptions", []):
            assumptions[assumption["route_id"]] = assumption
    for item in macro["limitations"]:
        limitations.setdefault((item["route_id"], item["code"]), dict(item))
    for item in macro["assumptions"]:
        assumptions[item["route_id"]] = item
    features = pd.concat(parts, ignore_index=True)
    sparse_statistics = _apply_sparse_fallback(features, panel, frames, snapshot["source_manifest"],
        source_axis, market, horizons, sparse_plans, sparse_shared)
    sparse_statistics.update(prepare_seconds=sparse_prepare_seconds, guard_seconds=sparse_guard_seconds)
    sparse_statistics["elapsed_seconds"] = (sparse_prepare_seconds + sparse_guard_seconds + sparse_statistics["apply_seconds"])
    assessments[0]["sparse_state_fallback_batch"] = sparse_statistics
    assessments[0]["macro_reuse_batch"] = dict(market=market, reused=macro_reused,
        catalog_windows=macro["statistics"]["catalog_windows"] if not macro_reused else 0)
    # A chunk's endpoint must not control the financial lookback of earlier signals.
    for record, assessment in zip(records, assessments):
        stock = str(record["stock_id"])
        history, evaluations = _financial_history(frames, snapshot["source_manifest"], stock, market, axis)
        selected = features.stock_id.eq(stock)
        features.loc[selected, history.columns] = history.reindex(
            pd.DatetimeIndex(features.loc[selected, "session_date"])).to_numpy()
        assessment["financial_state_intervals"] = evaluations
        prices = panel.loc[panel.stock_id.eq(stock)].set_index("session_date")["adjusted_close"].reindex(axis)
        cumulative = np.r_[0, prices.notna().to_numpy().cumsum()]
        left = axis.searchsorted(axis - pd.DateOffset(months=18), side="left")
        ready = pd.Series((cumulative[1:] - cumulative[left] >= 60) & prices.notna().to_numpy(), index=axis)
        features.loc[selected, "feature_ready"] = ready.reindex(
            pd.DatetimeIndex(features.loc[selected, "session_date"])).to_numpy()
    assessments[0]["feature_batch_timing"] = dict(worker_pid=os.getpid(),
        elapsed_seconds=time.monotonic() - batch_started, source_io_in_child=False)
    _notify(progress, "feature_batch_finished", market=market, stocks=len(records),
        elapsed_seconds=time.monotonic() - batch_started)
    return features, assessments, list(limitations.values()), list(assumptions.values())


def _worker_payload(records, snapshot, indices, axis, horizons, macro):
    ids = {str(record["stock_id"]) for record in records}
    frames = {}
    for route, frame in snapshot["frames"].items():
        if route in _MACRO or "stock_id" not in frame:
            continue
        selected = [indices[route][stock] for stock in ids if stock in indices[route]]
        positions = np.sort(np.concatenate(selected)) if selected else np.array([], dtype=np.int64)
        frames[route] = frame.iloc[positions].copy()
        frames[route].attrs = {}
    market = str(records[0]["market"]).upper()
    return dict(records=[dict(stock_id=str(record["stock_id"]), market=str(record["market"]).upper()) for record in records], frames=frames,
        market_sessions={market: snapshot["market_sessions"][market]},
        asof_by_market={market: snapshot["asof_by_market"][market]},
        source_manifest=json.loads(json.dumps(snapshot["source_manifest"])),
        axis=axis, horizons=tuple(horizons), macro=macro, output_sessions=_OUTPUT_SESSIONS)


def _deny_child_io(event, arguments):
    if event == "socket.connect":
        raise ResearchInputError("child_network_io_forbidden")
    if event != "open":
        return
    path, mode, flags = arguments
    if isinstance(path, (str, bytes, os.PathLike)):
        text = os.fsdecode(path).replace("/", "\\").casefold()
        if text.startswith("d:\\twstockdata\\"):
            raise ResearchInputError("child_source_io_forbidden")
        if (isinstance(mode, str) and any(letter in mode for letter in "wax+")) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)):
            raise ResearchInputError("child_filesystem_write_forbidden")


def _process_feature_batch(payload):
    """Pure in-memory worker: no source objects, leases, disk cache, or file I/O."""
    global _CHILD_IO_GUARD_INSTALLED
    sys.dont_write_bytecode = True
    if multiprocessing.parent_process() is not None and not _CHILD_IO_GUARD_INSTALLED:
        sys.addaudithook(_deny_child_io)
        _CHILD_IO_GUARD_INSTALLED = True
    snapshot = {key: payload[key] for key in ("records", "frames", "market_sessions", "asof_by_market", "source_manifest")}
    market = str(payload["records"][0]["market"]).upper()
    indices = {route: _group_index(frame) for route, frame in payload["frames"].items()}
    with threadpool_limits(limits=1):
        return _feature_batch(payload["records"], snapshot, indices, {market: payload["axis"]},
            payload["horizons"], precomputed_macro=payload["macro"], output_sessions=payload["output_sessions"])


def _exact_labels(features, market_sessions, asof_by_market, horizons):
    values = features.set_index(KEYS)["signal_close"]
    parts = []
    for market, rows in features.groupby("market", sort=False):
        axis = pd.DatetimeIndex(market_sessions[market])
        days = pd.DatetimeIndex(rows["session_date"])
        positions = axis.get_indexer(days)
        if (positions < 0).any():
            raise ResearchInputError("label_signal_not_in_official_axis")
        signal = rows["signal_close"].to_numpy(dtype=float)
        for horizon in horizons:
            endpoints = positions + horizon
            known = endpoints < len(axis)
            end = pd.Series(pd.NaT, index=rows.index, dtype="datetime64[ns]")
            end.loc[known] = axis.take(endpoints[known]).to_numpy()
            target_keys = pd.MultiIndex.from_arrays([rows["stock_id"], rows["market"], end], names=KEYS)
            target = values.reindex(target_keys).to_numpy(dtype=float)
            mature = end.le(pd.Timestamp(asof_by_market[market])).to_numpy()
            state = np.select([~np.isfinite(signal), ~known, ~mature, ~np.isfinite(target)],
                ["missing_signal_price", "unknown_target_session", "unmatured", "missing_target_price"], default="observed")
            returns = np.full(len(rows), np.nan)
            observed = state == "observed"
            returns[observed] = target[observed] / signal[observed] - 1
            result = rows[KEYS].copy()
            result["horizon"], result["label_end"] = horizon, end
            result["target_return"], result["label_state"] = returns, state
            parts.append(result)
    return pd.concat(parts, ignore_index=True).sort_values(KEYS + ["horizon"]).reset_index(drop=True)


def _assemble(snapshot, horizons, *, threads, progress):
    started = time.monotonic()
    _notify(progress, "feature_setup_start", stock_market_pairs=len(snapshot["records"]))
    frames = snapshot["frames"]
    indices = {route: _group_index(frame) for route, frame in frames.items()}
    begin = time.monotonic()
    sparse_shared = dict(macro_series=_sparse_series(frames, snapshot["source_manifest"]),
        macro_values={}, lock=threading.Lock())
    macro_prepare_seconds = time.monotonic() - begin
    axes = {}
    for market, source_axis in snapshot["market_sessions"].items():
        axis = pd.DatetimeIndex(source_axis).normalize()
        if axis.empty or axis.has_duplicates or not axis.is_monotonic_increasing:
            raise ResearchInputError("official_session_axis_invalid:" + market)
        available = frames.get("finmind_price_adj", pd.DataFrame())
        if available.empty or "date" not in available:
            raise ResearchInputError("current_adjusted_price_history_empty")
        first = pd.to_datetime(available["date"]).min()
        axis = axis[(axis >= first) & (axis <= snapshot["asof_by_market"][market])]
        if axis.empty or axis[-1] != snapshot["asof_by_market"][market]:
            raise ResearchInputError("official_axis_does_not_cover_current_asof:" + market)
        axes[market] = axis
    batches = []
    for market in sorted({str(record["market"]).upper() for record in snapshot["records"]}):
        records = [record for record in snapshot["records"] if str(record["market"]).upper() == market]
        for start in range(0, len(records), _BATCH_STOCKS):
            batches.append(records[start:start + _BATCH_STOCKS])
    macro_by_market = {}
    for market in sorted({str(record["market"]).upper() for record in snapshot["records"]}):
        axis = axes[market]
        record = next(record for record in snapshot["records"] if str(record["market"]).upper() == market)
        stock = str(record["stock_id"])
        panel, _ = _price_panel(_stock_rows(frames["finmind_price_adj"], indices["finmind_price_adj"], stock),
            _stock_rows(frames.get("official_market_daily"), indices.get("official_market_daily", {}), stock),
            record, axis, snapshot["asof_by_market"][market])
        macro_by_market[market] = _macro_history(panel, snapshot, axis, market, horizons, sparse_shared, progress)[0]
    parts, assessments, limits, assumptions = [], [], {}, {}
    _notify(progress, "stock_process_pool_start", workers=min(4, threads), batches=len(batches),
        parent_pid=os.getpid(), child_source_io=False)
    context = multiprocessing.get_context("spawn")
    manager = (ProcessPoolExecutor(max_workers=min(4, threads), mp_context=context)
               if threads > 1 else contextlib.nullcontext(None))
    with manager as pool:
        jobs = iter(batches)
        pending = []
        def submit(records):
            market = str(records[0]["market"]).upper()
            payload = _worker_payload(records, snapshot, indices, axes[market], horizons, macro_by_market[market])
            if pool is None:
                immediate = Future()
                immediate.set_result(_process_feature_batch(payload))
                return immediate
            return pool.submit(_process_feature_batch, payload)
        # Only four small payloads may be in flight, never the source snapshot.
        for _ in range(min(4, threads, len(batches))):
            pending.append(submit(next(jobs)))
        completed = 0
        while pending:
            future = pending.pop(0)
            feature, checked, limitations, assumed = future.result()
            parts.append(feature)
            assessments.extend(checked)
            for item in limitations:
                key = (item["route_id"], item["code"])
                limits.setdefault(key, item)
            for item in assumed:
                assumptions[item["route_id"]] = item
            completed += 1
            _notify(progress, "features", completed_batches=completed, total_batches=len(batches),
                elapsed_seconds=time.monotonic() - started,
                worker=checked[0].get("feature_batch_timing"),
                remaining_seconds_linear_estimate=(time.monotonic() - started) * (len(batches) - completed) / completed)
            following = next(jobs, None)
            if following is not None:
                pending.append(submit(following))
    _notify(progress, "stock_process_pool_closed", elapsed_seconds=time.monotonic() - started)
    features = pd.concat(parts, ignore_index=True).sort_values(KEYS).reset_index(drop=True)
    if features.duplicated(KEYS).any():
        raise ResearchInputError("duplicate_assembled_feature_key")
    definitions = catalog._definitions()
    order = list(catalog._LEGACY) + [name for name in definitions if name not in catalog._LEGACY]
    if len(order) != 38 or not set(order).issubset(features):
        raise ResearchInputError("selected_38_feature_schema_changed")
    by_horizon, baselines, legacy_reference = {}, {}, {}
    for horizon in horizons:
        plan = {entry["route_id"]: entry["usage"] for entry in catalog.select_source_plan([horizon])}
        by_horizon[str(horizon)] = [name for name in order if plan[definitions[name]["route"]] == "used"
            and (horizon >= 60 or name not in {"return_126", "return_252"})]
        baselines[str(horizon)] = list(by_horizon[str(horizon)])
        legacy_reference[str(horizon)] = [name for name in catalog._LEGACY if name in by_horizon[str(horizon)]]
    labels = _exact_labels(features, snapshot["market_sessions"], snapshot["asof_by_market"], horizons)
    coverage = dict(ordinary_stock_market_pairs=len(snapshot["records"]), feature_rows=len(features),
        feature_ready_rows=int(features.feature_ready.sum()), claims_five_year_history=False,
        no_minimum_fit_days_gate=True, label_states={str(k): int(v) for k, v in labels.label_state.value_counts().items()},
        features={name: dict(available_rows=int(features[name].notna().sum()), missing_rows=int(features[name].isna().sum())) for name in order},
        history_by_market={market: dict(first_session=axis[0].isoformat(), last_session=axis[-1].isoformat(),
            official_sessions=len(axis)) for market, axis in axes.items()},
        price_assessments=assessments, historical_membership_verified=False)
    coverage["feature_ready_days_by_market"] = {
        market: int(rows.loc[rows.feature_ready, "session_date"].nunique())
        for market, rows in features.groupby("market", sort=False)}
    sparse_reports = [item["sparse_state_fallback_batch"] for item in assessments if "sparse_state_fallback_batch" in item]
    sparse_summary = {key: sum(item[key] for item in sparse_reports) for key in (
        "candidate_rows", "rows", "overwritten_cells", "catalog_calls", "macro_cache_hits",
        "catalog_seconds", "apply_seconds", "prepare_seconds", "guard_seconds", "elapsed_seconds")}
    macro_statistics = [future.result()["statistics"] for future in sparse_shared.get("macro_history", {}).values()]
    for report in macro_statistics:
        stocks = sum(str(record["market"]).upper() == report["market"] for record in snapshot["records"])
        sparse = report["sparse"]
        for key in ("candidate_rows", "rows", "overwritten_cells"):
            sparse_summary[key] += sparse[key] * stocks
        for key in ("catalog_calls", "catalog_seconds", "apply_seconds", "guard_seconds", "elapsed_seconds"):
            sparse_summary[key] += sparse[key]
        sparse_summary["macro_cache_hits"] += sparse["rows"] * max(stocks - 1, 0)
    sparse_summary.update(macro_prepare_seconds=macro_prepare_seconds,
        time_basis="summed_worker_seconds; macro preparation reported once",
        full_date_fallback_policy="stop_for_parent_decision")
    metadata = dict(schema=catalog.FEATURE_SCHEMA, feature_schema=catalog.FEATURE_SCHEMA,
        feature_order=order, feature_order_by_horizon=by_horizon,
        baseline_feature_order_by_horizon=baselines, legacy_reference_feature_order=legacy_reference,
        horizons=list(horizons), asof_by_market={m: pd.Timestamp(d).strftime("%Y-%m-%d") for m, d in snapshot["asof_by_market"].items()},
        feature_metadata=definitions, source_manifest=copy.deepcopy(snapshot["source_manifest"]),
        historical_PIT_unverified=True, historical_membership_verified=False,
        availability_assumptions=list(assumptions.values()), limitations=list(limits.values()),
        history_policy="all_valid_current_history; overlapping_18mo_feature_lookbacks; exact_full_axis_labels",
        financial_history_policy="per_signal_18mo; explicit_publication_and_source_date_expiry_intervals",
        chunk_equivalence_status="per_signal_financial_events_and_targeted_sparse_fallback; parity_tests_not_run",
        sparse_state_fallback=sparse_summary,
        macro_reuse=dict(market_histories=len(macro_statistics),
            catalog_windows=sum(item["catalog_windows"] for item in macro_statistics),
            batch_cache_hits=sum(bool(item.get("macro_reuse_batch", {}).get("reused")) for item in assessments),
            unique_macro_fallback_rows=sum(item["sparse"]["rows"] for item in macro_statistics),
            market_statistics=macro_statistics,
            policy="one_original_catalog_macro_calculation_per_market_window;in_memory_only"),
        worker_execution=dict(mode="bounded_spawn_processes" if threads > 1 else "serial_in_memory",
            workers=min(4, threads), source_reads="parent_only", executor_closed_before_return=True,
            worker_pids=sorted({item["feature_batch_timing"]["worker_pid"] for item in assessments if "feature_batch_timing" in item})),
        single_prediction_lookback_is_not_training_history=True, minimum_price_observations=60,
        coverage=coverage)
    timings = dict(snapshot["timings"], assemble_seconds=time.monotonic() - started)
    _notify(progress, "assembled", feature_rows=len(features), label_rows=len(labels))
    return dict(features=features, labels=labels, feature_order_by_horizon=by_horizon,
        baseline_feature_order_by_horizon=baselines, legacy_reference_feature_order=legacy_reference,
        feature_metadata=definitions,
        market_sessions={m: pd.DatetimeIndex(days) for m, days in snapshot["market_sessions"].items()},
        asof_by_market=dict(snapshot["asof_by_market"]), source_audit=copy.deepcopy(snapshot["source_audit"]),
        bindings=copy.deepcopy(snapshot["bindings"]), coverage=coverage, timings=timings,
        metadata=metadata)


def build_research_inputs(horizons=(1, 63), threads=4, progress=None, *, expected_head_sha256=None):
    """Assemble selected-38 inputs; never fit models or save any dataset/cache.

    All business dates in returned frames/axes/cutoffs are naive, normalized
    pandas Timestamps. Horizon mapping keys are strings; the labels' horizon
    column remains integer-compatible with the existing candidate trainer.
    The current publication is pinned at startup. An explicit expected digest
    must match that current publication, never an older retained publication.
    """
    horizons = runtime._horizons(horizons)
    if type(threads) is not int or not 1 <= threads <= 4:
        raise ValueError("threads must be between one and four")
    if progress is not None and not callable(progress):
        raise TypeError("progress must be callable")
    started = time.monotonic()
    with contextlib.ExitStack() as stack:
        snapshot = _load_snapshot(stack, horizons, progress, expected_head_sha256)
        _notify(progress, "source_read_finished", elapsed_seconds=time.monotonic() - started,
            routes_read=len(snapshot["frames"]), head_sha256=snapshot["bindings"].get("head_sha256"))
        result = _assemble(snapshot, horizons, threads=threads, progress=progress)
        sources = snapshot["sources"]
        proof = sources.validate_pinned_context(sources.snapshot_descriptor())
        if proof.get("repinned") or proof.get("descriptor_refreshed"):
            raise ResearchInputError("pinned_context_changed_during_assembly")
        for binding in result["bindings"]["code"].values():
            if _proof_file(binding["path"]) != binding:
                raise ResearchInputError("input_code_changed_during_assembly:" + binding["path"])
        result["bindings"]["exit_pin_verified"] = True
        result["bindings"]["current_head_sha256_at_end"] = _proof_file(
            PUBLISHED_ROOT / "_metadata" / "stock_data_current.json")["sha256"]
        result["bindings"]["head_advanced_during_build"] = (
            result["bindings"]["current_head_sha256_at_end"] != result["bindings"]["head_sha256"])
        result["timings"]["total_seconds"] = time.monotonic() - started
        return result
