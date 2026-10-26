"""Research-only candidate orchestration over one sealed, read-only publication."""
from __future__ import annotations

import argparse
import calendar
import copy
import contextlib
import hashlib
import heapq
import importlib
import itertools
import json
import math
import os
import random
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import stock_cache_lifecycle as cache_lifecycle

SINGLE_SRC = Path(__file__).resolve().parents[1] / "single_stock_analysis" / "src"
if str(SINGLE_SRC) not in sys.path:
    sys.path.insert(0, str(SINGLE_SRC))

PUBLISHED_ROOT = Path(r"D:\TWStockData\shared_market_data")
CANDIDATE_ROOT = Path(r"D:\TWStockData\program_data\stock_daily_c0\candidates")
MODEL_SCHEMA = "tw-stock-daily-selected-candidate-v1"
FEATURE_SCHEMA = "tw-stock-selected-features-v1"
REPORT_SCHEMA = "single-stock-selected-candidate-report-v1"
CACHE_SCHEMA = "stock-daily-candidate-filtered-rows-v1"
CACHE_SEMANTICS = "published-json-row-filter-v1"
SHARED_ROWS_LAYOUT = "keyed_plus_shared_unkeyed_v1"
READ_ONLY_STORAGE_POLICY = "single-stock-read-only-inputs-v1"
READ_PLAN_SCHEMA = "selected-route-read-plan-v1"
_ROW_CACHE_BASES = {}
DEFAULT_HORIZONS = (1, 5, 20, 60, 63)
CORE_ROUTES = frozenset({"official_calendar", "finmind_stock_info", "finmind_price_adj", "official_market_daily"})
GLOBAL_ROUTES = frozenset({"global_market_history", "finmind_futures_institutional",
    "finmind_options_institutional", "finmind_exchange_rate", "finmind_government_bonds_yield",
    "finmind_crude_oil_prices", "finmind_gold_price", "official_sector_observations"})
SNAPSHOT_ROUTES = frozenset({"official_trading_restrictions", "official_sector_features",
    "finmind_industry_chain_flow", "official_finance_reporting_deadline"})
PUBLICATION_TIME_ROUTES = frozenset({"finmind_month_revenue", "finmind_financial_statements",
    "finmind_balance_sheet", "finmind_cash_flows_statement", "finmind_dividend",
    "finmind_dividend_result", "tdcc_share_distribution_raw", "news_and_hot_topics_raw",
    "global_market_history", "official_sector_observations"})
AVAILABLE_TIME_FIELDS = ("available_time", "announcement_time", "published_at", "publish_time", "release_time", "source_close_at")
_PHASES = {"read": "讀取", "features": "特徵", "train": "訓練", "predict": "推論", "report": "報告"}


class CandidateRuntimeError(ValueError):
    """A candidate cannot proceed without changing or inventing its contract."""


class CandidateProjectionNotReady(CandidateRuntimeError):
    """Interactive calls must not turn a cache miss into a full-market scan."""


def _clean(value):
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        return _clean(value.item())
    # pandas.NA/NaT are not serializable and must not become the strings 'nan'/'NaT'.
    import pandas as pd
    if pd.isna(value):
        return None
    raise TypeError("候選產物含無法封存的值：" + type(value).__name__)


def _canonical(value):
    return json.dumps(_clean(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha_file(path):
    import stock_daily_contract as contract
    return hashlib.sha256(contract._stable_file_bytes(path)).hexdigest()


def _write_json(path, value):
    path = _consumer_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value) + b"\n")


def _consumer_path(path):
    result = Path(path).resolve()
    forbidden = (PUBLISHED_ROOT.resolve(),
        Path(r"D:\TWStockData\program_data\stock_daily_c0\models").resolve(),
        Path(r"D:\MIX All").resolve(), Path(r"D:\stock\TWNextDayTop10").resolve())
    if any(result == root or root in result.parents for root in forbidden):
        raise CandidateRuntimeError("候選產物不可寫入中央來源、正式模型或其他消費端路徑。")
    if result.name.casefold() == "current.json" or any(part.casefold() in {
            "forward", "forward_ledger", "forward-ledger", "ledger", "ledgers"} for part in result.parts):
        raise CandidateRuntimeError("候選流程不可修改正式指標或前瞻帳本。")
    return result


@contextmanager
def _phase(name, detail=""):
    label = _PHASES[name]
    start = time.monotonic()
    stop = threading.Event()
    print(f"候選研究｜{label}開始" + ("｜" + detail if detail else ""), flush=True)

    def heartbeat():
        while not stop.wait(15):
            print(f"候選研究｜{label}進行中｜已用 {int(time.monotonic() - start)} 秒", flush=True)

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        yield
    except BaseException:
        print(f"候選研究｜{label}受阻", flush=True)
        raise
    else:
        print(f"候選研究｜{label}完成｜{time.monotonic() - start:.1f} 秒", flush=True)
    finally:
        stop.set()
        thread.join()


def _horizons(values):
    if isinstance(values, str):
        values = values.split(",")
    result = tuple(dict.fromkeys(int(value) for value in values))
    if not result or any(value not in DEFAULT_HORIZONS for value in result):
        raise CandidateRuntimeError("候選期限只接受 1、5、20、60、63 個市場交易日。")
    return result


def _day(value):
    import date_format as cd
    return cd.iso_day_or_none(value)


def _month_start(day, months=18):
    value = date.fromisoformat(day)
    index = value.year * 12 + value.month - 1 - months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1])).isoformat()


def _load_context(consumer_id="single", pinned_head_sha256=None):
    import stock_daily_contract as contract
    reference = None
    if pinned_head_sha256 is not None:
        digest = str(pinned_head_sha256).lower().strip()
        if not re.fullmatch("[0-9a-f]{64}", digest):
            raise CandidateRuntimeError("指定快照 SHA256 格式不正確。")
        path = PUBLISHED_ROOT / "_manifests" / (digest + ".json")
        # A requested pin must never silently become a different publication.
        if _sha_file(path) != digest:
            raise CandidateRuntimeError("指定封存快照的內容雜湊不符。")
        reference = {"root": str(PUBLISHED_ROOT), "manifest_path": str(path), "manifest_sha256": digest}
    return contract.load_c0_published_context(reference, consumer_id=consumer_id)


def _snapshot(context):
    sources = context["sources"]
    descriptor = sources.snapshot_descriptor()
    heads = descriptor.get("sources") or []
    if len(heads) != 1 or Path(heads[0]["root"]).resolve() != PUBLISHED_ROOT.resolve():
        raise CandidateRuntimeError("候選資料必須來自唯一中央封存快照。")
    digest = heads[0].get("manifest_sha256")
    if not isinstance(digest, str) or not re.fullmatch("[0-9a-fA-F]{64}", digest):
        raise CandidateRuntimeError("中央快照缺少可核對的 head 雜湊。")
    return {"head_sha256": digest.lower(), "snapshot_fingerprint": sources.fingerprint,
        "descriptor": descriptor, "calendar_evidence": copy.deepcopy(context["calendar_evidence"]),
        "identity_binding": copy.deepcopy(context["current_identity"]["binding"]),
        "retention_binding": copy.deepcopy(context["application_scope"]["binding"]),
        "capture_binding": copy.deepcopy(context["application_scope"]["capture_binding"]),
        "asof_by_market": copy.deepcopy(context.get("analysis_asof_by_market") or context["asof_by_market"]),
        "version_labels": copy.deepcopy(context.get("version_labels") or [])}


def _source_plan(horizons, source_plan, industry=None):
    catalog = importlib.import_module("stock_daily_feature_catalog")
    selected = copy.deepcopy(source_plan if source_plan is not None else
        catalog.select_source_plan(list(horizons), industry=industry))
    indexed = {}
    for entry in selected:
        route = entry.get("route_id")
        if route not in catalog.SOURCE_CATALOG or route in indexed or entry.get("usage") not in {
                "used", "context-only", "not-selected"}:
            raise CandidateRuntimeError("候選來源選擇清單不符合封存介面。")
        indexed[route] = entry
    if set(indexed) != set(catalog.SOURCE_CATALOG):
        raise CandidateRuntimeError("來源選擇清單必須保留完整來源目錄。")
    if any(indexed[route]["usage"] == "not-selected" for route in CORE_ROUTES):
        raise CandidateRuntimeError("候選價格、官方行情、普通股資格與日曆證據不可略過。")
    return list(indexed.values())


def _merge_source_plans(plans):
    """Union already-frozen requested plans; never choose rules at inference."""
    merged = {}
    priority = {"not-selected": 0, "context-only": 1, "used": 2}
    for plan in plans:
        for item in plan:
            route = item["route_id"]
            if route not in merged:
                merged[route] = copy.deepcopy(item)
            elif priority[item["usage"]] > priority[merged[route]["usage"]]:
                merged[route] = copy.deepcopy(item)
            elif item["usage"] == merged[route]["usage"] and item["reason_zh"] != merged[route]["reason_zh"]:
                merged[route]["reason_zh"] += "；" + item["reason_zh"]
    return list(merged.values())


def _definition_map(metadata):
    definitions = metadata.get("feature_metadata") or {}
    return {item["name"]: item for item in definitions} if isinstance(definitions, list) else definitions


def _training_horizon_orders(prepared, plans):
    metadata = prepared["metadata"]
    definitions = _definition_map(metadata)
    for horizon, plan in plans.items():
        usage = {entry["route_id"]: entry["usage"] for entry in plan}
        metadata["feature_order_by_horizon"][horizon] = [name for name in
            metadata["feature_order_by_horizon"][horizon] if usage.get(
                definitions[name].get("source_route", definitions[name].get("route"))) == "used"]


def _apply_frozen_schema(prepared, model, requested_horizons, source_plan):
    import numpy as np
    metadata, frozen = prepared["metadata"], model["metadata"]
    definitions = _definition_map(frozen)
    actual = _definition_map(metadata)
    for name in set(actual) & set(definitions):
        if actual[name] != definitions[name]:
            raise CandidateRuntimeError("推論特徵定義與封存模型不同：" + name)
    usage = {entry["route_id"]: entry["usage"] for entry in source_plan}
    for horizon in requested_horizons:
        head = (model.get("horizons") or {}).get(str(horizon), {})
        for task, kind in (head.get("selected") or {}).items():
            if kind is None:
                continue
            candidate = (head.get("candidates") or {}).get(kind, {})
            transformer = candidate.get("transformer") or {}
            needed = set(transformer.get("effective_feature_order") or []) | set(
                transformer.get("effective_indicator_order") or [])
            for name in needed:
                definition = definitions.get(name) or {}
                route = definition.get("source_route", definition.get("route"))
                if usage.get(route) != "used":
                    raise CandidateRuntimeError(f"封存 H{horizon} 的{task}模型需要未選入的特徵來源：{route}")
    for name in frozen["feature_order"]:
        if name not in prepared["features"]:
            prepared["features"][name] = np.nan
    for key in ("feature_order", "feature_order_by_horizon", "feature_metadata", "horizons"):
        metadata[key] = copy.deepcopy(frozen[key])
    metadata["requested_horizons"] = list(requested_horizons)


def _read_bounds(route, usage, starts, ends, records):
    def start_for(market):
        if route in SNAPSHOT_ROUTES:
            return None
        if usage == "context-only":
            financial = {"finmind_month_revenue", "finmind_financial_statements", "finmind_balance_sheet",
                "finmind_cash_flows_statement", "finmind_dividend", "finmind_dividend_result"}
            days = 30 if route == "news_and_hot_topics_raw" else 400 if route in financial else 90
            return (date.fromisoformat(ends[market]) - timedelta(days=days)).isoformat()
        return starts[market]

    if route in GLOBAL_ROUTES:
        return min((start_for(market) for market in starts), default=None), max(ends[market] for market in starts)
    lower = {str(item["stock_id"]): start_for(str(item["market"]).upper()) for item in records}
    upper = {str(item["stock_id"]): ends[str(item["market"]).upper()] for item in records}
    return None if all(value is None for value in lower.values()) else lower, upper


def _row_selected(row, route, stock_ids, start, end):
    if not isinstance(row, dict):
        raise CandidateRuntimeError("封存來源含非物件資料列。")
    stock = row.get("stock_id", row.get("data_id", row.get("code")))
    if route not in GLOBAL_ROUTES and stock is not None and str(stock) not in stock_ids:
        return False
    if isinstance(start, dict):
        start = start.get(str(stock), min(start.values()))
    if isinstance(end, dict):
        end = end.get(str(stock), max(end.values()))
    value = next((_day(row.get(key)) for key in ("date", "observation_date", "report_date", "date_id", "period_end")
        if _day(row.get(key)) is not None), None)
    return value is None or ((start is None or value >= start) and (end is None or value <= end))


def _cache_identity(item, route):
    artifact = item["artifact_descriptor"]
    return {"schema": CACHE_SCHEMA, "route_id": route,
        "root": os.path.normcase(str(Path(item.get("root", PUBLISHED_ROOT)).resolve())),
        "artifact": {"path": artifact.get("path", artifact.get("relative_path")),
            "sha256": artifact.get("sha256"), "size": artifact.get("size", artifact.get("size_bytes"))},
        "parser_semantics": CACHE_SEMANTICS}


def _cache_provenance(item):
    import stock_data_sources as sealed_reader
    return {"parent_identity": copy.deepcopy(item.get("parent_identity")),
        "source_identity": copy.deepcopy(item.get("source_identity")),
        "artifact": copy.deepcopy(item["artifact_descriptor"]),
        "parser_binding": {"runtime_sha256": _sha_file(__file__),
            "sealed_reader_sha256": _sha_file(sealed_reader.__file__)}}


def _cache_identity_compatible(actual, expected):
    """The dedicated v1 reader preserves its original row/date selection rules."""
    if not isinstance(actual, dict) or actual.get("schema") != CACHE_SCHEMA:
        return False
    semantics = actual.get("parser_semantics")
    if semantics is None:
        # Legacy v1 recorded file hashes as provenance, with no semantic token.
        # Its known format and selection rules remain supported without making
        # those unrelated file versions a permanent data-reuse requirement.
        if not isinstance(actual.get("parser_binding"), dict):
            return False
    elif semantics != CACHE_SEMANTICS:
        return False
    artifact = actual.get("artifact")
    if not isinstance(artifact, dict):
        return False
    normalized = _cache_identity({"root": actual.get("root", PUBLISHED_ROOT),
        "artifact_descriptor": artifact}, actual.get("route_id"))
    return normalized == expected


def _cache_key(identity):
    return hashlib.sha256(_canonical(identity)).hexdigest()


def _cache_base(cache_dir, identity):
    return Path(cache_dir) / "sealed_candidate_rows" / _cache_key(identity)


def _legacy_cache_bases(cache_dir):
    root = Path(cache_dir) / "sealed_candidate_rows"
    try:
        stamp = root.stat()
    except FileNotFoundError:
        return {}
    key = (str(root), stamp.st_mtime_ns)
    if key not in _ROW_CACHE_BASES:
        found = {}
        for base in root.iterdir():
            if not base.is_dir() or re.fullmatch("[0-9a-f]{64}", base.name) is None:
                continue
            sample = next((base / "queries").glob("*.json"), None)
            if sample is None:
                continue
            try:
                document = _cache_document(sample)
                identity = document["identity"]
                if _cache_key(identity) != base.name:
                    continue
                artifact = identity["artifact"]
                normalized = _cache_identity({"root": identity.get("root", PUBLISHED_ROOT),
                    "artifact_descriptor": artifact}, identity["route_id"])
                if _cache_identity_compatible(identity, normalized):
                    found.setdefault(_cache_key(normalized), []).append(base)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        _ROW_CACHE_BASES.clear()
        _ROW_CACHE_BASES[key] = found
    return _ROW_CACHE_BASES[key]


def _cache_candidates(cache_dir, identity, scope):
    direct, exact = _cache_base(cache_dir, identity), _cache_key(scope) + ".json"
    path = direct / "queries" / exact
    if path.is_file():
        yield path
    # Keep discovery lazy: a normal exact hit returns without enumerating any
    # directory or consulting the legacy-base metadata index.
    for path in (direct / "queries").glob("*.json"):
        if path.name != exact:
            yield path
    bases = [base for base in _legacy_cache_bases(cache_dir).get(_cache_key(identity), []) if base != direct]
    for base in bases:
        path = base / "queries" / exact
        if path.is_file():
            yield path
    for base in bases:
        for path in (base / "queries").glob("*.json"):
            if path.name != exact:
                yield path


def _query_scope(route, stock_ids, start, end):
    return {"stock_ids": None if route in GLOBAL_ROUTES else sorted(set(stock_ids)),
        "start": copy.deepcopy(start), "end": copy.deepcopy(end)}


def _bound_value(bound, stock):
    return bound.get(stock) if isinstance(bound, dict) else bound


def _scope_covers(cached, requested):
    cached_ids, ids = cached["stock_ids"], requested["stock_ids"]
    if cached_ids is not None and (ids is None or not set(ids).issubset(cached_ids)):
        return False
    for stock in ids or [""]:
        for key in ("start", "end"):
            have, want = _bound_value(cached[key], stock), _bound_value(requested[key], stock)
            if have is not None and (want is None or (want < have if key == "start" else want > have)):
                return False
    return True


def _atomic_bytes(path, raw):
    path = _consumer_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix="candidate-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _cache_document(path, *, expected=None):
    import stock_daily_contract as contract
    raw = contract._stable_file_bytes(path)
    if expected is not None and (len(raw) != expected["size"]
            or hashlib.sha256(raw).hexdigest() != expected["sha256"]):
        raise CandidateRuntimeError("read_plan_cache_manifest_changed")
    document = json.loads(raw.decode("utf-8"))
    expected = document.pop("integrity_sha256", None)
    if expected != hashlib.sha256(_canonical(document)).hexdigest():
        raise CandidateRuntimeError("候選消費端快取 manifest 雜湊不符。")
    if document.get("schema") != CACHE_SCHEMA:
        raise CandidateRuntimeError("候選消費端快取格式不相容。")
    return document


def _cache_components(document):
    layout = document.get("rows_layout")
    if layout not in (None, SHARED_ROWS_LAYOUT) or layout is None and document.get("shared_rows"):
        raise CandidateRuntimeError("候選消費端快取列版面不相容。")
    return [document["rows"]] + list(document.get("shared_rows") or [])


def _cache_component_paths(base, descriptor):
    paths = []
    for field, directory in ((None, "rows"), ("ordinals", "ordinals")):
        item = descriptor if field is None else descriptor.get(field)
        if item is None:
            continue
        if (not isinstance(item, dict) or re.fullmatch("[0-9a-f]{64}", str(item.get("sha256"))) is None
                or type(item.get("size")) is not int or item["size"] < 0
                or type(item.get("row_count")) is not int or item["row_count"] < 0):
            raise CandidateRuntimeError("候選消費端快取資料引用無效。")
        paths.append((Path(base) / directory / (item["sha256"] + ".jsonl"),
            {key: item[key] for key in ("sha256", "size", "row_count")}))
    if len(paths) == 2 and descriptor["row_count"] != descriptor["ordinals"]["row_count"]:
        raise CandidateRuntimeError("快取列與原始順序的筆數不符。")
    return paths


def _cache_file_identity(path):
    stat = Path(path).stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@contextmanager
def _cache_base_lease(cache_dir, identity):
    key = _cache_key(identity)
    with cache_lifecycle.CacheLease(cache_dir, "selected-" + key,
            cache_keys=["selected-base-" + key]) as lease:
        yield lease


def _cache_document_refs(cache_dir, manifest_path, document):
    root, base = Path(cache_dir), Path(manifest_path).parent.parent
    refs = [{"root": str(root), "ref": {"ref": Path(manifest_path).relative_to(root).as_posix(),
        "sha256": _sha_file(manifest_path), "size": Path(manifest_path).stat().st_size}, "expand": False}]
    for component in _cache_components(document):
        for path, descriptor in _cache_component_paths(base, component):
            refs.append({"root": str(root), "ref": {"ref": path.relative_to(root).as_posix(),
                "sha256": descriptor["sha256"], "size": descriptor["size"]}, "expand": False})
    return refs


def _verified_cache_file(path, descriptor, verified_files):
    proof = (verified_files or {}).get(str(Path(path).resolve()))
    if (not isinstance(proof, dict) or proof.get("sha256") != descriptor.get("sha256")
            or proof.get("size") != descriptor.get("size")):
        return False
    import stock_daily_selected_deployment as deployment
    try:
        with Path(path).open("rb") as handle:
            identity = list(deployment._handle_identity(handle))
        if identity != proof.get("identity"):
            return False
        with Path(path).open("rb") as handle:
            return list(deployment._handle_identity(handle)) == identity
    except OSError:
        return False


def _iter_cache_file(path, proof, *, ordinal=False, memo=None):
    identity = _cache_file_identity(path)
    key = (str(path), _cache_key(proof), identity, ordinal)
    if memo is not None and key in memo:
        yield from memo[key]
        return
    buffered = [] if memo is not None else None
    digest, size, count, previous = hashlib.sha256(), 0, 0, 0
    with Path(path).open("rb") as handle:
        for raw in handle:
            digest.update(raw)
            size += len(raw)
            count += 1
            value = json.loads(raw.decode("utf-8"))
            if ordinal:
                if type(value) is not int or value <= previous:
                    raise CandidateRuntimeError("候選消費端快取原始列順序無效。")
                previous = value
            elif not isinstance(value, dict):
                raise CandidateRuntimeError("候選消費端快取含非物件資料列。")
            if buffered is not None:
                buffered.append(value)
            yield value
    if (identity != _cache_file_identity(path) or digest.hexdigest() != proof["sha256"]
            or size != proof["size"] or count != proof["row_count"]):
        raise CandidateRuntimeError("候選消費端快取 digest、長度或筆數不符。")
    if memo is not None:
        memo[key] = buffered


def _iter_cache_component(base, descriptor, *, memo=None):
    paths = _cache_component_paths(base, descriptor)
    rows = _iter_cache_file(*paths[0], memo=memo)
    with contextlib.ExitStack() as cleanup:
        cleanup.callback(rows.close)
        if len(paths) == 1:
            yield from enumerate(rows, 1)
            return
        ordinals = _iter_cache_file(*paths[1], ordinal=True, memo=memo)
        cleanup.callback(ordinals.close)
        missing = object()
        for row, ordinal in itertools.zip_longest(rows, ordinals, fillvalue=missing):
            if row is missing or ordinal is missing:
                raise CandidateRuntimeError("快取列與原始順序的筆數不符。")
            yield ordinal, row


def _iter_cache_rows(base, document, *, shared_memo=None):
    components = _cache_components(document)
    if len(components) > 1 and any("ordinals" not in item for item in components):
        raise CandidateRuntimeError("共享快取缺少原始列順序。")
    streams = [_iter_cache_component(base, item, memo=shared_memo if index else None)
        for index, item in enumerate(components)]
    previous = 0
    with contextlib.ExitStack() as cleanup:
        for stream in streams:
            cleanup.callback(stream.close)
        for ordinal, row in heapq.merge(*streams, key=lambda item: item[0]):
            if ordinal <= previous:
                raise CandidateRuntimeError("共享快取含重複或逆序來源列。")
            previous = ordinal
            yield row


def _read_consumer_cache(cache_dir, identity, scope, route, stock_ids, start, end,
                         *, read_only=False, manifest_ref=None):
    root = _consumer_path(cache_dir)
    if read_only and not root.is_dir():
        if manifest_ref is not None:
            raise CandidateRuntimeError("read_plan_cache_missing")
        return None
    lease = None
    with cache_lifecycle.catalog_lock(root):
        candidates = ([_read_plan_path(root, manifest_ref, "consumer")]
            if manifest_ref is not None else _cache_candidates(root, identity, scope))
        for path in candidates:
            proof = _read_plan_file(root, path, "consumer", manifest_ref) if read_only else None
            document = _cache_document(path, expected=proof)
            if not _cache_identity_compatible(document.get("identity"), identity) or not _scope_covers(document["scope"], scope):
                if manifest_ref is not None:
                    raise CandidateRuntimeError("read_plan_cache_scope_mismatch")
                continue
            lease = cache_lifecycle.CacheLease(root, "selected-" + path.parent.parent.name,
                physical_refs=_cache_document_refs(root, path, document),
                cache_keys=["selected-base-" + path.parent.parent.name], already_locked=True)
            break
    if lease is None:
        if manifest_ref is not None:
            raise CandidateRuntimeError("read_plan_cache_missing")
        return None
    with lease:
        rows = [row for row in _iter_cache_rows(path.parent.parent, document, shared_memo={})
            if _row_selected(row, route, stock_ids, start, end)]
        binding = {"reader": "consumer_sealed_cache", "cache_manifest_path": str(path),
            "cache_manifest_sha256": _sha_file(path), "rows_sha256": document["rows"]["sha256"],
            "shared_rows": copy.deepcopy(document.get("shared_rows") or []),
            "cached_scope": document["scope"], "predicate": scope, "source_identity": identity,
            "source_provenance": copy.deepcopy(document.get("source_provenance")),
            "sealed_read_evidence": document["sealed_read_evidence"]}
        if read_only:
            _read_plan_file(root, path, "consumer", proof)
            binding["_read_only_files"] = {"manifest": proof}
        return rows, binding


def _read_plan_path(root, reference, kind):
    patterns = {"consumer": r"sealed_candidate_rows/[0-9a-f]{64}/queries/[0-9a-f]{64}\.json",
        "index": r"indexes/[0-9a-f]{64}\.json", "complete": r"complete/[0-9a-f]{64}\.json"}
    if (not isinstance(reference, dict) or set(reference) != {"path", "sha256", "size"}
            or not isinstance(reference["path"], str)
            or re.fullmatch(patterns[kind], reference["path"]) is None
            or re.fullmatch(r"[0-9a-f]{64}", str(reference["sha256"])) is None
            or type(reference["size"]) is not int or reference["size"] < 0):
        raise CandidateRuntimeError("read_plan_file_reference_invalid")
    return cache_lifecycle.cache_path(root, Path(root) / reference["path"])


def _read_plan_file(root, path, kind, expected=None):
    import stock_daily_contract as contract
    root, path = Path(root).resolve(), Path(path)
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise CandidateRuntimeError("read_plan_path_escape") from exc
    reference = {"path": relative, "sha256": "0" * 64, "size": 0}
    path = _read_plan_path(root, expected or reference, kind)
    raw = contract._stable_file_bytes(path)
    reference.update(sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))
    if expected is not None and reference != expected:
        raise CandidateRuntimeError("read_plan_file_changed")
    return reference


def _read_plan_entry(item, route, scope, binding, files=None):
    return {"route_id": route, "source_identity": _cache_identity(item, route),
        "parent_identity": copy.deepcopy(item.get("parent_identity")), "predicate": copy.deepcopy(scope),
        "reader": binding["reader"], "files": copy.deepcopy(files or {}), "binding_sha256": _cache_key(binding)}


def _seal_read_plan(plan):
    value = copy.deepcopy(plan)
    value.pop("integrity_sha256", None)
    value["integrity_sha256"] = _cache_key(value)
    return _clean(value)


def _empty_read_plan(cache_dir, head, *, legacy=False):
    value = {"schema": READ_PLAN_SCHEMA, "storage_policy": READ_ONLY_STORAGE_POLICY,
        "legacy": legacy, "cache_root": str(_consumer_path(cache_dir)), "head_sha256": head, "routes": {}}
    if legacy:
        value["legacy_bindings"] = {}
    return value


def _record_read_plan(actual, expected, route, entry):
    if expected is not None and _cache_key(expected["routes"][route]) != _cache_key(entry):
        raise CandidateRuntimeError("read_plan_route_evidence_mismatch:" + route)
    actual["routes"][route] = copy.deepcopy(entry)


def read_plan_from_manifest(manifest, cache_dir, head):
    """Resolve old report locators without creating replacement cache files."""
    if not isinstance(manifest, list):
        raise CandidateRuntimeError("legacy_read_plan_manifest_invalid")
    plan = _empty_read_plan(cache_dir, head, legacy=True)
    for entry in manifest:
        route = entry.get("route_id") if isinstance(entry, dict) else None
        if not isinstance(route, str) or route in plan["routes"]:
            raise CandidateRuntimeError("legacy_read_plan_route_invalid")
        if entry.get("usage") == "not-selected":
            value = {"route_id": route, "reader": "not-selected"}
        elif entry.get("status") == "missing":
            value = {"route_id": route, "reader": "missing"}
        elif route in {"official_calendar", "finmind_stock_info"}:
            value = {"route_id": route, "reader": "verified_context", "object_sha256": _cache_key(entry.get("object")),
                "evidence_sha256": _cache_key(entry.get("evidence"))}
        else:
            binding = entry.get("query_binding")
            if not isinstance(binding, dict):
                raise CandidateRuntimeError("legacy_read_plan_binding_required")
            reader, files = binding.get("reader"), {}
            predicate = binding.get("predicate")
            if not isinstance(predicate, dict):
                raise CandidateRuntimeError("legacy_read_plan_predicate_required")
            start = predicate.get("start", predicate.get("start_date"))
            end = predicate.get("end", predicate.get("end_date"))
            stocks = predicate.get("stock_ids") or []
            if reader == "not_ready":
                stocks = sorted({stock for bound in (start, end) if isinstance(bound, dict) for stock in bound})
            scope = _query_scope(route, stocks, start, end)
            if reader == "consumer_sealed_cache":
                reference = _read_plan_file(cache_dir, Path(binding["cache_manifest_path"]), "consumer")
                if reference["sha256"] != binding.get("cache_manifest_sha256"):
                    raise CandidateRuntimeError("legacy_read_plan_cache_hash_mismatch")
                files["manifest"] = reference
            elif reader == "sealed_projection":
                key = binding.get("cache_key")
                if re.fullmatch(r"[0-9a-f]{64}", str(key)) is None:
                    raise CandidateRuntimeError("legacy_read_plan_native_key_invalid")
                root = cache_lifecycle.source_registry(PUBLISHED_ROOT)
                for name, directory in (("index", "indexes"), ("complete", "complete")):
                    reference = _read_plan_file(root, root / directory / (key + ".json"), name)
                    field = "index_sha256" if name == "index" else "completion_sha256"
                    if reference["sha256"] != binding.get(field):
                        raise CandidateRuntimeError("legacy_read_plan_native_hash_mismatch")
                    files[name] = reference
            elif reader not in {"sealed_stream_fallback", "not_ready"}:
                raise CandidateRuntimeError("legacy_read_plan_reader_unsupported")
            value = _read_plan_entry({"root": PUBLISHED_ROOT, "artifact_descriptor": entry["object"],
                "parent_identity": head}, route, scope, binding, files)
            plan["legacy_bindings"][route] = copy.deepcopy(binding)
        plan["routes"][route] = value
    return _seal_read_plan(plan)


def _check_read_plan(plan, cache_dir, head, routes):
    keys = {"schema", "storage_policy", "legacy", "cache_root", "head_sha256", "routes", "integrity_sha256"}
    if isinstance(plan, dict) and plan.get("legacy") is True:
        keys.add("legacy_bindings")
    if (not isinstance(plan, dict) or set(plan) != keys
            or plan.get("schema") != READ_PLAN_SCHEMA or plan.get("storage_policy") != READ_ONLY_STORAGE_POLICY
            or type(plan.get("legacy")) is not bool or plan.get("cache_root") != str(_consumer_path(cache_dir))
            or plan.get("head_sha256") != head or not isinstance(plan.get("routes"), dict)
            or set(plan["routes"]) != set(routes) or _seal_read_plan(plan) != plan):
        raise CandidateRuntimeError("read_plan_invalid")
    row_routes = {route for route, entry in plan["routes"].items()
        if isinstance(entry, dict) and entry.get("reader") in {
            "consumer_sealed_cache", "sealed_projection", "sealed_stream_fallback", "not_ready"}}
    if plan["legacy"] and (not isinstance(plan["legacy_bindings"], dict)
            or set(plan["legacy_bindings"]) != row_routes):
        raise CandidateRuntimeError("legacy_read_plan_bindings_invalid")


class _ReadOnlyScratchUnavailable(CandidateRuntimeError):
    pass


def _read_only_native_rows(result):
    import stock_query_projection as query
    components = query._ordinal_components([(shard, None) for shard in result.selected])
    needs_spool = any(len(component) > query._MAX_OPEN_ROWS_SHARDS for component in components)
    with contextlib.ExitStack() as cleanup:
        if needs_spool:
            sizes = [shard.get("size_bytes") for shard in result.selected]
            if any(type(size) is not int or size < 0 for size in sizes):
                raise CandidateRuntimeError("read_plan_native_shard_size_invalid")
            # SQLite pages, JSON rows and sorting overhead; only the selected shards count.
            peak = sum(sizes) * 8 + 64 * 1024 * 1024
            try:
                temporary_root = tempfile.gettempdir()
                capacity = shutil.disk_usage(temporary_root)
                reserve = max(20 * 1024 ** 3, (capacity.total + 19) // 20)
                if capacity.free < peak + reserve:
                    raise _ReadOnlyScratchUnavailable("read_only_native_scratch_space_insufficient")
                result.consumer_temp_root = Path(cleanup.enter_context(
                    tempfile.TemporaryDirectory(prefix="selected-read-only-", dir=temporary_root)))
            except OSError as exc:
                raise _ReadOnlyScratchUnavailable("read_only_native_scratch_location_unavailable") from exc
        iterator = result.iter_rows()
        cleanup.callback(iterator.close)
        return list(iterator)


def _read_only_stream(sources, item, route, stock_ids, start, end, *, legacy_binding=None):
    rows, artifacts = [], []
    descriptor = item["artifact_descriptor"]
    with contextlib.closing(sources.iter_artifact_rows(route)) as source_iterator:
        for artifact, iterator in source_iterator:
            try:
                if artifact.content_sha256 != descriptor["sha256"]:
                    raise CandidateRuntimeError("read_only_stream_source_hash_mismatch")
                for row in iterator:
                    if _row_selected(row, route, stock_ids, start, end):
                        rows.append(row)
                reference = (artifact.metadata or {}).get("selected_ref") or {}
                if (reference.get("sha256") != descriptor["sha256"]
                        or reference.get("size") != descriptor.get("size", descriptor.get("size_bytes"))):
                    raise CandidateRuntimeError("read_only_stream_source_size_or_hash_unverified")
                artifacts.append({"sha256": artifact.content_sha256,
                    "snapshot_id": artifact.snapshot_id, "status": artifact.status})
            finally:
                close = getattr(iterator, "close", None)
                if close is not None:
                    close()
    if len(artifacts) != 1:
        raise CandidateRuntimeError("read_only_stream_source_ambiguous_or_missing")
    reason = legacy_binding.get("fallback_reason") if legacy_binding is not None else "read_only_verified_stream_v1"
    if not isinstance(reason, str) or not reason:
        raise CandidateRuntimeError("read_plan_stream_reason_invalid")
    return rows, {"reader": "sealed_stream_fallback", "fallback_reason": reason,
        "artifacts": artifacts, "predicate": {"stock_ids": stock_ids, "start": start, "end": end}}


def _read_route_read_only(sources, item, route, stock_ids, start, end, cache_dir,
                          *, reader_plan=None, legacy=False, legacy_binding=None):
    import stock_query_projection as query
    identity, scope = _cache_identity(item, route), _query_scope(route, stock_ids, start, end)
    if reader_plan is not None:
        if (not isinstance(reader_plan, dict) or set(reader_plan) != {"route_id", "source_identity",
                "parent_identity", "predicate", "reader", "files", "binding_sha256"}
                or reader_plan["route_id"] != route or reader_plan["source_identity"] != identity
                or reader_plan["parent_identity"] != item.get("parent_identity")
                or reader_plan["predicate"] != scope or reader_plan["reader"] not in {
                    "consumer_sealed_cache", "sealed_projection", "sealed_stream_fallback", "not_ready"}
                or not isinstance(reader_plan["files"], dict)
                or re.fullmatch(r"[0-9a-f]{64}", str(reader_plan["binding_sha256"])) is None):
            raise CandidateRuntimeError("read_plan_route_or_predicate_mismatch")
    if legacy and (not isinstance(legacy_binding, dict) or reader_plan is None
            or _cache_key(legacy_binding) != reader_plan["binding_sha256"]):
        raise CandidateRuntimeError("legacy_read_plan_binding_mismatch")
    choice = reader_plan["reader"] if reader_plan is not None else None
    files = copy.deepcopy(reader_plan["files"]) if reader_plan is not None else {}
    if choice == "not_ready":
        binding = {"reader": "not_ready", "allow_full_scan": False, "predicate": {"start": start, "end": end}}
        if (not legacy or files or _cache_key(binding) != _cache_key(legacy_binding)
                or _cache_key(_read_plan_entry(item, route, scope, binding)) != _cache_key(reader_plan)):
            raise CandidateRuntimeError("legacy_read_plan_not_ready_invalid")
        raise CandidateProjectionNotReady("legacy_recorded_projection_not_ready")
    if choice in (None, "consumer_sealed_cache"):
        if choice is not None and set(files) != {"manifest"}:
            raise CandidateRuntimeError("read_plan_consumer_files_invalid")
        cached = _read_consumer_cache(cache_dir, identity, scope, route, stock_ids, start, end,
            read_only=True, manifest_ref=files.get("manifest"))
        if cached is not None:
            rows, binding = cached
            files = binding.pop("_read_only_files")
            choice = "consumer_sealed_cache"
    if choice in (None, "sealed_projection"):
        root = cache_lifecycle.source_registry(item["root"])
        result = None
        try:
            if choice is None and not (root / "indexes").is_dir():
                raise query.ProjectionUnavailableError("projection_not_built")
            if choice is not None:
                if set(files) != {"index", "complete"}:
                    raise CandidateRuntimeError("read_plan_native_files_invalid")
                for name in ("index", "complete"):
                    _read_plan_file(root, _read_plan_path(root, files[name], name), name, files[name])
            result = query.query_published_rows(item["root"], item["artifact_descriptor"], route,
                stock_ids=None if route in GLOBAL_ROUTES else stock_ids, start_date=start, end_date=end,
                source_identity=item.get("source_identity"), parent_identity=item.get("parent_identity"),
                allow_build=False, consumer_temp_root=None)
            current = {"index": _read_plan_file(root, result.index_manifest_path, "index", files.get("index")),
                "complete": _read_plan_file(root, result.completion_path, "complete", files.get("complete"))}
            if (current["index"]["path"] != "indexes/" + result.cache_key + ".json"
                    or current["complete"]["path"] != "complete/" + result.cache_key + ".json"):
                raise CandidateRuntimeError("read_plan_native_key_mismatch")
            rows = _read_only_native_rows(result)
            binding = {"reader": "sealed_projection", "cache_key": result.cache_key,
                "predicate": result.predicate, "lineage": result.lineage, "metrics": result.metrics,
                "completion_sha256": current["complete"]["sha256"], "index_sha256": current["index"]["sha256"]}
            for name, path in (("index", result.index_manifest_path), ("complete", result.completion_path)):
                _read_plan_file(root, path, name, current[name])
            files, choice = current, "sealed_projection"
        except (query.UnsupportedProjectionError, query.ProjectionUnavailableError, _ReadOnlyScratchUnavailable):
            if reader_plan is not None:
                raise CandidateRuntimeError("read_plan_native_unavailable")
            choice, files = "sealed_stream_fallback", {}
        finally:
            if result is not None:
                result.close()
    if choice == "sealed_stream_fallback":
        if files:
            raise CandidateRuntimeError("read_plan_stream_files_invalid")
        rows, binding = _read_only_stream(sources, item, route, stock_ids, start, end,
            legacy_binding=legacy_binding)
    if choice != "consumer_sealed_cache":
        binding["source_provenance"] = _cache_provenance(item)
        if legacy and legacy_binding.get("consumer_cache") is not None:
            retained = legacy_binding["consumer_cache"]
            reference = _read_plan_file(cache_dir, Path(retained["manifest_path"]), "consumer")
            if reference["sha256"] != retained.get("manifest_sha256"):
                raise CandidateRuntimeError("read_plan_legacy_cache_hash_mismatch")
            document = _cache_document(_read_plan_path(cache_dir, reference, "consumer"), expected=reference)
            expected_reference = {"manifest_path": str(_read_plan_path(cache_dir, reference, "consumer")),
                "manifest_sha256": reference["sha256"], "manifest_size": reference["size"],
                "rows_sha256": document["rows"]["sha256"], "rows": document["rows"],
                "shared_rows": document.get("shared_rows") or []}
            if retained != expected_reference:
                raise CandidateRuntimeError("read_plan_legacy_cache_reference_mismatch")
            original = {key: value for key, value in legacy_binding.items() if key != "consumer_cache"}
            if document["sealed_read_evidence"] != original:
                raise CandidateRuntimeError("read_plan_legacy_cache_evidence_mismatch")
            cached_rows = _read_consumer_cache(cache_dir, identity, scope, route, stock_ids, start, end,
                read_only=True, manifest_ref=reference)[0]
            if _cache_key(cached_rows) != _cache_key(rows):
                raise CandidateRuntimeError("read_plan_legacy_cache_rows_mismatch")
            binding["consumer_cache"] = copy.deepcopy(retained)
            binding["source_provenance"] = copy.deepcopy(document.get("source_provenance"))
    if not legacy:
        binding["read_plan_policy"] = READ_ONLY_STORAGE_POLICY
    actual = _read_plan_entry(item, route, scope, binding, files)
    if reader_plan is not None and _cache_key(actual) != _cache_key(reader_plan):
        raise CandidateRuntimeError("read_plan_evidence_mismatch")
    binding["_selected_read_plan"] = actual
    return rows, binding


def _write_cache_component(cache_dir, identity, rows, temporary_dir):
    base = _cache_base(cache_dir, identity)
    temporary, proofs = [], []
    try:
        # Spool directories are private. Final components inherit their own
        # destination directory policy by creating adjacent temporary files.
        root = Path(cache_dir)
        cache_lifecycle.cache_path(root, root, allow_root=True)
        with cache_lifecycle.catalog_lock(root):
            for directory in (root / "sealed_candidate_rows", base, base / "rows", base / "ordinals"):
                directory.mkdir(exist_ok=True)
                cache_lifecycle.cache_path(root, directory)
            for directory in ("rows", "ordinals"):
                fd, name = tempfile.mkstemp(dir=base / directory, prefix=".candidate-", suffix=".tmp")
                temporary.append(Path(name))
                os.close(fd)
        hashes, sizes, count, previous = [hashlib.sha256(), hashlib.sha256()], [0, 0], 0, 0
        with temporary[0].open("wb") as data, temporary[1].open("wb") as order:
            for ordinal, row in rows:
                if type(ordinal) is not int or ordinal <= previous or not isinstance(row, dict):
                    raise CandidateRuntimeError("快取寫入的資料或原始順序無效。")
                previous, count = ordinal, count + 1
                chunks = (_canonical(row) + b"\n", _canonical(ordinal) + b"\n")
                for index, (handle, raw) in enumerate(zip((data, order), chunks)):
                    handle.write(raw)
                    hashes[index].update(raw)
                    sizes[index] += len(raw)
            for handle in (data, order):
                handle.flush()
                os.fsync(handle.fileno())
        with cache_lifecycle.catalog_lock(cache_dir):
            for index, directory in enumerate(("rows", "ordinals")):
                proof = {"sha256": hashes[index].hexdigest(), "size": sizes[index], "row_count": count}
                target = base / directory / (proof["sha256"] + ".jsonl")
                cache_lifecycle.cache_path(cache_dir, target.parent)
                if target.exists():
                    cache_lifecycle.cache_path(cache_dir, target)
                    if target.stat().st_size != proof["size"] or _sha_file(target) != proof["sha256"]:
                        raise CandidateRuntimeError("既有內容定址快取不符。")
                else:
                    os.replace(temporary[index], target)
                proofs.append(proof)
        return dict(proofs[0], ordinals=proofs[1])
    finally:
        for path in temporary:
            if path.exists():
                cache_lifecycle.unlink_cache_file(cache_dir, path)


def _save_cache_document(cache_dir, identity, scope, own, shared, evidence):
    base = _cache_base(cache_dir, identity)
    manifest_path = _consumer_path(base / "queries" / (_cache_key(scope) + ".json"))
    document = {"schema": CACHE_SCHEMA, "identity": identity, "scope": scope,
        "rows_layout": SHARED_ROWS_LAYOUT, "rows": own, "shared_rows": list(shared),
        "source_provenance": copy.deepcopy(evidence.get("source_provenance")), "sealed_read_evidence": evidence}
    document["integrity_sha256"] = hashlib.sha256(_canonical(document)).hexdigest()
    with cache_lifecycle.catalog_lock(cache_dir):
        _atomic_bytes(manifest_path, _canonical(document) + b"\n")
    return {"manifest_path": str(manifest_path), "manifest_sha256": _sha_file(manifest_path),
        "manifest_size": manifest_path.stat().st_size, "rows_sha256": own["sha256"],
        "rows": copy.deepcopy(own), "shared_rows": copy.deepcopy(list(shared))}


def _save_consumer_cache(cache_dir, identity, scope, rows, evidence):
    root = _consumer_path(cache_dir)
    root.mkdir(parents=True, exist_ok=True)
    with _cache_base_lease(root, identity), tempfile.TemporaryDirectory(prefix="selected-rows-", dir=root) as temporary:
        indexed = list(enumerate(rows, 1))
        if identity["route_id"] in GLOBAL_ROUTES:
            own, shared = _write_cache_component(root, identity, iter(indexed), temporary), []
        else:
            def keyed(item):
                return item[1].get("stock_id", item[1].get("data_id", item[1].get("code"))) is not None
            own = _write_cache_component(root, identity, (item for item in indexed if keyed(item)), temporary)
            unkeyed = _write_cache_component(root, identity, (item for item in indexed if not keyed(item)), temporary)
            shared = [unkeyed] if unkeyed["row_count"] else []
        return _save_cache_document(root, identity, scope, own, shared, evidence)


def _read_route(sources, item, route, stock_ids, start, end, cache_dir, *, allow_full_scan=False,
                read_only=False, persist_cache=True, reader_plan=None, legacy_plan=False, legacy_binding=None):
    if read_only or not persist_cache:
        return _read_route_read_only(sources, item, route, stock_ids, start, end, cache_dir,
            reader_plan=reader_plan, legacy=legacy_plan, legacy_binding=legacy_binding)
    import stock_query_projection as query
    identity = _cache_identity(item, route)
    scope = _query_scope(route, stock_ids, start, end)
    cached = _read_consumer_cache(cache_dir, identity, scope, route, stock_ids, start, end)
    if cached is not None:
        return cached
    try:
        result = query.query_published_rows(item["root"], item["artifact_descriptor"], route,
            stock_ids=None if route in GLOBAL_ROUTES else stock_ids,
            start_date=start, end_date=end, source_identity=item.get("source_identity"),
            parent_identity=item.get("parent_identity"), allow_build=False, consumer_temp_root=cache_dir)
        try:
            rows = list(result.iter_rows())
            binding = {"reader": "sealed_projection", "cache_key": result.cache_key,
                "predicate": result.predicate, "lineage": result.lineage, "metrics": result.metrics,
                "completion_sha256": _sha_file(result.completion_path), "index_sha256": _sha_file(result.index_manifest_path)}
        finally:
            result.close()
    except (query.UnsupportedProjectionError, query.ProjectionUnavailableError) as exc:
        if not allow_full_scan:
            raise CandidateProjectionNotReady("封存投影與候選消費端快取尚未就緒：" + route) from exc
        rows, artifacts = [], []
        for artifact, iterator in sources.iter_artifact_rows(route):
            if artifact.content_sha256 != item["artifact_descriptor"]["sha256"]:
                raise CandidateRuntimeError("串流 fallback 與原封存來源雜湊不同。")
            for row in iterator:
                if _row_selected(row, route, stock_ids, start, end):
                    rows.append(row)
            artifacts.append({"sha256": artifact.content_sha256, "snapshot_id": artifact.snapshot_id, "status": artifact.status})
        if len(artifacts) != 1:
            raise CandidateRuntimeError("封存串流來源缺失或有多個版本。")
        binding = {"reader": "sealed_stream_fallback", "fallback_reason": type(exc).__name__ + ":" + str(exc),
            "artifacts": artifacts, "predicate": {"stock_ids": stock_ids, "start": start, "end": end}}
    binding["source_provenance"] = _cache_provenance(item)
    binding["consumer_cache"] = _save_consumer_cache(cache_dir, identity, scope, rows, copy.deepcopy(binding))
    return rows, binding


def _normalise_frame(rows):
    import pandas as pd
    frame = pd.DataFrame(rows)
    if "stock_id" in frame:
        frame["stock_id"] = frame["stock_id"].astype(str)
    elif "data_id" in frame:
        frame["stock_id"] = frame["data_id"].astype(str)
    elif "code" in frame:
        frame["stock_id"] = frame["code"].astype(str)
    if "date" not in frame:
        for name in ("observation_date", "report_date", "date_id", "period_end"):
            if name in frame:
                frame["date"] = frame[name]
                break
    # Do not derive available_time from period_end, date, collection time or a deadline.
    return frame


def _availability(entry, item, frame, binding, stock_ids):
    result = copy.deepcopy(entry)
    artifact = copy.deepcopy(item["artifact_descriptor"])
    result.update(status=artifact.get("status", "unknown"), publication_status=artifact.get("status", "unknown"),
        availability="available" if len(frame) else "unknown_availability", object=artifact,
        source_identity=copy.deepcopy(item.get("source_identity")), query_binding=binding,
        fields=list(frame.columns), row_count=len(frame),
        coverage={"requested_stocks": len(stock_ids), "observed_stocks": int(frame["stock_id"].nunique())
            if "stock_id" in frame else None}, latest_data_date=None,
        historical_available_time="unknown", gaps=[])
    if "date" in frame:
        values = [value for value in frame["date"].map(_day) if value is not None]
        result["latest_data_date"] = max(values, default=None)
        result["first_data_date"] = min(values, default=None)
    if not len(frame):
        result["gaps"].append("查詢無資料，尚無對應股票與期間的正向無資料證據。")
    if entry["route_id"] in PUBLICATION_TIME_ROUTES:
        import pandas as pd
        fields = [key for key in AVAILABLE_TIME_FIELDS if key in frame]
        timed = any(pd.to_datetime(frame[key], errors="coerce", utc=True).notna().any() for key in fields)
        result["available_time_fields"] = fields
        result["historical_available_time"] = "row_evidence_required" if timed else "unknown"
        if entry["usage"] == "used" and not timed:
            result["requested_usage"] = "used"
            result["usage"] = "context-only"
            result["reason_zh"] += "；未證實歷史公開時間，本輪只作上下文，不回填歷史。"
    return result


def _price_panel(frames, records, windows, ends):
    import numpy as np
    import pandas as pd
    import stock_daily_price_quality as quality
    px, board = (frames.get(route, pd.DataFrame()).copy() for route in ("finmind_price_adj", "official_market_daily"))
    required_px = {"stock_id", "date", "open", "close", "spread", "Trading_Volume", "_shared_collected_at"}
    required_board = {"stock_id", "date", "market", "close", "volume"}
    if not required_px.issubset(px) or not required_board.issubset(board):
        raise CandidateRuntimeError("還原價格或同日官方行情缺少品質核對欄位；不得以原始價格補接。")
    for frame in (px, board):
        frame["date"] = pd.to_datetime(frame["date"].map(_day), errors="coerce")
        if frame["date"].isna().any():
            raise CandidateRuntimeError("價格或官方行情含未知日期。")
    kept, assessments = [], []
    for record in records:
        stock, market = str(record["stock_id"]), str(record["market"]).upper()
        axis = windows[market]
        price = px.loc[(px.stock_id == stock) & (px.date >= axis[0]) & (px.date <= ends[market])].copy()
        official = board.loc[(board.stock_id == stock) & (board.date >= axis[0]) & (board.date <= ends[market])].copy()
        reasons, notes = [], []
        if price.empty:
            reasons.append("price_data_unavailable")
        if price.duplicated(["stock_id", "date"]).any():
            reasons.append("duplicate_price_key")
        if official.duplicated(["stock_id", "date"]).any():
            reasons.append("duplicate_official_price_key")
        if official.empty:
            reasons.append("official_price_comparison_unavailable")
        if set(official["market"].astype(str).str.upper()) - {market}:
            reasons.append("price_window_official_market_mismatch")
        if price["_shared_collected_at"].isna().any():
            reasons.append("price_provenance_observation_missing")
        if not reasons:
            try:
                checked = quality.series_checks(price, official, ends[market])
                reasons.extend(reason for reason in checked["reasons"].get(stock, []) if reason != "missing_rows")
                notes.extend(checked.get("version_labels") or [])
            except quality.PriceSeriesQualityError as exc:
                reasons.append(str(exc))
        close = pd.to_numeric(price["close"], errors="coerce")
        if not (np.isfinite(close) & (close > 0)).all():
            reasons.append("price_close_invalid")
        actual = set(price["date"].dt.strftime("%Y-%m-%d"))
        if actual - set(axis):
            reasons.append("price_row_outside_verified_market_axis")
        missing = sorted(set(axis) - actual)
        assessment = {"stock_id": stock, "market": market, "expected_sessions": len(axis),
            "observed_sessions": len(actual), "missing_sessions": len(missing), "missing_dates": missing,
            "first_price_date": min(actual, default=None), "last_price_date": max(actual, default=None),
            "status": "unavailable" if reasons else "partial" if missing else "complete",
            "reason_codes": sorted(set(reasons)), "version_labels": notes, "imputed_prices": False}
        assessments.append(assessment)
        if reasons:
            continue
        price["market"] = market
        price["session_date"] = price["date"].dt.strftime("%Y-%m-%d")
        price["adjusted_close"] = close
        for target, aliases in {"high": ("max",), "low": ("min",)}.items():
            if target not in price:
                for alias in aliases:
                    if alias in price:
                        price[target] = price[alias]
                        break
        # Official activity fields and raw OHLC share the same stock/date key.
        # Never fill their gaps from FinMind or splice raw prices into adjusted OHLC.
        aligned = official.set_index("date").reindex(pd.DatetimeIndex(price["date"]))
        for field in ("volume", "amount", "transactions"):
            price[field] = pd.to_numeric(aligned[field], errors="coerce").to_numpy() if field in aligned else np.nan
        for field in ("open", "high", "low", "close"):
            price["raw_" + field] = pd.to_numeric(aligned[field], errors="coerce").to_numpy() if field in aligned else np.nan
        price["volume_unit"] = "股"
        kept.append(price)
    columns = ["stock_id", "market", "session_date", "adjusted_close"]
    return pd.concat(kept, ignore_index=True) if kept else pd.DataFrame(columns=columns), assessments


def _prune_after_cache_build(cache_dir, references, pinned_identities):
    """One best-effort sweep after a batch actually writes consumer row caches."""
    try:
        import stock_daily_selected_cache as cache
        import stock_daily_selected_cache_worker as worker
        identities = {_cache_key(value): value for value in cache.current_cache_identities()}
        identities.update({_cache_key(value): value for value in pinned_identities})
        protected = set()
        for reference in references:
            manifest = Path(reference["manifest_path"])
            protected.add(str(manifest))
            for component in [reference["rows"]] + list(reference.get("shared_rows") or []):
                protected.update(str(path) for path, _ in _cache_component_paths(manifest.parent.parent, component))
        result = worker._prune_consumer_cache({"cache_dir": str(cache_dir)},
            protected_files=sorted(protected), retained_identities=list(identities.values()), apply=True)
        return dict(result, status="deferred" if result.get("unknown") else "processed")
    except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
        return {"status": "deferred", "deleted_files": 0, "deleted_bytes": 0,
            "unknown": [{"path": str(cache_dir), "reason": type(exc).__name__ + ":" + str(exc)}]}


def load_candidate_inputs(stock_ids, *, context=None, consumer_id="single", cache_dir=None,
                          horizons=None, source_plan=None, allow_full_scan=False,
                          read_only=False, persist_cache=True, read_plan=None):
    """Batch selected routes once; retain unknown availability and per-market dates."""
    horizons = _horizons(horizons or DEFAULT_HORIZONS)
    context = context if context is not None else _load_context(consumer_id)
    snapshot = _snapshot(context)
    cache_dir = _consumer_path(cache_dir or CANDIDATE_ROOT / "cache")
    if type(read_only) is not bool or type(persist_cache) is not bool:
        raise CandidateRuntimeError("read_only_flags_invalid")
    read_only = read_only or not persist_cache
    if read_only and consumer_id != "single" or read_plan is not None and not read_only:
        raise CandidateRuntimeError("read_only_single_scope_required")
    identity = context["current_identity"]
    requested = list(dict.fromkeys(str(value) for value in stock_ids))
    if not requested:
        raise CandidateRuntimeError("候選查詢未指定股票。")
    positives = {str(item["stock_id"]): item for item in identity["records"]}
    records, excluded = [], []
    for stock in requested:
        record = positives.get(stock)
        if record is None:
            excluded.append({"stock_id": stock, "reason": "current_official_ordinary_unverified"})
        else:
            records.append(copy.deepcopy(record))
    if not records:
        raise CandidateRuntimeError("指定股票沒有已驗證的目前普通股資格；不納入 ETF 或未知分類。")
    ends = {str(key).upper(): _day(value) for key, value in snapshot["asof_by_market"].items()}
    axes = {str(key).upper(): list(value) for key, value in context["market_sessions"].items()}
    starts, windows = {}, {}
    verified = {str(key).upper(): value for key, value in
        (context["calendar_evidence"].get("calendar_verified_by_market") or {}).items()}
    for market in sorted({str(item["market"]).upper() for item in records}):
        if not ends.get(market) or verified.get(market) is not True:
            raise CandidateRuntimeError("市場資料日或日曆未驗證：" + market)
        starts[market] = _month_start(ends[market])
        windows[market] = [day for day in axes.get(market, []) if starts[market] <= day <= ends[market]]
        if not windows[market] or windows[market][-1] != ends[market]:
            raise CandidateRuntimeError("已驗證日曆未涵蓋該市場資料日：" + market)
    industries = {item.get("industry") for item in records if item.get("industry")}
    plan = _source_plan(horizons, source_plan, next(iter(industries)) if len(industries) == 1 else None)
    if read_plan is not None:
        _check_read_plan(read_plan, cache_dir, snapshot["head_sha256"], [item["route_id"] for item in plan])
    actual_read_plan = _empty_read_plan(cache_dir, snapshot["head_sha256"],
        legacy=read_plan["legacy"] if read_plan is not None else False) if read_only else None
    if read_plan is not None and read_plan["legacy"]:
        actual_read_plan["legacy_bindings"] = copy.deepcopy(read_plan["legacy_bindings"])
    sources, frames, manifest = context["sources"], {}, []
    new_cache_references, pinned_cache_identities = [], []
    ids = [str(item["stock_id"]) for item in records]
    with _phase("read", f"{len(ids)} 檔普通股，逐來源批次讀取"):
        for entry in plan:
            route = entry["route_id"]
            if entry["usage"] == "not-selected":
                if read_only:
                    _record_read_plan(actual_read_plan, read_plan, route, {"route_id": route, "reader": "not-selected"})
                manifest.append(dict(entry, status="not-selected", availability="not-selected", fields=[],
                    coverage=None, latest_data_date=None))
                continue
            items = tuple(sources.projection_artifacts(route))
            if len(items) > 1:
                raise CandidateRuntimeError("同一快照的來源物件不唯一：" + route)
            if not items:
                if read_only:
                    _record_read_plan(actual_read_plan, read_plan, route, {"route_id": route, "reader": "missing"})
                manifest.append(dict(entry, status="missing", availability="missing", fields=[],
                    coverage=None, latest_data_date=None, gaps=["封存目錄沒有本次選中來源。 "]))
                continue
            item = items[0]
            if (Path(item["root"]).resolve() != PUBLISHED_ROOT.resolve()
                    or str(item.get("parent_identity") or "").lower() != snapshot["head_sha256"]):
                raise CandidateRuntimeError("來源投影目錄與本輪封存 head 不一致，停止以免混版。")
            pinned_cache_identities.append(_cache_identity(item, route))
            if route in {"official_calendar", "finmind_stock_info"}:
                evidence = context["calendar_evidence"] if route == "official_calendar" else identity["binding"]
                if read_only:
                    _record_read_plan(actual_read_plan, read_plan, route,
                        {"route_id": route, "reader": "verified_context", "object_sha256": _cache_key(item["artifact_descriptor"]),
                         "evidence_sha256": _cache_key(evidence)})
                if route == "finmind_stock_info":
                    import pandas as pd
                    frames[route] = pd.DataFrame(records)
                manifest.append(dict(entry, status=item["artifact_descriptor"].get("status"), availability="available",
                    object=copy.deepcopy(item["artifact_descriptor"]), evidence=copy.deepcopy(evidence),
                    fields=list(frames[route].columns) if route in frames else [],
                    coverage={"requested_stocks": len(ids)}, latest_data_date=None,
                    current_identity_verified=route == "finmind_stock_info", historical_membership_verified=False,
                    usage="context-only", reason_zh=entry["reason_zh"] + "；使用已驗證封存側錄，不重讀大型內容。"))
                continue
            start, end = _read_bounds(route, entry["usage"], starts, ends, records)
            try:
                rows, binding = _read_route(sources, item, route, ids, start, end, cache_dir,
                    allow_full_scan=allow_full_scan, **({"read_only": True, "persist_cache": False,
                        "reader_plan": read_plan["routes"][route] if read_plan is not None else None,
                        "legacy_plan": read_plan["legacy"] if read_plan is not None else False,
                        "legacy_binding": read_plan["legacy_bindings"].get(route)
                            if read_plan is not None and read_plan["legacy"] else None} if read_only else {}))
            except CandidateProjectionNotReady as exc:
                if route in {"finmind_price_adj", "official_market_daily"}:
                    raise CandidateProjectionNotReady("必要價格投影或候選快取尚未就緒；請先離線訓練／準備同批股票，再產生研究報告。來源：" + entry["name_zh"]) from exc
                if read_only:
                    unavailable = {"reader": "not_ready", "allow_full_scan": False,
                        "predicate": {"start": start, "end": end}}
                    _record_read_plan(actual_read_plan, read_plan, route,
                        _read_plan_entry(item, route, _query_scope(route, ids, start, end), unavailable))
                manifest.append(dict(entry, status="projection_not_ready", availability="unknown_availability",
                    object=copy.deepcopy(item["artifact_descriptor"]), fields=[], coverage=None,
                    latest_data_date=None, reason_zh=entry["reason_zh"] + "；投影未就緒，互動報告不掃描全市場來源。",
                    query_binding={"reader": "not_ready", "allow_full_scan": False, "predicate": {"start": start, "end": end}}))
                continue
            if read_only:
                _record_read_plan(actual_read_plan, read_plan, route, binding.pop("_selected_read_plan"))
            if not read_only and binding.get("consumer_cache") is not None:
                new_cache_references.append(binding["consumer_cache"])
            frame = _normalise_frame(rows)
            # Apply each stock's own market cutoff, even on a common batch query.
            if "stock_id" in frame and "date" in frame and route not in GLOBAL_ROUTES:
                stock_ends = {str(value["stock_id"]): ends[str(value["market"]).upper()] for value in records}
                days = frame["date"].map(_day)
                limits = frame["stock_id"].map(stock_ends)
                frame = frame.loc[days.isna() | limits.isna() | (days <= limits)].copy()
            frames[route] = frame
            manifest.append(_availability(entry, item, frame, binding, ids))
            print(f"候選研究｜讀取｜{entry['name_zh']}｜{len(frame)} 筆", flush=True)
    if new_cache_references:
        snapshot["consumer_cache_cleanup"] = _prune_after_cache_build(
            cache_dir, new_cache_references, pinned_cache_identities)
    panel, assessments = _price_panel(frames, records, windows, ends)
    with _phase("features", "18 個曆月，保留缺值與未知公開時間"):
        feature_module = importlib.import_module("stock_daily_feature_catalog")
        prepared = feature_module.build_candidate_features(panel, axes, asof_by_market=ends,
            horizons=list(horizons), source_frames=frames, source_manifest=manifest)
    if prepared["metadata"].get("feature_schema") != FEATURE_SCHEMA:
        raise CandidateRuntimeError("候選特徵 schema 不符。")
    prepared["market_sessions"] = copy.deepcopy(axes)
    # The feature layer adds availability assumptions, missingness and unit
    # limitations. Keep that evidence rather than replacing it with I/O status.
    manifest = prepared["metadata"].get("source_manifest", manifest)
    for item in manifest:
        if item.get("status") == "projection_not_ready":
            item["availability"] = "unknown_availability"
            item["availability_reason_zh"] = "封存投影與候選快取尚未就緒，沒有執行全市場掃描；不等於合法無資料。"
    snapshot.update(query_bindings={item["route_id"]: item["query_binding"] for item in manifest if "query_binding" in item},
        window_by_market={market: {"start": starts[market], "end": ends[market],
            "expected_sessions": len(windows[market]), "calendar_first_session": windows[market][0],
            "full_18_month_axis": bool(axes[market] and axes[market][0] <= starts[market])} for market in windows})
    result = {"prepared": prepared, "panel": panel, "source_frames": frames, "frames": frames,
        "snapshot": snapshot, "identity": identity, "current_identity": identity,
        "source_manifest": manifest, "source_plan": plan, "market_sessions": axes,
        "asof_by_market": ends, "data_quality_assessments": assessments,
        "coverage": {"requested": len(requested), "eligible": len(records), "excluded": excluded,
            "price_usable": sum(item["status"] != "unavailable" for item in assessments),
            "historical_membership_verified": False}, "records": records}
    if read_only:
        result["read_plan"] = _seal_read_plan(actual_read_plan)
        if read_plan is not None and result["read_plan"] != read_plan:
            raise CandidateRuntimeError("read_plan_reproduction_mismatch")
    return result


def _new_run(output_dir=None):
    root = _consumer_path(output_dir or CANDIDATE_ROOT)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(5)
    path = root / run_id
    path.mkdir(parents=True, exist_ok=False)
    return path


def train_candidate_run(*, sample_size=50, seed=None, output_dir=None, cache_dir=None,
                        horizons=DEFAULT_HORIZONS, context=None, min_fit_days=60,
                        selection_days=20, test_days=20, write_reports=False):
    """Create a new isolated candidate; never adopt, publish, or update a ledger."""
    horizons = _horizons(horizons)
    if isinstance(sample_size, bool) or sample_size <= 0:
        raise CandidateRuntimeError("抽樣數量必須是正整數。")
    seed = secrets.randbits(32) if seed is None else int(seed)
    with _phase("read", "核對同一快照與普通股母體"):
        context = context if context is not None else _load_context()
        snapshot = _snapshot(context)
    records = sorted(context["current_identity"]["records"], key=lambda item: (str(item["stock_id"]), str(item["market"])))
    if len({str(item["stock_id"]) for item in records}) != len(records):
        raise CandidateRuntimeError("普通股母體有重複代碼，需先核對股票市場身分。")
    ids = [str(item["stock_id"]) for item in records]
    sampled = sorted(random.Random(seed).sample(ids, min(sample_size, len(ids))))
    run = _new_run(output_dir)
    manifest = {"schema": "stock-daily-selected-candidate-run-v1", "research_only": True, "promotion": False,
        "status": "prepared", "seed": seed, "sample_size_requested": sample_size,
        "sample_size_actual": len(sampled), "sampled_stock_ids": sampled, "population": records,
        "population_sha256": hashlib.sha256(_canonical(records)).hexdigest(),
        "universe_exclusions": copy.deepcopy(context["current_identity"].get("excluded") or []),
        "historical_membership_verified": False, "horizons": list(horizons), "lookback_calendar_months": 18,
        "snapshot_binding": snapshot, "split_rules": {"min_fit_days": min_fit_days,
            "selection_days": selection_days, "test_days": test_days}, "run_dir": str(run)}
    _write_json(run / "run_manifest.json", manifest)
    try:
        feature_module = importlib.import_module("stock_daily_feature_catalog")
        plans_by_horizon = {str(h): _source_plan([h], feature_module.select_source_plan([h], industry=None))
            for h in horizons}
        inputs = load_candidate_inputs(sampled, context=context, cache_dir=cache_dir, horizons=horizons,
            source_plan=_merge_source_plans(plans_by_horizon.values()), allow_full_scan=True)
        _training_horizon_orders(inputs["prepared"], plans_by_horizon)
        candidate = importlib.import_module("stock_daily_candidate_model")
        if candidate.MODEL_SCHEMA != MODEL_SCHEMA:
            raise CandidateRuntimeError("候選模型 schema 不符。")
        # fit_candidate seals the entire model. Supply orchestration evidence
        # BEFORE fitting rather than modifying a sealed model afterward.
        metadata = inputs["prepared"]["metadata"]
        metadata["runtime_input_contract"] = {"source_plan": inputs["source_plan"],
            "source_plan_by_horizon": plans_by_horizon,
            "consumer_cache_dir": str(_consumer_path(cache_dir or CANDIDATE_ROOT / "cache")),
            "horizons": list(horizons), "lookback_calendar_months": 18,
            "feature_module_sha256": _sha_file(importlib.import_module("stock_daily_feature_catalog").__file__)}
        metadata["snapshot_binding"] = inputs["snapshot"]
        metadata["source_manifest"] = inputs["source_manifest"]
        metadata["sampling"] = {key: manifest[key] for key in ("seed", "sampled_stock_ids", "population_sha256")}
        metadata["data_quality_assessments"] = inputs["data_quality_assessments"]
        with _phase("train", f"{len(sampled)} 檔，{len(horizons)} 個期限"):
            model = candidate.fit_candidate(inputs["prepared"], seed=seed, min_fit_days=min_fit_days,
                selection_days=selection_days, test_days=test_days)
        model_path = run / "candidate_model.json"
        saved = candidate.save_candidate(model, model_path)
        # Round-trip through the public loader before inference; sidecars are its responsibility.
        loaded, model_digest = _load_bound_model(candidate, model_path)
        payloads = []
        if write_reports:
            payloads = _predict_payloads(loaded, model_path, inputs, horizons, expected_model_sha256=model_digest)
            _write_reports(payloads, run / "reports")
        metrics = {str(h): _horizon_metrics(loaded, h) for h in horizons}
        _write_json(run / "metrics.json", {"schema": "stock-daily-selected-candidate-metrics-v1",
            "research_only": True, "promotion": False, "horizons": metrics})
        training_states = {str(h): (loaded.get("horizons") or {}).get(str(h), {}).get("status", "not_trained") for h in horizons}
        manifest.update(status="completed" if all(value == "trained" for value in training_states.values())
                else "completed_with_untrained_or_partial_horizons", training_status_by_horizon=training_states,
            model_binding=_model_binding(model_path, loaded),
            save_result=saved, snapshot_binding=inputs["snapshot"], source_manifest=inputs["source_manifest"],
            coverage=inputs["coverage"], data_quality_assessments=inputs["data_quality_assessments"],
            metrics=metrics, metrics_path=str(run / "metrics.json"), reports=len(payloads))
    except Exception as exc:
        manifest.update(status="failed", error={"type": type(exc).__name__, "detail": str(exc)})
        _write_json(run / "run_manifest.json", manifest)
        raise
    _write_json(run / "run_manifest.json", manifest)
    return {"run_dir": str(run), "model_path": str(model_path), "manifest": manifest, "payloads": payloads}


def _model_binding(path, model):
    metadata = model.get("metadata") or {}
    return {"path": str(Path(path).resolve()), "sha256": _sha_file(path),
        "schema": model.get("schema", MODEL_SCHEMA), "feature_schema": metadata.get("feature_schema"),
        "feature_order_by_horizon": copy.deepcopy(metadata.get("feature_order_by_horizon")),
        "research_only": True, "promotion": False}


def _load_bound_model(candidate, path):
    digest = _sha_file(path)
    model = candidate.load_candidate(path)
    if _sha_file(path) != digest:
        raise CandidateRuntimeError("候選模型在載入期間變更，停止以免模型與報告綁定不同版本。")
    return model, digest


def _resolve_stocks(query, identity):
    queries = list(query) if isinstance(query, (list, tuple)) else [str(query)]
    resolved = []
    for text in queries:
        text = str(text).strip().casefold()
        exact, partial = [], []
        for item in identity["records"]:
            code, name = str(item["stock_id"]), str(item.get("stock_name") or item.get("name") or "")
            if text and text in {code.casefold(), name.casefold()}:
                exact.append(item)
            elif text and text in name.casefold():
                partial.append(item)
        matches = exact or partial
        if len(matches) != 1:
            raise CandidateRuntimeError("股票查詢沒有唯一的已驗證普通股結果：" + text)
        if str(matches[0]["stock_id"]) not in resolved:
            resolved.append(str(matches[0]["stock_id"]))
    return resolved


def _feature_values(prepared, stock, market, day, model, horizon):
    import pandas as pd
    frame = prepared["features"]
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    selected = frame.loc[(frame["stock_id"].astype(str) == stock) & (frame["market"].astype(str).str.upper() == market)]
    key = "session_date" if "session_date" in selected else "date"
    selected = selected.loc[selected[key].map(_day) == day]
    if selected.empty:
        return []
    metadata = model["metadata"]
    definitions = _definition_map(metadata)
    eligible = (metadata.get("feature_order_by_horizon") or {}).get(str(horizon), [])
    order = list(metadata.get("feature_order") or eligible)
    head = (model.get("horizons") or {}).get(str(horizon), {})
    values = []
    for name in order:
        definition = definitions.get(name) or {}
        selected_by_task, validity, coverage = {}, {}, {}
        for task in ("direction", "return"):
            kind = (head.get("selected") or {}).get(task)
            candidate = (head.get("candidates") or {}).get(kind, {}) if kind is not None else {}
            transformer = candidate.get("transformer") or candidate
            numeric = name in (transformer.get("effective_feature_order") or [])
            indicator = name in (transformer.get("effective_indicator_order") or [])
            selected_by_task[task] = {"selected": numeric or indicator, "numeric": numeric,
                "missing_indicator": indicator, "candidate_kind": kind}
            validity[task] = copy.deepcopy((transformer.get("feature_validity") or {}).get(name))
            coverage[task] = copy.deepcopy((candidate.get("feature_coverage") or {}).get(name))
        inference_coverage = copy.deepcopy(((prepared["metadata"].get("coverage") or {}).get("features") or {}).get(name))
        if isinstance(inference_coverage, dict):
            total = inference_coverage.get("total_rows", inference_coverage.get("n_denominator"))
            finite = inference_coverage.get("available_rows", inference_coverage.get("n_finite"))
            missing = inference_coverage.get("missing_rows", inference_coverage.get("n_missing"))
            inference_coverage.update(n_denominator=total, n_finite=finite, n_missing=missing,
                coverage=finite / total if isinstance(total, (int, float)) and total > 0 and finite is not None else None)
        values.append({"name": name, "label_zh": definition.get("label_zh", name),
            "value": _clean(selected.iloc[-1].get(name)), "unit": definition.get("unit"),
            "category": definition.get("category"), "source_route": definition.get("source_route", definition.get("route")),
            "calculation_zh": definition.get("calculation_zh"), "eligible_for_horizon": name in eligible,
            "selected": any(item["selected"] for item in selected_by_task.values()),
            "selected_by_task": selected_by_task, "feature_validity": validity,
            "coverage": {"training_by_task": coverage, "inference": inference_coverage}})
    return values


def _stock_context(prepared, stock):
    context = copy.deepcopy(prepared.get("analysis_context") or {})
    if isinstance(context.get(stock), dict):
        context = context[stock]
    for key in ("fundamentals", "flows", "news", "global", "technical"):
        context.setdefault(key, [])
    for evidence in context.get("sources") or []:
        route = evidence.get("route_id", "")
        category = "news" if route == "news_and_hot_topics_raw" else "global" if route in GLOBAL_ROUTES else (
            "fundamentals" if route in {"finmind_month_revenue", "finmind_financial_statements", "finmind_balance_sheet",
                "finmind_cash_flows_statement", "finmind_per", "finmind_dividend", "finmind_dividend_result",
                "official_finance_reporting_deadline", "official_sector_features", "finmind_stock_info"} else
            "technical" if route in {"finmind_price_adj", "official_market_daily", "official_trading_restrictions"} else "flows")
        context[category].append(copy.deepcopy(evidence))
    return context


def _horizon_metrics(model, horizon):
    item = (model.get("horizons") or {}).get(str(horizon), {})
    candidates = item.get("candidates") or {}
    return {"comparison": {name: {key: copy.deepcopy(candidate.get(key)) for key in
            ("status", "reason_zh", "selection", "test", "n_denominator")} for name, candidate in candidates.items()},
        "selection": {"selected": copy.deepcopy(item.get("selected")),
            "reasons": copy.deepcopy(item.get("selection_reasons")), "lock_sha256": item.get("selection_lock_sha256")},
        "holdout": {name: copy.deepcopy(candidate.get("test")) for name, candidate in candidates.items()},
        **{key: copy.deepcopy(item.get(key)) for key in ("status", "reason_zh", "splits", "denominator",
            "baselines", "paired_differences", "calibration_status", "feature_coverage", "feature_validity",
            "legacy_seven_feasibility", "limitations")},
        "model_metrics": copy.deepcopy(model.get("metrics") or {})}


def _feature_limitations(prepared, stock, market, stock_count):
    metadata = prepared.get("metadata") or {}
    applicable, unattributed, texts = [], [], []

    def collect(items, *, stock_scoped=False):
        if isinstance(items, (str, dict)):
            items = [items]
        for original in items or []:
            item = {"reason_zh": original} if isinstance(original, str) else copy.deepcopy(original)
            if not isinstance(item, dict):
                continue
            targets = item.get("stock_ids")
            if targets is None and item.get("stock_id") is not None:
                targets = [item["stock_id"]]
            if targets is not None and stock not in {str(value) for value in targets}:
                continue
            markets = item.get("markets")
            if markets is None and item.get("market") is not None:
                markets = [item["market"]]
            if markets is not None and market not in {str(value).upper() for value in markets}:
                continue
            known_scope = stock_scoped or targets is not None or stock_count == 1 or item.get("scope") in {
                "global", "all-stocks", "all_stocks"}
            if not known_scope:
                unattributed.append(item)
                continue
            applicable.append(item)
            reason = item.get("reason_zh")
            if isinstance(reason, str) and reason.strip():
                texts.append(reason.strip())

    collect(metadata.get("limitations"))
    for container in (prepared, metadata):
        by_stock = container.get("limitations_by_stock") or {}
        if isinstance(by_stock, dict):
            collect(by_stock.get(stock), stock_scoped=True)
    context = _stock_context(prepared, stock)
    collect(context.get("limitations"), stock_scoped=True)
    if unattributed:
        texts.append("本批次另有尚未分股的來源限制，未當成本股已發生的限制；原始彙總保留於結構化批次證據。")
    return list(dict.fromkeys(texts)), {"stock_id": stock, "market": market,
        "applicable": applicable, "unattributed_batch": unattributed,
        "unattributed_applied_to_stock": False}


def _predict_payloads(model, model_path, inputs, horizons, *, expected_model_sha256=None):
    import pandas as pd
    candidate = importlib.import_module("stock_daily_candidate_model")
    with _phase("predict", "同一模型與封存特徵順序"):
        predictions = candidate.predict_candidate(model, inputs["prepared"])
    if not isinstance(predictions, pd.DataFrame):
        raise CandidateRuntimeError("候選推論未回傳資料表。")
    binding = _model_binding(model_path, model)
    if expected_model_sha256 is not None and binding["sha256"] != expected_model_sha256:
        raise CandidateRuntimeError("候選模型在推論期間變更，報告綁定無法確認。")
    payloads = []
    for stock_info in inputs["records"]:
        stock, market = str(stock_info["stock_id"]), str(stock_info["market"]).upper()
        day = inputs["asof_by_market"][market]
        feature_notes, feature_evidence = _feature_limitations(inputs["prepared"], stock, market, len(inputs["records"]))
        history = inputs["panel"].loc[inputs["panel"]["stock_id"].astype(str) == stock]
        prices = []
        for row in history.to_dict("records"):
            prices.append({"date": row["session_date"], **{key: _clean(row.get(key)) for key in
                ("open", "high", "low", "volume", "amount", "transactions")},
                "close": _clean(row.get("adjusted_close")), "adjusted_close": _clean(row.get("adjusted_close")),
                "price_basis": "adjusted", "adjusted_source_route": "finmind_price_adj",
                "adjusted_ohlc": {key: _clean(row.get("adjusted_close" if key == "close" else key))
                    for key in ("open", "high", "low", "close")},
                **{"raw_" + key: _clean(row.get("raw_" + key)) for key in ("open", "high", "low", "close")},
                "raw_price_basis": "official_unadjusted", "activity_source_route": "official_market_daily",
                "volume_unit": row.get("volume_unit")})
        for horizon in horizons:
            selected = predictions
            if not selected.empty:
                hkey = "horizon_sessions" if "horizon_sessions" in selected else "horizon"
                if not {"stock_id", "market", hkey}.issubset(selected):
                    raise CandidateRuntimeError("候選推論缺少股票、市場或期限身分。")
                selected = selected.loc[(selected.stock_id.astype(str) == stock) &
                    (selected.market.astype(str).str.upper() == market) & (selected[hkey] == horizon)]
            if len(selected) > 1:
                raise CandidateRuntimeError("候選推論含重複股票與期限。")
            prediction = _clean(selected.iloc[0].to_dict()) if len(selected) else {
                "status": "no_estimate", "reason": "candidate_prediction_unavailable",
                "prob_up": None, "expected_return": None}
            axis = inputs["market_sessions"][market]
            future = [value for value in axis if value > day]
            target = future[horizon - 1] if len(future) >= horizon else None
            limitations = ["本報告為候選研究，未正式採用，不更新既有模型、指標或前瞻帳本。",
                "目前普通股資格不代表歷史成分股資格；歷史時點完整性仍未證實。",
                "期末日、採集時間與法定期限不視為實際公告時間；未知公開時間不回填歷史。",
                "新聞僅作原始證據摘要，不據此宣稱價格變動的因果關係。"]
            if target is None:
                limitations.append("已驗證未來市場日曆不足，目標交易日未知，不以平日推算。")
            if horizon >= 60:
                limitations.append("長期限標籤重疊，訊號日數不等於獨立樣本數，機率校準與估計穩定性須另核對。")
            quality = next((item for item in inputs["data_quality_assessments"] if item["stock_id"] == stock), None)
            if quality and quality["status"] != "complete":
                state_zh = {"partial": "部分缺漏", "unavailable": "不可使用"}.get(quality["status"], "尚未確認")
                limitations.append(f"價格歷史狀態為{state_zh}，缺 {quality['missing_sessions']} 個市場交易日；不補價格或標籤。")
            limitations = list(dict.fromkeys(limitations + feature_notes))
            payload = {"schema": REPORT_SCHEMA, "language": "zh-TW", "research_only": True, "promotion": False,
                "stock": {"stock_id": stock, "stock_name": stock_info.get("stock_name", stock_info.get("name")),
                    "market": market, "industry": stock_info.get("industry")},
                "horizon_sessions": horizon, "asof": day, "target_session_date": target,
                "prediction": prediction, "model_binding": binding, "snapshot_binding": inputs["snapshot"],
                "source_manifest": inputs["source_manifest"], "feature_values": _feature_values(
                    inputs["prepared"], stock, market, day, model, horizon),
                "analysis_context": _stock_context(inputs["prepared"], stock),
                "metrics": _horizon_metrics(model, horizon),
                "feature_limitations": feature_evidence,
                "limitations": limitations, "price_history": prices, "price_volume_unit": "股", "data_quality_assessment": quality,
                "generated_at": datetime.now(timezone.utc).isoformat()}
            payload = _clean(payload)
            payload["runtime_payload_sha256"] = hashlib.sha256(_canonical(payload)).hexdigest()
            payloads.append(payload)
    return payloads


def _write_reports(payloads, output_dir):
    report = importlib.import_module("stock_candidate_report")
    output_dir = _consumer_path(output_dir)
    with _phase("report", f"{len(payloads)} 份繁體中文研究報告"):
        for payload in payloads:
            stock, horizon = payload["stock"]["stock_id"], payload["horizon_sessions"]
            target = output_dir / stock / ("H" + str(horizon))
            result = report.write_candidate_report(payload, target)
            pdf_path = result.get("pdf_path")
            if pdf_path is None:
                raise CandidateRuntimeError("候選報告未回傳 pdf_path，不能視為可交付。")
            checked = report.validate_candidate_report(payload, pdf_path)
            if not isinstance(checked, dict) or checked.get("valid") is not True:
                raise CandidateRuntimeError("候選報告內容或綁定驗證未通過。")
            _write_json(target / "runtime_report_receipt.json", {"research_only": True, "promotion": False,
                "runtime_payload_sha256": payload["runtime_payload_sha256"], "result": result, "validation": checked})


def generate_candidate_reports(stock_query, *, model_path, output_dir, cache_dir=None,
                               horizons=(1, 63), pinned_head_sha256=None):
    horizons = _horizons(horizons)
    candidate = importlib.import_module("stock_daily_candidate_model")
    model, model_digest = _load_bound_model(candidate, model_path)
    metadata = model.get("metadata") or {}
    if model.get("schema") != MODEL_SCHEMA or metadata.get("feature_schema") != FEATURE_SCHEMA:
        raise CandidateRuntimeError("指定模型不是已封存的新候選 schema。")
    rules = metadata.get("runtime_input_contract")
    if not isinstance(rules, dict) or not set(horizons).issubset(rules.get("horizons") or []):
        raise CandidateRuntimeError("模型未封存所需期限與來源規則，不可臨時換欄位。")
    feature_module = importlib.import_module("stock_daily_feature_catalog")
    if _sha_file(feature_module.__file__) != rules.get("feature_module_sha256"):
        raise CandidateRuntimeError("候選特徵程式已變更；需以原封存版本推論或另建候選。")
    plans = rules.get("source_plan_by_horizon")
    if not isinstance(plans, dict) or any(str(h) not in plans for h in horizons):
        raise CandidateRuntimeError("模型未封存各期限來源規則，不可於推論時重新選擇。")
    with _phase("read", "模型封存規則與明確快照"):
        context = _load_context(pinned_head_sha256=pinned_head_sha256)
        ids = _resolve_stocks(stock_query, context["current_identity"])
    plan = _merge_source_plans([plans[str(h)] for h in horizons])
    inputs = load_candidate_inputs(ids, context=context, cache_dir=cache_dir or rules.get("consumer_cache_dir"),
        horizons=rules["horizons"], source_plan=plan, allow_full_scan=False)
    _apply_frozen_schema(inputs["prepared"], model, horizons, plan)
    trained_head = (metadata.get("snapshot_binding") or {}).get("head_sha256")
    if trained_head != inputs["snapshot"]["head_sha256"]:
        inputs["snapshot"]["version_labels"].append({"code": "candidate_inference_snapshot_differs",
            "training_head_sha256": trained_head, "inference_head_sha256": inputs["snapshot"]["head_sha256"],
            "reason_zh": "推論使用新的完整封存快照，未混接訓練快照。"})
    payloads = _predict_payloads(model, model_path, inputs, horizons, expected_model_sha256=model_digest)
    _write_reports(payloads, _consumer_path(output_dir))
    return payloads


class _ChineseParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("add_help", False)
        super().__init__(*args, **kwargs)
        self._positionals.title = "位置參數"
        self._optionals.title = "選項"
        self.add_argument("-h", "--help", action="help", help="顯示說明並結束")

    def format_usage(self):
        return super().format_usage().replace("usage:", "用法：", 1)

    def format_help(self):
        return super().format_help().replace("usage:", "用法：", 1)

    def error(self, message):
        for original, translated in {"the following arguments are required:": "缺少必要參數：",
                "unrecognized arguments:": "無法識別的參數：", "invalid int value:": "整數格式不正確：",
                "invalid choice:": "無效選項：", "choose from": "可選值", "expected one argument": "必須有一個參數值",
                "argument ": "參數 "}.items():
            message = message.replace(original, translated)
        self.print_usage(sys.stderr)
        self.exit(2, "候選參數錯誤：" + message + "\n")


def main(argv=None):
    parser = _ChineseParser(description="18 個月共用候選研究；不切換正式 C0 模型", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True, title="執行模式")
    train = commands.add_parser("train", help="以普通股隨機抽樣建立新候選")
    train.add_argument("--sample-size", type=int, default=50, help="抽樣檔數，預設 50")
    train.add_argument("--seed", type=int, help="明確重現抽樣；未指定則每輪產生新種子並封存")
    train.add_argument("--horizons", default="1,5,20,60,63", help="市場交易日期限，以逗號分隔")
    train.add_argument("--output-dir", type=Path, help="候選根目錄，其下建立獨立執行目錄")
    train.add_argument("--cache-dir", type=Path, help="消費端快取目錄")
    train.add_argument("--write-reports", action="store_true", help="明確產生本次全部抽樣股票與期限的研究報告")
    report = commands.add_parser("report", help="以明確候選模型產生單股研究報告")
    report.add_argument("--stock", required=True, help="普通股代碼或名稱")
    report.add_argument("--candidate-model", required=True, type=Path, help="候選模型 JSON")
    report.add_argument("--horizons", default="1,63", help="市場交易日期限，以逗號分隔")
    report.add_argument("--output-dir", type=Path, help="研究報告目錄")
    report.add_argument("--cache-dir", type=Path, help="消費端快取目錄")
    report.add_argument("--pinned-head-sha256", help="明確指定封存快照；缺失時停止，不混用目前快照")
    args = parser.parse_args(argv)
    try:
        if args.command == "train":
            result = train_candidate_run(sample_size=args.sample_size, seed=args.seed, output_dir=args.output_dir,
                cache_dir=args.cache_dir, horizons=_horizons(args.horizons), write_reports=args.write_reports)
            print("候選研究完成，未正式採用。執行目錄：" + result["run_dir"], flush=True)
        else:
            output = args.output_dir or (_new_run(CANDIDATE_ROOT / "reports") / "reports")
            payloads = generate_candidate_reports(args.stock, model_path=args.candidate_model, output_dir=output,
                cache_dir=args.cache_dir, horizons=_horizons(args.horizons), pinned_head_sha256=args.pinned_head_sha256)
            print(f"候選研究完成，共 {len(payloads)} 份報告，未正式採用。目錄：{output}", flush=True)
        return 0
    except (OSError, ValueError, KeyError, TypeError, ImportError, RuntimeError) as exc:
        print("候選研究受阻：" + type(exc).__name__ + "：" + str(exc), file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
