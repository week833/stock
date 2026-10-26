"""Verified, content-addressed JSONL projections for published stock routes.

The canonical stock publication remains the source of truth.  This module adds
an independently verifiable query cache under ``_query`` and never changes the
published head or its receipts.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import errno
import hashlib
import heapq
import io
import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path

try:
    import date_format as cd
except ModuleNotFoundError:  # shared/ is not on sys.path yet (module imported directly)
    import sys as _sys
    _sys.path.append(str(Path(__file__).resolve().parents[2] / "shared"))
    import date_format as cd

import stock_cache_lifecycle as cache_lifecycle


SCHEMA = "stock-query-projection-v1"
COMPLETE_SCHEMA = "stock-query-projection-complete-v1"
PROJECTION_SCHEMA_VERSION = 1
QUERY_DIR = cache_lifecycle.QUERY_CACHE_DIR
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_YEAR_BUCKET_ROUTES = frozenset({
    "finmind_month_revenue",
    "finmind_financial_statements",
    "finmind_balance_sheet",
    "finmind_cash_flows_statement",
})
_STOCK_BUCKET_LAYOUT_POLICY = {
    "version": 3,
    "stock_period": "__stock__",
    "undated_period": "__undated__",
    "stockless_period_policy": "native_year_else_month",
}
_STOCK_FIELDS = ("stock_id", "data_id", "code")
_DATE_FIELDS = ("date", "observation_date", "report_date", "date_id", "period_end")
_MAX_OPEN_ROWS_SHARDS = 128
_DEFAULT_CHUNK_ROWS = 4096
_DEFAULT_CHUNK_BYTES = 8 * 1024 * 1024
_WRITE_BUFFER_BYTES = 64 * 1024
_MAX_PENDING_WRITE_BYTES = 64 * 1024 * 1024
_SPOOL_BATCH_ROWS = 1024
_SPOOL_BATCH_BYTES = 1024 * 1024
_SPOOL_FETCH_ROWS = 512


class ProjectionError(RuntimeError):
    """Base class for query-projection failures."""


class UnsupportedProjectionError(ProjectionError):
    """The route or requested filter cannot be represented without data loss."""


class ProjectionUnavailableError(ProjectionError):
    """No projection exists and the caller explicitly disabled cold building."""


class ProjectionLockTimeoutError(ProjectionError):
    """A concurrent projection build held the single-writer lock too long."""

    def __init__(self, message="projection_writer_lock_timeout", *, lock_path=None,
                 elapsed_seconds=None, timeout_seconds=None):
        self.lock_path = str(lock_path) if lock_path is not None else None
        self.elapsed_seconds = elapsed_seconds
        self.timeout_seconds = timeout_seconds
        super().__init__(message)


class ProjectionStreamingRequiredError(ProjectionError):
    """The selected shard count requires the verified-shard streaming API."""


class ProjectionResultLimitError(ProjectionError):
    """An explicit collect operation exceeded its configured safety limit."""


class ProjectionIntegrityError(ProjectionError):
    """A pinned artifact, projection index, or shard failed verification."""


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProjectionIntegrityError("duplicate_json_key")
        result[key] = value
    return result


def _constant(_value):
    raise ProjectionIntegrityError("nonfinite_json_number")


def _json_loads(value):
    try:
        return json.loads(value, object_pairs_hook=_pairs, parse_constant=_constant)
    except ProjectionIntegrityError:
        raise
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise ProjectionIntegrityError("invalid_json") from exc


def _canonical_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProjectionIntegrityError("noncanonical_identity") from exc


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _identity_hash(value):
    if value is None or value == "":
        return None
    return _sha256(_canonical_bytes(value))


def _file_hash(path):
    digest, size = hashlib.sha256(), 0
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _normalise_relative(value):
    if not isinstance(value, str) or not value.strip():
        raise ProjectionIntegrityError("artifact_relative_path_missing")
    value = value.strip().replace("\\", "/")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ProjectionIntegrityError("artifact_path_must_be_relative")
    parts = [part for part in value.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise ProjectionIntegrityError("artifact_path_escapes_root")
    return "/".join(parts)


def _descriptor_value(descriptor, primary, alias, normalise=None):
    first = descriptor.get(primary)
    second = descriptor.get(alias)
    if first is not None and second is not None:
        left = normalise(first) if normalise else first
        right = normalise(second) if normalise else second
        if left != right:
            raise ProjectionIntegrityError("artifact_descriptor_alias_mismatch")
        return left
    value = first if first is not None else second
    return normalise(value) if value is not None and normalise else value


def _prepare_descriptor(root, descriptor, route_id, parent_identity, source_identity):
    if not isinstance(descriptor, dict):
        raise ProjectionIntegrityError("artifact_descriptor_not_object")
    declared_route = descriptor.get("route_id")
    if declared_route is not None and declared_route != route_id:
        raise ProjectionIntegrityError("artifact_route_mismatch")
    relative = _descriptor_value(descriptor, "relative_path", "path", _normalise_relative)
    digest = descriptor.get("sha256")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest.lower()):
        raise ProjectionIntegrityError("artifact_sha256_invalid")
    digest = digest.lower()
    size = _descriptor_value(descriptor, "size_bytes", "size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ProjectionIntegrityError("artifact_size_invalid")
    if not isinstance(route_id, str) or not route_id.strip():
        raise ValueError("route_id must be a nonempty string")
    parent = parent_identity if parent_identity is not None else descriptor.get("parent_identity")
    if parent is None or parent == "":
        raise ProjectionIntegrityError("pinned_parent_identity_required")
    source = source_identity if source_identity is not None else descriptor.get("source_identity")
    root = Path(root).resolve()
    artifact = (root / Path(*relative.split("/"))).resolve()
    if not artifact.is_relative_to(root):
        raise ProjectionIntegrityError("artifact_path_escapes_root")
    try:
        parent_hash = _identity_hash(parent)
        source_hash = _identity_hash(source)
    except ProjectionIntegrityError:
        raise
    return {
        "root": root,
        "artifact": artifact,
        "route_id": route_id,
        "relative_path": relative,
        "sha256": digest,
        "size_bytes": size,
        "parent_identity": parent,
        "parent_identity_sha256": parent_hash,
        "source_identity": source,
        "source_identity_sha256": source_hash,
        "publication_entry": dict(descriptor),
    }


def _physical_identity(prepared):
    root_text = prepared["root"].as_posix()
    if os.name == "nt":
        root_text = root_text.casefold()
    identity = {
        "root": root_text,
        "route_id": prepared["route_id"],
        "relative_path": prepared["relative_path"],
        "sha256": prepared["sha256"],
        "size_bytes": prepared["size_bytes"],
        "projection_schema": SCHEMA,
        "projection_schema_version": PROJECTION_SCHEMA_VERSION,
        "shard_layout_policy": dict(_STOCK_BUCKET_LAYOUT_POLICY),
    }
    return identity


def _cache_key(prepared):
    return _sha256(_canonical_bytes(_physical_identity(prepared)))


class _JSONStream:
    """Incremental JSON tokenizer; row arrays are decoded one row at a time."""

    def __init__(self, stream):
        self.stream = stream
        self.buffer = ""
        self.pos = 0
        self.decoder = json.JSONDecoder(object_pairs_hook=_pairs, parse_constant=_constant)

    def _fill(self):
        remaining = self.buffer[self.pos:]
        chunk = self.stream.read(65536)
        self.buffer = remaining + chunk
        self.pos = 0
        return bool(chunk)

    def peek(self):
        while True:
            while self.pos < len(self.buffer) and self.buffer[self.pos].isspace():
                self.pos += 1
            if self.pos < len(self.buffer):
                return self.buffer[self.pos]
            if not self._fill():
                return ""

    def take(self, expected):
        if self.peek() != expected:
            raise ProjectionIntegrityError("invalid_json_structure")
        self.pos += 1

    def value(self):
        self.peek()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer, self.pos)
                if end == len(self.buffer):
                    remaining = self.buffer[self.pos:]
                    chunk = self.stream.read(65536)
                    if chunk:
                        self.buffer = remaining + chunk
                        self.pos = 0
                        continue
                self.pos = end
                return value
            except json.JSONDecodeError as exc:
                remaining = self.buffer[self.pos:]
                chunk = self.stream.read(65536)
                if not chunk:
                    raise ProjectionIntegrityError("invalid_json") from exc
                self.buffer = remaining + chunk
                self.pos = 0
            except ProjectionIntegrityError:
                raise

    def skip_value(self):
        char = self.peek()
        if char == "[":
            self.take("[")
            if self.peek() == "]":
                self.pos += 1
                return
            while True:
                self.skip_value()
                char = self.peek()
                if char == "]":
                    self.pos += 1
                    return
                self.take(",")
        elif char == "{":
            self.take("{")
            if self.peek() == "}":
                self.pos += 1
                return
            keys = set()
            while True:
                key = self.value()
                if not isinstance(key, str) or key in keys:
                    raise ProjectionIntegrityError("duplicate_or_invalid_json_key")
                keys.add(key)
                self.take(":")
                self.skip_value()
                char = self.peek()
                if char == "}":
                    self.pos += 1
                    return
                self.take(",")
        else:
            self.value()

    def array(self):
        self.take("[")
        if self.peek() == "]":
            self.pos += 1
            return
        while True:
            yield self.value()
            char = self.peek()
            if char == "]":
                self.pos += 1
                return
            self.take(",")


def _iter_json_rows(stream):
    parser = _JSONStream(stream)
    first = parser.peek()
    if first == "[":
        yield from parser.array()
    elif first == "{":
        parser.take("{")
        keys, found = set(), False
        while parser.peek() != "}":
            key = parser.value()
            if not isinstance(key, str) or key in keys:
                raise ProjectionIntegrityError("duplicate_or_invalid_json_key")
            keys.add(key)
            parser.take(":")
            if key in ("data", "records", "rows", "canonical_rows") and parser.peek() == "[":
                if found:
                    raise UnsupportedProjectionError("multiple_row_arrays")
                found = True
                yield from parser.array()
            else:
                parser.skip_value()
            if parser.peek() == "}":
                break
            parser.take(",")
            if parser.peek() == "}":
                raise ProjectionIntegrityError("invalid_json_structure")
        parser.take("}")
        if not found:
            raise UnsupportedProjectionError("unsupported_payload_shape")
    else:
        raise UnsupportedProjectionError("unsupported_payload_shape")
    if parser.peek():
        raise ProjectionIntegrityError("trailing_json_content")


class _HashingRaw(io.RawIOBase):
    def __init__(self, path):
        super().__init__()
        self._stream = open(path, "rb", buffering=0)
        self.digest = hashlib.sha256()
        self.bytes_read = 0

    def readable(self):
        return True

    def readinto(self, buffer):
        data = self._stream.read(len(buffer))
        if not data:
            return 0
        count = len(data)
        buffer[:count] = data
        self.digest.update(data)
        self.bytes_read += count
        return count

    def close(self):
        if not self.closed:
            self._stream.close()
        super().close()


def _normalise_day(value):
    """Row / bound day -> canonical ``YYYY-MM-DD``: a calendar day in either spelling (``YYYYMMDD`` or
    ``YYYY-MM-DD``), a ``date``/``datetime``, or an ISO timestamp (its leading day); else None.

    JSON numbers (``20261002``), bools, ISO-week forms and other formats are not dates (date directive 2026-10-06).
    """
    return cd.iso_calendar_day(value)


def _normalise_stock(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (str, int)):
        text = str(value).strip()
        return text or None
    return None


def _resolve_field_map(row, requested):
    if not isinstance(row, dict):
        raise UnsupportedProjectionError("row_not_object")
    if requested is not None:
        if not isinstance(requested, dict) or set(requested) - {"stock_field", "date_field"}:
            raise ValueError("field_map must use stock_field and/or date_field")
        result = {"stock_field": requested.get("stock_field"),
                  "date_field": requested.get("date_field")}
        for value in result.values():
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError("field_map values must be nonempty strings or None")
        return result
    resolved = {}
    for name, candidates in (("stock_field", _STOCK_FIELDS), ("date_field", _DATE_FIELDS)):
        matches = [field for field in candidates if field in row]
        if len(matches) > 1:
            raise UnsupportedProjectionError("ambiguous_" + name)
        resolved[name] = matches[0] if matches else None
    return resolved


class _WriterPool:
    def __init__(self, stage_dir, max_open=64):
        self.stage_dir = stage_dir
        self.max_open = max_open
        self.handles = OrderedDict()
        self.states = {}
        self.next_chunk = {}
        self.completed = []
        self.buffer_bytes = _WRITE_BUFFER_BYTES
        self.max_pending_bytes = _MAX_PENDING_WRITE_BYTES
        self.pending = OrderedDict()
        self.pending_bytes = 0

    @staticmethod
    def _key_name(key):
        return _sha256(_canonical_bytes(key))[:24]

    def _new_state(self, key):
        number = self.next_chunk.get(key, 0)
        self.next_chunk[key] = number + 1
        path = self.stage_dir / (self._key_name(key) + "_" + str(number).zfill(7) + ".jsonl")
        state = {"key": key, "chunk_index": number, "temp_path": path,
                 "digest": hashlib.sha256(), "size_bytes": 0, "rows": 0,
                 "dates": set(), "first_ordinal": None, "last_ordinal": None,
                 "created": False, "pending": bytearray()}
        self.states[key] = state
        return state

    def _handle(self, state):
        path = state["temp_path"]
        key = str(path)
        if key in self.handles:
            handle = self.handles.pop(key)
            self.handles[key] = handle
            return handle
        while len(self.handles) >= self.max_open:
            _, handle = self.handles.popitem(last=False)
            handle.close()
        handle = open(path, "ab")
        state["created"] = True
        self.handles[key] = handle
        return handle

    def _flush_state(self, state):
        pending = state["pending"]
        if not pending:
            return
        self._handle(state).write(pending)
        self.pending_bytes -= len(pending)
        pending.clear()
        self.pending.pop(str(state["temp_path"]), None)

    def _queue_line(self, state, line):
        if len(line) >= min(self.buffer_bytes, self.max_pending_bytes):
            self._flush_state(state)
            self._handle(state).write(line)
            return
        if len(state["pending"]) + len(line) > self.buffer_bytes:
            self._flush_state(state)
        while self.pending_bytes + len(line) > self.max_pending_bytes:
            self._flush_state(next(iter(self.pending.values())))
        state["pending"].extend(line)
        self.pending_bytes += len(line)
        key = str(state["temp_path"])
        self.pending[key] = state
        self.pending.move_to_end(key)
        if len(state["pending"]) >= self.buffer_bytes:
            self._flush_state(state)

    def _finish_state(self, key):
        state = self.states.pop(key, None)
        if state is None or not state["rows"]:
            return
        self._flush_state(state)
        handle = self.handles.pop(str(state["temp_path"]), None)
        if handle is not None:
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
        stock_key, period = key
        self.completed.append({
            "stock_key": stock_key,
            "period": period,
            "chunk_index": state["chunk_index"],
            "temp_path": state["temp_path"],
            "sha256": state["digest"].hexdigest(),
            "size_bytes": state["size_bytes"],
            "rows": state["rows"],
            "dates": sorted(state["dates"]),
            "first_ordinal": state["first_ordinal"],
            "last_ordinal": state["last_ordinal"],
        })

    def append(self, stock_key, period, day, ordinal, row, chunk_rows, chunk_bytes):
        key = (stock_key, period)
        state = self.states.get(key)
        wrapper = {"__sequence": ordinal, "__date": day, "__stock": stock_key, "row": row}
        try:
            line = json.dumps(wrapper, ensure_ascii=False, separators=(",", ":"),
                              allow_nan=False).encode("utf-8") + b"\n"
        except (TypeError, ValueError, OverflowError) as exc:
            raise ProjectionIntegrityError("row_not_json_serializable") from exc
        if state and state["rows"] and (state["rows"] >= chunk_rows or
                                         state["size_bytes"] + len(line) > chunk_bytes):
            self._finish_state(key)
            state = None
        if state is None:
            state = self._new_state(key)
        self._queue_line(state, line)
        state["digest"].update(line)
        state["size_bytes"] += len(line)
        state["rows"] += 1
        state["dates"].add(day) if day else None
        if state["first_ordinal"] is None:
            state["first_ordinal"] = ordinal
        state["last_ordinal"] = ordinal

    def finish(self):
        while self.pending:
            self._flush_state(next(iter(self.pending.values())))
        for handle in self.handles.values():
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
        self.handles.clear()
        for key in list(self.states):
            self._finish_state(key)
        return list(self.completed)

    def abort(self):
        for handle in self.handles.values():
            with contextlib.suppress(OSError):
                handle.close()
        self.handles.clear()
        for state in self.pending.values():
            state["pending"].clear()
        self.pending.clear()
        self.pending_bytes = 0


@contextlib.contextmanager
def _writer_lock(path, timeout_seconds=120, *, on_lock_wait=None):
    timeout_seconds = _validate_lock_timeout(timeout_seconds)
    if on_lock_wait is not None and not callable(on_lock_wait):
        raise TypeError("on_lock_wait must be callable")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            started = time.monotonic()
            deadline = started + timeout_seconds
            last_notice = None
            while True:
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno != errno.EACCES:
                        raise
                    now = time.monotonic()
                    elapsed = max(0.0, now - started)
                    if now >= deadline:
                        raise _lock_timeout_error(path, elapsed, timeout_seconds) from exc
                    if on_lock_wait is not None and (
                            last_notice is None or elapsed - last_notice >= 30.0):
                        on_lock_wait(elapsed, timeout_seconds)
                        last_notice = elapsed
                    time.sleep(min(0.05, max(0.0, deadline - now)))
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            started = time.monotonic()
            deadline = started + timeout_seconds
            last_notice = None
            while True:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if exc.errno not in (
                            errno.EACCES, errno.EAGAIN,
                            getattr(errno, "EWOULDBLOCK", errno.EAGAIN)):
                        raise
                    now = time.monotonic()
                    elapsed = max(0.0, now - started)
                    if now >= deadline:
                        raise _lock_timeout_error(path, elapsed, timeout_seconds) from exc
                    if on_lock_wait is not None and (
                            last_notice is None or elapsed - last_notice >= 30.0):
                        on_lock_wait(elapsed, timeout_seconds)
                        last_notice = elapsed
                    time.sleep(min(0.05, max(0.0, deadline - now)))
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _validate_lock_timeout(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("lock_timeout_seconds must be a finite positive number")
    try:
        valid = math.isfinite(value) and value > 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("lock_timeout_seconds must be a finite positive number")
    return float(value)


def _lock_timeout_error(path, elapsed_seconds, timeout_seconds):
    message = (
        "projection_writer_lock_timeout: "
        f"lock_path={path} elapsed_seconds={elapsed_seconds:.3f} "
        f"limit_seconds={timeout_seconds:g}"
    )
    return ProjectionLockTimeoutError(
        message, lock_path=path, elapsed_seconds=elapsed_seconds,
        timeout_seconds=timeout_seconds,
    )


def _atomic_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _read_cache(query_root, cache_key, expected_identity):
    manifest_path = query_root / "indexes" / (cache_key + ".json")
    complete_path = query_root / "complete" / (cache_key + ".json")
    if not complete_path.exists():
        return None
    if not manifest_path.is_file():
        raise ProjectionIntegrityError("sealed_projection_manifest_missing")
    marker = _json_loads(complete_path.read_bytes())
    if (not isinstance(marker, dict) or marker.get("schema") != COMPLETE_SCHEMA or
            marker.get("cache_key") != cache_key):
        raise ProjectionIntegrityError("invalid_projection_completion_marker")
    raw = manifest_path.read_bytes()
    if _sha256(raw) != marker.get("manifest_sha256"):
        raise ProjectionIntegrityError("projection_manifest_hash_mismatch")
    manifest = _json_loads(raw)
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise ProjectionIntegrityError("invalid_projection_manifest")
    if manifest.get("cache_key") != cache_key or manifest.get("physical_identity") != expected_identity:
        raise ProjectionIntegrityError("projection_physical_identity_mismatch")
    if not isinstance(manifest.get("shards"), list) or not isinstance(manifest.get("field_map"), dict):
        raise ProjectionIntegrityError("projection_manifest_shape_invalid")
    return manifest



def prune_query_cache(shared_root, *, retained_refs=(), retained_objects=(), apply=False):
    """Drop superseded indexes and only shards no retained index references.

    This manages QUERY_DIR only. Explicit physical cache refs may be retained by
    a consumer; report hash text is provenance, not an unlimited lifetime pin.
    """
    root = Path(os.path.abspath(os.fspath(shared_root)))
    query_root = root / QUERY_DIR
    report = dict(apply=bool(apply), removed_indexes=[], candidate_indexes=[], retained_indexes=[],
        removed_shards=[], candidate_shards=[], unknown=[], stale_leases=[], shard_pruning_blocked=False)
    lock = cache_lifecycle.catalog_lock(query_root) if apply else contextlib.nullcontext()
    with lock:
        try:
            live_objects = set(retained_objects) | cache_lifecycle.publication_objects(root)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            report["unknown"].append(dict(path=str(root / "_metadata" / "stock_data_current.json"), reason=str(exc)))
            return report
        records, unknown, stale = cache_lifecycle.active_lease_records(query_root, apply=apply)
        report["unknown"].extend(unknown)
        report["stale_leases"] = stale
        keep_keys, keep_shards = {record["key"] for record in records}, set()
        for record in records:
            keep_keys.update(key for key in record.get("cache_keys", ()) if isinstance(key, str))
            for reference in record.get("physical_refs", ()):
                claim = reference.get("ref") if isinstance(reference, dict) else None
                if (isinstance(claim, dict) and isinstance(claim.get("sha256"), str)
                        and os.path.normcase(str(Path(reference.get("root", "")).resolve()))
                            == os.path.normcase(str(root))):
                    live_objects.add(claim["sha256"])
        for reference in retained_refs:
            raw = reference.get("path", reference.get("ref")) if isinstance(reference, dict) else reference
            if isinstance(raw, dict):
                raw = raw.get("path", raw.get("ref"))
            if not isinstance(raw, str):
                report["unknown"].append(dict(reason="retained_cache_reference_unknown", reference=repr(reference)))
                continue
            path = Path(raw)
            if _SHA256_RE.fullmatch(raw):
                keep_keys.add(raw)
            elif path.name.endswith(".json") and _SHA256_RE.fullmatch(path.stem):
                keep_keys.add(path.stem)
            elif path.name.endswith(".jsonl") and _SHA256_RE.fullmatch(path.stem):
                keep_shards.add(path.name)
            else:
                report["unknown"].append(dict(path=raw, reason="retained_cache_reference_unknown"))
        indexes = query_root / "indexes"
        referenced = set(keep_shards)
        if indexes.exists():
            for path in indexes.iterdir():
                key, manifest, valid, shards = path.stem, None, False, set()
                if not path.is_file() or path.suffix != ".json" or not _SHA256_RE.fullmatch(key):
                    report["unknown"].append(dict(path=str(path), reason="index_not_owned"))
                    report["shard_pruning_blocked"] = True
                    continue
                marker_path = query_root / "complete" / path.name
                try:
                    raw = path.read_bytes()
                    manifest = _json_loads(raw)
                    if not isinstance(manifest, dict) or not isinstance(manifest.get("shards"), list):
                        raise ValueError("cache_index_shape_unknown")
                    for shard in manifest["shards"]:
                        declared = shard.get("path") if isinstance(shard, dict) else None
                        if (not isinstance(declared, str) or declared != "objects/" + str(shard.get("sha256")) + ".jsonl"
                                or not _SHA256_RE.fullmatch(str(shard.get("sha256")))):
                            raise ValueError("cache_shard_reference_unknown")
                        shards.add(Path(declared).name)
                    physical = manifest.get("physical_identity")
                    marker = _json_loads(marker_path.read_bytes())
                    if (manifest.get("schema") != SCHEMA or manifest.get("cache_key") != key
                            or not isinstance(physical, dict) or _sha256(_canonical_bytes(physical)) != key
                            or physical.get("root") != (root.as_posix().casefold() if os.name == "nt" else root.as_posix())
                            or not _SHA256_RE.fullmatch(str(physical.get("sha256")))
                            or marker.get("schema") != COMPLETE_SCHEMA or marker.get("cache_key") != key
                            or marker.get("manifest_sha256") != _sha256(raw)):
                        raise ValueError("cache_index_identity_unknown")
                    valid = True
                except (OSError, ValueError, TypeError, AttributeError) as exc:
                    report["unknown"].append(dict(path=str(path), reason=str(exc)))
                    if not isinstance(manifest, dict) or not isinstance(manifest.get("shards"), list):
                        report["shard_pruning_blocked"] = True
                    elif len(shards) != len(manifest["shards"]):
                        report["shard_pruning_blocked"] = True
                keep = not valid or key in keep_keys or manifest["physical_identity"]["sha256"] in live_objects
                if keep:
                    referenced.update(shards)
                    report["retained_indexes"].append(str(path))
                    continue
                report["candidate_indexes"].append(str(path))
                if apply:
                    try:
                        # Remove the commit marker first; readers cannot adopt
                        # an index whose cache lifetime has ended under this lock.
                        cache_lifecycle.unlink_cache_file(query_root, marker_path)
                        cache_lifecycle.unlink_cache_file(query_root, path)
                        report["removed_indexes"].append(str(path))
                    except OSError as exc:
                        referenced.update(shards)
                        report["retained_indexes"].append(str(path))
                        report["unknown"].append(dict(path=str(path), reason=str(exc)))
        objects = query_root / "objects"
        if objects.exists() and not report["shard_pruning_blocked"]:
            for path in objects.iterdir():
                if not path.is_file() or path.suffix != ".jsonl" or not _SHA256_RE.fullmatch(path.stem):
                    report["unknown"].append(dict(path=str(path), reason="shard_not_owned"))
                    continue
                if path.name in referenced:
                    continue
                report["candidate_shards"].append(str(path))
                if apply:
                    try:
                        cache_lifecycle.unlink_cache_file(query_root, path)
                        report["removed_shards"].append(str(path))
                    except OSError as exc:
                        report["unknown"].append(dict(path=str(path), reason=str(exc)))
    return report


def _commit_shard_objects(query_root, completed):
    object_dir = query_root / "objects"
    object_dir.mkdir(parents=True, exist_ok=True)
    build_bytes_read = 0
    build_bytes_written = 0
    for shard in completed:
        digest = shard["sha256"]
        target = object_dir / (digest + ".jsonl")
        source = Path(shard["temp_path"])
        if target.exists():
            if _file_hash(target) != (digest, shard["size_bytes"]):
                raise ProjectionIntegrityError("content_addressed_shard_corrupt")
            build_bytes_read += shard["size_bytes"]
        else:
            fd, staged_name = tempfile.mkstemp(prefix=digest + ".", suffix=".tmp", dir=str(object_dir))
            try:
                with os.fdopen(fd, "wb") as destination, open(source, "rb") as original:
                    shutil.copyfileobj(original, destination, 1024 * 1024)
                    build_bytes_read += shard["size_bytes"]
                    destination.flush()
                    os.fsync(destination.fileno())
                if _file_hash(staged_name) != (digest, shard["size_bytes"]):
                    raise ProjectionIntegrityError("staged_shard_hash_mismatch")
                build_bytes_read += shard["size_bytes"]
                if target.exists():
                    if _file_hash(target) != (digest, shard["size_bytes"]):
                        raise ProjectionIntegrityError("content_addressed_shard_corrupt")
                    build_bytes_read += shard["size_bytes"]
                else:
                    os.replace(staged_name, target)
                    build_bytes_written += shard["size_bytes"]
            finally:
                if os.path.exists(staged_name):
                    os.unlink(staged_name)
        shard["path"] = (Path("objects") / (digest + ".jsonl")).as_posix()
        del shard["temp_path"]
    return build_bytes_read, build_bytes_written


def _field_map_for_first_row(row, requested):
    if not isinstance(row, dict) or not row:
        raise UnsupportedProjectionError("row_not_object")
    return _resolve_field_map(row, requested)


def _build_projection(prepared, query_root, cache_key, expected_identity,
                      requested_field_map, chunk_rows, chunk_bytes):
    source_path = prepared["artifact"]
    try:
        before = source_path.stat()
    except OSError as exc:
        raise ProjectionIntegrityError("pinned_artifact_missing") from exc
    if before.st_size != prepared["size_bytes"]:
        raise ProjectionIntegrityError("pinned_artifact_size_mismatch")

    staging_root = query_root / "staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    scanned = 0
    missing_dates = 0
    missing_stocks = 0
    minimum, maximum = None, None
    field_map = None
    writers = None
    with tempfile.TemporaryDirectory(prefix=cache_key[:16] + "-", dir=str(staging_root)) as temp:
        writers = _WriterPool(Path(temp))
        raw = _HashingRaw(source_path)
        buffered = io.BufferedReader(raw, buffer_size=65536)
        text = io.TextIOWrapper(buffered, encoding="utf-8", errors="strict", newline="")
        try:
            for ordinal, row in enumerate(_iter_json_rows(text)):
                if not isinstance(row, dict) or not row:
                    raise UnsupportedProjectionError("row_not_object")
                if field_map is None:
                    field_map = _field_map_for_first_row(row, requested_field_map)
                date_field = field_map["date_field"]
                stock_field = field_map["stock_field"]
                day = _normalise_day(row.get(date_field)) if date_field else None
                stock = _normalise_stock(row.get(stock_field)) if stock_field else None
                if date_field and day is None:
                    missing_dates += 1
                if stock_field and stock is None:
                    missing_stocks += 1
                if day is None:
                    period = "__undated__"
                elif stock_field:
                    period = "__stock__"
                elif prepared["route_id"] in _YEAR_BUCKET_ROUTES:
                    period = day[:4]
                else:
                    period = day[:7]
                try:
                    writers.append(stock, period, day, ordinal, row, chunk_rows, chunk_bytes)
                except ProjectionIntegrityError as exc:
                    # Keep the builder's former pre-serialization error contract.
                    if str(exc) == "row_not_json_serializable":
                        raise ProjectionIntegrityError("pinned_artifact_parse_failed") from (exc.__cause__ or exc)
                    raise
                scanned += 1
                if day:
                    minimum = min(minimum, day) if minimum else day
                    maximum = max(maximum, day) if maximum else day
            if field_map is None:
                field_map = requested_field_map or {"stock_field": None, "date_field": None}
            chunks = writers.finish()
            raw_digest, raw_size = raw.digest.hexdigest(), raw.bytes_read
        except UnsupportedProjectionError:
            writers.abort()
            raise
        except ProjectionIntegrityError:
            writers.abort()
            raise
        except (UnicodeError, json.JSONDecodeError, OSError, ValueError, TypeError) as exc:
            writers.abort()
            raise ProjectionIntegrityError("pinned_artifact_parse_failed") from exc
        finally:
            text.close()

        if (raw_digest, raw_size) != (prepared["sha256"], prepared["size_bytes"]):
            raise ProjectionIntegrityError("pinned_artifact_hash_mismatch")
        try:
            after = source_path.stat()
        except OSError as exc:
            raise ProjectionIntegrityError("pinned_artifact_changed_during_read") from exc
        if (before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns or
                getattr(before, "st_ino", None) != getattr(after, "st_ino", None)):
            raise ProjectionIntegrityError("pinned_artifact_changed_during_read")

        dates_by_stock = {}
        for shard in chunks:
            dates_by_stock.setdefault(shard["stock_key"], set()).update(shard["dates"])
        stock_statistics = []
        for stock_key, values in dates_by_stock.items():
            ordered_dates = sorted(values)
            stock_statistics.append({
                "stock_key": stock_key,
                "distinct_observation_dates": len(ordered_dates),
                "minimum_date": ordered_dates[0] if ordered_dates else None,
                "maximum_date": ordered_dates[-1] if ordered_dates else None,
            })
        stock_statistics.sort(
            key=lambda item: (item["stock_key"] is not None, str(item["stock_key"]))
        )

        with cache_lifecycle.catalog_lock(query_root):
            projection_build_bytes_read, projection_build_bytes_written = _commit_shard_objects(
                query_root, chunks)
            manifest = {
                "schema": SCHEMA,
                "schema_version": PROJECTION_SCHEMA_VERSION,
                "cache_key": cache_key,
                "physical_identity": expected_identity,
                "source_artifact": {
                    "route_id": prepared["route_id"],
                    "relative_path": prepared["relative_path"],
                    "sha256": prepared["sha256"],
                    "size_bytes": prepared["size_bytes"],
                },
                "build_context": {
                    "parent_identity_sha256": prepared["parent_identity_sha256"],
                    "source_identity_sha256": prepared["source_identity_sha256"],
                    "built_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                },
                "field_map": field_map,
                "rows": scanned,
                "minimum_date": minimum,
                "maximum_date": maximum,
                "missing_date_rows": missing_dates,
                "missing_stock_rows": missing_stocks,
                "stock_statistics": stock_statistics,
                "shards": chunks,
            }
            raw_manifest = _canonical_bytes(manifest)
            manifest_digest = _sha256(raw_manifest)
            manifest_path = query_root / "indexes" / (cache_key + ".json")
            _atomic_write(manifest_path, raw_manifest)
            marker = _canonical_bytes({"schema": COMPLETE_SCHEMA, "cache_key": cache_key,
                                       "manifest_sha256": manifest_digest})
            complete_path = query_root / "complete" / (cache_key + ".json")
            _atomic_write(complete_path, marker)
    return (scanned, raw_size, projection_build_bytes_read,
            projection_build_bytes_written, int((time.monotonic() - started) * 1000))


def _parse_query_date(value, name):
    if value is None:
        return None
    result = _normalise_day(value)
    if result is None:
        raise ValueError(name + " must be YYYYMMDD（相容 YYYY-MM-DD）")
    return result


def _parse_query_bound(value, name):
    if value is None or not isinstance(value, dict):
        return _parse_query_date(value, name)
    if not value:
        raise ValueError(name + " per-stock map must not be empty")
    result = {}
    for raw_stock, raw_day in value.items():
        stock = _normalise_stock(raw_stock)
        if stock is None:
            raise ValueError(name + " map keys must be stock IDs")
        if stock in result:
            raise ValueError(name + " has duplicate normalized stock IDs")
        parsed_day = _parse_query_date(raw_day, name + "[" + stock + "]")
        if parsed_day is None:
            raise ValueError(name + " map values must be dates; use scalar None for no bound")
        result[stock] = parsed_day
    return result


def _bound_for_stock(bound, stock):
    return bound.get(stock) if isinstance(bound, dict) else bound


def _stock_ids(values):
    if values is None:
        return None
    if isinstance(values, (str, int)) and not isinstance(values, bool):
        values = [values]
    result = []
    for value in values:
        stock = _normalise_stock(value)
        if stock is None:
            raise ValueError("stock_ids must contain nonempty string or integer IDs")
        if stock not in result:
            result.append(stock)
    return result


def _select_shards(manifest, requested_stocks, start_date, end_date, before_count):
    field_map = manifest["field_map"]
    filtering_stocks = requested_stocks is not None
    filtering_dates = start_date is not None or end_date is not None or before_count > 0
    if filtering_stocks and (not field_map.get("stock_field") or manifest.get("missing_stock_rows")):
        raise UnsupportedProjectionError("stock_filter_not_lossless")
    if filtering_dates and (not field_map.get("date_field") or manifest.get("missing_date_rows")):
        raise UnsupportedProjectionError("date_filter_not_lossless")
    if before_count and start_date is None:
        raise ValueError("before_count requires start_date")
    stock_keys = list(dict.fromkeys(
        item.get("stock_key") for item in manifest.get("stock_statistics", [])))
    target_stocks = requested_stocks if requested_stocks is not None else stock_keys
    for bound in (start_date, end_date):
        if isinstance(bound, dict):
            if None in target_stocks:
                raise UnsupportedProjectionError("per_stock_bound_has_unkeyed_rows")
            if any(stock not in bound for stock in target_stocks):
                raise ValueError("per_stock_date_bound_missing_stock")
    for stock in target_stocks:
        own_start = _bound_for_stock(start_date, stock)
        own_end = _bound_for_stock(end_date, stock)
        if own_start and own_end and own_start > own_end:
            raise ValueError("start_date must not be after end_date for " + str(stock))

    carry_by_stock = {}
    carry_missing_stock = set()
    if before_count:
        dates_by_stock = {}
        for shard in manifest["shards"]:
            stock = shard.get("stock_key")
            if requested_stocks is not None and stock not in requested_stocks:
                continue
            own_start = _bound_for_stock(start_date, stock)
            if own_start is None:
                raise UnsupportedProjectionError("before_count_start_missing_for_stock")
            dates_by_stock.setdefault(stock, set()).update(
                day for day in shard.get("dates", []) if day < own_start)
        carry_keys = requested_stocks if requested_stocks is not None else list(dates_by_stock)
        for stock in carry_keys:
            prior = set(sorted(dates_by_stock.get(stock, set()))[-before_count:])
            if stock is None:
                carry_missing_stock = prior
            else:
                carry_by_stock[stock] = prior

    selected = []
    for shard in manifest["shards"]:
        stock = shard.get("stock_key")
        if requested_stocks is not None and stock not in requested_stocks:
            continue
        dates = shard.get("dates", [])
        if filtering_dates:
            if requested_stocks is not None:
                carries = carry_by_stock.get(stock, set())
            else:
                carries = carry_missing_stock if stock is None else carry_by_stock.get(stock, set())
            own_start = _bound_for_stock(start_date, stock)
            own_end = _bound_for_stock(end_date, stock)
            in_range = own_start is not None or own_end is not None
            if not any((in_range and (own_start is None or day >= own_start) and
                        (own_end is None or day <= own_end)) or day in carries for day in dates):
                continue
        selected.append(shard)
    selected.sort(key=lambda item: (item.get("first_ordinal", -1),
                                    item.get("chunk_index", 0), item.get("path", "")))
    predicate = {
        "start_date": start_date,
        "end_date": end_date,
        "before_count": before_count,
        "carry_dates": [],
        "carry_dates_by_stock": {key: sorted(value) for key, value in carry_by_stock.items()},
        "carry_dates_missing_stock": sorted(carry_missing_stock),
        "stock_ids": requested_stocks,
    }
    return selected, predicate


def _row_matches(wrapper, predicate):
    stock = wrapper.get("__stock")
    day = wrapper.get("__date")
    requested_stocks = predicate["stock_ids"]
    if requested_stocks is not None and stock not in requested_stocks:
        return False
    start_date = _bound_for_stock(predicate["start_date"], stock)
    end_date = _bound_for_stock(predicate["end_date"], stock)
    if start_date is None and end_date is None and not predicate["before_count"]:
        return True
    if day is None:
        return False
    inside = ((start_date is None or day >= start_date) and
              (end_date is None or day <= end_date))
    if inside:
        return True
    by_stock = predicate["carry_dates_by_stock"]
    carry = predicate["carry_dates_missing_stock"] if stock is None else by_stock.get(stock, [])
    return day in carry


def _query_stock_statistics(manifest, predicate):
    dates_by_stock = {}
    for shard in manifest.get("shards", []):
        stock = shard.get("stock_key")
        for day in shard.get("dates", []):
            if _row_matches({"__stock": stock, "__date": day}, predicate):
                dates_by_stock.setdefault(stock, set()).add(day)
    result = []
    for stock, dates in dates_by_stock.items():
        ordered = sorted(dates)
        result.append({
            "stock_key": stock,
            "distinct_observation_dates": len(ordered),
            "minimum_date": ordered[0] if ordered else None,
            "maximum_date": ordered[-1] if ordered else None,
        })
    result.sort(key=lambda item: (item["stock_key"] is not None, str(item["stock_key"])))
    return result


def _query_object_path(query_root, shard):
    relative = shard.get("path")
    normal = _normalise_relative(relative)
    base = query_root.resolve()
    result = (base / Path(*normal.split("/"))).resolve()
    if not result.is_relative_to(base):
        raise ProjectionIntegrityError("projection_shard_path_escapes_root")
    return result


def _verify_selected_shards(query_root, selected, metrics):
    verified = []
    for shard in selected:
        path = _query_object_path(query_root, shard)
        try:
            digest, size = _file_hash(path)
        except OSError as exc:
            raise ProjectionIntegrityError("projection_shard_missing") from exc
        metrics["projection_bytes_read"] += size
        if digest != shard.get("sha256") or size != shard.get("size_bytes"):
            raise ProjectionIntegrityError("projection_shard_hash_mismatch")
        metrics["shards_read"] += 1
        verified.append((shard, path))
    return verified


def _iter_checked_chunk(stream, shard, predicate, metrics):
    digest, size, rows = hashlib.sha256(), 0, 0
    first_sequence = None
    last_sequence = None
    expected_first = shard.get("first_ordinal")
    expected_last = shard.get("last_ordinal")
    if (isinstance(expected_first, bool) or not isinstance(expected_first, int) or
            isinstance(expected_last, bool) or not isinstance(expected_last, int) or
            expected_first < 0 or expected_last < expected_first):
        raise ProjectionIntegrityError("projection_shard_ordinal_bounds_invalid")
    try:
        for line in stream:
            digest.update(line)
            size += len(line)
            wrapper = _json_loads(line)
            if not isinstance(wrapper, dict) or not isinstance(wrapper.get("row"), dict):
                raise ProjectionIntegrityError("projection_row_shape_invalid")
            sequence = wrapper.get("__sequence")
            if (isinstance(sequence, bool) or not isinstance(sequence, int) or
                    sequence < 0):
                raise ProjectionIntegrityError("projection_sequence_invalid")
            if sequence < expected_first or sequence > expected_last:
                raise ProjectionIntegrityError("projection_sequence_outside_shard_bounds")
            if last_sequence is not None and sequence <= last_sequence:
                raise ProjectionIntegrityError("projection_sequence_not_increasing")
            if first_sequence is None:
                first_sequence = sequence
            last_sequence = sequence
            rows += 1
            metrics["projection_bytes_read"] += len(line)
            metrics["rows_scanned"] += 1
            if _row_matches(wrapper, predicate):
                yield sequence, wrapper["row"]
    except OSError as exc:
        raise ProjectionIntegrityError("projection_shard_read_failed") from exc
    if (digest.hexdigest() != shard.get("sha256") or
            size != shard.get("size_bytes") or rows != shard.get("rows") or
            first_sequence != expected_first or last_sequence != expected_last):
        raise ProjectionIntegrityError("projection_shard_changed_during_read")


def _ordinal_components(verified):
    """Partition verified shard envelopes into connected ordinal intervals."""
    ordered = []
    for shard, path in verified:
        first = shard.get("first_ordinal")
        last = shard.get("last_ordinal")
        if (isinstance(first, bool) or not isinstance(first, int) or
                isinstance(last, bool) or not isinstance(last, int) or
                first < 0 or last < first):
            raise ProjectionIntegrityError("projection_shard_ordinal_bounds_invalid")
        ordered.append((first, last, shard, path))
    ordered.sort(key=lambda item: (item[0], item[1], str(item[3])))

    components = []
    component = []
    maximum_last = None
    for first, last, shard, path in ordered:
        if component and first > maximum_last:
            components.append(component)
            component = []
            maximum_last = None
        component.append((shard, path))
        maximum_last = last if maximum_last is None else max(maximum_last, last)
    if component:
        components.append(component)
    return components


def _project_columns(row, columns):
    if columns is None:
        return row
    missing = [column for column in columns if column not in row]
    if missing:
        raise ProjectionIntegrityError("requested_column_missing")
    return {column: row[column] for column in columns}


class ProjectionQueryResult:
    """Pinned query result; rows remain lazy and large results need no list."""

    def __init__(self, root, query_root, route_id, publication_entry, manifest,
                 selected, predicate, status, metrics, current_binding, columns=None,
                 consumer_temp_root=None, cache_lease=None):
        self._cache_lease, self._closed = cache_lease, False
        self._active_iterators = 0
        self._lease_lock = threading.RLock()
        self.root = Path(root)
        self.query_root = Path(query_root)
        self.consumer_temp_root = (None if consumer_temp_root is None
                                   else Path(consumer_temp_root).resolve())
        if (self.consumer_temp_root is not None
                and (self.consumer_temp_root == self.root.resolve()
                     or self.root.resolve() in self.consumer_temp_root.parents)):
            raise ValueError("consumer_temp_root_must_be_outside_projection_root")
        self.route_id = route_id
        self.publication_entry = publication_entry
        self.manifest = manifest
        self.cache_key = manifest["cache_key"]
        self.index_manifest_path = self.query_root / "indexes" / (self.cache_key + ".json")
        self.completion_path = self.query_root / "complete" / (self.cache_key + ".json")
        self.selected = selected
        self.predicate = predicate
        self.status = status
        self.metrics = metrics
        self.current_binding = current_binding
        self.field_map = dict(manifest["field_map"])
        self.source_stock_statistics = list(manifest.get("stock_statistics", []))
        self.query_stock_statistics = _query_stock_statistics(manifest, predicate)
        self.columns = tuple(columns) if columns is not None else None
        self.lineage = {
            "built_from": dict(manifest.get("build_context") or {}),
            "requested_parent_identity_sha256": current_binding["parent_identity_sha256"],
            "requested_source_identity_sha256": current_binding["source_identity_sha256"],
            "source_artifact": dict(manifest["source_artifact"]),
        }

    def close(self):
        """Stop new readers; an active iterator retains its pin until it ends."""
        with self._lease_lock:
            self._closed = True
            self._release_closed_lease()

    def _release_closed_lease(self):
        if self._closed and not self._active_iterators and self._cache_lease is not None:
            self._cache_lease.close()
            self._cache_lease = None

    @contextlib.contextmanager
    def _iterator_pin(self):
        with self._lease_lock:
            if self._closed:
                raise ProjectionUnavailableError("projection_query_closed")
            self._active_iterators += 1
        try:
            yield
        finally:
            with self._lease_lock:
                self._active_iterators -= 1
                self._release_closed_lease()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        with contextlib.suppress(OSError, AttributeError):
            self.close()

    def iter_verified_shards(self):
        """Yield shard descriptors only after every selected shard has verified."""
        with self._iterator_pin():
            verified = _verify_selected_shards(self.query_root, self.selected, self.metrics)
            for shard, path in verified:
                yield {
                    "path": str(path),
                    "sha256": shard["sha256"],
                    "size_bytes": shard["size_bytes"],
                    "rows": shard["rows"],
                    "stock_key": shard["stock_key"],
                    "period": shard["period"],
                    "dates": list(shard["dates"]),
                    "minimum_date": min(shard["dates"]) if shard["dates"] else None,
                    "maximum_date": max(shard["dates"]) if shard["dates"] else None,
                    "first_ordinal": shard["first_ordinal"],
                    "last_ordinal": shard["last_ordinal"],
                    "index_manifest_path": str(self.index_manifest_path),
                    "route_id": self.route_id,
                    "field_map": dict(self.field_map),
                    "envelope_fields": {"sequence": "__sequence", "date": "__date",
                                        "stock": "__stock", "row": "row"},
                    "predicate": self.predicate,
                    "publication_entry": self.publication_entry,
                    "lineage": self.lineage,
                }

    def iter_rows(self, *, order="source"):
        """Stream matching rows in source order, or explicitly in stock-shard order."""
        if order not in ("source", "stock_shard"):
            raise ValueError("projection_iteration_order_invalid")
        with self._iterator_pin():
            verified = _verify_selected_shards(self.query_root, self.selected, self.metrics)
            if order == "stock_shard":
                yield from self._iter_stock_shard_rows(verified)
                return
            for component in _ordinal_components(verified):
                if len(component) == 1:
                    self.metrics["peak_open_shard_streams"] = max(
                        self.metrics.get("peak_open_shard_streams", 0), 1)
                    shard, path = component[0]
                    with open(path, "rb") as stream:
                        for _sequence, row in _iter_checked_chunk(
                                stream, shard, self.predicate, self.metrics):
                            yield _project_columns(row, self.columns)
                elif len(component) <= _MAX_OPEN_ROWS_SHARDS:
                    self.metrics["peak_open_shard_streams"] = max(
                        self.metrics.get("peak_open_shard_streams", 0), len(component))
                    yield from self._iter_heap_component(component)
                else:
                    self.metrics["peak_open_shard_streams"] = max(
                        self.metrics.get("peak_open_shard_streams", 0), 1)
                    yield from self._iter_spooled_component(component)

    def _iter_stock_shard_rows(self, verified):
        source_rows = self.manifest.get("rows")
        if type(source_rows) is not int or source_rows < 0:
            raise ProjectionIntegrityError("projection_manifest_row_count_invalid")
        for shard, _path in verified:
            first, last = shard.get("first_ordinal"), shard.get("last_ordinal")
            if (type(first) is not int or type(last) is not int
                    or first < 0 or last < first):
                raise ProjectionIntegrityError("projection_shard_ordinal_bounds_invalid")
            if last >= source_rows:
                raise ProjectionIntegrityError("projection_sequence_outside_source_bounds")
        seen = bytearray((source_rows + 7) // 8)
        last_by_stock = {}
        self.metrics.update(iteration_order="stock_shard", stock_sequence_preserved=True,
                            source_sequence_preserved=False)
        ordered = sorted(verified, key=lambda item: (
            item[0].get("stock_key") is not None, str(item[0].get("stock_key")),
            item[0]["first_ordinal"], str(item[1])))
        for shard, path in ordered:
            stock = shard.get("stock_key")
            self.metrics["peak_open_shard_streams"] = max(
                self.metrics.get("peak_open_shard_streams", 0), 1)
            with open(path, "rb") as stream:
                for sequence, row in _iter_checked_chunk(
                        stream, shard, self.predicate, self.metrics):
                    byte, bit = divmod(sequence, 8)
                    mask = 1 << bit
                    if seen[byte] & mask:
                        raise ProjectionIntegrityError("projection_duplicate_sequence")
                    seen[byte] |= mask
                    previous = last_by_stock.get(stock)
                    if previous is not None and sequence <= previous:
                        raise ProjectionIntegrityError("projection_sequence_not_increasing")
                    last_by_stock[stock] = sequence
                    yield _project_columns(row, self.columns)

    def _iter_heap_component(self, component):
        iterators = []
        heap = []
        previous_sequence = None
        with contextlib.ExitStack() as stack:
            for index, (shard, path) in enumerate(component):
                stream = stack.enter_context(open(path, "rb"))
                iterator = _iter_checked_chunk(stream, shard, self.predicate, self.metrics)
                item = _next_row(iterator)
                iterators.append(iterator)
                if item is not None:
                    heapq.heappush(heap, (item[0], index, item[1]))
            while heap:
                sequence, index, row = heapq.heappop(heap)
                if previous_sequence is not None and sequence <= previous_sequence:
                    raise ProjectionIntegrityError("projection_duplicate_sequence")
                previous_sequence = sequence
                yield _project_columns(row, self.columns)
                item = _next_row(iterators[index])
                if item is not None:
                    heapq.heappush(heap, (item[0], index, item[1]))

    def _iter_spooled_component(self, component):
        staging_root = (self.consumer_temp_root if self.consumer_temp_root is not None
                        else self.query_root / "staging")
        staging_root.mkdir(parents=True, exist_ok=True)
        self.metrics["spool_components"] = self.metrics.get("spool_components", 0) + 1
        with tempfile.TemporaryDirectory(prefix="ordered-rows-", dir=str(staging_root)) as temp:
            database_path = Path(temp) / "rows.sqlite3"
            try:
                connection = sqlite3.connect(str(database_path))
            except sqlite3.Error as exc:
                raise ProjectionError("projection_spool_failed") from exc
            try:
                try:
                    connection.execute("PRAGMA journal_mode=OFF")
                    connection.execute("PRAGMA synchronous=OFF")
                    connection.execute("PRAGMA cache_size=-4096")
                    connection.execute(
                        "CREATE TABLE rows (sequence INTEGER PRIMARY KEY, row_json TEXT NOT NULL)"
                    )
                    batch = []
                    batch_bytes = 0
                    batch_rows = 0

                    def flush_batch():
                        nonlocal batch_bytes, batch_rows
                        if not batch:
                            return
                        connection.executemany(
                            "INSERT INTO rows(sequence, row_json) VALUES (?, ?)", batch)
                        connection.commit()
                        self.metrics["spool_rows_written"] = (
                            self.metrics.get("spool_rows_written", 0) + batch_rows)
                        self.metrics["spool_payload_bytes_written"] = (
                            self.metrics.get("spool_payload_bytes_written", 0) + batch_bytes)
                        batch.clear()
                        batch_bytes = 0
                        batch_rows = 0

                    for shard, path in component:
                        with open(path, "rb") as stream:
                            for sequence, row in _iter_checked_chunk(
                                    stream, shard, self.predicate, self.metrics):
                                try:
                                    row_json = json.dumps(
                                        row, ensure_ascii=False, separators=(",", ":"),
                                        allow_nan=False)
                                except (TypeError, ValueError, OverflowError) as exc:
                                    raise ProjectionIntegrityError(
                                        "projection_spool_row_invalid") from exc
                                payload_bytes = len(row_json.encode("utf-8"))
                                batch.append((sequence, row_json))
                                batch_bytes += payload_bytes
                                batch_rows += 1
                                if (batch_rows >= _SPOOL_BATCH_ROWS or
                                        batch_bytes >= _SPOOL_BATCH_BYTES):
                                    flush_batch()
                    flush_batch()
                    cursor = connection.execute(
                        "SELECT row_json FROM rows ORDER BY sequence")
                    while True:
                        records = cursor.fetchmany(_SPOOL_FETCH_ROWS)
                        if not records:
                            break
                        payload_bytes = sum(len(record[0].encode("utf-8"))
                                            for record in records)
                        self.metrics["spool_rows_read"] = (
                            self.metrics.get("spool_rows_read", 0) + len(records))
                        self.metrics["spool_payload_bytes_read"] = (
                            self.metrics.get("spool_payload_bytes_read", 0) + payload_bytes)
                        for (row_json,) in records:
                            row = _json_loads(row_json)
                            if not isinstance(row, dict):
                                raise ProjectionIntegrityError(
                                    "projection_spool_row_shape_invalid")
                            yield _project_columns(row, self.columns)
                except sqlite3.IntegrityError as exc:
                    raise ProjectionIntegrityError("projection_duplicate_sequence") from exc
                except sqlite3.Error as exc:
                    raise ProjectionError("projection_spool_failed") from exc
            finally:
                connection.close()

    def iter_batches(self, batch_size=1024):
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")
        batch = []
        for row in self.iter_rows():
            batch.append(row)
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch

    def record_consumer_scan(self, *, rows_scanned, projection_bytes_read=None):
        """Record downstream DuckDB or adapter work performed over yielded shards."""
        if isinstance(rows_scanned, bool) or not isinstance(rows_scanned, int) or rows_scanned < 0:
            raise ValueError("rows_scanned must be a nonnegative integer")
        self.metrics["consumer_scan_reported"] = True
        self.metrics["consumer_rows_scanned"] += rows_scanned
        self.metrics["rows_scanned"] += rows_scanned
        if projection_bytes_read is not None:
            if (isinstance(projection_bytes_read, bool) or
                    not isinstance(projection_bytes_read, int) or projection_bytes_read < 0):
                raise ValueError("projection_bytes_read must be a nonnegative integer")
            self.metrics["consumer_projection_bytes_read"] += projection_bytes_read
            self.metrics["projection_bytes_read"] += projection_bytes_read

    def collect(self, max_rows=None):
        """Every matching row as one list (no default row cap).

        A list holds every row by definition, so its memory is the size of the query result itself: the query's
        own stock / date / before_count scope is what bounds it.  Callers that must stay memory-bounded whatever
        the result size use ``iter_rows()`` / ``iter_batches()`` (the production readers do; nothing in the
        pipeline calls ``collect``).  ``max_rows`` is only an explicit, caller-chosen guard."""
        if max_rows is not None and (
                isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 0):
            raise ValueError("max_rows must be None or a nonnegative integer")
        rows = []
        for row in self.iter_rows():
            if max_rows is not None and len(rows) >= max_rows:
                raise ProjectionResultLimitError("collect_row_limit_exceeded")
            rows.append(row)
        return rows


def _next_row(iterator):
    try:
        return next(iterator)
    except StopIteration:
        return None


def query_published_rows(root, artifact_descriptor, route_id, *, stock_ids=None,
                         start_date=None, end_date=None, before_count=0,
                         source_identity=None, parent_identity=None, allow_build=True,
                         chunk_rows=_DEFAULT_CHUNK_ROWS, chunk_bytes=_DEFAULT_CHUNK_BYTES,
                         field_map=None, columns=None, lock_timeout_seconds=120,
                         on_lock_wait=None, consumer_temp_root=None):
    """Return a lazy query result over verified stock/date shards.

    The artifact descriptor is one route entry from an already pinned canonical
    publication.  Warm reads never open or hash the parent artifact.
    """
    lock_timeout_seconds = _validate_lock_timeout(lock_timeout_seconds)
    if on_lock_wait is not None and not callable(on_lock_wait):
        raise TypeError("on_lock_wait must be callable")
    if isinstance(chunk_rows, bool) or not isinstance(chunk_rows, int) or chunk_rows <= 0:
        raise ValueError("chunk_rows must be a positive integer")
    if isinstance(chunk_bytes, bool) or not isinstance(chunk_bytes, int) or chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be a positive integer")
    if isinstance(before_count, bool) or not isinstance(before_count, int) or before_count < 0:
        raise ValueError("before_count must be a nonnegative integer")
    start = _parse_query_bound(start_date, "start_date")
    end = _parse_query_bound(end_date, "end_date")
    if isinstance(start, str) and isinstance(end, str) and start > end:
        raise ValueError("start_date must not be after end_date")
    requested_stocks = _stock_ids(stock_ids)
    query_root = cache_lifecycle.source_registry(Path(root).resolve())
    with cache_lifecycle.catalog_lock(query_root):
        prepared = _prepare_descriptor(root, artifact_descriptor, route_id,
                                       parent_identity, source_identity)
        identity = _physical_identity(prepared)
        cache_key = _cache_key(prepared)
        physical_ref = dict(root=str(prepared["root"]), ref={"ref": prepared["relative_path"],
            "sha256": prepared["sha256"], "size": prepared["size_bytes"]}, expand=False, route_ids=[route_id])
        cache_lease = cache_lifecycle.CacheLease(query_root, cache_key,
            physical_refs=[physical_ref], cache_keys=[cache_key], already_locked=True)
    try:
        metrics = {
            "source_bytes_read": 0,
            "projection_bytes_read": 0,
            "projection_build_bytes_read": 0,
            "projection_build_bytes_written": 0,
            "rows_scanned": 0,
            "consumer_projection_bytes_read": 0,
            "consumer_rows_scanned": 0,
            "consumer_scan_reported": False,
            "shards_read": 0,
            "peak_open_shard_streams": 0,
            "spool_components": 0,
            "spool_rows_written": 0,
            "spool_rows_read": 0,
            "spool_payload_bytes_written": 0,
            "spool_payload_bytes_read": 0,
            "cache_hit": False,
            "build_elapsed_ms": 0,
        }
        manifest = _read_cache(query_root, cache_key, identity)
        status = "cache_hit"
        if manifest is None:
            if not allow_build:
                raise ProjectionUnavailableError("projection_not_built")
            lock = query_root / "locks" / (cache_key + ".lock")
            with _writer_lock(lock, timeout_seconds=lock_timeout_seconds,
                              on_lock_wait=on_lock_wait):
                manifest = _read_cache(query_root, cache_key, identity)
                if manifest is None:
                    (scanned, source_size, build_read, build_written,
                     elapsed) = _build_projection(
                        prepared, query_root, cache_key, identity, field_map,
                        chunk_rows, chunk_bytes)
                    manifest = _read_cache(query_root, cache_key, identity)
                    if manifest is None:
                        raise ProjectionIntegrityError("projection_not_sealed_after_build")
                    metrics["source_bytes_read"] = source_size
                    metrics["projection_build_bytes_read"] = build_read
                    metrics["projection_build_bytes_written"] = build_written
                    metrics["rows_scanned"] = scanned
                    metrics["build_elapsed_ms"] = elapsed
                    status = "built"
                else:
                    status = "cache_hit"
        else:
            metrics["cache_hit"] = True
        metrics["cache_hit"] = status == "cache_hit"
        if field_map is not None:
            requested = _resolve_field_map({"_": None}, field_map)
            if requested != manifest.get("field_map"):
                raise UnsupportedProjectionError("projection_field_map_mismatch")
        selected, predicate = _select_shards(manifest, requested_stocks, start, end, before_count)
        binding = {
            "parent_identity_sha256": prepared["parent_identity_sha256"],
            "source_identity_sha256": prepared["source_identity_sha256"],
        }
        result = ProjectionQueryResult(prepared["root"], query_root, route_id,
                                       prepared["publication_entry"], manifest, selected,
                                       predicate, status, metrics, binding, columns,
                                       consumer_temp_root=consumer_temp_root, cache_lease=cache_lease)
        if status == "built":
            try:
                cleanup = prune_query_cache(root, retained_objects=(prepared["sha256"],), apply=True)
                metrics["cache_cleanup"] = {name: len(cleanup[name]) for name in
                    ("removed_indexes", "removed_shards", "retained_indexes", "unknown")}
                metrics["cache_cleanup"]["shard_pruning_blocked"] = cleanup["shard_pruning_blocked"]
            except (OSError, ValueError, TypeError, KeyError) as exc:
                metrics["cache_cleanup"] = dict(removed_indexes=0, removed_shards=0,
                    unknown=1, reason=str(exc), shard_pruning_blocked=True)
        return result
    except BaseException:
        cache_lease.close()
        raise


_PUBLICATION_SCHEMAS = ("stock-data-publication-v1", "stock-data-publication-v2")


def pin_current_publication(shared_root, route_ids):
    """Read a small current manifest and return its pinned selected route entries."""
    root = Path(shared_root).resolve()
    path = root / "_metadata" / "stock_data_current.json"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ProjectionIntegrityError("published_manifest_missing") from exc
    document = _json_loads(raw)
    # v1 and v2 heads (binding v2 contract); only entry-level fields and metadata identity scalars are read here
    if not isinstance(document, dict) or document.get("schema") not in _PUBLICATION_SCHEMAS:
        raise ProjectionIntegrityError("published_manifest_schema_invalid")
    if not isinstance(document.get("datasets"), list):
        raise ProjectionIntegrityError("published_manifest_datasets_invalid")
    manifest_sha = _sha256(raw)
    requested = list(dict.fromkeys(route_ids))
    pinned = []
    for route_id in requested:
        entries = [entry for entry in document["datasets"]
                   if isinstance(entry, dict) and entry.get("route_id") == route_id]
        if len(entries) != 1:
            raise ProjectionIntegrityError("published_route_missing_or_ambiguous:" + str(route_id))
        entry = dict(entries[0])
        entry["parent_identity"] = manifest_sha
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        entry["source_identity"] = {
            key: metadata[key] for key in
            ("source_producer_id", "source_run_id", "candidate_object_sha256")
            if metadata.get(key) is not None
        }
        pinned.append(entry)
    return {"root": root, "manifest_sha256": manifest_sha,
            "manifest": document, "routes": pinned}


def build_published_query_projections(shared_root, route_ids, *, field_maps=None,
                                      chunk_rows=_DEFAULT_CHUNK_ROWS,
                                      chunk_bytes=_DEFAULT_CHUNK_BYTES):
    """Build or reuse projections for selected routes in the current snapshot."""
    snapshot = pin_current_publication(shared_root, route_ids)
    field_maps = field_maps or {}
    reports = []
    for entry in snapshot["routes"]:
        result = query_published_rows(
            snapshot["root"], entry, entry["route_id"],
            parent_identity=snapshot["manifest_sha256"],
            source_identity=entry.get("source_identity"),
            allow_build=True, field_map=field_maps.get(entry["route_id"]),
            chunk_rows=chunk_rows, chunk_bytes=chunk_bytes)
        reports.append({"route_id": entry["route_id"], "status": result.status,
                        "metrics": dict(result.metrics), "field_map": result.field_map,
                        "projection_rows": result.manifest.get("rows"),
                        "shard_count": len(result.selected),
                        "parent_manifest_sha256": snapshot["manifest_sha256"]})
    return reports


__all__ = [
    "ProjectionError", "UnsupportedProjectionError", "ProjectionUnavailableError",
    "ProjectionLockTimeoutError", "ProjectionStreamingRequiredError",
    "ProjectionResultLimitError",
    "ProjectionIntegrityError", "ProjectionQueryResult", "query_published_rows",
    "pin_current_publication", "build_published_query_projections", "prune_query_cache",
]
