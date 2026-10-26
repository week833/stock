"""Single-consumer adoption of a frozen selected model, never the shared current."""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import math
import os
import re
import tempfile
import threading
import uuid
import weakref
from datetime import datetime, timezone
from pathlib import Path

import stock_daily_candidate_runtime as runtime
import stock_daily_contract as contract

SINGLE_ROOT = Path(r"D:\TWStockData\program_data\dstock_single")
SELECTED_CURRENT = SINGLE_ROOT / "models" / "selected_current.json"
APPROVED_MODEL_PATH = Path(r"D:\TWStockData\program_data\stock_daily_c0\candidates\acceptance_20261017T182819\research\20261017T103954Z-dc5bb0a82a\candidate_model.json")
DEFAULT_CACHE_DIR = APPROVED_MODEL_PATH.parents[2] / "consumer_cache"
DEPLOYMENT_SCHEMA = "single-stock-selected-deployment-v1"
REPORT_SCHEMA = "single-stock-selected-formal-report-v1"
REPORT_CONTEXT_SCHEMA = "single-stock-report-context-v1"
ACCEPTANCE_SCHEMA = "single-stock-selected-preactivation-acceptance-v1"
RECEIPT_SCHEMA = "single-stock-selected-activation-receipt-v1"
BINDING_ENV = "DStock_SINGLE_SELECTED_BINDING"
READ_ONLY_STORAGE_POLICY = runtime.READ_ONLY_STORAGE_POLICY
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_DIGEST_CACHE = {}
_MODEL_CACHE = {}
_CACHE_LOCK = threading.RLock()


class _HeadLeaseBinding(dict):
    """JSON fields stay unchanged; the temporary source owner is process-local."""
    def __init__(self, value, lease):
        super().__init__(value)
        self._source_lease = lease
        self._source_finalizer = weakref.finalize(self, lease.close)

    def close_source_lease(self):
        self._source_finalizer()

    def __deepcopy__(self, memo):
        result = {}
        memo[id(self)] = result
        result.update(copy.deepcopy(dict(self), memo))
        return result


def close_source_lease(binding):
    close = getattr(binding, "close_source_lease", None)
    if close is not None:
        close()


def _carry_head_lease(value, owner):
    result = _HeadLeaseBinding(value, owner._source_lease)
    owner._source_finalizer.detach()
    owner._source_lease = None
    return result


class SelectedDeploymentError(ValueError):
    """An adoption or report cannot be verified without inventing evidence."""


def _require(condition, code):
    if not condition:
        raise SelectedDeploymentError(code)


def _is_native_json_tree(value):
    """Certify only this call's plain JSON tree, without aliases or cycles."""
    seen = set()

    def visit(item):
        kind = type(item)
        if item is None or kind is str or kind is bool or kind is int:
            return True
        if kind is float:
            return math.isfinite(item)
        if kind is not dict and kind is not list:
            return False
        identity = id(item)
        if identity in seen:
            return False
        seen.add(identity)
        if kind is dict:
            return all(type(key) is str and visit(child) for key, child in item.items())
        return all(visit(child) for child in item)

    try:
        return visit(value)
    except RecursionError:
        return False


def _canonical_compatible(value):
    if isinstance(value, _HeadLeaseBinding):
        value = dict(value)
    if not _is_native_json_tree(value):
        return runtime._canonical(value)
    # On this strict tree, runtime._clean is an identity transformation.
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")


def _detached_clone(value):
    if isinstance(value, _HeadLeaseBinding):
        value = dict(value)
    if _is_native_json_tree(value):
        try:
            return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=False,
                separators=(",", ":"), allow_nan=False))
        except (ValueError, OverflowError, RecursionError):
            # Encoder/decoder limits must not reject otherwise copyable values.
            pass
    return copy.deepcopy(value)


def _digest(value):
    return hashlib.sha256(_canonical_compatible(value)).hexdigest()


def _handle_identity(handle):
    stat = os.fstat(handle.fileno())
    identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    if os.name == "nt":
        # Windows st_ctime is creation time. ChangeTime also catches a write
        # whose writer restored LastWriteTime; do not cache on mtime alone.
        import ctypes
        import msvcrt
        from ctypes import wintypes

        class BasicInfo(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in
                ("CreationTime", "LastAccessTime", "LastWriteTime", "ChangeTime")] + [
                ("FileAttributes", wintypes.DWORD)]

        api = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandleEx
        api.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        api.restype = wintypes.BOOL
        info = BasicInfo()
        if not api(msvcrt.get_osfhandle(handle.fileno()), 0, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        identity += (info.ChangeTime,)
    return identity


def _file_sha(path):
    path = Path(path).resolve(strict=True)
    with path.open("rb") as handle:
        before = _handle_identity(handle)
        with _CACHE_LOCK:
            cached = _DIGEST_CACHE.get(str(path))
        if cached is not None and cached[0] == before:
            digest = cached[1]
        else:
            sha = hashlib.sha256()
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                sha.update(chunk)
            digest = sha.hexdigest()
        _require(before == _handle_identity(handle), "selected_file_changed_during_read")
    with path.open("rb") as handle:
        _require(before == _handle_identity(handle), "selected_file_replaced_during_read")
    with _CACHE_LOCK:
        if len(_DIGEST_CACHE) >= 128:
            _DIGEST_CACHE.clear()
        _DIGEST_CACHE[str(path)] = (before, digest)
    return digest


def _json(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            _require(key not in value, "selected_duplicate_json_key")
            value[key] = item
        return value

    raw = contract._stable_file_bytes(path)
    value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique,
        parse_constant=lambda token: (_ for _ in ()).throw(SelectedDeploymentError("selected_nonfinite_json")))
    _require(isinstance(value, dict), "selected_json_object_required")
    return value, hashlib.sha256(raw).hexdigest()


def _reference(reference):
    _require(isinstance(reference, dict) and isinstance(reference.get("path"), str)
        and _SHA.fullmatch(str(reference.get("sha256", ""))), "selected_evidence_reference_invalid")
    document, digest = _json(reference["path"])
    _require(digest == reference["sha256"], "selected_evidence_digest_mismatch")
    return document


def _model_digest(binding):
    path = Path(binding["model_path"]).resolve(strict=True)
    _require(path == APPROVED_MODEL_PATH.resolve(strict=True), "selected_model_not_authorized")
    digest = _file_sha(path)
    _require(digest == binding["model_sha256"], "selected_model_digest_mismatch")
    sidecar = contract._stable_file_bytes(path.with_suffix(path.suffix + ".sha256")).decode("utf-8").strip()
    _require(sidecar == digest, "selected_model_sidecar_mismatch")
    return path


def _load_model(binding):
    candidate = importlib.import_module("stock_daily_candidate_model")
    path = _model_digest(binding)
    key = (str(path), binding["model_sha256"])
    with _CACHE_LOCK:
        cached = _MODEL_CACHE.get(key)
    if cached is not None:
        return cached, path
    model, digest = runtime._load_bound_model(candidate, path)
    _require(digest == binding["model_sha256"] and model.get("schema") == runtime.MODEL_SCHEMA
        and (model.get("metadata") or {}).get("feature_schema") == runtime.FEATURE_SCHEMA,
        "selected_frozen_model_schema_mismatch")
    with _CACHE_LOCK:
        _MODEL_CACHE.clear()
        _MODEL_CACHE[key] = model
    return model, path


def _authorization(note):
    _require(isinstance(note, str) and bool(note.strip()), "selected_explicit_authorization_required")
    return {"note": note, "sha256": hashlib.sha256(note.encode("utf-8")).hexdigest()}


def _validate_binding(binding):
    _require(isinstance(binding, dict) and binding.get("schema") == DEPLOYMENT_SCHEMA
        and binding.get("consumer_id") == "single", "selected_deployment_schema_invalid")
    _require(isinstance(binding.get("activation_id"), str)
        and re.fullmatch(r"[A-Za-z0-9_-]{8,100}", binding["activation_id"]), "selected_activation_id_invalid")
    approved = datetime.fromisoformat(str(binding.get("approved_at", "")).replace("Z", "+00:00"))
    _require(approved.tzinfo is not None, "selected_approval_timezone_required")
    _require(_SHA.fullmatch(str(binding.get("model_sha256", ""))), "selected_model_sha256_invalid")
    authorization = binding.get("authorization") or {}
    _require(authorization == _authorization(authorization.get("note")), "selected_authorization_digest_mismatch")
    _require("prior" in binding and (binding["prior"] is None or isinstance(binding["prior"], dict)),
        "selected_prior_evidence_required")
    _require(isinstance(binding.get("cache_dir"), str), "selected_cache_directory_required")
    runtime._consumer_path(binding["cache_dir"])
    _model_digest(binding)
    feature = binding.get("feature_binding")
    if feature is not None:
        path = Path(__file__).resolve().with_name("stock_daily_feature_catalog.py")
        _require(isinstance(feature, dict) and Path(feature.get("path", "")).resolve() == path
            and _file_sha(path) == feature.get("sha256"), "selected_frozen_feature_code_changed")
    if "integrity_sha256" in binding:
        unsigned = {key: value for key, value in binding.items() if key != "integrity_sha256"}
        _require(binding["integrity_sha256"] == _digest(unsigned), "selected_pointer_integrity_mismatch")
    return copy.deepcopy(binding)


def _validate_acceptance(reference, binding, *, verify_current_sources=False):
    acceptance = _reference(reference)
    _require(acceptance.get("schema") == ACCEPTANCE_SCHEMA and acceptance.get("consumer_id") == "single"
        and acceptance.get("status") == "passed_preactivation_review"
        and acceptance.get("formal_adoption_authorized") is True, "selected_preactivation_acceptance_required")
    _require(acceptance.get("model_sha256") == binding["model_sha256"]
        and Path(acceptance.get("model_path", "")).resolve() == Path(binding["model_path"]).resolve()
        and acceptance.get("authorization_note_sha256") == binding["authorization"]["sha256"],
        "selected_acceptance_authorization_or_model_mismatch")
    research = _reference(acceptance.get("research_acceptance"))
    research_status = research.get("acceptance", research.get("validation_worker_acceptance"))
    _require(research_status == "passed_research_reports_only"
        and research.get("research_only") is True and research.get("promotion") is False
        and research.get("can_deliver_research_report") is True
        and research.get("model_sha256") == binding["model_sha256"], "selected_research_evidence_scope_invalid")
    tests = _reference(acceptance.get("focused_tests"))
    _require(type(tests.get("exit_code")) is int and tests["exit_code"] == 0
        and isinstance(tests.get("command"), str) and tests["command"].strip(), "selected_focused_tests_not_passed")
    if verify_current_sources:
        references = acceptance.get("source_bindings")
        _require(isinstance(references, list) and bool(references), "selected_tested_source_bindings_required")
        for item in references:
            _require(isinstance(item, dict) and _file_sha(item["path"]) == item.get("sha256"),
                "selected_accepted_source_changed")
    return acceptance


def _receipt_paths(binding):
    root = SELECTED_CURRENT.parent / "selected_receipts"
    return root / (binding["activation_id"] + ".json"), root / (binding["activation_id"] + ".activated.json")


def _validate_receipt(binding):
    _require("integrity_sha256" in binding and isinstance(binding.get("acceptance"), dict),
        "selected_committed_binding_required")
    _require(isinstance(binding.get("feature_binding"), dict), "selected_frozen_feature_binding_required")
    prepared, committed = _receipt_paths(binding)
    receipt = _reference(binding.get("activation_receipt"))
    _require(Path(binding["activation_receipt"]["path"]).resolve() == prepared.resolve(),
        "selected_activation_receipt_path_mismatch")
    core = {key: value for key, value in binding.items() if key not in {"integrity_sha256", "activation_receipt"}}
    _require(receipt.get("schema") == RECEIPT_SCHEMA and receipt.get("event") == "authorized"
        and receipt.get("deployment_binding") == core, "selected_activation_receipt_mismatch")
    commit, _ = _json(committed)
    _require(commit.get("schema") == RECEIPT_SCHEMA and commit.get("event") == "activated"
        and commit.get("activation_id") == binding["activation_id"]
        and commit.get("binding_sha256") == _digest(binding), "selected_activation_commit_receipt_mismatch")
    _validate_acceptance(binding["acceptance"], binding)


def active_selected_binding() -> dict | None:
    """Read one small pointer; unchanged model bytes are SHA/identity cached."""
    try:
        document, _ = _json(SELECTED_CURRENT)
    except FileNotFoundError:
        if SELECTED_CURRENT.exists() or SELECTED_CURRENT.is_symlink():
            raise
        return None
    binding = _validate_binding(document)
    _validate_receipt(binding)
    return binding


def selected_analysis_environment() -> tuple[dict[str, str], dict] | None:
    """Refresh at each job start; no context/snapshot construction or model parse."""
    binding = active_selected_binding()
    if binding is None:
        return None
    job = _job_binding(binding)
    try:
        environment = dict(os.environ)
        environment[BINDING_ENV] = runtime._canonical(dict(job)).decode("utf-8")
        return environment, job
    except BaseException:
        close_source_lease(job)
        raise


def _head_binding(head_sha256=None):
    import stock_cache_lifecycle as lifecycle
    root = runtime.PUBLISHED_ROOT.resolve()
    registry = lifecycle.source_registry(root)
    with lifecycle.catalog_lock(registry):
        digest = _file_sha(root / "_metadata" / "stock_data_current.json") if head_sha256 is None else str(head_sha256)
        _require(_SHA.fullmatch(digest), "selected_job_head_invalid")
        archive = root / "_manifests" / (digest + ".json")
        _require(_file_sha(archive) == digest, "selected_job_head_archive_mismatch")
        # PublishedSources defines its fingerprint from these source identities,
        # before any route payload is read. Preserve that exact existing algorithm.
        fingerprint = hashlib.sha256(json.dumps([("shared_market_data", digest, None)], ensure_ascii=True).encode("utf-8")).hexdigest()
        lease = lifecycle.CacheLease(registry, "source-" + digest, physical_refs=[{
            "root": str(root), "ref": {"ref": archive.relative_to(root).as_posix(),
                "sha256": digest, "size": archive.stat().st_size}, "expand": True}], already_locked=True)
        try:
            return _HeadLeaseBinding({"head_sha256": digest, "snapshot_fingerprint": fingerprint,
                "published_sources": [{"source_id": "shared_market_data", "kind": "manifest_json", "root": str(root),
                    "manifest_path": str(archive), "manifest_sha256": digest, "snapshot_id": digest}],
                "snapshot_verification_scope": "head_bytes_only_payloads_verified_during_inference"}, lease)
        except BaseException:
            lease.close(already_locked=True)
            raise


def rebind_job_head(binding, head_sha256):
    head = _head_binding(head_sha256)
    try:
        result = _carry_head_lease({**binding, **head}, head)
    finally:
        close_source_lease(head)
    close_source_lease(binding)
    return result


def _job_binding(deployment, head_sha256=None):
    labels = []
    acceptance = _reference(deployment["acceptance"])
    for reference in acceptance.get("source_bindings") or []:
        try:
            actual = _file_sha(reference["path"])
        except OSError:
            actual = None
        if actual != reference.get("sha256"):
            labels.append({"code": "selected_non_numeric_source_differs", "path": reference.get("path"),
                "recorded": reference.get("sha256"), "actual": actual})
    head = _head_binding(head_sha256)
    try:
        return _carry_head_lease({"schema": "single-stock-selected-runtime-binding-v1",
            "deployment_binding": copy.deepcopy(deployment), **head, "version_labels": labels,
            "storage_policy": READ_ONLY_STORAGE_POLICY, "report_context_schema": REPORT_CONTEXT_SCHEMA}, head)
    finally:
        close_source_lease(head)


def _deployment(value):
    return value.get("deployment_binding", value) if isinstance(value, dict) else value


def analysis_binding():
    raw = os.environ.get(BINDING_ENV)
    if raw is not None:
        job = json.loads(raw)
        pinned = _validate_binding(_deployment(job))
        _validate_receipt(pinned)
        if job.get("schema") != "single-stock-selected-runtime-binding-v1":
            job = _job_binding(pinned)
        head = _head_binding(job.get("head_sha256"))
        try:
            _require(job.get("snapshot_fingerprint") == head["snapshot_fingerprint"], "selected_job_fingerprint_mismatch")
            labels = job.setdefault("version_labels", [])
            job["storage_policy"] = READ_ONLY_STORAGE_POLICY
            _require(job.get("report_context_schema", REPORT_CONTEXT_SCHEMA) == REPORT_CONTEXT_SCHEMA,
                "selected_job_report_context_schema_invalid")
            job["report_context_schema"] = REPORT_CONTEXT_SCHEMA
            try:
                current, _ = _json(SELECTED_CURRENT)
                if current.get("activation_id") != pinned["activation_id"]:
                    labels.append({"code": "selected_activation_changed_after_job_start",
                        "recorded": pinned["activation_id"], "actual": current.get("activation_id")})
            except (OSError, ValueError) as exc:
                labels.append({"code": "selected_current_unavailable_job_receipt_retained", "actual": type(exc).__name__})
            try:
                current_head = _file_sha(runtime.PUBLISHED_ROOT / "_metadata" / "stock_data_current.json")
                if current_head != job["head_sha256"]:
                    labels.append({"code": "newer_data_available", "recorded": job["head_sha256"], "actual": current_head})
            except (OSError, ValueError) as exc:
                labels.append({"code": "current_head_unavailable_job_pin_retained", "actual": type(exc).__name__})
            return _carry_head_lease(job, head)
        finally:
            close_source_lease(head)
    active = active_selected_binding()
    return _job_binding(active) if active is not None else None


def _exclusive_json(path, document):
    raw = runtime._canonical(document) + b"\n"
    with Path(path).open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    return {"path": str(Path(path).resolve()), "sha256": hashlib.sha256(raw).hexdigest()}


def activate_selected_model(model_path, *, cache_dir, authorization_note, acceptance_path) -> dict:
    """Explicit administrative call only. No caller invokes this during analysis."""
    model_path = Path(model_path).resolve(strict=True)
    binding = {"schema": DEPLOYMENT_SCHEMA, "consumer_id": "single",
        "activation_id": "single-selected-" + uuid.uuid4().hex,
        "approved_at": datetime.now(timezone.utc).isoformat(), "model_path": str(model_path),
        "model_sha256": _file_sha(model_path), "cache_dir": str(runtime._consumer_path(cache_dir)),
        "prior": None, "authorization": _authorization(authorization_note)}
    _validate_binding(binding)
    model, _ = _load_model(binding)
    features = Path(__file__).resolve().with_name("stock_daily_feature_catalog.py")
    feature_sha = ((model.get("metadata") or {}).get("runtime_input_contract") or {}).get("feature_module_sha256")
    _require(_file_sha(features) == feature_sha, "selected_frozen_feature_code_changed")
    binding["feature_binding"] = {"path": str(features), "sha256": feature_sha}
    acceptance_path = Path(acceptance_path).resolve(strict=True)
    binding["acceptance"] = {"path": str(acceptance_path), "sha256": _file_sha(acceptance_path),
        "schema": ACCEPTANCE_SCHEMA, "scope": "preactivation_review_not_formal_runtime_acceptance"}
    _validate_acceptance(binding["acceptance"], binding, verify_current_sources=True)
    SELECTED_CURRENT.parent.mkdir(parents=True, exist_ok=True)
    lock = SELECTED_CURRENT.parent / ".selected_activation.lock"
    # Exclusive lock and prior-byte CAS protect against two administrative writers.
    with lock.open("xb") as guard:
        try:
            guard.write(binding["activation_id"].encode("ascii"))
            guard.flush()
            prior = active_selected_binding()
            prior_sha = _file_sha(SELECTED_CURRENT) if prior is not None else None
            if prior is not None:
                binding["prior"] = {"activation_id": prior["activation_id"], "pointer_sha256": prior_sha,
                    "model_sha256": prior["model_sha256"], "activation_receipt": prior["activation_receipt"]}
            prepared, committed = _receipt_paths(binding)
            prepared.parent.mkdir(parents=True, exist_ok=True)
            binding["activation_receipt"] = _exclusive_json(prepared, {"schema": RECEIPT_SCHEMA,
                "event": "authorized", "deployment_binding": copy.deepcopy(binding)})
            binding["integrity_sha256"] = _digest(binding)
            with tempfile.NamedTemporaryFile(dir=SELECTED_CURRENT.parent, prefix=".selected-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(runtime._canonical(binding) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                _require((_file_sha(SELECTED_CURRENT) if SELECTED_CURRENT.exists() else None) == prior_sha,
                    "selected_prior_pointer_changed")
                os.replace(temporary, SELECTED_CURRENT)
                _exclusive_json(committed, {"schema": RECEIPT_SCHEMA, "event": "activated",
                    "activation_id": binding["activation_id"], "binding_sha256": _digest(binding),
                    "activated_at": datetime.now(timezone.utc).isoformat()})
            finally:
                if temporary.exists():
                    temporary.unlink()
        finally:
            guard.close()
            lock.unlink()
    return active_selected_binding()


def load_selected_context(pinned_head_sha256=None, activation_binding=None):
    """Reuse the verified reader without loading the unrelated shared C0 model."""
    policy = contract.c0_policy_binding(consumer_id="single")
    active = {key: None for key in contract._DUAL_BINDING_KEYS}
    active.update(policy_binding=policy, current_policy_binding=policy,
        contract_adoption_verified=False, contract_adoption_status="not_applicable_selected_model",
        artifact_binding={"sha256": (activation_binding or {}).get("model_sha256")},
        model_activation_epoch=(activation_binding or {}).get("approved_at"))
    if pinned_head_sha256 is None:
        return contract.load_c0_published_context(None, consumer_id="single", runtime_binding=active)
    import stock_cache_lifecycle as lifecycle
    root = runtime.PUBLISHED_ROOT.resolve()
    with lifecycle.catalog_lock(lifecycle.source_registry(root)):
        digest = str(pinned_head_sha256).strip().lower()
        _require(_SHA.fullmatch(digest), "selected_pinned_head_sha256_invalid")
        path = root / "_manifests" / (digest + ".json")
        _require(_file_sha(path) == digest, "selected_pinned_head_digest_mismatch")
        reference = {"root": str(root), "manifest_path": str(path), "manifest_sha256": digest}
        return contract.load_c0_published_context(reference, consumer_id="single", runtime_binding=active,
            selected_single_build=True)


def _inference(stock_query, binding, horizons, cache_dir, pinned_head_sha256, *, read_only=False, read_plan=None,
               verified_context=None):
    horizons = runtime._horizons(horizons)
    model, path = _load_model(binding)
    rules = (model.get("metadata") or {}).get("runtime_input_contract")
    _require(isinstance(rules, dict) and set(horizons).issubset(rules.get("horizons") or []),
        "selected_frozen_horizons_missing")
    features = importlib.import_module("stock_daily_feature_catalog")
    _require(_file_sha(features.__file__) == rules.get("feature_module_sha256"), "selected_frozen_feature_code_changed")
    plans = rules.get("source_plan_by_horizon")
    _require(isinstance(plans, dict) and all(str(h) in plans for h in horizons), "selected_frozen_source_plan_missing")
    owns_context = verified_context is None
    context = load_selected_context(pinned_head_sha256, binding) if owns_context else verified_context
    try:
        ids = runtime._resolve_stocks(stock_query, context["current_identity"])
        _require(len(ids) == 1, "selected_single_ordinary_stock_required")
        plan = runtime._merge_source_plans([plans[str(h)] for h in horizons])
        inputs = runtime.load_candidate_inputs(ids, context=context, consumer_id="single",
            cache_dir=cache_dir, horizons=rules["horizons"], source_plan=plan, allow_full_scan=read_only,
            **({"read_only": True, "persist_cache": False, "read_plan": read_plan} if read_only else {}))
        definitions = runtime._definition_map(model["metadata"])
        required_routes = set()
        for h in horizons:
            head = model["horizons"][str(h)]
            for kind in (head.get("selected") or {}).values():
                transformer = ((head.get("candidates") or {}).get(kind) or {}).get("transformer") or {}
                for name in set(transformer.get("effective_feature_order") or []) | set(
                        transformer.get("effective_indicator_order") or []):
                    definition = definitions.get(name) or {}
                    required_routes.add(definition.get("source_route", definition.get("route")))
        cold = sorted(item["route_id"] for item in inputs["source_manifest"]
            if item.get("status") == "projection_not_ready" and item["route_id"] in required_routes)
        _require(not cold, "selected_required_feature_projection_not_ready:" + ",".join(cold)
            + ";prepare_selected_cache_required")
        runtime._apply_frozen_schema(inputs["prepared"], model, horizons, plan)
        trained = (model["metadata"].get("snapshot_binding") or {}).get("head_sha256")
        if trained != inputs["snapshot"]["head_sha256"]:
            inputs["snapshot"]["version_labels"].append({"code": "candidate_inference_snapshot_differs",
                "training_head_sha256": trained, "inference_head_sha256": inputs["snapshot"]["head_sha256"],
                "reason_zh": "推論使用新的完整封存快照，未混接訓練快照。"})
        payloads = runtime._predict_payloads(model, path, inputs, horizons, expected_model_sha256=binding["model_sha256"])
        analysis = importlib.import_module("stock_report_analysis")
        report_contexts = {}
        for payload in payloads:
            key = (str(payload["stock"]["stock_id"]), payload["asof"])
            if key not in report_contexts:
                report_contexts[key] = analysis.build_report_context(inputs["source_frames"],
                    inputs["source_manifest"], stock=payload["stock"], asof=payload["asof"])
            payload["report_context"] = _detached_clone(report_contexts[key])
            _require_report_context(payload)
        if read_only and not (read_plan is not None and read_plan["legacy"]):
            for payload in payloads:
                payload["storage_policy"] = READ_ONLY_STORAGE_POLICY
                payload["read_plan"] = copy.deepcopy(inputs["read_plan"])
        for payload in payloads:
            # The candidate seal must cover the attached report evidence and read policy.
            payload["runtime_payload_sha256"] = _digest({key: value for key, value in payload.items()
                if key != "runtime_payload_sha256"})
        return payloads
    finally:
        if owns_context:
            context["sources"].close()


def _formal_limitations(limitations, *, audit=False):
    old = "本報告為候選研究，未正式採用，不更新既有模型、指標或前瞻帳本。"
    new = ("本報告為啟用前獨立驗收，不代表已啟用或可傳送。" if audit else
        "本模型已獲授權用於單股報告；採用不代表全面勝過基準，不改共用模型或前瞻帳本。")
    return [new if note == old else note for note in (limitations or [])]


def _require_report_context(payload):
    context = payload.get("report_context")
    _require(isinstance(context, dict) and _is_native_json_tree(context)
        and context.get("schema") == REPORT_CONTEXT_SCHEMA
        and context.get("stock_id") == str((payload.get("stock") or {}).get("stock_id"))
        and context.get("asof") == payload.get("asof")
        and "sources" in context and "statuses" in context, "selected_report_context_invalid_or_missing")


def _formal_payload(payload, binding, *, audit=False, seal=True):
    value = _detached_clone(payload)
    _require_report_context(value)
    value.update(schema=REPORT_SCHEMA, language="zh-TW", research_only=False, promotion=True,
        deployment_binding=_detached_clone(binding), deployment_status="preactivation_audit" if audit else "active",
        candidate_runtime_payload_sha256=value.pop("runtime_payload_sha256", None),
        report_context_schema=REPORT_CONTEXT_SCHEMA)
    # Only the obsolete promotion disclaimer changes; all numerical evidence stays.
    value["limitations"] = _formal_limitations(value.get("limitations"), audit=audit)
    if seal:
        value["runtime_payload_sha256"] = _digest(value)
    return value


def _output_root(output_dir, audit):
    path = Path(output_dir).resolve()
    root = (SINGLE_ROOT / "audit" if audit else SINGLE_ROOT).resolve()
    _require(path == root or root in path.parents, "selected_output_outside_consumer_scope")
    return runtime._consumer_path(path)


def generate_selected_reports(stock_query, *, output_dir, cache_dir=None, horizons=(1, 63),
                              pinned_head_sha256=None, activation_binding=None) -> list[dict]:
    job = activation_binding if activation_binding is not None else analysis_binding()
    try:
        return _generate_selected_reports(stock_query, output_dir=output_dir, cache_dir=cache_dir,
            horizons=horizons, pinned_head_sha256=pinned_head_sha256, activation_binding=job)
    finally:
        close_source_lease(job)


def _generate_selected_reports(stock_query, *, output_dir, cache_dir=None, horizons=(1, 63),
                               pinned_head_sha256=None, activation_binding=None) -> list[dict]:
    job = activation_binding if activation_binding is not None else analysis_binding()
    _require(job is not None, "selected_deployment_absent")
    _require(job.get("report_context_schema", REPORT_CONTEXT_SCHEMA) == REPORT_CONTEXT_SCHEMA,
        "selected_job_report_context_schema_invalid")
    binding = _validate_binding(_deployment(job))
    audit = "activation_receipt" not in binding
    if not audit:
        _validate_receipt(binding)
    output = _output_root(output_dir, audit)
    cache = runtime._consumer_path(cache_dir or binding["cache_dir"])
    _require(cache == runtime._consumer_path(binding["cache_dir"]), "selected_read_only_cache_root_mismatch")
    horizons = runtime._horizons(horizons)
    job_head = job.get("head_sha256") if job.get("schema") == "single-stock-selected-runtime-binding-v1" else None
    if pinned_head_sha256 is not None and job_head is not None:
        _require(pinned_head_sha256.lower() == job_head, "selected_explicit_pin_differs_from_job")
    pinned_head_sha256 = pinned_head_sha256 or job_head
    payloads = [_formal_payload(value, binding, audit=audit, seal=False) for value in
        _inference(stock_query, binding, horizons, cache, pinned_head_sha256, read_only=True)]
    report = importlib.import_module("stock_candidate_report")
    for payload in payloads:
        payload["snapshot_binding"].setdefault("version_labels", []).extend(copy.deepcopy(job.get("version_labels") or []))
        payload["inference_horizons"] = list(horizons)
        _require(payload.get("storage_policy") == READ_ONLY_STORAGE_POLICY
            and isinstance(payload.get("read_plan"), dict) and payload["read_plan"].get("legacy") is False,
            "selected_read_only_payload_required")
        payload["runtime_payload_sha256"] = _digest({key: value for key, value in payload.items()
            if key != "runtime_payload_sha256"})
        result = report.write_candidate_report(payload, output)
        pdf = Path(result["pdf_path"]).resolve(strict=True)
        data = Path(result["json_path"]).resolve(strict=True)
        _require(pdf.parent == data.parent and output in pdf.parents, "selected_report_output_paths_invalid")
        # The renderer already performs structural preflight; the formal validator
        # additionally reconstructs the frozen prediction from the pinned head.
        validation_job = dict(job) if job.get("schema") == "single-stock-selected-runtime-binding-v1" else {
            "deployment_binding": binding}
        validation_job["storage_policy"] = READ_ONLY_STORAGE_POLICY
        validation_job["report_context_schema"] = REPORT_CONTEXT_SCHEMA
        checked = validate_selected_report(payload, contract._stable_file_bytes(pdf),
            stock_id=payload["stock"]["stock_id"], horizon=payload["horizon_sessions"],
            expected_runtime_binding=validation_job, pdf_path=pdf, cache_dir=cache, require_active=not audit)
        _require(checked.get("valid") is True, "selected_generated_report_validation_failed:" + str(checked.get("errors")))
        payload.update(output_pdf=str(pdf), output_json=str(data), verification=checked)
        print("PDF：" + str(pdf), flush=True)
        print("JSON：" + str(data), flush=True)
    return payloads


def validate_selected_report(payload_or_bundle, pdf_bytes, *, stock_id, horizon,
                             expected_runtime_binding=None, pdf_path=None, cache_dir=None,
                             require_active=True) -> dict:
    """Verify real PDF bytes, compact evidence, activation and pinned reproduction."""
    try:
        report = importlib.import_module("stock_candidate_report")
        with report._parsed_evidence_operation():
            return _validate_selected_report(payload_or_bundle, pdf_bytes, stock_id=stock_id, horizon=horizon,
                expected_runtime_binding=expected_runtime_binding, pdf_path=pdf_path, cache_dir=cache_dir,
                require_active=require_active, report=report)
    except (OSError, ValueError, KeyError, TypeError, ImportError, RuntimeError) as exc:
        return {"valid": False, "verified": False, "prediction_reproduced": False,
            "errors": [{"code": type(exc).__name__, "message": str(exc)}]}


def _validate_selected_report(payload_or_bundle, pdf_bytes, *, stock_id, horizon,
                              expected_runtime_binding=None, pdf_path=None, cache_dir=None,
                              require_active=True, report) -> dict:
    try:
        bundle = payload_or_bundle if isinstance(payload_or_bundle, dict) and "payload" in payload_or_bundle else None
        payload = _detached_clone(bundle["payload"] if bundle is not None else payload_or_bundle)
        _require(isinstance(payload, dict) and payload.get("schema") == REPORT_SCHEMA
            and payload.get("language") == "zh-TW" and payload.get("research_only") is False
            and payload.get("promotion") is True, "selected_formal_report_schema_invalid")
        for field in ("output_pdf", "output_json", "verification"):
            payload.pop(field, None)
        _require(str((payload.get("stock") or {}).get("stock_id")) == str(stock_id)
            and type(payload.get("horizon_sessions")) is int and payload["horizon_sessions"] == int(horizon),
            "selected_report_identity_or_horizon_mismatch")
        job_context_policy = isinstance(expected_runtime_binding, dict) and "report_context_schema" in expected_runtime_binding
        if job_context_policy:
            _require(expected_runtime_binding["report_context_schema"] == REPORT_CONTEXT_SCHEMA,
                "selected_job_report_context_schema_invalid")
        requires_report_context = job_context_policy or "report_context_schema" in payload or "report_context" in payload
        if requires_report_context:
            _require(payload.get("report_context_schema") == REPORT_CONTEXT_SCHEMA,
                "selected_report_context_schema_missing_or_downgraded")
            _require_report_context(payload)
        binding = _validate_binding(payload.get("deployment_binding"))
        policy = payload.get("storage_policy")
        marked = any(isinstance(entry, dict) and isinstance(entry.get("query_binding"), dict)
            and "read_plan_policy" in entry["query_binding"] for entry in payload.get("source_manifest") or [])
        requires_policy = isinstance(expected_runtime_binding, dict) and "storage_policy" in expected_runtime_binding
        if policy is None:
            _require("storage_policy" not in payload and "read_plan" not in payload and not marked and not requires_policy,
                "selected_storage_policy_missing_or_downgraded")
        else:
            _require(policy == READ_ONLY_STORAGE_POLICY and isinstance(payload.get("read_plan"), dict)
                and payload["read_plan"].get("schema") == runtime.READ_PLAN_SCHEMA
                and payload["read_plan"].get("storage_policy") == policy
                and payload["read_plan"].get("legacy") is False, "selected_storage_policy_or_plan_invalid")
        if requires_policy:
            _require(expected_runtime_binding["storage_policy"] == READ_ONLY_STORAGE_POLICY
                and policy == READ_ONLY_STORAGE_POLICY, "selected_job_storage_policy_mismatch")
        if expected_runtime_binding is not None:
            expected = expected_runtime_binding.get("deployment_binding", expected_runtime_binding)
            _require(binding == expected, "selected_job_binding_mismatch")
            if expected_runtime_binding.get("schema") == "single-stock-selected-runtime-binding-v1":
                expected_head = expected_runtime_binding.get("head_sha256")
                _require((payload.get("snapshot_binding") or {}).get("head_sha256") == expected_head
                    and (payload.get("snapshot_binding") or {}).get("snapshot_fingerprint") ==
                    expected_runtime_binding.get("snapshot_fingerprint"), "selected_job_head_binding_mismatch")
        if require_active:
            _require(payload.get("deployment_status") == "active", "selected_preactivation_report_not_deliverable")
            _validate_receipt(binding)
            # A completed job can be verified against its immutable activation
            # receipt even if a later, explicitly authorized adoption supersedes it.
            if expected_runtime_binding is None:
                _require(active_selected_binding() == binding, "selected_active_binding_mismatch")
        snapshot = payload.get("snapshot_binding") or {}
        if "descriptor_reference" in snapshot:
            descriptor = report._resolve_descriptor_reference(snapshot["descriptor_reference"], payload, pdf_path)
            payload = report._restore_descriptor(payload, descriptor)
        unsigned = {key: value for key, value in payload.items() if key != "runtime_payload_sha256"}
        _require(payload.get("runtime_payload_sha256") == _digest(unsigned), "selected_runtime_payload_digest_mismatch")
        raw = bytes(pdf_bytes)
        _require(raw.startswith(b"%PDF-") and raw.rstrip().endswith(b"%%EOF"), "selected_pdf_incomplete")
        if pdf_path is not None:
            path = Path(pdf_path).resolve(strict=True)
            _require(contract._stable_file_bytes(path) == raw, "selected_pdf_bytes_mismatch")
            sidecar, _ = _json(path.with_suffix(".json"))
            if bundle is not None:
                _require(bundle == sidecar, "selected_bundle_bytes_mismatch")
            bundle = sidecar
            structural = report.validate_candidate_report(payload, path)
            _require(structural.get("valid") is True, "selected_pdf_or_sidecar_validation_failed")
        else:
            sections = report.build_report_sections(payload)
            _require(bundle is not None, "selected_bundle_or_pdf_path_required")
            report._validate_sidecar_payload(payload, bundle, None)
            _require(bundle.get("sections") == sections and bundle.get("sections_sha256") == report._digest(sections)
                and (bundle.get("pdf") or {}).get("sha256") == hashlib.sha256(raw).hexdigest()
                and (bundle.get("pdf") or {}).get("size") == len(raw), "selected_pdf_bundle_binding_mismatch")
            with tempfile.TemporaryDirectory(prefix="selected-pdf-validation-") as directory:
                path = Path(directory) / "report.pdf"
                path.write_bytes(raw)
                structural = report._inspect_structure(path, sections)
        head = (payload.get("snapshot_binding") or {}).get("head_sha256")
        _require(isinstance(head, str) and _SHA.fullmatch(head), "selected_report_head_missing")
        inference_horizons = runtime._horizons(payload.get("inference_horizons") or (int(horizon),))
        _require(int(horizon) in inference_horizons, "selected_inference_horizon_mismatch")
        cache = runtime._consumer_path(cache_dir or binding["cache_dir"])
        _require(cache == runtime._consumer_path(binding["cache_dir"]), "selected_read_only_cache_root_mismatch")
        plan = payload["read_plan"] if policy is not None else runtime.read_plan_from_manifest(
            payload.get("source_manifest"), cache, head)
        borrow_context = getattr(report, "_borrow_selected_context", None)
        verified_context = borrow_context(head, binding) if borrow_context is not None else None
        reproduced = _inference(str(stock_id), binding, inference_horizons, cache, head,
            read_only=True, read_plan=plan,
            **({"verified_context": verified_context} if verified_context is not None else {}))
        reproduced = [value for value in reproduced if value["horizon_sessions"] == int(horizon)]
        _require(len(reproduced) == 1, "selected_prediction_reproduction_missing")
        expected = reproduced[0]
        if policy is not None:
            _require(expected.get("storage_policy") == policy and expected.get("read_plan") == plan,
                "selected_read_plan_reproduction_mismatch")
        expected_limitations = _formal_limitations(expected.get("limitations"),
            audit=payload.get("deployment_status") != "active")
        stable_fields = ("stock", "horizon_sessions", "asof", "target_session_date", "prediction", "model_binding",
            "feature_values", "analysis_context", "metrics", "feature_limitations", "limitations", "price_history",
            "price_volume_unit", "data_quality_assessment", "source_manifest")
        if requires_report_context:
            _require_report_context(expected)
            stable_fields += ("report_context",)
        for key in stable_fields:
            expected_value = expected_limitations if key == "limitations" else expected.get(key)
            _require(_canonical_compatible(payload.get(key)) == _canonical_compatible(expected_value),
                "selected_prediction_or_evidence_mismatch:" + key)
        for key in ("head_sha256", "snapshot_fingerprint", "descriptor", "identity_binding", "retention_binding",
                    "capture_binding", "asof_by_market", "window_by_market"):
            _require(_canonical_compatible(payload["snapshot_binding"].get(key)) ==
                _canonical_compatible(expected["snapshot_binding"].get(key)), "selected_snapshot_reproduction_mismatch:" + key)
        return {"valid": True, "verified": True, "prediction_reproduced": True,
            "reproduction_status": "reproduced", "schema": "single-stock-selected-report-verification-v1",
            "deployment_binding": binding, "head_sha256": head,
            "runtime_payload_sha256": payload["runtime_payload_sha256"], "pdf_sha256": hashlib.sha256(raw).hexdigest(),
            "pdf_validation": structural, "activation_verified": require_active, "errors": []}
    except (OSError, ValueError, KeyError, TypeError, ImportError, RuntimeError) as exc:
        return {"valid": False, "verified": False, "prediction_reproduced": False,
            "errors": [{"code": type(exc).__name__, "message": str(exc)}]}
