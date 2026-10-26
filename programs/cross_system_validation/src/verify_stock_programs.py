#!/usr/bin/env python3
"""Read-only cross-system acceptance for the current (C0) stock workflow.

The verifier never starts an application, calls a provider or Telegram,
registers/changes a scheduled task, or repairs a file.  The only write is the
single acceptance snapshot in ``--reports`` mode; ``--structure-only`` and the
``--central-mix-live`` alias build the same payload and write nothing.

Active components (``RETAINED_COMPONENT_IDS``) follow the current-only C0
architecture:

* ``central_publication``: requirements registry v3 (C0 six routes) and the
  published head ``shared_market_data\\_metadata\\stock_data_current.json``
  with its immutable ``_manifests\\<sha>.json`` copy and exact ``_objects``.
* ``workflow_scheduler``: the MIX weekday 08:00 Windows Task, queried read-only
  and compared with ``MIX_WEEKDAY_0800_TASK_DEFINITION.json``.
* ``mix_analysis_report``: ``LATEST_C0_RUN.json`` -> manifest -> refs.
* ``mix_mitake_persistence``: not applicable to C0 (label, never a pass).
* ``single_stock_analysis_report``: on-demand C0 reports under ``reports\\c0``.
* ``telegram_service_publication``: command-bot identity plus the
  ``mix_report_delivery_state_v1`` written by ``mix_report_sender.py``.
* ``data_retention_guard`` / ``retirement_isolation``: read-only direct
  observation (retired files absent, no current reference or task pointing at
  them, data roots present, Central head intact, quarantine allowlist is
  programs only).  The legacy evidence files have no producer and are
  validated only when present.
* ``schedule_reconciliation``: retired task names must not be enabled.

The former Central -> MIX v1 -> Mitake "central-mix-live" path (48-route
receipt, publication receipt, target_date, Mitake receipt) had no caller and
was removed on 2026-10-12; see README.
"""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import ntpath
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable, Mapping, Sequence


SCHEMA = "cross_system_acceptance_v2"
DEFAULT_OUTPUT = Path(r"D:\TWStockData\services\acceptance\latest.json")

# MIX current report pointer, written by D:\MIX All\src\mixstock\core_daily.py
# and read by telegram_report_delivery\mix_report_sender.py.  The retired
# LATEST_REPORT_RUN.json / LATEST_PRODUCTION_RUN.json pointers are never read
# (not even as a fallback).
MIX_C0_NAMESPACE_ROOT = Path(r"D:\TWStockData\program_data\mix")
MIX_C0_REPORTS_ROOT = MIX_C0_NAMESPACE_ROOT / "reports"
MIX_C0_POINTER_NAME = "LATEST_C0_RUN.json"
MIX_C0_RUNTIME_POINTER_NAME = "LATEST_C0_RUN_BY_RUNTIME.json"
MIX_C0_POINTER_SCHEMA = "mix-stock-daily-c0-current-report-v1"
MIX_C0_MANIFEST_SCHEMA = "mix-stock-daily-c0-manifest-v1"
MIX_C0_PAYLOAD_SCHEMA = "mix-stock-daily-c0-report-v1"
MIX_C0_STATUS = "limited_baseline_effect_unverified"
MIX_C0_HORIZONS = (1, 5, 20, 60)
MIX_C0_RUN_ID_RE = re.compile(r"c0_[0-9]{8}T[0-9]{12}Z_[0-9a-f]{10}")
MIX_C0_ARTIFACT_NAMES = {
    "payload": "report_payload.json",
    "html": "report.html",
    "input_sources": "published_inputs.json",
    "excel": "MIX_C0.xlsx",
}
MIX_C0_ADOPTION_FIELDS = (
    "original_model_feature_contract",
    "current_policy_binding",
    "contract_adoption_ref",
    "effective_C0_semantics_projection_sha256",
    "model_activation_epoch",
)
# Telegram delivery of the MIX C0 report (mix_report_sender.deliver_mix_bundle).
MIX_C0_DELIVERY_STATE_SCHEMA = "mix_report_delivery_state_v1"
MIX_DELIVERY_STATE_PATH = Path(r"D:\TWStockData\services\telegram\data\mix_report_delivery_state.json")

# Central = current-only C0 publication (requirements v3, six routes).  The
# routes are read from the registry; the known set is only used for a label.
CENTRAL_REQUIREMENTS_REGISTRY_PATH = Path(
    r"D:\TWStockData\contracts\shared_market_data\requirements_v1.json"
)
CENTRAL_REQUIREMENTS_REGISTRY_SCHEMA = "shared-market-data-requirements-v3"
CENTRAL_REQUIREMENTS_REGISTRY_SCHEMA_VERSION = 3
CENTRAL_PUBLICATION_ROOT = Path(r"D:\TWStockData\shared_market_data")
CENTRAL_HEAD_RELATIVE = "_metadata/stock_data_current.json"
CENTRAL_ARCHIVE_RELATIVE = "_manifests"
CENTRAL_OBJECTS_RELATIVE = "_objects"
CENTRAL_HEAD_SCHEMA = "stock-data-publication-v1"
CENTRAL_PROFILE_ID = "C0"
CENTRAL_C0_ROUTE_IDS = (
    "official_calendar",
    "official_market_daily",
    "finmind_stock_info",
    "finmind_price_adj",
    "finmind_dividend",
    "finmind_dividend_result",
)
CENTRAL_MARKET_ASOF_ROUTE = "official_market_daily"
# v1/v2 Central evidence that no current producer writes; listed, never read.
RETIRED_CENTRAL_EVIDENCE = (
    r"D:\TWStockData\services\market_update\receipts\latest_manifest.json",
    r"D:\TWStockData\services\market_update\source_seal.json",
    r"D:\TWStockData\tools\market_update_scheduler\CENTRAL_SOURCE_EXPECTED_MANIFEST.json",
)

# v2 is deliberately component based.  These identifiers are the only
# required acceptance consumers (ids are kept stable across the C0 alignment).
RETAINED_COMPONENT_IDS = (
    "central_publication",
    "workflow_scheduler",
    "mix_analysis_report",
    "mix_mitake_persistence",
    "single_stock_analysis_report",
    "telegram_service_publication",
    "data_retention_guard",
    "retirement_isolation",
    "schedule_reconciliation",
)
RETAINED_MODE = "retained-components"
RETIRED_CONSUMER_IDS = (
    "full_market_analysis",
    "cowork",
    "twmix",
    "premarket_daytrade_top10",
    "daily_analysis_scheduler",
)
RETAINED_FRESHNESS_HOURS = 48.0
RETAINED_ACCEPTANCE_EVIDENCE_ROOT = Path(r"D:\TWStockData\services\acceptance")
RETAINED_RETENTION_EVIDENCE = RETAINED_ACCEPTANCE_EVIDENCE_ROOT / "data_retention_guard.json"
RETAINED_RETIREMENT_EVIDENCE = RETAINED_ACCEPTANCE_EVIDENCE_ROOT / "retirement_isolation.json"

# Governance by read-only direct observation.  The two legacy evidence files
# above have no current producer (program_quarantine.py apply writes a
# different schema below migration_snapshots and requires a typed
# confirmation), so they are validated only when present.  The constants
# below mirror D:\TWStockData\services\acceptance\program_quarantine.py; the
# tests parse that file (AST, never imported) and compare, so they cannot
# drift apart silently.
QUARANTINE_PROGRAM_PATH = RETAINED_ACCEPTANCE_EVIDENCE_ROOT / "program_quarantine.py"
QUARANTINE_ALLOWLIST_PATH = RETAINED_ACCEPTANCE_EVIDENCE_ROOT / "program_quarantine_allowlist_v1.json"
QUARANTINE_ALLOWLIST_SCHEMA = "retained-program-quarantine-allowlist-v1"
QUARANTINE_ALLOWLIST_ENTRY_FIELDS = frozenset({"entry_id", "source_root_id", "source_path", "relative_path", "category"})
# program_quarantine.ALLOWED_ROOT_IDS: the current (pre-retirement) location of each retired program root.
RETIRED_PROGRAM_ROOTS: dict[str, Path] = {
    "full_market_analysis": Path(r"D:\stock\programs\full_market_analysis"),
    "cowork": Path(r"D:\cowork"),
    "premarket_daytrade_top10": Path(r"D:\cowork\premarket_daytrade_top10"),
    "twmix": Path(r"D:\claude code\twmix"),
    "old_scheduler": Path(r"D:\stock\programs\daily_analysis_scheduler"),
}
# D:\cowork itself still hosts non-retired directories (logs, backups), so the
# root path is not a retired reference; only the retired files and their own
# sub-directories are.
RETIRED_SHARED_ROOT_IDS = frozenset({"cowork"})
# program_quarantine.ALLOWED_SUFFIXES
RETIRED_PROGRAM_SUFFIXES = (".py", ".cmd", ".bat", ".ps1", ".mjs")
# program_quarantine.PROTECTED_ROOTS (an allowlist entry may never be inside one).
QUARANTINE_PROTECTED_ROOTS = tuple(
    Path(item)
    for item in (
        r"D:\TWStockData\shared_market_data",
        r"D:\TWStockData\program_data",
        r"D:\TWStockData\services",
        r"D:\stock\programs\single_stock_analysis",
        r"D:\stock\programs\telegram_report_delivery",
        r"D:\MIX All",
        r"D:\stock\programs\shared",
        r"D:\stock\programs\shared_data_management",
        r"D:\stock\programs\environment_setup",
        r"D:\stock\programs\maintenance",
        r"D:\stock\programs\cross_system_validation",
        r"D:\stock\data",
        r"D:\stock\outputs",
        r"D:\stock\reports",
        r"D:\stock\logs",
        r"D:\stock\state",
        r"D:\stock\backup",
        r"D:\stock\GitHub",
        r"D:\cowork\data",
        r"D:\cowork\outputs",
        r"D:\cowork\logs",
        r"D:\cowork\state",
        r"D:\cowork\staging",
        r"D:\cowork\audit",
        r"D:\cowork\assets",
        r"D:\claude code\twmix\data",
        r"D:\claude code\twmix\outputs",
        r"D:\claude code\twmix\backup",
    )
)
# Data part of PROTECTED_ROOTS.  The current data authorities must exist and be
# non-empty (blocking).  The legacy data roots of retired programs are only
# observed: their absence is a label because it can neither be attributed to
# nor excluded from the retirement without an event audit.  D:\stock\data is
# no longer a data authority (shared\price_adj_commit_rebuild.py treats it as
# a forbidden root) and is absent today.
RETAINED_DATA_AUTHORITY_ROOTS: dict[str, Path] = {
    "shared_market_data": CENTRAL_PUBLICATION_ROOT,
    "program_data": Path(r"D:\TWStockData\program_data"),
}
RETIRED_LEGACY_DATA_ROOTS: dict[str, Path] = {
    "stock_data": Path(r"D:\stock\data"),
    "cowork_data": Path(r"D:\cowork\data"),
    "twmix_data": Path(r"D:\claude code\twmix\data"),
}
# Reference scan of the current program trees (read-only, text + Python AST).
# blocking=True: code of a retained component -- any reference blocks.
# blocking=False: non-retained tooling / legacy trees -- references are labels.
# Not scanned: cross_system_validation (it owns the retired-name denylist).
REFERENCE_SCAN_SUFFIXES = (".py", ".pyw", ".cmd", ".bat", ".ps1", ".psm1", ".mjs", ".js")
REFERENCE_SCAN_EXCLUDED_DIR_NAMES = frozenset(
    {
        "tests",
        "test",
        "fixtures",
        "backup",
        "backups",
        "__pycache__",
        ".venv",
        "venv",
        "site-packages",
        "node_modules",
        ".git",
        ".pytest_cache",
        "external_entrypoints",
    }
)
REFERENCE_SCAN_EXCLUDED_FILE_RE = re.compile(r"(?i)^(?:test_.*|.*_test|conftest)\.pyw?$")
REFERENCE_SCAN_MAX_HITS_PER_FILE = 5
RETAINED_REFERENCE_SCAN_SCOPES: tuple[dict[str, object], ...] = (
    {"scope_id": "mix_src", "component_id": "mix_analysis_report", "path": Path(r"D:\MIX All\src"), "recursive": True, "blocking": True},
    {"scope_id": "mix_tools", "component_id": "mix_analysis_report", "path": Path(r"D:\MIX All\tools"), "recursive": True, "blocking": True},
    {"scope_id": "mix_entrypoints", "component_id": "mix_analysis_report", "path": Path(r"D:\MIX All"), "recursive": False, "blocking": True},
    {"scope_id": "single_stock_analysis", "component_id": "single_stock_analysis_report", "path": Path(r"D:\stock\programs\single_stock_analysis"), "recursive": True, "blocking": True},
    {"scope_id": "telegram_report_delivery", "component_id": "telegram_service_publication", "path": Path(r"D:\stock\programs\telegram_report_delivery"), "recursive": True, "blocking": True},
    {"scope_id": "shared", "component_id": "shared", "path": Path(r"D:\stock\programs\shared"), "recursive": True, "blocking": True},
    {"scope_id": "shared_data_management", "component_id": "shared", "path": Path(r"D:\stock\programs\shared_data_management"), "recursive": True, "blocking": True},
    {"scope_id": "central_market_update", "component_id": "central_publication", "path": Path(r"D:\TWStockData\services\market_update"), "recursive": True, "blocking": True},
    {"scope_id": "central_control", "component_id": "central_publication", "path": Path(r"D:\TWStockData\services\shared_market_data_control"), "recursive": True, "blocking": True},
    {"scope_id": "stock_programs_entrypoints", "component_id": "tooling", "path": Path(r"D:\stock\programs"), "recursive": False, "blocking": False},
    {"scope_id": "maintenance", "component_id": "tooling", "path": Path(r"D:\stock\programs\maintenance"), "recursive": True, "blocking": False},
    {"scope_id": "environment_setup", "component_id": "tooling", "path": Path(r"D:\stock\programs\environment_setup"), "recursive": True, "blocking": False},
    {"scope_id": "central_external_entrypoints", "component_id": "legacy", "path": Path(r"D:\TWStockData\services\market_update\external_entrypoints"), "recursive": True, "blocking": False},
    {"scope_id": "daily_orchestration", "component_id": "legacy", "path": Path(r"D:\TWStockData\services\daily_orchestration"), "recursive": True, "blocking": False},
    {"scope_id": "workflow_scheduler_v1", "component_id": "legacy", "path": Path(r"D:\TWStockData\services\workflow_scheduler"), "recursive": True, "blocking": False},
    {"scope_id": "acceptance_tools", "component_id": "legacy", "path": RETAINED_ACCEPTANCE_EVIDENCE_ROOT, "recursive": True, "blocking": False},
)
REFERENCE_SCAN_NOT_SCANNED = (
    {"path": r"D:\stock\programs\cross_system_validation", "reason": "verifier_owns_retired_name_denylist"},
)
GOVERNANCE_OBSERVATION_BASIS = "read_only_current_state_observation_not_event_audit"
RETAINED_TELEGRAM_SOURCE = Path(
    r"D:\stock\programs\telegram_report_delivery\telegram_stock_command_bot.py"
)
RETAINED_TELEGRAM_OWNER = Path(
    r"D:\TWStockData\services\telegram\data\stock_command_bot.owner.json"
)
RETAINED_TELEGRAM_HEARTBEAT = Path(
    r"D:\TWStockData\services\telegram\data\stock_command_bot.heartbeat.json"
)
# Written only by the retired D-stock full-market sender; never read.
RETIRED_TELEGRAM_MESSAGE_RECEIPT = Path(
    r"D:\TWStockData\services\telegram\data\telegram_delivery_state.json"
)

# MIX weekday 08:00 Windows Task (telegram_report_delivery).  Queried read-only.
MIX_TASK_NAME = "MIX Telegram Report 08-00"
MIX_TASK_PATH = "\\"
MIX_TASK_SENDER_ROOT = Path(r"D:\stock\programs\telegram_report_delivery")
MIX_TASK_DEFINITION_PATH = MIX_TASK_SENDER_ROOT / "MIX_WEEKDAY_0800_TASK_DEFINITION.json"
MIX_TASK_DEFINITION_SCHEMA = "mix_telegram_weekday_schedule_definition_v1"
MIX_TASK_TIMEZONE = "Taipei Standard Time"
MIX_TASK_EXPECTED_ARGUMENTS = (
    '/d /c "set NO_PAUSE=1&&call D:\\stock\\programs\\telegram_report_delivery\\RUN_SEND_MIX_REPORT.cmd'
    ' --max-wait-seconds 0 --poll-seconds 60"'
)
MIX_TASK_EXPECTED_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
MIX_TASK_SOURCE_POINTER = str(MIX_C0_REPORTS_ROOT / MIX_C0_POINTER_NAME)
# Old v1/v2 task names.  None may be enabled; they target retired programs.
LEGACY_TASK_NAMES = (
    "TWStockData Central Market Update 03-00",
    "TWStockData Central Market Update 02-00",
    "Premarket Top10 Daily 08-00",
    "MIX ALL Daily Analysis",
    "cowork 每日預測",
    "twmix 每日全市場分析",
    "D-Stock and MIX Daily Analysis 07-00",
    "D-Stock Telegram Report 08-00",
)
TASK_XML_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"
POWERSHELL_EXE = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
TASK_QUERY_TIMEOUT_SECONDS = 120
# Workflow scheduler v1 final receipt (Central -> MIX v1 -> Mitake); never read.
RETIRED_WORKFLOW_FINAL_POINTER = Path(
    r"D:\TWStockData\services\scheduler\state\workflow_scheduler_v1\latest_final_receipt.json"
)
RETIRED_SCHEDULE_EVIDENCE = RETAINED_ACCEPTANCE_EVIDENCE_ROOT / "schedule_reconciliation.json"

# Single-stock C0 (tw_single_stock_c0.py): on-demand reports, no latest pointer.
SINGLE_C0_REPORTS_ROOT = Path(r"D:\TWStockData\program_data\dstock_single\reports\c0")
SINGLE_C0_REPORT_SCHEMA = "single-stock-daily-c0-estimate-v1"
SINGLE_C0_REPORT_NAME_RE = re.compile(r"c0_([0-9A-Za-z]{2,16})_h([0-9]{1,3})_([0-9]{8}T[0-9]{12})\.json")
SINGLE_C0_PDF_NAME_RE = re.compile(r"c0_([0-9A-Za-z]{2,16})_h([0-9]{1,3})_([0-9]{8}T[0-9]{12})\.pdf")
SINGLE_C0_HORIZONS = (1, 63)
SINGLE_C0_ESTIMATE_CLASS = "daily_estimate_effect_unknown"
SINGLE_C0_NO_ESTIMATE_CLASS = "c0_no_estimate_data_time_only"
SINGLE_C0_REPORT_CLASSES = (SINGLE_C0_ESTIMATE_CLASS, SINGLE_C0_NO_ESTIMATE_CLASS)
SINGLE_C0_TARGET_DEFINITION = "signal_close_to_same_market_H_session_close"
# The v3 latest_report_manifest.json is from the retired single-stock flow.
RETIRED_SINGLE_LATEST_MANIFEST = Path(
    r"D:\TWStockData\program_data\dstock_single\reports\latest_report_manifest.json"
)


def _safe_stat(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except (OSError, ValueError):
        return None


def _normalize_windows_path(value: object) -> str | None:
    """Normalize a Windows path without resolving it against this process cwd."""
    if not isinstance(value, str) or not value.strip():
        return None
    return ntpath.normpath(value.strip().replace("/", "\\"))


def _relative_to(path: Path, base: Path) -> bool:
    """Return whether *path* is a strict child of *base*.

    The production roots are Windows paths, but the verifier's fixture tests
    can run on a non-Windows host.  Select ``ntpath`` from the path syntax
    instead of the host OS so a Windows fixture retains Windows' case-
    insensitive drive and component semantics.  Native POSIX fixtures still
    use ``os.path``.  Keep the lexical checks here as well as at callers: a
    containment helper must not turn traversal, globs, a relative path, a
    different drive, or the root itself into a valid child path.
    """

    def looks_windows_path(value: str) -> bool:
        return bool(
            os.name == "nt"
            or re.match(r"^[A-Za-z]:[\\/]", value)
            or value.startswith((r"\\\\", "\\"))
        )

    try:
        path_text = os.fspath(path)
        base_text = os.fspath(base)
        if not isinstance(path_text, str) or not isinstance(base_text, str):
            return False

        windows_path = looks_windows_path(path_text)
        windows_base = looks_windows_path(base_text)
        if windows_path != windows_base:
            return False

        path_module = ntpath if windows_path else os.path
        separators = r"[\\/]+" if windows_path else r"/+"
        for value in (path_text, base_text):
            if any(char in value for char in "*?[]"):
                return False
            if any(part in {".", ".."} for part in re.split(separators, value)):
                return False
            if not path_module.isabs(value):
                return False

        canonical_path = path_module.normcase(path_module.abspath(path_text))
        canonical_base = path_module.normcase(path_module.abspath(base_text))
        if canonical_path == canonical_base:
            return False
        return path_module.commonpath([canonical_path, canonical_base]) == canonical_base
    except (OSError, TypeError, ValueError):
        return False


def _retained_stat_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    return (
        int(getattr(value, "st_dev", 0)),
        int(getattr(value, "st_ino", 0)),
        int(getattr(value, "st_size", -1)),
        int(getattr(value, "st_mtime_ns", 0)),
    )


def _retained_read_json(path: Path, label: str) -> tuple[Mapping[str, object] | None, dict[str, object]]:
    """Read one exact JSON artifact with a small TOCTOU identity check."""
    result: dict[str, object] = {"path": str(path), "status": "blocked", "blocking": True, "pass": False}
    before = _safe_stat(path)
    if before is None or not path.is_file():
        result["reason"] = f"{label}_missing"
        return None, result
    try:
        with path.open("rb") as handle:
            raw = handle.read()
            during = os.fstat(handle.fileno())
        after = path.stat()
    except (OSError, ValueError) as exc:
        result["reason"] = f"{label}_unreadable"
        result["error"] = type(exc).__name__
        return None, result
    before_id = _retained_stat_identity(before)
    during_id = _retained_stat_identity(during)
    after_id = _retained_stat_identity(after)
    if before_id != during_id or before_id != after_id or len(raw) != int(after.st_size):
        result["reason"] = f"{label}_changed_during_read"
        return None, result
    result.update(
        {
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "identity": {
                "device": before_id[0],
                "inode": before_id[1],
                "size": before_id[2],
                "mtime_ns": before_id[3],
            },
        }
    )
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        result["reason"] = f"{label}_invalid_json"
        result["error"] = type(exc).__name__
        return None, result
    if not isinstance(value, Mapping):
        result["reason"] = f"{label}_object_required"
        return None, result
    result.update({"status": "pass", "blocking": False, "pass": True})
    return value, result


def _retained_sha(value: object) -> str | None:
    text = str(value or "").strip().lower()
    return text if re.fullmatch(r"[0-9a-f]{64}", text) else None


def _retained_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _retained_safe_relative(value: object) -> str | None:
    text = str(value or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or ntpath.splitdrive(text)[0]:
        return None
    if any(token in text for token in ("*", "?", "[", "]")):
        return None
    parts = tuple(part for part in text.split("/") if part)
    if not parts or any(part in {".", ".."} for part in parts):
        return None
    return "/".join(parts)


def _retained_read_bytes(path: Path, label: str) -> tuple[bytes | None, dict[str, object]]:
    """Read one exact binary artifact and return immutable identity evidence."""
    result: dict[str, object] = {"path": str(path), "status": "blocked", "blocking": True, "pass": False}
    before = _safe_stat(path)
    if before is None or not path.is_file():
        result["reason"] = f"{label}_missing"
        return None, result
    try:
        with path.open("rb") as handle:
            raw = handle.read()
            during = os.fstat(handle.fileno())
        after = path.stat()
    except (OSError, ValueError) as exc:
        result["reason"] = f"{label}_unreadable"
        result["error"] = type(exc).__name__
        return None, result
    before_id = _retained_stat_identity(before)
    if before_id != _retained_stat_identity(during) or before_id != _retained_stat_identity(after) or len(raw) != int(after.st_size):
        result["reason"] = f"{label}_changed_during_read"
        return None, result
    digest = hashlib.sha256(raw).hexdigest()
    result.update(
        {
            "status": "pass",
            "blocking": False,
            "pass": True,
            "size": len(raw),
            "sha256": digest,
            "identity": {
                "device": before_id[0],
                "inode": before_id[1],
                "size": before_id[2],
                "mtime_ns": before_id[3],
            },
        }
    )
    return raw, result


def _retained_artifact_record(value: object, root: Path, label: str) -> tuple[dict[str, object] | None, dict[str, object]]:
    """Verify a receipt-declared artifact under one canonical namespace."""
    if isinstance(value, Mapping):
        path_value = value.get("path") or value.get("absolute_path")
        declared_sha = value.get("sha256") or value.get("content_sha256") or value.get("raw_sha256")
        declared_size = value.get("size")
    else:
        path_value = value
        declared_sha = None
        declared_size = None
    text = str(path_value or "").strip()
    path = Path(text) if text else Path()
    check: dict[str, object] = {"path": text or None, "status": "blocked", "blocking": True, "pass": False}
    if not text or not path.is_absolute() or any(token in text for token in ("*", "?", "[", "]")) or not _relative_to(path, root):
        check["reason"] = f"{label}_path_invalid"
        return None, check
    raw, read = _retained_read_bytes(path, label)
    check.update(read)
    if raw is None:
        check["reason"] = read.get("reason", f"{label}_unreadable")
        return None, check
    actual_sha = str(read.get("sha256") or "").lower()
    if declared_sha is not None and _retained_sha(declared_sha) != actual_sha:
        check.update({"status": "blocked", "blocking": True, "pass": False, "reason": f"{label}_hash_mismatch"})
        return None, check
    if declared_size is not None and (isinstance(declared_size, bool) or not isinstance(declared_size, int) or declared_size != len(raw)):
        check.update({"status": "blocked", "blocking": True, "pass": False, "reason": f"{label}_size_mismatch"})
        return None, check
    record = {"path": str(path), "sha256": actual_sha, "size": len(raw), "identity": read.get("identity")}
    return record, check


def _retained_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _retained_quarantine_record(value: object, label: str, root: Path) -> tuple[dict[str, object] | None, dict[str, object]]:
    if not isinstance(value, Mapping):
        return None, {"status": "blocked", "blocking": True, "pass": False, "reason": f"{label}_missing"}
    manifest_path = str(value.get("manifest_path") or value.get("path") or "").strip()
    record, check = _retained_artifact_record({"path": manifest_path, "sha256": value.get("sha256") or value.get("manifest_sha256"), "size": value.get("size")}, root, label)
    if record is None:
        return None, check
    if value.get("zero_touch") is not True or value.get("data_touched") is True or value.get("data_deleted") is True or value.get("data_moved") is True:
        check.update({"status": "blocked", "blocking": True, "pass": False, "reason": f"{label}_zero_touch_invalid"})
        return None, check
    return record, {"status": "pass", "blocking": False, "pass": True, **record}


def _mix_c0_strict_json(raw: bytes) -> object:
    """Decode JSON like mix_report_sender: duplicate keys and NaN/Infinity are tampering."""

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key: " + key)
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise ValueError("nonfinite JSON: " + value)

    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique, parse_constant=reject_constant)


def _mix_c0_read_json(path: Path, label: str) -> tuple[Mapping[str, object] | None, dict[str, object]]:
    raw, read = _retained_read_bytes(path, label)
    if raw is None:
        return None, read
    try:
        value = _mix_c0_strict_json(raw)
    except (UnicodeError, ValueError) as exc:
        read.update({"status": "blocked", "blocking": True, "pass": False, "reason": f"{label}_invalid_json", "error": type(exc).__name__})
        return None, read
    if not isinstance(value, Mapping):
        read.update({"status": "blocked", "blocking": True, "pass": False, "reason": f"{label}_object_required"})
        return None, read
    return value, read


def _mix_c0_same_path(left: object, right: object) -> bool:
    try:
        return os.path.normcase(os.path.abspath(os.fspath(left))) == os.path.normcase(os.path.abspath(os.fspath(right)))
    except (TypeError, ValueError):
        return False


def _mix_c0_ref(value: object, *, root: Path, expected_name: str, label: str) -> tuple[dict[str, object] | None, dict[str, object]]:
    """Verify one exact {path,size,sha256} ref of the current C0 run (KEEP: integrity/tamper)."""
    check: dict[str, object] = {"label": label, "status": "blocked", "blocking": True, "pass": False}
    if not isinstance(value, Mapping) or set(value) != {"path", "size", "sha256"}:
        check["reason"] = f"{label}_ref_invalid"
        return None, check
    text = value.get("path")
    if not isinstance(text, str) or not text.strip():
        check["reason"] = f"{label}_path_missing"
        return None, check
    check["path"] = text
    path = Path(text)
    if not path.is_absolute() or not _relative_to(path, root) or not _mix_c0_same_path(path, root / expected_name):
        check["reason"] = f"{label}_path_not_current_run_ref"
        return None, check
    size = value.get("size")
    sha = value.get("sha256")
    if type(size) is not int or size <= 0:
        check["reason"] = f"{label}_size_invalid"
        return None, check
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        check["reason"] = f"{label}_sha256_invalid"
        return None, check
    raw, read = _retained_read_bytes(path, label)
    if raw is None:
        check.update(read)
        check["reason"] = read.get("reason", f"{label}_unreadable")
        return None, check
    if len(raw) != size or read.get("sha256") != sha:
        check.update({"reason": f"{label}_size_or_sha256_mismatch", "declared_size": size, "actual_size": len(raw), "declared_sha256": sha, "actual_sha256": read.get("sha256")})
        return None, check
    record: dict[str, object] = {"path": str(path), "size": len(raw), "sha256": str(read.get("sha256")), "identity": read.get("identity")}
    return record, {"label": label, "status": "pass", "blocking": False, "pass": True, **record}


def _mix_c0_header_problem(document: Mapping[str, object], schema: str, label: str, *, run_id: str | None = None, asof: str | None = None) -> str | None:
    horizons = document.get("horizons")
    if document.get("schema") != schema:
        return f"{label}_schema_invalid"
    if document.get("profile") != "C0":
        return f"{label}_profile_invalid"
    if document.get("status") != MIX_C0_STATUS:
        return f"{label}_status_invalid"
    if not isinstance(horizons, list) or any(type(item) is not int for item in horizons) or horizons != list(MIX_C0_HORIZONS):
        return f"{label}_horizons_invalid"
    if run_id is not None and document.get("run_id") != run_id:
        return f"{label}_run_id_mismatch"
    if asof is not None and _mix_c0_day(document.get("as_of_date")) != asof:
        return f"{label}_as_of_date_mismatch"
    return None


def _mix_c0_day(value: object) -> str | None:
    """YYYYMMDD for a real calendar day written as YYYYMMDD or YYYY-MM-DD, else None."""
    text = str(value or "").strip()
    if re.fullmatch(r"\d{8}", text):
        candidate = text
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", text[:10]) and (len(text) == 10 or text[10] in "T "):
        candidate = text[:10].replace("-", "")
    else:
        return None
    try:
        datetime.strptime(candidate, "%Y%m%d")
    except ValueError:
        return None
    return candidate


def _mix_c0_present(document: object, field: str) -> bool:
    return isinstance(document, Mapping) and field in document and document[field] not in (None, "", {}, [])


def _mix_c0_warning(warnings: list[dict[str, object]], code: str, **detail: object) -> None:
    warnings.append({"code": code, "severity": "warning", "blocking": False, **detail})


def _mix_c0_binding_problem(manifest: Mapping[str, object], payload: Mapping[str, object], warnings: list[dict[str, object]]) -> str | None:
    """Same-run manifest/payload consistency is KEEP; a missing field is only a label."""
    for field in MIX_C0_ADOPTION_FIELDS:
        in_manifest, in_payload = _mix_c0_present(manifest, field), _mix_c0_present(payload, field)
        if in_manifest and in_payload and manifest[field] != payload[field]:
            return f"mix_c0_manifest_payload_{field}_mismatch"
        if not in_manifest or not in_payload:
            _mix_c0_warning(warnings, "mix_c0_report_field_missing", field=field, manifest_present=in_manifest, payload_present=in_payload)
    manifest_model = manifest.get("model") if isinstance(manifest.get("model"), Mapping) else {}
    payload_model = payload.get("model") if isinstance(payload.get("model"), Mapping) else {}
    for field in ("artifact_binding", "pointer_binding"):
        in_manifest, in_payload = _mix_c0_present(manifest_model, field), _mix_c0_present(payload_model, field)
        if in_manifest and in_payload and manifest_model[field] != payload_model[field]:
            return f"mix_c0_manifest_payload_model_{field}_mismatch"
        if not in_manifest or not in_payload:
            _mix_c0_warning(warnings, "mix_c0_report_field_missing", field="model." + field, manifest_present=in_manifest, payload_present=in_payload)
    requirements = payload.get("daily_requirements")
    if _mix_c0_present(manifest, "requirements_version") and _mix_c0_present(requirements, "requirements_version"):
        if manifest["requirements_version"] != requirements["requirements_version"]:  # type: ignore[index]
            return "mix_c0_manifest_payload_requirements_version_mismatch"
    if manifest.get("forward_binding") != payload.get("forward_binding"):
        return "mix_c0_manifest_payload_forward_binding_mismatch"
    forward = payload.get("forward_binding")
    if not isinstance(forward, Mapping) or not forward.get("base_model_version"):
        _mix_c0_warning(warnings, "mix_c0_report_field_missing", field="payload.forward_binding.base_model_version")
    for field in ("source_binding", "coverage", "runtime_evidence"):
        if not isinstance(payload.get(field), Mapping) or not payload.get(field):
            _mix_c0_warning(warnings, "mix_c0_report_field_missing", field="payload." + field)
    return None


def _mix_c0_prediction_problem(manifest: Mapping[str, object], payload: Mapping[str, object]) -> tuple[str | None, dict[str, object]]:
    """KEEP gates of mix_report_sender._verify_predictions, incl. ETF / non-ordinary exclusion.

    The validator has no current published identity, so a report without its
    own coverage.records classification evidence fails closed (ETF exclusion
    cannot be shown).
    """
    stats: dict[str, object] = {}
    records = payload.get("predictions")
    if not isinstance(records, list) or not records:
        return "mix_c0_predictions_missing", stats
    universe = payload.get("universe_policy")
    if (
        not isinstance(universe, Mapping)
        or universe.get("selection") not in {"current_verified_ordinary_twse_tpex", "explicit_current_ordinary_filter"}
        or universe.get("training_scope_is_inference_allowlist") is not False
    ):
        return "mix_c0_universe_policy_invalid", stats
    coverage = payload.get("coverage")
    coverage_records = coverage.get("records") if isinstance(coverage, Mapping) else None
    if not isinstance(coverage_records, list) or not coverage_records:
        return "mix_c0_coverage_records_missing_etf_exclusion_unverifiable", stats

    def pair_of(item: Mapping[str, object], *market_keys: str) -> tuple[str, str]:
        market = next((item.get(key) for key in market_keys if item.get(key)), "")
        return str(item.get("stock_id") or ""), str(market or "").upper()

    ordinary: set[tuple[str, str]] = set()
    non_ordinary: set[tuple[str, str]] = set()
    for item in coverage_records:
        if not isinstance(item, Mapping):
            return "mix_c0_coverage_record_invalid", stats
        pair = pair_of(item, "market", "board")
        if (
            item.get("is_ordinary_share") is True
            and item.get("security_type") == "ordinary_share"
            and item.get("current_ordinary_classification_verified") is not False
            and pair[1] in {"TWSE", "TPEX"}
            and pair[0]
        ):
            ordinary.add(pair)
        else:
            non_ordinary.add(pair)
    excluded = coverage.get("excluded") if isinstance(coverage, Mapping) and isinstance(coverage.get("excluded"), list) else []
    excluded_pairs = {pair_of(item, "market") for item in excluded if isinstance(item, Mapping)}
    seen: set[tuple[str, str, int]] = set()
    usable = 0
    for record in records:
        if not isinstance(record, Mapping):
            return "mix_c0_prediction_record_invalid", stats
        pair = pair_of(record, "market")
        horizon = record.get("horizon")
        if type(horizon) is not int or horizon not in MIX_C0_HORIZONS or not pair[0] or (*pair, horizon) in seen:
            return "mix_c0_prediction_pair_or_horizon_invalid", stats
        if pair in non_ordinary:
            stats["offending_pair"] = "/".join(pair)
            return "mix_c0_prediction_non_ordinary_or_etf_present", stats
        if pair not in ordinary and not (pair in excluded_pairs and record.get("prediction_status") != "predicted"):
            stats["offending_pair"] = "/".join(pair)
            return "mix_c0_prediction_outside_ordinary_coverage", stats
        seen.add((*pair, horizon))
        status = record.get("prediction_status")
        if not isinstance(status, str) or not status:
            return "mix_c0_prediction_status_missing", stats
        if status == "predicted":
            usable += 1
            for field in ("prob_up", "expected_return"):
                number = record.get(field)
                if type(number) not in (int, float) or number != number or number in (float("inf"), float("-inf")):
                    return "mix_c0_prediction_value_not_finite", stats
            if not 0 <= record["prob_up"] <= 1:  # type: ignore[operator]
                return "mix_c0_prediction_prob_up_out_of_range", stats
    if {item[2] for item in seen} != set(MIX_C0_HORIZONS):
        return "mix_c0_prediction_horizons_incomplete", stats
    for document, label in ((manifest, "manifest"), (payload, "payload")):
        if (
            type(document.get("prediction_count")) is not int
            or document.get("prediction_count") != len(records)
            or type(document.get("usable_prediction_count")) is not int
            or document.get("usable_prediction_count") != usable
        ):
            return f"mix_c0_{label}_prediction_counts_mismatch", stats
    if usable < 1:
        return "mix_c0_no_usable_prediction", stats
    for field in ("fit_performed", "backtests_performed", "stock_order_is_ranking"):
        if payload.get(field) is not False:
            return f"mix_c0_limitation_{field}_invalid", stats
    model = payload.get("model") if isinstance(payload.get("model"), Mapping) else {}
    if model.get("formal_model_qualified") is not False or manifest.get("formal_model_qualified") is not False:
        return "mix_c0_formal_model_qualified_claimed", stats
    if manifest.get("delivery_status") != "not_requested" or payload.get("delivery_status") != "not_requested":
        return "mix_c0_producer_delivery_status_invalid", stats
    stats.update(
        {
            "prediction_count": len(records),
            "usable_prediction_count": usable,
            "ordinary_pairs": len(ordinary),
            "non_ordinary_pairs_excluded": len(non_ordinary),
            "coverage_excluded_pairs": len(excluded_pairs),
            "etf_non_ordinary_excluded_verified": True,
        }
    )
    return None, stats


def _mix_c0_data_time(
    manifest: Mapping[str, object],
    payload: Mapping[str, object],
    *,
    asof: str,
    now: datetime,
    central_expected_asof: object,
    warnings: list[dict[str, object]],
    pointer: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Show the data time actually used; stale / unverified freshness is a warning, never a gate."""
    source = "payload.data_time" if isinstance(payload.get("data_time"), Mapping) else "manifest.data_time" if isinstance(manifest.get("data_time"), Mapping) else None
    data_time = payload.get("data_time") if source == "payload.data_time" else manifest.get("data_time") if source else {}
    data_time = data_time if isinstance(data_time, Mapping) else {}
    head = data_time.get("head") if isinstance(data_time.get("head"), Mapping) else {}
    # Same fallback order as mix_report_sender.load_mix_bundle.
    created_at = payload.get("created_at") or manifest.get("created_at") or (pointer or {}).get("created_at") or None
    summary: dict[str, object] = {
        "report_as_of_date": asof,
        "source": source,
        "created_at": created_at,
        "analysis_asof": data_time.get("analysis_asof"),
        "asof_by_market": data_time.get("asof_by_market"),
        "price_panel_max_date": data_time.get("price_panel_max_date"),
        "read_at_utc": data_time.get("read_at_utc"),
        "published_at_declared": head.get("published_at"),
        "freshness_status": data_time.get("freshness_status"),
        "is_stale": data_time.get("is_stale"),
        "stale_markets": data_time.get("stale_markets"),
        "calendar_verified_through": data_time.get("calendar_verified_through"),
        "is_older_than_requested": data_time.get("is_older_than_requested"),
    }
    if source is None:
        _mix_c0_warning(warnings, "mix_c0_data_time_missing", detail="報告未記錄 data_time；資料時間以 as_of_date 顯示", report_as_of_date=asof)
    else:
        freshness = data_time.get("freshness_status")
        if not freshness:
            _mix_c0_warning(warnings, "mix_c0_freshness_status_missing", source=source)
        elif freshness != "latest":
            _mix_c0_warning(warnings, "mix_c0_data_not_latest", freshness_status=freshness, stale_markets=data_time.get("stale_markets"), calendar_verified_through=data_time.get("calendar_verified_through"), report_as_of_date=asof)
        if data_time.get("is_older_than_requested") is True:
            _mix_c0_warning(warnings, "mix_c0_data_older_than_requested", report_as_of_date=asof)
    created = _retained_datetime(created_at)
    if created is None:
        _mix_c0_warning(warnings, "mix_c0_created_at_missing")
    else:
        age_hours = (now - created).total_seconds() / 3600.0
        summary["report_age_hours"] = round(age_hours, 2)
        if age_hours > RETAINED_FRESHNESS_HOURS:
            _mix_c0_warning(warnings, "mix_c0_report_older_than_window", report_age_hours=round(age_hours, 2), window_hours=RETAINED_FRESHNESS_HOURS, created_at=created_at)
    central_day = _mix_c0_day(central_expected_asof)
    summary["central_expected_asof"] = central_day
    if central_day is None:
        _mix_c0_warning(warnings, "mix_c0_central_expected_asof_unavailable", report_as_of_date=asof)
    elif central_day != asof:
        _mix_c0_warning(warnings, "mix_c0_asof_differs_from_central", report_as_of_date=asof, central_expected_asof=central_day)
    return summary


def _mix_c0_report_evidence(
    reports_root: Path,
    *,
    central_expected_asof: object = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Validate the current MIX C0 report: LATEST_C0_RUN.json -> manifest -> refs.

    Mirrors the read-only checks of ``mix_report_sender.load_mix_bundle``.
    KEEP (blocking): schemas/status/H, immutable run namespace, exact ref
    path/size/SHA-256, same-run manifest/payload consistency, ETF and
    non-ordinary exclusion, prediction validity.  Non-blocking (warnings):
    data freshness, report age, Central/version/binding differences, a newer
    run in LATEST_C0_RUN_BY_RUNTIME.json and missing descriptive fields.  The
    retired LATEST_PRODUCTION_RUN.json / LATEST_REPORT_RUN.json are never read.
    """
    now = now or datetime.now(timezone.utc)
    pointer_path = reports_root / MIX_C0_POINTER_NAME
    runtime_pointer_path = reports_root / MIX_C0_RUNTIME_POINTER_NAME
    warnings: list[dict[str, object]] = []
    result: dict[str, object] = {
        "status": "blocked",
        "blocking": True,
        "pass": False,
        "pointer_path": str(pointer_path),
        "runtime_pointer_path": str(runtime_pointer_path),
        "pointer_schema": MIX_C0_POINTER_SCHEMA,
        "legacy_pointers_read": False,
        "warnings": warnings,
    }
    pointer, pointer_read = _mix_c0_read_json(pointer_path, "mix_c0_pointer")
    result["pointer_read"] = pointer_read
    if pointer is None:
        result["reason"] = pointer_read.get("reason", "mix_c0_pointer_missing")
        return result
    problem = _mix_c0_header_problem(pointer, MIX_C0_POINTER_SCHEMA, "mix_c0_pointer")
    if problem:
        result["reason"] = problem
        return result
    run_id = pointer.get("run_id")
    if not isinstance(run_id, str) or not MIX_C0_RUN_ID_RE.fullmatch(run_id):
        result["reason"] = "mix_c0_pointer_run_id_invalid"
        return result
    asof = _mix_c0_day(pointer.get("as_of_date"))
    if asof is None:
        result["reason"] = "mix_c0_pointer_as_of_date_invalid"
        return result
    result.update({"run_id": run_id, "as_of_date": asof})
    run_root = reports_root / "c0" / run_id
    manifest_record, manifest_check = _mix_c0_ref(pointer.get("manifest"), root=run_root, expected_name="manifest.json", label="mix_c0_manifest")
    result["manifest_check"] = manifest_check
    if manifest_record is None:
        result["reason"] = manifest_check.get("reason", "mix_c0_manifest_invalid")
        return result
    manifest, manifest_read = _mix_c0_read_json(Path(str(manifest_record["path"])), "mix_c0_manifest")
    if manifest is None or manifest_read.get("sha256") != manifest_record["sha256"]:
        result["reason"] = manifest_read.get("reason", "mix_c0_manifest_changed_during_read")
        return result
    problem = _mix_c0_header_problem(manifest, MIX_C0_MANIFEST_SCHEMA, "mix_c0_manifest", run_id=run_id, asof=asof)
    if problem:
        result["reason"] = problem
        return result
    refs = manifest.get("artifacts")
    if not isinstance(refs, Mapping) or not isinstance(refs.get("payload"), Mapping):
        result["reason"] = "mix_c0_manifest_payload_ref_missing"
        return result
    unknown = sorted(set(refs) - set(MIX_C0_ARTIFACT_NAMES))
    if unknown:
        result.update({"reason": "mix_c0_manifest_unknown_artifact_ref", "unknown_artifacts": unknown})
        return result
    artifacts: dict[str, dict[str, object]] = {}
    for key, name in MIX_C0_ARTIFACT_NAMES.items():
        if key not in refs:
            _mix_c0_warning(warnings, "mix_c0_report_artifact_missing", artifact=key)
            continue
        record, check = _mix_c0_ref(refs[key], root=run_root, expected_name=name, label=f"mix_c0_{key}")
        if record is None:
            result.update({"reason": check.get("reason", f"mix_c0_{key}_invalid"), "artifact_check": check})
            return result
        artifacts[key] = record
    payload, payload_read = _mix_c0_read_json(Path(str(artifacts["payload"]["path"])), "mix_c0_payload")
    if payload is None or payload_read.get("sha256") != artifacts["payload"]["sha256"]:
        result["reason"] = payload_read.get("reason", "mix_c0_payload_changed_during_read")
        return result
    problem = _mix_c0_header_problem(payload, MIX_C0_PAYLOAD_SCHEMA, "mix_c0_payload", run_id=run_id, asof=asof)
    if problem:
        result["reason"] = problem
        return result
    if "input_sources" in refs and payload.get("input_sources") != refs["input_sources"]:
        result["reason"] = "mix_c0_payload_input_sources_ref_mismatch"
        return result
    problem = _mix_c0_binding_problem(manifest, payload, warnings)
    if problem:
        result["reason"] = problem
        return result
    problem, prediction_stats = _mix_c0_prediction_problem(manifest, payload)
    result["predictions"] = prediction_stats
    if problem:
        result["reason"] = problem
        return result
    result["data_time"] = _mix_c0_data_time(manifest, payload, asof=asof, now=now, central_expected_asof=central_expected_asof, warnings=warnings, pointer=pointer)
    # Same label sources as mix_report_sender: payload.version_labels + forward_binding.version_labels.
    forward_labels = payload.get("forward_binding").get("version_labels") if isinstance(payload.get("forward_binding"), Mapping) else None  # type: ignore[union-attr]
    codes = sorted(
        {
            str(item.get("code"))
            for labels in (payload.get("version_labels"), forward_labels)
            if isinstance(labels, list)
            for item in labels
            if isinstance(item, Mapping) and item.get("code")
        }
    )
    if codes:
        _mix_c0_warning(warnings, "mix_c0_report_version_labels", codes=codes)
    source_binding = payload.get("source_binding") if isinstance(payload.get("source_binding"), Mapping) else {}
    readiness = source_binding.get("readiness")
    if isinstance(readiness, Mapping) and readiness.get("ready") is not True:
        _mix_c0_warning(warnings, "mix_c0_six_route_readiness_not_ready_at_report_time", readiness_status=readiness.get("status"))
    statuses = source_binding.get("route_statuses")
    if isinstance(statuses, Mapping):
        partial = sorted(f"{key}={value}" for key, value in statuses.items() if str(value).upper() != "COMPLETE")
        if partial:
            _mix_c0_warning(warnings, "mix_c0_route_status_not_complete", routes=partial)
    if runtime_pointer_path.exists():
        runtime_pointer, runtime_read = _mix_c0_read_json(runtime_pointer_path, "mix_c0_runtime_pointer")
        if runtime_pointer is None:
            _mix_c0_warning(warnings, "mix_c0_runtime_pointer_unreadable", reason=runtime_read.get("reason"))
        elif runtime_pointer.get("run_id") != run_id:
            _mix_c0_warning(warnings, "mix_c0_newer_run_not_selected", selected_run_id=run_id, runtime_run_id=runtime_pointer.get("run_id"), runtime_as_of_date=runtime_pointer.get("as_of_date"), runtime_created_at=runtime_pointer.get("created_at"))
    result.update(
        {
            "status": "pass",
            "blocking": False,
            "pass": True,
            "pointer_sha256": pointer_read.get("sha256"),
            "created_at": result["data_time"].get("created_at"),  # type: ignore[union-attr]
            "manifest": manifest_record,
            "artifacts": artifacts,
            "warning_count": len(warnings),
        }
    )
    return result


def _mix_c0_delivery_fingerprint(run_id: str, manifest: Mapping[str, object], artifacts: Mapping[str, Mapping[str, object]]) -> str:
    """mix_report_sender dedup identity: run_id + manifest/artifact refs (path, size, sha256)."""
    refs = [manifest, *(artifacts[key] for key in MIX_C0_ARTIFACT_NAMES if key in artifacts)]
    body = {
        "schema": MIX_C0_PAYLOAD_SCHEMA,
        "run_id": run_id,
        "refs": [(str(Path(str(item["path"])).resolve(strict=False)), item["size"], item["sha256"]) for item in refs],
    }
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _warning(code: str, **detail: object) -> dict[str, object]:
    return {"code": code, "severity": "warning", "blocking": False, **detail}


def _blocked(result: dict[str, object], reason: str, **detail: object) -> dict[str, object]:
    result.update({"status": "blocked", "blocking": True, "pass": False, "reason": reason, **detail})
    return result


def _retained_paths(fixture_root: Path | None = None) -> dict[str, Path]:
    """Return the fixed evidence paths without discovering candidates.

    A fixture mirrors this layout below one caller-owned directory.  Production
    paths are fixed; no glob, legacy pointer or ``latest`` fallback is used.
    The single exception is the fixed single-stock ``reports\\c0`` directory,
    whose on-demand reports have no pointer and are all verified.
    """
    if fixture_root is not None:
        root = fixture_root.resolve()
        return {
            "central_registry": root / "central" / "requirements_v1.json",
            "central_publication_root": root / "central" / "shared_market_data",
            "mix_task_definition": root / "scheduler" / MIX_TASK_DEFINITION_PATH.name,
            "scheduled_tasks": root / "scheduler" / "scheduled_tasks.json",
            "mix_c0_pointer": root / "mix" / "reports" / MIX_C0_POINTER_NAME,
            "mix_c0_runtime_pointer": root / "mix" / "reports" / MIX_C0_RUNTIME_POINTER_NAME,
            "mix_reports_root": root / "mix" / "reports",
            "mix_root": root / "mix",
            "single_c0_reports_root": root / "single" / "reports" / "c0",
            "telegram_source": root / "telegram" / "telegram_stock_command_bot.py",
            "telegram_owner": root / "telegram" / "owner.json",
            "telegram_heartbeat": root / "telegram" / "heartbeat.json",
            "mix_delivery_state": root / "telegram" / MIX_DELIVERY_STATE_PATH.name,
            "retention": root / "retention" / "data_retention_guard.json",
            "retirement": root / "retirement" / "retirement_isolation.json",
            "quarantine_allowlist": root / "governance" / QUARANTINE_ALLOWLIST_PATH.name,
            "program_data_root": root / "program_data",
        }
    return {
        "central_registry": CENTRAL_REQUIREMENTS_REGISTRY_PATH,
        "central_publication_root": CENTRAL_PUBLICATION_ROOT,
        "mix_task_definition": MIX_TASK_DEFINITION_PATH,
        "mix_c0_pointer": MIX_C0_REPORTS_ROOT / MIX_C0_POINTER_NAME,
        "mix_c0_runtime_pointer": MIX_C0_REPORTS_ROOT / MIX_C0_RUNTIME_POINTER_NAME,
        "mix_reports_root": MIX_C0_REPORTS_ROOT,
        "mix_root": MIX_C0_NAMESPACE_ROOT,
        "single_c0_reports_root": SINGLE_C0_REPORTS_ROOT,
        "telegram_source": RETAINED_TELEGRAM_SOURCE,
        "telegram_owner": RETAINED_TELEGRAM_OWNER,
        "telegram_heartbeat": RETAINED_TELEGRAM_HEARTBEAT,
        "mix_delivery_state": MIX_DELIVERY_STATE_PATH,
        "retention": RETAINED_RETENTION_EVIDENCE,
        "retirement": RETAINED_RETIREMENT_EVIDENCE,
        "quarantine_allowlist": QUARANTINE_ALLOWLIST_PATH,
        "program_data_root": RETAINED_DATA_AUTHORITY_ROOTS["program_data"],
    }


def _retained_governance_config(paths: Mapping[str, Path], fixture_root: Path | None = None) -> dict[str, object]:
    """Fixed governance observation scope; a fixture mirrors it below ``governance``.

    Fixture layout: ``governance\\retired\\<root>`` (premarket nested under
    cowork like production), ``governance\\legacy_data\\<id>``,
    ``programs\\<scope>`` (blocking) and ``programs\\tooling`` (labels only).
    """
    if fixture_root is None:
        return {
            "retired_roots": dict(RETIRED_PROGRAM_ROOTS),
            "protected_roots": tuple(QUARANTINE_PROTECTED_ROOTS),
            "data_authority_roots": dict(RETAINED_DATA_AUTHORITY_ROOTS),
            "legacy_data_roots": dict(RETIRED_LEGACY_DATA_ROOTS),
            "scan_scopes": tuple(dict(item) for item in RETAINED_REFERENCE_SCAN_SCOPES),
            "not_scanned": [dict(item) for item in REFERENCE_SCAN_NOT_SCANNED],
        }
    root = fixture_root.resolve()
    governance = root / "governance"
    retired = governance / "retired"
    retired_roots = {
        "full_market_analysis": retired / "full_market_analysis",
        "cowork": retired / "cowork",
        "premarket_daytrade_top10": retired / "cowork" / "premarket_daytrade_top10",
        "twmix": retired / "twmix",
        "old_scheduler": retired / "daily_analysis_scheduler",
    }
    data_authority = {"shared_market_data": paths["central_publication_root"], "program_data": paths["program_data_root"]}
    legacy_data = {key: governance / "legacy_data" / key for key in RETIRED_LEGACY_DATA_ROOTS}
    programs = root / "programs"
    scopes = tuple(
        {"scope_id": scope_id, "component_id": component_id, "path": programs / scope_id, "recursive": True, "blocking": blocking}
        for scope_id, component_id, blocking in (
            ("mix", "mix_analysis_report", True),
            ("single", "single_stock_analysis_report", True),
            ("telegram", "telegram_service_publication", True),
            ("shared", "shared", True),
            ("central", "central_publication", True),
            ("tooling", "tooling", False),
        )
    )
    return {
        "retired_roots": retired_roots,
        "protected_roots": (*data_authority.values(), *legacy_data.values(), retired / "cowork" / "data", programs),
        "data_authority_roots": data_authority,
        "legacy_data_roots": legacy_data,
        "scan_scopes": scopes,
        "not_scanned": [],
    }


def _retained_strict_json(path: Path, label: str) -> tuple[Mapping[str, object] | None, bytes | None, dict[str, object]]:
    """Exact bytes + strict JSON (duplicate keys / NaN are tampering)."""
    raw, read = _retained_read_bytes(path, label)
    if raw is None:
        return None, None, read
    try:
        value = _mix_c0_strict_json(raw)
    except (UnicodeError, ValueError) as exc:
        read.update({"status": "blocked", "blocking": True, "pass": False, "reason": f"{label}_invalid_json", "error": type(exc).__name__})
        return None, raw, read
    if not isinstance(value, Mapping):
        read.update({"status": "blocked", "blocking": True, "pass": False, "reason": f"{label}_object_required"})
        return None, raw, read
    return value, raw, read


def _retained_hash_file(path: Path, label: str) -> dict[str, object]:
    """Stream-hash one exact file with a before/during/after identity check (KEEP: integrity)."""
    result: dict[str, object] = {"path": str(path), "status": "blocked", "blocking": True, "pass": False}
    before = _safe_stat(path)
    if before is None or not path.is_file():
        result["reason"] = f"{label}_missing"
        return result
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            during = os.fstat(handle.fileno())
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        after = path.stat()
    except (OSError, ValueError) as exc:
        result.update({"reason": f"{label}_unreadable", "error": type(exc).__name__})
        return result
    before_id = _retained_stat_identity(before)
    if before_id != _retained_stat_identity(during) or before_id != _retained_stat_identity(after) or size != int(after.st_size):
        result["reason"] = f"{label}_changed_during_read"
        return result
    result.update({"status": "pass", "blocking": False, "pass": True, "size": size, "sha256": digest.hexdigest()})
    return result


# --------------------------------------------------------------------------
# central_publication: requirements v3 + published head
# --------------------------------------------------------------------------


def _central_registry_evidence(path: Path) -> tuple[dict[str, object] | None, dict[str, object]]:
    """Requirements registry v3: one C0 publication scope, internally consistent."""
    value, _, read = _retained_strict_json(path, "central_registry")
    evidence: dict[str, object] = dict(read)
    warnings: list[dict[str, object]] = []
    evidence["warnings"] = warnings
    if value is None:
        evidence.setdefault("reason", "central_registry_missing")
        return None, _blocked(evidence, str(evidence["reason"]))
    evidence.update({"schema": value.get("schema"), "schema_version": value.get("schema_version"), "requirements_version": value.get("requirements_version")})
    if value.get("schema") != CENTRAL_REQUIREMENTS_REGISTRY_SCHEMA or value.get("schema_version") != CENTRAL_REQUIREMENTS_REGISTRY_SCHEMA_VERSION:
        return None, _blocked(evidence, "central_registry_schema_invalid", expected_schema=CENTRAL_REQUIREMENTS_REGISTRY_SCHEMA)
    publication = value.get("publication_contract")
    scope = publication.get("publication_scope") if isinstance(publication, Mapping) else None
    route_ids = scope.get("required_route_ids") if isinstance(scope, Mapping) else None
    if (
        not isinstance(scope, Mapping)
        or scope.get("profile_id") != CENTRAL_PROFILE_ID
        or not isinstance(route_ids, list)
        or not route_ids
        or any(not isinstance(item, str) or not item.strip() for item in route_ids)
        or len(set(route_ids)) != len(route_ids)
    ):
        return None, _blocked(evidence, "central_registry_publication_scope_invalid")
    assert isinstance(publication, Mapping)
    for key, expected in (
        ("current_head_relative_path", CENTRAL_HEAD_RELATIVE),
        ("archive_relative_root", CENTRAL_ARCHIVE_RELATIVE),
        ("objects_relative_root", CENTRAL_OBJECTS_RELATIVE),
    ):
        if publication.get(key) != expected:
            return None, _blocked(evidence, "central_registry_publication_layout_unexpected", field=key, declared=publication.get(key), expected=expected)
    requirements = value.get("shared_raw_requirements")
    if not isinstance(requirements, list) or any(not isinstance(item, Mapping) for item in requirements):
        return None, _blocked(evidence, "central_registry_route_set_inconsistent")
    ids = [str(item.get("id") or "").strip() for item in requirements]
    if (
        len(set(ids)) != len(ids)
        or set(ids) != set(route_ids)
        or any(item.get("required") is not True for item in requirements)
    ):
        return None, _blocked(evidence, "central_registry_route_set_inconsistent", scope_route_ids=list(route_ids), requirement_ids=ids)
    contracts = value.get("route_contracts")
    if not isinstance(contracts, Mapping) or {str(key) for key in contracts} != set(route_ids):
        return None, _blocked(evidence, "central_registry_route_contracts_inconsistent")
    interface = value.get("interface")
    if isinstance(interface, Mapping) and interface.get("route_ids") is not None and (
        not isinstance(interface.get("route_ids"), list) or set(interface["route_ids"]) != set(route_ids)
    ):
        return None, _blocked(evidence, "central_registry_interface_route_ids_inconsistent")
    if set(route_ids) != set(CENTRAL_C0_ROUTE_IDS):
        warnings.append(_warning("central_registry_route_set_differs_from_known_c0", route_ids=sorted(route_ids), known=sorted(CENTRAL_C0_ROUTE_IDS)))
    evidence.update(
        {
            "status": "pass",
            "blocking": False,
            "pass": True,
            "profile_id": CENTRAL_PROFILE_ID,
            "route_ids": list(route_ids),
            "route_total": len(route_ids),
            "required_total": len(route_ids),
            "optional_total": 0,
            "formal_consumer_mappings_non_authoritative": True,
        }
    )
    return {
        "path": path,
        "sha256": str(evidence["sha256"]),
        "route_ids": tuple(route_ids),
        "requirements_version": value.get("requirements_version"),
    }, evidence


def _central_head_evidence(root: Path, registry: Mapping[str, object] | None, now: datetime) -> dict[str, object]:
    """Published head -> identical immutable archive -> exact objects (KEEP: integrity/tamper).

    PARTIAL routes, an old head and version differences are labels only; the
    data time actually published is always shown.
    """
    head_path = root.joinpath(*CENTRAL_HEAD_RELATIVE.split("/"))
    warnings: list[dict[str, object]] = []
    result: dict[str, object] = {"path": str(head_path), "publication_root": str(root), "status": "blocked", "blocking": True, "pass": False, "warnings": warnings}
    raw, read = _retained_read_bytes(head_path, "central_head")
    result["read"] = read
    if raw is None:
        return _blocked(result, str(read.get("reason") or "central_head_missing"))
    head_sha = str(read["sha256"])
    archive_path = root / CENTRAL_ARCHIVE_RELATIVE / f"{head_sha}.json"
    result["archive_path"] = str(archive_path)
    archive_raw, archive_read = _retained_read_bytes(archive_path, "central_head_archive")
    if archive_raw is None:
        return _blocked(result, "central_head_archive_missing", archive_read=archive_read)
    if archive_raw != raw:
        return _blocked(result, "central_head_archive_mismatch")
    try:
        head = _mix_c0_strict_json(raw)
    except (UnicodeError, ValueError) as exc:
        return _blocked(result, "central_head_invalid_json", error=type(exc).__name__)
    if not isinstance(head, Mapping) or head.get("schema") != CENTRAL_HEAD_SCHEMA:
        return _blocked(result, "central_head_schema_invalid", schema=head.get("schema") if isinstance(head, Mapping) else None)
    run_id = head.get("run_id")
    producer_id = head.get("producer_id")
    published_at = head.get("published_at")
    if not all(isinstance(item, str) and item.strip() for item in (run_id, producer_id, published_at)):
        return _blocked(result, "central_head_identity_missing")
    result.update({"head_sha256": head_sha, "run_id": run_id, "producer_id": producer_id, "published_at": published_at, "previous_sha": head.get("previous_sha")})
    datasets = head.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        return _blocked(result, "central_head_datasets_missing")
    by_id: dict[str, Mapping[str, object]] = {}
    for item in datasets:
        route_id = item.get("route_id") if isinstance(item, Mapping) else None
        if not isinstance(route_id, str) or not route_id.strip() or route_id in by_id:
            return _blocked(result, "central_head_dataset_identity_invalid")
        assert isinstance(item, Mapping)
        by_id[route_id] = item
    required = tuple(registry.get("route_ids") or ()) if registry else CENTRAL_C0_ROUTE_IDS
    missing = sorted(set(required) - set(by_id))
    if missing:
        return _blocked(result, "central_head_required_route_missing", missing_routes=missing)
    extra = sorted(set(by_id) - set(required))
    if extra:
        warnings.append(_warning("central_head_extra_routes", routes=extra))
    routes: list[dict[str, object]] = []
    for route_id in required:
        item = by_id[route_id]
        relative = _retained_safe_relative(item.get("path"))
        sha = _retained_sha(item.get("sha256"))
        size = _retained_int(item.get("size"))
        if (
            relative is None
            or sha is None
            or size is None
            or size < 0
            or not relative.startswith(CENTRAL_OBJECTS_RELATIVE + "/")
            or relative.count("/") != 1
            or PurePosixPath(relative).stem != sha
        ):
            return _blocked(result, "central_head_object_ref_invalid", route_id=route_id)
        check = _retained_hash_file(root.joinpath(*relative.split("/")), f"central_object_{route_id}")
        if check.get("pass") is not True:
            return _blocked(result, str(check.get("reason")), route_id=route_id, object_check=check)
        if check.get("sha256") != sha or check.get("size") != size:
            return _blocked(result, "central_head_object_hash_mismatch", route_id=route_id, declared_sha256=sha, actual_sha256=check.get("sha256"), declared_size=size, actual_size=check.get("size"))
        status = str(item.get("status") or "").strip().upper()
        if status == "PARTIAL":
            warnings.append(_warning("central_route_partial", route_id=route_id, actual_max_date=item.get("actual_max_date")))
        elif status != "SUCCESS":
            return _blocked(result, "central_head_route_status_invalid", route_id=route_id, route_status=item.get("status"))
        routes.append(
            {
                "route_id": route_id,
                "status": status,
                "actual_min_date": item.get("actual_min_date"),
                "actual_max_date": item.get("actual_max_date"),
                "record_count": item.get("record_count"),
                "sha256": sha,
                "size": size,
            }
        )
    metadata = head.get("metadata") if isinstance(head.get("metadata"), Mapping) else {}
    calendar = metadata.get("verified_market_calendar") if isinstance(metadata.get("verified_market_calendar"), Mapping) else {}
    capture = metadata.get("application_capture_scope") if isinstance(metadata.get("application_capture_scope"), Mapping) else {}
    market_asof = by_id.get(CENTRAL_MARKET_ASOF_ROUTE, {}).get("actual_max_date")
    data_time: dict[str, object] = {
        "published_at": published_at,
        "market_asof": market_asof,
        "route_actual_max_dates": {item["route_id"]: item["actual_max_date"] for item in routes},
        "calendar_verified_asof": calendar.get("verified_asof"),
        "calendar_coverage_end": calendar.get("coverage_end"),
        "future_targets_verified": calendar.get("future_targets_verified"),
    }
    published = _retained_datetime(published_at)
    if published is None:
        warnings.append(_warning("central_head_published_at_invalid", published_at=published_at))
    else:
        age_hours = round((now - published).total_seconds() / 3600.0, 2)
        data_time["head_age_hours"] = age_hours
        if age_hours > RETAINED_FRESHNESS_HOURS:
            warnings.append(_warning("central_head_older_than_window", head_age_hours=age_hours, window_hours=RETAINED_FRESHNESS_HOURS, published_at=published_at))
    if _mix_c0_day(market_asof) is None:
        warnings.append(_warning("central_head_market_asof_unavailable", route_id=CENTRAL_MARKET_ASOF_ROUTE))
    declared_version = capture.get("requirements_version")
    registry_version = registry.get("requirements_version") if registry else None
    if declared_version and registry_version and declared_version != registry_version:
        warnings.append(_warning("central_head_requirements_version_differs", head_requirements_version=declared_version, registry_requirements_version=registry_version))
    result.update({"status": "pass", "blocking": False, "pass": True, "routes": routes, "data_time": data_time, "market_asof": market_asof})
    return result


def _retained_central_component(paths: Mapping[str, Path], now: datetime) -> dict[str, object]:
    registry, registry_check = _central_registry_evidence(paths["central_registry"])
    head_check = _central_head_evidence(paths["central_publication_root"], registry, now)
    checks = {"registry": registry_check, "published_head": head_check}
    failures = [name for name, value in checks.items() if value.get("blocking") is True or value.get("pass") is not True]
    warnings = [{"check": name, **item} for name, value in checks.items() for item in (value.get("warnings") or []) if isinstance(item, Mapping)]
    result: dict[str, object] = {
        "component_id": "central_publication",
        "status": "pass" if not failures else "blocked",
        "blocking": bool(failures),
        "pass": not failures,
        "checks": checks,
        "path": str(head_check.get("path")),
        "profile_id": CENTRAL_PROFILE_ID,
        "route_union": {
            "total": registry_check.get("route_total"),
            "required": registry_check.get("required_total"),
            "optional": registry_check.get("optional_total"),
            "route_ids": registry_check.get("route_ids"),
        },
        "retired_evidence_not_read": list(RETIRED_CENTRAL_EVIDENCE),
        "warnings": warnings,
    }
    if head_check.get("pass") is True:
        result.update(
            {
                "run_id": head_check.get("run_id"),
                "head_sha256": head_check.get("head_sha256"),
                "published_at": head_check.get("published_at"),
                "market_asof": head_check.get("market_asof"),
                "data_time": head_check.get("data_time"),
            }
        )
    if registry_check.get("pass") is True:
        result["registry_sha256"] = registry_check.get("sha256")
    if failures:
        result["reason"] = str(checks[failures[0]].get("reason") or failures[0])
    return result


# --------------------------------------------------------------------------
# workflow_scheduler / schedule_reconciliation: read-only Windows Task query
# --------------------------------------------------------------------------


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _query_scheduled_tasks(names: Sequence[str]) -> dict[str, object]:
    """Read-only: Get-ScheduledTask + Export-ScheduledTask for the named tasks.

    Never registers, changes, starts or stops a task.  Any failure is
    ``unavailable`` (the caller decides whether that blocks).
    """
    script = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)",
            "$names = @(" + ", ".join(_ps_quote(name) for name in names) + ")",
            "$rows = @()",
            "$me = [System.Security.Principal.WindowsIdentity]::GetCurrent()",
            "$meSid = if ($null -eq $me.User) { $null } else { [string]$me.User.Value.ToUpperInvariant() }",
            "foreach ($task in @(Get-ScheduledTask -ErrorAction Stop)) {",
            "  if ($names -ccontains [string]$task.TaskName) {",
            "    $xml = [string](Export-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath -ErrorAction Stop)",
            # Owner SID resolved like INSTALL_MIX_WEEKDAY_0800_TASK.ps1 Resolve-PrincipalSid (read-only lookup).
            "    $principal = [string]$task.Principal.UserId; $sid = $null",
            "    $candidates = @($principal); if ($principal -notmatch '^S-\\d-' -and $principal -notmatch '\\\\') { $domain = ($me.Name -split '\\\\', 2)[0]; if ($domain) { $candidates = @(\"$domain\\$principal\") + $candidates } }",
            "    foreach ($candidate in ($candidates | Select-Object -Unique)) { if ($null -ne $sid -or [string]::IsNullOrWhiteSpace($candidate)) { continue }; try { if ($candidate -match '^S-\\d-') { $sid = (New-Object System.Security.Principal.SecurityIdentifier($candidate)).Value.ToUpperInvariant() } else { $sid = (New-Object System.Security.Principal.NTAccount($candidate)).Translate([System.Security.Principal.SecurityIdentifier]).Value.ToUpperInvariant() } } catch { $sid = $null } }",
            "    $rows += [pscustomobject]@{ task_name = [string]$task.TaskName; task_path = [string]$task.TaskPath; state = [string]$task.State; xml = $xml; principal_user_id = $principal; principal_sid = $sid; current_user_sid = $meSid }",
            "  }",
            "}",
            "ConvertTo-Json -InputObject @($rows) -Depth 3 -Compress",
        ]
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    method = "Get-ScheduledTask/Export-ScheduledTask (read-only)"
    try:
        completed = subprocess.run(
            [str(POWERSHELL_EXE), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
            capture_output=True,
            timeout=TASK_QUERY_TIMEOUT_SECONDS,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "unavailable", "method": method, "error": type(exc).__name__}
    if completed.returncode != 0:
        return {"status": "unavailable", "method": method, "returncode": completed.returncode, "stderr": completed.stderr.decode("utf-8", "replace")[-500:]}
    try:
        rows = json.loads(completed.stdout.decode("utf-8-sig").strip() or "[]")
    except (UnicodeError, ValueError) as exc:
        return {"status": "unavailable", "method": method, "error": type(exc).__name__}
    if isinstance(rows, Mapping):
        rows = [rows]
    if not isinstance(rows, list) or any(not isinstance(item, Mapping) for item in rows):
        return {"status": "unavailable", "method": method, "error": "unexpected_output"}
    return {"status": "ok", "method": method, "tasks": [dict(item) for item in rows]}


def _fixture_task_query(path: Path) -> dict[str, object]:
    """Fixture stand-in for the read-only query; a missing file means no task is registered."""
    method = "fixture_scheduled_tasks_json"
    if _safe_stat(path) is None:
        return {"status": "ok", "method": method, "tasks": []}
    value, _, read = _retained_strict_json(path, "fixture_scheduled_tasks")
    tasks = value.get("tasks") if isinstance(value, Mapping) else None
    if not isinstance(tasks, list) or any(not isinstance(item, Mapping) for item in tasks):
        return {"status": "unavailable", "method": method, "error": read.get("reason") or "fixture_tasks_invalid"}
    return {"status": "ok", "method": method, "tasks": [dict(item) for item in tasks]}


# Field-by-field mirror of INSTALL_MIX_WEEKDAY_0800_TASK.ps1 Assert-Definition:
# (section, field, rule, expected).  "" is the definition root.  Rules:
#   eq        ``[string]$x -cne 'v'`` / list ``-join`` (case-sensitive, exact type)
#   is_true / is_false   ``$x -ne $true`` / ``$x -ne $false``
#   int       ``[int]$x -ne n``
#   path_ci   ``[string]$x -ne $suite`` (case-insensitive string)
#   match / not_match    ``-notmatch 're'`` (must match) / ``-match 're'`` (must not match)
#   absent    ``@($x.PSObject.Properties.Name) -contains 'f'`` (legacy field refused)
# The cross_system_validation tests extract this list from the ps1 and compare
# it with this table, so the two cannot drift apart silently.
MIX_TASK_DEFINITION_RULES: tuple[tuple[str, str, str, object], ...] = (
    ("", "schema_version", "eq", MIX_TASK_DEFINITION_SCHEMA),
    ("", "task_name", "eq", MIX_TASK_NAME),
    ("", "timezone", "eq", MIX_TASK_TIMEZONE),
    ("trigger", "days_of_week", "eq", list(MIX_TASK_EXPECTED_DAYS)),
    ("trigger", "start_time", "eq", "08:00"),
    ("trigger", "start_when_available", "is_true", True),
    ("action", "executable", "match", r"(?i)\\cmd\.exe$"),
    ("action", "arguments", "eq", MIX_TASK_EXPECTED_ARGUMENTS),
    ("action", "working_directory", "path_ci", str(MIX_TASK_SENDER_ROOT)),
    ("action", "no_pause", "is_true", True),
    ("action", "max_wait_seconds", "int", 0),
    ("action", "arguments", "not_match", r"(?i)D-stock|RUN_SCHEDULED_SEND|RUN_SEND_LATEST_REPORT|ensure|refresh|update|allow-network"),
    ("action", "arguments", "match", r"(?i)RUN_SEND_MIX_REPORT\.cmd"),
    ("settings", "multiple_instances", "eq", "IgnoreNew"),
    ("settings", "execution_time_limit_seconds", "int", 0),
    ("settings", "allow_start_if_on_batteries", "is_true", True),
    ("settings", "dont_stop_if_going_on_batteries", "is_true", True),
    ("settings", "dont_stop_on_idle_end", "is_true", True),
    ("publication_contract", "source", "eq", MIX_TASK_SOURCE_POINTER),
    ("publication_contract", "required_pointer_schema", "eq", MIX_C0_POINTER_SCHEMA),
    ("publication_contract", "required_status", "eq", MIX_C0_STATUS),
    ("publication_contract", "required_official", "is_true", True),
    ("publication_contract", "required_publishable", "is_false", False),
    ("publication_contract", "publishable_is_quality_indicator", "is_true", True),
    ("publication_contract", "quality_conditions_action", "eq", "include_unmet_conditions_without_blocking"),
    ("publication_contract", "required_execution_success", "is_true", True),
    ("publication_contract", "required_artifact_integrity", "is_true", True),
    ("publication_contract", "requires_central_receipt_and_registry", "is_true", True),
    ("publication_contract", "selection_policy", "eq", "formal_only"),
    ("publication_contract", "no_action_action", "eq", "reject_no_action_report"),
    ("publication_contract", "stale_report_action", "eq", "send_with_report_and_latest_data_days_labeled"),
    ("publication_contract", "integrity_failure_action", "eq", "exit_nonzero_fail_closed_no_send"),
    # Retired combined action (previous round) and MIX v1 feedback receipt: C0 never writes one.
    ("publication_contract", "stale_or_integrity_failure_action", "absent", None),
    ("publication_contract", "requires_feedback_publication_receipt", "absent", None),
    ("publication_contract", "formal_unavailable_action", "eq", "exit_nonzero_no_fallback"),
    ("publication_contract", "formal_send_failure_action", "eq", "exit_nonzero_no_fallback"),
)
# INSTALL_MIX_WEEKDAY_0800_TASK.ps1 registers the task as the *current* interactive
# user (``New-ScheduledTaskPrincipal -UserId $identity.name``), not a fixed account.
# None = no fixed principal: an owner different from the verifying user is a label.
# A fixed SID here would make an owner mismatch block.
MIX_TASK_FIXED_PRINCIPAL_SID: str | None = None


_MISSING = object()


def _mix_task_rule_ok(section: Mapping[str, object], field: str, rule: str, expected: object) -> bool:
    value = section.get(field, _MISSING)
    if rule == "absent":
        return value is _MISSING
    if rule == "eq":
        return value is not _MISSING and type(value) is type(expected) and value == expected
    if rule == "is_true":
        return value is True
    if rule == "is_false":
        return value is False
    if rule == "int":
        return type(value) is int and value == expected
    if rule == "path_ci":
        return isinstance(value, str) and value.casefold() == str(expected).casefold()
    text = value if isinstance(value, str) else ""
    if rule == "match":
        return re.search(str(expected), text) is not None
    if rule == "not_match":
        return re.search(str(expected), text) is None
    raise ValueError(f"unknown MIX task definition rule: {rule}")


def _mix_task_definition_evidence(path: Path) -> tuple[Mapping[str, object] | None, dict[str, object]]:
    """Same contract as INSTALL_MIX_WEEKDAY_0800_TASK.ps1 Assert-Definition (every field, see MIX_TASK_DEFINITION_RULES)."""
    value, _, read = _retained_strict_json(path, "mix_task_definition")
    check: dict[str, object] = dict(read)
    if value is None:
        return None, _blocked(check, str(check.get("reason") or "mix_task_definition_missing"))
    problems: list[str] = []
    for section_name, field, rule, expected in MIX_TASK_DEFINITION_RULES:
        raw_section = value.get(section_name) if section_name else value
        section = raw_section if isinstance(raw_section, Mapping) else {}
        if not _mix_task_rule_ok(section, field, rule, expected):
            label = f"{section_name}.{field}" if section_name else field
            if label not in problems:
                problems.append(label)
    if problems:
        sections = sorted({item.split(".", 1)[0] if "." in item else "root" for item in problems})
        return None, _blocked(check, "mix_task_definition_invalid", invalid_fields=problems, invalid_sections=sections)
    check.update({"status": "pass", "blocking": False, "pass": True, "schema_version": MIX_TASK_DEFINITION_SCHEMA, "task_name": MIX_TASK_NAME, "rules_checked": len(MIX_TASK_DEFINITION_RULES)})
    return value, check


def _task_xml_snapshot(xml_text: object) -> dict[str, object] | None:
    """Parse an exported task XML into the fields INSTALL_MIX_WEEKDAY_0800_TASK.ps1 verifies."""
    if not isinstance(xml_text, str) or not xml_text.strip():
        return None
    text = re.sub(r"^\ufeff?\s*<\?xml[^>]*\?>", "", xml_text.strip())
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return None
    ns = {"t": TASK_XML_NAMESPACE}

    def text_of(node: ElementTree.Element | None, name: str) -> str | None:
        child = node.find(f"t:{name}", ns) if node is not None else None
        return child.text.strip() if child is not None and child.text is not None else None

    execs = root.findall("t:Actions/t:Exec", ns)
    actions = root.find("t:Actions", ns)
    triggers = root.find("t:Triggers", ns)
    calendar = root.findall("t:Triggers/t:CalendarTrigger", ns)
    weekly = calendar[0].find("t:ScheduleByWeek", ns) if len(calendar) == 1 else None
    days_node = weekly.find("t:DaysOfWeek", ns) if weekly is not None else None
    settings = root.find("t:Settings", ns)
    idle = settings.find("t:IdleSettings", ns) if settings is not None else None
    return {
        "action_count": len(list(actions)) if actions is not None else 0,
        "exec_count": len(execs),
        "command": text_of(execs[0], "Command") if execs else None,
        "arguments": text_of(execs[0], "Arguments") if execs else None,
        "working_directory": text_of(execs[0], "WorkingDirectory") if execs else None,
        "trigger_count": len(list(triggers)) if triggers is not None else 0,
        "calendar_trigger_count": len(calendar),
        "days": sorted(str(child.tag).rsplit("}", 1)[-1] for child in list(days_node)) if days_node is not None else None,
        "weeks_interval": text_of(weekly, "WeeksInterval") if weekly is not None else None,
        "start_boundary": text_of(calendar[0], "StartBoundary") if len(calendar) == 1 else None,
        "trigger_enabled": text_of(calendar[0], "Enabled") if len(calendar) == 1 else None,
        "execution_time_limit": text_of(settings, "ExecutionTimeLimit"),
        "disallow_start_if_on_batteries": text_of(settings, "DisallowStartIfOnBatteries"),
        "stop_if_going_on_batteries": text_of(settings, "StopIfGoingOnBatteries"),
        "start_when_available": text_of(settings, "StartWhenAvailable"),
        "multiple_instances": text_of(settings, "MultipleInstancesPolicy"),
        "stop_on_idle_end": text_of(idle, "StopOnIdleEnd") if idle is not None else None,
        "enabled": text_of(settings, "Enabled"),
    }


def _mix_task_mismatches(snapshot: Mapping[str, object], definition: Mapping[str, object]) -> list[str]:
    """Registered task vs definition, with the same rules as INSTALL_MIX_WEEKDAY_0800_TASK.ps1 -Verify."""
    action = definition.get("action") if isinstance(definition.get("action"), Mapping) else {}
    trigger = definition.get("trigger") if isinstance(definition.get("trigger"), Mapping) else {}
    problems: list[str] = []
    if snapshot.get("action_count") != 1 or snapshot.get("exec_count") != 1:
        problems.append("action_count")
    if str(snapshot.get("command") or "").casefold() != str(action.get("executable") or "").casefold():
        problems.append("action_executable")
    if snapshot.get("arguments") != action.get("arguments"):
        problems.append("action_arguments")
    if (_normalize_windows_path(snapshot.get("working_directory")) or "").casefold() != (_normalize_windows_path(action.get("working_directory")) or "").casefold():
        problems.append("action_working_directory")
    if snapshot.get("trigger_count") != 1 or snapshot.get("calendar_trigger_count") != 1:
        problems.append("trigger_count")
    if snapshot.get("days") != sorted(str(day) for day in (trigger.get("days_of_week") or [])) or str(snapshot.get("weeks_interval") or "1") != "1":
        problems.append("trigger_days")
    boundary = str(snapshot.get("start_boundary") or "")
    clock = boundary.split("T", 1)[1][:5] if "T" in boundary else ""
    if clock != str(trigger.get("start_time") or ""):
        problems.append("trigger_start_time")
    if snapshot.get("execution_time_limit") != "PT0S":
        problems.append("execution_time_limit")
    if snapshot.get("disallow_start_if_on_batteries") == "true" or snapshot.get("stop_if_going_on_batteries") == "true":
        problems.append("battery_settings")
    if snapshot.get("start_when_available") != "true":
        problems.append("start_when_available")
    if snapshot.get("multiple_instances") != "IgnoreNew":
        problems.append("multiple_instances")
    if snapshot.get("stop_on_idle_end") == "true":
        problems.append("stop_on_idle_end")
    return problems


def _mix_task_owner_evidence(task: Mapping[str, object]) -> dict[str, object]:
    """Registered owner SID vs the installer's principal rule.

    INSTALL registers as the current interactive user (no fixed principal), so
    with ``MIX_TASK_FIXED_PRINCIPAL_SID`` unset an owner that is not the verifying
    user, or an owner SID that could not be resolved, is only a label.  Only a
    fixed principal SID that differs blocks.
    """
    principal_sid = str(task.get("principal_sid") or "").strip().upper() or None
    current_sid = str(task.get("current_user_sid") or "").strip().upper() or None
    owner: dict[str, object] = {
        "principal_user_id": task.get("principal_user_id"),
        "principal_sid": principal_sid,
        "current_user_sid": current_sid,
        "fixed_principal_sid": MIX_TASK_FIXED_PRINCIPAL_SID,
        "installer_principal": "current_interactive_user" if MIX_TASK_FIXED_PRINCIPAL_SID is None else "fixed",
        "blocking": False,
    }
    if MIX_TASK_FIXED_PRINCIPAL_SID is not None:
        if principal_sid != MIX_TASK_FIXED_PRINCIPAL_SID.upper():
            owner.update({"blocking": True, "reason": "schedule_task_owner_sid_mismatch"})
        return owner
    if principal_sid is None:
        owner["warning"] = "schedule_owner_sid_unverified"
    elif current_sid is None or principal_sid != current_sid:
        owner["warning"] = "schedule_owner_not_current_user"
    else:
        owner["matches_current_user"] = True
    return owner


def _task_enabled(task: Mapping[str, object]) -> bool:
    if str(task.get("state") or "").strip().casefold() == "disabled":
        return False
    snapshot = _task_xml_snapshot(task.get("xml"))
    return not (snapshot is not None and snapshot.get("enabled") == "false")


def _retained_scheduler_component(paths: Mapping[str, Path], query: Mapping[str, object]) -> dict[str, object]:
    """MIX weekday 08:00 Windows Task: not registered = warning; registered = must match the definition."""
    definition, definition_check = _mix_task_definition_evidence(paths["mix_task_definition"])
    warnings: list[dict[str, object]] = []
    result: dict[str, object] = {
        "component_id": "workflow_scheduler",
        "schedule_kind": "windows_task",
        "task_name": MIX_TASK_NAME,
        "task_path": MIX_TASK_PATH,
        "path": str(paths["mix_task_definition"]),
        "checks": {"definition": definition_check},
        "query": {key: query.get(key) for key in ("status", "method", "error", "returncode")},
        "retired_evidence_not_read": [str(RETIRED_WORKFLOW_FINAL_POINTER)],
        "warnings": warnings,
        "status": "blocked",
        "blocking": True,
        "pass": False,
    }
    if definition is None:
        return _blocked(result, str(definition_check.get("reason") or "mix_task_definition_invalid"))
    if query.get("status") != "ok":
        return _blocked(result, "schedule_query_unavailable")
    matches = [task for task in query.get("tasks") or [] if isinstance(task, Mapping) and task.get("task_name") == MIX_TASK_NAME]
    if not matches:
        warnings.append(_warning("schedule_not_registered", task_name=MIX_TASK_NAME, detail="MIX 08:00 排程尚未註冊；註冊另行處理（INSTALL_MIX_WEEKDAY_0800_TASK.ps1 -Register -ConfirmRegister），本工具不註冊"))
        result.update({"status": "warning", "blocking": False, "pass": None, "registered": False, "reason": "schedule_not_registered"})
        return result
    if len(matches) != 1 or matches[0].get("task_path") != MIX_TASK_PATH:
        return _blocked(result, "schedule_task_identity_ambiguous", task_paths=[task.get("task_path") for task in matches])
    task = matches[0]
    snapshot = _task_xml_snapshot(task.get("xml"))
    if snapshot is None:
        return _blocked(result, "schedule_task_xml_unreadable", registered=True)
    result["registered_snapshot"] = snapshot
    problems = _mix_task_mismatches(snapshot, definition)
    if problems:
        return _blocked(result, "schedule_task_definition_mismatch", registered=True, mismatches=problems)
    owner = _mix_task_owner_evidence(task)
    result["owner"] = owner
    if owner.get("blocking") is True:
        return _blocked(result, str(owner.get("reason")), registered=True)
    if owner.get("warning"):
        warnings.append(_warning(str(owner["warning"]), task_name=MIX_TASK_NAME, principal_user_id=owner.get("principal_user_id"), principal_sid=owner.get("principal_sid"), current_user_sid=owner.get("current_user_sid")))
    if not _task_enabled(task):
        # Definition matches but the task will not run: a label, never a pass.
        warnings.append(_warning("schedule_registered_but_disabled", task_name=MIX_TASK_NAME, state=task.get("state")))
        result.update({"status": "warning", "blocking": False, "pass": None, "registered": True, "state": task.get("state"), "reason": "schedule_registered_but_disabled"})
        return result
    result.update({"status": "pass", "blocking": False, "pass": True, "registered": True, "state": task.get("state")})
    return result


def _retained_schedule_component(query: Mapping[str, object]) -> dict[str, object]:
    """Retired v1/v2 task names may exist only disabled (read-only query, no evidence file)."""
    warnings: list[dict[str, object]] = []
    result: dict[str, object] = {
        "component_id": "schedule_reconciliation",
        "path": None,
        "query": {key: query.get(key) for key in ("status", "method", "error", "returncode")},
        "current_tasks": [MIX_TASK_NAME],
        "legacy_task_names": list(LEGACY_TASK_NAMES),
        "retired_evidence_not_read": [str(RETIRED_SCHEDULE_EVIDENCE)],
        "warnings": warnings,
        "status": "blocked",
        "blocking": True,
        "pass": False,
    }
    if query.get("status") != "ok":
        return _blocked(result, "schedule_query_unavailable")
    legacy = [task for task in query.get("tasks") or [] if isinstance(task, Mapping) and task.get("task_name") in LEGACY_TASK_NAMES]
    enabled = sorted(str(task.get("task_name")) for task in legacy if _task_enabled(task))
    present = [{"task_name": str(task.get("task_name")), "task_path": task.get("task_path"), "state": task.get("state"), "enabled": _task_enabled(task)} for task in legacy]
    result["legacy_tasks_present"] = present
    if enabled:
        return _blocked(result, "schedule_legacy_task_enabled", enabled_legacy_tasks=enabled)
    for item in present:
        warnings.append(_warning("legacy_task_present_disabled", task_name=item["task_name"]))
    result.update({"status": "pass", "blocking": False, "pass": True})
    return result


# --------------------------------------------------------------------------
# mix_analysis_report / mix_mitake_persistence / telegram delivery
# --------------------------------------------------------------------------


def _retained_mix_component(paths: Mapping[str, Path], central: Mapping[str, object], now: datetime) -> dict[str, object]:
    """MIX analysis report = the current C0 report named by LATEST_C0_RUN.json."""
    evidence = _mix_c0_report_evidence(paths["mix_reports_root"], central_expected_asof=central.get("expected_asof"), now=now)
    result: dict[str, object] = {"component_id": "mix_analysis_report", "path": str(paths["mix_c0_pointer"]), **evidence}
    if evidence.get("pass") is not True:
        return result
    asof = str(evidence["as_of_date"])
    artifacts: dict[str, object] = {"artifact_manifest": {"status": "pass", "blocking": False, "pass": True, **evidence["manifest"]}}  # type: ignore[dict-item]
    for key, record in (evidence.get("artifacts") or {}).items():  # type: ignore[union-attr]
        artifacts[f"artifact_{key}"] = {"status": "pass", "blocking": False, "pass": True, **record}
    result.update(
        {
            "report_run_id": evidence["run_id"],
            "expected_asof": f"{asof[:4]}-{asof[4:6]}-{asof[6:]}",
            "central_head_sha256": str(central.get("head_sha256") or "") or None,
            "central_registry_sha256": str(central.get("registry_sha256") or "") or None,
            "status_sha256": evidence.get("pointer_sha256"),
            "artifacts": artifacts,
        }
    )
    return result


def _retained_mitake_component(mix: Mapping[str, object]) -> dict[str, object]:
    """C0 (core_daily) produces no Mitake handoff: not applicable, a label, never a pass."""
    return {
        "component_id": "mix_mitake_persistence",
        "status": "not_applicable",
        "applicable": False,
        "blocking": False,
        "pass": None,
        "reason": "not_applicable_c0",
        "profile": "C0",
        "mix_report_run_id": mix.get("report_run_id"),
        "retired_evidence_not_read": ["mix-mitake-remake-close-reopen-v2 result / workflow_scheduler_v1 mitake phase"],
        "warnings": [
            _warning(
                "mitake_not_applicable_c0",
                detail="MIX C0 不做三竹同步；舊 v1 三竹證據不讀取，也不視為通過",
            )
        ],
    }


def _mix_c0_delivery_evidence(
    reports_root: Path,
    state_path: Path,
    *,
    central_expected_asof: object = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Telegram delivery of the current MIX C0 report (``mix_report_delivery_state_v1``).

    Reads only ``LATEST_C0_RUN.json`` (via :func:`_mix_c0_report_evidence`) and
    the state written by ``mix_report_sender.deliver_mix_bundle``; never calls
    the sender or Telegram.  Not delivered yet -- no state file, or a valid
    state with no record for the current report's fingerprint -- is a warning
    (``mix_report_not_yet_delivered``, pass=None).  A record for the current
    report that is incomplete, corrupt or inconsistent with its fingerprint /
    manifest SHA / message_id / sent_items, or an unreadable / wrong-schema
    state file, still blocks (integrity).
    """
    report = _mix_c0_report_evidence(reports_root, central_expected_asof=central_expected_asof, now=now)
    result: dict[str, object] = {
        "status": "blocked",
        "blocking": True,
        "pass": False,
        "pointer_path": report.get("pointer_path"),
        "delivery_state": str(state_path),
        "delivery_state_schema": MIX_C0_DELIVERY_STATE_SCHEMA,
        "warnings": [],
    }
    warnings: list[dict[str, object]] = result["warnings"]  # type: ignore[assignment]
    if _safe_stat(state_path) is None:
        warnings.append(_warning("mix_report_not_yet_delivered", delivery_state=str(state_path), report_run_id=report.get("run_id"), report_as_of_date=report.get("as_of_date")))
        result.update({"status": "warning", "blocking": False, "pass": None, "reason": "mix_report_not_yet_delivered", "run_id": report.get("run_id"), "as_of_date": report.get("as_of_date")})
        return result
    if report.get("pass") is not True:
        return _blocked(result, str(report.get("reason") or "mix_c0_report_invalid"))
    run_id = str(report["run_id"])
    manifest = report["manifest"]
    artifacts = report["artifacts"]
    assert isinstance(manifest, Mapping) and isinstance(artifacts, Mapping)
    fingerprint = _mix_c0_delivery_fingerprint(run_id, manifest, artifacts)
    result.update({"run_id": run_id, "as_of_date": report.get("as_of_date"), "fingerprint": fingerprint})

    state, state_read = _mix_c0_read_json(state_path, "mix_delivery_state")
    if state is None:
        return _blocked(result, "mix_delivery_state_missing_or_invalid", state_read=state_read)
    deliveries = state.get("deliveries")
    if state.get("schema_version") != MIX_C0_DELIVERY_STATE_SCHEMA or not isinstance(deliveries, Mapping):
        return _blocked(result, "mix_delivery_state_schema_invalid")
    entry = deliveries.get(fingerprint)
    last_complete = str(state.get("last_report_complete_fingerprint") or state.get("last_complete_fingerprint") or "")
    result["last_complete_fingerprint"] = last_complete or None
    if entry is None:
        # A record for the current run_id stored under another fingerprint means the
        # run's manifest/artifact refs changed after delivery: integrity, not a state.
        stale_keys = sorted(
            str(key)
            for key, item in deliveries.items()
            if isinstance(item, Mapping) and str(item.get("run_id") or "") == run_id
        )
        if stale_keys:
            return _blocked(result, "mix_delivery_state_fingerprint_mismatch", recorded_fingerprints=stale_keys)
        # Same meaning as a missing state file: the current report is not sent yet.
        # A state, not an integrity problem -> label (never a pass, never a block).
        warnings.append(
            _warning(
                "mix_report_not_yet_delivered",
                delivery_state=str(state_path),
                delivery_state_present=True,
                report_run_id=run_id,
                report_as_of_date=report.get("as_of_date"),
                fingerprint=fingerprint,
                last_complete_fingerprint=last_complete or None,
            )
        )
        result.update({"status": "warning", "blocking": False, "pass": None, "reason": "mix_report_not_yet_delivered", "delivery_state_present": True})
        return result
    if not isinstance(entry, Mapping):
        # A record keyed by the current fingerprint that is not an object is corrupt.
        return _blocked(result, "mix_delivery_state_entry_invalid")
    if entry.get("status") != "complete" or entry.get("report_delivery_status") != "complete":
        return _blocked(result, "mix_delivery_state_not_complete", entry_status=entry.get("report_delivery_status") or entry.get("status"))
    if (
        str(entry.get("run_id") or "") != run_id
        or entry.get("profile") != "C0"
        or not _mix_c0_same_path(str(entry.get("manifest_path") or ""), str(manifest.get("path")))
        or str(entry.get("manifest_sha256") or "").lower() != str(manifest.get("sha256"))
    ):
        return _blocked(result, "mix_delivery_state_identity_mismatch")
    if _mix_c0_day(entry.get("expected_asof")) != report.get("as_of_date"):
        return _blocked(result, "mix_delivery_state_asof_mismatch")

    messages = entry.get("messages")
    sent_items = entry.get("sent_items")
    if not isinstance(messages, list) or not messages:
        return _blocked(result, "mix_delivery_message_receipts_missing")
    keys: list[str] = []
    for message in messages:
        message_id = message.get("message_id") if isinstance(message, Mapping) else None
        key = message.get("key") if isinstance(message, Mapping) else None
        if not isinstance(key, str) or not key or type(message_id) is not int or message_id <= 0:
            return _blocked(result, "mix_delivery_message_id_missing")
        keys.append(key)
    if (
        len(keys) != len(set(keys))
        or not isinstance(sent_items, list)
        or any(not isinstance(item, str) for item in sent_items)
        or set(sent_items) != set(keys)
    ):
        return _blocked(result, "mix_delivery_message_receipt_keys_inconsistent")
    summary_count = sum(1 for key in keys if key.startswith("summary:"))
    document_count = len(keys) - summary_count
    expected_documents = {
        str(record.get("sha256"))
        for record in (manifest, artifacts.get("payload"), artifacts.get("html"), artifacts.get("excel"))
        if isinstance(record, Mapping)
    }
    delivered_documents = {key.rsplit(":", 1)[-1] for key in keys if not key.startswith("summary:")}
    if summary_count != 1 or document_count < 1:
        return _blocked(result, "mix_delivery_summary_or_document_message_missing")
    if delivered_documents != expected_documents:
        return _blocked(result, "mix_delivery_document_receipts_not_current_artifacts")
    if last_complete and last_complete != fingerprint:
        warnings.append(_warning("mix_delivery_last_complete_is_other_report", last_complete_fingerprint=last_complete))
    result.update(
        {
            "status": "pass",
            "blocking": False,
            "pass": True,
            "completed_at": entry.get("completed_at"),
            "message_counts": {"summary": summary_count, "document": document_count},
        }
    )
    return result


def _telegram_service_evidence(paths: Mapping[str, Path], now: datetime) -> dict[str, object]:
    """Command-bot identity: disk source SHA = owner = heartbeat, same PID, fresh heartbeat."""
    source_raw, source_read = _retained_read_bytes(paths["telegram_source"], "telegram_source")
    owner, owner_read = _retained_read_json(paths["telegram_owner"], "telegram_owner")
    heartbeat, heartbeat_read = _retained_read_json(paths["telegram_heartbeat"], "telegram_heartbeat")
    result: dict[str, object] = {
        "status": "blocked",
        "blocking": True,
        "pass": False,
        "paths": {"source": str(paths["telegram_source"]), "owner": str(paths["telegram_owner"]), "heartbeat": str(paths["telegram_heartbeat"])},
        "reads": {"source": source_read, "owner": owner_read, "heartbeat": heartbeat_read},
    }
    if source_raw is None or owner is None or heartbeat is None:
        return _blocked(result, "telegram_runtime_evidence_missing")
    source_sha = str(source_read.get("sha256") or "").lower()
    owner_sha = _retained_sha(owner.get("sourceSha256") or owner.get("source_sha256"))
    heartbeat_sha = _retained_sha(heartbeat.get("sourceSha256") or heartbeat.get("source_sha256"))
    owner_pid = _retained_int(owner.get("processId") if owner.get("processId") is not None else owner.get("pid"))
    heartbeat_pid = _retained_int(heartbeat.get("pid") if heartbeat.get("pid") is not None else heartbeat.get("processId"))
    if owner_sha != source_sha or heartbeat_sha != source_sha or owner_pid is None or owner_pid <= 0 or heartbeat_pid != owner_pid:
        return _blocked(result, "telegram_source_or_pid_identity_invalid")
    heartbeat_status = str(heartbeat.get("status") or "").strip().lower()
    if heartbeat_status not in {"booting", "running", "polling", "working"}:
        return _blocked(result, "telegram_service_not_running", heartbeat_status=heartbeat_status)
    heartbeat_age = _retained_datetime(heartbeat.get("updatedAt") or heartbeat.get("updated_at"))
    stale_seconds = _retained_int(heartbeat.get("serviceHeartbeatStaleSeconds")) or 180
    if heartbeat_age is None or (now - heartbeat_age).total_seconds() < -300 or (now - heartbeat_age).total_seconds() > stale_seconds:
        return _blocked(result, "telegram_heartbeat_stale", heartbeat_updated_at=heartbeat.get("updatedAt") or heartbeat.get("updated_at"))
    result.update({"status": "pass", "blocking": False, "pass": True, "source_sha256": source_sha, "pid": owner_pid, "heartbeat_status": heartbeat_status, "heartbeat_updated_at": heartbeat_age.isoformat()})
    return result


def _retained_telegram_component(paths: Mapping[str, Path], central: Mapping[str, object], now: datetime) -> dict[str, object]:
    service = _telegram_service_evidence(paths, now)
    delivery = _mix_c0_delivery_evidence(paths["mix_reports_root"], paths["mix_delivery_state"], central_expected_asof=central.get("expected_asof"), now=now)
    checks = {"service": service, "mix_delivery": delivery}
    failures = [name for name, value in checks.items() if value.get("blocking") is True]
    unverified = [name for name, value in checks.items() if value.get("status") == "warning"]
    result: dict[str, object] = {
        "component_id": "telegram_service_publication",
        "checks": checks,
        "path": str(paths["mix_delivery_state"]),
        "retired_evidence_not_read": [str(RETIRED_TELEGRAM_MESSAGE_RECEIPT)],
        "warnings": [{"check": name, **item} for name, value in checks.items() for item in (value.get("warnings") or []) if isinstance(item, Mapping)],
    }
    if failures:
        result.update({"status": "blocked", "blocking": True, "pass": False, "reason": str(checks[failures[0]].get("reason") or failures[0])})
    elif unverified:
        result.update({"status": "warning", "blocking": False, "pass": None, "reason": str(checks[unverified[0]].get("reason") or unverified[0])})
    else:
        result.update({"status": "pass", "blocking": False, "pass": True})
    if service.get("pass") is True:
        result.update({"source_sha256": service.get("source_sha256"), "pid": service.get("pid"), "heartbeat_updated_at": service.get("heartbeat_updated_at")})
    if delivery.get("pass") is True:
        result.update({"message_fingerprint": delivery.get("fingerprint"), "message_counts": delivery.get("message_counts"), "delivered_run_id": delivery.get("run_id")})
    return result


# --------------------------------------------------------------------------
# single_stock_analysis_report: on-demand C0 reports
# --------------------------------------------------------------------------


def _single_c0_canonical(value: object) -> bytes:
    """tw_single_stock_c0._canonical for already-parsed JSON values."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _single_c0_ordinary(record: object, stock_id: str) -> bool:
    return (
        isinstance(record, Mapping)
        and str(record.get("stock_id") or "") == stock_id
        and record.get("is_ordinary_share") is True
        and record.get("security_type") == "ordinary_share"
        and record.get("current_ordinary_classification_verified") is not False
        and str(record.get("board") or record.get("market") or "").upper() in {"TWSE", "TPEX"}
    )


def _single_c0_report_check(json_path: Path) -> tuple[dict[str, object] | None, dict[str, object], list[dict[str, object]]]:
    """One sealed single-stock C0 report: schema, seal, PDF bytes, ETF/non-ordinary exclusion."""
    warnings: list[dict[str, object]] = []
    check: dict[str, object] = {"path": str(json_path), "status": "blocked", "blocking": True, "pass": False}
    match = SINGLE_C0_REPORT_NAME_RE.fullmatch(json_path.name)
    if match is None:
        return None, _blocked(check, "single_c0_report_name_invalid"), warnings
    document, _, read = _retained_strict_json(json_path, "single_c0_report")
    check["read"] = read
    if document is None:
        return None, _blocked(check, str(read.get("reason") or "single_c0_report_unreadable")), warnings
    code, horizon_text = match.group(1), match.group(2)
    stock = document.get("stock") if isinstance(document.get("stock"), Mapping) else {}
    horizon = document.get("horizon_sessions")
    if document.get("report_schema_version") != SINGLE_C0_REPORT_SCHEMA or document.get("profile") != "C0":
        return None, _blocked(check, "single_c0_report_schema_invalid"), warnings
    if type(horizon) is not int or horizon not in SINGLE_C0_HORIZONS or str(horizon) != horizon_text:
        return None, _blocked(check, "single_c0_report_horizon_invalid"), warnings
    if str(stock.get("stock_id") or "") != code:
        return None, _blocked(check, "single_c0_report_identity_mismatch"), warnings
    # Formal-attachment contract of stock_daily_contract.validate_c0_estimate and
    # telegram_stock_command_bot.validate_c0_no_estimate_report (KEEP).
    report_class = document.get("report_class")
    if (
        report_class not in SINGLE_C0_REPORT_CLASSES
        or document.get("source_mode") != "published"
        or document.get("target_definition") != SINGLE_C0_TARGET_DEFINITION
    ):
        return None, _blocked(check, "single_c0_report_class_source_or_target_invalid", report_class=report_class), warnings
    if document.get("qa_override") is not False:
        return None, _blocked(check, "single_c0_report_qa_override_not_formal"), warnings
    if (
        document.get("historical_pit_verified") is not False
        or document.get("historical_membership_verified") is not False
        or document.get("corporate_actions_verified") is not False
        or document.get("effectiveness_status") != "unknown"
        or document.get("trading_effect") != "unverified"
    ):
        return None, _blocked(check, "single_c0_report_claims_unverified_historical_or_effectiveness"), warnings
    carries_estimate = "prob_up" in document or "expected_return" in document or document.get("prediction") is not None
    if report_class == SINGLE_C0_NO_ESTIMATE_CLASS:
        if carries_estimate or document.get("estimate_status") != "no_estimate":
            return None, _blocked(check, "single_c0_no_estimate_report_carries_estimate"), warnings
    elif not isinstance(document.get("prediction"), Mapping) or "prob_up" not in document:
        return None, _blocked(check, "single_c0_estimate_report_estimate_missing"), warnings
    if not _mix_c0_same_path(str(document.get("output_json") or ""), json_path) or not _mix_c0_same_path(str(document.get("output_pdf") or ""), json_path.with_suffix(".pdf")):
        return None, _blocked(check, "single_c0_report_path_mismatch"), warnings
    sealed = dict(document)
    declared_seal = sealed.pop("estimate_binding_sha256", None)
    try:
        actual_seal = hashlib.sha256(_single_c0_canonical(sealed)).hexdigest()
    except (TypeError, ValueError):
        actual_seal = None
    if not isinstance(declared_seal, str) or declared_seal != actual_seal:
        return None, _blocked(check, "single_c0_report_seal_mismatch"), warnings
    pdf_path = json_path.with_suffix(".pdf")
    pdf_raw, pdf_read = _retained_read_bytes(pdf_path, "single_c0_report_pdf")
    if pdf_raw is None:
        return None, _blocked(check, str(pdf_read.get("reason") or "single_c0_report_pdf_missing")), warnings
    if pdf_read.get("sha256") != document.get("pdf_sha256") or document.get("pdf_size") != len(pdf_raw):
        return None, _blocked(check, "single_c0_report_pdf_hash_mismatch"), warnings
    prediction = document.get("prediction") if isinstance(document.get("prediction"), Mapping) else None
    prob_up = document.get("prob_up")
    if prediction is not None or prob_up is not None:
        coverage = document.get("coverage") if isinstance(document.get("coverage"), Mapping) else {}
        records = coverage.get("records") if isinstance(coverage.get("records"), list) else []
        if not _single_c0_ordinary(stock, code) or not any(_single_c0_ordinary(item, code) for item in records):
            return None, _blocked(check, "single_c0_report_non_ordinary_or_etf_estimate"), warnings
        if type(prob_up) not in (int, float) or prob_up != prob_up or not 0 <= prob_up <= 1:  # type: ignore[operator]
            return None, _blocked(check, "single_c0_report_prediction_invalid"), warnings
    verification = document.get("verification") if isinstance(document.get("verification"), Mapping) else None
    verification_status = verification.get("status") if verification else None
    if verification and verification_status == "failed" and verification.get("integrity_error") is True:
        return None, _blocked(check, "single_c0_report_verification_integrity_failed"), warnings
    if verification_status not in {"verified", "not_applicable_no_estimate"}:
        warnings.append(_warning("single_c0_report_not_verified", path=str(json_path), verification_status=verification_status))
    data_time = document.get("data_time") if isinstance(document.get("data_time"), Mapping) else None
    if data_time is None:
        warnings.append(_warning("single_c0_data_time_missing", path=str(json_path), latest_data_date=document.get("latest_data_date")))
    elif data_time.get("freshness_status") not in (None, "latest"):
        warnings.append(_warning("single_c0_data_not_latest", path=str(json_path), freshness_status=data_time.get("freshness_status")))
    record = {
        "path": str(json_path),
        "pdf_path": str(pdf_path),
        "stock_id": code,
        "horizon": horizon,
        "created_stamp": match.group(3),
        "report_class": document.get("report_class"),
        "latest_data_date": document.get("latest_data_date"),
        "analysis_asof": document.get("analysis_asof"),
        "source_statuses": document.get("source_statuses"),
        "verification_status": verification_status,
        "json_sha256": read.get("sha256"),
        "pdf_sha256": pdf_read.get("sha256"),
    }
    check.update({"status": "pass", "blocking": False, "pass": True})
    return record, check, warnings


def _retained_single_component(paths: Mapping[str, Path]) -> dict[str, object]:
    """Every on-demand C0 report in the fixed reports\\c0 directory (no pointer exists by design)."""
    root = paths["single_c0_reports_root"]
    warnings: list[dict[str, object]] = []
    result: dict[str, object] = {
        "component_id": "single_stock_analysis_report",
        "path": str(root),
        "on_demand": True,
        "retired_evidence_not_read": [str(RETIRED_SINGLE_LATEST_MANIFEST)],
        "warnings": warnings,
        "status": "blocked",
        "blocking": True,
        "pass": False,
    }
    try:
        names = sorted(item.name for item in root.iterdir() if item.is_file()) if root.is_dir() else []
    except OSError as exc:
        return _blocked(result, "single_c0_reports_root_unreadable", error=type(exc).__name__)
    reports = [name for name in names if SINGLE_C0_REPORT_NAME_RE.fullmatch(name)]
    orphans = [name for name in names if SINGLE_C0_PDF_NAME_RE.fullmatch(name) and name[:-4] + ".json" not in reports]
    for name in orphans:
        warnings.append(_warning("single_c0_orphan_pdf", path=str(root / name)))
    if not reports:
        warnings.append(_warning("single_c0_no_report_yet", path=str(root), detail="個股分析為按需執行，尚無 C0 報告"))
        result.update({"status": "warning", "blocking": False, "pass": None, "reason": "single_c0_no_report_yet", "report_count": 0})
        return result
    records: list[dict[str, object]] = []
    for name in reports:
        record, check, report_warnings = _single_c0_report_check(root / name)
        if record is None:
            return _blocked(result, str(check.get("reason")), failed_report=check, verified_report_count=len(records))
        warnings.extend(report_warnings)
        records.append(record)
    latest = max(records, key=lambda item: str(item["created_stamp"]))
    result.update({"status": "pass", "blocking": False, "pass": True, "report_count": len(records), "latest_report": latest, "reports": records})
    return result


# --------------------------------------------------------------------------
# governance: read-only direct observation (legacy evidence only when present)
# --------------------------------------------------------------------------


def _union_matches(union: object, expected: Mapping[str, object] | None) -> bool:
    return (
        isinstance(union, Mapping)
        and expected is not None
        and expected.get("total") is not None
        and all(union.get(key) == expected.get(key) for key in ("total", "required", "optional"))
    )


def _legacy_retention_evidence(paths: Mapping[str, Path], union: Mapping[str, object] | None) -> dict[str, object]:
    """Original rules of data_retention_guard.json (applied only when the file exists)."""
    document, read = _retained_read_json(paths["retention"], "data_retention_guard")
    result: dict[str, object] = {"path": str(paths["retention"]), "present": True, "status": "blocked", "blocking": True, "pass": False, "read": read}
    if document is None:
        result["reason"] = read.get("reason", "data_retention_guard_missing")
        return result
    if document.get("schema") != "cross-system-data-retention-guard-v1" or document.get("status") not in {"pass", "verified", "complete"} or document.get("data_untouched") is not True or _retained_int(document.get("deleted_count")) != 0 or _retained_int(document.get("moved_count")) != 0:
        result["reason"] = "data_retention_guard_status_invalid"
        return result
    quarantine, check = _retained_quarantine_record(document.get("quarantine_manifest") or document.get("quarantine"), "quarantine_manifest", Path(r"D:\TWStockData") if paths["retention"] == RETAINED_RETENTION_EVIDENCE else paths["retention"].parents[1])
    if quarantine is None:
        result["reason"] = check.get("reason", "quarantine_manifest_invalid")
        result["quarantine_manifest"] = check
        return result
    if not _union_matches(document.get("central_union"), union):
        result["reason"] = "data_retention_central_union_invalid"
        return result
    result.update({"status": "pass", "blocking": False, "pass": True, "zero_touch": True, "quarantine_manifest": quarantine, "central_union": dict(document["central_union"])})
    return result


def _legacy_retirement_evidence(paths: Mapping[str, Path], union: Mapping[str, object] | None) -> dict[str, object]:
    """Original rules of retirement_isolation.json (applied only when the file exists)."""
    document, read = _retained_read_json(paths["retirement"], "retirement_isolation")
    result: dict[str, object] = {"path": str(paths["retirement"]), "present": True, "status": "blocked", "blocking": True, "pass": False, "read": read}
    if document is None:
        result["reason"] = read.get("reason", "retirement_isolation_missing")
        return result
    if document.get("schema") != "cross-system-retirement-isolation-v1" or document.get("status") not in {"pass", "verified", "complete"} or document.get("data_untouched") is not True or document.get("telegram_protected") is not True:
        result["reason"] = "retirement_isolation_status_invalid"
        return result
    if not _union_matches(document.get("central_union"), union):
        result["reason"] = "retirement_isolation_central_union_invalid"
        return result
    isolated = document.get("isolated_consumers")
    if not isinstance(isolated, list) or any(str(item).strip() in RETAINED_COMPONENT_IDS for item in isolated):
        result["reason"] = "retirement_isolation_active_component_invalid"
        return result
    result.update({"status": "pass", "blocking": False, "pass": True, "data_untouched": True, "telegram_protected": True, "central_union": dict(document["central_union"]), "isolated_consumers": [str(item) for item in isolated]})
    return result


def _legacy_evidence_if_present(path: Path, validate: Callable[[], dict[str, object]]) -> dict[str, object]:
    """A missing legacy file is not required (no producer); a present one must still pass its own rules."""
    if not os.path.lexists(path):
        return {"path": str(path), "present": False, "required": False, "status": "not_present"}
    return validate()


def _gov_norm(path: object) -> str:
    return os.path.normcase(os.path.normpath(os.fspath(path)))  # type: ignore[arg-type]


def _gov_text_norm(text: str) -> str:
    """Lower-cased text with ``/`` and escaped backslashes folded to one backslash."""
    return re.sub(r"\\+", r"\\", text.replace("/", "\\")).lower()


def _governance_allowlist(path: Path, retired_roots: Mapping[str, Path]) -> tuple[list[dict[str, object]] | None, dict[str, object]]:
    """Read program_quarantine_allowlist_v1.json (read-only) with program_quarantine's entry rules."""
    value, _, read = _retained_strict_json(path, "quarantine_allowlist")
    check: dict[str, object] = dict(read)
    if value is None:
        return None, _blocked(check, str(check.get("reason") or "quarantine_allowlist_missing"))
    if value.get("schema") != QUARANTINE_ALLOWLIST_SCHEMA:
        return None, _blocked(check, "quarantine_allowlist_schema_invalid", schema=value.get("schema"))
    entries = value.get("entries")
    if not isinstance(entries, list) or not entries:
        return None, _blocked(check, "quarantine_allowlist_entries_required")
    normalized: list[dict[str, object]] = []
    for index, entry in enumerate(entries):
        if (
            not isinstance(entry, Mapping)
            or set(entry) != QUARANTINE_ALLOWLIST_ENTRY_FIELDS
            or any(not isinstance(entry[key], str) or not entry[key].strip() for key in QUARANTINE_ALLOWLIST_ENTRY_FIELDS)
        ):
            return None, _blocked(check, "quarantine_allowlist_entry_invalid", entry_index=index)
        entry_id, root_id, relative_text = str(entry["entry_id"]), str(entry["source_root_id"]), str(entry["relative_path"])
        root = retired_roots.get(root_id)
        if root is None:
            return None, _blocked(check, "quarantine_allowlist_unknown_root", entry_id=entry_id, source_root_id=root_id)
        relative = PureWindowsPath(relative_text)
        if relative.drive or relative.root or not relative.parts or any(part in {".", ".."} for part in relative.parts) or any(token in relative_text for token in "*?[]"):
            return None, _blocked(check, "quarantine_allowlist_relative_path_invalid", entry_id=entry_id, relative_path=relative_text)
        observed = root.joinpath(*relative.parts)
        if _gov_norm(str(entry["source_path"])) != _gov_norm(observed):
            return None, _blocked(check, "quarantine_allowlist_source_path_mismatch", entry_id=entry_id, source_path=entry["source_path"], expected_path=str(observed))
        if root_id in RETIRED_SHARED_ROOT_IDS and any(other_id != root_id and _relative_to(observed, other) for other_id, other in retired_roots.items()):
            return None, _blocked(check, "quarantine_allowlist_root_overlap", entry_id=entry_id)
        if entry["category"] not in {"retired_analysis", "retired_wrapper"}:
            return None, _blocked(check, "quarantine_allowlist_category_invalid", entry_id=entry_id, category=entry["category"])
        normalized.append(
            {
                "entry_id": entry_id,
                "source_root_id": root_id,
                "relative_path": relative_text,
                "category": entry["category"],
                "observed_path": str(observed),
                "suffix": relative.suffix.lower(),
            }
        )
    if len({item["entry_id"] for item in normalized}) != len(normalized) or len({_gov_norm(item["observed_path"]) for item in normalized}) != len(normalized):
        return None, _blocked(check, "quarantine_allowlist_duplicate_entry")
    check.update({"status": "pass", "blocking": False, "pass": True, "schema": QUARANTINE_ALLOWLIST_SCHEMA, "entry_total": len(normalized)})
    return normalized, check


def _retired_reference_patterns(entries: Sequence[Mapping[str, object]], retired_roots: Mapping[str, Path]) -> dict[str, object]:
    """Path tokens, path-segment names and Python module names that identify the retired programs."""
    tokens: set[str] = set()
    segments: set[str] = set()
    for root_id, root in retired_roots.items():
        if root_id not in RETIRED_SHARED_ROOT_IDS:
            tokens.add(_gov_text_norm(str(root)))
            segments.add(root.name.lower())
    modules: set[str] = set()
    for entry in entries:
        observed = Path(str(entry["observed_path"]))
        root = retired_roots[str(entry["source_root_id"])]
        tokens.add(_gov_text_norm(str(observed)))
        parent = observed.parent
        while _relative_to(parent, root):
            tokens.add(_gov_text_norm(str(parent)))
            parent = parent.parent
        parts = PureWindowsPath(str(entry["relative_path"])).parts
        if len(parts) >= 2 and parts[0].lower() == "src":
            if len(parts) > 2:
                modules.add(parts[1])
            elif parts[1].lower().endswith(".py"):
                modules.add(parts[1][:-3])
    ordered = sorted(tokens, key=lambda item: (-len(item), item))
    return {
        "path_tokens": ordered,
        "segment_names": sorted(segments),
        "module_names": sorted(modules),
        "path_re": re.compile(r"(?<![\w])(?:" + "|".join(re.escape(item) for item in ordered) + r")(?![\w.\-])") if ordered else None,
        "segment_re": re.compile(r"\\(?:" + "|".join(re.escape(item) for item in sorted(segments)) + r")(?![\w.\-])") if segments else None,
        # A quoted path component equal to a retired root name catches paths
        # assembled piecewise (Path(...) / "twmix", os.path.join, Join-Path).
        "literal_re": re.compile(r"(?<![\w])[\"'](?:" + "|".join(re.escape(item) for item in sorted(segments)) + r")[\"']") if segments else None,
    }


def _gov_text_matches(text: str, patterns: Mapping[str, object]) -> list[str]:
    normalized = _gov_text_norm(text)
    found: list[str] = []
    for key in ("path_re", "segment_re", "literal_re"):
        regex = patterns.get(key)
        if isinstance(regex, re.Pattern):
            found.extend(match.group(0) for match in regex.finditer(normalized))
    return sorted(set(found))


def _gov_iter_files(root: Path, recursive: bool) -> tuple[list[Path], list[dict[str, object]]]:
    """Program files of one scan scope (no symlink following, fixed exclusions)."""
    files: list[Path] = []
    errors: list[dict[str, object]] = []

    def wanted(name: str) -> bool:
        return name.lower().endswith(REFERENCE_SCAN_SUFFIXES) and REFERENCE_SCAN_EXCLUDED_FILE_RE.match(name) is None

    if not recursive:
        try:
            with os.scandir(root) as iterator:
                for item in sorted(iterator, key=lambda value: value.name):
                    if item.is_file(follow_symlinks=False) and wanted(item.name):
                        files.append(Path(item.path))
        except OSError as exc:
            errors.append({"path": str(root), "error": type(exc).__name__})
        return files, errors

    def on_error(exc: OSError) -> None:
        errors.append({"path": str(getattr(exc, "filename", root)), "error": type(exc).__name__})

    for directory, dir_names, file_names in os.walk(root, onerror=on_error, followlinks=False):
        dir_names[:] = sorted(name for name in dir_names if name.lower() not in REFERENCE_SCAN_EXCLUDED_DIR_NAMES)
        files.extend(Path(directory) / name for name in sorted(file_names) if wanted(name))
    return files, errors


def _gov_decode(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp950", "replace")


def _gov_python_imports(text: str) -> list[tuple[int, str]] | None:
    """Top-level module names imported (import / from-import / import_module / __import__)."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name.split(".")[0]) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.lineno, node.module.split(".")[0]))
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else None
            if name in {"import_module", "__import__"}:
                found.append((node.lineno, node.args[0].value.split(".")[0]))
    return found


def _gov_reference_scan(scopes: Sequence[Mapping[str, object]], patterns: Mapping[str, object]) -> dict[str, object]:
    """Read-only text + AST scan of the current program trees for retired references."""
    listed: list[tuple[Mapping[str, object], bool, list[Path], list[dict[str, object]]]] = []
    current_modules: set[str] = set()
    for scope in scopes:
        root = Path(str(scope["path"]))
        exists = root.is_dir()
        files, errors = _gov_iter_files(root, bool(scope.get("recursive"))) if exists else ([], [])
        listed.append((scope, exists, files, errors))
        if scope.get("blocking") is True:
            for path in files:
                if path.suffix.lower() in {".py", ".pyw"}:
                    current_modules.add(path.parent.name if path.name.lower() == "__init__.py" else path.stem)
    retired_modules = {str(item) for item in patterns.get("module_names") or ()}  # type: ignore[union-attr]
    shadowed = sorted(retired_modules & current_modules)
    effective_modules = retired_modules - current_modules
    results: list[dict[str, object]] = []
    for scope, exists, files, errors in listed:
        hits: list[dict[str, object]] = []
        unreadable: list[dict[str, object]] = list(errors)
        unparseable: list[str] = []
        for path in files:
            try:
                with path.open("rb") as handle:
                    text = _gov_decode(handle.read())
            except OSError as exc:
                unreadable.append({"path": str(path), "error": type(exc).__name__})
                continue
            file_hits: list[dict[str, object]] = []
            for line_number, line in enumerate(text.splitlines(), 1):
                for match in _gov_text_matches(line, patterns):
                    file_hits.append({"line": line_number, "kind": "path", "match": match})
            if path.suffix.lower() in {".py", ".pyw"}:
                imports = _gov_python_imports(text)
                if imports is None:
                    unparseable.append(str(path))
                else:
                    file_hits.extend({"line": line_number, "kind": "python_import", "match": name} for line_number, name in imports if name in effective_modules)
            if file_hits:
                file_hits.sort(key=lambda item: (int(item["line"]), str(item["kind"])))
                hits.append({"path": str(path), "hit_count": len(file_hits), "hits": file_hits[:REFERENCE_SCAN_MAX_HITS_PER_FILE]})
        results.append(
            {
                "scope_id": scope.get("scope_id"),
                "component_id": scope.get("component_id"),
                "path": str(scope.get("path")),
                "recursive": bool(scope.get("recursive")),
                "blocking": scope.get("blocking") is True,
                "exists": exists,
                "files_scanned": len(files),
                "files_with_references": hits,
                "unreadable": unreadable,
                "python_unparseable": unparseable,
            }
        )
    return {
        "method": "read_only_text_and_python_ast_scan",
        "rules": {
            "suffixes": list(REFERENCE_SCAN_SUFFIXES),
            "excluded_dir_names": sorted(REFERENCE_SCAN_EXCLUDED_DIR_NAMES),
            "excluded_file_pattern": REFERENCE_SCAN_EXCLUDED_FILE_RE.pattern,
            "symlinks_followed": False,
            "comments_and_strings_count_as_references": True,
            "path_token_count": len(patterns.get("path_tokens") or ()),  # type: ignore[arg-type]
            "segment_names": list(patterns.get("segment_names") or ()),  # type: ignore[arg-type]
            "retired_python_modules": sorted(effective_modules),
            "retired_python_modules_shadowed_by_current_module": shadowed,
        },
        "scopes": results,
    }


def _gov_schedule_references(query: Mapping[str, object], patterns: Mapping[str, object]) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for task in query.get("tasks") or []:  # type: ignore[union-attr]
        if not isinstance(task, Mapping):
            continue
        matches = _gov_text_matches(str(task.get("xml") or ""), patterns)
        if matches:
            found.append({"task_name": task.get("task_name"), "task_path": task.get("task_path"), "state": task.get("state"), "enabled": _task_enabled(task), "matches": matches[:REFERENCE_SCAN_MAX_HITS_PER_FILE]})
    return found


def _gov_finish(result: dict[str, object], failures: list[dict[str, object]]) -> dict[str, object]:
    if failures:
        return _blocked(result, str(failures[0]["reason"]), failures=failures)
    result.update({"status": "pass", "blocking": False, "pass": True})
    return result


def _retained_retirement_component(
    paths: Mapping[str, Path],
    config: Mapping[str, object],
    allowlist: Sequence[Mapping[str, object]] | None,
    allowlist_check: Mapping[str, object],
    query: Mapping[str, object],
    union: Mapping[str, object] | None,
) -> dict[str, object]:
    """Retired programs are absent and unreachable from retained code and tasks (read-only observation)."""
    warnings: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    checks: dict[str, object] = {}
    result: dict[str, object] = {
        "component_id": "retirement_isolation",
        "path": str(paths["quarantine_allowlist"]),
        "observation_basis": GOVERNANCE_OBSERVATION_BASIS,
        "observation_note": "現況觀察：退役檔不在原位置、現行程式與已查詢排程沒有指向它們；不是退役事件稽核",
        "checks": checks,
        "warnings": warnings,
        "status": "blocked",
        "blocking": True,
        "pass": False,
    }
    legacy = _legacy_evidence_if_present(paths["retirement"], lambda: _legacy_retirement_evidence(paths, union))
    checks["legacy_evidence"] = legacy
    if legacy.get("present") is True and legacy.get("pass") is not True:
        failures.append({"reason": str(legacy.get("reason") or "retirement_isolation_invalid"), "check": "legacy_evidence"})
    checks["allowlist"] = dict(allowlist_check)
    if allowlist is None:
        failures.append({"reason": str(allowlist_check.get("reason") or "quarantine_allowlist_invalid"), "check": "allowlist"})
        return _gov_finish(result, failures)
    retired_roots: Mapping[str, Path] = config["retired_roots"]  # type: ignore[assignment]
    present = [{"entry_id": item["entry_id"], "path": item["observed_path"]} for item in allowlist if os.path.lexists(str(item["observed_path"]))]
    checks["retired_files_absent"] = {
        "status": "blocked" if present else "pass",
        "entry_total": len(allowlist),
        "present_count": len(present),
        "present": present[:20],
        "retired_roots": {key: {"path": str(root), "exists": os.path.lexists(root), "shared_root": key in RETIRED_SHARED_ROOT_IDS} for key, root in retired_roots.items()},
    }
    if present:
        failures.append({"reason": "retired_program_file_present", "check": "retired_files_absent", "present_count": len(present)})
    for key, root in retired_roots.items():
        if key not in RETIRED_SHARED_ROOT_IDS and os.path.lexists(root):
            warnings.append(_warning("retired_program_root_present", root_id=key, path=str(root), detail="退役程式根目錄重新出現（allowlist 檔案不在，不擋；請確認內容）"))
    patterns = _retired_reference_patterns(allowlist, retired_roots)
    scan = _gov_reference_scan(config["scan_scopes"], patterns)  # type: ignore[arg-type]
    scan["not_scanned"] = list(config.get("not_scanned") or [])  # type: ignore[call-overload]
    checks["reference_scan"] = scan
    for scope in scan["scopes"]:  # type: ignore[union-attr]
        if scope["blocking"]:
            if not scope["exists"]:
                failures.append({"reason": "retirement_reference_scan_scope_missing", "check": "reference_scan", "scope_id": scope["scope_id"], "path": scope["path"]})
            if scope["unreadable"]:
                failures.append({"reason": "retirement_reference_scan_file_unreadable", "check": "reference_scan", "scope_id": scope["scope_id"], "unreadable": scope["unreadable"][:20]})
            if scope["files_with_references"]:
                failures.append({"reason": "retired_program_referenced_by_current_code", "check": "reference_scan", "scope_id": scope["scope_id"], "files": [item["path"] for item in scope["files_with_references"]][:20]})
        else:
            for item in scope["files_with_references"]:
                warnings.append(_warning("retired_program_referenced_by_non_retained_tree", scope_id=scope["scope_id"], path=item["path"], hit_count=item["hit_count"], first_match=item["hits"][0]["match"]))
        for path in scope["python_unparseable"]:
            warnings.append(_warning("reference_scan_python_unparseable", scope_id=scope["scope_id"], path=path, detail="AST 無法解析，只做文字比對"))
    schedule: dict[str, object] = {
        "source": "schedule_reconciliation read-only query (same call)",
        "query": {key: query.get(key) for key in ("status", "method", "error", "returncode")},
        "task_names_queried": [MIX_TASK_NAME, *LEGACY_TASK_NAMES],
        "scope_note": "只檢查同一次唯讀查詢回傳的工作（MIX 08:00 與舊排程名稱），其他名稱的工作不在觀察範圍",
    }
    checks["scheduled_tasks"] = schedule
    if query.get("status") != "ok":
        failures.append({"reason": "retirement_schedule_query_unavailable", "check": "scheduled_tasks"})
    else:
        references = _gov_schedule_references(query, patterns)
        schedule.update({"tasks_seen": len(query.get("tasks") or []), "references": references})  # type: ignore[arg-type]
        enabled = [item for item in references if item["enabled"]]
        if enabled:
            failures.append({"reason": "retired_program_referenced_by_enabled_task", "check": "scheduled_tasks", "tasks": [item["task_name"] for item in enabled]})
        for item in references:
            if not item["enabled"]:
                warnings.append(_warning("retired_program_referenced_by_disabled_task", task_name=item["task_name"], matches=item["matches"]))
    return _gov_finish(result, failures)


def _retained_retention_component(
    paths: Mapping[str, Path],
    config: Mapping[str, object],
    allowlist: Sequence[Mapping[str, object]] | None,
    allowlist_check: Mapping[str, object],
    central: Mapping[str, object],
    union: Mapping[str, object] | None,
) -> dict[str, object]:
    """Data roots present, Central head intact, quarantine scope is programs only (read-only observation)."""
    warnings: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    checks: dict[str, object] = {}
    result: dict[str, object] = {
        "component_id": "data_retention_guard",
        "path": str(paths["quarantine_allowlist"]),
        "observation_basis": GOVERNANCE_OBSERVATION_BASIS,
        "observation_note": "現況觀察：資料根目錄存在、Central head／manifest／objects 完整、隔離清單只含程式檔；無法證明過去從未刪除或搬移資料（沒有事件稽核紀錄）",
        "checks": checks,
        "warnings": warnings,
        "status": "blocked",
        "blocking": True,
        "pass": False,
    }
    legacy = _legacy_evidence_if_present(paths["retention"], lambda: _legacy_retention_evidence(paths, union))
    checks["legacy_evidence"] = legacy
    if legacy.get("present") is True and legacy.get("pass") is not True:
        failures.append({"reason": str(legacy.get("reason") or "data_retention_guard_invalid"), "check": "legacy_evidence"})
    checks["allowlist"] = dict(allowlist_check)
    if allowlist is None:
        failures.append({"reason": str(allowlist_check.get("reason") or "quarantine_allowlist_invalid"), "check": "allowlist"})
    else:
        protected: Sequence[Path] = config["protected_roots"]  # type: ignore[assignment]
        non_program = [{"entry_id": item["entry_id"], "path": item["observed_path"]} for item in allowlist if item["suffix"] not in RETIRED_PROGRAM_SUFFIXES]
        in_protected = [
            {"entry_id": item["entry_id"], "path": item["observed_path"]}
            for item in allowlist
            if any(_relative_to(Path(str(item["observed_path"])), root) or _gov_norm(str(item["observed_path"])) == _gov_norm(root) for root in protected)
        ]
        checks["allowlist_programs_only"] = {
            "status": "blocked" if non_program or in_protected else "pass",
            "entry_total": len(allowlist),
            "allowed_suffixes": list(RETIRED_PROGRAM_SUFFIXES),
            "non_program_entries": non_program[:20],
            "entries_in_protected_roots": in_protected[:20],
            "protected_root_count": len(protected),
        }
        if non_program:
            failures.append({"reason": "data_retention_allowlist_non_program_entry", "check": "allowlist_programs_only", "count": len(non_program)})
        if in_protected:
            failures.append({"reason": "data_retention_allowlist_entry_in_protected_root", "check": "allowlist_programs_only", "count": len(in_protected)})
    roots: dict[str, object] = {}
    for name, root in dict(config["data_authority_roots"]).items():  # type: ignore[call-overload]
        record: dict[str, object] = {"path": str(root), "exists": root.is_dir()}
        if record["exists"]:
            try:
                with os.scandir(root) as iterator:
                    record["non_empty"] = next(iterator, None) is not None
            except OSError as exc:
                record.update({"non_empty": None, "error": type(exc).__name__})
        roots[name] = record
        if not record["exists"]:
            failures.append({"reason": "data_retention_data_root_missing", "check": "data_authority_roots", "root": name, "path": str(root)})
        elif record.get("non_empty") is not True:
            failures.append({"reason": "data_retention_data_root_empty_or_unreadable", "check": "data_authority_roots", "root": name, "path": str(root)})
    checks["data_authority_roots"] = roots
    legacy_roots: dict[str, object] = {}
    for name, root in dict(config["legacy_data_roots"]).items():  # type: ignore[call-overload]
        exists = os.path.lexists(root)
        legacy_roots[name] = {"path": str(root), "exists": exists}
        if not exists:
            warnings.append(_warning("legacy_data_root_absent", root=name, path=str(root), detail="退役程式舊資料目錄不存在；現況觀察無法判斷是否於退役時刪除或搬移"))
    checks["legacy_data_roots"] = legacy_roots
    central_checks = central.get("checks") if isinstance(central.get("checks"), Mapping) else {}
    head = central_checks.get("published_head") if isinstance(central_checks, Mapping) and isinstance(central_checks.get("published_head"), Mapping) else {}
    checks["central_publication"] = {
        "reused_from": "central_publication.checks.published_head",
        "pass": head.get("pass") is True,
        "reason": head.get("reason"),
        "head_sha256": head.get("head_sha256"),
        "run_id": head.get("run_id"),
        "archive_path": head.get("archive_path"),
        "route_objects_verified": len(head.get("routes") or []) if head.get("pass") is True else 0,
    }
    if head.get("pass") is not True:
        failures.append({"reason": "data_retention_central_publication_not_intact", "check": "central_publication", "central_reason": head.get("reason")})
    return _gov_finish(result, failures)


# --------------------------------------------------------------------------
# payload
# --------------------------------------------------------------------------


def _retained_payload(
    args: argparse.Namespace,
    *,
    task_query: Callable[[Sequence[str]], Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Build the app-neutral acceptance snapshot.

    Every component keeps its own status (pass / blocked / warning /
    not_applicable).  Only ``blocking`` components make the snapshot blocked;
    warnings and labels are listed with the data time actually read.
    """
    fixture_root = args.fixture_root.resolve() if args.fixture_root else None
    paths = _retained_paths(fixture_root)
    now = datetime.now(timezone.utc)
    wanted = {str(item).strip() for item in (getattr(args, "only_program", None) or ()) if str(item).strip()}
    unknown = wanted - set(RETAINED_COMPONENT_IDS)
    if unknown:
        raise ValueError(f"unknown retained component id(s): {', '.join(sorted(unknown))}")
    if task_query is None:
        task_query = (lambda names: _fixture_task_query(paths["scheduled_tasks"])) if fixture_root else _query_scheduled_tasks
    query = task_query((MIX_TASK_NAME, *LEGACY_TASK_NAMES))
    central = _retained_central_component(paths, now)
    central_context: dict[str, object] = {
        "run_id": central.get("run_id"),
        "expected_asof": central.get("market_asof"),
        "head_sha256": central.get("head_sha256"),
        "registry_sha256": central.get("registry_sha256"),
    }
    union = central.get("route_union") if isinstance(central.get("route_union"), Mapping) else None
    scheduler = _retained_scheduler_component(paths, query)
    mix = _retained_mix_component(paths, central_context, now)
    mitake = _retained_mitake_component(mix)
    single = _retained_single_component(paths)
    telegram = _retained_telegram_component(paths, central_context, now)
    governance = _retained_governance_config(paths, fixture_root)
    allowlist, allowlist_check = _governance_allowlist(paths["quarantine_allowlist"], governance["retired_roots"])  # type: ignore[arg-type]
    retention = _retained_retention_component(paths, governance, allowlist, allowlist_check, central, union)  # type: ignore[arg-type]
    retirement = _retained_retirement_component(paths, governance, allowlist, allowlist_check, query, union)  # type: ignore[arg-type]
    schedule = _retained_schedule_component(query)
    checks: dict[str, dict[str, object]] = {
        "central_publication": central,
        "workflow_scheduler": scheduler,
        "mix_analysis_report": mix,
        "mix_mitake_persistence": mitake,
        "single_stock_analysis_report": single,
        "telegram_service_publication": telegram,
        "data_retention_guard": retention,
        "retirement_isolation": retirement,
        "schedule_reconciliation": schedule,
    }
    selected_ids = tuple(item for item in RETAINED_COMPONENT_IDS if not wanted or item in wanted)
    selected_checks = {item: checks[item] for item in selected_ids}
    blocking_failures = [
        {"component_id": component_id, "code": str(evidence.get("reason") or component_id), **evidence}
        for component_id, evidence in selected_checks.items()
        if evidence.get("blocking") is True or evidence.get("status") == "blocked"
    ]
    output_root = str(args.output.resolve()) if args.output is not None else str((fixture_root / "acceptance.latest.json") if fixture_root else DEFAULT_OUTPUT)
    return {
        "schema": SCHEMA,
        "schema_version": 2,
        "generated_at": now.isoformat(),
        "mode": RETAINED_MODE,
        "fixture": bool(fixture_root),
        "read_only_scope": {
            "real_systems_are_never_started": True,
            "real_systems_are_never_written": True,
            "scheduled_tasks_are_only_queried": True,
            "external_services_called": False,
            "output_path_if_reports": output_root if args.reports else None,
        },
        "criteria": {
            "active_components": list(RETAINED_COMPONENT_IDS),
            "selected_components": list(selected_ids),
            "retired_consumers_not_required": list(RETIRED_CONSUMER_IDS),
            "central_profile": CENTRAL_PROFILE_ID,
            "central_registry_schema": CENTRAL_REQUIREMENTS_REGISTRY_SCHEMA,
            "central_head": CENTRAL_HEAD_RELATIVE,
            "central_head_schema": CENTRAL_HEAD_SCHEMA,
            "central_route_union": union,
            "central_consumer_mappings_are_non_authoritative": True,
            "unique_acceptance_output": str(DEFAULT_OUTPUT),
            "freshness_window_hours": RETAINED_FRESHNESS_HOURS,
            "mix_report_pointer": MIX_C0_POINTER_NAME,
            "mix_report_pointer_schema": MIX_C0_POINTER_SCHEMA,
            "mix_delivery_state_schema": MIX_C0_DELIVERY_STATE_SCHEMA,
            "mix_task_name": MIX_TASK_NAME,
            "mitake": "not_applicable_c0",
            "single_stock_report_schema": SINGLE_C0_REPORT_SCHEMA,
            "freshness_version_binding_differences_are_warnings": True,
            "integrity_tamper_etf_exclusion_block": True,
            "no_fixture_latest_or_legacy_fallback": True,
            "governance_observation_basis": GOVERNANCE_OBSERVATION_BASIS,
            "quarantine_allowlist": str(paths["quarantine_allowlist"]),
        },
        "overall_status": "pass" if not blocking_failures else "blocked",
        "non_blocking_unverified": [
            {"component_id": component_id, "status": evidence.get("status"), "reason": evidence.get("reason")}
            for component_id, evidence in selected_checks.items()
            if evidence.get("status") in {"warning", "not_applicable"}
        ],
        "warnings": [
            {"component_id": component_id, **warning}
            for component_id, evidence in selected_checks.items()
            for warning in (evidence.get("warnings") or [])
            if isinstance(warning, Mapping)
        ],
        "blocking_failures": blocking_failures,
        "components": selected_checks,
        "checks": selected_checks,
        "evidence_paths": {name: str(path) for name, path in paths.items()},
    }


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only app-neutral acceptance for the current C0 stock workflow.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--structure-only", action="store_true", help="evaluate every component; never writes output")
    mode.add_argument("--central-mix-live", action="store_true", help="compatibility alias of --structure-only (the v1 live path is retired); never writes output")
    parser.add_argument(
        "--reports",
        action="store_true",
        help="evaluate every component and write the canonical acceptance snapshot",
    )
    parser.add_argument("--fixture-root", type=Path, help="test-only evidence root; never reads production paths or queries the Task Scheduler")
    parser.add_argument("--only", action="append", dest="only_program", help="limit checks to a retained component id; repeatable")
    parser.add_argument("--output", type=Path, help="fixture-only output override; production output is fixed")
    parser.add_argument("--json", action="store_true", help="print the complete result JSON to stdout")
    args = parser.parse_args(argv)
    if args.structure_only and args.reports:
        parser.error("--structure-only cannot be combined with --reports")
    if args.central_mix_live and args.reports:
        parser.error("--central-mix-live is read-only and cannot write acceptance output")
    if args.output and not args.fixture_root:
        parser.error("--output is allowed only with --fixture-root")
    if args.output and args.fixture_root and not _relative_to(args.output, args.fixture_root):
        parser.error("fixture output must stay under --fixture-root")
    if not args.structure_only and not args.central_mix_live and not args.reports:
        args.reports = True
    return args


def _print_summary(payload: Mapping[str, object]) -> None:
    print(f"overall_status={payload['overall_status']} mode={payload['mode']}")
    checks = payload.get("components", payload.get("checks", {}))
    for check_name, check in (checks.items() if isinstance(checks, Mapping) else ()):
        status = str(check.get("status") or "unknown").upper() if isinstance(check, Mapping) else "UNKNOWN"
        reason = check.get("reason") if isinstance(check, Mapping) else None
        warning_count = len(check.get("warnings") or []) if isinstance(check, Mapping) else 0
        print(f"[{status}] {check_name}" + (f" reason={reason}" if reason else "") + (f" warnings={warning_count}" if warning_count else ""))
    if payload.get("blocking_failures"):
        print(f"  - blocking_failures={len(payload['blocking_failures'])}")


def _write_report(payload: Mapping[str, object], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _build_payload(args: argparse.Namespace) -> dict[str, object]:
    return _retained_payload(args)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        payload = _build_payload(args)
    except (OSError, ValueError) as exc:
        print(f"configuration_error={exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_summary(payload)

    if args.reports:
        if args.fixture_root:
            output = args.output or (args.fixture_root.resolve() / "acceptance.latest.json")
        else:
            output = DEFAULT_OUTPUT
        try:
            _write_report(payload, output)
        except OSError as exc:
            print(f"output_error={output}: {exc}", file=sys.stderr)
            return 2
        print(f"acceptance_json={output}")
    return 0 if payload["overall_status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
