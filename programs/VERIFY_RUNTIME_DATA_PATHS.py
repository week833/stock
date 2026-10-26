#!/usr/bin/env python3
"""Fast cross-project runtime data-path health check.

This check intentionally validates path resolution, access and one JSON sample
per required source; sources the updater publishes are proven through its
publication pointer instead.  It does not hash the full shared-data tree; use
``VERIFY_SHARED_REFERENCE_DATA.ps1`` when a deep integrity audit is required.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


PROGRAMS_DIR = Path(__file__).resolve().parent
SHARED_DIR = PROGRAMS_DIR / "shared"
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

from path_contract import CONTRACT_PATH, PathContractError, load_path_contract


EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_WARN = 2

PATH_CONTRACT = load_path_contract()
DEFAULT_STOCK_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MIX_ROOT = Path(r"D:\MIX All")
DEFAULT_RESULT_PATH = PATH_CONTRACT.daily("state", "runtime_path_health_latest.json")

FORBIDDEN_SOURCE_COMPONENTS = frozenset(
    {"backup", "backups", "output", "outputs", "report", "reports"}
)

# The updater publishes every shared raw route as one content-addressed object
# below ``_objects`` and advertises the current set through this pointer.  The
# per-category directories keep only what older producers left behind, so a
# legacy file there is not evidence about the data consumers actually read.
PUBLICATION_POINTER = Path("_metadata") / "stock_data_current.json"
PUBLICATION_SCHEMA = "stock-data-publication-v1"
# shared/stock_data_paths.py reads v1 and v2 heads alike (entry-level
# path/sha256/size are identical), so this gate must not turn red the day the
# updater starts writing v2.
PUBLICATION_SCHEMAS = frozenset({PUBLICATION_SCHEMA, "stock-data-publication-v2"})
PUBLICATION_OBJECT_DIRECTORY = "_objects"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# Per-route ``status`` in the pointer.  The updater only carries SUCCESS and
# PARTIAL into the head (FAILED / BLOCKED / WAITING_QUOTA / EMPTY never become
# a published route), so those two are the whole vocabulary; anything else is
# a malformed pointer and fails closed.
#   SUCCESS  complete as declared by the updater: nothing to report.
#   PARTIAL  the object is published and readable, but the updater declares
#            known coverage gaps.  This gate answers "can consumers resolve and
#            read the data", not "is the data complete" (completeness belongs
#            to VALIDATE_MARKET_DATA_QUALITY and the per-route coverage
#            evidence), so by default PARTIAL passes but is always reported:
#            an INFO issue per route, the route list in the result JSON
#            (publication.partial_routes) and the status on each path entry.
#            ``--strict-publication`` turns every PARTIAL route into an error.
PUBLICATION_STATUS_COMPLETE = "SUCCESS"
PUBLICATION_STATUS_PARTIAL = "PARTIAL"

STOCK_LEGACY_RELATIVE_PATHS = (
    Path("data/history_cache"),
    Path("data/market_calendar"),
    Path("data/industry_operational_cache"),
    Path("data/trading_restrictions"),
    Path("data/broker_branch_cache"),
    Path("data/broker_branch_import"),
    Path("data/broker_branch_source_cache"),
    Path("data/finlab_broker_cache"),
    Path("data/single_stock_3m_cache/finmind"),
    Path("data/single_stock_3m_cache/industry_official"),
)

MIX_LEGACY_RELATIVE_PATHS = (
    Path("data/history_cache"),
    Path("data/finmind_cache"),
    Path("data/external"),
)

OBSOLETE_RESIDUE_PATHS = (
    *(
        (f"stock_legacy_{index}", "stock", relative)
        for index, relative in enumerate(STOCK_LEGACY_RELATIVE_PATHS, start=1)
    ),
    *(
        (f"mix_legacy_{index}", "mix", relative)
        for index, relative in enumerate(MIX_LEGACY_RELATIVE_PATHS, start=1)
    ),
)


@dataclass(frozen=True)
class CategorySpec:
    key: str
    required_directory: bool = True
    json_pattern: str | None = None
    recursive: bool = False
    publication_route: str | None = None


CATEGORY_SPECS = (
    CategorySpec("prices_history", json_pattern="*.json"),
    CategorySpec("providers_finmind_json", publication_route="finmind_stock_info"),
    CategorySpec("providers_global_markets", publication_route="global_market_history"),
    CategorySpec("official_calendar", publication_route="official_calendar"),
    # This is a rebuildable D-stock diagnostic cache, not a Central-published
    # shared-raw category.  A missing cache must remain observable in the
    # report, but must not make the canonical shared-data health gate fail.
    CategorySpec(
        "official_industry_operational",
        required_directory=False,
    ),
    CategorySpec("official_trading_restrictions", publication_route="official_trading_restrictions"),
    CategorySpec("official_market_daily", publication_route="official_market_daily"),
    CategorySpec("official_sector_features", publication_route="official_sector_features"),
    CategorySpec("brokers_normalized_cache", required_directory=False),
    CategorySpec("brokers_imports", required_directory=False),
    CategorySpec("brokers_provider_raw", required_directory=False),
    CategorySpec("brokers_finlab_cache", required_directory=False),
)

# These three paths are Stock-owned derived data.  They are intentionally
# stored in the registry's ``project_local_data`` object and resolved from the
# central path contract's dstock_daily namespace, rather than being treated as
# raw categories below canonical_shared.  Keeping this distinction explicit
# prevents a missing derived cache from silently becoming a legacy fallback.
DERIVED_CATEGORY_PATHS = {
    "prices_history": ("prices", "history"),
    "official_industry_operational": ("industry_operational",),
    "brokers_normalized_cache": ("brokers", "normalized_cache"),
}


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(_resolved(left))) == os.path.normcase(
        str(_resolved(right))
    )


def _is_under(path: Path, root: Path, *, allow_root: bool = True) -> bool:
    candidate = _resolved(path)
    boundary = _resolved(root)
    try:
        relative = candidate.relative_to(boundary)
    except ValueError:
        return False
    return allow_root or bool(relative.parts)


def _legacy_paths(stock_root: Path, mix_root: Path) -> tuple[Path, ...]:
    return tuple(_resolved(stock_root / item) for item in STOCK_LEGACY_RELATIVE_PATHS) + tuple(
        _resolved(mix_root / item) for item in MIX_LEGACY_RELATIVE_PATHS
    )


def _path_is_forbidden(path: Path, stock_root: Path, mix_root: Path) -> str | None:
    candidate = _resolved(path)
    lowered_parts = {part.casefold() for part in candidate.parts}
    blocked_parts = sorted(lowered_parts & FORBIDDEN_SOURCE_COMPONENTS)
    if blocked_parts:
        return f"來源路徑包含禁止的產物目錄：{', '.join(blocked_parts)}"
    for legacy in _legacy_paths(stock_root, mix_root):
        if _same_path(candidate, legacy) or _is_under(candidate, legacy):
            return f"來源路徑解析到 legacy 資料樹：{legacy}"
    return None


def _check_obsolete_residue(
    report: "HealthReport",
    *,
    stock_root: Path,
    mix_root: Path,
) -> None:
    roots = {"stock": stock_root, "mix": mix_root}
    for label, root_name, relative in OBSOLETE_RESIDUE_PATHS:
        path = _resolved(roots[root_name] / relative)
        if not path.exists():
            continue
        report.issue(
            "error",
            "obsolete_backup_residue_present",
            "已停用的 legacy 資料路徑重新出現；正式 runtime 不應依賴或重建此路徑。",
            path=path,
            context=label,
        )


class HealthReport:
    def __init__(
        self,
        stock_root: Path,
        mix_root: Path,
        *,
        strict_publication: bool = False,
    ) -> None:
        self.strict_publication = strict_publication
        self.publication_routes: dict[str, Mapping[str, Any]] | None = None
        self.publication_loaded = False
        self.payload: dict[str, Any] = {
            "schema_version": 1,
            "check_type": "runtime_data_paths_fast",
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "completed_at_utc": None,
            "stock_root": str(stock_root),
            "mix_root": str(mix_root),
            "status": "running",
            "exit_code": None,
            "stock_registry": {},
            "publication": {},
            "resolved_paths": [],
            "mix_configs": [],
            "issues": [],
            "totals": {
                "paths_checked": 0,
                "json_samples_checked": 0,
                "mix_configs_checked": 0,
                "errors": 0,
                "warnings": 0,
                "notices": 0,
            },
        }

    def issue(
        self,
        severity: str,
        code: str,
        message: str,
        *,
        path: Path | str | None = None,
        context: str | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "severity": severity,
            "code": code,
            "message": message,
        }
        if path is not None:
            entry["path"] = str(path)
        if context:
            entry["context"] = context
        self.payload["issues"].append(entry)
        # "info" is reported but never changes the exit code.
        counter = {"error": "errors", "info": "notices"}.get(severity, "warnings")
        self.payload["totals"][counter] += 1

    def add_path(self, entry: Mapping[str, Any]) -> None:
        self.payload["resolved_paths"].append(dict(entry))
        self.payload["totals"]["paths_checked"] += 1

    def finish(self) -> int:
        errors = int(self.payload["totals"]["errors"])
        warnings = int(self.payload["totals"]["warnings"])
        if errors:
            code = EXIT_FAIL
            status = "fail"
        elif warnings:
            code = EXIT_WARN
            status = "warn"
        else:
            code = EXIT_PASS
            status = "pass"
        self.payload["status"] = status
        self.payload["exit_code"] = code
        self.payload["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        return code


def _load_json(
    path: Path,
    report: HealthReport,
    *,
    code: str,
    context: str,
) -> Any | None:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except FileNotFoundError:
        report.issue("error", code, "JSON 檔案不存在。", path=path, context=context)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        report.issue(
            "error",
            code,
            f"JSON 無法讀取或格式無效：{exc}",
            path=path,
            context=context,
        )
    return None


def _latest_file(
    directory: Path,
    pattern: str,
    *,
    recursive: bool,
) -> tuple[Path | None, int]:
    iterator: Iterable[Path] = (
        directory.rglob(pattern) if recursive else directory.glob(pattern)
    )
    latest: Path | None = None
    latest_mtime = -1
    count = 0
    for candidate in iterator:
        try:
            if not candidate.is_file():
                continue
            stat = candidate.stat()
        except OSError:
            continue
        count += 1
        if stat.st_mtime_ns > latest_mtime:
            latest = candidate
            latest_mtime = stat.st_mtime_ns
    return latest, count


def _load_publication_routes(
    report: HealthReport,
    shared_root: Path,
) -> Mapping[str, Mapping[str, Any]] | None:
    """Read the common publication pointer once and index its routes."""

    if report.publication_loaded:
        return report.publication_routes
    report.publication_loaded = True
    pointer = _resolved(shared_root / PUBLICATION_POINTER)
    document = _load_json(
        pointer,
        report,
        code="publication_pointer_invalid",
        context="stock_publication",
    )
    if document is None:
        return None
    report.payload["totals"]["json_samples_checked"] += 1
    datasets = document.get("datasets") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or document.get("schema") not in PUBLICATION_SCHEMAS
        or not isinstance(datasets, list)
        or not all(isinstance(item, dict) for item in datasets)
    ):
        report.issue(
            "error",
            "publication_pointer_invalid",
            f"共用資料發布指標必須是 {' 或 '.join(sorted(PUBLICATION_SCHEMAS))} 且含 datasets 清單。",
            path=pointer,
            context="stock_publication",
        )
        return None
    routes: dict[str, Mapping[str, Any]] = {}
    for item in datasets:
        route_id = item.get("route_id")
        if not isinstance(route_id, str) or not route_id.strip() or route_id in routes:
            report.issue(
                "error",
                "publication_pointer_invalid",
                "共用資料發布指標的 route_id 必須是不重複的非空字串。",
                path=pointer,
                context="stock_publication",
            )
            return None
        routes[route_id] = item
    report.publication_routes = routes
    report.payload["publication"] = {
        "pointer": str(pointer),
        "schema": document.get("schema"),
        "producer_id": document.get("producer_id"),
        "run_id": document.get("run_id"),
        "published_at": document.get("published_at"),
        "route_count": len(routes),
        "partial_routes": [],
    }
    return routes


def _check_publication_route(
    report: HealthReport,
    *,
    role: str,
    shared_root: Path,
    route_id: str,
    entry: dict[str, Any],
    severity: str,
) -> None:
    """Prove one published route is present as an intact content-addressed object.

    The objects can be many gigabytes, so this is a stat-level check: the
    pointer names the object, the object lives below ``_objects`` under its own
    digest, and its size is the declared one.  Content hashing is left to the
    deep audit.
    """

    routes = _load_publication_routes(report, shared_root)
    if routes is None:
        entry["status"] = "fail"
        return
    item = routes.get(route_id)
    if item is None:
        entry["status"] = "fail" if severity == "error" else "warn"
        report.issue(
            severity,
            "publication_route_missing",
            f"共用資料發布指標沒有路由 {route_id}。",
            path=shared_root / PUBLICATION_POINTER,
            context=role,
        )
        return
    entry["publication_route"] = route_id
    entry["publication_status"] = item.get("status")
    digest = item.get("sha256")
    size = item.get("size")
    relative = item.get("path")
    object_root = _resolved(shared_root / PUBLICATION_OBJECT_DIRECTORY)
    problem: str | None = None
    object_path: Path | None = None
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        problem = "發布物件的 sha256 無效。"
    elif type(size) is not int or size < 0:
        problem = "發布物件的 size 無效。"
    elif not isinstance(relative, str) or not relative.strip():
        problem = "發布物件缺少路徑。"
    else:
        object_path = _resolved(shared_root / relative.strip())
        if (
            not _is_under(object_path, object_root, allow_root=False)
            or object_path.name != f"{digest}.json"
        ):
            problem = "發布物件不在 _objects 內或檔名不是自身 sha256。"
        elif not object_path.is_file():
            problem = "發布物件不存在。"
        else:
            try:
                observed_size = object_path.stat().st_size
            except OSError:
                observed_size = -1
            if observed_size != size:
                problem = "發布物件大小與指標宣告不符。"
    if problem is not None:
        entry["status"] = "fail" if severity == "error" else "warn"
        report.issue(
            severity,
            "publication_object_invalid",
            f"{route_id}：{problem}",
            path=object_path or (shared_root / PUBLICATION_POINTER),
            context=role,
        )
        return
    entry["publication_object"] = str(object_path)
    status = item.get("status")
    if status == PUBLICATION_STATUS_COMPLETE:
        return
    if status == PUBLICATION_STATUS_PARTIAL:
        entry["publication_partial"] = True
        report.payload["publication"]["partial_routes"].append(route_id)
        if report.strict_publication:
            entry["status"] = "fail" if severity == "error" else "warn"
            report.issue(
                severity,
                "publication_route_partial",
                f"{route_id}：updater 宣告此路由為 PARTIAL（已知覆蓋缺口），"
                "--strict-publication 不接受。",
                path=object_path,
                context=role,
            )
        else:
            report.issue(
                "info",
                "publication_route_partial",
                f"{route_id}：物件可讀且大小相符，但 updater 宣告為 PARTIAL（已知覆蓋缺口）；"
                "本檢查只確認可讀，資料完整度請看 VALIDATE_MARKET_DATA_QUALITY。",
                path=object_path,
                context=role,
            )
        return
    entry["status"] = "fail" if severity == "error" else "warn"
    report.issue(
        severity,
        "publication_route_status_invalid",
        f"{route_id}：發布指標的 status 必須是 "
        f"{PUBLICATION_STATUS_COMPLETE} 或 {PUBLICATION_STATUS_PARTIAL}，收到 {status!r}。",
        path=object_path,
        context=role,
    )


def _check_source_directory(
    report: HealthReport,
    *,
    role: str,
    configured: str,
    path: Path,
    spec: CategorySpec,
    stock_root: Path,
    mix_root: Path,
    shared_root: Path,
) -> None:
    resolved = _resolved(path)
    forbidden = _path_is_forbidden(resolved, stock_root, mix_root)
    exists = resolved.is_dir()
    readable = exists and os.access(resolved, os.R_OK)
    writable = exists and os.access(resolved, os.W_OK)
    entry: dict[str, Any] = {
        "role": role,
        "configured_path": configured,
        "resolved_path": str(resolved),
        "required": spec.required_directory,
        "exists": exists,
        "readable": readable,
        "writable_for_stock_owner": writable,
        "json_sample": None,
        "matching_json_files": None,
        "status": "pass",
    }

    severity = "error" if spec.required_directory else "warning"
    if forbidden:
        entry["status"] = "fail"
        report.issue(
            "error",
            "forbidden_source_path",
            forbidden,
            path=resolved,
            context=role,
        )
    elif not exists:
        if spec.required_directory:
            entry["status"] = "fail"
            report.issue(
                "error",
                "source_directory_missing",
                "資料來源目錄不存在。",
                path=resolved,
                context=role,
            )
        else:
            entry["status"] = "optional_missing"
    elif not readable:
        entry["status"] = "fail" if severity == "error" else "warn"
        report.issue(
            severity,
            "source_directory_unreadable",
            "資料來源目錄不可讀。",
            path=resolved,
            context=role,
        )
    elif spec.publication_route:
        _check_publication_route(
            report,
            role=role,
            shared_root=shared_root,
            route_id=spec.publication_route,
            entry=entry,
            severity=severity,
        )
    elif spec.json_pattern:
        try:
            sample, count = _latest_file(
                resolved,
                spec.json_pattern,
                recursive=spec.recursive,
            )
        except OSError as exc:
            sample, count = None, 0
            report.issue(
                "error",
                "source_directory_scan_failed",
                f"無法列舉資料來源：{exc}",
                path=resolved,
                context=role,
            )
        entry["matching_json_files"] = count
        if sample is None:
            entry["status"] = "fail"
            report.issue(
                "error",
                "required_json_sample_missing",
                f"找不到必要 JSON 樣本：{spec.json_pattern}",
                path=resolved,
                context=role,
            )
        else:
            entry["json_sample"] = str(sample)
            before_errors = int(report.payload["totals"]["errors"])
            sample_payload = _load_json(
                sample,
                report,
                code="invalid_runtime_json_sample",
                context=role,
            )
            report.payload["totals"]["json_samples_checked"] += 1
            if sample_payload is None and int(report.payload["totals"]["errors"]) > before_errors:
                entry["status"] = "fail"

    report.add_path(entry)


def _valid_relative_path(raw: Any) -> bool:
    if not isinstance(raw, str) or not raw.strip():
        return False
    path = Path(raw.strip())
    return not path.is_absolute() and ".." not in path.parts


def _process_shared_root_override(environ: Mapping[str, str]) -> str | None:
    """Return a process-level shared-root override without loading project secrets."""

    for key, raw_value in environ.items():
        if str(key).casefold() != "stock_shared_data_root":
            continue
        value = str(raw_value or "").strip()
        return value or None
    return None


def _load_registry_contract(
    report: HealthReport,
    registry: Mapping[str, Any],
    registry_path: Path,
) -> tuple[Any, Path, Path] | None:
    raw_contract_path = registry.get("contract_path")
    if not isinstance(raw_contract_path, str) or not raw_contract_path.strip():
        report.issue(
            "error",
            "stock_registry_contract_missing",
            "Stock registry 必須指定中央 path contract。",
            path=registry_path,
        )
        return None

    configured_contract_path = Path(raw_contract_path.strip()).expanduser()
    if not configured_contract_path.is_absolute():
        report.issue(
            "error",
            "stock_registry_contract_path_invalid",
            "contract_path 必須是中央契約的絕對路徑。",
            path=configured_contract_path,
        )
        return None
    contract_path = _resolved(configured_contract_path)
    if not _same_path(contract_path, CONTRACT_PATH):
        report.issue(
            "error",
            "stock_registry_contract_path_mismatch",
            f"contract_path 必須精確指向中央契約：{CONTRACT_PATH}。",
            path=contract_path,
        )
        return None

    try:
        contract = load_path_contract(contract_path)
    except (PathContractError, OSError, UnicodeError, ValueError, TypeError) as exc:
        report.issue(
            "error",
            "stock_registry_contract_invalid",
            f"中央 path contract 無效：{exc}",
            path=contract_path,
        )
        return None

    contract_id = str(getattr(contract, "contract_id", "") or "").strip()
    expected_contract_id = str(
        getattr(PATH_CONTRACT, "contract_id", "") or ""
    ).strip()
    if not contract_id or not expected_contract_id or contract_id != expected_contract_id:
        report.issue(
            "error",
            "stock_registry_contract_id_mismatch",
            "中央 path contract contract_id 與目前正式契約不一致。",
            path=contract_path,
        )
        return None

    raw_contract_root = getattr(contract, "root", None)
    if raw_contract_root is None:
        report.issue(
            "error",
            "stock_registry_contract_root_invalid",
            "中央 path contract 缺少 root。",
            path=contract_path,
        )
        return None
    try:
        contract_root = _resolved(Path(raw_contract_root))
    except (OSError, TypeError, ValueError) as exc:
        report.issue(
            "error",
            "stock_registry_contract_root_invalid",
            f"中央 path contract root 無效：{exc}",
            path=contract_path,
        )
        return None
    if not _same_path(contract_root, PATH_CONTRACT.root):
        report.issue(
            "error",
            "stock_registry_contract_root_mismatch",
            f"中央 path contract root 必須是 {PATH_CONTRACT.root}。",
            path=contract_root,
        )
        return None
    return contract, contract_path, contract_root


def _load_stock_registry(
    report: HealthReport,
    stock_root: Path,
    mix_root: Path,
    environ: Mapping[str, str],
) -> tuple[Path | None, dict[str, Path], str, str | None]:
    registry_path = stock_root / "config" / "shared_data_paths.json"
    registry = _load_json(
        registry_path,
        report,
        code="stock_registry_invalid",
        context="stock_registry",
    )
    if not isinstance(registry, dict):
        if registry is not None:
            report.issue(
                "error",
                "stock_registry_invalid",
                "Stock registry 根節點必須是 JSON object。",
                path=registry_path,
            )
        return None, {}, "", None
    if registry.get("schema_version") not in (1, 2):
        report.issue(
            "error",
            "stock_registry_schema_invalid",
            "Stock registry schema_version 必須是 1 或 2。",
            path=registry_path,
        )

    contract_info = _load_registry_contract(report, registry, registry_path)
    if contract_info is None:
        return None, {}, "", None
    contract, contract_path, contract_root = contract_info

    raw_namespace = registry.get("managed_namespace")
    managed_namespace = (
        raw_namespace.strip() if isinstance(raw_namespace, str) else ""
    )
    if managed_namespace != "canonical_shared":
        report.issue(
            "error",
            "stock_registry_namespace_invalid",
            "managed_namespace 必須是中央契約的 canonical_shared。",
            path=registry_path,
        )
        return None, {}, "", None

    try:
        shared_root = _resolved(contract.namespace(managed_namespace))
    except (AttributeError, KeyError, OSError, PathContractError, TypeError, ValueError) as exc:
        report.issue(
            "error",
            "stock_registry_namespace_invalid",
            f"中央 path contract 無法解析 managed_namespace：{exc}",
            path=registry_path,
        )
        return None, {}, "", None
    if not _same_path(shared_root, PATH_CONTRACT.canonical_shared):
        report.issue(
            "error",
            "stock_registry_namespace_mismatch",
            f"managed_namespace 必須解析到 {PATH_CONTRACT.canonical_shared}。",
            path=shared_root,
        )
        return None, {}, "", None

    raw_environment_override = registry.get("environment_override")
    env_name = (
        raw_environment_override.strip()
        if isinstance(raw_environment_override, str)
        else ""
    )
    if raw_environment_override is not None and not isinstance(
        raw_environment_override, str
    ):
        env_name = str(raw_environment_override).strip()
    if env_name:
        report.issue(
            "error",
            "stock_registry_override_forbidden",
            "environment_override 必須為 null 或空字串；中央 shared root 不接受環境變數重導向。",
            path=registry_path,
        )

    process_override = _process_shared_root_override(environ)
    if process_override:
        report.issue(
            "error",
            "stock_shared_root_override_forbidden",
            "STOCK_SHARED_DATA_ROOT 環境變數會改寫中央 root，已拒絕。",
            path=Path(process_override).expanduser(),
        )

    legacy_root = registry.get("root_relative_to_project")
    if legacy_root is not None and str(legacy_root).strip():
        configured_legacy_root = Path(str(legacy_root).strip()).expanduser()
        if not configured_legacy_root.is_absolute():
            report.issue(
                "error",
                "stock_registry_legacy_root_invalid",
                "legacy root_relative_to_project 若保留，必須是 canonical root 的絕對路徑。",
                path=registry_path,
            )
            return None, {}, "", None
        resolved_legacy_root = _resolved(configured_legacy_root)
        if not _same_path(resolved_legacy_root, shared_root):
            report.issue(
                "error",
                "stock_registry_legacy_root_mismatch",
                f"legacy root_relative_to_project 必須精確等於 contract canonical root：{shared_root}。",
                path=resolved_legacy_root,
            )
            return None, {}, "", None

    forbidden = _path_is_forbidden(shared_root, stock_root, mix_root)
    if forbidden:
        report.issue(
            "error",
            "forbidden_shared_root",
            forbidden,
            path=shared_root,
            context="stock_registry",
        )

    categories = registry.get("categories")
    if not isinstance(categories, dict):
        report.issue(
            "error",
            "stock_registry_categories_invalid",
            "Stock registry 缺少 categories object。",
            path=registry_path,
        )
        return shared_root, {}, "", None

    project_local_data = registry.get("project_local_data")
    if project_local_data is None:
        project_local_data = {}
    elif not isinstance(project_local_data, dict):
        report.issue(
            "error",
            "stock_registry_project_local_data_invalid",
            "Stock registry 的 project_local_data 必須是 JSON object。",
            path=registry_path,
        )
        project_local_data = {}

    report.payload["stock_registry"] = {
        "path": str(registry_path),
        "contract_path": str(contract_path),
        "contract_id": str(getattr(contract, "contract_id", "")),
        "contract_root": str(contract_root),
        "managed_namespace": managed_namespace,
        "environment_override": env_name,
        "environment_override_active": False,
        "root_source": "contract:canonical_shared",
        "resolved_root": str(shared_root),
    }

    resolved_categories: dict[str, Path] = {}
    for spec in CATEGORY_SPECS:
        raw = categories.get(spec.key)

        # The migration contract keeps these three names for compatibility
        # with older registry consumers, but their authoritative values are
        # app-owned absolute paths under the central dstock_daily namespace.
        # Accept an explicit absolute category value when present; otherwise
        # use the matching project_local_data value.  Relative values remain
        # supported for isolated verifier fixtures only.
        if spec.key in DERIVED_CATEGORY_PATHS and not _valid_relative_path(raw):
            derived_raw: Any = raw
            if not (
                isinstance(derived_raw, str)
                and Path(derived_raw.strip()).expanduser().is_absolute()
            ):
                derived_raw = project_local_data.get(spec.key)
            if not isinstance(derived_raw, str) or not derived_raw.strip():
                report.issue(
                    "error",
                    "stock_registry_derived_category_invalid",
                    "衍生分類必須由 project_local_data 指定中央 dstock_daily 下的絕對路徑。",
                    path=registry_path,
                    context=spec.key,
                )
                continue
            configured_derived = Path(derived_raw.strip()).expanduser()
            if not configured_derived.is_absolute():
                report.issue(
                    "error",
                    "stock_registry_derived_category_invalid",
                    "衍生分類不得使用相對路徑或 legacy fallback。",
                    path=registry_path,
                    context=spec.key,
                )
                continue
            try:
                expected_derived = _resolved(
                    contract.daily(*DERIVED_CATEGORY_PATHS[spec.key])
                )
            except (
                AttributeError,
                KeyError,
                OSError,
                PathContractError,
                TypeError,
                ValueError,
            ) as exc:
                report.issue(
                    "error",
                    "stock_registry_derived_category_contract_invalid",
                    f"中央契約無法解析衍生分類：{exc}",
                    path=registry_path,
                    context=spec.key,
                )
                continue
            resolved_derived = _resolved(configured_derived)
            if not _same_path(resolved_derived, expected_derived):
                report.issue(
                    "error",
                    "stock_registry_derived_category_mismatch",
                    f"衍生分類必須精確指向中央 dstock_daily 路徑：{expected_derived}。",
                    path=resolved_derived,
                    context=spec.key,
                )
                continue
            project_local_value = project_local_data.get(spec.key)
            if project_local_value is not None:
                if not isinstance(project_local_value, str) or not project_local_value.strip():
                    report.issue(
                        "error",
                        "stock_registry_project_local_category_invalid",
                        "衍生分類的 project_local_data 值必須是絕對路徑。",
                        path=registry_path,
                        context=spec.key,
                    )
                    continue
                resolved_project_local = _resolved(
                    Path(project_local_value.strip()).expanduser()
                )
                if not Path(project_local_value.strip()).expanduser().is_absolute():
                    report.issue(
                        "error",
                        "stock_registry_project_local_category_invalid",
                        "衍生分類的 project_local_data 值不得使用相對路徑。",
                        path=registry_path,
                        context=spec.key,
                    )
                    continue
                if not _same_path(resolved_project_local, expected_derived):
                    report.issue(
                        "error",
                        "stock_registry_project_local_category_mismatch",
                        f"project_local_data 衍生分類必須精確指向中央 dstock_daily 路徑：{expected_derived}。",
                        path=resolved_project_local,
                        context=spec.key,
                    )
                    continue
            resolved_categories[spec.key] = resolved_derived
            _check_source_directory(
                report,
                role=f"stock:{spec.key}",
                configured=str(derived_raw),
                path=resolved_derived,
                spec=spec,
                stock_root=stock_root,
                mix_root=mix_root,
                shared_root=shared_root,
            )
            continue

        if not _valid_relative_path(raw):
            report.issue(
                "error",
                "stock_registry_category_invalid",
                "分類必須存在且為 shared root 下的相對路徑。",
                path=registry_path,
                context=spec.key,
            )
            continue
        resolved_category = _resolved(shared_root / str(raw))
        if not _is_under(resolved_category, shared_root, allow_root=False):
            report.issue(
                "error",
                "stock_registry_category_escape",
                "分類路徑超出 shared root。",
                path=resolved_category,
                context=spec.key,
            )
            continue
        resolved_categories[spec.key] = resolved_category
        _check_source_directory(
            report,
            role=f"stock:{spec.key}",
            configured=str(raw),
            path=resolved_category,
            spec=spec,
            stock_root=stock_root,
            mix_root=mix_root,
            shared_root=shared_root,
        )
    return shared_root, resolved_categories, "", None


def _resolve_required_mix_child(
    report: HealthReport,
    *,
    config_name: str,
    field_name: str,
    raw: Any,
    shared_root: Path,
    stock_root: Path,
    mix_root: Path,
) -> Path | None:
    if not _valid_relative_path(raw):
        report.issue(
            "error",
            "mix_data_source_not_relative",
            "MIX data source 必須是 shared_root 下的非空相對路徑。",
            context=f"{config_name}:data_sources.{field_name}",
        )
        return None
    resolved = _resolved(shared_root / str(raw))
    if not _is_under(resolved, shared_root, allow_root=False):
        report.issue(
            "error",
            "mix_data_source_escape",
            "MIX data source 超出 shared_root。",
            path=resolved,
            context=f"{config_name}:data_sources.{field_name}",
        )
        return None
    forbidden = _path_is_forbidden(resolved, stock_root, mix_root)
    if forbidden:
        report.issue(
            "error",
            "forbidden_mix_data_source",
            forbidden,
            path=resolved,
            context=f"{config_name}:data_sources.{field_name}",
        )
    return resolved


def _resolve_external_feature_cache(
    report: HealthReport,
    *,
    config_name: str,
    field_name: str,
    raw: Any,
    external_data: Path,
    default_directory: str,
    stock_root: Path,
    mix_root: Path,
) -> Path | None:
    if raw is None or not str(raw).strip():
        resolved = _resolved(external_data / default_directory)
    else:
        configured = Path(str(raw).strip()).expanduser()
        if configured.is_absolute():
            report.issue(
                "warning",
                "mix_external_cache_hardcoded_absolute",
                "外部資料快取仍使用重複絕對路徑；建議省略或改用 external_data 下相對路徑。",
                path=configured,
                context=f"{config_name}:{field_name}",
            )
            resolved = _resolved(configured)
        elif ".." in configured.parts:
            report.issue(
                "error",
                "mix_external_cache_escape",
                "外部資料快取不得包含 ..。",
                context=f"{config_name}:{field_name}",
            )
            return None
        else:
            resolved = _resolved(external_data / configured)
    if not _is_under(resolved, external_data, allow_root=False):
        report.issue(
            "error",
            "mix_external_cache_escape",
            "外部資料快取必須位於 external_data 之下。",
            path=resolved,
            context=f"{config_name}:{field_name}",
        )
    forbidden = _path_is_forbidden(resolved, stock_root, mix_root)
    if forbidden:
        report.issue(
            "error",
            "forbidden_mix_external_cache",
            forbidden,
            path=resolved,
            context=f"{config_name}:{field_name}",
        )
    return resolved


def _compare_effective_path(
    report: HealthReport,
    *,
    config_name: str,
    role: str,
    actual: Path | None,
    expected: Path | None,
) -> None:
    if actual is None or expected is None:
        return
    if not _same_path(actual, expected):
        report.issue(
            "error",
            "mix_stock_path_mismatch",
            f"MIX effective {role} 與 Stock registry 不一致；預期 {expected}。",
            path=actual,
            context=config_name,
        )


def _check_mix_configs(
    report: HealthReport,
    *,
    stock_root: Path,
    mix_root: Path,
    registry_root: Path | None,
    categories: Mapping[str, Path],
    shared_root_override: str,
    shared_root_override_source: str | None,
) -> None:
    config_dir = mix_root / "config"
    config_files = sorted(config_dir.glob("mix_config*.json"))
    if not config_files:
        report.issue(
            "error",
            "mix_configs_missing",
            "找不到 MIX 正式設定檔。",
            path=config_dir,
        )
        return

    expected_external_candidates = [
        categories.get("official_calendar"),
        categories.get("official_market_daily"),
        categories.get("official_sector_features"),
    ]
    expected_external_parents = [
        _resolved(path.parent)
        for path in expected_external_candidates
        if path is not None
    ]
    expected_external = (
        expected_external_parents[0] if expected_external_parents else None
    )
    for parent in expected_external_parents[1:]:
        if expected_external is not None and not _same_path(parent, expected_external):
            report.issue(
                "error",
                "stock_official_categories_split",
                "Stock official categories 必須共用同一個 official 父目錄。",
                path=parent,
            )

    for config_path in config_files:
        config_name = config_path.name
        payload = _load_json(
            config_path,
            report,
            code="mix_config_invalid",
            context=config_name,
        )
        if not isinstance(payload, dict):
            if payload is not None:
                report.issue(
                    "error",
                    "mix_config_invalid",
                    "MIX config 根節點必須是 JSON object。",
                    path=config_path,
                )
            continue
        data_sources = payload.get("data_sources")
        if not isinstance(data_sources, dict):
            report.issue(
                "error",
                "mix_data_sources_invalid",
                "MIX config 缺少 data_sources object。",
                path=config_path,
            )
            continue

        configured_shared_root = data_sources.get("shared_root")
        if shared_root_override:
            override_path = Path(shared_root_override).expanduser()
            if not override_path.is_absolute():
                report.issue(
                    "error",
                    "stock_shared_root_override_relative",
                    "STOCK_SHARED_DATA_ROOT 必須是絕對路徑。",
                    path=override_path,
                    context=config_name,
                )
                effective_root = None
            else:
                effective_root = _resolved(override_path)
            root_source = shared_root_override_source or "process_env"
        elif isinstance(configured_shared_root, str) and configured_shared_root.strip():
            configured_path = Path(configured_shared_root.strip()).expanduser()
            if not configured_path.is_absolute():
                report.issue(
                    "error",
                    "mix_shared_root_not_absolute",
                    "MIX data_sources.shared_root 必須是絕對路徑。",
                    path=configured_path,
                    context=config_name,
                )
                effective_root = None
            else:
                effective_root = _resolved(configured_path)
            root_source = "config:data_sources.shared_root"
        else:
            report.issue(
                "error",
                "mix_shared_root_missing",
                "MIX config 缺少 data_sources.shared_root。",
                path=config_path,
            )
            effective_root = None
            root_source = "missing"

        config_result: dict[str, Any] = {
            "config": str(config_path),
            "root_source": root_source,
            "configured_shared_root": configured_shared_root,
            "effective_shared_root": str(effective_root) if effective_root else None,
            # MIX v1.4 derives history in its own namespace; it is deliberately
            # absent from data_sources and is not a shared input to validate.
            "history_cache": None,
            "finmind_cache": None,
            "external_data": None,
            "calendar_cache": None,
            "market_daily_cache": None,
            "sector_features_cache": None,
        }

        if effective_root is not None:
            forbidden = _path_is_forbidden(effective_root, stock_root, mix_root)
            if forbidden:
                report.issue(
                    "error",
                    "forbidden_mix_shared_root",
                    forbidden,
                    path=effective_root,
                    context=config_name,
                )
            _compare_effective_path(
                report,
                config_name=config_name,
                role="shared_root",
                actual=effective_root,
                expected=registry_root,
            )
            finmind = _resolve_required_mix_child(
                report,
                config_name=config_name,
                field_name="finmind_cache",
                raw=data_sources.get("finmind_cache"),
                shared_root=effective_root,
                stock_root=stock_root,
                mix_root=mix_root,
            )
            external = _resolve_required_mix_child(
                report,
                config_name=config_name,
                field_name="external_data",
                raw=data_sources.get("external_data"),
                shared_root=effective_root,
                stock_root=stock_root,
                mix_root=mix_root,
            )
            config_result["finmind_cache"] = str(finmind) if finmind else None
            config_result["external_data"] = str(external) if external else None
            _compare_effective_path(
                report,
                config_name=config_name,
                role="finmind_cache",
                actual=finmind,
                expected=categories.get("providers_finmind_json"),
            )
            _compare_effective_path(
                report,
                config_name=config_name,
                role="external_data",
                actual=external,
                expected=expected_external,
            )

            if external is not None:
                calendar_path = _resolved(external / "calendar")
                config_result["calendar_cache"] = str(calendar_path)
                _compare_effective_path(
                    report,
                    config_name=config_name,
                    role="calendar_cache",
                    actual=calendar_path,
                    expected=categories.get("official_calendar"),
                )

                external_features = payload.get("external_features")
                if isinstance(external_features, dict):
                    market_cfg = external_features.get("official_market_daily")
                    if isinstance(market_cfg, dict) and bool(
                        market_cfg.get("enabled", False)
                    ):
                        market_path = _resolve_external_feature_cache(
                            report,
                            config_name=config_name,
                            field_name="official_market_daily.cache_path",
                            raw=market_cfg.get("cache_path"),
                            external_data=external,
                            default_directory="market_daily",
                            stock_root=stock_root,
                            mix_root=mix_root,
                        )
                        config_result["market_daily_cache"] = (
                            str(market_path) if market_path else None
                        )
                        _compare_effective_path(
                            report,
                            config_name=config_name,
                            role="market_daily_cache",
                            actual=market_path,
                            expected=categories.get("official_market_daily"),
                        )

                    if bool(external_features.get("enable_sector_features", False)):
                        sector_path = _resolve_external_feature_cache(
                            report,
                            config_name=config_name,
                            field_name="sector_cache_path",
                            raw=external_features.get("sector_cache_path"),
                            external_data=external,
                            default_directory="sector_features",
                            stock_root=stock_root,
                            mix_root=mix_root,
                        )
                        config_result["sector_features_cache"] = (
                            str(sector_path) if sector_path else None
                        )
                        _compare_effective_path(
                            report,
                            config_name=config_name,
                            role="sector_features_cache",
                            actual=sector_path,
                            expected=categories.get("official_sector_features"),
                        )
                        catalog_raw = external_features.get(
                            "sector_catalog_path",
                            "config/sector_feature_catalog.json",
                        )
                        catalog = Path(str(catalog_raw)).expanduser()
                        if not catalog.is_absolute():
                            catalog = mix_root / catalog
                        catalog = _resolved(catalog)
                        _load_json(
                            catalog,
                            report,
                            code="mix_sector_catalog_invalid",
                            context=config_name,
                        )

        if data_sources.get("read_only") is not True:
            report.issue(
                "error",
                "mix_shared_sources_not_read_only",
                "MIX data_sources.read_only 必須為 true。",
                path=config_path,
            )

        report.payload["mix_configs"].append(config_result)
        report.payload["totals"]["mix_configs_checked"] += 1


def run_health_check(
    *,
    stock_root: Path,
    mix_root: Path,
    environ: Mapping[str, str] | None = None,
    strict_publication: bool = False,
) -> tuple[dict[str, Any], int]:
    resolved_stock = _resolved(stock_root)
    resolved_mix = _resolved(mix_root)
    report = HealthReport(
        resolved_stock,
        resolved_mix,
        strict_publication=strict_publication,
    )

    if not resolved_stock.is_dir():
        report.issue(
            "error",
            "stock_root_missing",
            "Stock 專案根目錄不存在。",
            path=resolved_stock,
        )
    if not resolved_mix.is_dir():
        report.issue(
            "error",
            "mix_root_missing",
            "MIX 專案根目錄不存在。",
            path=resolved_mix,
        )

    _check_obsolete_residue(
        report,
        stock_root=resolved_stock,
        mix_root=resolved_mix,
    )
    active_environ = os.environ if environ is None else environ
    registry_root, categories, override, override_source = _load_stock_registry(
        report,
        resolved_stock,
        resolved_mix,
        active_environ,
    )
    _check_mix_configs(
        report,
        stock_root=resolved_stock,
        mix_root=resolved_mix,
        registry_root=registry_root,
        categories=categories,
        shared_root_override=override,
        shared_root_override_source=override_source,
    )
    exit_code = report.finish()
    return report.payload, exit_code


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    destination = _resolved(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.tmp"
    )
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="快速驗證 D:\\stock 與 D:\\MIX All 的正式執行資料路徑。"
    )
    parser.add_argument("--stock-root", type=Path, default=DEFAULT_STOCK_ROOT)
    parser.add_argument("--mix-root", type=Path, default=DEFAULT_MIX_ROOT)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=f"結果 JSON；預設為 {DEFAULT_RESULT_PATH}。",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="只輸出檢查摘要，不寫 latest JSON。",
    )
    parser.add_argument(
        "--strict-publication",
        action="store_true",
        help="發布指標中 status 為 PARTIAL 的路由視為錯誤（預設只回報、不影響回傳碼）。",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stock_root = _resolved(args.stock_root)
    mix_root = _resolved(args.mix_root)
    output = _resolved(args.output or DEFAULT_RESULT_PATH)
    payload, exit_code = run_health_check(
        stock_root=stock_root,
        mix_root=mix_root,
        strict_publication=args.strict_publication,
    )

    if not args.no_write:
        try:
            _write_json_atomic(output, payload)
        except OSError as exc:
            payload["issues"].append(
                {
                    "severity": "error",
                    "code": "health_result_write_failed",
                    "message": f"無法寫入健康檢查結果：{exc}",
                    "path": str(output),
                }
            )
            payload["totals"]["errors"] = int(payload["totals"]["errors"]) + 1
            payload["status"] = "fail"
            payload["exit_code"] = EXIT_FAIL
            exit_code = EXIT_FAIL

    if not args.quiet:
        label = {EXIT_PASS: "OK", EXIT_WARN: "WARN", EXIT_FAIL: "ERROR"}[exit_code]
        print(
            f"[{label}] Runtime data paths: "
            f"{payload['totals']['paths_checked']} paths, "
            f"{payload['totals']['json_samples_checked']} JSON samples, "
            f"{payload['totals']['mix_configs_checked']} MIX configs."
        )
        print(
            f"[{label}] Errors={payload['totals']['errors']}; "
            f"warnings={payload['totals']['warnings']}; "
            f"notices={payload['totals']['notices']}."
        )
        for issue in payload["issues"][:20]:
            print(
                f"  [{issue['severity'].upper()}] {issue['code']}: "
                f"{issue['message']}"
                + (f" ({issue['path']})" if issue.get("path") else "")
            )
        if len(payload["issues"]) > 20:
            print(f"  ... {len(payload['issues']) - 20} more issues in result JSON.")
        if not args.no_write:
            print(f"Machine-readable result: {output}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
