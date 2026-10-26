"""Build source-activation DATA evidence after actual deployment readback.

The producer consumes the resulting JSON, never imports this analysis module.
Source activation and a model pointer prove configuration; they do not assert
that a normal analysis entry has executed successfully.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import stock_daily_contract_adoption as adoption

SCHEMA = "tw-stock-application-active-evidence-v1"
CANONICAL_PATH = Path(r"D:\TWStockData\contracts\shared_market_data\application_active_evidence_v1.json")
ACTIVATION_SCHEMA = "tw-stock-consumer-current-source-activation-v1"


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _actual_file_ref(reference, drift, missing_code):
    """The deployed bytes as they are now (consumer source / current files); never a gate."""
    path = Path((reference or {}).get("path") or "") if isinstance(reference, Mapping) else Path("")
    try:
        actual = adoption.content_ref(path)
    except FileNotFoundError:
        drift.note(missing_code, recorded=(reference or {}).get("sha256") if isinstance(reference, Mapping) else None,
            detail={"path": str(path)})
        return None
    if isinstance(reference, Mapping) and (reference.get("sha256"), reference.get("size")) != (actual["sha256"], actual["size"]):
        drift.note("mutable_ref_changed_since_adoption", recorded={"sha256": reference.get("sha256"),
            "size": reference.get("size")}, actual={"sha256": actual["sha256"], "size": actual["size"]},
            detail={"path": actual["path"]})
    return actual


def build_active_evidence(active, current_contract_refs, consumer_activations, *, revision="2026-10-09.current-only.1"):
    """Return exact-byte evidence; callers own the authorized atomic JSON write.

    Consumer source bytes no longer decide whether the producer may publish:
    the actual sha of each listed source is recorded.  Adoption, pointer,
    activation and contract differences are ``version_labels`` (present only
    when non-empty).  Content-addressed receipts/projections still raise on
    tamper, and the consumer inventory grammar is kept.
    """
    drift = adoption.C0VersionDrift()
    _require(isinstance(active, Mapping), "application_active_model_binding_missing")
    if active.get("contract_adoption_verified") is not True:
        drift.note("adoption_unverified", actual=active.get("contract_adoption_status"))
    pointer, pointer_ref = adoption.read_content_ref(active.get("pointer_binding"), mutable=True, drift=drift)
    receipt, adoption_ref = adoption.read_content_ref(active.get("contract_adoption_ref"), expected_schema=adoption.SCHEMA)
    model = active["artifact_binding"]
    semantics = receipt.get("effective_C0_semantics") or {}
    before = adoption._projection(semantics.get("original_projection"))
    after = adoption._projection(semantics.get("current_projection"))
    effective = adoption.projection_digest(after)
    if not (before == after and semantics.get("equal") is True
            and adoption.projection_digest(before) == effective
            and semantics.get("original_projection_sha256") == semantics.get("current_projection_sha256") == effective
            and active.get("effective_C0_semantics_projection_sha256")
                == active.get("original_effective_C0_semantics_projection_sha256") == effective):
        drift.note("effective_semantics_changed", recorded=adoption.projection_digest(before), actual=effective)
    for name, projection in (("original_projection_ref", before), ("current_projection_ref", after)):
        actual, reference = adoption.read_content_ref(semantics.get(name), expected_schema=adoption.PROJECTION_SCHEMA)
        _require(actual == projection and reference["size"] == len(adoption.projection_bytes(projection))
            and reference["sha256"] == adoption.projection_digest(projection),
            "application_active_projection_reference_noncanonical_codec")
    if not (pointer.get("contract_adoption_ref") == active.get("contract_adoption_ref")
            and pointer.get("artifact_sha256") == model["sha256"] == receipt.get("stable_base_model_version")):
        drift.note("model_pointer_drift", recorded=receipt.get("stable_base_model_version"),
            actual={"pointer_artifact_sha256": pointer.get("artifact_sha256"), "model_sha256": model["sha256"]})
    expected = {"requirements_ref": active["current_policy_binding"]["requirements_raw_sha256"],
        "daily_requirements_ref": active["current_policy_binding"]["policy_sha256"],
        "application_capture_requirements_ref": active["current_policy_binding"]["application_capture_binding"]["raw_sha256"]}
    _require(isinstance(current_contract_refs, Mapping) and set(current_contract_refs) == set(expected),
        "application_active_current_contract_inventory_invalid")
    contract_refs = {}
    for name, digest in expected.items():
        _, ref = adoption.read_content_ref(current_contract_refs[name], mutable=True, drift=drift)
        if ref["sha256"] != digest:
            drift.note("policy_version_mismatch", recorded=digest, actual=ref["sha256"], detail={"field": name})
        contract_refs[name] = ref
    _require(isinstance(consumer_activations, list) and bool(consumer_activations),
        "application_active_consumer_evidence_missing")
    consumers, seen = [], set()
    for item in consumer_activations:
        _require(isinstance(item, Mapping) and item.get("profile") == "C0"
            and item.get("purpose") in {"current_analysis", "current_inference"}, "application_active_consumer_invalid")
        identity = (item.get("consumer_id"), item.get("entry_id"))
        _require(all(isinstance(value, str) and value for value in identity) and identity not in seen,
            "application_active_consumer_duplicate_or_missing_identity")
        activation, activation_ref = adoption.read_content_ref(item.get("activation_ref"), expected_schema=ACTIVATION_SCHEMA)
        _require(activation.get("schema_version") == 1 and activation.get("consumer_id") == identity[0]
            and activation.get("entry_id") == identity[1] and activation.get("profile") == "C0",
            "application_active_source_activation_receipt_mismatch")
        if not (activation.get("activation_status") == "source_configured" and activation.get("runtime_status") == "not_asserted"
                and activation.get("model_sha256") == model["sha256"]):
            drift.note("activation_receipt_differs", recorded=activation.get("model_sha256"), actual=model["sha256"],
                detail={"consumer_id": identity[0], "entry_id": identity[1],
                        "activation_status": activation.get("activation_status")})
        files = item.get("source_files")
        _require(isinstance(files, list) and bool(files), "application_active_source_files_not_sealed_by_activation")
        if files != activation.get("source_files"):
            drift.note("source_list_drift", detail={"consumer_id": identity[0], "entry_id": identity[1]})
        actual_files = []
        for source in files:
            # The consumer's actual deployed sha is recorded; it never blocks publication.
            actual = _actual_file_ref(source, drift, "consumer_source_missing")
            if actual is not None:
                actual_files.append(actual)
        if (item.get("source_sha256") != activation.get("source_sha256")
                or item.get("source_sha256") not in {source["sha256"] for source in actual_files}):
            drift.note("consumer_source_drift", recorded=item.get("source_sha256"),
                actual=[source["sha256"] for source in actual_files],
                detail={"consumer_id": identity[0], "entry_id": identity[1]})
        _, current_pointer_ref = adoption.read_content_ref(item.get("current_pointer_ref"), mutable=True, drift=drift)
        if current_pointer_ref != pointer_ref:
            drift.note("model_pointer_drift", recorded=(item.get("current_pointer_ref") or {}).get("sha256"),
                actual=pointer_ref.get("sha256"), detail={"consumer_id": identity[0], "entry_id": identity[1]})
        consumers.append({"consumer_id": identity[0], "entry_id": identity[1], "profile": "C0",
            "purpose": item["purpose"], "activation_ref": activation_ref, "current_pointer_ref": current_pointer_ref,
            "source_sha256": item.get("source_sha256"), "source_files": actual_files})
        seen.add(identity)
    result = {"schema": SCHEMA, "schema_version": 1, "revision": revision,
        "verified_effective_c0_semantics_sha256": effective,
        "purpose_version": "current-only-c0-20261009.v1", "purpose": "current_application_capture_scope",
        "current_contract_refs": contract_refs,
        "model_binding": {"pointer_ref": pointer_ref, "adoption_ref": adoption_ref,
            "model_sha256": model["sha256"], "feature_schema": model["feature_schema"], "profile_id": "C0"},
        "active_consumers": sorted(consumers, key=lambda value: (value["consumer_id"], value["entry_id"]))}
    if drift.labels:
        result["version_labels"] = drift.labels
    return result


def source_activation_receipt(consumer_id, entry_id, source_files, active, *, entry_source_sha256):
    """Construct a configured-source receipt from deployed bytes, with no runtime-success claim.

    The receipt records the bytes as deployed now (no recorded-digest pin and no
    adoption gate); whether the entry is in the closure is visible from the list.
    """
    checked = []
    for ref in source_files:
        path = ref.get("path") if isinstance(ref, Mapping) else ref
        checked.append(adoption.content_ref(path))
    return {"schema": ACTIVATION_SCHEMA, "schema_version": 1, "consumer_id": consumer_id, "entry_id": entry_id,
        "profile": "C0", "activation_status": "source_configured", "runtime_status": "not_asserted",
        "model_sha256": active["artifact_binding"]["sha256"], "source_sha256": entry_source_sha256,
        "source_files": checked}
