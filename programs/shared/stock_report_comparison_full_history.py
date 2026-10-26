"""Independent, zero-GC preparation of complete native comparison projections."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import stock_cache_lifecycle as lifecycle
import stock_daily_candidate_runtime as runtime
import stock_report_comparison as comparison

SCHEMA = "single-stock-comparison-full-history-v1"
SEMANTICS = "full-source-native-source-order-readback-v1"
SINGLE_ROOT = Path(r"D:\TWStockData\program_data\dstock_single")


def _query():
    import stock_query_projection
    return stock_query_projection


def _now():
    return datetime.now(timezone.utc).isoformat()


def _save(path, value):
    unsigned = {key: item for key, item in value.items() if key != "integrity_sha256"}
    lifecycle.atomic_json(path, dict(unsigned, integrity_sha256=hashlib.sha256(runtime._canonical(unsigned)).hexdigest()))


def _previous(path):
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    value = _query()._json_loads(raw)
    unsigned = {key: item for key, item in value.items() if key != "integrity_sha256"}
    if (value.get("schema") != SCHEMA or value.get("integrity_sha256")
            != hashlib.sha256(runtime._canonical(unsigned)).hexdigest()):
        raise _query().ProjectionIntegrityError("full_history_state_integrity_mismatch")
    return value


def _pin(root, head_sha, registry, query):
    with lifecycle.catalog_lock(registry):
        head, document = comparison._head(root, head_sha)
        entries = []
        refs = [{"root": str(root), "ref": {"ref": "_manifests/" + head + ".json", "sha256": head,
                 "size": comparison._contained(root, "_manifests/" + head + ".json", "_manifests/").stat().st_size},
                 "expand": False}]
        keys = []
        for route in comparison._ROUTES:
            matches = [entry for entry in document["datasets"] if isinstance(entry, dict) and entry.get("route_id") == route]
            if len(matches) != 1:
                raise query.ProjectionIntegrityError("comparison_route_missing_or_ambiguous:" + route)
            entry = matches[0]
            comparison._contained(root, entry.get("path"), "_objects/")
            prepared = query._prepare_descriptor(root, entry, route, head, None)
            entries.append((entry, prepared))
            keys.append(query._cache_key(prepared))
            refs.append({"root": str(root), "ref": {"ref": prepared["relative_path"],
                "sha256": prepared["sha256"], "size": prepared["size_bytes"]}, "expand": False, "route_ids": [route]})
        lease = lifecycle.CacheLease(registry, "comparison-full-history-" + head,
            physical_refs=refs, cache_keys=keys, already_locked=True)
    return head, entries, lease


def _date_alias_proof(root, entry, query, state):
    identity = {name: entry[name] for name in ("route_id", "path", "sha256", "size")}
    path = state / "date_aliases" / (entry["route_id"] + "-" + entry["sha256"] + ".json")
    proof = _previous(path)
    if proof:
        if proof.get("source_artifact") != identity or proof.get("validator") != "native-date-alias-equivalence-v1":
            raise query.ProjectionIntegrityError("date_alias_proof_identity_mismatch")
        return proof, path
    proof = {"schema": SCHEMA, "validator": "native-date-alias-equivalence-v1", "source_artifact": identity,
             "rows": 0, "disagreement_rows": 0, "date_missing_but_alias_valid_rows": 0,
             "missing_stock_id_rows": 0, "undated_rows": 0}
    digest = hashlib.sha256()
    with closing(comparison._verified_rows(root, entry)) as rows:
        for row in rows:
            proof["rows"] += 1
            digest.update(runtime._canonical(row) + b"\n")
            days = {name: query._normalise_day(row.get(name)) for name in query._DATE_FIELDS}
            valid = {day for day in days.values() if day is not None}
            proof["disagreement_rows"] += len(valid) > 1
            proof["date_missing_but_alias_valid_rows"] += bool(valid) and days["date"] is None
            proof["missing_stock_id_rows"] += query._normalise_stock(row.get("stock_id")) is None
            proof["undated_rows"] += not valid
    proof["full_source_hash_size_verified"] = True
    proof["source_order_rows_sha256"] = digest.hexdigest()
    proof["safe_mapping"] = all(proof[name] == 0 for name in
        ("disagreement_rows", "date_missing_but_alias_valid_rows", "missing_stock_id_rows"))
    _save(path, proof)
    return proof, path


def _prepare_route(root, entry, prepared, registry, previous, temp, query, state):
    started = time.perf_counter()
    key, identity = query._cache_key(prepared), query._physical_identity(prepared)
    built, build_metrics = False, None
    alias_proof = alias_path = None
    with query._writer_lock(registry / "locks" / (key + ".lock"), timeout_seconds=120):
        manifest = query._read_cache(registry, key, identity)
        if manifest is None:
            # Preserve native parse, source hash, shard commit and atomic sealing.
            try:
                build_metrics = query._build_projection(prepared, registry, key, identity, None,
                    query._DEFAULT_CHUNK_ROWS, query._DEFAULT_CHUNK_BYTES)
            except query.UnsupportedProjectionError as exc:
                if str(exc) != "ambiguous_date_field":
                    raise
                alias_proof, alias_path = _date_alias_proof(root, entry, query, state)
                if not alias_proof["safe_mapping"]:
                    raise
                build_metrics = query._build_projection(prepared, registry, key, identity,
                    {"stock_field": "stock_id", "date_field": "date"},
                    query._DEFAULT_CHUNK_ROWS, query._DEFAULT_CHUNK_BYTES)
            built = True
            manifest = query._read_cache(registry, key, identity)
            if manifest is None:
                raise query.ProjectionIntegrityError("comparison_projection_not_sealed_after_build")
    index = registry / "indexes" / (key + ".json")
    complete = registry / "complete" / (key + ".json")
    proofs = {"index_sha256": runtime._sha_file(index), "completion_sha256": runtime._sha_file(complete)}
    cached = previous.get(key, {})
    reused_validation = (not built and cached.get("validation_semantics") == SEMANTICS
                         and all(cached.get(name) == value for name, value in proofs.items()))
    if reused_validation:
        validation = {name: cached[name] for name in ("readback_rows", "source_order_rows_sha256", "readback_metrics")}
    else:
        result = query.query_published_rows(root, entry, entry["route_id"], parent_identity=prepared["parent_identity"],
            allow_build=False, stock_ids=None, start_date=None, end_date=None, before_count=0,
            columns=None, consumer_temp_root=temp)
        digest, count = hashlib.sha256(), 0
        try:
            with closing(result.iter_rows()) as iterator:
                for row in iterator:
                    digest.update(runtime._canonical(row) + b"\n")
                    count += 1
            if count != manifest["rows"]:
                raise query.ProjectionIntegrityError("comparison_readback_row_count_mismatch")
            validation = {"readback_rows": count, "source_order_rows_sha256": digest.hexdigest(),
                          "readback_metrics": dict(result.metrics)}
        finally:
            result.close()
    field_map, missing = manifest["field_map"], manifest.get("missing_stock_rows")
    if alias_proof and validation["source_order_rows_sha256"] != alias_proof["source_order_rows_sha256"]:
        raise query.ProjectionIntegrityError("date_alias_readback_source_order_mismatch")
    qualified = field_map.get("stock_field") == "stock_id" and type(missing) is int and missing == 0
    return dict(route_id=entry["route_id"], cache_key=key, source_artifact=manifest["source_artifact"],
        status="ready" if qualified else "unsupported_stock_lookup", accelerated_lookup_eligible=qualified,
        fallback_reason=None if qualified else "stock_mapping_not_lossless", built=built,
        validation_reused=reused_validation, validation_semantics=SEMANTICS,
        source_rows=manifest["rows"], minimum_date=manifest.get("minimum_date"), maximum_date=manifest.get("maximum_date"),
        missing_date_rows=manifest.get("missing_date_rows"), missing_stock_rows=missing, field_map=field_map,
        stock_count=len(manifest.get("stock_statistics", [])), shard_count=len(manifest["shards"]),
        cold_build_metrics=build_metrics, elapsed_seconds=time.perf_counter() - started,
        commit_lock_seconds=None, lock_timing_decomposed=False, catalog_wait="possible_not_separately_measured",
        date_alias_proof=({"path": str(alias_path), "sha256": runtime._sha_file(alias_path)} if alias_path else None),
        **proofs, **validation)


def prepare_once(*, central_root=None, head_sha=None, state_root=None, consumer_temp_root=None):
    """Build missing complete indexes; verify cold results, reuse sealed physical SHAs."""
    query = _query()
    root = Path(central_root or comparison.CENTRAL_ROOT).resolve(strict=True)
    state = runtime._consumer_path(state_root or SINGLE_ROOT / "state" / "report_comparison_full_history")
    temp = runtime._consumer_path(consumer_temp_root or SINGLE_ROOT / "cache" / "report_comparison")
    registry = lifecycle.source_registry(root)
    result = {"schema": SCHEMA, "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex,
        "started_at": _now(), "pid": os.getpid(), "status": "running", "ready": False, "routes": [], "validated_indexes": {},
        "gc": {"status": "deferred", "no_delete": True, "scan_performed": False,
               "reason": "maintenance_not_part_of_preparation"}}
    lease = None
    try:
        with query._writer_lock(state / "preparation.lock", timeout_seconds=0.05):
            previous = _previous(state / "latest.json").get("validated_indexes", {})
            head, entries, lease = _pin(root, head_sha, registry, query)
            result["head_sha256"] = head
            _save(state / "latest.json", result)
            for entry, prepared in entries:
                if (state / "pause_requested.json").exists():
                    result.update(status="deferred", reason="pause_requested_before_next_route")
                    break
                result["current_route"] = entry["route_id"]
                _save(state / "latest.json", result)
                print("comparison_full_history building_or_verifying route=" + entry["route_id"], flush=True)
                started = time.perf_counter()
                try:
                    route = _prepare_route(root, entry, prepared, registry, previous, temp, query, state)
                except query.UnsupportedProjectionError as exc:
                    route = {"route_id": entry["route_id"], "cache_key": query._cache_key(prepared),
                        "status": "unsupported", "accelerated_lookup_eligible": False,
                        "fallback_reason": type(exc).__name__ + ":" + str(exc), "native_seal_verified": False,
                        "elapsed_seconds": time.perf_counter() - started, "commit_lock_seconds": None,
                        "lock_timing_decomposed": False, "catalog_wait": "possible_not_separately_measured"}
                result["routes"].append(route)
                if route.get("validation_semantics") == SEMANTICS:
                    result["validated_indexes"][route["cache_key"]] = route
                _save(state / "latest.json", result)
                print("comparison_full_history route=" + entry["route_id"] + " status=" + route["status"]
                      + " elapsed_seconds=" + str(route["elapsed_seconds"]), flush=True)
            else:
                unsupported = any(row["status"] == "unsupported" for row in result["routes"])
                result.update(status="partial_with_raw_fallback" if unsupported else "ready", ready=not unsupported)
            result["sealed_verified_routes"] = sum(row.get("validation_semantics") == SEMANTICS for row in result["routes"])
            result["accelerated_routes"] = [row["route_id"] for row in result["routes"] if row["accelerated_lookup_eligible"]]
            result["raw_fallback_routes"] = [row["route_id"] for row in result["routes"] if not row["accelerated_lookup_eligible"]]
            result["finished_at"] = _now()
            _save(state / "runs" / (result["run_id"] + ".json"), result)
            _save(state / "latest.json", result)
    except query.ProjectionLockTimeoutError as exc:
        result.update(status="deferred", reason="native_writer_lock_busy", error=str(exc))
    except Exception as exc:
        result.update(status="failed", ready=False, finished_at=_now(),
                      error={"type": type(exc).__name__, "detail": str(exc)})
        _save(state / "runs" / (result["run_id"] + ".json"), result)
        _save(state / "latest.json", result)
    finally:
        if lease is not None:
            lease.close()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare complete comparison history without GC or model loading.")
    parser.add_argument("--once", action="store_true", required=True)
    parser.add_argument("--head-sha256")
    args = parser.parse_args(argv)
    result = prepare_once(head_sha=args.head_sha256)
    if sys.stdout is not None:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 2 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
