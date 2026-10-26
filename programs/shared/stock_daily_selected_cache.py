"""Explicit offline preparation of runtime-compatible, consumer-owned row caches."""
from __future__ import annotations

import argparse
import copy
import contextlib
import errno
import hashlib
import importlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

import stock_daily_candidate_runtime as runtime
import stock_cache_lifecycle as lifecycle

RECEIPT_SCHEMA = "stock-daily-selected-cache-preparation-v1"
ALL_ORDINARY_INDEX = "all_ordinary_verified.json"
_CONTEXT_ROUTES = frozenset({"official_calendar", "finmind_stock_info"})
_COMPLETE_STATUSES = frozenset({"success", "complete", "available", "committed"})
_LOCAL_GUARDS = {}
_GUARDS_LOCK = threading.Lock()
_HELD_GUARDS = threading.local()


class SelectedCacheBusy(runtime.CandidateRuntimeError):
    """Another manual/background preparation owns the OS byte-range guard."""


@contextmanager
def consumer_cache_guard(cache_dir):
    """Nonblocking, thread-reentrant guard; its file is never a stale-lock token."""
    root = runtime._consumer_path(cache_dir)
    key = str(root)
    held = getattr(_HELD_GUARDS, "paths", set())
    if key in held:
        yield
        return
    with _GUARDS_LOCK:
        local = _LOCAL_GUARDS.setdefault(key, threading.Lock())
    if not local.acquire(blocking=False):
        raise SelectedCacheBusy("consumer_cache_preparation_busy")
    handle, locked = None, False
    try:
        root.mkdir(parents=True, exist_ok=True)
        handle = (root / ".selected_cache_prepare.guard").open("a+b")
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.lockf(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB, 1, 0)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise SelectedCacheBusy("consumer_cache_preparation_busy") from exc
            raise
        locked = True
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
            handle.flush()
        _HELD_GUARDS.paths = held | {key}
        yield
    finally:
        _HELD_GUARDS.paths = held
        try:
            if handle is not None:
                try:
                    if locked:
                        handle.seek(0)
                        if os.name == "nt":
                            import msvcrt
                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.lockf(handle.fileno(), fcntl.LOCK_UN, 1, 0)
                finally:
                    handle.close()
        finally:
            local.release()


def preparation_code_binding():
    modules = [runtime, importlib.import_module("stock_data_sources"),
        importlib.import_module("stock_query_projection"), importlib.import_module("stock_daily_feature_catalog")]
    return {"modules": {Path(module.__file__).name: runtime._sha_file(module.__file__) for module in modules},
        "preparer_sha256": runtime._sha_file(__file__), "python_version": sys.version}


def _stock_key(row):
    value = row.get("stock_id", row.get("data_id", row.get("code")))
    return None if value is None else str(value)


def _frozen_inputs(model_path, horizons, pinned_head_sha256, stock_ids):
    candidate = importlib.import_module("stock_daily_candidate_model")
    feature = importlib.import_module("stock_daily_feature_catalog")
    model, digest = runtime._load_bound_model(candidate, model_path)
    metadata = model.get("metadata") or {}
    rules = metadata.get("runtime_input_contract") or {}
    if model.get("schema") != runtime.MODEL_SCHEMA or metadata.get("feature_schema") != runtime.FEATURE_SCHEMA:
        raise runtime.CandidateRuntimeError("指定模型不是已封存的候選模型。")
    if (not set(horizons).issubset(rules.get("horizons") or [])
            or rules.get("lookback_calendar_months") != 18):
        raise runtime.CandidateRuntimeError("模型未封存指定期限或 18 個曆月的輸入契約。")
    if runtime._sha_file(feature.__file__) != rules.get("feature_module_sha256"):
        raise runtime.CandidateRuntimeError("特徵程式與模型封存雜湊不同，不能準備不同規則的快取。")
    plans = rules.get("source_plan_by_horizon")
    if not isinstance(plans, dict) or any(str(value) not in plans for value in horizons):
        raise runtime.CandidateRuntimeError("模型缺少指定期限的封存來源計畫。")
    plan = runtime._source_plan(rules["horizons"], runtime._merge_source_plans(
        [plans[str(value)] for value in horizons]))
    import stock_daily_selected_deployment as deployment
    active = deployment.active_selected_binding()
    if (active is not None and Path(active["model_path"]).resolve() == Path(model_path).resolve()
            and active["model_sha256"] == digest):
        context = deployment.load_selected_context(pinned_head_sha256, active)
    else:
        context = runtime._load_context(pinned_head_sha256=pinned_head_sha256)
    snapshot = runtime._snapshot(context)
    if pinned_head_sha256 is not None and snapshot["head_sha256"] != str(pinned_head_sha256).lower().strip():
        raise runtime.CandidateRuntimeError("實際封存 head 與明確指定的 head 不同。")
    positives = {str(item["stock_id"]): item for item in context["current_identity"]["records"]}
    if stock_ids is None:
        ids = sorted(positives)
    else:
        values = stock_ids.split(",") if isinstance(stock_ids, str) else stock_ids
        ids = list(dict.fromkeys(str(value).strip() for value in values))
        if any(value not in positives for value in ids):
            raise runtime.CandidateRuntimeError("只能準備目前官方已驗證普通股，不能略過未知股票或 ETF。")
    if not ids:
        raise runtime.CandidateRuntimeError("沒有可準備的官方普通股。")
    records = [copy.deepcopy(positives[value]) for value in ids]
    ends = {str(key).upper(): runtime._day(value) for key, value in snapshot["asof_by_market"].items()}
    axes = {str(key).upper(): list(value) for key, value in context["market_sessions"].items()}
    verified = {str(key).upper(): value for key, value in
        (context["calendar_evidence"].get("calendar_verified_by_market") or {}).items()}
    starts, windows = {}, {}
    for market in sorted({str(item["market"]).upper() for item in records}):
        if not ends.get(market) or verified.get(market) is not True:
            raise runtime.CandidateRuntimeError("市場資料日或日曆未驗證：" + market)
        starts[market] = runtime._month_start(ends[market])
        days = [day for day in axes.get(market, []) if starts[market] <= day <= ends[market]]
        if not days or days[-1] != ends[market]:
            raise runtime.CandidateRuntimeError("已驗證日曆未涵蓋市場資料日：" + market)
        windows[market] = {"start": starts[market], "end": ends[market], "expected_sessions": len(days)}
    return {"model_sha256": digest, "rules": copy.deepcopy(rules), "plan": plan,
        "context": context, "snapshot": snapshot, "records": records, "stock_ids": ids,
        "starts": starts, "ends": ends, "windows": windows,
        "all_stocks": set(ids) == set(positives), "official_stock_count": len(positives)}


def _requests(route, usage, frozen):
    records, starts, ends = frozen["records"], frozen["starts"], frozen["ends"]
    if route in runtime.GLOBAL_ROUTES:
        lower, upper = runtime._read_bounds(route, usage, starts, ends, records)
        return {None: runtime._query_scope(route, frozen["stock_ids"], lower, upper)}
    result = {}
    for record in records:
        stock = str(record["stock_id"])
        lower, upper = runtime._read_bounds(route, usage, starts, ends, [record])
        result[stock] = runtime._query_scope(route, [stock], lower, upper)
    return result


def _selected(row, route, scope):
    return runtime._row_selected(row, route, scope["stock_ids"] or [], scope["start"], scope["end"])


def _row_targets(row, route, requests):
    stock = _stock_key(row)
    if route in runtime.GLOBAL_ROUTES:
        return [None]
    return requests.keys() if stock is None else ([stock] if stock in requests else [])


def _file_identity(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _shared_window(scope):
    def edge(value, choose):
        return choose(value.values()) if isinstance(value, dict) and value else value
    return {"stock_ids": [], "start": edge(scope["start"], min), "end": edge(scope["end"], max)}


def _reuse_caches(cache_dir, identity, route, requests, *, verified_files=None):
    """Reuse exact verified metadata, or read each required physical component once."""
    pending, selected, groups, reused = dict(requests), {}, {}, {}
    verified = {}

    def unchanged(path, descriptor):
        key = (str(Path(path)), descriptor["sha256"], descriptor["size"])
        if key not in verified:
            verified[key] = runtime._verified_cache_file(path, descriptor, verified_files)
        return verified[key]

    representative = next(iter(requests.values()), {})
    with lifecycle.catalog_lock(cache_dir):
        for path in runtime._cache_candidates(cache_dir, identity, representative):
            if not pending:
                break
            document = runtime._cache_document(path)
            if not runtime._cache_identity_compatible(document.get("identity"), identity):
                continue
            targets = {key: scope for key, scope in pending.items()
                if runtime._scope_covers(document["scope"], scope)}
            if not targets:
                continue
            components = runtime._cache_components(document)
            base = path.parent.parent
            manifest_descriptor = {"sha256": runtime._sha_file(path), "size": path.stat().st_size}
            trusted = unchanged(path, manifest_descriptor)
            trusted = trusted and all(unchanged(leaf, proof)
                for component in components for leaf, proof in runtime._cache_component_paths(base, component))
            for key, scope in targets.items():
                binding = {"manifest_path": str(path), "manifest_sha256": manifest_descriptor["sha256"],
                    "rows_sha256": document["rows"]["sha256"], "manifest_size": manifest_descriptor["size"],
                    "rows": copy.deepcopy(document["rows"]), "shared_rows": copy.deepcopy(document.get("shared_rows") or [])}
                if trusted and document["scope"] == scope:
                    reused[key] = {"row_count": sum(item["row_count"] for item in components),
                        "reader": "consumer_sealed_cache", "cache": binding, "scope": scope,
                        "validation": "unchanged_verified_inventory_files"}
                else:
                    selected[key] = {"row_count": 0, "reader": "consumer_sealed_cache", "cache": binding, "scope": scope}
                    for component in components:
                        group_key = (str(base), runtime._cache_key(component))
                        group = groups.setdefault(group_key, {"base": base, "component": component, "targets": {}})
                        group["targets"][key] = scope
                del pending[key]
    data_uses, component_memo = {}, {}
    for group in groups.values():
        path = runtime._cache_component_paths(group["base"], group["component"])[0][0]
        data_uses[str(path)] = data_uses.get(str(path), 0) + 1
    for group in groups.values():
        data_path = runtime._cache_component_paths(group["base"], group["component"])[0][0]
        memo = component_memo if data_uses[str(data_path)] > 1 else None
        targets, windows, keyed_counts = group["targets"], {}, dict.fromkeys(group["targets"], 0)
        for key, scope in targets.items():
            window_key = runtime._cache_key(_shared_window(scope))
            windows.setdefault(window_key, {"scope": scope, "keys": [], "count": 0})["keys"].append(key)
        for ordinal, row in runtime._iter_cache_component(group["base"], group["component"], memo=memo):
            stock = _stock_key(row)
            if route in runtime.GLOBAL_ROUTES or stock is None:
                for window in windows.values():
                    if _selected(row, route, window["scope"]):
                        window["count"] += 1
            elif stock in targets and _selected(row, route, targets[stock]):
                keyed_counts[stock] += 1
        for window in windows.values():
            for key in window["keys"]:
                selected[key]["row_count"] += window["count"] + keyed_counts[key]
    reused.update(selected)
    return pending, reused


def _stream_to_spool(sources, item, route, requests, cache_dir, database):
    import stock_query_projection as query

    database.execute("CREATE TABLE rows (ordinal INTEGER PRIMARY KEY, stock TEXT, row BLOB NOT NULL)")
    database.execute("CREATE INDEX rows_stock ON rows(stock, ordinal)")
    count = 0

    def consume(iterator):
        nonlocal count
        for row in iterator:
            count += 1
            if not isinstance(row, dict):
                raise runtime.CandidateRuntimeError("封存來源含非物件資料列。")
            targets = _row_targets(row, route, requests)
            if any(_selected(row, route, requests[key]) for key in targets):
                database.execute("INSERT INTO rows VALUES (?, ?, ?)",
                    (count, _stock_key(row), runtime._canonical(row)))

    # Unfiltered sealed shards preserve runtime's date aliases, unknown dates and
    # unkeyed rows. All predicates are applied by the exact runtime helper.
    try:
        result = query.query_published_rows(item["root"], item["artifact_descriptor"], route,
            stock_ids=None, start_date=None, end_date=None, source_identity=item.get("source_identity"),
            parent_identity=item.get("parent_identity"), allow_build=False, consumer_temp_root=cache_dir)
    except (query.UnsupportedProjectionError, query.ProjectionUnavailableError) as exc:
        artifacts = []
        for artifact, iterator in sources.iter_artifact_rows(route):
            descriptor = item["artifact_descriptor"]
            if artifact.content_sha256 != descriptor["sha256"]:
                raise runtime.CandidateRuntimeError("串流來源與封存來源雜湊不同。")
            ref = (artifact.metadata or {}).get("selected_ref") or {}
            if ref.get("sha256") != descriptor["sha256"] or ref.get("size") != descriptor["size"]:
                raise runtime.CandidateRuntimeError("串流來源缺少完整 hash/size 驗證證據。")
            consume(iterator)
            artifacts.append({"sha256": artifact.content_sha256, "snapshot_id": artifact.snapshot_id,
                "status": artifact.status, "selected_ref": copy.deepcopy(ref)})
        if len(artifacts) != 1:
            raise runtime.CandidateRuntimeError("封存串流來源缺失或有多個版本。")
        binding = {"reader": "sealed_stream_fallback", "fallback_reason": type(exc).__name__ + ":" + str(exc),
            "artifacts": artifacts, "source_hash_verified": True, "stream_exhausted": True}
    else:
        try:
            consume(result.iter_rows())
            binding = {"reader": "sealed_projection", "cache_key": result.cache_key,
                "projection_predicate": copy.deepcopy(result.predicate), "lineage": copy.deepcopy(result.lineage),
                "metrics": dict(result.metrics), "completion_sha256": runtime._sha_file(result.completion_path),
                "index_sha256": runtime._sha_file(result.index_manifest_path),
                "source_hash_verified": "sealed_projection_build", "stream_exhausted": True}
        finally:
            result.close()
    database.commit()
    binding.update(rows_examined=count, source_identity=runtime._cache_identity(item, route),
        source_provenance=runtime._cache_provenance(item))
    return binding


def _spooled_rows(database, route, key, scope, *, unkeyed=False):
    if route in runtime.GLOBAL_ROUTES:
        cursor = database.execute("SELECT ordinal, row FROM rows ORDER BY ordinal")
    elif unkeyed:
        cursor = database.execute("SELECT ordinal, row FROM rows WHERE stock IS NULL ORDER BY ordinal")
    else:
        cursor = database.execute("SELECT ordinal, row FROM rows WHERE stock = ? ORDER BY ordinal", (key,))
    for ordinal, raw in cursor:
        row = json.loads(raw.decode("utf-8"))
        if _selected(row, route, scope):
            yield ordinal, row


def _save_streamed_cache(cache_dir, identity, scope, rows, evidence, temporary_dir, *, shared_rows=()):
    own = runtime._write_cache_component(cache_dir, identity, rows, temporary_dir)
    reference = runtime._save_cache_document(cache_dir, identity, scope, own, shared_rows, evidence)
    return {"row_count": own["row_count"] + sum(item["row_count"] for item in shared_rows),
        "reader": evidence["reader"], "scope": scope, "cache": reference}


def _coverage(results, route, stock_ids, publication_status):
    stocks = {}
    for stock in stock_ids:
        item = copy.deepcopy(results[None if route in runtime.GLOBAL_ROUTES else stock])
        item["cache_ready"] = True
        item["status"] = "available" if item["row_count"] else "published_query_empty"
        item["legitimate_empty"] = False
        if not item["row_count"]:
            item.update(empty_basis="verified_exhaustive_query_only", absence_status="absence_unverified",
                financial_absence_proved=False)
        item["publication_status"] = publication_status
        stocks[stock] = item
    return stocks


def _coverage_status(publication_status):
    status = str(publication_status).casefold()
    return "source_partial" if status == "partial" else "published_query_verified" if status in _COMPLETE_STATUSES else (
        "source_completeness_unverified")


def prepare_selected_cache(model_path, cache_dir, *, stock_ids=None, horizons=(1, 63), pinned_head_sha256=None,
                           verified_files=None, _defer_prune=False):
    """Prepare all current ordinary stocks by default; never called by report jobs.

    Returns a persisted receipt. ``ready`` concerns the complete requested cache
    scope, not model quality, formal promotion, delivery or historical PIT proof.
    Missing publications return a non-ready receipt; PARTIAL remains explicit
    without blocking a verified cache. Integrity failures persist a failed
    receipt and raise without publishing the failed route.
    """
    model_path, cache_dir = Path(model_path).resolve(), runtime._consumer_path(cache_dir)
    if cache_dir == model_path or cache_dir in model_path.parents:
        raise runtime.CandidateRuntimeError("快取目錄不可涵蓋模型來源，請指定獨立 consumer cache 目錄。")
    with consumer_cache_guard(cache_dir):
        return _prepare_selected_cache(model_path, cache_dir, stock_ids=stock_ids,
            horizons=horizons, pinned_head_sha256=pinned_head_sha256, verified_files=verified_files, defer_prune=_defer_prune)


def _prepare_selected_cache(model_path, cache_dir, *, stock_ids, horizons, pinned_head_sha256, verified_files=None, defer_prune=False):
    horizons = runtime._horizons(horizons)
    model_path, cache_dir = Path(model_path).resolve(), runtime._consumer_path(cache_dir)
    if cache_dir == model_path or cache_dir in model_path.parents:
        raise runtime.CandidateRuntimeError("快取目錄不可涵蓋模型來源，請指定獨立 consumer cache 目錄。")
    frozen = _frozen_inputs(model_path, horizons, pinned_head_sha256, stock_ids)
    ready_error = None
    if verified_files is None:
        import stock_daily_selected_cache_worker as worker
        try:
            verified_files = worker._ready_file_proofs(cache_dir)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # An unknown previous inventory is not permission to delete it, nor
            # a new barrier to independently preparing valid current data.
            verified_files, ready_error = {}, str(exc)
    code_binding = preparation_code_binding()
    cache_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex
    receipt_path = cache_dir / "selected_cache_preparations" / (run_id + ".json")
    receipt = {"schema": RECEIPT_SCHEMA, "run_id": run_id, "status": "preparing", "ready": False,
        "receipt_path": str(receipt_path), "model_path": str(model_path),
        "model_sha256": frozen["model_sha256"], "head_sha256": frozen["snapshot"]["head_sha256"],
        "snapshot_binding": frozen["snapshot"], "horizons": list(horizons),
        "runtime_horizons": frozen["rules"]["horizons"], "lookback_calendar_months": 18,
        "window_by_market": frozen["windows"], "all_stocks": frozen["all_stocks"],
        "requested_all_stocks": stock_ids is None, "stock_ids": frozen["stock_ids"],
        "stock_count": len(frozen["stock_ids"]), "official_stock_count": frozen["official_stock_count"],
        "source_plan": frozen["plan"], "source_plan_sha256": hashlib.sha256(runtime._canonical(frozen["plan"])).hexdigest(),
        "cache_dir": str(cache_dir), "code_binding": code_binding,
        "offline_only": True, "central_projection_build": False,
        "data_completeness_verified": False, "source_partial": False, "historical_membership_verified": False,
        "formal_promotion": False, "routes": [], "started_at": datetime.now(timezone.utc).isoformat()}
    import stock_data_sources as sealed_reader
    feature = importlib.import_module("stock_daily_feature_catalog")
    bindings = {str(model_path): frozen["model_sha256"], str(Path(runtime.__file__).resolve()): runtime._sha_file(runtime.__file__),
        str(Path(sealed_reader.__file__).resolve()): runtime._sha_file(sealed_reader.__file__),
        str(Path(feature.__file__).resolve()): frozen["rules"]["feature_module_sha256"]}
    file_identities = {path: _file_identity(Path(path)) for path in bindings}

    def unchanged(*, full_hash=False):
        if (any(_file_identity(Path(path)) != identity for path, identity in file_identities.items())
                or (full_hash and (any(runtime._sha_file(path) != digest for path, digest in bindings.items())
                    or preparation_code_binding() != code_binding))):
            raise runtime.CandidateRuntimeError("準備期間模型或來源讀取程式已變更，不能宣稱快取就緒。")

    def persist():
        selected_count = sum(item["usage"] != "not-selected" for item in frozen["plan"])
        visited = [item for item in receipt["routes"] if item["usage"] != "not-selected"]
        ready_count = sum(item["cache_ready"] is True for item in visited)
        receipt["source_partial"] = any(item.get("source_partial") is True for item in visited)
        receipt["routes_coverage"] = {"selected": selected_count, "cache_ready": ready_count,
            "missing": sum(item["status"] == "missing" for item in visited),
            "failed": sum(item["status"] == "failed" for item in visited),
            "pending": selected_count - sum(item["status"] != "preparing" for item in visited),
            "not_selected": len(frozen["plan"]) - selected_count}
        receipt["stock_coverage"] = {"requested": len(frozen["stock_ids"]), "official": frozen["official_stock_count"],
            "selected_stock_route_pairs": len(frozen["stock_ids"]) * selected_count,
            "cache_ready_stock_route_pairs": len(frozen["stock_ids"]) * ready_count,
            "all_requested_routes_cache_ready": len(frozen["stock_ids"]) if ready_count == selected_count else 0}
        document = copy.deepcopy(receipt)
        document["integrity_sha256"] = hashlib.sha256(runtime._canonical(document)).hexdigest()
        runtime._atomic_bytes(receipt_path, runtime._canonical(document) + b"\n")

    sources = frozen["context"]["sources"]
    active = None
    preparing = lifecycle.CacheLease(cache_dir, "selected-preparing-" + run_id)
    preparing_keys = set()
    try:
        with tempfile.TemporaryDirectory(prefix="selected-cache-", dir=cache_dir) as temporary:
            for entry in frozen["plan"]:
                route = entry["route_id"]
                active = dict(entry, cache_ready=False, status="preparing")
                receipt["routes"].append(active)
                if entry["usage"] == "not-selected":
                    active.update(status="not-selected", cache_ready=None)
                    continue
                items = tuple(sources.projection_artifacts(route))
                if len(items) > 1:
                    raise runtime.CandidateRuntimeError("同一封存來源物件不唯一：" + route)
                if not items:
                    active.update(status="missing", coverage_status="source_missing", reason="selected_route_not_published", stocks={
                        stock: {"status": "missing", "cache_ready": False, "legitimate_empty": False}
                        for stock in frozen["stock_ids"]})
                    continue
                item = items[0]
                if (Path(item["root"]).resolve() != runtime.PUBLISHED_ROOT.resolve()
                        or str(item.get("parent_identity") or "").lower() != receipt["head_sha256"]):
                    raise runtime.CandidateRuntimeError("來源 root/head 與凍結快照不同：" + route)
                active["artifact"] = copy.deepcopy(item["artifact_descriptor"])
                active["publication_status"] = item["artifact_descriptor"].get("status", "unknown")
                active["source_partial"] = str(active["publication_status"]).casefold() == "partial"
                active["coverage_status"] = _coverage_status(active["publication_status"])
                if route in _CONTEXT_ROUTES:
                    proof = (frozen["context"]["calendar_evidence"] if route == "official_calendar"
                        else frozen["context"]["current_identity"]["binding"])
                    active.update(status="verified_context", cache_ready=True, evidence=copy.deepcopy(proof))
                    continue
                requests = _requests(route, entry["usage"], frozen)
                identity = runtime._cache_identity(item, route)
                preparing_keys.add("selected-base-" + runtime._cache_key(identity))
                preparing.update_refs([], cache_keys=sorted(preparing_keys))
                pending, results = _reuse_caches(cache_dir, identity, route, requests, verified_files=verified_files)
                active["reused_queries"] = len(results)
                active["prepared_queries"] = 0
                if pending:
                    database_path = Path(temporary) / "route.sqlite"
                    with closing(sqlite3.connect(database_path)) as database:
                        binding = _stream_to_spool(sources, item, route, pending, cache_dir, database)
                        unchanged()
                        shared = {}
                        if route not in runtime.GLOBAL_ROUTES:
                            for scope in pending.values():
                                window = _shared_window(scope)
                                window_key = runtime._cache_key(window)
                                if window_key not in shared:
                                    component = runtime._write_cache_component(cache_dir, identity,
                                        _spooled_rows(database, route, None, window, unkeyed=True), temporary)
                                    shared[window_key] = [component] if component["row_count"] else []
                        for key, scope in pending.items():
                            evidence = dict(binding, predicate=scope, offline_preparation_run_id=run_id)
                            components = shared.get(runtime._cache_key(_shared_window(scope)), [])
                            results[key] = _save_streamed_cache(cache_dir, identity, scope,
                                _spooled_rows(database, route, key, scope), evidence, temporary, shared_rows=components)
                            active["prepared_queries"] += 1
                    database_path.unlink()
                    active["read_evidence"] = binding
                active["stocks"] = _coverage(results, route, frozen["stock_ids"], active["publication_status"])
                active.update(status="prepared", cache_ready=True)
                persist()
            unchanged(full_hash=True)
        selected = [item for item in receipt["routes"] if item["usage"] != "not-selected"]
        cached = [item for item in selected if item["cache_ready"]]
        partial = any(str(item.get("publication_status")).casefold() == "partial" for item in selected)
        receipt.update(status="ready" if len(cached) == len(selected) else "partial",
            ready=len(cached) == len(selected), source_partial=partial,
            coverage_status="selected_routes_missing" if len(cached) != len(selected) else
                "source_partial" if partial else "source_completeness_unverified" if any(
                    item.get("coverage_status") == "source_completeness_unverified" for item in selected)
                else "published_query_verified", data_completeness_verified=False,
            completed_at=datetime.now(timezone.utc).isoformat())
        if not defer_prune:
            if ready_error is not None:
                receipt["gc"] = {"candidate_files": 0, "deleted_files": 0, "deleted_bytes": 0,
                    "unknown": [{"path": str(cache_dir), "reason": ready_error}]}
            else:
                import stock_daily_selected_cache_worker as worker
                receipt["gc"] = worker._prune_consumer_cache({"cache_dir": str(cache_dir)},
                    protected_files=_receipt_cache_paths(receipt), ready_files=verified_files, apply=True)
        persist()
        if receipt["ready"] and receipt["all_stocks"] and receipt["requested_all_stocks"]:
            index = {"schema": "stock-daily-selected-all-ordinary-index-v1", "model_path": str(model_path),
                "model_sha256": receipt["model_sha256"], "head_sha256": receipt["head_sha256"],
                "horizons": list(horizons), "code_binding": code_binding,
                "receipt": {"path": str(receipt_path), "sha256": runtime._sha_file(receipt_path)}}
            index["integrity_sha256"] = hashlib.sha256(runtime._canonical(index)).hexdigest()
            runtime._atomic_bytes(cache_dir / "selected_cache_preparations" / ALL_ORDINARY_INDEX,
                runtime._canonical(index) + b"\n")
    except Exception as exc:
        if active is not None and active["status"] == "preparing":
            active.update(status="failed", cache_ready=False, coverage_status="validation_failed")
        receipt.update(status="failed", ready=False, coverage_status="validation_failed",
            error={"type": type(exc).__name__, "detail": str(exc)},
            completed_at=datetime.now(timezone.utc).isoformat())
        persist()
        raise
    finally:
        preparing.close()
        close = getattr(sources, "close", None)
        if callable(close):
            close()
    return receipt


def current_cache_identities():
    """Use current publication metadata, never source payloads or old receipts."""
    document, _ = lifecycle.publication_metadata(runtime.PUBLISHED_ROOT)
    return [runtime._cache_identity({"root": runtime.PUBLISHED_ROOT, "artifact_descriptor": entry}, entry["route_id"])
        for entry in document["datasets"]]


def _receipt_cache_paths(receipt):
    paths = {receipt["receipt_path"]}
    for route in receipt["routes"]:
        for stock in (route.get("stocks") or {}).values():
            reference = stock.get("cache")
            if not isinstance(reference, dict):
                continue
            manifest = Path(reference["manifest_path"])
            paths.add(str(manifest))
            components = [reference["rows"]] + list(reference.get("shared_rows") or [])
            for component in components:
                paths.update(str(path) for path, _ in runtime._cache_component_paths(manifest.parent.parent, component))
    return sorted(paths)


def prune_selected_cache(cache_dir, *, protected_files, retained_identities=(), apply=False):
    """Retire owned caches with explicit ready refs; worker supplies the sealed inventory."""
    root = Path(os.path.abspath(os.fspath(cache_dir)))
    runtime._consumer_path(root)
    report = {"apply": bool(apply), "candidate_files": [], "removed_files": [],
        "retained": [], "unknown": [], "reclaimed_bytes": 0}
    if not root.exists():
        return report
    try:
        lifecycle.cache_path(root, root, allow_root=True)
    except OSError as exc:
        report["unknown"].append({"path": str(root), "reason": str(exc)})
        return report
    protected = set()
    for reference in protected_files:
        raw = reference.get("path") if isinstance(reference, dict) else reference
        if isinstance(raw, str):
            protected.add(os.path.normcase(os.path.abspath(raw)))
    retained_keys = {runtime._cache_key(value) for value in retained_identities}
    with contextlib.ExitStack() as locks:
        if apply:
            locks.enter_context(consumer_cache_guard(root))
            locks.enter_context(lifecycle.catalog_lock(root))
        records, unknown, stale = lifecycle.active_lease_records(root, apply=apply)
        report["unknown"].extend(unknown)
        live_bases = set()
        for lease in records:
            for key in [lease.get("key")] + list(lease.get("cache_keys", ())):
                if isinstance(key, str) and key.startswith("selected-base-"):
                    live_bases.add(key[len("selected-base-"):])
                elif isinstance(key, str) and key.startswith("selected-"):
                    live_bases.add(key[len("selected-"):])
            for reference in lease.get("physical_refs", ()):
                claim = reference.get("ref") if isinstance(reference, dict) else None
                if (isinstance(claim, dict) and os.path.normcase(os.path.abspath(str(reference.get("root", ""))))
                        == os.path.normcase(str(root)) and isinstance(claim.get("ref"), str)):
                    protected.add(os.path.normcase(os.path.abspath(root / claim["ref"])))
        directory = root / "sealed_candidate_rows"
        if not directory.exists():
            return report
        try:
            lifecycle.cache_path(root, directory)
        except OSError as exc:
            report["unknown"].append({"path": str(directory), "reason": str(exc)})
            return report
        for base in directory.iterdir():
            try:
                lifecycle.cache_path(root, base)
                if not base.is_dir() or re.fullmatch("[0-9a-f]{64}", base.name) is None:
                    raise ValueError("selected_cache_base_not_owned")
                if base.name in live_bases or base.name in retained_keys:
                    report["retained"].append(str(base))
                    continue
                allowed = {"queries", "rows", "ordinals"}
                if any(child.name not in allowed or not child.is_dir() for child in base.iterdir()):
                    raise ValueError("selected_cache_base_contents_unknown")
                for child in base.iterdir():
                    lifecycle.cache_path(root, child)
                queries = base / "queries"
                if not queries.exists():
                    raise ValueError("selected_cache_query_metadata_missing")
                kept, block_rows = set(), False
                for path in queries.iterdir():
                    try:
                        lifecycle.cache_path(root, path)
                        if not path.is_file() or re.fullmatch("[0-9a-f]{64}\.json", path.name) is None:
                            raise ValueError("selected_cache_query_not_owned")
                        document = runtime._cache_document(path)
                        if runtime._cache_key(document["identity"]) != base.name or runtime._cache_key(document["scope"]) != path.stem:
                            raise ValueError("selected_cache_query_identity_unknown")
                        # Only the supported v1/new row formats establish a
                        # complete reference closure for row-file retirement.
                        identity = document["identity"]
                        expected = runtime._cache_identity({"root": identity.get("root", runtime.PUBLISHED_ROOT),
                            "artifact_descriptor": identity["artifact"]}, identity["route_id"])
                        if not runtime._cache_identity_compatible(identity, expected):
                            raise ValueError("selected_cache_parser_semantics_unknown")
                        components = runtime._cache_components(document)
                        references = {os.path.normcase(os.path.abspath(leaf))
                            for component in components for leaf, _ in runtime._cache_component_paths(base, component)}
                        if os.path.normcase(str(path)) in protected or runtime._cache_key(expected) in retained_keys:
                            kept.update(references)
                            report["retained"].append(str(path))
                            continue
                        report["candidate_files"].append(str(path))
                        if apply:
                            size = path.stat().st_size
                            lifecycle.unlink_cache_file(root, path)
                            report["removed_files"].append(str(path))
                            report["reclaimed_bytes"] += size
                    except (OSError, ValueError, KeyError, TypeError) as exc:
                        block_rows = True
                        report["unknown"].append({"path": str(path), "reason": str(exc)})
                for name in ("rows", "ordinals"):
                    folder = base / name
                    if not folder.exists() or block_rows:
                        continue
                    for path in folder.iterdir():
                        try:
                            name = os.path.normcase(str(path))
                            # Retention needs no leaf open. Deletion candidates
                            # still require the complete no-follow identity guard.
                            if name in protected or name in kept:
                                report["retained"].append(str(path))
                                continue
                            lifecycle.cache_path(root, path)
                            if not path.is_file() or re.fullmatch("[0-9a-f]{64}\.jsonl", path.name) is None:
                                raise ValueError("selected_cache_row_not_owned")
                            report["candidate_files"].append(str(path))
                            if apply:
                                size = path.stat().st_size
                                lifecycle.unlink_cache_file(root, path)
                                report["removed_files"].append(str(path))
                                report["reclaimed_bytes"] += size
                        except (OSError, ValueError) as exc:
                            report["unknown"].append({"path": str(path), "reason": str(exc)})
            except (OSError, ValueError, KeyError, TypeError) as exc:
                report["unknown"].append({"path": str(base), "reason": str(exc)})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="明確離線準備單股 consumer cache；不執行報告、訓練或中央更新。",
        allow_abbrev=False)
    parser.add_argument("--model-path", "--candidate-model", required=True, type=Path)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--stock-ids", help="逗號分隔普通股代碼；未指定則準備目前官方普通股全體")
    parser.add_argument("--horizons", default="1,63")
    parser.add_argument("--pinned-head-sha256")
    args = parser.parse_args(argv)
    try:
        receipt = prepare_selected_cache(args.model_path, args.cache_dir, stock_ids=args.stock_ids,
            horizons=args.horizons, pinned_head_sha256=args.pinned_head_sha256)
    except SelectedCacheBusy:
        print("離線快取準備延後：已有程序持有 consumer cache guard。", file=sys.stderr, flush=True)
        return 3
    except (OSError, ValueError, KeyError, TypeError, ImportError, RuntimeError, sqlite3.Error) as exc:
        print("離線快取準備失敗：" + type(exc).__name__ + "：" + str(exc), file=sys.stderr, flush=True)
        return 2
    print(("離線快取已就緒：" if receipt["ready"] else "離線快取仍有缺口：") + receipt["receipt_path"], flush=True)
    return 0 if receipt["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
