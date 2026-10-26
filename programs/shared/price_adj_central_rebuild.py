"""Rebuild the D-stock PriceAdj app-owned market snapshot from central data.

The central updater remains the owner of the raw publication.  This tool only
reads the receipt, sidecar, canonical JSON and official market snapshot, then
creates a new versioned D-stock derived snapshot when ``--apply`` is given.
The default operation is a read-only dry-run.  Existing app-owned state is
never replaced.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Iterable, Iterator, Mapping

APP_DAILY_ROOT = Path(r"D:\TWStockData\program_data\dstock_daily").resolve(strict=False)
STATE_ROOT = APP_DAILY_ROOT / "state" / "price_adj_updater"
CENTRAL_ROOT = Path(r"D:\TWStockData\shared_market_data").resolve(strict=False)
CENTRAL_FINMIND = CENTRAL_ROOT / "providers" / "finmind" / "json"
CENTRAL_OFFICIAL = CENTRAL_ROOT / "official" / "market_daily" / "snapshots"
CENTRAL_RECEIPT = Path(
    r"D:\TWStockData\services\market_update\receipts\latest_manifest.json"
).resolve(strict=False)
EXPECTED_RECEIPT_SHA = (
    "6c6e8f172ee66f6033184ea3039f6b83f049156774ed282449afd0a4b26970ef"
)
CENTRAL_DATASET_ID = "finmind_price_adj"
CENTRAL_SIDECAR = CENTRAL_FINMIND / "finmind_price_adj.manifest.json"
CENTRAL_DATA = CENTRAL_FINMIND / "finmind_price_adj.json"
DEFAULT_OLD_MANIFEST = STATE_ROOT / "market" / "manifest.json"
DEFAULT_OLD_CACHE = APP_DAILY_ROOT / "cache" / "price_adj_market"
DEFAULT_OUTPUT = STATE_ROOT / "migration_v20260823_price_adj_v3"


class RebuildError(RuntimeError):
    pass


class CentralOwnerOnlyError(RebuildError):
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
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _regular(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    if resolved.is_symlink() or not resolved.is_file():
        raise RebuildError(f"{label} must be a regular file: {resolved}")
    return resolved


def _read_json(path: Path, label: str) -> Any:
    path = _regular(path, label)
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RebuildError(f"{label} is invalid JSON: {path}") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise RebuildError(f"cannot hash file: {path}") from exc
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _iter_data_rows(path: Path) -> Iterator[Mapping[str, Any]]:
    """Read one central publication once and yield its canonical data rows.

    The publication is a single compact JSON envelope.  A C-level ``json.load``
    is materially faster and avoids the quadratic buffer copying of a naive
    ``raw_decode`` loop; this process is intentionally one publication-wide
    load, never one load per stock caller.
    """

    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RebuildError(f"cannot read central PriceAdj payload: {path}") from exc
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
        raise RebuildError("central PriceAdj payload has no valid data array")
    for value in payload["data"]:
        if not isinstance(value, Mapping):
            raise RebuildError("central PriceAdj data row is not an object")
        yield value


def _normalise_market(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if text in {"TWSE", "TPEX", "EMERGING"}:
        return text
    if text in {"TPEx", "OTC"}:
        return "TPEX"
    return None


def _load_central_contract(expected_asof: str, receipt_path: Path) -> dict[str, Any]:
    receipt = _read_json(receipt_path, "central latest receipt")
    if not isinstance(receipt, Mapping):
        raise RebuildError("central receipt root is not an object")
    receipt_sha = _sha256_file(receipt_path)
    if receipt_sha != EXPECTED_RECEIPT_SHA:
        raise RebuildError(
            f"central receipt SHA changed: expected {EXPECTED_RECEIPT_SHA}, actual {receipt_sha}"
        )
    if receipt.get("status") != "complete" or receipt.get("publishable") is not True:
        raise RebuildError("central receipt is not complete/publishable")
    if receipt.get("expected_asof") != expected_asof:
        raise RebuildError("central receipt expected_asof mismatch")
    datasets = receipt.get("datasets")
    if not isinstance(datasets, list):
        raise RebuildError("central receipt datasets is invalid")
    matches = [
        item
        for item in datasets
        if isinstance(item, Mapping) and item.get("dataset_id") == CENTRAL_DATASET_ID
    ]
    if len(matches) != 1:
        raise RebuildError("central PriceAdj receipt dataset is missing or ambiguous")
    item = matches[0]
    if item.get("status") != "complete" or item.get("publishable") is not True:
        raise RebuildError("central PriceAdj receipt dataset is not publishable")
    if item.get("expected_asof") != expected_asof:
        raise RebuildError("central PriceAdj receipt dataset expected_asof mismatch")
    publication_run_id = str(item.get("publication_run_id") or "").strip()
    if not publication_run_id or str(item.get("run_id") or "").strip() != str(receipt.get("run_id") or "").strip():
        raise RebuildError("central PriceAdj receipt run binding is invalid")
    publish = item.get("publish")
    artifacts = publish.get("artifacts") if isinstance(publish, Mapping) else None
    if not isinstance(artifacts, list) or len(artifacts) != 2:
        raise RebuildError("central PriceAdj receipt artifacts are invalid")
    by_target = {
        str(record.get("target_relative_path") or ""): record
        for record in artifacts
        if isinstance(record, Mapping)
    }
    data_record = by_target.get("providers/finmind/json/finmind_price_adj.json")
    sidecar_record = by_target.get(
        "providers/finmind/json/finmind_price_adj.manifest.json"
    )
    if data_record is None or sidecar_record is None:
        raise RebuildError("central PriceAdj receipt artifact identity is invalid")
    data_path = _regular(CENTRAL_DATA, "central PriceAdj data")
    sidecar_path = _regular(CENTRAL_SIDECAR, "central PriceAdj sidecar")
    for path, record, label in (
        (data_path, data_record, "central PriceAdj data"),
        (sidecar_path, sidecar_record, "central PriceAdj sidecar"),
    ):
        expected_size = record.get("size")
        if isinstance(expected_size, bool) or not isinstance(expected_size, int):
            raise RebuildError(f"{label} receipt size is invalid")
        actual_size = path.stat().st_size
        if actual_size != expected_size:
            raise RebuildError(f"{label} receipt size mismatch")
        declared = str(record.get("content_sha256") or "").lower()
        if len(declared) != 64:
            raise RebuildError(f"{label} receipt file hash is invalid")
        if _sha256_file(path) != declared:
            raise RebuildError(f"{label} receipt file hash mismatch")
    sidecar = _read_json(sidecar_path, "central PriceAdj sidecar")
    if not isinstance(sidecar, Mapping):
        raise RebuildError("central PriceAdj sidecar root is invalid")
    identity = sidecar.get("dataset_identity")
    if not isinstance(identity, Mapping) or identity.get("dataset_id") != CENTRAL_DATASET_ID:
        raise RebuildError("central PriceAdj sidecar dataset identity mismatch")
    if sidecar.get("schema") != "shared-market-data-dataset-publication-v1":
        raise RebuildError("central PriceAdj sidecar schema mismatch")
    if sidecar.get("expected_asof") != expected_asof or sidecar.get("status") != "complete":
        raise RebuildError("central PriceAdj sidecar status/asof mismatch")
    if sidecar.get("publication_run_id") != publication_run_id or sidecar.get("run_id") != publication_run_id:
        raise RebuildError("central PriceAdj sidecar publication run mismatch")
    coverage = sidecar.get("coverage")
    if not isinstance(coverage, Mapping) or coverage.get("status") != "complete" or coverage.get("ratio") != 1.0:
        raise RebuildError("central PriceAdj sidecar coverage is not complete")
    pit = sidecar.get("pit")
    if not isinstance(pit, Mapping) or pit.get("eligible") is not True or pit.get("expected_asof") != expected_asof:
        raise RebuildError("central PriceAdj sidecar PIT is not eligible")
    content_hash = str(sidecar.get("content_sha256") or "").lower()
    if len(content_hash) != 64:
        raise RebuildError("central PriceAdj sidecar content hash is invalid")
    return {
        "receipt": receipt,
        "receipt_sha256": receipt_sha,
        "dataset": item,
        "data_path": data_path,
        "data_file_sha256": str(data_record["content_sha256"]).lower(),
        "sidecar_path": sidecar_path,
        "sidecar_sha256": str(sidecar_record["content_sha256"]).lower(),
        "sidecar": sidecar,
        "content_sha256": content_hash,
        "publication_run_id": publication_run_id,
        "orchestration_run_id": str(receipt.get("run_id") or "").strip(),
        "registry_sha256": str(receipt.get("registry_sha256") or "").lower(),
        "artifact_list_sha256": str(
            (publish.get("artifact_list_sha256") if isinstance(publish, Mapping) else "")
            or item.get("publication_artifact_list_sha256")
            or ""
        ).lower(),
    }


def _load_required_markets(expected_asof: str) -> tuple[list[str], dict[str, dict[str, Any]], dict[str, Any]]:
    snapshot_candidates = sorted(
        path
        for path in CENTRAL_OFFICIAL.glob(f"official_market_daily_{expected_asof.replace('-', '')}_*.json")
        if path.is_file() and not path.is_symlink()
    )
    if len(snapshot_candidates) != 1:
        raise RebuildError("official current market snapshot is missing or ambiguous")
    snapshot_path = snapshot_candidates[0]
    snapshot = _read_json(snapshot_path, "official current market snapshot")
    if not isinstance(snapshot, Mapping):
        raise RebuildError("official current market snapshot root is invalid")
    data = snapshot.get("data")
    bars = data.get("bars") if isinstance(data, Mapping) else None
    if not isinstance(bars, list):
        raise RebuildError("official current market snapshot bars are invalid")
    by_code: dict[str, dict[str, Any]] = {}
    for bar in bars:
        if not isinstance(bar, Mapping) or bar.get("session") != expected_asof:
            continue
        code = str(bar.get("code") or "").strip().upper()
        market = _normalise_market(bar.get("market"))
        if not code or market is None:
            continue
        semantics = {"market": market, "open_is_proxy": market == "EMERGING"}
        previous = by_code.get(code)
        if previous is not None and previous != semantics:
            raise RebuildError(f"official market mapping conflict: {code}")
        by_code[code] = semantics
    info_codes = load_common_equity_codes(CENTRAL_FINMIND, as_of=expected_asof)
    required = sorted(info_codes.intersection(by_code))
    if not required:
        raise RebuildError("official current common-equity universe is empty")
    required_markets = {code: by_code[code] for code in required}
    info_path, info_payload = _latest_info_payload(CENTRAL_FINMIND, not_after=expected_asof)
    fetched_at = str(info_payload.get("fetched_at") or "").strip()
    if not fetched_at:
        raise RebuildError("PIT TaiwanStockInfo provenance is missing")
    info_provenance = _normalize_info_provenance(
        {
            "path": str(info_path.resolve(strict=False)),
            "sha256": _sha256_file(info_path),
            "fetched_at": fetched_at,
        },
        expected_asof,
    )
    return required, required_markets, {
        "snapshot_path": snapshot_path,
        "snapshot_sha256": _sha256_file(snapshot_path),
        "info_provenance": info_provenance,
        "info_count": len(info_codes),
        "snapshot_code_count": len(by_code),
    }


def _collect_current_rows(
    contract: Mapping[str, Any],
    expected_asof: str,
    required_codes: list[str],
    required_markets: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    required = set(required_codes)
    rows_by_code: dict[str, dict[str, Any]] = {}
    source_row_count = 0
    ignored_nonrequired_count = 0
    ignored_noncanonical_id_count = 0
    required_input_count = 0
    duplicate_count = 0
    canonical_rows_hasher = hashlib.sha256()
    canonical_rows_hasher.update(b"[")
    first_canonical = True
    for raw in _iter_data_rows(contract["data_path"]):
        encoded = json.dumps(
            raw,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if not first_canonical:
            canonical_rows_hasher.update(b",")
        canonical_rows_hasher.update(encoded)
        first_canonical = False
        if raw.get("date") != expected_asof:
            continue
        source_row_count += 1
        raw_code = raw.get("stock_id")
        code = str(raw_code or "").strip().upper()
        if code not in required:
            ignored_nonrequired_count += 1
            if not isinstance(raw_code, str) or raw_code != code:
                ignored_noncanonical_id_count += 1
            continue
        required_input_count += 1
        normalized = _validate_row(raw, expected_asof, required_markets)
        previous = rows_by_code.get(code)
        if previous is not None:
            if _canonical(previous) != _canonical(normalized):
                raise RebuildError(f"central PriceAdj duplicate conflict: {code}")
            duplicate_count += 1
            continue
        rows_by_code[code] = normalized
    canonical_rows_hasher.update(b"]")
    if canonical_rows_hasher.hexdigest() != str(contract["content_sha256"]).lower():
        raise RebuildError("central PriceAdj canonical content hash mismatch")
    rows = [rows_by_code[code] for code in sorted(rows_by_code)]
    coverage: dict[str, Any] = {
        "distinct_codes": len(rows),
        "required_count": len(required_codes),
        "required_intersection": len(set(rows_by_code).intersection(required)),
        "coverage_threshold": len(required_codes),
        "coverage_ratio": 1.0,
        "coverage_ok": set(rows_by_code) == required,
        "coverage_basis": "required_current_common_equities_100pct",
        "missing_count": len(required - set(rows_by_code)),
        "missing_codes": sorted(required - set(rows_by_code))[:100],
        "source_row_count": source_row_count,
        "ignored_nonrequired_count": ignored_nonrequired_count,
        "ignored_noncanonical_id_count": ignored_noncanonical_id_count,
        "normalized_distinct_count": len(rows),
        "required_input_count": required_input_count,
        "duplicate_count": duplicate_count,
    }
    zero_codes = sorted(
        row["stock_id"]
        for row in rows
        if all(float(row[field]) == 0.0 for field in ("open", "max", "min", "close"))
    )
    if zero_codes:
        coverage["zero_price_count"] = len(zero_codes)
        coverage["zero_price_codes"] = zero_codes[:100]
    if not coverage["coverage_ok"]:
        raise RebuildError(
            "central PriceAdj current session coverage incomplete: "
            + json.dumps({"missing_codes": coverage["missing_codes"], "missing_count": coverage["missing_count"]})
        )
    coverage = _validate_coverage(coverage, rows, required_codes, required_markets, expected_asof)
    current = {
        "rows": rows,
        "coverage": coverage,
        "source_row_count": source_row_count,
        "required_count": len(required_codes),
    }
    return rows, coverage, current


def _load_old_sessions(old_manifest: Path, old_cache_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    del old_manifest, old_cache_root
    _central_owner_only("price_adj_migration_old_cache")


def _build_current_item(
    contract: Mapping[str, Any],
    expected_asof: str,
    required_codes: list[str],
    required_markets: Mapping[str, Mapping[str, Any]],
    provenance: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes]:
    _central_owner_only("price_adj_migration_rebuild")
    rows, coverage, _ = _collect_current_rows(
        contract, expected_asof, required_codes, required_markets
    )
    params = _market_params(expected_asof)
    request_trace = _request_trace(params)
    pit = contract["sidecar"].get("pit")
    fetched_at = str(
        contract["sidecar"].get("evaluated_at_utc")
        or (pit.get("evaluated_at_utc") if isinstance(pit, Mapping) else "")
        or contract["sidecar"].get("available_at_utc")
        or ""
    ).strip()
    if not fetched_at:
        raise RebuildError("central PriceAdj sidecar has no evaluation timestamp")
    generation_id = _generation_id(
        expected_asof,
        params,
        rows,
        required_codes,
        required_markets,
        coverage,
        request_trace,
        provenance,
    )
    name = _generation_file_name(expected_asof, generation_id)
    payload: dict[str, Any] = {
        "schema_version": MARKET_GENERATION_SCHEMA,
        "update_mode": "market",
        "url": MARKET_DATA_URL,
        "params": params,
        "market_request": params,
        "request_fingerprint": _canonical_sha256(params),
        "generation_id": generation_id,
        "session_date": expected_asof,
        "fetched_at": fetched_at,
        "info_provenance": dict(provenance),
        "required_codes": list(required_codes),
        "required_markets": dict(required_markets),
        "coverage": coverage,
        "request_count": 1,
        "request_trace": request_trace,
        "row_count": len(rows),
        "data": rows,
    }
    generation_bytes = _json_bytes(payload)
    generation_file_sha256 = hashlib.sha256(generation_bytes).hexdigest()
    committed_at = datetime.now(timezone.utc).isoformat()
    generation_sha256 = _generation_sha256(
        generation_id, name, rows, request_trace
    )
    checkpoint = _checkpoint(
        expected_asof,
        params,
        generation_id,
        name,
        generation_file_sha256,
        generation_sha256,
        len(rows),
        required_codes,
        required_markets,
        coverage,
        request_trace,
        provenance,
        committed_at,
    )
    session = {
        "date": expected_asof,
        "status": "committed",
        "generation_id": generation_id,
        "params": dict(params),
        "request_fingerprint": _canonical_sha256(params),
        "required_codes": list(required_codes),
        "required_markets": dict(required_markets),
        "file_names": [name],
        "coverage": dict(coverage),
        "request_count": 1,
        "request_trace": request_trace,
        "fetched_at": fetched_at,
        "committed_at": committed_at,
        "generation_file_sha256": generation_file_sha256,
        "generation_sha256": generation_sha256,
        "row_count": len(rows),
        "checkpoint": checkpoint,
        "info_provenance": dict(provenance),
    }
    descriptor: dict[str, Any] = {
        "schema_version": MARKET_COMMIT_SCHEMA,
        "date": expected_asof,
        "generation_id": generation_id,
        "generation_file_name": name,
        "generation_file_sha256": generation_file_sha256,
        "generation_sha256": generation_sha256,
        "row_count": len(rows),
        "required_codes_sha256": _canonical_sha256(required_codes),
        "required_markets_sha256": _canonical_sha256(required_markets),
        "info_provenance": dict(provenance),
        "checkpoint_sha256": _canonical_sha256(checkpoint),
        "manifest_session_sha256": _canonical_sha256(session),
        "committed_at": committed_at,
    }
    if set(descriptor) != set(MARKET_COMMIT_FIELDS):
        raise RebuildError("generated PriceAdj descriptor schema mismatch")
    return session, descriptor, generation_bytes, _json_bytes(descriptor)


def _write_apply(
    output_root: Path,
    old_manifest: Mapping[str, Any],
    old_cache_root: Path,
    current_session: Mapping[str, Any],
    current_descriptor: Mapping[str, Any],
    generation_bytes: bytes,
    descriptor_bytes: bytes,
    contract: Mapping[str, Any],
    universe_meta: Mapping[str, Any],
    expected_asof: str,
) -> dict[str, Any]:
    output_root = output_root.resolve(strict=False)
    if output_root.exists():
        raise RebuildError(f"refusing to overwrite output root: {output_root}")
    if not _inside(output_root, APP_DAILY_ROOT) or _inside(output_root, CENTRAL_ROOT):
        raise RebuildError("output root must be inside app-owned dstock_daily")
    if _inside(output_root, Path(os.environ.get("TEMP", "__never__")).resolve(strict=False)):
        raise RebuildError("output root cannot be Temp")
    cache_root = output_root / "cache" / "price_adj_market"
    commit_root = cache_root / "commits"
    manifest_root = output_root / "state" / "price_adj_updater" / "market"
    cache_root.mkdir(parents=True)
    commit_root.mkdir(parents=True)
    manifest_root.mkdir(parents=True)
    old_cache_root = old_cache_root.resolve(strict=False)
    old_commit_root = old_cache_root / "commits"
    old_sessions = old_manifest.get("sessions")
    if not isinstance(old_sessions, list):
        raise RebuildError("old manifest sessions invalid")
    copied_names: set[str] = set()
    copied_descriptors: list[bytes] = []
    normalized_old_sessions: list[dict[str, Any]] = []
    for item in old_sessions:
        if not isinstance(item, Mapping):
            raise RebuildError("old manifest session invalid")
        normalized_session = dict(item)
        # The pre-pointer live state contains the producer checkpoint in its
        # manifest even though its legacy descriptor/payload has no
        # info_provenance.  The strict reader's admitted legacy shape omits
        # that field; preserve the original source untouched and repair only
        # the copied v3 binding.
        if "info_provenance" not in normalized_session:
            normalized_session.pop("checkpoint", None)
        names = item.get("file_names")
        if not isinstance(names, list) or len(names) != 1:
            raise RebuildError("old session generation filename invalid")
        name = str(names[0])
        if Path(name).name != name:
            raise RebuildError("old session generation path invalid")
        source = old_cache_root / name
        destination = cache_root / name
        if not _inside(source, old_cache_root) or not source.is_file() or source.is_symlink():
            raise RebuildError(f"old generation missing: {source}")
        shutil.copy2(source, destination)
        copied_names.add(name)
        descriptor_name = None
        generation_id = str(item.get("generation_id") or "")
        date = str(item.get("date") or "").replace("-", "")
        if generation_id and date:
            descriptor_name = f"price_adj_market_{date}_{generation_id[:16]}.commit.json"
        if not descriptor_name:
            raise RebuildError("old session descriptor identity invalid")
        descriptor_source = old_commit_root / descriptor_name
        descriptor_destination = commit_root / descriptor_name
        if not descriptor_source.is_file() or descriptor_source.is_symlink():
            raise RebuildError(f"old descriptor missing: {descriptor_source}")
        shutil.copy2(descriptor_source, descriptor_destination)
        descriptor_payload = _read_json(descriptor_destination, "old PriceAdj descriptor")
        if "info_provenance" not in descriptor_payload:
            descriptor_payload["manifest_session_sha256"] = _canonical_sha256(
                normalized_session
            )
            descriptor_destination.write_bytes(_json_bytes(descriptor_payload))
        copied_descriptors.append(descriptor_destination.read_bytes())
        normalized_old_sessions.append(normalized_session)
    current_name = str(current_descriptor["generation_file_name"])
    (cache_root / current_name).write_bytes(generation_bytes)
    current_descriptor_name = (
        f"price_adj_market_{expected_asof.replace('-', '')}_"
        f"{str(current_descriptor['generation_id'])[:16]}.commit.json"
    )
    (commit_root / current_descriptor_name).write_bytes(descriptor_bytes)
    old_dates = [str(item.get("date")) for item in normalized_old_sessions]
    dates = sorted(old_dates + [expected_asof])
    sessions = normalized_old_sessions
    sessions.append(dict(current_session))
    manifest = {
        "mode": "market",
        "plan_fingerprint": _canonical_sha256(
            {
                "source_manifest_sha256": _sha256_file(DEFAULT_OLD_MANIFEST),
                "central_receipt_sha256": contract["receipt_sha256"],
                "central_publication_run_id": contract["publication_run_id"],
                "expected_asof": expected_asof,
                "current_generation_id": current_session["generation_id"],
            }
        ),
        "range_start": dates[0],
        "range_end": dates[-1],
        "schema_version": MARKET_GENERATION_SCHEMA,
        "sessions": sessions,
        "target": expected_asof,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = manifest_root / "manifest.json"
    manifest_path.write_bytes(_json_bytes(manifest))
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    previous_pointer_path = STATE_ROOT / "active_pointer.json"
    source_manifest_sha = _sha256_file(DEFAULT_OLD_MANIFEST)
    pointer = {
        "schema": "dstock_price_adj_state_pointer_v1",
        "pointer_id": "price_adj_migration_v20260823_v3",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "active_root": str(output_root),
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha,
        "cache_root": str(cache_root),
        "source_manifest_sha256": source_manifest_sha,
        "plan_fingerprint": manifest["plan_fingerprint"],
        "mode": "market",
        "range_start": dates[0],
        "range_end": dates[-1],
        "target": expected_asof,
        "session_count": len(sessions),
        "sessions": [
            {
                "date": str(item.get("date")),
                "generation_id": str(item.get("generation_id")),
                "row_count": int(item.get("row_count") or 0),
                "generation_file_sha256": str(item.get("generation_file_sha256") or ""),
                "generation_sha256": str(item.get("generation_sha256") or ""),
            }
            for item in sessions
        ],
        "central_receipt_sha256": contract["receipt_sha256"],
        "central_registry_sha256": contract["registry_sha256"],
        "central_expected_asof": expected_asof,
        "central_dataset_id": CENTRAL_DATASET_ID,
        "central_publication_run_id": contract["publication_run_id"],
        "central_orchestration_run_id": contract["orchestration_run_id"],
        "central_sidecar_sha256": contract["sidecar_sha256"],
        "central_content_sha256": contract["content_sha256"],
        "central_data_file_sha256": contract["data_file_sha256"],
        "central_artifact_list_sha256": contract["artifact_list_sha256"],
        "official_snapshot_path": str(universe_meta["snapshot_path"]),
        "official_snapshot_sha256": universe_meta["snapshot_sha256"],
        "info_provenance": dict(universe_meta["info_provenance"]),
        "previous_pointer": str(previous_pointer_path),
        "rollback": {
            "active_root": str(
                APP_DAILY_ROOT / "state" / "price_adj_updater" / "migration_v20260823_price_adj_v1"
            ),
            "manifest_path": str(DEFAULT_OLD_MANIFEST),
            "cache_root": str(
                APP_DAILY_ROOT / "cache" / "price_adj_market"
            ),
            "target": "2026-08-18",
        },
    }
    pointer_tmp = previous_pointer_path.with_name(
        f"active_pointer.json.{os.getpid()}.tmp"
    )
    pointer_tmp.write_bytes(_json_bytes(pointer))
    os.replace(pointer_tmp, previous_pointer_path)
    return {
        "output_root": str(output_root),
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha,
        "pointer_path": str(previous_pointer_path),
        "pointer": pointer,
        "copied_old_sessions": len(old_sessions),
        "current_session": dict(current_session),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    _central_owner_only("price_adj_migration_rebuild")
    expected_asof = str(args.expected_asof).strip()
    if len(expected_asof) != 10 or expected_asof[4] != "-" or expected_asof[7] != "-":
        raise RebuildError("expected_asof must be YYYY-MM-DD")
    old_manifest = _regular(Path(args.old_manifest), "old PriceAdj manifest")
    old_cache_root = Path(args.old_cache_root).resolve(strict=False)
    if not old_cache_root.is_dir() or old_cache_root.is_symlink() or _inside(old_cache_root, CENTRAL_ROOT):
        raise RebuildError("old cache root must be an app-owned regular directory")
    output_root = Path(args.output_root).resolve(strict=False)
    contract = _load_central_contract(expected_asof, Path(args.receipt))
    required_codes, required_markets, universe_meta = _load_required_markets(expected_asof)
    old_manifest_payload, old_validated = _load_old_sessions(old_manifest, old_cache_root)
    current_session, current_descriptor, generation_bytes, descriptor_bytes = _build_current_item(
        contract,
        expected_asof,
        required_codes,
        required_markets,
        universe_meta["info_provenance"],
    )
    plan = {
        "status": "ready_to_apply",
        "writes": bool(args.apply),
        "expected_asof": expected_asof,
        "required_count": len(required_codes),
        "current_row_count": int(current_session["row_count"]),
        "current_generation_id": current_session["generation_id"],
        "current_generation_file_sha256": current_session["generation_file_sha256"],
        "current_generation_sha256": current_session["generation_sha256"],
        "central_receipt_sha256": contract["receipt_sha256"],
        "central_registry_sha256": contract["registry_sha256"],
        "central_publication_run_id": contract["publication_run_id"],
        "central_content_sha256": contract["content_sha256"],
        "official_snapshot_sha256": universe_meta["snapshot_sha256"],
        "old_session_count": len(old_validated),
        "output_root": str(output_root),
    }
    if args.apply:
        plan["apply_result"] = _write_apply(
            output_root,
            old_manifest_payload,
            old_cache_root,
            current_session,
            current_descriptor,
            generation_bytes,
            descriptor_bytes,
            contract,
            universe_meta,
            expected_asof,
        )
        plan["status"] = "applied"
    return plan


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
