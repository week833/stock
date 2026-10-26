"""Verify centrally published shared-market-data artifacts.

This verifier is deliberately receipt-authoritative.  It does not scan the
shared-data directory.  Only ``publish.artifacts`` declarations in the
current central receipt are read, and a passing result is atomically
published for the D-stock acceptance consumer.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import uuid
from typing import Any


CANONICAL_SHARED_ROOT = Path(r"D:\TWStockData\shared_market_data")
CANONICAL_RECEIPT_PATH = Path(
    r"D:\TWStockData\services\market_update\receipts\latest_manifest.json"
)
CANONICAL_REGISTRY_PATH = Path(
    r"D:\TWStockData\contracts\shared_market_data\requirements_v1.json"
)
REGISTRY_SCHEMA = "shared-market-data-requirements-v1"
DEFAULT_OUTPUT_PATH = Path(
    r"D:\TWStockData\program_data\dstock_daily\state\shared_market_data_control\verification_latest.json"
)
RECEIPT_SCHEMA = "shared-market-data-update-manifest-v1"
VERIFICATION_SCHEMA = "shared-data-central-receipt-verification-v1"
SCHEMA_VERSION = 1
ROUTE_TOTAL = 48
REQUIRED_ROUTE_TOTAL = 40
OPTIONAL_ROUTE_TOTAL = 8
CENTRAL_PRODUCER_OWNER = "market_update"
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
GLOB_CHARS = frozenset("*?[]")
STREAM_CHUNK_SIZE = 1024 * 1024
_WIN32_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


class VerificationFailure(RuntimeError):
    """A bounded, fail-closed verification error."""

    def __init__(self, code: str, *, field: str = "") -> None:
        self.code = code
        self.field = field
        super().__init__(code)


def _absolute_without_resolve(path: Path) -> Path:
    """Normalize a path lexically without following links or reparse points."""

    try:
        return Path(os.path.abspath(os.fspath(path)))
    except (OSError, TypeError, ValueError) as exc:
        raise VerificationFailure("path_invalid", field=str(path)) from exc


def _ensure_no_follow(path: Path, *, missing_code: str, field: str | None = None) -> None:
    """Reject symlink/reparse traversal before any content is consumed."""

    try:
        observed = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise VerificationFailure(missing_code, field=field or str(path)) from exc
    if stat.S_ISLNK(observed.st_mode) or (
        int(getattr(observed, "st_file_attributes", 0))
        & _WIN32_FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise VerificationFailure("source_reparse_or_symlink", field=field or str(path))


def _ensure_no_follow_components(root: Path, candidate: Path) -> None:
    """Check the root and every lexical component of a target path."""

    root_absolute = _absolute_without_resolve(root)
    candidate_absolute = _absolute_without_resolve(candidate)
    try:
        relative_parts = candidate_absolute.relative_to(root_absolute).parts
    except ValueError as exc:
        raise VerificationFailure("path_outside_root", field=str(candidate)) from exc
    _ensure_no_follow(root_absolute, missing_code="shared_root_missing")
    current = root_absolute
    for part in relative_parts:
        current = current / part
        _ensure_no_follow(current, missing_code="source_missing")


def _identity_from_stat(stat: os.stat_result) -> tuple[int, int, int, int]:
    return (
        int(getattr(stat, "st_dev", 0)),
        int(getattr(stat, "st_ino", 0)),
        int(stat.st_size),
        int(stat.st_mtime_ns),
    )


def _identity(path: Path) -> tuple[int, int, int, int]:
    try:
        _ensure_no_follow(path, missing_code="source_missing")
        return _identity_from_stat(os.stat(path, follow_symlinks=False))
    except OSError as exc:
        raise VerificationFailure("source_missing", field=str(path)) from exc


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise VerificationFailure("receipt_canonical_json_invalid") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value.strip()) is not None


def _read_stable_bytes(path: Path) -> tuple[bytes, tuple[int, int, int, int]]:
    before = _identity(path)
    try:
        with path.open("rb") as handle:
            raw = handle.read()
            handle_identity = _identity_from_stat(os.fstat(handle.fileno()))
    except (OSError, UnicodeError) as exc:
        raise VerificationFailure("source_unreadable", field=str(path)) from exc
    after = _identity(path)
    if before != after or before != handle_identity or after[2] != len(raw):
        raise VerificationFailure("source_changed_during_read", field=str(path))
    return raw, after


def _registry_path_for_receipt(receipt_path: Path, registry_path: Path | None) -> Path:
    if _absolute_without_resolve(receipt_path) == _absolute_without_resolve(CANONICAL_RECEIPT_PATH):
        return CANONICAL_REGISTRY_PATH
    if registry_path is not None:
        return registry_path
    sibling = receipt_path.parent / CANONICAL_REGISTRY_PATH.name
    if not sibling.is_file():
        raise VerificationFailure("registry_path_missing", field=str(sibling))
    return sibling


def _require_text(value: object, *, code: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VerificationFailure(code, field=field)
    return value.strip()


def _safe_relative_declaration(value: object, *, field: str) -> str:
    text = _require_text(value, code="relative_path_invalid", field=field)
    if "\\" in text or ":" in text or any(char in text for char in GLOB_CHARS):
        raise VerificationFailure("relative_path_invalid", field=field)
    posix = PurePosixPath(text)
    if posix.is_absolute() or any(part in {"", ".", ".."} for part in posix.parts):
        raise VerificationFailure("relative_path_invalid", field=field)
    return "/".join(posix.parts)


def _expected_sidecar_path(payload_path: str) -> str:
    if not payload_path.endswith(".json"):
        raise VerificationFailure("registry_sidecar_path_invalid", field=payload_path)
    return f"{payload_path[:-5]}.manifest.json"


def select_explicit_route_ids(route_ids: object, requested_route_ids: object) -> tuple[str, ...]:
    """Validate a consumer's explicit route selection without changing the union."""

    if not isinstance(route_ids, (list, tuple)) or not isinstance(requested_route_ids, (list, tuple)):
        raise VerificationFailure("explicit_route_ids_invalid", field="route_ids")
    universe = tuple(_require_text(value, code="explicit_route_id_invalid", field="route_ids") for value in route_ids)
    if len(set(universe)) != len(universe):
        raise VerificationFailure("explicit_route_ids_duplicate", field="route_ids")
    selected: list[str] = []
    universe_set = set(universe)
    for value in requested_route_ids:
        route_id = _require_text(value, code="explicit_route_id_invalid", field="requested_route_ids")
        if route_id not in universe_set:
            raise VerificationFailure("explicit_route_id_unknown", field=route_id)
        if route_id in selected:
            raise VerificationFailure("explicit_route_ids_duplicate", field=route_id)
        selected.append(route_id)
    if not selected:
        raise VerificationFailure("explicit_route_ids_empty", field="requested_route_ids")
    return tuple(selected)


def _load_registry(path: Path) -> dict[str, Any]:
    try:
        raw, _ = _read_stable_bytes(path)
    except VerificationFailure as exc:
        code = {
            "source_missing": "registry_missing",
            "source_unreadable": "registry_unreadable",
            "source_changed_during_read": "registry_changed_during_read",
        }.get(exc.code, "registry_unavailable")
        raise VerificationFailure(code, field=str(path)) from exc
    registry_raw_sha = _sha256_bytes(raw)
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationFailure("registry_parse_error", field=str(path)) from exc
    if not isinstance(value, Mapping):
        raise VerificationFailure("registry_schema_invalid", field="registry")
    if value.get("schema") != REGISTRY_SCHEMA:
        raise VerificationFailure("registry_schema_invalid", field="schema")
    if value.get("schema_version") != 1 or isinstance(value.get("schema_version"), bool):
        raise VerificationFailure("registry_schema_version_invalid", field="schema_version")
    requirements = value.get("shared_raw_requirements")
    if not isinstance(requirements, list) or len(requirements) != ROUTE_TOTAL:
        raise VerificationFailure("registry_route_count_invalid", field="shared_raw_requirements")
    if not requirements:
        raise VerificationFailure("registry_requirements_invalid", field="shared_raw_requirements")
    route_ids: list[str] = []
    required_ids: list[str] = []
    optional_ids: list[str] = []
    route_requirements: dict[str, Mapping[str, Any]] = {}
    for requirement in requirements:
        if not isinstance(requirement, Mapping) or not isinstance(requirement.get("id"), str):
            raise VerificationFailure("registry_route_invalid", field="shared_raw_requirements")
        route_id = requirement["id"].strip()
        if not route_id:
            raise VerificationFailure("registry_route_id_invalid", field="shared_raw_requirements")
        if route_id in route_ids:
            raise VerificationFailure("registry_duplicate_route_id", field=route_id)
        required = requirement.get("required")
        if not isinstance(required, bool):
            raise VerificationFailure("registry_required_flag_invalid", field=route_id)
        if requirement.get("classification") != "shared_raw":
            raise VerificationFailure("registry_classification_invalid", field=route_id)
        _require_text(requirement.get("dataset"), code="registry_dataset_invalid", field=route_id)
        _require_text(requirement.get("provider"), code="registry_provider_invalid", field=route_id)
        target_paths = requirement.get("target_relative_paths")
        if not isinstance(target_paths, list) or not target_paths:
            raise VerificationFailure("registry_target_paths_invalid", field=route_id)
        for target_path in target_paths:
            _safe_relative_declaration(target_path, field=f"{route_id}.target_relative_paths")
        adapter = requirement.get("adapter")
        if not isinstance(adapter, Mapping):
            raise VerificationFailure("registry_adapter_invalid", field=route_id)
        if adapter.get("owner") != CENTRAL_PRODUCER_OWNER:
            raise VerificationFailure("registry_adapter_owner_invalid", field=route_id)
        _require_text(adapter.get("command"), code="registry_adapter_command_invalid", field=route_id)
        route_ids.append(route_id)
        route_requirements[route_id] = requirement
        (required_ids if required else optional_ids).append(route_id)
    if len(required_ids) != REQUIRED_ROUTE_TOTAL or len(optional_ids) != OPTIONAL_ROUTE_TOTAL:
        raise VerificationFailure("registry_route_counts_invalid", field="shared_raw_requirements")
    declared_route_order = value.get("route_order")
    if declared_route_order is not None:
        if not isinstance(declared_route_order, list) or declared_route_order != route_ids:
            raise VerificationFailure("registry_route_order_mismatch", field="route_order")
    contracts = value.get("route_contracts")
    if not isinstance(contracts, Mapping) or set(contracts) != set(required_ids):
        raise VerificationFailure("registry_route_contracts_mismatch", field="route_contracts")
    if len(contracts) != REQUIRED_ROUTE_TOTAL:
        raise VerificationFailure("registry_route_contracts_count_invalid", field="route_contracts")
    normalized_contracts: dict[str, Mapping[str, Any]] = {}
    contract_targets: set[str] = set()
    contract_sidecars: set[str] = set()
    for route_id in required_ids:
        contract = contracts.get(route_id)
        if not isinstance(contract, Mapping) or contract.get("required") is not True:
            raise VerificationFailure("registry_route_contract_invalid", field=route_id)
        requirement = route_requirements[route_id]
        if contract.get("classification") != "shared_raw":
            raise VerificationFailure("registry_route_contract_invalid", field=route_id)
        if contract.get("dataset") != requirement.get("dataset") or contract.get("provider") != requirement.get("provider"):
            raise VerificationFailure("registry_route_contract_identity_mismatch", field=route_id)
        producer = contract.get("producer")
        if not isinstance(producer, Mapping):
            raise VerificationFailure("registry_producer_invalid", field=route_id)
        producer_id = _require_text(producer.get("id"), code="registry_producer_id_invalid", field=route_id)
        if producer.get("unique") is not True or producer.get("central_owner") != CENTRAL_PRODUCER_OWNER:
            raise VerificationFailure("registry_producer_owner_invalid", field=route_id)
        if producer.get("status") not in {"available", "ready"} or producer.get("execute") is not True:
            raise VerificationFailure("registry_producer_status_invalid", field=route_id)
        adapter_command = requirement["adapter"].get("command")
        if producer_id != adapter_command:
            raise VerificationFailure("registry_producer_binding_invalid", field=route_id)
        publication = contract.get("publication")
        if not isinstance(publication, Mapping):
            raise VerificationFailure("registry_publication_invalid", field=route_id)
        target = _safe_relative_declaration(
            publication.get("target_relative_path"),
            field=f"{route_id}.publication.target_relative_path",
        )
        sidecar = _safe_relative_declaration(
            publication.get("manifest_relative_path"),
            field=f"{route_id}.publication.manifest_relative_path",
        )
        if sidecar != _expected_sidecar_path(target) or target in contract_targets or sidecar in contract_sidecars:
            raise VerificationFailure("registry_publication_target_duplicate", field=route_id)
        contract_targets.add(target)
        contract_sidecars.add(sidecar)
        for key in ("update", "pit", "coverage"):
            if not isinstance(contract.get(key), Mapping):
                raise VerificationFailure("registry_route_contract_invalid", field=f"{route_id}.{key}")
        coverage_contract = contract["coverage"]
        if coverage_contract.get("missing_unit_behavior") != "fail_closed":
            raise VerificationFailure("registry_coverage_policy_invalid", field=route_id)
        fields = contract.get("fields")
        if not isinstance(fields, list) or not fields:
            raise VerificationFailure("registry_fields_invalid", field=route_id)
        normalized_contracts[route_id] = contract
    audit = value.get("route_contract_audit")
    if not isinstance(audit, Mapping):
        raise VerificationFailure("registry_route_audit_invalid", field="route_contract_audit")
    if audit.get("required_route_count") != REQUIRED_ROUTE_TOTAL or audit.get("route_contract_count") != REQUIRED_ROUTE_TOTAL:
        raise VerificationFailure("registry_route_audit_mismatch", field="route_contract_audit.count")
    for field, expected in (("required_route_ids", required_ids), ("optional_route_ids", optional_ids)):
        declared = audit.get(field)
        if not isinstance(declared, list) or declared != expected:
            raise VerificationFailure("registry_route_audit_mismatch", field=f"route_contract_audit.{field}")
    return {
        "path": path,
        "raw_sha256": registry_raw_sha,
        "canonical_sha256": _sha256_bytes(_canonical_json(value)),
        "route_ids": tuple(route_ids),
        "required_route_ids": tuple(required_ids),
        "optional_route_ids": tuple(optional_ids),
        "route_order_sha256": _sha256_bytes(_canonical_json(route_ids)),
        "route_requirements": route_requirements,
        "route_contracts": normalized_contracts,
    }


def _read_stable_hashed(path: Path) -> tuple[int, str]:
    before = _identity(path)
    digest = hashlib.sha256()
    byte_count = 0
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                byte_count += len(chunk)
                digest.update(chunk)
            handle_identity = _identity_from_stat(os.fstat(handle.fileno()))
    except (OSError, UnicodeError) as exc:
        raise VerificationFailure("artifact_unreadable", field=str(path)) from exc
    after = _identity(path)
    if before != after or before != handle_identity or after[2] != byte_count:
        raise VerificationFailure("artifact_changed_during_read", field=str(path))
    return byte_count, digest.hexdigest()


def _declared_artifact_target(raw_target: object, *, field: str) -> str:
    """Normalize one receipt-declared artifact target, naming traversal explicitly."""

    try:
        return _safe_relative_declaration(raw_target, field=field)
    except VerificationFailure as exc:
        if exc.code == "relative_path_invalid":
            if isinstance(raw_target, str) and ".." in PurePosixPath(raw_target.replace("\\", "/")).parts:
                raise VerificationFailure("artifact_target_path_escape", field=exc.field) from exc
            raise VerificationFailure("artifact_target_invalid", field=exc.field) from exc
        raise


def _safe_target(root: Path, raw_target: object) -> tuple[str, Path]:
    target = _declared_artifact_target(raw_target, field="target_relative_path")
    posix = PurePosixPath(target)
    try:
        root_absolute = _absolute_without_resolve(root)
        candidate = root_absolute.joinpath(*posix.parts)
        _ensure_no_follow_components(root_absolute, candidate)
    except (OSError, RuntimeError, ValueError) as exc:
        raise VerificationFailure("shared_root_invalid", field="canonical_root") from exc
    if not candidate.is_file():
        raise VerificationFailure("artifact_missing", field="target_relative_path")
    return "/".join(posix.parts), candidate


def _require_date(value: object, *, field: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    try:
        parsed = date.fromisoformat(text)
    except (TypeError, ValueError) as exc:
        raise VerificationFailure("date_invalid", field=field) from exc
    if parsed.isoformat() != text:
        raise VerificationFailure("date_invalid", field=field)
    return text


def _artifact_list_hash(declarations: list[Mapping[str, Any]]) -> str:
    entries = [
        {
            "staging_relative_path": item.get("staging_relative_path"),
            "target_relative_path": item.get("target_relative_path"),
            "content_sha256": item.get("content_sha256"),
            "size": item.get("size"),
        }
        for item in declarations
    ]
    return _sha256_bytes(_canonical_json(entries))


def _validate_registry_bound_receipt(
    receipt: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> dict[str, Any]:
    route_ids = tuple(str(value) for value in registry["route_ids"])
    required_ids = tuple(str(value) for value in registry["required_route_ids"])
    optional_ids = tuple(str(value) for value in registry["optional_route_ids"])
    route_set = set(route_ids)
    expected_asof = _require_date(receipt.get("expected_asof"), field="expected_asof")
    declared_canonical = receipt.get("registry_canonical_sha256")
    declared_registry = receipt.get("registry_sha256")
    declared_raw = receipt.get("registry_raw_sha256")
    if not _valid_sha256(declared_registry) or not _valid_sha256(declared_canonical):
        raise VerificationFailure("receipt_registry_canonical_sha_missing", field="registry_canonical_sha256")
    if not _valid_sha256(declared_raw):
        raise VerificationFailure("receipt_registry_raw_sha_missing", field="registry_raw_sha256")
    if str(declared_registry).casefold() != str(registry["canonical_sha256"]).casefold():
        raise VerificationFailure("receipt_registry_sha_mismatch", field="registry_sha256")
    if str(declared_canonical).casefold() != str(registry["canonical_sha256"]).casefold():
        raise VerificationFailure("receipt_registry_canonical_sha_mismatch", field="registry_canonical_sha256")
    if str(declared_raw).casefold() != str(registry["raw_sha256"]).casefold():
        raise VerificationFailure("receipt_registry_raw_sha_mismatch", field="registry_raw_sha256")

    datasets = receipt.get("datasets")
    if not isinstance(datasets, list) or len(datasets) != len(route_ids):
        raise VerificationFailure("receipt_routes_mismatch", field="datasets")

    def dataset_route_id(value: object) -> str:
        if not isinstance(value, Mapping):
            return ""
        candidate = value.get("dataset_id") or value.get("route_id") or value.get("id")
        return candidate.strip() if isinstance(candidate, str) else ""

    dataset_ids: list[str] = []
    datasets_by_id: dict[str, Mapping[str, Any]] = {}
    for dataset in datasets:
        dataset_id = dataset_route_id(dataset)
        if not dataset_id:
            raise VerificationFailure("receipt_dataset_identity_invalid", field="datasets")
        if dataset_id in dataset_ids:
            raise VerificationFailure("receipt_duplicate_route_id", field=dataset_id)
        if not isinstance(dataset, Mapping) or not isinstance(dataset.get("required"), bool):
            raise VerificationFailure("receipt_dataset_required_flag_invalid", field=dataset_id)
        if dataset.get("required") is not (dataset_id in set(required_ids)):
            raise VerificationFailure("receipt_dataset_required_flag_mismatch", field=dataset_id)
        for key in ("id", "route_id", "dataset_id"):
            value = dataset.get(key)
            if value is not None and (not isinstance(value, str) or value.strip() != dataset_id):
                raise VerificationFailure("receipt_dataset_identity_alias", field=f"datasets.{dataset_id}.{key}")
        dataset_ids.append(dataset_id)
        datasets_by_id[dataset_id] = dataset
    if tuple(dataset_ids) != route_ids:
        raise VerificationFailure("receipt_route_order_mismatch", field="datasets")

    route_registry = receipt.get("route_registry")
    if not isinstance(route_registry, Mapping):
        raise VerificationFailure("receipt_route_registry_invalid", field="route_registry")
    route_registry_sha = route_registry.get("sha256")
    if not _valid_sha256(route_registry_sha):
        raise VerificationFailure("receipt_route_registry_hash_missing", field="route_registry.sha256")
    if str(route_registry_sha).casefold() != str(registry["canonical_sha256"]).casefold():
        raise VerificationFailure("receipt_route_registry_hash_mismatch", field="route_registry.sha256")
    route_entries = route_registry.get("routes")
    if not isinstance(route_entries, list):
        raise VerificationFailure("receipt_route_registry_routes_missing", field="route_registry.routes")
    declared_total = route_registry.get("total", route_registry.get("route_total", route_registry.get("count")))
    if isinstance(declared_total, bool) or not isinstance(declared_total, int) or declared_total != ROUTE_TOTAL:
        raise VerificationFailure("receipt_route_registry_total_mismatch", field="route_registry.total")
    route_entry_ids: list[str] = []
    for route in route_entries:
        route_id = dataset_route_id(route)
        if not route_id or route_id in route_entry_ids:
            raise VerificationFailure("receipt_duplicate_route_id", field="route_registry.routes")
        if not isinstance(route, Mapping) or route.get("required") is not (route_id in set(required_ids)):
            raise VerificationFailure("receipt_route_required_flag_mismatch", field=route_id)
        route_entry_ids.append(route_id)
    if tuple(route_entry_ids) != route_ids:
        raise VerificationFailure("receipt_route_order_mismatch", field="route_registry.routes")
    declared_order_sha = receipt.get("route_order_sha256", route_registry.get("route_order_sha256"))
    if not _valid_sha256(declared_order_sha):
        raise VerificationFailure("receipt_route_order_hash_missing", field="route_order_sha256")
    if str(declared_order_sha).casefold() != str(registry["route_order_sha256"]).casefold():
        raise VerificationFailure("receipt_route_order_hash_mismatch", field="route_order_sha256")

    coverage = receipt.get("coverage")
    if not isinstance(coverage, Mapping):
        raise VerificationFailure("receipt_coverage_invalid", field="coverage")
    required_total = coverage.get("required_total", coverage.get("required_datasets"))
    verified_total = coverage.get("required_complete", coverage.get("verified_datasets"))
    optional_total = coverage.get("optional_total", coverage.get("optional_datasets"))
    optional_verified = coverage.get("optional_verified", coverage.get("optional_verified_datasets"))
    if (
        isinstance(required_total, bool)
        or not isinstance(required_total, int)
        or required_total != REQUIRED_ROUTE_TOTAL
        or isinstance(verified_total, bool)
        or not isinstance(verified_total, int)
        or verified_total != REQUIRED_ROUTE_TOTAL
        or isinstance(optional_total, bool)
        or not isinstance(optional_total, int)
        or optional_total != OPTIONAL_ROUTE_TOTAL
        or isinstance(optional_verified, bool)
        or not isinstance(optional_verified, int)
        or not 0 <= optional_verified <= OPTIONAL_ROUTE_TOTAL
        or coverage.get("all_verified") is not True
    ):
        raise VerificationFailure("receipt_coverage_mismatch", field="coverage")

    route_requirements = registry["route_requirements"]
    route_contracts = registry["route_contracts"]
    for route_id in route_ids:
        dataset = datasets_by_id[route_id]
        requirement = route_requirements[route_id]
        expected_dataset = requirement.get("dataset")
        if dataset.get("dataset") != expected_dataset:
            raise VerificationFailure("receipt_dataset_name_mismatch", field=route_id)
        if dataset.get("provider") != requirement.get("provider"):
            raise VerificationFailure("receipt_provider_mismatch", field=route_id)
        if dataset.get("expected_asof") != expected_asof:
            raise VerificationFailure("receipt_dataset_expected_asof_mismatch", field=route_id)
        if dataset.get("run_id") != receipt.get("run_id"):
            raise VerificationFailure("receipt_dataset_run_mismatch", field=route_id)
        adapter = dataset.get("adapter")
        contract = route_contracts.get(route_id)
        producer = contract.get("producer") if isinstance(contract, Mapping) else None
        if not isinstance(adapter, Mapping) or adapter.get("owner") != CENTRAL_PRODUCER_OWNER:
            raise VerificationFailure("receipt_producer_owner_mismatch", field=route_id)
        if route_id in required_ids:
            expected_command = producer.get("id") if isinstance(producer, Mapping) else None
        else:
            # Only required routes carry a route contract; an optional route is
            # bound to the adapter declared by its registry requirement.
            expected_command = requirement["adapter"].get("command")
        if not expected_command or adapter.get("command") != expected_command:
            raise VerificationFailure("receipt_producer_binding_mismatch", field=route_id)
        for field, requirement_key in (
            ("schema_policy", "schema_policy"),
            ("freshness_policy", "freshness_policy"),
            ("pit_policy", "pit_policy"),
            ("coverage_policy", "coverage_policy"),
            ("hash_policy", "hash_policy"),
        ):
            if dataset.get(field) != requirement.get(requirement_key):
                raise VerificationFailure("receipt_policy_mismatch", field=f"{route_id}.{field}")
        coverage_value = dataset.get("coverage")
        if (
            not isinstance(coverage_value, Mapping)
            or coverage_value.get("ok") is not True
            or coverage_value.get("status") != "complete"
            or isinstance(coverage_value.get("denominator"), bool)
            or not isinstance(coverage_value.get("denominator"), int)
            or coverage_value.get("denominator") <= 0
            or isinstance(coverage_value.get("observed"), bool)
            or not isinstance(coverage_value.get("observed"), int)
            or coverage_value.get("observed") < coverage_value.get("denominator")
        ):
            raise VerificationFailure("receipt_dataset_coverage_invalid", field=route_id)
        pit_value = dataset.get("pit")
        if not isinstance(pit_value, Mapping) or pit_value.get("ok") is not True or pit_value.get("eligible") is not True:
            raise VerificationFailure("receipt_dataset_pit_invalid", field=route_id)
        if pit_value.get("expected_asof") is not None and pit_value.get("expected_asof") != expected_asof:
            raise VerificationFailure("receipt_dataset_pit_asof_mismatch", field=route_id)
        freshness_value = dataset.get("freshness")
        if not isinstance(freshness_value, Mapping) or freshness_value.get("ok") is not True:
            raise VerificationFailure("receipt_dataset_freshness_invalid", field=route_id)
        hash_value = dataset.get("hash")
        if (
            not isinstance(hash_value, Mapping)
            or hash_value.get("algorithm") != "sha256"
            or hash_value.get("ok") is not True
            or not _valid_sha256(hash_value.get("value"))
        ):
            raise VerificationFailure("receipt_dataset_hash_invalid", field=route_id)
        lineage = dataset.get("lineage")
        if not isinstance(lineage, Mapping):
            raise VerificationFailure("receipt_dataset_lineage_missing", field=route_id)
        if lineage.get("run_id") != receipt.get("run_id") or lineage.get("expected_asof") != expected_asof:
            raise VerificationFailure("receipt_dataset_lineage_mismatch", field=route_id)
        if (
            not _valid_sha256(lineage.get("registry_raw_sha256"))
            or not _valid_sha256(lineage.get("registry_canonical_sha256"))
            or str(lineage.get("registry_raw_sha256")).casefold() != str(registry["raw_sha256"]).casefold()
            or str(lineage.get("registry_canonical_sha256")).casefold() != str(registry["canonical_sha256"]).casefold()
        ):
            raise VerificationFailure("receipt_dataset_lineage_registry_mismatch", field=route_id)
        if dataset.get("required"):
            publish = dataset.get("publish")
            if not isinstance(publish, Mapping) or publish.get("status") != "committed":
                raise VerificationFailure("required_dataset_publish_missing", field=route_id)
            declarations = publish.get("artifacts")
            if not isinstance(declarations, list) or not declarations:
                raise VerificationFailure("required_dataset_artifacts_missing", field=route_id)
            declared_targets = {
                _declared_artifact_target(item.get("target_relative_path"), field=f"{route_id}.publish.artifacts")
                for item in declarations
                if isinstance(item, Mapping)
            }
            publication = contract.get("publication") if isinstance(contract, Mapping) else None
            expected_targets = {
                publication.get("target_relative_path"),
                publication.get("manifest_relative_path"),
            } if isinstance(publication, Mapping) else set()
            expected_targets = {
                _safe_relative_declaration(target, field=f"{route_id}.publication")
                for target in expected_targets
            }
            if not expected_targets.issubset(declared_targets):
                raise VerificationFailure("required_dataset_artifacts_incomplete", field=route_id)
    return {
        "registry_raw_sha256": str(registry["raw_sha256"]).casefold(),
        "registry_canonical_sha256": str(registry["canonical_sha256"]).casefold(),
        "registry_sha256": str(registry["canonical_sha256"]).casefold(),
        "route_ids": list(route_ids),
        "route_total": ROUTE_TOTAL,
        "required_total": REQUIRED_ROUTE_TOTAL,
        "optional_total": OPTIONAL_ROUTE_TOTAL,
        "required_route_ids": list(required_ids),
        "optional_route_ids": list(optional_ids),
        "route_order_sha256": str(registry["route_order_sha256"]).casefold(),
    }


def _receipt_payload(receipt_path: Path) -> tuple[Mapping[str, Any], str, str, str]:
    raw, _ = _read_stable_bytes(receipt_path)
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationFailure("receipt_json_invalid") from exc
    if not isinstance(value, Mapping):
        raise VerificationFailure("receipt_schema_invalid", field="receipt")
    schema = value.get("schema")
    if not isinstance(schema, Mapping) or schema.get("manifest") != RECEIPT_SCHEMA:
        raise VerificationFailure("receipt_schema_invalid", field="schema.manifest")
    schema_version = value.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != 1:
        raise VerificationFailure("receipt_schema_version_invalid", field="schema_version")
    if value.get("status") != "complete":
        raise VerificationFailure("receipt_not_complete", field="status")
    if value.get("publishable") is not True:
        raise VerificationFailure("receipt_not_publishable", field="publishable")
    if value.get("partial") is not False:
        raise VerificationFailure("receipt_partial", field="partial")
    if value.get("fail_closed") is not False:
        raise VerificationFailure("receipt_fail_closed", field="fail_closed")
    run_id = value.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise VerificationFailure("receipt_run_id_missing", field="run_id")
    expected_asof = _require_date(value.get("expected_asof"), field="expected_asof")
    declared_manifest_sha = value.get("manifest_content_sha256")
    if not _valid_sha256(declared_manifest_sha):
        raise VerificationFailure("receipt_manifest_hash_missing", field="manifest_content_sha256")
    without_hash = dict(value)
    without_hash.pop("manifest_content_sha256", None)
    if _sha256_bytes(_canonical_json(without_hash)) != str(declared_manifest_sha).casefold():
        raise VerificationFailure("receipt_manifest_hash_mismatch", field="manifest_content_sha256")
    datasets = value.get("datasets")
    if not isinstance(datasets, list):
        raise VerificationFailure("receipt_datasets_invalid", field="datasets")
    return value, _sha256_bytes(raw), run_id.strip(), expected_asof


def _verify(
    *,
    shared_root: Path,
    receipt_path: Path,
    registry_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    receipt, receipt_sha, run_id, expected_asof = _receipt_payload(receipt_path)
    registry = _load_registry(registry_path)
    registry_binding = _validate_registry_bound_receipt(receipt, registry)
    required_route_ids = set(registry["required_route_ids"])
    route_contracts = registry["route_contracts"]
    datasets = receipt["datasets"]
    seen_targets: set[str] = set()
    checked_files = 0
    checked_bytes = 0
    unpublished_datasets = 0

    for dataset in datasets:
        if not isinstance(dataset, Mapping):
            raise VerificationFailure("dataset_record_invalid", field="datasets")
        dataset_id = dataset.get("dataset_id") or dataset.get("route_id") or dataset.get("id")
        dataset_name = dataset.get("dataset") or dataset_id
        if not isinstance(dataset_id, str) or not dataset_id.strip() or not isinstance(dataset_name, str) or not dataset_name.strip():
            raise VerificationFailure("dataset_identity_invalid", field="dataset")
        if dataset.get("expected_asof") != expected_asof:
            raise VerificationFailure("dataset_expected_asof_mismatch", field="expected_asof")
        publish = dataset.get("publish")
        if publish is None:
            # The central receipt may explicitly report optional/conditional
            # datasets without a committed publish section.  There is no
            # artifact to inspect; never infer a path or scan the root.
            if dataset_id in required_route_ids:
                raise VerificationFailure("required_dataset_publish_missing", field="publish")
            if dataset.get("status") not in {"partial", "skipped_conditional"} or dataset.get("publishable") is True:
                raise VerificationFailure("dataset_publish_missing", field="publish")
            unpublished_datasets += 1
            continue
        if not isinstance(publish, Mapping):
            raise VerificationFailure("dataset_publish_invalid", field="publish")
        if dataset.get("status") != "complete" or dataset.get("publishable") is not True:
            raise VerificationFailure("dataset_not_publishable", field="publishable")
        if publish.get("status") != "committed":
            raise VerificationFailure("dataset_publish_invalid", field="publish.status")
        declarations = publish.get("artifacts")
        if not isinstance(declarations, list) or not declarations:
            raise VerificationFailure("dataset_artifacts_invalid", field="publish.artifacts")
        artifact_count = publish.get("artifact_count")
        if isinstance(artifact_count, bool) or not isinstance(artifact_count, int) or artifact_count != len(declarations):
            raise VerificationFailure("dataset_artifact_count_mismatch", field="publish.artifact_count")
        declared_target_paths = publish.get("target_relative_paths")
        if not isinstance(declared_target_paths, list):
            raise VerificationFailure("dataset_target_paths_missing", field="publish.target_relative_paths")
        normalized_target_paths = [
            _safe_relative_declaration(value, field="publish.target_relative_paths")
            for value in declared_target_paths
        ]
        normalized: list[Mapping[str, Any]] = []
        artifact_observations: dict[str, tuple[int, str]] = {}
        for declaration in declarations:
            if not isinstance(declaration, Mapping):
                raise VerificationFailure("artifact_declaration_invalid", field="publish.artifacts")
            _safe_relative_declaration(
                declaration.get("staging_relative_path"),
                field="publish.artifacts.staging_relative_path",
            )
            target, candidate = _safe_target(shared_root, declaration.get("target_relative_path"))
            if target in seen_targets:
                raise VerificationFailure("artifact_target_duplicate", field="target_relative_path")
            seen_targets.add(target)
            declared_size = declaration.get("size")
            if isinstance(declared_size, bool) or not isinstance(declared_size, int) or declared_size < 0:
                raise VerificationFailure("artifact_size_invalid", field="size")
            declared_sha = declaration.get("content_sha256")
            if not _valid_sha256(declared_sha):
                raise VerificationFailure("artifact_hash_invalid", field="content_sha256")
            normalized.append(declaration)
            actual_size, actual_sha = _read_stable_hashed(candidate)
            if actual_size != declared_size:
                raise VerificationFailure("artifact_size_mismatch", field="size")
            if actual_sha.casefold() != str(declared_sha).casefold():
                raise VerificationFailure("artifact_hash_mismatch", field="content_sha256")
            artifact_observations[target] = (actual_size, actual_sha.casefold())
            checked_files += 1
            checked_bytes += actual_size
        if set(normalized_target_paths) != set(artifact_observations) or len(normalized_target_paths) != len(artifact_observations):
            raise VerificationFailure("dataset_target_paths_mismatch", field="publish.target_relative_paths")
        declared_list_sha = publish.get("artifact_list_sha256")
        if not _valid_sha256(declared_list_sha):
            raise VerificationFailure("artifact_list_hash_missing", field="publish.artifact_list_sha256")
        if _artifact_list_hash(normalized).casefold() != str(declared_list_sha).casefold():
            raise VerificationFailure("artifact_list_hash_mismatch", field="publish.artifact_list_sha256")
        if dataset_id in required_route_ids:
            contract = route_contracts[dataset_id]
            publication = contract["publication"]
            payload_target = _safe_relative_declaration(
                publication["target_relative_path"],
                field=f"{dataset_name}.publication.target_relative_path",
            )
            sidecar_target = _safe_relative_declaration(
                publication["manifest_relative_path"],
                field=f"{dataset_name}.publication.manifest_relative_path",
            )
            payload_observation = artifact_observations.get(payload_target)
            sidecar_observation = artifact_observations.get(sidecar_target)
            if payload_observation is None or sidecar_observation is None:
                raise VerificationFailure("required_dataset_artifacts_incomplete", field=dataset_id)
            dataset_hash = dataset["hash"]["value"]
            if str(dataset_hash).casefold() != payload_observation[1]:
                raise VerificationFailure("dataset_payload_hash_mismatch", field=dataset_id)
            lineage = dataset["lineage"]
            if lineage.get("content_sha256") is not None and str(lineage["content_sha256"]).casefold() != payload_observation[1]:
                raise VerificationFailure("dataset_lineage_content_hash_mismatch", field=dataset_id)
            sidecar_size, sidecar_hash = sidecar_observation
            if sidecar_size <= 0 or not _valid_sha256(sidecar_hash):
                raise VerificationFailure("dataset_sidecar_invalid", field=dataset_id)

    if checked_files <= 0:
        raise VerificationFailure("no_published_artifacts", field="datasets")

    return {
        "schema": VERIFICATION_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "status": "pass",
        "canonical_root": str(shared_root),
        "central_receipt_path": str(receipt_path),
        "central_receipt_sha256": receipt_sha,
        "central_receipt_run_id": run_id,
        "registry_path": str(registry_path),
        "registry_sha256": registry_binding["registry_sha256"],
        "registry_raw_sha256": registry_binding["registry_raw_sha256"],
        "registry_canonical_sha256": registry_binding["registry_canonical_sha256"],
        "registry": {
            "route_total": registry_binding["route_total"],
            "required_total": registry_binding["required_total"],
            "optional_total": registry_binding["optional_total"],
            "route_ids": registry_binding["route_ids"],
            "required_route_ids": registry_binding["required_route_ids"],
            "optional_route_ids": registry_binding["optional_route_ids"],
            "route_order_sha256": registry_binding["route_order_sha256"],
            "registry_raw_sha256": registry_binding["registry_raw_sha256"],
            "registry_canonical_sha256": registry_binding["registry_canonical_sha256"],
            "producer_owner": CENTRAL_PRODUCER_OWNER,
        },
        "expected_asof": expected_asof,
        "receipt_schema": RECEIPT_SCHEMA,
        "receipt_manifest_content_sha256": str(receipt["manifest_content_sha256"]).casefold(),
        "hash_algorithm": "SHA256",
        "issues": [],
        "totals": {
            "checked_files": checked_files,
            "checked_bytes": checked_bytes,
            "declared_artifacts": checked_files,
            "datasets": len(datasets),
            "datasets_without_published_artifacts": unpublished_datasets,
        },
        "lineage": {
            "source": "central_receipt",
            "receipt_sha256": receipt_sha,
            "run_id": run_id,
            "expected_asof": expected_asof,
            "registry_raw_sha256": registry_binding["registry_raw_sha256"],
            "registry_canonical_sha256": registry_binding["registry_canonical_sha256"],
        },
        "mode": "formal",
        "verified_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8")
        with temporary.open("wb") as handle:
            handle.write(encoded)
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def verify_central_receipt(
    *,
    shared_root: Path | str = CANONICAL_SHARED_ROOT,
    receipt_path: Path | str = CANONICAL_RECEIPT_PATH,
    registry_path: Path | str | None = None,
    output_path: Path | str = DEFAULT_OUTPUT_PATH,
) -> dict[str, Any]:
    """Verify the current central receipt and publish only a passing result."""

    root = Path(shared_root)
    receipt = Path(receipt_path)
    registry = _registry_path_for_receipt(receipt, Path(registry_path) if registry_path is not None else None)
    output = Path(output_path)
    try:
        result = _verify(
            shared_root=root,
            receipt_path=receipt,
            registry_path=registry,
            output_path=output,
        )
        _atomic_write(output, result)
        return result
    except VerificationFailure as exc:
        return {
            "schema": VERIFICATION_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "status": "fail",
            "mode": "formal",
            "canonical_root": str(root),
            "central_receipt_path": str(receipt),
            "registry_path": str(registry),
            "hash_algorithm": "SHA256",
            "issues": [{"code": exc.code, "field": exc.field}],
            "totals": {"checked_files": 0},
        }
    except (OSError, RuntimeError, ValueError):
        return {
            "schema": VERIFICATION_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "status": "fail",
            "mode": "formal",
            "canonical_root": str(root),
            "central_receipt_path": str(receipt),
            "registry_path": str(registry),
            "hash_algorithm": "SHA256",
            "issues": [{"code": "verification_io_failed", "field": "verification"}],
            "totals": {"checked_files": 0},
        }


def main(argv: list[str] | None = None) -> int:
    del argv  # The production contract intentionally has no path overrides.
    result = verify_central_receipt()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
