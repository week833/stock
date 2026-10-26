from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

PROGRAMS_ROOT = Path(__file__).resolve().parent
SHARED_ROOT = PROGRAMS_ROOT / "shared"
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))

from path_contract import PathContractError, load_path_contract


DATE_RE = re.compile(r"^\d{8}$")
FILE_RE = re.compile(r"^(?P<market>[^_]+)_(?P<code>.+)\.json$", re.IGNORECASE)
PRICE_FIELDS = ("open", "high", "low", "close")
MAX_SAMPLES_PER_CODE = 5
MAX_AFFECTED_FILES_PER_CODE = 25
AUTHORITY_STATUS_SCHEMA_VERSION = 1
AUTHORITY_STATUS_VALUES = frozenset(
    {"no_trade_or_suspended", "no_trade", "suspended"}
)
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass
class FileAudit:
    path: str
    market: str = "unknown"
    filename_market: str = ""
    filename_code: str = ""
    code: str = ""
    rows: int = 0
    first_date: str = ""
    last_date: str = ""
    issue_counts: Counter[str] = field(default_factory=Counter)
    severity_counts: Counter[str] = field(default_factory=Counter)
    samples: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    stale_classification: str = ""
    authority_status: str = ""
    authority_status_evidence: dict[str, Any] | None = None
    superseded_evidence: dict[str, Any] | None = None
    model_eligible: bool = False
    model_exclusion_reason: str = ""

    def add_issue(
        self,
        severity: str,
        code: str,
        message: str,
        *,
        row_index: int | None = None,
    ) -> None:
        self.issue_counts[code] += 1
        self.severity_counts[severity] += 1
        bucket = self.samples[code]
        # Stale files are a bounded, low-cardinality warning in normal use,
        # but truncating them hides the tail of a data-quality failure. Keep
        # every stale detail; retain the historical cap for other issue types.
        if code == "stale_history" or len(bucket) < MAX_SAMPLES_PER_CODE:
            sample: dict[str, Any] = {
                "severity": severity,
                "path": self.path,
                "message": message,
            }
            if row_index is not None:
                sample["row_index"] = row_index
            bucket.append(sample)


def _is_emerging_market(market: Any) -> bool:
    """Return whether *market* uses the official ESB price semantics."""
    return str(market or "").strip().casefold() in {
        "emerging",
        "esb",
        "興櫃",
    }


def _resolve_price_semantics(
    payload: dict[str, Any], *, market: str
) -> tuple[str, str | None]:
    """Resolve the documented price schema without hard-coding stock codes.

    ESB/興櫃 sources publish transaction high, low and average rather than a
    conventional open/close pair. Existing ESB payloads may omit the new
    marker, so the market default is the ESB semantic. A payload can opt in
    to strict conventional OHLC checks explicitly.
    """
    declared = payload.get("price_semantics")
    if declared is None or (isinstance(declared, str) and not declared.strip()):
        if _is_emerging_market(market):
            return "emerging_high_low_average", None
        return "conventional_ohlc", None
    if not isinstance(declared, str):
        return "conventional_ohlc", "price_semantics must be a string"
    semantics = declared.strip().casefold()
    if semantics == "conventional_ohlc":
        return "conventional_ohlc", None
    if semantics == "emerging_high_low_average":
        if _is_emerging_market(market):
            return "emerging_high_low_average", None
        return (
            "conventional_ohlc",
            "emerging_high_low_average is only valid for Emerging/ESB/興櫃",
        )
    return "conventional_ohlc", f"unsupported price_semantics={declared!r}"


def _validate_authority_status_evidence(
    evidence: Any,
    *,
    expected_market: str,
    expected_code: str,
    as_of: date,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Validate the fail-closed per-file no-trade evidence contract.

    A sidecar is trusted only when its schema, exact as-of date, identity,
    status, and source URL/hash are all present and valid. The normalized
    result is returned so reports never rely on arbitrary unvalidated fields.
    """
    if not isinstance(evidence, dict):
        return False, "evidence must be a JSON object", None
    schema_version = evidence.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != AUTHORITY_STATUS_SCHEMA_VERSION
    ):
        return False, "schema_version must equal 1", None
    evidence_as_of = evidence.get("as_of")
    try:
        parsed_as_of = date.fromisoformat(str(evidence_as_of))
    except (TypeError, ValueError):
        return False, "as_of must be YYYY-MM-DD", None
    if parsed_as_of != as_of:
        return False, f"as_of={evidence_as_of!r} does not match {as_of.isoformat()}", None

    evidence_market = str(evidence.get("market") or "").strip()
    if not evidence_market or evidence_market.casefold() != expected_market.casefold():
        return False, "market does not match the history payload", None
    evidence_code = str(evidence.get("code") or "").strip()
    if not evidence_code or evidence_code != expected_code:
        return False, "code does not match the history payload", None

    status = evidence.get("status")
    if not isinstance(status, str) or status not in AUTHORITY_STATUS_VALUES:
        return False, "status is not an allowed authority status", None
    if evidence.get("observed_no_trade") is not True:
        return False, "observed_no_trade must be true", None
    through_date = _parse_yyyymmdd(evidence.get("through_date"))
    if through_date != as_of:
        return False, "through_date must equal as_of", None

    source = evidence.get("source")
    if not isinstance(source, dict):
        return False, "source must contain url and sha256", None
    source_url = source.get("url")
    if not isinstance(source_url, str) or not re.fullmatch(
        r"https?://[^\s]+", source_url
    ):
        return False, "source.url must be an http(s) URL", None
    source_sha256 = source.get("sha256")
    if not isinstance(source_sha256, str) or not SHA256_RE.fullmatch(source_sha256):
        return False, "source.sha256 must be a 64-character hex digest", None

    normalized = {
        "schema_version": AUTHORITY_STATUS_SCHEMA_VERSION,
        "as_of": as_of.isoformat(),
        "market": expected_market,
        "code": expected_code,
        "status": status,
        "through_date": as_of.strftime("%Y%m%d"),
        "observed_no_trade": True,
        "source": {"url": source_url, "sha256": source_sha256.lower()},
    }
    return True, "", normalized


def _load_authority_status_sidecar(
    path: Path, authority_status_dir: Path | None
) -> dict[str, Any] | None:
    """Load the exact per-file sidecar, returning ``{}`` for invalid JSON.

    Missing sidecars intentionally return ``None``: the caller retains the
    normal stale classification. No missing evidence is treated as proof of
    no-trade.
    """
    if authority_status_dir is None:
        return None
    sidecar = authority_status_dir.resolve() / path.name
    if not sidecar.is_file():
        return None
    try:
        loaded = json.loads(sidecar.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _parse_yyyymmdd(value: Any) -> date | None:
    text = str(value or "").strip()
    if not DATE_RE.fullmatch(text):
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _filename_identity(path: Path) -> tuple[str, str]:
    match = FILE_RE.match(path.name)
    if not match:
        return "", ""
    return match.group("market"), match.group("code")


def validate_history_file(
    path: Path,
    *,
    as_of: date,
    max_stale_days: int,
    authority_status_evidence: dict[str, Any] | None = None,
) -> FileAudit:
    audit = FileAudit(path=str(path.resolve()))
    filename_market, filename_code = _filename_identity(path)
    audit.filename_market = filename_market
    audit.filename_code = filename_code
    if not filename_market:
        audit.add_issue("error", "invalid_filename", "expected <market>_<code>.json")

    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        audit.add_issue("error", "unreadable_json", f"{type(exc).__name__}: {exc}")
        return audit

    if not isinstance(payload, dict):
        audit.add_issue("error", "invalid_document", "top-level JSON value is not an object")
        return audit

    market = str(payload.get("market") or "").strip()
    code = str(payload.get("code") or "").strip()
    audit.code = code
    audit.market = market or filename_market or "unknown"
    price_semantics, semantics_error = _resolve_price_semantics(
        payload, market=audit.market
    )
    if semantics_error:
        audit.add_issue("error", "invalid_price_semantics", semantics_error)
    if not market:
        audit.add_issue("error", "missing_market", "market is empty")
    elif filename_market and market.casefold() != filename_market.casefold():
        audit.add_issue(
            "error",
            "identity_mismatch",
            f"filename market={filename_market!r}, payload market={market!r}",
        )
    if not code:
        audit.add_issue("error", "missing_code", "code is empty")
    elif filename_code and code != filename_code:
        audit.add_issue(
            "error",
            "identity_mismatch",
            f"filename code={filename_code!r}, payload code={code!r}",
        )

    rows = payload.get("rows")
    if not isinstance(rows, list):
        audit.add_issue("error", "invalid_rows", "rows is not an array")
        return audit
    audit.rows = len(rows)
    if not rows:
        audit.add_issue("error", "empty_rows", "history contains no rows")
        return audit

    declared_count = payload.get("row_count")
    if isinstance(declared_count, bool) or not isinstance(declared_count, int):
        audit.add_issue("error", "invalid_row_count", f"row_count={declared_count!r}")
    elif declared_count != len(rows):
        audit.add_issue(
            "error",
            "row_count_mismatch",
            f"row_count={declared_count}, actual={len(rows)}",
        )

    previous_date: date | None = None
    first_text = ""
    last_text = ""
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            audit.add_issue("error", "invalid_row", "row is not an object", row_index=index)
            continue

        date_text = str(row.get("date") or "").strip()
        row_date = _parse_yyyymmdd(date_text)
        if row_date is None:
            audit.add_issue(
                "error", "invalid_date", f"date={date_text!r}", row_index=index
            )
        else:
            if not first_text:
                first_text = date_text
            last_text = date_text
            if row_date > as_of:
                audit.add_issue(
                    "error",
                    "future_date",
                    f"date={date_text}, as_of={as_of:%Y%m%d}",
                    row_index=index,
                )
            if previous_date is not None and row_date <= previous_date:
                issue_code = "duplicate_date" if row_date == previous_date else "unsorted_date"
                audit.add_issue(
                    "error",
                    issue_code,
                    f"date={date_text}, previous={previous_date:%Y%m%d}",
                    row_index=index,
                )
            previous_date = row_date

        prices: dict[str, float] = {}
        for field_name in PRICE_FIELDS:
            number = _finite_number(row.get(field_name))
            if number is None or number <= 0:
                audit.add_issue(
                    "error",
                    "invalid_price",
                    f"{field_name}={row.get(field_name)!r}",
                    row_index=index,
                )
            else:
                prices[field_name] = number

        volume = _finite_number(row.get("volume"))
        if volume is None or volume < 0:
            audit.add_issue(
                "error",
                "invalid_volume",
                f"volume={row.get('volume')!r}",
                row_index=index,
            )

        if len(prices) == len(PRICE_FIELDS):
            low = prices["low"]
            high = prices["high"]
            if low > high:
                audit.add_issue(
                    "error", "invalid_ohlc", f"low={low} > high={high}", row_index=index
                )
            elif price_semantics == "conventional_ohlc" and (
                low > min(prices["open"], prices["close"])
                or high < max(prices["open"], prices["close"])
            ):
                audit.add_issue(
                    "error",
                    "invalid_ohlc",
                    (
                        f"open={prices['open']}, high={high}, low={low}, "
                        f"close={prices['close']}"
                    ),
                    row_index=index,
                )

    audit.first_date = first_text
    audit.last_date = last_text
    for metadata_name, actual in (("first_date", first_text), ("last_date", last_text)):
        declared = str(payload.get(metadata_name) or "").strip()
        if declared != actual:
            audit.add_issue(
                "error",
                "date_metadata_mismatch",
                f"{metadata_name}={declared!r}, actual={actual!r}",
            )

    last_date = _parse_yyyymmdd(last_text)
    if last_date is not None:
        stale_days = (as_of - last_date).days
        if stale_days > max_stale_days:
            if authority_status_evidence is None:
                audit.add_issue(
                    "warning",
                    "stale_history",
                    f"last_date={last_text}, age_days={stale_days}, threshold={max_stale_days}",
                )

    if authority_status_evidence is not None:
        valid_evidence, evidence_error, normalized_evidence = (
            _validate_authority_status_evidence(
                authority_status_evidence,
                expected_market=audit.market,
                expected_code=audit.code,
                as_of=as_of,
            )
        )
        if not valid_evidence:
            audit.add_issue(
                "error",
                "invalid_authority_status_evidence",
                evidence_error,
            )
            # Invalid evidence must never hide the stale warning.
            if last_date is not None and (as_of - last_date).days > max_stale_days:
                audit.add_issue(
                    "warning",
                    "stale_history",
                    f"last_date={last_text}, age_days={(as_of - last_date).days}, threshold={max_stale_days}",
                )
        elif normalized_evidence is not None:
            audit.authority_status = normalized_evidence["status"]
            audit.authority_status_evidence = normalized_evidence
            if last_date is not None and (as_of - last_date).days > max_stale_days:
                audit.stale_classification = normalized_evidence["status"]
                audit.add_issue(
                    "warning",
                    normalized_evidence["status"],
                    (
                        f"official authority evidence classifies the period through "
                        f"{normalized_evidence['as_of']} as {normalized_evidence['status']}"
                    ),
                )

    last_quote_date = str(payload.get("last_quote_data_date") or "").strip()
    if last_quote_date and _parse_yyyymmdd(last_quote_date) is None:
        audit.add_issue(
            "warning",
            "invalid_quote_metadata_date",
            f"last_quote_data_date={last_quote_date!r}",
        )
    elif last_quote_date and last_text and last_quote_date != last_text:
        audit.add_issue(
            "warning",
            "quote_history_date_mismatch",
            f"last_quote_data_date={last_quote_date}, last_date={last_text}",
        )

    source = str(payload.get("source") or "").strip()
    if not source:
        audit.add_issue("warning", "missing_source", "source is empty")
    if price_semantics == "emerging_high_low_average":
        audit.model_eligible = False
        audit.model_exclusion_reason = "esb_high_low_average_not_conventional_ohlc"
    elif audit.severity_counts.get("error"):
        audit.model_eligible = False
        audit.model_exclusion_reason = "validation_error"
    elif audit.authority_status:
        audit.model_eligible = False
        audit.model_exclusion_reason = "authority_status_no_trade_or_suspended"
    elif audit.issue_counts.get("stale_history"):
        audit.model_eligible = False
        audit.model_exclusion_reason = "stale_history"
    else:
        audit.model_eligible = True
        audit.model_exclusion_reason = ""
    return audit


def _model_safe_history_row(
    row: Any,
    *,
    as_of: date,
    market: str = "unknown",
    price_semantics: str | None = None,
) -> bool:
    """Return True only when a row can safely enter OHLCV calculations.

    Invalid observations are dropped instead of being guessed or numerically
    "repaired".  That keeps adjusted-price/source defects out of indicators
    while retaining every independently valid observation.
    """
    if not isinstance(row, dict):
        return False
    row_date = _parse_yyyymmdd(row.get("date"))
    if row_date is None or row_date > as_of:
        return False
    prices = {field: _finite_number(row.get(field)) for field in PRICE_FIELDS}
    if any(value is None or value <= 0 for value in prices.values()):
        return False
    volume = _finite_number(row.get("volume"))
    if volume is None or volume < 0:
        return False
    open_price = float(prices["open"])
    high = float(prices["high"])
    low = float(prices["low"])
    close = float(prices["close"])
    conventional = price_semantics or (
        "emerging_high_low_average"
        if _is_emerging_market(market)
        else "conventional_ohlc"
    )
    # ESB/興櫃 rows expose transaction high/low/average semantics.  Their
    # legacy open/close fields can be provider proxies, not conventional
    # OHLC observations; fail closed until a dedicated ESB model consumes
    # the documented fields.
    if conventional == "emerging_high_low_average":
        return False
    return bool(
        low <= high
        and (
            conventional != "conventional_ohlc"
            or (low <= min(open_price, close) and high >= max(open_price, close))
        )
    )


def repair_history_file(path: Path, *, as_of: date) -> dict[str, Any]:
    """Drop unsafe rows and atomically refresh derived row/date metadata."""
    resolved = path.resolve()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {"path": str(resolved), "status": "failed", "reason": f"{type(exc).__name__}: {exc}"}
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        return {"path": str(resolved), "status": "failed", "reason": "invalid history document"}

    market = str(payload.get("market") or "").strip()
    price_semantics, semantics_error = _resolve_price_semantics(
        payload, market=market
    )
    if semantics_error:
        return {
            "path": str(resolved),
            "status": "failed",
            "reason": semantics_error,
        }

    original_rows = payload["rows"]
    valid_by_date: dict[str, dict[str, Any]] = {}
    for row in original_rows:
        if _model_safe_history_row(
            row,
            as_of=as_of,
            market=market,
            price_semantics=price_semantics,
        ):
            valid_by_date[str(row["date"])] = row
    repaired_rows = [valid_by_date[key] for key in sorted(valid_by_date)]
    if not repaired_rows:
        return {"path": str(resolved), "status": "failed", "reason": "repair would leave no valid rows"}

    changed = repaired_rows != original_rows
    metadata_changed = bool(
        payload.get("row_count") != len(repaired_rows)
        or str(payload.get("first_date") or "") != str(repaired_rows[0]["date"])
        or str(payload.get("last_date") or "") != str(repaired_rows[-1]["date"])
    )
    dropped = max(0, len(original_rows) - len(repaired_rows))
    if changed or metadata_changed:
        payload["rows"] = repaired_rows
        payload["row_count"] = len(repaired_rows)
        payload["first_date"] = str(repaired_rows[0]["date"])
        payload["last_date"] = str(repaired_rows[-1]["date"])
        payload["quality_sanitization"] = {
            "schema_version": 1,
            "policy": "drop_invalid_rows_without_imputation",
            "as_of": as_of.isoformat(),
            "dropped_row_count": dropped,
        }
        temporary = resolved.with_name(f".{resolved.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, resolved)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return {"path": str(resolved), "status": "failed", "reason": f"{type(exc).__name__}: {exc}"}
    return {
        "path": str(resolved),
        "status": "repaired" if changed or metadata_changed else "unchanged",
        "original_row_count": len(original_rows),
        "row_count": len(repaired_rows),
        "dropped_row_count": dropped,
    }


def repair_error_files(
    report: dict[str, Any], *, as_of: date
) -> dict[str, Any]:
    # affected_files is intentionally sample-capped for a bounded JSON report;
    # it must never become the repair boundary. Revalidate every canonical file
    # so runs with more than MAX_AFFECTED_FILES_PER_CODE cannot leave tail files
    # silently unrepaired.
    history_dir = Path(str(report.get("history_dir") or ""))
    paths: list[Path] = []
    if history_dir.is_dir():
        for path in sorted(history_dir.glob("*.json"), key=lambda item: item.name.casefold()):
            audit = validate_history_file(path, as_of=as_of, max_stale_days=14)
            if audit.severity_counts["error"]:
                paths.append(path)
    results = [repair_history_file(path, as_of=as_of) for path in paths]
    return {
        "policy": "drop_invalid_rows_without_imputation",
        "candidate_file_count": len(paths),
        "repaired_file_count": sum(item["status"] == "repaired" for item in results),
        "unchanged_file_count": sum(item["status"] == "unchanged" for item in results),
        "failed_file_count": sum(item["status"] == "failed" for item in results),
        "dropped_row_count": sum(int(item.get("dropped_row_count") or 0) for item in results),
        "failures": [item for item in results if item["status"] == "failed"],
    }


def _stale_detail(
    audit: FileAudit, *, as_of: date, max_stale_days: int
) -> dict[str, Any] | None:
    last_date = _parse_yyyymmdd(audit.last_date)
    if last_date is None:
        return None
    age_days = (as_of - last_date).days
    if age_days <= max_stale_days:
        return None
    return {
        "severity": "warning",
        "classification": "stale_history",
        "path": audit.path,
        "market": audit.market,
        "code": audit.code,
        "last_date": audit.last_date,
        "age_days": age_days,
        "threshold": max_stale_days,
        "message": (
            f"last_date={audit.last_date}, age_days={age_days}, "
            f"threshold={max_stale_days}"
        ),
    }


def _consistent_identity(audit: FileAudit) -> bool:
    """Only use a path as a market supersession candidate when its identity is sound."""
    return bool(
        audit.filename_market
        and audit.filename_code
        and audit.market
        and audit.market != "unknown"
        and audit.code
        and audit.filename_market.casefold() == audit.market.casefold()
        and audit.filename_code == audit.code
    )


def _find_superseding_market_path(
    target: FileAudit, audits: list[FileAudit], *, as_of: date
) -> FileAudit | None:
    """Find a newer PIT-safe same-code file under another market prefix.

    A filename/code identity mismatch is deliberately excluded. This prevents
    a malformed file from being treated as superseded merely because another
    file happens to contain the same payload code. A candidate containing a
    future row or any validation error is also excluded, so an unsafe newest
    candidate cannot hide a target's stale warning.
    """
    if not _consistent_identity(target):
        return None
    target_last = _parse_yyyymmdd(target.last_date)
    if target_last is None:
        return None
    candidates: list[tuple[date, str, FileAudit]] = []
    for candidate in audits:
        if candidate.path == target.path or not _consistent_identity(candidate):
            continue
        if candidate.code != target.code:
            continue
        if candidate.filename_market.casefold() == target.filename_market.casefold():
            continue
        candidate_last = _parse_yyyymmdd(candidate.last_date)
        if candidate_last is None or candidate_last <= target_last:
            continue
        if candidate_last > as_of:
            continue
        if candidate.issue_counts.get("future_date"):
            continue
        if candidate.severity_counts.get("error"):
            continue
        candidates.append((candidate_last, candidate.path.casefold(), candidate))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0].toordinal(), item[1]))
    return candidates[0][2]


def _superseded_evidence(target: FileAudit, candidate: FileAudit) -> dict[str, Any]:
    """Return explicit old/new evidence for a stale path classification."""
    evidence = {
        "classification": "superseded_market_path",
        "old_path": target.path,
        "old_market": target.market,
        "old_market_prefix": target.filename_market,
        "old_code": target.code,
        "old_last_date": target.last_date,
        "new_path": candidate.path,
        "new_market": candidate.market,
        "new_market_prefix": candidate.filename_market,
        "new_code": candidate.code,
        "new_last_date": candidate.last_date,
    }
    evidence["old"] = {
        "path": target.path,
        "market": target.market,
        "market_prefix": target.filename_market,
        "code": target.code,
        "last_date": target.last_date,
    }
    evidence["new"] = {
        "path": candidate.path,
        "market": candidate.market,
        "market_prefix": candidate.filename_market,
        "code": candidate.code,
        "last_date": candidate.last_date,
    }
    return evidence


def _classify_superseded_stale(
    audits: list[FileAudit], *, as_of: date, max_stale_days: int
) -> None:
    """Convert only proven cross-market stale aliases into a non-warning issue."""
    for audit in audits:
        if not audit.issue_counts.get("stale_history"):
            continue
        candidate = _find_superseding_market_path(audit, audits, as_of=as_of)
        if candidate is None:
            detail = _stale_detail(
                audit, as_of=as_of, max_stale_days=max_stale_days
            )
            if detail is not None:
                # Replace the capped generic sample with the complete, stable
                # detail that is also exposed at report level.
                audit.samples["stale_history"] = [detail]
            continue

        evidence = _superseded_evidence(audit, candidate)
        audit.stale_classification = "superseded_market_path"
        audit.superseded_evidence = evidence
        audit.issue_counts.pop("stale_history", None)
        if audit.severity_counts.get("warning", 0) <= 1:
            audit.severity_counts.pop("warning", None)
        else:
            audit.severity_counts["warning"] -= 1
        audit.issue_counts["superseded_market_path"] += 1
        audit.samples.pop("stale_history", None)
        audit.samples["superseded_market_path"] = [
            {
                "severity": "info",
                "path": audit.path,
                "message": "stale path is superseded by a newer market path",
                **evidence,
            }
        ]


def _merge_samples(
    target: dict[str, list[dict[str, Any]]], source: dict[str, list[dict[str, Any]]]
) -> None:
    for code, incoming in source.items():
        if code == "stale_history":
            target[code].extend(incoming)
            continue
        remaining = MAX_SAMPLES_PER_CODE - len(target[code])
        if remaining > 0:
            target[code].extend(incoming[:remaining])


def _stable_samples(
    samples: dict[str, list[dict[str, Any]]]
) -> dict[str, list[dict[str, Any]]]:
    def key(item: dict[str, Any]) -> tuple[str, int, str]:
        row_index = item.get("row_index")
        return (
            str(item.get("path") or "").casefold(),
            int(row_index) if isinstance(row_index, int) else -1,
            str(item.get("message") or ""),
        )

    return {
        code: sorted(items, key=key)
        for code, items in sorted(samples.items())
    }


def validate_history_directory(
    history_dir: Path,
    *,
    as_of: date,
    max_stale_days: int = 14,
    workers: int = 8,
    authority_status_dir: Path | None = None,
) -> dict[str, Any]:
    resolved = history_dir.resolve()
    if not resolved.is_dir():
        return {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "as_of": as_of.isoformat(),
            "history_dir": str(resolved),
            "authority_status_dir": (
                str(authority_status_dir.resolve())
                if authority_status_dir is not None
                else ""
            ),
            "status": "error",
            "files": {"total": 0, "healthy": 0, "warning": 0, "error": 0},
            "rows": 0,
            "issue_counts": {"history_directory_missing": 1},
            "severity_counts": {"error": 1},
            "issue_samples": {
                "history_directory_missing": [
                    {
                        "severity": "error",
                        "path": str(resolved),
                        "message": "history directory does not exist",
                    }
                ]
            },
            "affected_files": {"history_directory_missing": [str(resolved)]},
            "markets": {},
            "stale_details": [],
            "authority_status_details": [],
            "superseded_market_paths": [],
            "model_input_details": [],
            "file_audit_details": [],
        }

    paths = sorted(resolved.glob("*.json"), key=lambda item: item.name.casefold())
    worker_count = max(1, min(int(workers), 32))

    def run(path: Path) -> FileAudit:
        return validate_history_file(
            path,
            as_of=as_of,
            max_stale_days=max_stale_days,
            authority_status_evidence=_load_authority_status_sidecar(
                path, authority_status_dir
            ),
        )

    if worker_count == 1:
        audits = list(map(run, paths))
    else:
        executor = ThreadPoolExecutor(max_workers=worker_count)
        try:
            audits = list(executor.map(run, paths))
        finally:
            executor.shutdown(wait=True)

    _classify_superseded_stale(
        audits, as_of=as_of, max_stale_days=max_stale_days
    )

    issue_counts: Counter[str] = Counter()
    severity_counts: Counter[str] = Counter()
    samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    affected_files: dict[str, list[str]] = defaultdict(list)
    file_status_counts: Counter[str] = Counter()
    markets: dict[str, dict[str, Any]] = {}
    stale_details: list[dict[str, Any]] = []
    authority_status_details: list[dict[str, Any]] = []
    superseded_market_paths: list[dict[str, Any]] = []
    model_input_details: list[dict[str, Any]] = []
    file_audit_details: list[dict[str, Any]] = []
    total_rows = 0
    for audit in audits:
        total_rows += audit.rows
        model_input_details.append(
            {
                "path": audit.path,
                "market": audit.market,
                "code": audit.code,
                "price_semantics": (
                    "emerging_high_low_average"
                    if _is_emerging_market(audit.market)
                    else "conventional_ohlc"
                ),
                "model_eligible": audit.model_eligible,
                "exclusion_reason": audit.model_exclusion_reason,
                "authority_status": audit.authority_status,
            }
        )
        file_audit_details.append(
            {
                "path": audit.path,
                "market": audit.market,
                "code": audit.code,
                "rows": audit.rows,
                "first_date": audit.first_date,
                "last_date": audit.last_date,
                "issue_counts": dict(sorted(audit.issue_counts.items())),
                "severity_counts": dict(sorted(audit.severity_counts.items())),
                "model_eligible": audit.model_eligible,
                "model_exclusion_reason": audit.model_exclusion_reason,
                "authority_status": audit.authority_status,
            }
        )
        issue_counts.update(audit.issue_counts)
        severity_counts.update(audit.severity_counts)
        _merge_samples(samples, audit.samples)
        if audit.stale_classification == "superseded_market_path":
            if audit.superseded_evidence is not None:
                superseded_market_paths.append(audit.superseded_evidence)
        elif audit.authority_status and audit.authority_status_evidence is not None:
            authority_status_details.append(
                {
                    "classification": audit.authority_status,
                    "path": audit.path,
                    "market": audit.market,
                    "code": audit.code,
                    "last_date": audit.last_date,
                    "evidence": audit.authority_status_evidence,
                }
            )
        else:
            detail = _stale_detail(
                audit, as_of=as_of, max_stale_days=max_stale_days
            )
            if detail is not None and audit.issue_counts.get("stale_history"):
                stale_details.append(detail)
        for code in audit.issue_counts:
            bucket = affected_files[code]
            if len(bucket) < MAX_AFFECTED_FILES_PER_CODE:
                bucket.append(audit.path)
        if audit.severity_counts["error"]:
            file_status = "error"
        elif audit.severity_counts["warning"]:
            file_status = "warning"
        else:
            file_status = "healthy"
        file_status_counts[file_status] += 1

        market = markets.setdefault(
            audit.market,
            {
                "files": 0,
                "rows": 0,
                "healthy_files": 0,
                "warning_files": 0,
                "error_files": 0,
                "first_date": "",
                "last_date": "",
            },
        )
        market["files"] += 1
        market["rows"] += audit.rows
        market[f"{file_status}_files"] += 1
        if audit.first_date and (
            not market["first_date"] or audit.first_date < market["first_date"]
        ):
            market["first_date"] = audit.first_date
        if audit.last_date and audit.last_date > market["last_date"]:
            market["last_date"] = audit.last_date

    if not paths:
        issue_counts["no_history_files"] += 1
        severity_counts["error"] += 1
        samples["no_history_files"].append(
            {
                "severity": "error",
                "path": str(resolved),
                "message": "no *.json history files found",
            }
        )

    stale_details.sort(key=lambda item: str(item.get("path") or "").casefold())
    superseded_market_paths.sort(
        key=lambda item: (
            str(item.get("old_path") or "").casefold(),
            str(item.get("new_path") or "").casefold(),
        )
    )
    authority_status_details.sort(
        key=lambda item: str(item.get("path") or "").casefold()
    )

    status = "error" if severity_counts["error"] else (
        "warning" if severity_counts["warning"] else "ok"
    )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of.isoformat(),
        "history_dir": str(resolved),
        "authority_status_dir": (
            str(authority_status_dir.resolve())
            if authority_status_dir is not None
            else ""
        ),
        "status": status,
        "files": {
            "total": len(paths),
            "healthy": file_status_counts["healthy"],
            "warning": file_status_counts["warning"],
            "error": file_status_counts["error"],
        },
        "rows": total_rows,
        "issue_counts": dict(sorted(issue_counts.items())),
        "severity_counts": dict(sorted(severity_counts.items())),
        "issue_samples": _stable_samples(samples),
        "affected_files": dict(sorted(affected_files.items())),
        "markets": dict(sorted(markets.items())),
        "stale_details": stale_details,
        "authority_status_details": authority_status_details,
        "superseded_market_paths": superseded_market_paths,
        "model_input_details": sorted(
            model_input_details, key=lambda item: str(item.get("path") or "").casefold()
        ),
        "file_audit_details": sorted(
            file_audit_details, key=lambda item: str(item.get("path") or "").casefold()
        ),
    }


PUBLISHED_PRICE_ROUTES = ("official_market_daily", "finmind_price_adj")
_PUBLISHED_DAY_RE = re.compile(r"^(?:[0-9]{8}|[0-9]{4}-[0-9]{2}-[0-9]{2})$")
_OFFICIAL_OBSERVATION_SOURCES = {
    "TWSE": ("twse_daily_all", "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"),
    "TPEx": ("tpex_mainboard_daily", "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"),
    "Emerging": ("tpex_emerging_daily", "https://www.tpex.org.tw/www/zh-tw/emerging/des010"),
}
_BOARD_IDS = {"TWSE": 1, "TPEx": 2, "Emerging": 3}
_BOARD_NO_QUOTE = 8
_BOARD_MISSING_PROXY = 16
_BOARD_PROXY = 32
_OFFICIAL_WIRE_MISSING = frozenset({"", "-", "--", "---", "n/a", "na", "null", "none"})
_OFFICIAL_LEGACY_SHAPES = {
    "TWSE": ("Code", ("OpeningPrice", "HighestPrice", "LowestPrice", "ClosingPrice"),
             ("TradeVolume", "TradeValue", "Transaction"),
             "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"),
    "TPEx": ("SecuritiesCompanyCode", ("Open", "High", "Low", "Close"),
             ("TradingShares", "TransactionAmount", "TransactionNumber"),
             "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"),
}
_OFFICIAL_NATIVE_FIELDS = frozenset({"Date", "Code", "SecuritiesCompanyCode"} |
                                    {field for _identity, prices, quantities, _endpoint in _OFFICIAL_LEGACY_SHAPES.values()
                                     for field in (*prices, *quantities)})


def _published_number(value: Any) -> float | None:
    # Huge valid JSON integers must become an invalid numeric observation,
    # not crash this adapter. Leave legacy authority/history behavior intact.
    try:
        return _finite_number(value)
    except OverflowError:
        return None


def _official_observation_evidence(row: dict[str, Any], market: str) -> bool:
    expected = _OFFICIAL_OBSERVATION_SOURCES.get(market)
    if expected is None or (row.get("source_id"), row.get("source_url")) != expected:
        return False
    value = row.get("observed_at") or row.get("_shared_collected_at")
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _official_missing_proxy(row: dict[str, Any], market: str) -> bool:
    return (market == "Emerging" and row.get("row_schema") == "official-history-observation-v1"
            and row.get("observation_status") == "priced" and row.get("open_is_proxy") is True
            and row.get("open_basis") == "previous_average"
            and row.get("open_unavailable_reason") == "official_previous_average_missing"
            and "open" in row and row["open"] is None
            and "previous_average" in row and row["previous_average"] is None
            and _official_observation_evidence(row, market))


def _official_no_quote(row: dict[str, Any], market: str) -> bool:
    # Older normalized official observations predate row_schema. They still
    # need an explicit observation status and exact producer/source evidence;
    # bare nulls, an arbitrary marker, or another schema cannot waive prices.
    return (row.get("row_schema") in (None, "official-history-observation-v1")
            and row.get("observation_status") == "no_quote"
            and (all(field in row and row[field] is None for field in PRICE_FIELDS)
                 or all(field in row and _published_number(row[field]) == 0 for field in PRICE_FIELDS))
            and _official_observation_evidence(row, market))


def _official_wire_number(value: Any) -> tuple[bool, float | None]:
    if value is None:
        return True, None
    if isinstance(value, str):
        value = value.strip()
        if value.casefold() in _OFFICIAL_WIRE_MISSING:
            return True, None
        value = value.replace(",", "")
    number = _published_number(value)
    return number is not None, number


def _official_native_row_matches(row: dict[str, Any], market: str, *, producer_attested: bool) -> bool:
    """Accept only the producer's lossless TWSE/TPEx OpenAPI row additions.

    See stock_shared_store._source_identity/_market_daily_format_only and
    market_daily_normalize: native identity, all four quotes and actual
    quantities must agree independently with every normalized counterpart.
    The validated published entry/producer is required, not inferred from
    an arbitrary all-null row or a current security-board catalog.
    """
    shape = _OFFICIAL_LEGACY_SHAPES.get(market)
    if (not producer_attested or shape is None
            or row.get("row_schema") not in (None, "official-history-observation-v1")):
        return False
    identity_field, price_fields, quantity_fields, endpoint = shape
    modern = _official_observation_evidence(row, market)
    if row.get("row_schema") == "official-history-observation-v1" and not modern:
        return False
    if (row.get("source_id") not in (None, _OFFICIAL_OBSERVATION_SOURCES[market][0])
            or row.get("source_url") not in (None, endpoint, _OFFICIAL_OBSERVATION_SOURCES[market][1])
            or (row.get("source_url") == _OFFICIAL_OBSERVATION_SOURCES[market][1] and not modern)):
        return False
    if all(key in row for key in ("Code", "ClosingPrice", "SecuritiesCompanyCode", "Close")):
        return False
    raw_stock, stock = row.get(identity_field), row.get("stock_id")
    if (not isinstance(raw_stock, str) or not isinstance(stock, str)
            or not re.fullmatch(r"[A-Za-z0-9]+", raw_stock.strip()) or raw_stock.strip() != stock.strip()):
        return False
    wire_day = row.get("Date")
    if not isinstance(wire_day, str):
        return False
    wire_day = wire_day.strip()
    try:
        if re.fullmatch(r"[0-9]{7}", wire_day) and int(wire_day[:3]) > 0:
            raw_day = date(int(wire_day[:3]) + 1911, int(wire_day[3:5]), int(wire_day[5:]))
        else:
            raw_day = _published_day(wire_day)
    except ValueError:
        return False
    if raw_day is None or raw_day != _published_day(row.get("date")):
        return False
    for normalized, native in zip(PRICE_FIELDS, price_fields):
        if native not in row or normalized not in row or isinstance(row[normalized], bool):
            return False
        valid, value = _official_wire_number(row[native])
        if not valid or (row[normalized] is not None and _published_number(row[normalized]) is None):
            return False
        if value != (None if row[normalized] is None else _published_number(row[normalized])):
            return False
    for normalized, native in zip(("volume", "amount", "transactions"), quantity_fields):
        if native not in row or normalized not in row or isinstance(row[normalized], bool):
            return False
        valid, value = _official_wire_number(row[native])
        if not valid or (row[normalized] is not None and _published_number(row[normalized]) is None):
            return False
        if value != (None if row[normalized] is None else _published_number(row[normalized])):
            return False
    present = sum(row[field] is not None for field in PRICE_FIELDS)
    return (row.get("observation_status") == "no_quote" if present == 0 else
            row.get("observation_status") == "partial" if present < 4 else
            row.get("observation_status") in (None, "priced"))


def _published_output_path(path: Path, sources: Any, root: Path) -> Path:
    """Refuse canonical reports before mkdir/write, including redirected parents."""
    output = path.resolve(strict=False)
    lexical = Path(os.path.abspath(path))
    nominal_root = Path(os.path.abspath(load_path_contract().canonical_shared))
    if lexical.is_relative_to(nominal_root) or output.is_relative_to(root.resolve()):
        raise RuntimeError("published --json-out must be outside canonical_shared")
    catalog = sources.snapshot_descriptor(PUBLISHED_PRICE_ROUTES)
    for source in catalog.get("pinned_sources", []):
        source_root = Path(source["root"]).resolve()
        if output.is_relative_to(source_root):
            raise RuntimeError("published --json-out must be outside canonical source roots")
        for field in ("manifest_path", "head_path"):
            if source.get("identity", {}).get(field):
                if output == Path(source["identity"][field]).resolve(strict=False):
                    raise RuntimeError("published --json-out would overwrite a pinned head")
        for entries in source.get("entries", {}).values():
            for ref, _entry in entries:
                target = source_root / str(ref.get("ref", ref.get("path", "")))
                if output == target.resolve(strict=False):
                    raise RuntimeError("published --json-out would overwrite a pinned payload")
    return output


def _published_day(value: Any) -> date | None:
    # Provider rows remain untouched; only logical calendar-day keys accept
    # the two published spellings. Numeric JSON and timestamps are not days.
    if not isinstance(value, str) or not _PUBLISHED_DAY_RE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _comparison_value(value: Any) -> Any:
    # Match JSON value equality for 1 and 1.0 without changing published bytes.
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _comparison_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_comparison_value(item) for item in value]
    return value


def _published_sources() -> tuple[Any, Path]:
    source_root = PROGRAMS_ROOT / "single_stock_analysis" / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    from stock_data_sources import PublishedSources, SourceSpec

    root = load_path_contract().canonical_shared.resolve()
    spec = SourceSpec(
        "manifest_json", root, root / "_metadata" / "stock_data_current.json",
        source_id="shared_market_data",
    )
    return PublishedSources((spec,), row_cache_dir=None), root


def _physical_identity(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"device": stat.st_dev, "inode": stat.st_ino, "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns}


def validate_published_prices(*, as_of: date, max_stale_days: int = 14,
                              source_binding: tuple[Any, Path] | None = None,
                              price_routes: tuple[str, ...] = PUBLISHED_PRICE_ROUTES) -> dict[str, Any]:
    """Audit active published prices sequentially; never materialize or repair data.

    PriceAdj and official quotes have separate identities and price semantics.
    The reader verifies strict JSON, SHA, size and count before exposing rows,
    then rechecks physical identity when every iterator is exhausted.
    """
    issues: Counter[str] = Counter()
    severities: Counter[str] = Counter()
    samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    routes: list[dict[str, Any]] = []
    total_rows = 0
    # An exact-day, bounded-to-input index, not a current-board lookup or a
    # persistent cache. OMD provenance must finish verification before use.
    boards = sqlite3.connect(":memory:")
    boards.execute("PRAGMA temp_store=MEMORY")
    boards.execute("CREATE TABLE board(stock TEXT, day INTEGER, flags INTEGER, PRIMARY KEY(stock,day)) WITHOUT ROWID")
    board_coverage: Counter[str] = Counter()

    def issue(issue_code: str, message: str, *, route: str, severity: str = "error", **detail: Any) -> None:
        issues[issue_code] += 1
        severities[severity] += 1
        if len(samples[issue_code]) < MAX_SAMPLES_PER_CODE:
            samples[issue_code].append(dict(severity=severity, route_id=route, message=message, **detail))

    try:
        sources, root = source_binding if source_binding is not None else _published_sources()
    except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        sources, root = None, None
        issue("published_source_unavailable", f"{type(exc).__name__}: {exc}", route="")
    pinned = sources.descriptor() if sources is not None else None
    if not price_routes or any(route not in PUBLISHED_PRICE_ROUTES for route in price_routes) or len(set(price_routes)) != len(price_routes):
        raise ValueError("invalid published price route selection")
    if "finmind_price_adj" in price_routes and "official_market_daily" not in price_routes:
        raise ValueError("PriceAdj quality requires the same-pin official market route")
    if price_routes != tuple(route for route in PUBLISHED_PRICE_ROUTES if route in price_routes):
        raise ValueError("official market evidence must be verified before PriceAdj")
    producers = {source["source_id"]: source.get("producer_id") for source in (pinned or {}).get("sources", [])}
    legacy_no_quote_keys = []
    for route in price_routes if sources is not None else ():
        start_errors = severities["error"]
        start_warnings = severities["warning"]
        count, artifacts = 0, []
        classifications: Counter[str] = Counter()
        official_proof_failed = False
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        seen: dict[tuple[str, str, date], bytes] = {}
        try:
            for artifact, rows in sources.iter_artifact_rows(route):
                artifact_start_count = count
                entry = artifact.metadata
                metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
                selected = dict(entry.get("selected_ref") or {})
                target = root / str(selected.get("path") or entry.get("path") or "")
                before = _physical_identity(target)
                evidence = dict(source_id=artifact.source_id, snapshot_id=artifact.snapshot_id,
                                status=artifact.status, content_sha256=artifact.content_sha256,
                                selected_ref=selected, file_identity_before=before,
                                expected_asof=metadata.get("expected_asof"),
                                gaps=metadata.get("gaps", []), prior_gaps=metadata.get("prior_gaps", []),
                                partial_recovery=metadata.get("partial_recovery"))
                artifacts.append(evidence)
                if str(artifact.status).lower() not in ("success", "complete", "available", "committed"):
                    issue("published_status_incomplete", f"published status {artifact.status}", route=route)
                for field_name in ("gaps", "prior_gaps", "unresolved_prior_gaps", "unresolved_prior_reasons", "errors"):
                    value = metadata.get(field_name)
                    if value:
                        issue("published_coverage_gaps", f"{field_name} remains unresolved", route=route,
                              gap_field=field_name, gap_count=len(value) if isinstance(value, (list, dict)) else 1)
                for row_index, row in enumerate(rows):
                    count += 1
                    if not isinstance(row, dict):
                        issue("invalid_row", "row is not an object", route=route, row_index=row_index)
                        continue
                    day = _published_day(row.get("date"))
                    stock = str(row.get("stock_id") or row.get("data_id") or "").strip()
                    market = str(row.get("market") or "").strip() if route == "official_market_daily" else "adjusted"
                    if not stock:
                        issue("missing_code", "stock identity is empty", route=route, row_index=row_index)
                    if not market:
                        issue("missing_market", "market is empty", route=route, row_index=row_index)
                    detail = dict(row_index=row_index, market=market, code=stock)
                    group = groups.setdefault((market, stock), {"rows": 0, "priced_rows": 0, "no_quote_rows": 0,
                                                               "first_date": None, "last_date": None,
                                                               "last_observation_date": None})
                    group["rows"] += 1
                    if day is None:
                        issue("invalid_date", f"date={row.get('date')!r}", route=route, **detail)
                    else:
                        if day > as_of:
                            issue("future_date", f"date={day:%Y%m%d}, as_of={as_of:%Y%m%d}", route=route, **detail)
                        group["last_observation_date"] = max(group["last_observation_date"], day) if group["last_observation_date"] else day
                        logical = _comparison_value(dict(row, date=day.strftime("%Y%m%d")))
                        digest = hashlib.sha256(json.dumps(logical, sort_keys=True, ensure_ascii=False,
                                                           separators=(",", ":"), allow_nan=False).encode("utf-8")).digest()
                        key = market, stock, day
                        if key in seen:
                            issue("duplicate_date" if seen[key] == digest else "duplicate_natural_key_conflict",
                                  f"duplicate market/stock/day={market}/{stock}/{day:%Y%m%d}", route=route, **detail)
                        else:
                            seen[key] = digest
                    native_present = route == "official_market_daily" and bool(_OFFICIAL_NATIVE_FIELDS.intersection(row))
                    native_matches = native_present and _official_native_row_matches(
                        row, market, producer_attested=(artifact.source_id == "shared_market_data"
                                                       and artifact.route_id == "official_market_daily"
                                                       and producers.get(artifact.source_id) == "twstock_updater"))
                    if native_present and not native_matches:
                        issue("invalid_native_observation_evidence", "native provider identity/quotes/quantities do not completely match normalized row and provenance", route=route, **detail)
                    legacy_no_quote = bool(native_matches and row.get("observation_status") == "no_quote")
                    no_quote = (legacy_no_quote if native_present else
                                route == "official_market_daily" and _official_no_quote(row, market))
                    if legacy_no_quote:
                        classifications["attested_legacy_no_quote_observations"] += 1
                        legacy_no_quote_keys.append(dict(stock_id=stock, date=day.strftime("%Y%m%d"), market=market))
                    missing_proxy = route == "official_market_daily" and _official_missing_proxy(row, market)
                    if route == "official_market_daily" and row.get("observation_status") not in (None, "priced", "no_quote"):
                        issue("invalid_observation_status", f"unsupported observation_status={row.get('observation_status')!r}", route=route, **detail)
                    historical_flags = 0
                    board_proven = native_matches if native_present else _official_observation_evidence(row, market)
                    if route == "official_market_daily" and stock and day is not None and board_proven:
                        flags = (_BOARD_IDS[market] | (_BOARD_NO_QUOTE if no_quote else 0)
                                 | (_BOARD_MISSING_PROXY if missing_proxy else 0)
                                 | (_BOARD_PROXY if row.get("open_is_proxy") is True and row.get("open_basis") == "previous_average" else 0))
                        boards.execute("INSERT INTO board VALUES(?,?,?) ON CONFLICT(stock,day) DO UPDATE SET flags=CASE WHEN flags=excluded.flags THEN flags ELSE 0 END",
                                       (stock, day.toordinal(), flags))
                    elif route == "finmind_price_adj":
                        found = boards.execute("SELECT flags FROM board WHERE stock=? AND day=?",
                                               (stock, day.toordinal())).fetchone() if stock and day is not None else None
                        historical_flags = found[0] if found else 0
                        board_coverage["matched_exact_day" if historical_flags else "unverified_exact_day"] += 1
                        no_quote = bool(historical_flags & _BOARD_NO_QUOTE) and all(row.get(f) == 0 for f in PRICE_FIELDS)
                    if no_quote:
                        classifications["no_quote_observations_excluded_from_bars"] += 1
                        group["no_quote_rows"] += 1
                    else:
                        classifications["priced_observations"] += 1
                        group["priced_rows"] += 1
                        if day is not None:
                            group["first_date"] = min(group["first_date"], day) if group["first_date"] else day
                            group["last_date"] = max(group["last_date"], day) if group["last_date"] else day
                    prices = {}
                    for field_name in PRICE_FIELDS:
                        alias = {"high": "max", "low": "min"}.get(field_name) if route == "finmind_price_adj" else None
                        value = row.get(field_name, row.get(alias) if alias else None)
                        number = _published_number(value)
                        if alias and field_name in row and alias in row:
                            alias_number = _published_number(row[alias])
                            if number != alias_number or (number is None and not (value is None and row[alias] is None)):
                                issue("price_alias_conflict", f"{field_name} and {alias} do not agree", route=route, **detail)
                        if no_quote and (value is None or number == 0):
                            continue
                        if field_name == "open" and missing_proxy:
                            classifications["official_missing_previous_average_proxy"] += 1
                            continue
                        if (field_name == "open" and route == "finmind_price_adj" and number == 0
                                and historical_flags & _BOARD_MISSING_PROXY):
                            classifications["adjusted_zero_open_with_exact_day_missing_proxy"] += 1
                            continue
                        if number is None or number <= 0:
                            if field_name == "open" and route == "finmind_price_adj" and number == 0 and not historical_flags:
                                issue("opening_semantics_unverified", "zero open has no exact-day board/first-observation proof", route=route, **detail)
                            else:
                                issue("invalid_price", f"{field_name}={value!r}", route=route, **detail)
                        else:
                            prices[field_name] = number
                    volume = _published_number(row.get("volume", row.get("Trading_Volume")))
                    if route == "finmind_price_adj" and "volume" in row and "Trading_Volume" in row:
                        if volume != _published_number(row["Trading_Volume"]):
                            issue("volume_alias_conflict", "volume and Trading_Volume do not agree", route=route, **detail)
                    if (volume is None and not (no_quote and "volume" in row and row["volume"] is None)) or (volume is not None and volume < 0):
                        issue("invalid_volume", f"volume={row.get('volume', row.get('Trading_Volume'))!r}", route=route, **detail)
                    for quantity in ("amount", "transactions") if route == "official_market_daily" else ():
                        if quantity in row and row[quantity] is not None:
                            number = _published_number(row[quantity])
                            if number is None or number < 0:
                                issue("invalid_quantity", f"{quantity}={row[quantity]!r}", route=route, **detail)
                    semantics, semantics_error = _resolve_price_semantics(row, market=market)
                    if semantics_error:
                        issue("invalid_price_semantics", semantics_error, route=route, **detail)
                    if all(field in prices for field in ("low", "high", "close")):
                        low, high = prices["low"], prices["high"]
                        if low > high or not low <= prices["close"] <= high:
                            issue("invalid_ohlc", repr(prices), route=route, **detail)
                        if low <= high and "open" in prices and not low <= prices["open"] <= high:
                            if route == "official_market_daily":
                                if (market == "Emerging" and semantics == "emerging_high_low_average"
                                        and row.get("open_is_proxy") is True and row.get("open_basis") == "previous_average"):
                                    classifications["official_previous_average_outside_daily_range"] += 1
                                else:
                                    issue("invalid_ohlc", "opening is outside daily range without Emerging proxy evidence", route=route, **detail)
                            elif historical_flags & 7 == _BOARD_IDS["Emerging"]:
                                classifications["adjusted_previous_average_outside_daily_range"] += 1
                            elif historical_flags:
                                issue("invalid_ohlc", "opening is outside daily range on exact-day mainboard", route=route, **detail)
                            else:
                                issue("opening_semantics_unverified", "opening is outside daily range; exact-day board is unavailable or conflicting", route=route, **detail)
                after = _physical_identity(target)
                evidence["file_identity_after"] = after
                evidence["rows_read"] = count - artifact_start_count
                if (type(entry.get("record_count")) is not int or
                        entry["record_count"] != evidence["rows_read"]):
                    issue("published_record_count_mismatch",
                          f"declared={entry.get('record_count')!r}, read={evidence['rows_read']}", route=route)
                    official_proof_failed = True
                if before != after:
                    issue("published_identity_changed", "payload identity changed while reading", route=route)
                    official_proof_failed = True
        except (OSError, ValueError, TypeError, KeyError, RuntimeError, sqlite3.Error) as exc:
            issue("published_artifact_invalid", f"{type(exc).__name__}: {exc}", route=route)
            official_proof_failed = True
        if route == "official_market_daily":
            if official_proof_failed:
                boards.execute("DELETE FROM board")
            boards.commit()
        if not artifacts:
            issue("published_route_missing", "no verified published artifact", route=route)
        if not count:
            issue("empty_rows", "published route contains no rows", route=route)
        group_details = []
        for (market, stock), group in sorted(groups.items()):
            last = group["last_date"]
            if last is not None and (as_of - last).days > max_stale_days:
                issue("stale_history", f"last_date={last:%Y%m%d}, age_days={(as_of-last).days}, threshold={max_stale_days}",
                      route=route, severity="warning", market=market, code=stock)
            group_details.append(dict(market=market, code=stock, rows=group["rows"],
                                      priced_rows=group["priced_rows"], no_quote_rows=group["no_quote_rows"],
                                      first_date=group["first_date"].strftime("%Y%m%d") if group["first_date"] else "",
                                      last_date=last.strftime("%Y%m%d") if last else "",
                                      last_observation_date=group["last_observation_date"].strftime("%Y%m%d") if group["last_observation_date"] else "",
                                      price_semantics="emerging_high_low_average" if _is_emerging_market(market) else "conventional_ohlc"))
        routes.append(dict(route_id=route, rows=count, artifacts=artifacts, securities=group_details,
                           observation_classifications=dict(classifications),
                           errors=severities["error"]-start_errors, warnings=severities["warning"]-start_warnings))
        total_rows += count
        del seen, groups
        sources.release(route)
    board_coverage["verified_exact_day_keys"] = boards.execute("SELECT COUNT(*) FROM board WHERE flags<>0").fetchone()[0]
    board_coverage["conflicting_exact_day_keys"] = boards.execute("SELECT COUNT(*) FROM board WHERE flags=0").fetchone()[0]
    boards.close()
    diagnostics = list(sources.diagnostics) if sources is not None else []
    for message in diagnostics:
        issue("published_reader_diagnostic", str(message), route="")
    final_descriptor = sources.descriptor() if sources is not None else None
    head_observations = []
    for source in (pinned or {}).get("sources", []):
        head_path = source.get("manifest_path") or source.get("head_path")
        if head_path:
            try:
                raw = Path(head_path).read_bytes()
                current_sha = hashlib.sha256(raw).hexdigest()
                head_observations.append(dict(path=head_path, pinned_sha256=source.get("manifest_sha256") or source.get("head_sha256"),
                                              current_sha256=current_sha, size=len(raw)))
            except OSError as exc:
                head_observations.append(dict(path=head_path, observation_error=str(exc)))
    file_status = Counter("error" if item["errors"] else "warning" if item["warnings"] else "healthy" for item in routes)
    return dict(schema_version=2, source_mode="published", generated_at=datetime.now(timezone.utc).isoformat(),
                as_of=as_of.strftime("%Y%m%d"), canonical_root=str(root) if root is not None else "",
                history_dir="", authority_status_dir="", status="error" if severities["error"] else "warning" if severities["warning"] else "ok",
                files=dict(total=len(routes), healthy=file_status["healthy"], warning=file_status["warning"], error=file_status["error"]),
                rows=total_rows, issue_counts=dict(sorted(issues.items())), severity_counts=dict(severities),
                issue_samples=dict(samples), routes=routes, diagnostics=diagnostics,
                pinned_descriptor=pinned, verified_descriptor=final_descriptor, head_observations=head_observations,
                opening_semantics_evidence=dict(board_coverage),
                attested_legacy_no_quote_keys=legacy_no_quote_keys,
                semantic_contract_sources=["https://finmind.github.io/tutor/TaiwanMarket/Technical/"],
                data_repaired=False)


def resolve_history_directory(project_root: Path) -> Path:
    del project_root
    try:
        # Price histories are app-owned D-stock daily state, not canonical
        # shared raw data.  Resolve the managed namespace directly so the
        # default command audits the actual model input root.
        return load_path_contract().daily("prices", "history")
    except PathContractError as exc:
        raise RuntimeError(f"invalid central path contract: {exc}") from exc


def resolve_authority_status_directory(path: Path | None = None) -> Path:
    """Resolve the canonical authority sidecar directory or a safe override.

    The default is centrally managed dstock_daily state. Explicit overrides
    are allowed only below that same managed root; resolving existing parents
    also prevents a junction/symlink from redirecting reads outside it.
    """
    contract = load_path_contract()
    daily_root = contract.dstock_daily.resolve(strict=False)
    if path is None:
        return contract.daily("state", "market_data_authority_status")
    resolved = Path(path).resolve(strict=False)
    if not resolved.is_relative_to(daily_root):
        raise PathContractError(
            f"authority status directory is outside dstock_daily: {path}"
        )
    return resolved


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate active published Taiwan-stock prices or explicit legacy history files."
    )
    parser.add_argument("--source-mode", choices=("published", "legacy-history"),
                        help="default: published; --history-dir explicitly selects legacy-history")
    parser.add_argument("--history-dir", type=Path, help="explicitly audit the legacy history folder")
    parser.add_argument("--as-of", help="validation date in YYYY-MM-DD (default: today)")
    parser.add_argument("--max-stale-days", type=int, default=14)
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument(
        "--authority-status-dir",
        type=Path,
        help="optional directory of exact per-file no-trade evidence sidecars",
    )
    parser.add_argument("--json-out", type=Path, help="write the complete audit report")
    parser.add_argument(
        "--repair-invalid",
        action="store_true",
        help="atomically drop invalid rows without imputation, then validate again",
    )
    parser.add_argument(
        "--strict-warnings",
        action="store_true",
        help="return a non-zero exit code when warnings exist",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.max_stale_days < 0:
        raise SystemExit("--max-stale-days must be >= 0")
    try:
        as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    except ValueError as exc:
        raise SystemExit(f"invalid --as-of: {exc}") from exc

    project_root = Path(__file__).resolve().parents[1]
    source_mode = args.source_mode or ("legacy-history" if args.history_dir is not None else "published")
    if source_mode == "published" and (args.history_dir is not None or args.authority_status_dir is not None):
        print("[ERROR] --history-dir and --authority-status-dir require legacy-history mode", file=sys.stderr)
        return 2
    if source_mode == "published" and args.repair_invalid:
        print("[ERROR] published inputs are immutable; --repair-invalid requires explicit legacy-history mode", file=sys.stderr)
        return 2
    try:
        if source_mode == "legacy-history":
            history_dir = args.history_dir or resolve_history_directory(project_root)
            authority_status_dir = resolve_authority_status_directory(args.authority_status_dir)
            report = validate_history_directory(
                history_dir, as_of=as_of, max_stale_days=args.max_stale_days,
                workers=args.workers, authority_status_dir=authority_status_dir,
            )
            if args.repair_invalid and report.get("severity_counts", {}).get("error", 0):
                repair = repair_error_files(report, as_of=as_of)
                report = validate_history_directory(
                    history_dir, as_of=as_of, max_stale_days=args.max_stale_days,
                    workers=args.workers, authority_status_dir=authority_status_dir,
                )
                report["repair"] = repair
            report["source_mode"] = "legacy-history"
        else:
            binding = _published_sources()
            if args.json_out:
                _published_output_path(args.json_out, *binding)
            report = validate_published_prices(as_of=as_of, max_stale_days=args.max_stale_days,
                                              source_binding=binding)
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except PathContractError as exc:
        print(f"[ERROR] invalid authority status path: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError, ImportError, sqlite3.Error) as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    files = report["files"]
    severities = report["severity_counts"]
    print(f"status={report['status']}")
    print(f"source_mode={report['source_mode']}")
    print(f"canonical_root={report['canonical_root']}" if source_mode == "published" else f"history_dir={report['history_dir']}")
    print(
        "files="
        f"{files['total']} healthy={files['healthy']} warning={files['warning']} "
        f"error={files['error']} rows={report['rows']}"
    )
    print(
        f"issues: error={severities.get('error', 0)} "
        f"warning={severities.get('warning', 0)}"
    )
    for code, count in report["issue_counts"].items():
        print(f"  {code}: {count}")

    if args.json_out:
        if source_mode == "published":
            try:
                # Check existing ancestors again after a long scan, before any
                # report mutation. Never silently repin the input head.
                output = _published_output_path(args.json_out, *binding)
            except (RuntimeError, OSError, ValueError) as exc:
                print(f"[ERROR] {exc}", file=sys.stderr)
                return 2
        else:
            output = args.json_out.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.tmp")
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, output)
        print(f"json_report={output}")

    if severities.get("error", 0):
        return 2
    if args.strict_warnings and severities.get("warning", 0):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
