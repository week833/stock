"""Process-local cache for immutable central-evidence reads.

The session is intentionally small and non-persistent.  It caches only values
that have been read from regular files during one caller-owned process/run.
Every lookup starts with the resolved path and current file identity; a size,
mtime, ctime or inode change makes the previous entry unreachable.  The
resulting evidence key also records the content SHA and the bound lineage
scope so callers cannot accidentally reuse a value for a different formal
run/as-of request.

This module does not validate a producer contract.  Consumers remain
responsible for schema, PIT, coverage, publication and cross-lineage checks.
It only removes repeated parse/hash work after those consumers have selected
the same immutable evidence files.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import threading
from typing import Any


class EvidenceSessionError(RuntimeError):
    """A file could not be read or decoded by the evidence session."""


@dataclass(frozen=True)
class FileIdentity:
    """The filesystem identity used before consulting a cached value."""

    path: str
    size: int
    mtime_ns: int
    ctime_ns: int
    inode: int

    @classmethod
    def from_path(cls, path: Path) -> "FileIdentity":
        resolved = Path(path).resolve(strict=False)
        try:
            stat = resolved.stat()
        except OSError as exc:
            raise EvidenceSessionError(f"evidence file is unavailable: {resolved}") from exc
        return cls(
            path=str(resolved),
            size=int(stat.st_size),
            mtime_ns=int(stat.st_mtime_ns),
            ctime_ns=int(getattr(stat, "st_ctime_ns", 0)),
            inode=int(getattr(stat, "st_ino", 0)),
        )


@dataclass(frozen=True)
class EvidenceKey:
    """Complete identity for one cached evidence value."""

    identity: FileIdentity
    sha256: str
    receipt_run_id: str
    expected_asof: str
    target: str


@dataclass(frozen=True)
class _JsonEntry:
    key: EvidenceKey
    value: dict[str, Any]


class CentralEvidenceSession:
    """A caller-owned, in-memory immutable-evidence read session.

    The cache is deliberately attached to a session object rather than a
    module-wide global.  A production caller can keep one session for one
    request/run and discard it with the request; no stale evidence is written
    to disk or shared with a later process.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._scope = ("", "", "")
        self._json_by_identity_scope: dict[tuple[FileIdentity, str, str, str], _JsonEntry] = {}
        self._sha_by_identity_scope: dict[tuple[FileIdentity, str, str, str], EvidenceKey] = {}
        self._canonical_by_identity_scope: dict[tuple[FileIdentity, str, str, str], EvidenceKey] = {}
        self._json_loads = 0
        self._sha_loads = 0
        self._canonical_loads = 0

    def bind(
        self,
        *,
        receipt_run_id: str = "",
        expected_asof: str = "",
        target: str = "",
    ) -> None:
        """Bind subsequent cache keys to the current formal lineage scope."""

        with self._lock:
            self._scope = (
                str(receipt_run_id or "").strip(),
                str(expected_asof or "").strip(),
                str(target or "").strip(),
            )

    def _lookup_key(self, identity: FileIdentity) -> tuple[FileIdentity, str, str, str]:
        return (identity, *self._scope)

    @staticmethod
    def _read_bytes(identity: FileIdentity) -> bytes:
        try:
            return Path(identity.path).read_bytes()
        except OSError as exc:
            raise EvidenceSessionError(f"evidence file is unreadable: {identity.path}") from exc

    def _key(self, identity: FileIdentity, digest: str) -> EvidenceKey:
        return EvidenceKey(identity, digest, *self._scope)

    def read_json(self, path: Path, label: str) -> dict[str, Any]:
        """Read one JSON object, reusing it only while identity is unchanged."""

        identity = FileIdentity.from_path(path)
        lookup = self._lookup_key(identity)
        with self._lock:
            cached = self._json_by_identity_scope.get(lookup)
            if cached is not None:
                return cached.value
            raw = self._read_bytes(identity)
            digest = hashlib.sha256(raw).hexdigest()
            try:
                value = json.loads(raw.decode("utf-8-sig"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise EvidenceSessionError(f"{label} cannot be decoded: {identity.path}") from exc
            if not isinstance(value, dict):
                raise EvidenceSessionError(f"{label} must be a JSON object: {identity.path}")
            key = self._key(identity, digest)
            self._json_by_identity_scope[lookup] = _JsonEntry(key, value)
            self._sha_by_identity_scope.setdefault(lookup, key)
            self._json_loads += 1
            return value

    def sha256(self, path: Path) -> str:
        """Return a file SHA, reusing the same session evidence identity."""

        identity = FileIdentity.from_path(path)
        lookup = self._lookup_key(identity)
        with self._lock:
            cached = self._sha_by_identity_scope.get(lookup)
            if cached is not None:
                return cached.sha256
            raw = self._read_bytes(identity)
            key = self._key(identity, hashlib.sha256(raw).hexdigest())
            self._sha_by_identity_scope[lookup] = key
            self._sha_loads += 1
            return key.sha256

    def canonical_json_sha256(self, path: Path, label: str) -> str:
        """Hash canonical JSON while sharing the parsed evidence object."""

        identity = FileIdentity.from_path(path)
        lookup = self._lookup_key(identity)
        with self._lock:
            cached = self._canonical_by_identity_scope.get(lookup)
            if cached is not None:
                return cached.sha256
            value = self.read_json(path, label)
            encoded = json.dumps(
                value,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            key = self._key(identity, hashlib.sha256(encoded).hexdigest())
            self._canonical_by_identity_scope[lookup] = key
            self._canonical_loads += 1
            return key.sha256

    def stats(self) -> dict[str, int]:
        """Return non-sensitive counters for local performance evidence."""

        with self._lock:
            return {
                "json_entries": len(self._json_by_identity_scope),
                "sha_entries": len(self._sha_by_identity_scope),
                "canonical_entries": len(self._canonical_by_identity_scope),
                "json_loads": self._json_loads,
                "sha_loads": self._sha_loads,
                "canonical_loads": self._canonical_loads,
            }


__all__ = [
    "CentralEvidenceSession",
    "EvidenceKey",
    "EvidenceSessionError",
    "FileIdentity",
]
