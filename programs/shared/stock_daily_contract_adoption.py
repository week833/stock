"""Exact-byte administrative adoption of an unchanged C0 coefficient artifact.

This module never loads a model runtime, fetches data, fits coefficients, changes
the active pointer, or writes a forward database.  A receipt records the two
independently derived effective projections; equality is recomputed at every
validation rather than accepted from the receipt's boolean.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path

SCHEMA = "tw-stock-c0-current-contract-adoption-v1"
PROJECTION_SCHEMA = "tw-stock-c0-effective-semantics-v1"
MODEL_SCHEMA = "tw-stock-daily-linear-c0-v1"
FEATURE_SCHEMA = "tw-stock-c0-price-seven-v1"
FEATURE_ORDER = ["return_5", "return_20", "return_60", "return_126", "return_252", "volatility_20", "volatility_60"]
POLICY_KEYS = ("profile_id", "requirements_version", "feature_version", "policy_sha256", "requirements_raw_sha256")
PRESERVATION = {"new_epoch": False, "base_changed": False, "group_mapping_changed": False,
    "cursors_reset": False, "calibration_or_retrain_event_emitted": False}
_SHA = re.compile(r"[0-9a-f]{64}")
# Horizons/routes a projection must at least contain (more is a recorded difference).
_MIN_HORIZONS = {1, 5, 20, 60}
_CORE_ROUTES = {"official_calendar", "official_market_daily", "finmind_stock_info", "finmind_price_adj"}


class C0IntegrityError(ValueError):
    """Tampering, a torn read or invalid content: always raised (stock-daily exception split)."""


class C0VersionDrift:
    """Collects version/freshness differences as labels; never raises.

    Each label is ``{code, recorded, actual, detail}``; the list is exposed as
    ``version_labels`` so consumers can show what differs from the recorded
    contract while the analysis proceeds with the data actually read.
    """

    def __init__(self, labels=None):
        self.labels = labels if isinstance(labels, list) else []

    def note(self, code, *, recorded=None, actual=None, detail=None):
        label = {"code": str(code), "recorded": _plain(recorded), "actual": _plain(actual), "detail": _plain(detail)}
        if label not in self.labels:
            self.labels.append(label)
        return label

    def extend(self, labels):
        for label in labels or []:
            if isinstance(label, Mapping) and label not in self.labels:
                self.labels.append(_plain(dict(label)))
        return self

    def codes(self):
        return [label["code"] for label in self.labels]


def _plain(value):
    """A JSON-safe copy (labels are written into reports with allow_nan=False)."""
    if value is None:
        return None
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str, allow_nan=False))
    except (TypeError, ValueError):
        return str(value)


def _drift(drift):
    return drift if drift is not None else C0VersionDrift()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def projection_bytes(value):
    """The independent projection document codec includes exactly one final LF."""
    return canonical(value) + b"\n"


def projection_digest(value):
    return hashlib.sha256(projection_bytes(value)).hexdigest()


def _require(condition, reason):
    if not condition:
        raise C0IntegrityError(reason)


def _stable_bytes(path):
    selected = Path(path).resolve(strict=True)
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    with selected.open("rb") as handle:
        before = identity(os.fstat(handle.fileno()))
        raw = handle.read()
        after = identity(os.fstat(handle.fileno()))
    _require(before == after == identity(selected.stat()), "c0_adoption_ref_changed_during_read")
    return raw


def _json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            _require(key not in result, "c0_adoption_duplicate_json_key")
            result[key] = value
        return result
    result = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError("c0_adoption_nonfinite_json")))
    _require(isinstance(result, Mapping), "c0_adoption_json_object_required")
    return dict(result)


def content_ref(path, *, schema=None, revision=None):
    selected = Path(path).resolve(strict=True)
    raw = _stable_bytes(selected)
    result = {"path": str(selected), "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
    if schema is not None:
        result["schema"] = schema
    if revision is not None:
        result["revision"] = revision
    return result


def read_content_ref(reference, *, expected_schema=None, json_object=True, mutable=False, drift=None):
    """Read an exact-byte reference.

    A content-addressed file (receipt, projection, sealed core, original policy)
    that no longer matches its recorded digest was tampered with: always raised.
    A ``mutable`` file (current daily policy json, registry, capture
    requirements, current.json, consumer source) is expected to change after
    adoption: with a ``drift`` collector the actual bytes are read and the change
    is recorded as ``mutable_ref_changed_since_adoption``.  A torn read still raises.
    """
    _require(isinstance(reference, Mapping), "c0_adoption_content_ref_missing")
    path = Path(reference.get("path") or "")
    _require(path.is_absolute() and isinstance(reference.get("sha256"), str)
        and _SHA.fullmatch(reference["sha256"]) is not None
        and type(reference.get("size")) is int and reference["size"] > 0,
        "c0_adoption_content_ref_invalid")
    raw = _stable_bytes(path)
    actual = {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
    if actual != {"sha256": reference["sha256"], "size": reference["size"]}:
        _require(mutable and drift is not None, "c0_adoption_content_ref_digest_or_size_mismatch")
        drift.note("mutable_ref_changed_since_adoption", recorded={"sha256": reference["sha256"], "size": reference["size"]},
            actual=actual, detail={"path": str(path)})
    if expected_schema is not None:
        _require(reference.get("schema") == expected_schema, "c0_adoption_content_ref_schema_mismatch")
    value = _json(raw) if json_object else raw
    if json_object and reference.get("schema") is not None:
        _require(value.get("schema") == reference["schema"], "c0_adoption_referenced_body_schema_mismatch")
    result = {"path": str(path.resolve()), **{key: copy.deepcopy(reference[key])
        for key in ("sha256", "size", "schema", "revision") if key in reference}}
    result.update(actual)
    return value, result


def policy_identity(binding):
    _require(isinstance(binding, Mapping), "c0_original_feature_contract_missing")
    result = {key: binding.get(key) for key in POLICY_KEYS}
    _require(result["profile_id"] == "C0" and all(isinstance(result[key], str) and result[key]
        for key in POLICY_KEYS), "c0_original_feature_contract_invalid")
    for key in ("policy_sha256", "requirements_raw_sha256"):
        _require(_SHA.fullmatch(result[key]) is not None, "c0_original_feature_contract_digest_invalid")
    return result


def original_model_binding(pointer, drift=None):
    """Read original coefficient/sidecar bytes without importing or refitting.

    The training-time core identity is the sha recorded in the artifact (which
    must agree with its own sidecar).  The current core file is only compared:
    a difference is the label ``inference_code_differs_from_training``.
    """
    drift = _drift(drift)
    _require(isinstance(pointer, Mapping) and pointer.get("schema") == "tw-stock-daily-linear-current-v1",
        "c0_adoption_pointer_schema_invalid")
    artifact_path = Path(pointer.get("artifact_path") or "")
    _require(artifact_path.is_absolute(), "c0_adoption_artifact_path_invalid")
    raw = _stable_bytes(artifact_path)
    artifact_sha = hashlib.sha256(raw).hexdigest()
    _require(pointer.get("artifact_sha256") == artifact_sha and pointer.get("artifact_size") == len(raw),
        "c0_adoption_original_artifact_mismatch")
    model = _json(raw)
    sidecar_path = artifact_path.with_name(artifact_path.name + ".metadata.json")
    sidecar_raw = _stable_bytes(sidecar_path)
    sidecar = _json(sidecar_raw)
    metadata = model.get("metadata") or {}
    _require(model.get("schema") == metadata.get("schema") == sidecar.get("schema") == MODEL_SCHEMA,
        "c0_adoption_original_model_schema_mismatch")
    _require(sidecar.get("artifact_sha256") == artifact_sha and sidecar.get("artifact_byte_size") == len(raw),
        "c0_adoption_original_sidecar_mismatch")
    _require(metadata.get("feature_schema") == sidecar.get("feature_schema") == FEATURE_SCHEMA
        and metadata.get("feature_order") == sidecar.get("feature_order") == FEATURE_ORDER,
        "c0_adoption_original_feature_contract_mismatch")
    core_path = Path(pointer.get("module_path") or "")
    _require(core_path.is_absolute(), "c0_adoption_original_core_path_invalid")
    recorded_core = metadata.get("module_sha256")
    # Artifact self-consistency (sidecar is sealed by the artifact digest above).
    _require(isinstance(recorded_core, str) and _SHA.fullmatch(recorded_core) is not None
        and recorded_core == sidecar.get("module_sha256"), "c0_adoption_original_core_source_mismatch")
    if pointer.get("module_sha256") != recorded_core:
        drift.note("model_pointer_module_sha_differs", recorded=recorded_core, actual=pointer.get("module_sha256"),
            detail={"pointer_field": "module_sha256"})
    try:
        current_core = hashlib.sha256(_stable_bytes(core_path)).hexdigest()
    except FileNotFoundError:
        current_core = None
        drift.note("training_code_file_missing", recorded=recorded_core, detail={"path": str(core_path)})
    if current_core is not None and current_core != recorded_core:
        drift.note("inference_code_differs_from_training", recorded=recorded_core, actual=current_core,
            detail={"path": str(core_path), "meaning": "model_built_by_a_different_program_version"})
    core_sha = recorded_core
    anchor = copy.deepcopy(metadata.get("policy_binding"))
    identity = policy_identity(anchor)
    _require(metadata.get("policy_binding_sha256") == sidecar.get("policy_binding_sha256") == digest(identity),
        "c0_adoption_original_policy_identity_mismatch")
    _require(metadata.get("caller_policy_binding_sha256") == digest(anchor),
        "c0_adoption_original_caller_policy_mismatch")
    result = {"artifact_path": str(artifact_path.resolve()), "sha256": artifact_sha, "size": len(raw),
        "sidecar_sha256": hashlib.sha256(sidecar_raw).hexdigest(), "module_sha256": core_sha,
        "schema": MODEL_SCHEMA, "feature_schema": FEATURE_SCHEMA, "feature_order": list(FEATURE_ORDER),
        "target_definition": copy.deepcopy(metadata.get("target_definition"))}
    return result, anchor, model


def _projection(value):
    _require(isinstance(value, Mapping) and value.get("schema") == PROJECTION_SCHEMA,
        "c0_adoption_effective_projection_schema_invalid")
    # Version and contract provenance deliberately do not replace scientific fields.
    _require(value.get("profile_id") == "C0", "c0_adoption_effective_projection_profile_invalid")
    _require(value.get("schema_version") == 1 and value.get("model_schema") == MODEL_SCHEMA
        and value.get("feature_schema") == FEATURE_SCHEMA and value.get("feature_order") == FEATURE_ORDER
        and [item.get("name") for item in value.get("features", [])] == FEATURE_ORDER
        and isinstance(value.get("horizons"), list) and _MIN_HORIZONS.issubset(value["horizons"])
        and (value.get("price_gate") or {}).get("required_positions") == 253,
        "c0_adoption_effective_projection_incomplete")
    # At least the routes the model reads; any further route is a recorded difference, not a block.
    _require(isinstance(value.get("required_routes"), list) and _CORE_ROUTES.issubset(value["required_routes"]),
        "c0_adoption_effective_projection_routes_invalid")
    canonical(value)
    return copy.deepcopy(dict(value))


def _policy_module_info(reference):
    """Record the policy module's current sha; it never gates (plan 2.2)."""
    path = Path((reference or {}).get("path") or "") if isinstance(reference, Mapping) else Path("")
    actual = None
    if path.is_absolute():
        try:
            raw = _stable_bytes(path)
            actual = {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
        except FileNotFoundError:
            actual = None
    result = {"path": str(path)}
    result.update(actual or {"sha256": None, "size": None})
    if isinstance(reference, Mapping) and reference.get("sha256") is not None:
        result["recorded_sha256"] = reference.get("sha256")
    return result


def _current_contract(contract, drift=None):
    """Read the daily contract refs (mutable files) and record every difference as a label.

    Schema major versions (policy v2, registry v3, capture v1) remain parse
    requirements; versions, bindings and file names are labels.
    """
    drift = _drift(drift)
    _require(isinstance(contract, Mapping), "c0_adoption_current_contract_missing")
    policy, policy_ref = read_content_ref(contract.get("daily_policy_ref"), expected_schema="tw-stock-daily-requirements-v2",
        mutable=True, drift=drift)
    registry, registry_ref = read_content_ref(contract.get("registry_ref"), expected_schema="shared-market-data-requirements-v3",
        mutable=True, drift=drift)
    module_ref = _policy_module_info(contract.get("policy_module_ref"))
    capture, capture_ref = read_content_ref(contract.get("capture_requirements_ref"),
        expected_schema="twstock.application-capture-requirements.v1", mutable=True, drift=drift)
    _require(policy.get("schema_version") == 2 and "C0" in (policy.get("profiles") or {})
        and registry.get("schema_version") == 3 and capture.get("schema_version") == 1,
        "c0_adoption_current_only_schema_invalid")
    if set(policy.get("profiles") or {}) != {"C0"}:
        drift.note("policy_version_mismatch", recorded=["C0"], actual=sorted(policy.get("profiles") or {}),
            detail={"field": "profiles"})
    for field in ("requirements_version", "feature_version"):
        if contract.get(field) != policy.get(field):
            drift.note("policy_version_mismatch", recorded=contract.get(field), actual=policy.get(field), detail={"field": field})
    binding = policy.get("requirements_binding") or {}
    if binding.get("raw_sha256") != registry_ref["sha256"] or binding.get("byte_size") != registry_ref["size"]:
        drift.note("policy_version_mismatch", recorded={"sha256": binding.get("raw_sha256"), "size": binding.get("byte_size")},
            actual={"sha256": registry_ref["sha256"], "size": registry_ref["size"]}, detail={"field": "requirements_binding"})
    if Path(binding.get("canonical_path") or "").name != Path(registry_ref["path"]).name:
        drift.note("policy_version_mismatch", recorded=binding.get("canonical_path"), actual=registry_ref["path"],
            detail={"field": "requirements_binding.canonical_path"})
    capture_binding = policy.get("application_capture_binding") or {}
    if capture_binding.get("raw_sha256") != capture_ref["sha256"] or capture_binding.get("byte_size") != capture_ref["size"]:
        drift.note("policy_version_mismatch", recorded={"sha256": capture_binding.get("raw_sha256"),
            "size": capture_binding.get("byte_size")}, actual={"sha256": capture_ref["sha256"], "size": capture_ref["size"]},
            detail={"field": "application_capture_binding"})
    return {"daily_policy_ref": policy_ref, "registry_ref": registry_ref, "policy_module_ref": module_ref,
        "capture_requirements_ref": capture_ref,
        "requirements_version": policy.get("requirements_version"), "feature_version": policy.get("feature_version")}, policy, registry


def create_contract_adoption(pointer, current_contract, *, original_projection, current_projection,
                             original_projection_ref, current_projection_ref, projection_derivation):
    """Construct a receipt from true original bytes and independently extracted projections.

    Differing semantics no longer prevent a receipt: the receipt records
    ``equal: false`` and the differing projection fields, and every validation
    reports that as ``effective_semantics_changed`` (it never claims equality).
    """
    drift = C0VersionDrift()
    original, anchor, _ = original_model_binding(pointer, drift)
    contract, current_policy, current_registry = _current_contract(current_contract, drift)
    before = _projection(original_projection)
    after = _projection(current_projection)
    original_ref_value, before_ref = read_content_ref(original_projection_ref, expected_schema=PROJECTION_SCHEMA)
    current_ref_value, after_ref = read_content_ref(current_projection_ref, expected_schema=PROJECTION_SCHEMA)
    _require(before == original_ref_value and after == current_ref_value, "c0_adoption_projection_source_ref_mismatch")
    _require(before_ref["sha256"] == projection_digest(before) and before_ref["size"] == len(projection_bytes(before))
        and after_ref["sha256"] == projection_digest(after) and after_ref["size"] == len(projection_bytes(after)),
        "c0_adoption_projection_reference_noncanonical_codec")
    equal = canonical(before) == canonical(after)
    derivation = _verify_projection_derivation(projection_derivation, original, policy_identity(anchor),
        current_policy, current_registry, before, after, drift)
    receipt = {"schema": SCHEMA, "schema_version": 1, "original_model": original,
        "original_training_policy_identity": policy_identity(anchor), "current_contract": contract,
        "effective_C0_semantics": {"projection_schema": PROJECTION_SCHEMA,
            "original_projection": before, "original_projection_sha256": projection_digest(before),
            "current_projection": after, "current_projection_sha256": projection_digest(after), "equal": equal,
            "original_projection_ref": before_ref, "current_projection_ref": after_ref},
        "stable_base_model_version": original["sha256"], "adoption_kind": "operational_contract_cleanup_no_refit",
        "forward_identity_preservation": dict(PRESERVATION), "projection_derivation": derivation}
    if not equal:
        receipt["effective_C0_semantics"]["differing_fields"] = _differing_fields(before, after)
    if drift.labels:
        receipt["version_labels"] = drift.labels
    return receipt


def _differing_fields(before, after):
    keys = sorted(set(before) | set(after))
    return [key for key in keys if canonical(before.get(key)) != canonical(after.get(key))]


def validate_contract_adoption(receipt_ref, pointer, *, expected_current_contract,
                               expected_current_projection, expected_original_projection):
    """Audit a receipt against live references and two real projections (optional audit).

    Content-addressed bytes (receipt, projections, sealed core, original policy
    and registry extract) that differ from their recorded digest still raise.
    Every version/policy difference becomes a mismatch label; ``verified`` is
    True only when there is none, so callers can isolate forward statistics.
    """
    drift = C0VersionDrift()
    receipt, reference = read_content_ref(receipt_ref, expected_schema=SCHEMA)
    _require(receipt.get("schema_version") == 1 and receipt.get("adoption_kind") == "operational_contract_cleanup_no_refit",
        "c0_adoption_schema_or_kind_invalid")
    # Program-version differences are labels of their own; they do not make the
    # administrative adoption unverified (forward isolation is for adoption).
    code = C0VersionDrift()
    original, anchor, model = original_model_binding(pointer, code)
    if receipt.get("original_model") != original or receipt.get("original_training_policy_identity") != policy_identity(anchor):
        drift.note("adoption_receipt_model_differs", recorded=(receipt.get("original_model") or {}).get("sha256"),
            actual=original["sha256"], detail={"fields": [key for key in sorted(set(original) | set(receipt.get("original_model") or {}))
                if (receipt.get("original_model") or {}).get(key) != original.get(key)]})
    if receipt.get("stable_base_model_version") != original["sha256"] or receipt.get("forward_identity_preservation") != PRESERVATION:
        drift.note("adoption_receipt_forward_identity_differs", recorded=receipt.get("stable_base_model_version"),
            actual=original["sha256"])
    expected, current_policy, current_registry = _current_contract(expected_current_contract, drift)
    declared, _, _ = _current_contract(receipt.get("current_contract"), drift)
    compare = lambda value: {key: item for key, item in value.items() if key != "policy_module_ref"}
    if canonical(compare(expected)) != canonical(compare(declared)):
        drift.note("policy_version_mismatch", detail={"field": "current_contract", "differs": [key for key in sorted(compare(expected))
            if canonical(expected.get(key)) != canonical(declared.get(key))]})
    if expected["policy_module_ref"].get("sha256") != (declared["policy_module_ref"].get("recorded_sha256")
            or declared["policy_module_ref"].get("sha256")):
        drift.note("policy_module_changed_since_adoption",
            recorded=declared["policy_module_ref"].get("recorded_sha256") or declared["policy_module_ref"].get("sha256"),
            actual=expected["policy_module_ref"].get("sha256"), detail={"path": expected["policy_module_ref"].get("path")})
    semantics = receipt.get("effective_C0_semantics") or {}
    before = _projection(semantics.get("original_projection"))
    after = _projection(semantics.get("current_projection"))
    original_value, before_ref = read_content_ref(semantics.get("original_projection_ref"), expected_schema=PROJECTION_SCHEMA)
    current_value, after_ref = read_content_ref(semantics.get("current_projection_ref"), expected_schema=PROJECTION_SCHEMA)
    _require(before == original_value and after == current_value, "c0_adoption_effective_projection_source_mismatch")
    # The recomputed projections come from current (mutable) policy: an incomplete
    # one is a policy version difference, not tampering (the receipt's own copies
    # were validated above).
    def _recomputed(value):
        try:
            return _projection(value) if value is not None else None
        except C0IntegrityError:
            return None
    if expected_original_projection is None or before != _recomputed(expected_original_projection):
        drift.note("policy_version_mismatch", detail={"field": "original_projection", "reason":
            "recomputed_original_projection_unavailable" if expected_original_projection is None else "differs"})
    if expected_current_projection is None or after != _recomputed(expected_current_projection):
        drift.note("policy_version_mismatch", detail={"field": "current_projection", "reason":
            "recomputed_current_projection_unavailable" if expected_current_projection is None else "differs",
            "differing_fields": _differing_fields(after, expected_current_projection)
                if isinstance(expected_current_projection, Mapping) else None})
    _require(before_ref["sha256"] == projection_digest(before) and before_ref["size"] == len(projection_bytes(before))
        and after_ref["sha256"] == projection_digest(after) and after_ref["size"] == len(projection_bytes(after)),
        "c0_adoption_projection_reference_noncanonical_codec")
    _verify_projection_derivation(receipt.get("projection_derivation"), original, policy_identity(anchor),
        current_policy, current_registry, before, after, drift)
    before_digest, after_digest = projection_digest(before), projection_digest(after)
    if not (semantics.get("projection_schema") == PROJECTION_SCHEMA and semantics.get("equal") is True
            and semantics.get("original_projection_sha256") == before_digest
            and semantics.get("current_projection_sha256") == after_digest
            and before_digest == after_digest and canonical(before) == canonical(after)):
        drift.note("effective_semantics_changed", recorded=before_digest, actual=after_digest,
            detail={"differing_fields": _differing_fields(before, after), "receipt_equal": semantics.get("equal")})
    status = "verified" if not drift.labels else "drift"
    return {"schema": SCHEMA, "verified": not drift.labels, "status": status, "mismatches": drift.labels,
        "code_labels": code.labels, "contract_adoption_ref": reference,
        "original_model_feature_contract": anchor, "original_model": original, "model": model,
        "current_contract": expected, "effective_C0_semantics_projection_sha256": after_digest,
        "original_effective_C0_semantics_projection_sha256": before_digest,
        "stable_base_model_version": original["sha256"], "forward_identity_preservation": dict(PRESERVATION)}


def adopted_pointer_spec(pointer, receipt_ref):
    """Return a new-pointer specification; activation remains an explicit outer operation."""
    _require(isinstance(pointer, Mapping) and pointer.get("schema") == "tw-stock-daily-linear-current-v1",
        "c0_adoption_pointer_schema_invalid")
    read_content_ref(receipt_ref, expected_schema=SCHEMA)
    result = copy.deepcopy(dict(pointer))
    result["contract_adoption_ref"] = copy.deepcopy(dict(receipt_ref))
    return result


def _verify_projection_derivation(derivation, original_model, anchor, current_policy, current_registry, before, after,
                                  drift=None):
    drift = _drift(drift)
    _require(isinstance(derivation, Mapping), "c0_adoption_projection_derivation_missing")
    # Content-addressed inputs: a digest difference is tampering and raises.
    sealed, sealed_ref = read_content_ref(derivation.get("sealed_core_semantics_ref"),
        expected_schema="tw-stock-c0-sealed-core-semantics-v1")
    old_policy, old_policy_ref = read_content_ref(derivation.get("original_policy_ref"))
    old_registry, old_registry_ref = read_content_ref(derivation.get("original_registry_extract_ref"))
    if (old_policy_ref["sha256"] != anchor["policy_sha256"]
            or (old_registry.get("original_registry_ref") or {}).get("sha256") != anchor["requirements_raw_sha256"]):
        drift.note("policy_version_mismatch", recorded={"policy_sha256": anchor["policy_sha256"],
            "requirements_raw_sha256": anchor["requirements_raw_sha256"]}, actual={"policy_sha256": old_policy_ref["sha256"],
            "requirements_raw_sha256": (old_registry.get("original_registry_ref") or {}).get("sha256")},
            detail={"field": "original_projection_inputs"})
    linear_sources = [ref for ref in sealed.get("source_refs", []) if Path(ref.get("path", "")).name == "stock_daily_linear.py"]
    if len(linear_sources) != 1 or linear_sources[0].get("sha256") != original_model["module_sha256"]:
        drift.note("inference_code_differs_from_training", recorded=[ref.get("sha256") for ref in linear_sources],
            actual=original_model["module_sha256"], detail={"field": "sealed_core_semantics.source_refs"})
    try:
        original_rebuilt = effective_c0_projection(old_policy, old_registry, sealed_core_semantics=sealed, original=True)
    except ValueError as exc:
        original_rebuilt = None
        drift.note("policy_version_mismatch", detail={"field": "original_projection_reconstruction", "reason": str(exc)})
    try:
        current_rebuilt = effective_c0_projection(current_policy, current_registry, sealed_core_semantics=sealed, drift=drift)
    except ValueError as exc:
        current_rebuilt = None
        drift.note("policy_version_mismatch", detail={"field": "current_projection_reconstruction", "reason": str(exc)})
    if before != original_rebuilt or after != current_rebuilt:
        drift.note("policy_version_mismatch", detail={"field": "independent_projection_reconstruction",
            "original_equal": before == original_rebuilt, "current_equal": after == current_rebuilt})
    return {"sealed_core_semantics_ref": sealed_ref, "original_policy_ref": old_policy_ref,
        "original_registry_extract_ref": old_registry_ref}


def effective_c0_projection(policy, registry, *, sealed_core_semantics, original=False, drift=None):
    """Project the effective C0 semantics; a missing field is a named error, never a bare KeyError.

    With a ``drift`` collector, current declared math that differs from the
    sealed core is recorded (``policy_version_mismatch``) instead of raised.
    """
    try:
        return _effective_c0_projection(policy, registry, sealed_core_semantics=sealed_core_semantics,
            original=original, drift=drift)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("c0_effective_projection_field_missing:" + str(exc)) from exc


def _effective_c0_projection(policy, registry, *, sealed_core_semantics, original=False, drift=None):
    import copy
    core = sealed_core_semantics
    model = core if original else policy["c0_model_contract"]
    if not original:
        for field in ("model_schema", "feature_schema", "feature_order", "feature_math", "horizons",
                      "target_definition", "core_label_up_comparison", "price_gate", "quality", "prediction"):
            if canonical(model.get(field)) != canonical(core.get(field)):
                if drift is None:
                    raise ValueError("c0_current_declared_math_differs_from_unchanged_sealed_core:" + field)
                drift.note("policy_version_mismatch", detail={"field": "c0_model_contract." + field,
                    "reason": "current_declared_math_differs_from_unchanged_sealed_core"})
    if original:
        selected = registry["selected_routes"]
    else:
        selected = {}
        for route in policy["profiles"]["C0"]["required_routes"]:
            catalog = registry["capability_catalog"]["routes"][route]
            matches = [item for item in registry["shared_raw_requirements"] if item["id"] == route]
            if len(matches) != 1:
                raise ValueError("c0_native_route_not_exactly_once")
            selected[route] = {"catalog": catalog, "raw_contract": matches[0]}
    returns, risk = policy["feature_specs"]["returns"], policy["feature_specs"]["risk"]
    return_windows = [n for n in returns["windows"] if n in model["feature_math"]["return_windows"]]
    risk_windows = [n for n in risk["windows"] if n in model["feature_math"]["volatility_windows"]]
    features = [{"name": "return_" + str(n), "window": n, "formula": model["feature_math"]["return_formula"], "unit": returns["unit"]} for n in return_windows]
    features += [{"name": "volatility_" + str(n), "window": n, "formula": model["feature_math"]["volatility_formula"], "ddof": risk.get("ddof", core["feature_math"]["volatility_ddof"]), "minimum_daily_return_count": n, "required_close_count": n + 1, "unit": risk["unit"]} for n in risk_windows]
    windows = {route: {key: copy.deepcopy(policy["route_windows"][route][key]) for key in ("unit", "observations", "predecessor_observations", "minimum_observations")} for route in ("official_market_daily", "official_calendar", "finmind_price_adj", "finmind_dividend", "finmind_dividend_result")}
    for route in ("finmind_dividend", "finmind_dividend_result"):
        windows[route]["observation_semantics"] = policy["route_windows"][route]["observation_semantics"]
        windows[route]["required_anchors"] = copy.deepcopy(policy["route_windows"][route]["required_anchors"])
    for key in ("future_target_sessions", "target_axis_policy"):
        windows["official_calendar"][key] = policy["route_windows"]["official_calendar"][key]
    proofs = {route: {"order": value["catalog"]["requirements_order"], "natural_keys": copy.deepcopy(value["catalog"]["natural_keys"]), "native_schema_policy": value["raw_contract"]["schema_policy"], "raw_normalized_hash_policy": value["raw_contract"]["hash_policy"], "missing_evidence_stays_in_denominator": value["catalog"]["coverage"].get("missing_evidence_stays_in_denominator", True)} for route, value in selected.items()}
    if original:
        forward = copy.deepcopy(core["forward_semantics"])
        cadence = policy["training_policy"]["cadence"]
        forward.update(calibrate_after_new_mature_signal_dates=cadence["calibrate_after_new_mature_signal_days"], manual_retrain_token_after_new_mature_signal_dates=cadence["retrain_after_new_mature_signal_days"], calibration_max_signal_dates=cadence["calibration_max_signal_days"], automatic_fit=cadence["automatic_daily_retraining"])
    else:
        forward = copy.deepcopy(policy["forward_policy"])
    return {"schema": "tw-stock-c0-effective-semantics-v1", "schema_version": 1, "profile_id": "C0", "model_schema": model["model_schema"], "feature_schema": model["feature_schema"], "feature_order": copy.deepcopy(model["feature_order"]), "features": features, "horizons": copy.deepcopy(model["horizons"]), "target_definition": model["target_definition"], "core_label_up_comparison": model["core_label_up_comparison"], "price_gate": copy.deepcopy(model["price_gate"]), "universe": {key: copy.deepcopy(policy["universe_policy"][key]) for key in ("markets", "security_class", "excluded_classes", "current_info_is_not_historical_membership")}, "required_routes": copy.deepcopy(policy["profiles"]["C0"]["required_routes"]), "required_route_windows": windows, "current_identity": {"unit": "verified_current_official_positive_ordinary_identity", "historical_membership": "not_claimed", "source_proof_required": True}, "route_proof_contracts": proofs, "quality": copy.deepcopy(model["quality"]), "prediction": copy.deepcopy(model["prediction"]), "forward_semantics": forward}

