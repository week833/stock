#!/usr/bin/env python3
"""Fail-closed resolver for the central D-stock path contract.

The contract file is the only source of managed data roots.  This module does
not create directories, migrate data, read project-local path registries, or
load secrets.
"""

from __future__ import annotations

import json
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


CONTRACT_PATH = Path(r"D:\TWStockData\config\path_contract.json")
EXPECTED_ROOT = Path(r"D:\TWStockData")
EXPECTED_CONTRACT_VERSION = "2.0.0"
# Known schema versions load silently.  Any other X.Y.Z whose major version is
# supported (1.x legacy, 2.x current) is accepted with a version warning: the
# file version is a configuration schema label, not a data/model pin, and the
# structural checks below (root, managed paths, write root) still apply.  The
# same rule is mirrored in telegram_report_sender.py,
# CHECK_COMMAND_BOT_STATUS.ps1 and INSTALL_COMMAND_BOT_SERVICE.ps1.
_KNOWN_CONTRACT_VERSIONS = frozenset({"1.1.0", EXPECTED_CONTRACT_VERSION})
_SUPPORTED_CONTRACT_VERSIONS = _KNOWN_CONTRACT_VERSIONS  # backward-compatible name
SUPPORTED_CONTRACT_MAJOR_VERSIONS = frozenset({1, 2})
_CONTRACT_VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
EXPECTED_LIVE_MITAKE_ACCOUNT = Path(r"D:\MitakeGU")
REQUIRED_MANAGED_PATHS = (
    "canonical_shared",
    "dstock_daily",
    "dstock_single",
    "mitake_index_evidence",
)


class PathContractError(RuntimeError):
    """Raised when the central path contract is absent or invalid."""


class PathContractVersionWarning(UserWarning):
    """Emitted when the contract version is not a known one but its major is supported."""


def contract_version_labels(contract_version: str) -> tuple[dict[str, Any], ...]:
    """Classify a contract version.

    Returns ``()`` for a known version, one ``path_contract_version_unknown``
    label for an unknown version with a supported major, and raises
    :class:`PathContractError` for an unparsable version or unsupported major.
    """
    version = str(contract_version or "").strip()
    if version in _KNOWN_CONTRACT_VERSIONS:
        return ()
    match = _CONTRACT_VERSION_PATTERN.fullmatch(version)
    if match is None or int(match.group(1)) not in SUPPORTED_CONTRACT_MAJOR_VERSIONS:
        raise PathContractError(
            "path contract version must be "
            f"{'.x or '.join(str(major) for major in sorted(SUPPORTED_CONTRACT_MAJOR_VERSIONS))}.x "
            f"(known: {', '.join(sorted(_KNOWN_CONTRACT_VERSIONS))}): "
            f"{version or '<missing>'}"
        )
    return (
        {
            "code": "path_contract_version_unknown",
            "recorded": sorted(_KNOWN_CONTRACT_VERSIONS),
            "actual": version,
            "detail": "accepted because major version is supported; structural checks still apply",
        },
    )


def _same_path(left: Path, right: Path) -> bool:
    return str(left).casefold() == str(right).casefold()


def _absolute_under(path: Path, root: Path, label: str) -> Path:
    if not path.is_absolute():
        raise PathContractError(f"{label} must be an absolute path: {path}")
    resolved = path.resolve(strict=False)
    root_resolved = root.resolve(strict=False)
    if not resolved.is_relative_to(root_resolved):
        raise PathContractError(f"{label} is outside contract root: {path}")
    return resolved


def _safe_child(base: Path, parts: tuple[str, ...]) -> Path:
    for part in parts:
        candidate = Path(part)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise PathContractError(
                f"contract child path must stay below its namespace: {part}"
            )
    resolved = (base.joinpath(*parts)).resolve(strict=False)
    if not resolved.is_relative_to(base.resolve(strict=False)):
        raise PathContractError("contract child path escaped its namespace")
    return resolved


@dataclass(frozen=True)
class PathContract:
    contract_id: str
    contract_version: str
    root: Path
    canonical_shared: Path
    dstock_daily: Path
    dstock_single: Path
    mitake_index_evidence: Path
    live_mitake_account: Path
    version_labels: tuple[dict[str, Any], ...] = ()

    def namespace(self, name: str, *parts: str) -> Path:
        roots = {
            "canonical_shared": self.canonical_shared,
            "dstock_daily": self.dstock_daily,
            "dstock_single": self.dstock_single,
            "mitake_index_evidence": self.mitake_index_evidence,
        }
        try:
            base = roots[name]
        except KeyError as exc:
            raise PathContractError(f"unknown managed namespace: {name}") from exc
        return _safe_child(base, tuple(str(part) for part in parts))

    def shared(self, *parts: str) -> Path:
        return self.namespace("canonical_shared", *parts)

    def daily(self, *parts: str) -> Path:
        return self.namespace("dstock_daily", *parts)

    def single(self, *parts: str) -> Path:
        return self.namespace("dstock_single", *parts)

    def mitake_evidence(self, *parts: str) -> Path:
        return self.namespace("mitake_index_evidence", *parts)


def _read_payload(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise PathContractError(f"path contract is missing: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PathContractError(f"path contract cannot be read: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PathContractError("path contract must be a JSON object")
    return payload


def load_path_contract(path: Path = CONTRACT_PATH) -> PathContract:
    payload = _read_payload(Path(path))
    root_value = str(payload.get("root") or "").strip()
    if not root_value or not _same_path(Path(root_value), EXPECTED_ROOT):
        raise PathContractError(
            f"path contract root must be {EXPECTED_ROOT}: {root_value or '<missing>'}"
        )

    managed = payload.get("managed_paths")
    if not isinstance(managed, dict):
        raise PathContractError("path contract managed_paths must be an object")
    root = EXPECTED_ROOT.resolve(strict=False)
    values: dict[str, Path] = {}
    for name in REQUIRED_MANAGED_PATHS:
        value = str(managed.get(name) or "").strip()
        if not value:
            raise PathContractError(f"path contract missing managed path: {name}")
        values[name] = _absolute_under(Path(value), root, name)

    policies = payload.get("policies")
    if not isinstance(policies, dict):
        raise PathContractError("path contract policies must be an object")
    write_root = str(policies.get("write_root") or "").strip()
    if not write_root or not _same_path(Path(write_root), EXPECTED_ROOT):
        raise PathContractError("path contract policies.write_root must equal root")

    external = payload.get("external_paths")
    if not isinstance(external, dict):
        raise PathContractError("path contract external_paths must be an object")
    live_mitake = str(external.get("live_mitake_account") or "").strip()
    if not live_mitake or not Path(live_mitake).is_absolute():
        raise PathContractError("path contract live_mitake_account must be absolute")
    if not _same_path(Path(live_mitake), EXPECTED_LIVE_MITAKE_ACCOUNT):
        raise PathContractError(
            f"path contract live_mitake_account must be {EXPECTED_LIVE_MITAKE_ACCOUNT}"
        )

    contract_id = str(payload.get("contract_id") or "").strip()
    contract_version = str(payload.get("contract_version") or "").strip()
    if not contract_id or not contract_version:
        raise PathContractError("path contract id and version are required")
    version_labels = contract_version_labels(contract_version)
    if version_labels:
        warnings.warn(
            f"path contract version {contract_version} is not a known version "
            f"({', '.join(sorted(_KNOWN_CONTRACT_VERSIONS))}); accepted by major version",
            PathContractVersionWarning,
            stacklevel=2,
        )

    return PathContract(
        contract_id=contract_id,
        contract_version=contract_version,
        root=root,
        canonical_shared=values["canonical_shared"],
        dstock_daily=values["dstock_daily"],
        dstock_single=values["dstock_single"],
        mitake_index_evidence=values["mitake_index_evidence"],
        live_mitake_account=Path(live_mitake).resolve(strict=False),
        version_labels=version_labels,
    )


CONTRACT = load_path_contract()


__all__ = [
    "CONTRACT",
    "CONTRACT_PATH",
    "EXPECTED_CONTRACT_VERSION",
    "EXPECTED_ROOT",
    "EXPECTED_LIVE_MITAKE_ACCOUNT",
    "PathContract",
    "PathContractError",
    "PathContractVersionWarning",
    "SUPPORTED_CONTRACT_MAJOR_VERSIONS",
    "contract_version_labels",
    "load_path_contract",
]
