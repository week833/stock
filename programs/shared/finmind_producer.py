"""Shared FinMind producer contracts.

This module owns the small pieces that must be identical for every D-stock
FinMind writer: dataset typing, the cross-process request ledger, and
append-only response partitions.  It deliberately does not know about the
full-market model or any other program.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


DEFAULT_SERVICE_ROOT = Path(
    os.environ.get("FINMIND_SERVICE_ROOT", r"D:\TWStockData\services\finmind")
)
QUOTA_PATH = DEFAULT_SERVICE_ROOT / "quota_v1.json"
QUOTA_GUARD_PATH = DEFAULT_SERVICE_ROOT / "quota_v1.guard"
OWNER_PATH = QUOTA_GUARD_PATH / "owner.json"
LOCK_PATH = QUOTA_GUARD_PATH / ".lock"
QUOTA_SCHEMA_VERSION = 1
QUOTA_WINDOW_SECONDS = 3600
TOKEN_HOURLY_LIMIT = 6000
GUARD_STALE_SECONDS = QUOTA_WINDOW_SECONDS

PARTITION_SCHEMA = "finmind_incremental_partition_v1"
PARTITION_DIR_NAME = "_partitions"
PARTITION_MANIFEST_NAME = "partition_manifest_v1.json"
PARTITION_INDEX_NAME = "partition_index_v1.json"
PARTITION_LOCK_NAME = ".partition_sidecar.lock"

_TAIPEI = _dt.timezone(_dt.timedelta(hours=8), name="Asia/Taipei")
_DATE_FIELDS = {"stock_id", "date"}
_META_FIELDS = {"_available_at", "_source", "_source_url", "_freshness"}
_CODE_RE = re.compile(r"[0-9][0-9A-Z]{3,7}")
_DATASET_RE = re.compile(r"[A-Za-z0-9_.-]+")


class FinMindProducerError(RuntimeError):
    """Base error for a contract or coordination failure."""


class CentralOwnerOnlyError(FinMindProducerError):
    """Raised because raw partition/cache ownership belongs to Central."""

    error_class = "central_owner_only"

    def __init__(self, operation: str) -> None:
        self.payload = {
            "error_class": self.error_class,
            "operation": str(operation),
            "owner": "central_market_update",
            "network": False,
            "writes": False,
        }
        super().__init__(f"central_owner_only:{operation}")


def _central_owner_only(operation: str) -> None:
    raise CentralOwnerOnlyError(operation)


class DatasetValidationError(FinMindProducerError):
    """Raised when a provider row is not valid for its dataset schema."""


class QuotaLedgerError(FinMindProducerError):
    """Raised when the shared quota ledger cannot be trusted."""


class QuotaBusyError(FinMindProducerError):
    """Raised only for a non-blocking guard acquisition."""


class QuotaExhaustedError(FinMindProducerError):
    """Raised when a caller explicitly requests a non-blocking reservation."""

    def __init__(self, *, used: int, remaining: int, reset_at: float, limit: int):
        self.used = int(used)
        self.remaining = int(remaining)
        self.reset_at = float(reset_at)
        self.limit = int(limit)
        super().__init__("quota_exhausted")


class PartitionContractError(FinMindProducerError):
    """Raised when an immutable partition or sidecar is inconsistent."""


@dataclass(frozen=True, slots=True)
class DatasetSchema:
    name: str
    numeric_fields: frozenset[str]
    text_fields: frozenset[str]
    core: bool = True

    @property
    def fields(self) -> frozenset[str]:
        return frozenset(_DATE_FIELDS) | self.numeric_fields | self.text_fields | _META_FIELDS


def _schema(
    name: str,
    numeric_fields: Iterable[str],
    text_fields: Iterable[str] = (),
    *,
    core: bool = True,
) -> DatasetSchema:
    return DatasetSchema(
        name=name,
        numeric_fields=frozenset(numeric_fields),
        text_fields=frozenset(text_fields),
        core=core,
    )


DATASET_SCHEMAS: Mapping[str, DatasetSchema] = {
    "TaiwanStockMarginPurchaseShortSale": _schema(
        "TaiwanStockMarginPurchaseShortSale",
        (
            "MarginPurchaseBuy",
            "MarginPurchaseCashRepayment",
            "MarginPurchaseLimit",
            "MarginPurchaseSell",
            "MarginPurchaseTodayBalance",
            "MarginPurchaseYesterdayBalance",
            "OffsetLoanAndShort",
            "ShortSaleBuy",
            "ShortSaleCashRepayment",
            "ShortSaleLimit",
            "ShortSaleSell",
            "ShortSaleTodayBalance",
            "ShortSaleYesterdayBalance",
        ),
        ("Note",),
    ),
    "TaiwanStockShareholding": _schema(
        "TaiwanStockShareholding",
        (
            "ForeignInvestmentRemainingShares",
            "ForeignInvestmentShares",
            "ForeignInvestmentRemainRatio",
            "ForeignInvestmentSharesRatio",
            "ForeignInvestmentUpperLimitRatio",
            "ChineseInvestmentUpperLimitRatio",
            "NumberOfSharesIssued",
        ),
        ("stock_name", "InternationalCode", "RecentlyDeclareDate", "note"),
    ),
    "TaiwanStockDayTrading": _schema(
        "TaiwanStockDayTrading",
        ("Volume", "BuyAmount", "SellAmount"),
        ("BuyAfterSale",),
    ),
    "TaiwanStockPER": _schema(
        "TaiwanStockPER",
        ("dividend_yield", "PER", "PBR"),
    ),
    "TaiwanStockInstitutionalInvestorsBuySellWide": _schema(
        "TaiwanStockInstitutionalInvestorsBuySellWide",
        (
            "Foreign_Investor_buy",
            "Foreign_Investor_sell",
            "Foreign_Dealer_Self_buy",
            "Foreign_Dealer_Self_sell",
            "Investment_Trust_buy",
            "Investment_Trust_sell",
            "Dealer_buy",
            "Dealer_sell",
            "Dealer_self_buy",
            "Dealer_self_sell",
            "Dealer_Hedging_buy",
            "Dealer_Hedging_sell",
        ),
    ),
}

FEATURE_DATASET_SCHEMAS = DATASET_SCHEMAS


def get_dataset_schema(dataset: str) -> DatasetSchema:
    try:
        return DATASET_SCHEMAS[str(dataset)]
    except KeyError as exc:
        raise DatasetValidationError("unknown_dataset") from exc


def _date_key(value: Any) -> str | None:
    # A JSON number (20261002) or bool is never a date: str() of it would pass
    # the digit check below and silently become a session key.  The provider
    # separators accepted below are FinMind wire leniency and stay unchanged.
    if isinstance(value, (bool, int, float)):
        return None
    text = str(value or "").strip()
    if "T" in text:
        text = text.split("T", 1)[0]
    digits = re.sub(r"[^0-9]", "", text)
    if len(digits) != 8:
        return None
    try:
        _dt.datetime.strptime(digits, "%Y%m%d")
    except ValueError:
        return None
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"


def _code(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    return text if _CODE_RE.fullmatch(text) else None


def _number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", "")
        if not value:
            return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    return number


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (Mapping, list, tuple, set)):
        raise DatasetValidationError("text_field_invalid")
    if not isinstance(value, str):
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError):
            numeric = None
        if numeric is not None and not math.isfinite(numeric):
            raise DatasetValidationError("text_field_invalid")
    return str(value)


def validate_dataset_row(
    dataset: str,
    row: Mapping[str, Any],
    *,
    session: str | None = None,
) -> dict[str, Any]:
    """Validate one row with the dataset's native field types.

    Unknown fields remain fail-closed.  Known text fields are preserved as
    text, including blank provider values such as ``Note`` and
    ``BuyAfterSale``; they are not sent through numeric coercion.
    """

    schema = get_dataset_schema(dataset)
    if not isinstance(row, Mapping):
        raise DatasetValidationError("row_invalid")
    unknown = {str(key) for key in row if str(key) not in schema.fields}
    if unknown:
        raise DatasetValidationError("unknown_field")
    code = _code(row.get("stock_id"))
    date = _date_key(row.get("date"))
    if code is None or date is None:
        raise DatasetValidationError("identity_invalid")
    if session is not None:
        session_key = _date_key(session)
        if session_key is None or date != session_key:
            raise DatasetValidationError("session_mismatch")
    result: dict[str, Any] = {"stock_id": code, "date": date}
    numeric_count = 0
    for key in sorted(str(name) for name in row if str(name) not in _DATE_FIELDS):
        value = row.get(key)
        if key in schema.text_fields or key in _META_FIELDS:
            result[key] = _text(value)
            continue
        if key not in schema.numeric_fields:
            raise DatasetValidationError("field_type_unknown")
        parsed = _number(value)
        if parsed is None:
            raise DatasetValidationError("numeric_field_invalid")
        result[key] = parsed
        numeric_count += 1
    if numeric_count == 0:
        raise DatasetValidationError("numeric_fields_missing")
    return result


def normalize_dataset_rows(
    dataset: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    session: str | None = None,
) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        iterator = iter(rows)
    except TypeError as exc:
        raise DatasetValidationError("rows_invalid") from exc
    for raw in iterator:
        normalized = validate_dataset_row(dataset, raw, session=session)
        key = (normalized["stock_id"], normalized["date"])
        previous = by_key.get(key)
        if previous is not None and _canonical(previous) != _canonical(normalized):
            raise DatasetValidationError("duplicate_conflict")
        by_key[key] = normalized
    return [by_key[key] for key in sorted(by_key)]


def freshness_status(
    fetched_at: Any,
    *,
    now: float | None = None,
    max_age_seconds: int = 86400,
    cache_available: bool = True,
    core: bool = True,
) -> dict[str, Any]:
    """Return an explicit freshness decision for cached supplemental data."""

    if not cache_available:
        return {"status": "unavailable", "usable": False, "age_seconds": None}
    text = str(fetched_at or "").strip().replace("Z", "+00:00")
    try:
        parsed = _dt.datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.timezone.utc)
        age = max(0.0, float(now if now is not None else time.time()) - parsed.timestamp())
    except (TypeError, ValueError, OverflowError):
        return {
            "status": "invalid_cache",
            "usable": False if core else True,
            "age_seconds": None,
        }
    fresh = age <= max(0, int(max_age_seconds))
    return {
        "status": "fresh" if fresh else ("stale_core" if core else "stale_cache"),
        "usable": bool(fresh or not core),
        "age_seconds": int(age),
        "max_age_seconds": int(max_age_seconds),
        "core": bool(core),
    }


def cache_fallback(
    dataset: str,
    cached: Any,
    *,
    fetched_at: Any,
    now: float | None = None,
    max_age_seconds: int = 86400,
    core: bool | None = None,
) -> dict[str, Any]:
    """Select a cached response without hiding its freshness state."""

    schema = get_dataset_schema(dataset)
    freshness = freshness_status(
        fetched_at,
        now=now,
        max_age_seconds=max_age_seconds,
        cache_available=cached is not None,
        core=schema.core if core is None else bool(core),
    )
    if freshness["usable"]:
        return {"status": freshness["status"], "usable": True, "data": cached, "freshness": freshness}
    return {"status": freshness["status"], "usable": False, "data": None, "freshness": freshness}


def token_fingerprint(token: str) -> str:
    value = str(token or "").strip()
    if not value:
        raise QuotaLedgerError("token_required")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _iso(stamp: float) -> str:
    return _dt.datetime.fromtimestamp(float(stamp), tz=_dt.timezone.utc).isoformat(timespec="seconds")


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError) as exc:
        raise QuotaLedgerError("quota_ledger_invalid") from exc


class _GuardLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle: Any = None

    def __enter__(self) -> "_GuardLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(" ")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            self.handle.close()
            self.handle = None
            raise QuotaBusyError("quota_guard_busy") from exc
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                self.handle.seek(0)
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


@dataclass(frozen=True, slots=True)
class QuotaReservation:
    dataset: str
    stock_id: str
    token_fingerprint: str
    used: int
    remaining: int
    reset_at: float
    limit: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "stock_id": self.stock_id,
            "token_fingerprint": self.token_fingerprint,
            "used": self.used,
            "remaining": self.remaining,
            "reset_at": _iso(self.reset_at),
            "reset_at_epoch": self.reset_at,
            "limit": self.limit,
        }


def _progress_event(
    *,
    dataset: str,
    stock_id: str,
    used: int,
    remaining: int,
    reset_at: float,
    overall_progress: Mapping[str, Any] | None,
    event: str,
) -> dict[str, Any]:
    overall = dict(overall_progress or {})
    return {
        "event": event,
        "dataset": dataset,
        "stock_id": stock_id,
        "stock": stock_id,
        "used": int(used),
        "remaining": int(remaining),
        "reset_at": _iso(reset_at),
        "reset": _iso(reset_at),
        "reset_at_epoch": float(reset_at),
        "overall_progress": overall,
        "overall": dict(overall),
    }


def print_finmind_progress(event: Mapping[str, Any]) -> None:
    """Default progress sink; it never prints a token or request URL."""

    print(
        "[FinMind] "
        f"{event.get('event', 'progress')} "
        f"dataset={event.get('dataset', '')} "
        f"stock={event.get('stock_id', '')} "
        f"used={event.get('used', '')} "
        f"remaining={event.get('remaining', '')} "
        f"reset={event.get('reset_at', '')} "
        f"overall={event.get('overall_progress', {})}",
        flush=True,
    )


class SharedFinMindQuota:
    """Atomic per-token sliding-window quota shared by all D-stock writers."""

    def __init__(
        self,
        *,
        service_root: Path | str = DEFAULT_SERVICE_ROOT,
        limit: int = TOKEN_HOURLY_LIMIT,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        stale_after_seconds: float = GUARD_STALE_SECONDS,
    ):
        self.service_root = Path(service_root)
        self.quota_path = self.service_root / "quota_v1.json"
        self.guard_path = self.service_root / "quota_v1.guard"
        self.owner_path = self.guard_path / "owner.json"
        self.lock_path = self.guard_path / ".lock"
        self.limit = max(1, int(limit))
        self.clock = clock
        self.sleep = sleep
        try:
            stale_after = float(stale_after_seconds)
        except (TypeError, ValueError, OverflowError) as exc:
            raise QuotaLedgerError("quota_stale_timeout_invalid") from exc
        if not math.isfinite(stale_after) or stale_after < 0:
            raise QuotaLedgerError("quota_stale_timeout_invalid")
        self.stale_after_seconds = stale_after

    def _ensure_paths(self) -> None:
        self.service_root.mkdir(parents=True, exist_ok=True)
        if self.guard_path.exists() and not self.guard_path.is_dir():
            raise QuotaLedgerError("quota_guard_invalid")
        self.guard_path.mkdir(parents=True, exist_ok=True)

    def _read_records(self, now: float) -> tuple[list[dict[str, Any]], int]:
        payload = _load_json(self.quota_path)
        if payload is None:
            return [], self.limit
        if not isinstance(payload, Mapping):
            raise QuotaLedgerError("quota_ledger_invalid")
        try:
            declared = int(payload.get("limit") or self.limit)
        except (TypeError, ValueError) as exc:
            raise QuotaLedgerError("quota_ledger_invalid") from exc
        if declared <= 0:
            raise QuotaLedgerError("quota_ledger_invalid")
        records: list[dict[str, Any]] = []
        raw_requests = payload.get("requests")
        if raw_requests is not None and not isinstance(raw_requests, list):
            raise QuotaLedgerError("quota_ledger_invalid")
        for raw in raw_requests or []:
            if isinstance(raw, Mapping):
                stamp_value = raw.get("at", raw.get("timestamp"))
                fingerprint = str(raw.get("token_fingerprint") or "")
            else:
                stamp_value = raw
                fingerprint = ""
            try:
                stamp = float(stamp_value)
            except (TypeError, ValueError) as exc:
                raise QuotaLedgerError("quota_ledger_invalid") from exc
            if not math.isfinite(stamp):
                raise QuotaLedgerError("quota_ledger_invalid")
            records.append({"at": stamp, "token_fingerprint": fingerprint})
        if not records and "window_start" in payload and "used" in payload:
            try:
                window_start = float(payload["window_start"])
                used = int(payload["used"])
            except (TypeError, ValueError) as exc:
                raise QuotaLedgerError("quota_ledger_invalid") from exc
            if not math.isfinite(window_start) or used < 0:
                raise QuotaLedgerError("quota_ledger_invalid")
            if window_start + QUOTA_WINDOW_SECONDS > now:
                records = [{"at": now, "token_fingerprint": ""} for _ in range(used)]
        current = [record for record in records if float(record["at"]) > now - QUOTA_WINDOW_SECONDS]
        effective_limit = min(self.limit, declared) if current else self.limit
        return current, max(1, effective_limit)

    @staticmethod
    def _token_records(records: Iterable[Mapping[str, Any]], fingerprint: str) -> list[float]:
        values = [float(record["at"]) for record in records if record.get("token_fingerprint") == fingerprint]
        # A legacy aggregate ledger has no token identity.  Keep those
        # observations conservative even after this token has new keyed rows.
        legacy = [float(record["at"]) for record in records if not record.get("token_fingerprint")]
        return values + legacy

    def _write(self, records: list[dict[str, Any]], *, now: float, limit: int | None = None) -> None:
        by_token: dict[str, list[float]] = {}
        for record in records:
            fingerprint = str(record.get("token_fingerprint") or "")
            if fingerprint:
                by_token.setdefault(fingerprint, []).append(float(record["at"]))
        timestamps = sorted(float(record["at"]) for record in records)
        payload = {
            "schema_version": QUOTA_SCHEMA_VERSION,
            "window_seconds": QUOTA_WINDOW_SECONDS,
            "limit": max(1, int(limit if limit is not None else self.limit)),
            "window_start": min(timestamps) if timestamps else now,
            "used": len(timestamps),
            "requests": [
                {
                    "at": float(record["at"]),
                    "token_fingerprint": str(record.get("token_fingerprint") or ""),
                }
                for record in sorted(records, key=lambda item: float(item["at"]))
            ],
            "by_token": {key: sorted(values) for key, values in sorted(by_token.items())},
            "updated_at": _iso(now),
        }
        _atomic_json(self.quota_path, payload)

    def _write_owner(self, payload: Mapping[str, Any]) -> None:
        _atomic_json(self.owner_path, dict(payload))

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            # os.kill(pid, 0) is not a harmless existence probe on Windows.
            # OpenProcess lets stale-owner recovery remain conservative without
            # sending a termination signal to the probed process.
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            process_query_limited_information = 0x1000
            handle = kernel32.OpenProcess(
                process_query_limited_information,
                False,
                wintypes.DWORD(pid),
            )
            if handle:
                kernel32.CloseHandle(handle)
                return True
            # An access-denied result means the process cannot be disproved.
            return ctypes.get_last_error() == 5
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _recover_stale_owner(self, now: float) -> bool:
        """Remove only an owner proven dead and older than the stale timeout."""

        if not self.owner_path.is_file():
            return False
        try:
            payload = _load_json(self.owner_path)
        except QuotaLedgerError:
            return False
        if not isinstance(payload, Mapping):
            return False
        raw_pid = payload.get("pid")
        if isinstance(raw_pid, bool):
            return False
        try:
            pid = int(raw_pid)
            updated_text = str(payload.get("updated_at") or "").strip().replace("Z", "+00:00")
            updated = _dt.datetime.fromisoformat(updated_text)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=_dt.timezone.utc)
            updated_at = float(updated.timestamp())
        except (TypeError, ValueError, OverflowError, OSError):
            return False
        if self._pid_exists(pid) or now - updated_at < self.stale_after_seconds:
            return False
        try:
            self.owner_path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise QuotaLedgerError("quota_owner_recovery_failed") from exc
        return True

    def snapshot(self, token: str | None = None) -> dict[str, Any]:
        self._ensure_paths()
        now = float(self.clock())
        fingerprint = token_fingerprint(token) if token else ""
        while True:
            try:
                with _GuardLock(self.lock_path):
                    self._recover_stale_owner(now)
                    records, limit = self._read_records(now)
                    values = self._token_records(records, fingerprint) if fingerprint else [float(r["at"]) for r in records]
                    used = len(values)
                    reset_at = min(values) + QUOTA_WINDOW_SECONDS if values else now
                    return {
                        "schema_version": QUOTA_SCHEMA_VERSION,
                        "limit": limit,
                        "used": used,
                        "remaining": max(0, limit - used),
                        "reset_at": _iso(reset_at),
                        "reset_at_epoch": reset_at,
                        "quota_path": str(self.quota_path),
                        "owner_path": str(self.owner_path),
                    }
            except QuotaBusyError:
                self.sleep(0.2)

    def acquire(
        self,
        token: str,
        *,
        dataset: str = "",
        stock_id: str = "",
        wait: bool = True,
        progress: Callable[[Mapping[str, Any]], None] | None = print_finmind_progress,
        overall_progress: Mapping[str, Any] | None = None,
    ) -> QuotaReservation:
        fingerprint = token_fingerprint(token)
        dataset_text = str(dataset or "")
        stock_text = str(stock_id or "")
        self._ensure_paths()
        while True:
            now = float(self.clock())
            try:
                with _GuardLock(self.lock_path):
                    self._recover_stale_owner(now)
                    records, limit = self._read_records(now)
                    values = self._token_records(records, fingerprint)
                    used = len(values)
                    if used < limit:
                        records.append({"at": now, "token_fingerprint": fingerprint})
                        self._write(records, now=now, limit=limit)
                        reservation = QuotaReservation(
                            dataset=dataset_text,
                            stock_id=stock_text,
                            token_fingerprint=fingerprint,
                            used=used + 1,
                            remaining=max(0, limit - used - 1),
                            reset_at=(min(values) + QUOTA_WINDOW_SECONDS if values else now + QUOTA_WINDOW_SECONDS),
                            limit=limit,
                        )
                        self._write_owner(
                            {
                                "schema_version": QUOTA_SCHEMA_VERSION,
                                "state": "acquired",
                                "pid": os.getpid(),
                                "owner_id": uuid.uuid4().hex,
                                "dataset": dataset_text,
                                "stock_id": stock_text,
                                "token_fingerprint": fingerprint,
                                "used": reservation.used,
                                "remaining": reservation.remaining,
                                "reset_at": _iso(reservation.reset_at),
                                "updated_at": _iso(now),
                            }
                        )
                        if progress:
                            progress(
                                _progress_event(
                                    dataset=dataset_text,
                                    stock_id=stock_text,
                                    used=reservation.used,
                                    remaining=reservation.remaining,
                                    reset_at=reservation.reset_at,
                                    overall_progress=overall_progress,
                                    event="quota_acquired",
                                )
                            )
                        return reservation
                    reset_at = min(values) + QUOTA_WINDOW_SECONDS if values else now + QUOTA_WINDOW_SECONDS
                    event = _progress_event(
                        dataset=dataset_text,
                        stock_id=stock_text,
                        used=used,
                        remaining=0,
                        reset_at=reset_at,
                        overall_progress=overall_progress,
                        event="quota_wait",
                    )
                    self._write_owner(
                        {
                            "schema_version": QUOTA_SCHEMA_VERSION,
                            "state": "waiting",
                            "pid": os.getpid(),
                            "owner_id": uuid.uuid4().hex,
                            "dataset": dataset_text,
                            "stock_id": stock_text,
                            "token_fingerprint": fingerprint,
                            "used": used,
                            "remaining": 0,
                            "reset_at": _iso(reset_at),
                            "updated_at": _iso(now),
                        }
                    )
                if progress:
                    progress(event)
                if not wait:
                    raise QuotaExhaustedError(
                        used=used,
                        remaining=0,
                        reset_at=reset_at,
                        limit=limit,
                    )
                delay = max(0.1, reset_at - float(self.clock()))
                self._sleep_with_progress(
                    delay,
                    event,
                    progress=progress,
                    overall_progress=overall_progress,
                )
            except QuotaBusyError:
                if not wait:
                    raise
                self.sleep(0.2)

    def _sleep_with_progress(
        self,
        seconds: float,
        event: Mapping[str, Any],
        *,
        progress: Callable[[Mapping[str, Any]], None] | None,
        overall_progress: Mapping[str, Any] | None,
    ) -> None:
        remaining = max(0.0, float(seconds))
        while remaining > 0:
            chunk = min(30.0, remaining)
            self.sleep(chunk)
            remaining -= chunk
            if progress:
                refreshed = dict(event)
                refreshed["event"] = "quota_wait_heartbeat"
                refreshed["wait_remaining_seconds"] = int(max(0.0, remaining))
                refreshed["overall_progress"] = dict(overall_progress or {})
                progress(refreshed)

    def wait_retry_after(
        self,
        seconds: float,
        *,
        dataset: str = "",
        stock_id: str = "",
        progress: Callable[[Mapping[str, Any]], None] | None = print_finmind_progress,
        overall_progress: Mapping[str, Any] | None = None,
    ) -> None:
        delay = max(0.0, float(seconds))
        now = float(self.clock())
        event = _progress_event(
            dataset=str(dataset or ""),
            stock_id=str(stock_id or ""),
            used=0,
            remaining=0,
            reset_at=now + delay,
            overall_progress=overall_progress,
            event="retry_after_wait",
        )
        if progress:
            progress(event)
        self._sleep_with_progress(delay, event, progress=progress, overall_progress=overall_progress)


def _safe_component(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text or text in {".", ".."} or not _DATASET_RE.fullmatch(text):
        raise PartitionContractError(f"{label}_invalid")
    return text


def _partition_payload_bytes(payload: Mapping[str, Any]) -> bytes:
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    except (TypeError, ValueError) as exc:
        raise PartitionContractError("partition_payload_invalid") from exc
    return text.encode("utf-8")


def _atomic_create_bytes(path: Path, content: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        return True
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _sidecar(path: Path, schema: str, root_key: str) -> dict[str, Any]:
    if not path.exists():
        return {"schema": schema, "schema_version": 1, root_key: {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise PartitionContractError("partition_sidecar_invalid") from exc
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema") != schema
        or payload.get("schema_version") != 1
        or not isinstance(payload.get(root_key), Mapping)
    ):
        raise PartitionContractError("partition_sidecar_invalid")
    return dict(payload)


def write_immutable_partition(
    cache_root: Path | str,
    dataset: str,
    session: str,
    payload: Mapping[str, Any],
    *,
    request_fingerprint: str = "",
    fetched_at: Any = None,
) -> dict[str, Any]:
    """Write one response once and append only its sidecar index entries."""

    _central_owner_only("finmind_partition_write")

    dataset_name = _safe_component(dataset, "dataset")
    session_key = _date_key(session)
    if session_key is None:
        raise PartitionContractError("session_invalid")
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
        raise PartitionContractError("partition_payload_invalid")
    root = Path(cache_root)
    if any(not isinstance(row, Mapping) for row in payload["data"]):
        raise PartitionContractError("partition_row_invalid")
    data = [dict(row) for row in payload["data"]]
    normalized_payload = {
        "schema": PARTITION_SCHEMA,
        "schema_version": 1,
        "dataset": dataset_name,
        "session": session_key,
        "params": dict(payload.get("params") or {}),
        "fetched_at": str(fetched_at or payload.get("fetched_at") or _iso(time.time())),
        "data": data,
    }
    content = _partition_payload_bytes(normalized_payload)
    digest = hashlib.sha256(content).hexdigest()
    relative = Path(PARTITION_DIR_NAME) / dataset_name / f"{session_key.replace('-', '')}_{digest[:24]}.json"
    target = root / relative
    created = _atomic_create_bytes(target, content)
    if not created:
        try:
            if target.read_bytes() != content:
                raise PartitionContractError("partition_conflict")
        except OSError as exc:
            raise PartitionContractError("partition_unreadable") from exc

    manifest_path = root / PARTITION_MANIFEST_NAME
    index_path = root / PARTITION_INDEX_NAME
    relative_text = relative.as_posix()
    entry = {
        "dataset": dataset_name,
        "session": session_key,
        "path": relative_text,
        "sha256": digest,
        "row_count": len(data),
        "request_fingerprint": str(request_fingerprint or ""),
        "fetched_at": normalized_payload["fetched_at"],
    }
    while True:
        try:
            with _GuardLock(root / PARTITION_LOCK_NAME):
                manifest = _sidecar(manifest_path, "finmind_incremental_partition_manifest_v1", "partitions")
                index = _sidecar(index_path, "finmind_incremental_partition_index_v1", "datasets")
                partitions = dict(manifest.get("partitions") or {})
                old = partitions.get(relative_text)
                if old is not None and _canonical(old) != _canonical(entry):
                    raise PartitionContractError("partition_manifest_conflict")
                partitions[relative_text] = entry
                manifest.update({"partitions": partitions, "updated_at": _iso(time.time())})
                datasets = dict(index.get("datasets") or {})
                dataset_entries = dict(datasets.get(dataset_name) or {})
                session_entries = list(dataset_entries.get(session_key) or [])
                if relative_text not in session_entries:
                    session_entries.append(relative_text)
                    session_entries.sort()
                dataset_entries[session_key] = session_entries
                datasets[dataset_name] = dataset_entries
                index.update({"datasets": datasets, "updated_at": _iso(time.time())})
                _atomic_json(manifest_path, manifest)
                _atomic_json(index_path, index)
            break
        except QuotaBusyError:
            time.sleep(0.05)
    return {
        "path": target,
        "relative_path": relative_text,
        "sha256": digest,
        "row_count": len(data),
        "created": created,
        "manifest_path": manifest_path,
        "index_path": index_path,
    }


def iter_partition_paths(cache_root: Path | str, dataset: str) -> list[Path]:
    _central_owner_only("finmind_partition_cache_read")
    dataset_name = _safe_component(dataset, "dataset")
    directory = Path(cache_root) / PARTITION_DIR_NAME / dataset_name
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.json") if path.is_file())


def read_partition_payload(path: Path) -> dict[str, Any] | None:
    _central_owner_only("finmind_partition_cache_read")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(payload, Mapping) or payload.get("schema") != PARTITION_SCHEMA:
        return None
    if not isinstance(payload.get("data"), list):
        return None
    return dict(payload)


def partition_status(cache_root: Path | str) -> dict[str, Any]:
    _central_owner_only("finmind_partition_cache_read")
    root = Path(cache_root)
    path = root / PARTITION_MANIFEST_NAME
    if not path.is_file():
        return {"quality": "unknown", "reason": "partition_manifest_missing", "file_count": 0, "latest_by_dataset": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError):
        return {"quality": "unknown", "reason": "partition_manifest_invalid", "file_count": 0, "latest_by_dataset": {}}
    index_path = root / PARTITION_INDEX_NAME
    if not index_path.is_file():
        return {"quality": "unknown", "reason": "partition_index_missing", "file_count": 0, "latest_by_dataset": {}}
    try:
        index_payload = json.loads(index_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError):
        return {"quality": "unknown", "reason": "partition_index_invalid", "file_count": 0, "latest_by_dataset": {}}
    if (
        not isinstance(index_payload, Mapping)
        or index_payload.get("schema") != "finmind_incremental_partition_index_v1"
        or index_payload.get("schema_version") != 1
        or not isinstance(index_payload.get("datasets"), Mapping)
    ):
        return {"quality": "unknown", "reason": "partition_index_invalid", "file_count": 0, "latest_by_dataset": {}}
    partitions = payload.get("partitions") if isinstance(payload, Mapping) else None
    if not isinstance(partitions, Mapping):
        return {"quality": "unknown", "reason": "partition_manifest_invalid", "file_count": 0, "latest_by_dataset": {}}
    latest: dict[str, str] = {}
    valid = 0
    for raw in partitions.values():
        if not isinstance(raw, Mapping):
            continue
        dataset = str(raw.get("dataset") or "")
        session = _date_key(raw.get("session"))
        relative = str(raw.get("path") or "")
        indexed = index_payload["datasets"].get(dataset) if isinstance(index_payload["datasets"], Mapping) else None
        indexed_sessions = indexed.get(session) if isinstance(indexed, Mapping) else None
        if (
            dataset
            and session
            and relative
            and isinstance(indexed_sessions, list)
            and relative in indexed_sessions
            and (root / relative).is_file()
        ):
            valid += 1
            latest[dataset] = max(session, latest.get(dataset, ""))
    return {
        "quality": "known" if valid else "stale",
        "reason": None if valid else "partition_manifest_empty",
        "file_count": valid,
        "latest_by_dataset": latest,
        "updated_at": payload.get("updated_at"),
    }


__all__ = [
    "DATASET_SCHEMAS",
    "CentralOwnerOnlyError",
    "DatasetSchema",
    "DatasetValidationError",
    "FEATURE_DATASET_SCHEMAS",
    "FinMindProducerError",
    "GUARD_STALE_SECONDS",
    "OWNER_PATH",
    "PARTITION_DIR_NAME",
    "PARTITION_INDEX_NAME",
    "PARTITION_LOCK_NAME",
    "PARTITION_MANIFEST_NAME",
    "PARTITION_SCHEMA",
    "PartitionContractError",
    "QUOTA_GUARD_PATH",
    "QUOTA_PATH",
    "QuotaBusyError",
    "QuotaExhaustedError",
    "QuotaLedgerError",
    "QuotaReservation",
    "SharedFinMindQuota",
    "TOKEN_HOURLY_LIMIT",
    "cache_fallback",
    "freshness_status",
    "get_dataset_schema",
    "iter_partition_paths",
    "normalize_dataset_rows",
    "partition_status",
    "print_finmind_progress",
    "read_partition_payload",
    "token_fingerprint",
    "validate_dataset_row",
    "write_immutable_partition",
]
