"""Validate/rebuild a PriceAdj commit snapshot without touching live data.

Default mode is read-only.  ``--apply`` is required before writing, and the
destination must be a new app-owned ``dstock_daily`` migration directory.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

APP_DAILY_ROOT = Path(r"D:\TWStockData\program_data\dstock_daily").resolve(strict=False)
CENTRAL_RAW_ROOT = Path(r"D:\TWStockData\shared_market_data").resolve(strict=False)
STOCK_DATA_ROOT = Path(r"D:\stock\data").resolve(strict=False)


class MigrationError(RuntimeError):
    pass


class CentralOwnerOnlyError(MigrationError):
    """Raised because legacy/migration snapshots are not D-stock inputs."""

    error_class = "central_owner_only"

    def __init__(self, operation: str) -> None:
        self.payload = {
            "error_class": self.error_class,
            "operation": str(operation),
            "owner": "D:\\TWStockData\\services\\market_update",
            "network": False,
            "writes": False,
        }
        super().__init__(f"central_owner_only:{operation}")


def _central_owner_only(operation: str) -> None:
    raise CentralOwnerOnlyError(operation)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe(path: Path | str, label: str) -> Path:
    value = Path(path).expanduser().resolve(strict=False)
    temp_text = os.environ.get("TEMP") or os.environ.get("TMP")
    forbidden = [CENTRAL_RAW_ROOT, STOCK_DATA_ROOT]
    if temp_text:
        forbidden.append(Path(temp_text).resolve(strict=False))
    if any(_inside(value, root) for root in forbidden):
        raise MigrationError(f"{label} is forbidden: {value}")
    return value


def _regular(path: Path, label: str) -> Path:
    value = _safe(path, label)
    if value.is_symlink() or not value.is_file():
        raise MigrationError(f"{label} must be a regular file: {value}")
    return value


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationError(f"{label} is invalid JSON: {path}") from exc


def _bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def _date(value: Any) -> str:
    text = str(value or "").strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise MigrationError(f"invalid session date: {text!r}") from exc


def _generation_path(cache_root: Path, raw_name: Any) -> Path:
    name = str(raw_name or "").strip()
    path = _safe(cache_root / name, "old generation")
    if path.parent != cache_root or path.name != name:
        raise MigrationError(f"generation path escapes old cache root: {name}")
    return _regular(path, "old generation")


def _validated_sessions(manifest: Mapping[str, Any], cache_root: Path) -> list[dict[str, Any]]:
    _central_owner_only("price_adj_migration_old_cache")
    raw_sessions = manifest.get("sessions")
    if not isinstance(raw_sessions, list) or not raw_sessions:
        raise MigrationError("old PriceAdj manifest has no sessions")
    result: list[dict[str, Any]] = []
    for raw_session in raw_sessions:
        if not isinstance(raw_session, Mapping) or raw_session.get("status") != "committed":
            raise MigrationError("only committed PriceAdj sessions are admissible")
        session_date = _date(raw_session.get("date"))
        names = raw_session.get("file_names")
        if not isinstance(names, list) or len(names) != 1:
            raise MigrationError(f"session {session_date} file_names is invalid")
        source_path = _generation_path(cache_root, names[0])
        payload = _read_json(source_path, "old generation")
        if not isinstance(payload, Mapping) or set(payload) not in {MARKET_PAYLOAD_FIELDS, MARKET_LEGACY_PAYLOAD_FIELDS}:
            raise MigrationError(f"generation schema is not admitted: {source_path}")
        if payload.get("schema_version") != MARKET_GENERATION_SCHEMA or payload.get("update_mode") != "market":
            raise MigrationError(f"generation contract mismatch: {source_path}")
        params = payload.get("params")
        required_codes = payload.get("required_codes")
        required_markets = _normalize_markets(payload.get("required_markets"))
        if not isinstance(params, Mapping) or dict(params) != _market_params(session_date):
            raise MigrationError(f"generation request mismatch: {source_path}")
        if not isinstance(required_codes, list) or required_markets is None or list(required_markets) != required_codes:
            raise MigrationError(f"generation universe mismatch: {source_path}")
        rows = _validate_rows(payload.get("data"), session_date, required_codes, required_markets)
        coverage = _validate_coverage(payload.get("coverage"), rows, required_codes, required_markets, session_date)
        if not coverage.get("coverage_ok"):
            raise MigrationError(f"generation coverage is incomplete: {source_path}")
        request_trace = _validate_request_trace(payload.get("request_trace"), params)
        info = payload.get("info_provenance") if "info_provenance" in payload else None
        generation_id = (
            _generation_id(session_date, params, rows, required_codes, required_markets, coverage, request_trace, info)
            if info is not None
            else _legacy_generation_id(session_date, params, rows, required_codes, required_markets, coverage, request_trace)
        )
        if payload.get("generation_id") != generation_id:
            raise MigrationError(f"generation identity mismatch: {source_path}")
        committed_at = str(raw_session.get("committed_at") or payload.get("fetched_at") or "").strip()
        if not committed_at:
            raise MigrationError(f"session {session_date} committed_at missing")
        result.append(
            {
                "date": session_date,
                "payload": dict(payload),
                "rows": rows,
                "coverage": dict(coverage),
                "request_trace": request_trace,
                "required_codes": list(required_codes),
                "required_markets": dict(required_markets),
                "info_provenance": info,
                "generation_id": generation_id,
                "committed_at": committed_at,
            }
        )
    return result


def _write_rebuild(items: list[dict[str, Any]], old_manifest: Path, output_root: Path) -> None:
    _central_owner_only("price_adj_migration_rebuild")
    output_root = _safe(output_root, "output root")
    if not _inside(output_root, APP_DAILY_ROOT):
        raise MigrationError(f"output root must be a new app-owned dstock_daily path: {output_root}")
    if output_root.exists():
        raise MigrationError(f"refusing to overwrite output: {output_root}")
    cache_root = output_root / "cache" / "price_adj_market"
    commit_root = cache_root / "commits"
    state_root = output_root / "state" / "price_adj_updater" / "market"
    commit_root.mkdir(parents=True)
    state_root.mkdir(parents=True)
    sessions: list[dict[str, Any]] = []
    for item in items:
        name = _generation_file_name(item["date"], item["generation_id"])
        payload = dict(item["payload"])
        payload.update({"schema_version": MARKET_GENERATION_SCHEMA, "session_date": item["date"], "generation_id": item["generation_id"], "data": item["rows"], "row_count": len(item["rows"])})
        generation_raw = _bytes(payload)
        (cache_root / name).write_bytes(generation_raw)
        file_sha = hashlib.sha256(generation_raw).hexdigest()
        generation_sha = _generation_sha256(item["generation_id"], name, item["rows"], item["request_trace"])
        checkpoint = _checkpoint(item["date"], payload["params"], item["generation_id"], name, file_sha, generation_sha, len(item["rows"]), item["required_codes"], item["required_markets"], item["coverage"], item["request_trace"], item["info_provenance"], item["committed_at"])
        session: dict[str, Any] = {
            "date": item["date"], "status": "committed", "generation_id": item["generation_id"], "params": dict(payload["params"]),
            "request_fingerprint": payload["request_fingerprint"], "required_codes": item["required_codes"], "required_markets": item["required_markets"],
            "file_names": [name], "coverage": item["coverage"], "request_count": 1, "request_trace": item["request_trace"],
            "fetched_at": payload["fetched_at"], "committed_at": item["committed_at"], "generation_file_sha256": file_sha,
            "generation_sha256": generation_sha, "row_count": len(item["rows"]),
        }
        descriptor: dict[str, Any] = {
            "schema_version": MARKET_COMMIT_SCHEMA, "date": item["date"], "generation_id": item["generation_id"], "generation_file_name": name,
            "generation_file_sha256": file_sha, "generation_sha256": generation_sha, "row_count": len(item["rows"]),
            "required_codes_sha256": _canonical_sha256(item["required_codes"]), "required_markets_sha256": _canonical_sha256(item["required_markets"]),
            "checkpoint_sha256": _canonical_sha256(checkpoint), "committed_at": item["committed_at"],
        }
        if item["info_provenance"] is not None:
            session.update({"checkpoint": checkpoint, "info_provenance": dict(item["info_provenance"])})
            descriptor["info_provenance"] = dict(item["info_provenance"])
        descriptor["manifest_session_sha256"] = _canonical_sha256(session)
        (commit_root / f"price_adj_market_{item['date'].replace('-', '')}_{item['generation_id'][:16]}.commit.json").write_bytes(_bytes(descriptor))
        sessions.append(session)
    dates = sorted(item["date"] for item in sessions)
    manifest = {
        "mode": "market", "plan_fingerprint": _canonical_sha256({"source_manifest_sha256": hashlib.sha256(old_manifest.read_bytes()).hexdigest()}),
        "range_start": dates[0], "range_end": dates[-1], "schema_version": MARKET_GENERATION_SCHEMA,
        "sessions": sessions, "target": dates[-1], "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (state_root / "manifest.json").write_bytes(_bytes(manifest))


def main(argv: list[str] | None = None) -> int:
    try:
        del argv
        _central_owner_only("price_adj_migration_rebuild")
    except CentralOwnerOnlyError as exc:
        print(json.dumps({"status": "fail_closed", **exc.payload}, ensure_ascii=False, sort_keys=True))
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
