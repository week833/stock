"""Independent, offline --once worker for the active single-stock cache only."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import traceback
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import stock_daily_candidate_runtime as runtime
import stock_daily_selected_cache as cache
import stock_daily_selected_deployment as deployment

SINGLE_ROOT = deployment.SINGLE_ROOT
WORKER_SCHEMA = "single-stock-selected-cache-worker-v1"
READY_SCHEMA = "single-stock-selected-cache-ready-v1"
INVENTORY_SCHEMA = "single-stock-selected-cache-inventory-v1"
ADOPTION_SCHEMA = "single-stock-selected-cache-adoption-v1"
BACKGROUND_POLICY_SCHEMA = "single-stock-selected-cache-background-policy-v1"
HORIZONS = (1, 63)


class CacheWorkerIntegrityError(ValueError):
    """A matching ready claim or receipt failed verification; never use fallback."""


def _require(condition, message):
    if not condition:
        raise CacheWorkerIntegrityError(message)


def _digest(value):
    return hashlib.sha256(runtime._canonical(value)).hexdigest()


def _state_root():
    return runtime._consumer_path(SINGLE_ROOT / "state" / "selected_cache_preparation")


def _log_root():
    return runtime._consumer_path(SINGLE_ROOT / "logs" / "selected_cache_preparation")


def _background_policy_path():
    return _state_root() / "background_policy.json"


def read_background_policy():
    """Read one bounded policy; missing or unreadable consent never enables work."""
    path = _background_policy_path()
    result = {"schema": BACKGROUND_POLICY_SCHEMA, "path": str(path), "paused": True,
        "policy_valid": False, "status": "paused"}

    def unique_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("background_policy_duplicate_key")
            value[key] = item
        return value

    try:
        with path.open("rb") as handle:
            raw = handle.read(4097)
        if len(raw) > 4096:
            raise ValueError("background_policy_too_large")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs)
        if (not isinstance(value, dict) or set(value) != {"schema", "paused"}
                or value.get("schema") != BACKGROUND_POLICY_SCHEMA or type(value.get("paused")) is not bool):
            raise ValueError("background_policy_invalid")
    except FileNotFoundError:
        return dict(result, reason="background_policy_missing")
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        return dict(result, reason="background_policy_unavailable", error=type(exc).__name__)
    return dict(result, paused=value["paused"], policy_valid=True,
        status="paused" if value["paused"] else "enabled", reason="explicit_background_policy")


def set_background_paused(paused):
    """An explicit user command only changes the small policy, never OS tasks."""
    if type(paused) is not bool:
        raise ValueError("background_policy_boolean_required")
    runtime._atomic_bytes(_background_policy_path(), runtime._canonical(
        {"schema": BACKGROUND_POLICY_SCHEMA, "paused": paused}) + b"\n")
    return read_background_policy()


def _paused_result(policy):
    return {"schema": WORKER_SCHEMA, "status": "paused", "ready": False,
        "prepared": False, "writes": False, "background_policy": policy}


def _inside(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    _require(root in path.parents, "selected_cache_reference_outside_consumer_scope")
    return path


def _native_identity(path):
    with Path(path).open("rb") as handle:
        return list(deployment._handle_identity(handle))


def _write_sealed(path, document):
    value = copy.deepcopy(document)
    value["integrity_sha256"] = _digest(value)
    runtime._atomic_bytes(path, runtime._canonical(value) + b"\n")


def _read_sealed(path, expected_sha256=None, *, proofs=None):
    before = _native_identity(path)
    document, digest = deployment._json(path)
    _require(before == _native_identity(path), "selected_cache_document_changed_during_read")
    _require(expected_sha256 is None or digest == expected_sha256, "selected_cache_document_hash_mismatch")
    unsigned = {key: value for key, value in document.items() if key != "integrity_sha256"}
    _require(document.get("integrity_sha256") == _digest(unsigned), "selected_cache_document_integrity_mismatch")
    if proofs is not None:
        path = Path(path).resolve()
        proofs[str(path)] = {"path": str(path), "sha256": digest, "size": before[2], "identity": before}
    return unsigned, digest


def _file_proof(path, expected_sha256, *, expected_size=None, row_count=None, ordinal=False, verified_files=None):
    path = Path(path)
    descriptor = {"sha256": expected_sha256, "size": expected_size}
    if runtime._verified_cache_file(path, descriptor, verified_files):
        return copy.deepcopy(verified_files[str(path.resolve())])
    digest, size, count, previous = hashlib.sha256(), 0, 0, 0
    with path.open("rb") as handle:
        before = list(deployment._handle_identity(handle))
        chunks = handle if row_count is not None else iter(lambda: handle.read(1024 * 1024), b"")
        for raw in chunks:
            digest.update(raw)
            size += len(raw)
            if row_count is not None:
                value = json.loads(raw.decode("utf-8"))
                if ordinal:
                    _require(type(value) is int and value > previous, "selected_cache_ordinal_order_invalid")
                    previous = value
                else:
                    _require(isinstance(value, dict), "selected_cache_row_object_required")
                count += 1
        _require(before == list(deployment._handle_identity(handle)), "selected_cache_file_changed_during_verification")
    _require(before == _native_identity(path), "selected_cache_file_replaced_during_verification")
    _require(digest.hexdigest() == expected_sha256, "selected_cache_file_hash_mismatch")
    _require(expected_size is None or size == expected_size, "selected_cache_file_size_mismatch")
    _require(row_count is None or count == row_count, "selected_cache_file_row_count_mismatch")
    return {"path": str(path.resolve()), "sha256": expected_sha256, "size": size, "identity": before}


def _current_target(*, owners=None):
    active = deployment.active_selected_binding()
    if active is None:
        return None
    head = deployment._head_binding()
    try:
        target = {"activation_id": active["activation_id"], "activation_binding_sha256": _digest(active),
            "model_path": str(Path(active["model_path"]).resolve()), "model_sha256": active["model_sha256"],
            "cache_dir": str(runtime._consumer_path(active["cache_dir"])), "head_sha256": head["head_sha256"],
            "horizons": list(HORIZONS), "code_binding": cache.preparation_code_binding(),
            "worker_sha256": runtime._sha_file(__file__)}
        if owners is not None:
            owners.append(head)
            head = None
        return target
    finally:
        if head is not None:
            deployment.close_source_lease(head)


def _ready_file_proofs(cache_dir):
    """Read the existing sealed inventory; do not rehash its payloads here."""
    try:
        marker, _ = _read_sealed(_state_root() / "ready.json")
    except FileNotFoundError:
        return {}
    _require(marker.get("schema") == READY_SCHEMA, "selected_cache_ready_schema_invalid")
    _require(Path(marker["target"]["cache_dir"]).resolve() == Path(cache_dir).resolve(), "selected_cache_ready_root_mismatch")
    reference = marker["inventory"]
    path = _inside(reference["path"], _state_root() / "verified")
    inventory, _ = _read_sealed(path, reference["sha256"])
    _require(inventory.get("schema") == INVENTORY_SCHEMA and inventory.get("target") == marker["target"]
        and inventory.get("receipt") == marker.get("receipt"), "selected_cache_inventory_binding_mismatch")
    return {str(_inside(item["path"], cache_dir)): item for item in inventory["files"]}


def _prune_consumer_cache(target, *, protected_files=None, retained_identities=None, ready_files=None, apply=False):
    try:
        ready = _ready_file_proofs(target["cache_dir"]) if ready_files is None else ready_files
        additional = protected_files.values() if isinstance(protected_files, dict) else protected_files or ()
        files = list(ready.values() if isinstance(ready, dict) else ready) + list(additional)
        identities = cache.current_cache_identities() if retained_identities is None else retained_identities
        report = cache.prune_selected_cache(target["cache_dir"], protected_files=files,
            retained_identities=identities, apply=apply)
        return {"candidate_files": len(report["candidate_files"]), "deleted_files": len(report["removed_files"]),
            "deleted_bytes": report["reclaimed_bytes"], "unknown": report["unknown"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"candidate_files": 0, "deleted_files": 0, "deleted_bytes": 0,
            "unknown": [{"path": target["cache_dir"], "reason": str(exc)}]}


def _deferred_gc():
    return {"status": "deferred", "no_delete": True, "reason": "maintenance_not_part_of_preparation",
        "scan_performed": False, "candidate_files": None, "deleted_files": 0, "deleted_bytes": 0}


def _matches_preparation(value, target, *, require_code=True):
    keys = ("model_path", "model_sha256", "head_sha256", "horizons")
    return all(value.get(key) == target[key] for key in keys + (("code_binding",) if require_code else ()))


def _receipt_reference(target):
    path = Path(target["cache_dir"]) / "selected_cache_preparations" / cache.ALL_ORDINARY_INDEX
    try:
        index, _ = _read_sealed(path)
    except FileNotFoundError:
        return None
    _require(index.get("schema") == "stock-daily-selected-all-ordinary-index-v1", "selected_cache_index_schema_invalid")
    if not _matches_preparation(index, target):
        return None
    reference = copy.deepcopy(index["receipt"])
    if "adoption" in index:
        reference["adoption"] = index["adoption"]
    return reference


def _frozen_scope_binding(receipt):
    return _digest({key: receipt.get(key) for key in ("source_plan", "source_plan_sha256", "stock_ids",
        "stock_count", "official_stock_count", "runtime_horizons", "lookback_calendar_months", "window_by_market")})


def _check_frozen_scope(receipt, target, frozen):
    _require(receipt.get("source_plan") == frozen["plan"]
        and receipt.get("source_plan_sha256") == _digest(frozen["plan"])
        and receipt.get("stock_ids") == frozen["stock_ids"]
        and receipt.get("window_by_market") == frozen["windows"]
        and receipt.get("runtime_horizons") == frozen["rules"]["horizons"]
        and receipt.get("lookback_calendar_months") == 18, "selected_cache_legacy_frozen_scope_mismatch")
    snapshot = receipt.get("snapshot_binding") or {}
    _require(snapshot.get("head_sha256") == target["head_sha256"], "selected_cache_legacy_snapshot_mismatch")
    for key in ("identity_binding", "retention_binding", "capture_binding", "asof_by_market"):
        _require(snapshot.get(key) == frozen["snapshot"].get(key), "selected_cache_legacy_side_evidence_mismatch:" + key)
    by_route = {entry["route_id"]: entry for entry in receipt["routes"]}
    for entry in frozen["plan"]:
        if entry["usage"] == "not-selected":
            continue
        route = entry["route_id"]
        actual = by_route[route]
        items = tuple(frozen["context"]["sources"].projection_artifacts(route))
        _require(len(items) == 1 and items[0]["parent_identity"] == target["head_sha256"]
            and Path(items[0]["root"]).resolve() == runtime.PUBLISHED_ROOT.resolve()
            and actual["artifact"] == items[0]["artifact_descriptor"]
            and actual.get("publication_status") == items[0]["artifact_descriptor"].get("status"),
            "selected_cache_legacy_artifact_mismatch:" + route)
        if route in cache._CONTEXT_ROUTES:
            expected = (frozen["context"]["calendar_evidence"] if route == "official_calendar"
                else frozen["context"]["current_identity"]["binding"])
            _require(actual.get("evidence") == expected, "selected_cache_legacy_context_mismatch:" + route)
            continue
        scopes = cache._requests(route, entry["usage"], frozen)
        for stock, value in actual["stocks"].items():
            _require(value.get("scope") == scopes[None if route in runtime.GLOBAL_ROUTES else stock],
                "selected_cache_legacy_query_scope_mismatch:" + route)


def _verify_receipt(reference, target, *, frozen=None, verified_files=None):
    root = Path(target["cache_dir"])
    receipt_path = _inside(reference["path"], root / "selected_cache_preparations")
    _require(frozen is not None or reference.get("sha256") is not None, "selected_cache_receipt_hash_required")
    proofs = {}
    receipt, digest = _read_sealed(receipt_path, reference.get("sha256"), proofs=proofs)
    adoption = None
    if "adoption" in reference:
        ref = reference["adoption"]
        path = _inside(ref["path"], root / "selected_cache_preparations" / "adoptions")
        adoption, _ = _read_sealed(path, ref["sha256"], proofs=proofs)
        _require(adoption.get("schema") == ADOPTION_SCHEMA and adoption.get("operation") == "adopt_verified_cache"
            and _matches_preparation(adoption, target)
            and adoption.get("original_receipt") == {"path": str(receipt_path), "sha256": digest}
            and adoption.get("frozen_scope_sha256") == _frozen_scope_binding(receipt),
            "selected_cache_adoption_binding_mismatch")
    _require(receipt.get("schema") == cache.RECEIPT_SCHEMA
        and _matches_preparation(receipt, target, require_code=frozen is None and adoption is None),
        "selected_cache_preparation_binding_mismatch")
    ids = receipt.get("stock_ids") or []
    _require(receipt.get("ready") is True and receipt.get("status") == "ready"
        and receipt.get("all_stocks") is True and receipt.get("requested_all_stocks") is True
        and receipt.get("stock_count") == receipt.get("official_stock_count") == len(set(ids)) == len(ids)
        and len(ids) > 0 and Path(receipt["cache_dir"]).resolve() == root.resolve(),
        "selected_cache_all_ordinary_receipt_required")
    selected = {entry["route_id"] for entry in receipt["source_plan"] if entry["usage"] != "not-selected"}
    routes = [entry for entry in receipt["routes"] if entry["usage"] != "not-selected"]
    coverage = receipt["routes_coverage"]
    _require(len(routes) == len(selected) and {entry["route_id"] for entry in routes} == selected
        and coverage["selected"] == coverage["cache_ready"] == len(selected)
        and all(coverage.get(key, 0) == 0 for key in ("missing", "failed", "pending")),
        "selected_cache_route_coverage_incomplete")
    if frozen is not None:
        _check_frozen_scope(receipt, target, frozen)
    manifests, row_descriptors = {}, {}
    for route in routes:
        _require(route.get("cache_ready") is True, "selected_cache_route_not_ready")
        if route["route_id"] in cache._CONTEXT_ROUTES:
            _require(route.get("status") == "verified_context" and route.get("evidence") is not None,
                "selected_cache_context_evidence_missing")
            continue
        _require(set(route.get("stocks") or {}) == set(ids), "selected_cache_stock_coverage_incomplete")
        for stock in route["stocks"].values():
            reference = stock["cache"]
            manifest_path = _inside(reference["manifest_path"], root / "sealed_candidate_rows")
            key = str(manifest_path)
            if key not in manifests:
                document, sha = _read_sealed(manifest_path, reference["manifest_sha256"], proofs=proofs)
                prior = (verified_files or {}).get(key)
                trusted = isinstance(prior, dict) and all(prior.get(field) == proofs[key].get(field)
                    for field in ("sha256", "size", "identity"))
                manifests[key] = (document, sha, trusted)
            document, sha, manifest_trusted = manifests[key]
            identity = document["identity"]
            expected_identity = runtime._cache_identity({"root": runtime.PUBLISHED_ROOT,
                "artifact_descriptor": route["artifact"]}, route["route_id"])
            _require(sha == reference["manifest_sha256"] and document.get("schema") == runtime.CACHE_SCHEMA
                and runtime._cache_identity_compatible(identity, expected_identity)
                and runtime._scope_covers(document["scope"], stock["scope"]), "selected_cache_query_binding_mismatch")
            _require(document["rows"]["sha256"] == reference["rows_sha256"], "selected_cache_rows_reference_mismatch")
            _require(reference.get("shared_rows", []) == document.get("shared_rows", []), "selected_cache_shared_rows_reference_mismatch")
            for component in runtime._cache_components(document):
                for position, (data_path, descriptor) in enumerate(runtime._cache_component_paths(manifest_path.parent.parent, component)):
                    data_path = _inside(data_path, root)
                    data_key = str(data_path)
                    _require(data_key not in row_descriptors or row_descriptors[data_key] == descriptor,
                        "selected_cache_shared_rows_descriptor_mismatch")
                    if data_key not in proofs:
                        proofs[data_key] = _file_proof(data_path, descriptor["sha256"], expected_size=descriptor["size"],
                            row_count=descriptor["row_count"], ordinal=position == 1,
                            verified_files=verified_files if manifest_trusted else None)
                        row_descriptors[data_key] = descriptor
    # Cache readiness is not source completeness, even for an older receipt.
    observed_partial = any(str(item.get("publication_status")).casefold() == "partial" for item in routes)
    recorded_partial = receipt.get("source_partial")
    receipt["source_partial"] = (True if observed_partial or recorded_partial is True
        or receipt.get("coverage_status") == "source_partial" else False if recorded_partial is False else None)
    statuses = [cache._coverage_status(item.get("publication_status")) for item in routes]
    receipt["coverage_status"] = ("source_partial" if receipt["source_partial"] else
        "source_completeness_unverified" if receipt["source_partial"] is None
        or "source_completeness_unverified" in statuses else "published_query_verified")
    receipt["data_completeness_verified"] = False
    return receipt, list(proofs.values())


def _matching_ready(target):
    try:
        marker, _ = _read_sealed(_state_root() / "ready.json")
    except FileNotFoundError:
        return None
    _require(marker.get("schema") == READY_SCHEMA, "selected_cache_ready_schema_invalid")
    if marker.get("target") != target:
        return None
    ref = marker["inventory"]
    inventory_path = _inside(ref["path"], _state_root() / "verified")
    inventory, _ = _read_sealed(inventory_path, ref["sha256"])
    _require(inventory.get("schema") == INVENTORY_SCHEMA and inventory.get("target") == target
        and inventory.get("receipt") == marker.get("receipt") and bool(inventory.get("files")),
        "selected_cache_inventory_binding_mismatch")
    paths = set()
    for item in inventory["files"]:
        path = _inside(item["path"], target["cache_dir"])
        _require(str(path) not in paths, "selected_cache_duplicate_inventory_file")
        paths.add(str(path))
        _require(_native_identity(path) == item["identity"], "selected_cache_verified_file_changed")
    _require(str(Path(marker["receipt"]["path"]).resolve()) in paths, "selected_cache_receipt_not_in_inventory")
    return dict(marker, _verified_files=inventory["files"])


def _finish(run_id, status, *, target=None, **details):
    state = {"schema": WORKER_SCHEMA, "run_id": run_id, "status": status, "ready": status == "ready",
        "target": target, "observed_at": datetime.now(timezone.utc).isoformat(), "offline_only": True,
        "ready_marker_path": str(_state_root() / "ready.json"), "last_good_preserved": status != "ready",
        **details}
    _write_sealed(_log_root() / (run_id + ".json"), state)
    _write_sealed(_state_root() / "latest.json", state)
    return state


def _publish_ready(run_id, target, reference, receipt, files, *, operation, index=None):
    with cache.consumer_cache_guard(_state_root()):
        if _current_target() != target:
            return False
        for item in files:
            _require(_native_identity(item["path"]) == item["identity"], "selected_cache_file_changed_before_commit")
        inventory_path = _state_root() / "verified" / (run_id + ".json")
        _write_sealed(inventory_path, {"schema": INVENTORY_SCHEMA, "target": target,
            "receipt": reference, "files": files, "operation": operation})
        inventory_ref = {"path": str(inventory_path), "sha256": runtime._sha_file(inventory_path)}
        marker = {"schema": READY_SCHEMA, "target": target, "receipt": reference,
            "inventory": inventory_ref, "source_partial": receipt["source_partial"],
            "coverage_status": receipt["coverage_status"], "data_completeness_verified": False,
            "operation": operation, "verified_at": datetime.now(timezone.utc).isoformat()}
        if _current_target() != target:
            return False
        if index is not None:
            _write_sealed(Path(target["cache_dir"]) / "selected_cache_preparations" / cache.ALL_ORDINARY_INDEX, index)
        _write_sealed(_state_root() / "ready.json", marker)
        return True


def adopt_verified_cache(receipt_path):
    """Explicit compatibility adoption. Never scan routes, prepare or rewrite rows.

    Frozen model rules and bounded published identity/calendar side evidence are
    rechecked. Existing consumer manifest/row bytes are fully verified once.
    The original receipt and all existing query/row cache files stay untouched.
    """
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex
    target, owners, frozen = None, [], None
    operation = "adopt_verified_cache"
    try:
        target = _current_target(owners=owners)
        _require(target is not None, "selected_cache_adoption_requires_active_binding")
        with cache.consumer_cache_guard(target["cache_dir"]):
            path = _inside(receipt_path, Path(target["cache_dir"]) / "selected_cache_preparations")
            frozen = cache._frozen_inputs(target["model_path"], HORIZONS, target["head_sha256"], None)
            receipt, files = _verify_receipt({"path": str(path)}, target, frozen=frozen)
            original = {"path": str(path), "sha256": next(item["sha256"] for item in files if item["path"] == str(path))}
            if _current_target() != target:
                return _finish(run_id, "pending", target=target, operation=operation, original_receipt=original,
                    reason="target_changed_during_adoption")
            evidence_path = Path(target["cache_dir"]) / "selected_cache_preparations" / "adoptions" / (run_id + ".json")
            evidence = {"schema": ADOPTION_SCHEMA, "operation": operation,
                **{key: target[key] for key in ("model_path", "model_sha256", "head_sha256", "horizons", "code_binding")},
                "original_receipt": original, "frozen_scope_sha256": _frozen_scope_binding(receipt),
                "cache_dir": target["cache_dir"], "source_partial": receipt["source_partial"],
                "coverage_status": receipt["coverage_status"], "data_completeness_verified": False,
                "source_routes_scanned": 0, "query_caches_rewritten": 0, "row_caches_rewritten": 0,
                "verified_cache_files": len(files), "verified_at": datetime.now(timezone.utc).isoformat()}
            _write_sealed(evidence_path, evidence)
            adoption_ref = {"path": str(evidence_path), "sha256": runtime._sha_file(evidence_path)}
            files.append(_file_proof(evidence_path, adoption_ref["sha256"]))
            reference = dict(original, adoption=adoption_ref)
            index = {"schema": "stock-daily-selected-all-ordinary-index-v1", "operation": operation,
                **{key: target[key] for key in ("model_path", "model_sha256", "head_sha256", "horizons", "code_binding")},
                "receipt": original, "adoption": adoption_ref}
            if not _publish_ready(run_id, target, reference, receipt, files, operation=operation, index=index):
                return _finish(run_id, "pending", target=target, operation=operation, original_receipt=original,
                    reason="target_changed_before_adoption_commit")
            return _finish(run_id, "ready", target=target, operation=operation, receipt=reference,
                original_receipt=original, prepared=False, source_routes_scanned=0,
                query_caches_rewritten=0, row_caches_rewritten=0, source_partial=receipt["source_partial"],
                coverage_status=receipt["coverage_status"], data_completeness_verified=False, gc=_deferred_gc())
    except cache.SelectedCacheBusy:
        return _finish(run_id, "deferred", target=target, operation=operation, reason="consumer_cache_guard_busy")
    except Exception as exc:
        return _finish(run_id, "failed", target=target, operation=operation,
            error={"type": type(exc).__name__, "detail": str(exc)}, traceback=traceback.format_exc())
    finally:
        for owner in owners:
            deployment.close_source_lease(owner)
        if frozen is not None:
            close = getattr(frozen["context"]["sources"], "close", None)
            if callable(close):
                close()


def run_once():
    """Prepare/verify only; no GC, network, training, analysis, services or task installation."""
    policy = read_background_policy()
    if policy["paused"]:
        return _paused_result(policy)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex
    target, owners = None, []
    try:
        target = _current_target(owners=owners)
        if target is None:
            return _finish(run_id, "inactive", reason="selected_activation_absent")
        with cache.consumer_cache_guard(target["cache_dir"]):
            policy = read_background_policy()
            if policy["paused"]:
                return _paused_result(policy)
            if _current_target() != target:
                return _finish(run_id, "pending", target=target, reason="target_changed_before_preparation")
            marker = _matching_ready(target)
            if marker is not None:
                if _current_target() != target:
                    return _finish(run_id, "pending", target=target, reason="target_changed_during_fast_check")
                return _finish(run_id, "ready", target=target, fast_path=True,
                    receipt=marker["receipt"], source_partial=marker["source_partial"],
                    coverage_status=marker["coverage_status"], data_completeness_verified=False,
                    gc=_deferred_gc())
            verified_files = _ready_file_proofs(target["cache_dir"])
            reference = _receipt_reference(target)
            prepared = False
            if reference is None:
                receipt = cache.prepare_selected_cache(target["model_path"], target["cache_dir"],
                    stock_ids=None, horizons=HORIZONS, pinned_head_sha256=target["head_sha256"], verified_files=verified_files, _defer_prune=True)
                prepared = True
                reference = {"path": receipt["receipt_path"], "sha256": runtime._sha_file(receipt["receipt_path"])}
                if _current_target() != target:
                    return _finish(run_id, "pending", target=target, receipt=reference, reason="target_changed_during_preparation")
                if not receipt["ready"]:
                    return _finish(run_id, "partial", target=target, receipt=reference,
                        source_partial=receipt["source_partial"], coverage_status=receipt["coverage_status"], data_completeness_verified=False)
            receipt, files = _verify_receipt(reference, target, verified_files=verified_files)
            if not _publish_ready(run_id, target, reference, receipt, files, operation="verify_prepared_cache"):
                return _finish(run_id, "pending", target=target, receipt=reference, reason="target_changed_before_ready_commit")
            return _finish(run_id, "ready", target=target, fast_path=False, prepared=prepared,
                receipt=reference, source_partial=receipt["source_partial"], coverage_status=receipt["coverage_status"],
                data_completeness_verified=False, gc=_deferred_gc())
    except cache.SelectedCacheBusy:
        return _finish(run_id, "deferred", target=target, reason="consumer_cache_guard_busy")
    except Exception as exc:
        error, recheck_error = {"type": type(exc).__name__, "detail": str(exc)}, None
        if target is not None:
            try:
                if _current_target() != target:
                    return _finish(run_id, "pending", target=target, error=error, reason="target_changed_during_failed_preparation")
            except Exception as recheck:
                recheck_error = {"type": type(recheck).__name__, "detail": str(recheck)}
        return _finish(run_id, "failed", target=target, error=error, target_recheck_error=recheck_error, traceback=traceback.format_exc())
    finally:
        for owner in owners:
            deployment.close_source_lease(owner)


def read_status():
    """Read-only freshness check; creates no guard, log, state or cache files."""
    target = None
    try:
        target = _current_target()
        if target is None:
            return {"schema": WORKER_SCHEMA, "status": "inactive", "ready": False}
        marker = _matching_ready(target)
        if _current_target() != target:
            return {"schema": WORKER_SCHEMA, "status": "pending", "ready": False, "target": target,
                "reason": "target_changed_during_status_check"}
        try:
            latest, _ = _read_sealed(_state_root() / "latest.json")
        except FileNotFoundError:
            latest = {}
        last_status = latest.get("status") if latest.get("target") == target else None
        status = "ready" if marker else "failed" if last_status == "failed" else "pending"
        return {"schema": WORKER_SCHEMA, "status": status, "ready": marker is not None,
            "target": target, "receipt": marker["receipt"] if marker else None,
            "last_attempt_status": last_status, "last_attempt_error": latest.get("error") if last_status == "failed" else None,
            "source_partial": marker["source_partial"] if marker else None, "data_completeness_verified": False}
    except Exception as exc:
        return {"schema": WORKER_SCHEMA, "status": "failed", "ready": False, "target": target,
            "error": {"type": type(exc).__name__, "detail": str(exc)}}


def _prepare_comparison_history():
    from stock_report_comparison_full_history import SINGLE_ROOT, prepare_once
    if not (SINGLE_ROOT / "state" / "report_comparison_full_history" / "latest.json").is_file():
        return {"status": "deferred", "reason": "awaiting_initial_full_history_preparation"}
    return prepare_once()


def main(argv=None):
    parser = argparse.ArgumentParser(description="單股端獨立離線 cache 背景準備，不啟動分析或更新服務。",
        allow_abbrev=False)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--pause-background", action="store_true")
    mode.add_argument("--resume-background", action="store_true")
    mode.add_argument("--adopt-receipt", type=Path, metavar="PATH")
    args = parser.parse_args(argv)
    comparison_failed = False
    if args.status or args.pause_background or args.resume_background:
        result = (dict(read_status(), background_policy=read_background_policy()) if args.status
            else set_background_paused(args.pause_background))
        if sys.stdout is not None:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        if args.once:
            policy = read_background_policy()
            if policy["paused"]:
                return 0
        _log_root().mkdir(parents=True, exist_ok=True)
        with (_log_root() / "worker_output.log").open("a", encoding="utf-8", newline="\n") as handle:
            with redirect_stdout(handle), redirect_stderr(handle):
                result = adopt_verified_cache(args.adopt_receipt) if args.adopt_receipt is not None else run_once()
                if args.once:
                    # run_once has released the selected consumer guard and source owners.
                    history = _prepare_comparison_history()
                    comparison_failed = history["status"] == "failed"
                    print(json.dumps({"phase": "comparison_full_history", "status": history["status"],
                        "run_id": history.get("run_id")}, ensure_ascii=False, sort_keys=True))
    return 2 if comparison_failed or result["status"] in {"failed", "partial"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
