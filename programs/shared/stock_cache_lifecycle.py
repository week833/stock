"""Lifecycle locks and reader leases for rebuildable analysis caches only."""
from __future__ import annotations

import contextlib
import ctypes
import errno
import hashlib
import json
import os
import stat
from pathlib import Path
import tempfile
import time
import threading
import uuid


PREOPEN_HISTORIC_HEAD = "a807be3ea33b4acb2d076ba763755dc37b51ca029fd74ed2d7771f5d07cc8ffd"
QUERY_CACHE_DIR = "_query/stock-query-projection-v1"
_CATALOG_THREADS = threading.local()
_CATALOG_MUTEX = threading.Lock()
_CATALOG_LOCKS = {}


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def file_lock(path):
    """A process lock; process exit releases it without a guessed lease timeout."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        if os.name == "nt":
            import msvcrt
            while True:
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
                    time.sleep(0.05)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def catalog_lock(cache_root):
    """Short catalog lock, reentrant only within the same thread/module."""
    path = Path(cache_root) / "locks" / "lifecycle.lock"
    key = str(path.resolve())
    with _CATALOG_MUTEX:
        mutex = _CATALOG_LOCKS.setdefault(key, threading.RLock())
    with mutex:
        held = getattr(_CATALOG_THREADS, "held", set())
        if key in held:
            yield
            return
        with file_lock(path):
            _CATALOG_THREADS.held = held | {key}
            try:
                yield
            finally:
                _CATALOG_THREADS.held = held


def source_registry(shared_root):
    return Path(shared_root) / QUERY_CACHE_DIR



def _windows_cache_metadata(path):
    """Metadata only: READ_ATTRIBUTES, share all, no reparse traversal or privileges."""
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    native = ctypes.WinDLL("ntdll", use_last_error=True)
    class UnicodeString(ctypes.Structure):
        _fields_ = [("Length", w.USHORT), ("MaximumLength", w.USHORT), ("Buffer", w.LPWSTR)]
    class ObjectAttributes(ctypes.Structure):
        _fields_ = [("Length", w.ULONG), ("RootDirectory", w.HANDLE),
            ("ObjectName", ctypes.POINTER(UnicodeString)), ("Attributes", w.ULONG),
            ("SecurityDescriptor", ctypes.c_void_p), ("SecurityQualityOfService", ctypes.c_void_p)]
    class IoStatus(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]
    class StandardInfo(ctypes.Structure):
        _fields_ = [("AllocationSize", ctypes.c_longlong), ("EndOfFile", ctypes.c_longlong),
            ("NumberOfLinks", w.DWORD), ("DeletePending", ctypes.c_ubyte), ("Directory", ctypes.c_ubyte)]
    class FileId(ctypes.Structure):
        _fields_ = [("VolumeSerialNumber", ctypes.c_ulonglong), ("FileId", ctypes.c_ubyte * 16)]
    class AttributeTag(ctypes.Structure):
        _fields_ = [("FileAttributes", w.DWORD), ("ReparseTag", w.DWORD)]
    native.NtOpenFile.argtypes = (ctypes.POINTER(w.HANDLE), w.DWORD,
        ctypes.POINTER(ObjectAttributes), ctypes.POINTER(IoStatus), w.ULONG, w.ULONG)
    native.NtOpenFile.restype = ctypes.c_long
    native.RtlNtStatusToDosError.argtypes = (w.ULONG,)
    native.RtlNtStatusToDosError.restype = w.ULONG
    kernel.GetFileInformationByHandleEx.argtypes = (w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD)
    kernel.GetFileInformationByHandleEx.restype = w.BOOL
    kernel.GetFinalPathNameByHandleW.argtypes = (w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD)
    kernel.GetFinalPathNameByHandleW.restype = w.DWORD
    kernel.CloseHandle.argtypes = (w.HANDLE,)
    absolute = os.path.abspath(os.fspath(path))
    name = ctypes.create_unicode_buffer("\\??\\" + absolute)
    value = UnicodeString(len(name.value) * 2, (len(name.value) + 1) * 2, ctypes.cast(name, w.LPWSTR))
    attributes = ObjectAttributes(ctypes.sizeof(ObjectAttributes), None, ctypes.pointer(value), 0x40, None, None)
    handle, status_block = w.HANDLE(), IoStatus()
    status = native.NtOpenFile(ctypes.byref(handle), 0x80, ctypes.byref(attributes),
        ctypes.byref(status_block), 7, 0x200000)
    if status < 0:
        raise ctypes.WinError(native.RtlNtStatusToDosError(status & 0xffffffff))
    if not handle.value:
        raise OSError(errno.EACCES, "cache_metadata_handle_unknown", absolute)
    try:
        tag, standard, identity = AttributeTag(), StandardInfo(), FileId()
        for kind, result in ((9, tag), (1, standard), (18, identity)):
            if not kernel.GetFileInformationByHandleEx(handle, kind, ctypes.byref(result), ctypes.sizeof(result)):
                raise ctypes.WinError(ctypes.get_last_error())
        if tag.FileAttributes & 0x400 or tag.ReparseTag:
            raise OSError(errno.EPERM, "cache_target_reparse_point", absolute)
        if standard.DeletePending or not standard.NumberOfLinks:
            raise OSError(errno.EBUSY, "cache_metadata_delete_pending_or_unlinked", absolute)
        file_id = int.from_bytes(bytes(identity.FileId), "little")
        if not identity.VolumeSerialNumber or not file_id:
            raise OSError(errno.EIO, "cache_metadata_identity_unknown", absolute)
        final = ctypes.create_unicode_buffer(32768)
        size = kernel.GetFinalPathNameByHandleW(handle, final, len(final), 0)
        if not size or size >= len(final):
            raise OSError(errno.EIO, "cache_metadata_final_path_unknown", absolute)
        if not final.value.startswith("\\\\?\\"):
            raise OSError(errno.EPERM, "cache_metadata_final_path_unknown", absolute)
        final_path = final.value[4:]
        if os.path.normcase(final_path) != os.path.normcase(absolute):
            raise OSError(errno.EPERM, "cache_metadata_final_path_changed", absolute)
        return {"volume_serial": identity.VolumeSerialNumber, "file_id": file_id,
            "path": final_path, "directory": bool(standard.Directory)}
    finally:
        kernel.CloseHandle(handle)


def cache_path(cache_root, path, *, allow_root=False, missing_ok=False):
    """Validate an explicit cache boundary without following root/ancestor reparses."""
    root = Path(os.path.abspath(os.fspath(cache_root)))
    target = Path(os.path.abspath(os.fspath(path)))
    try:
        inside = os.path.normcase(os.path.commonpath((root, target))) == os.path.normcase(str(root))
    except ValueError:
        inside = False
    if not inside or target == root and not allow_root:
        raise OSError(errno.EPERM, "cache_target_outside_root", str(target))
    checked = []
    for ancestor in (*reversed(target.parents), target):
        try:
            identity = ancestor.lstat()
        except FileNotFoundError:
            if missing_ok and ancestor == target:
                return None
            raise
        if (stat.S_ISLNK(identity.st_mode)
                or getattr(identity, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise OSError(errno.EPERM, "cache_target_reparse_point", str(ancestor))
        checked.append((ancestor, identity.st_dev, identity.st_ino,
            getattr(identity, "st_file_attributes", 0)))
    if os.name == "nt":
        metadata = _windows_cache_metadata(target)
        if (metadata["volume_serial"], metadata["file_id"]) != (identity.st_dev, identity.st_ino):
            raise OSError(errno.EPERM, "cache_metadata_identity_changed", str(target))
        if metadata["directory"] != stat.S_ISDIR(identity.st_mode):
            raise OSError(errno.EPERM, "cache_metadata_identity_changed", str(target))
        for ancestor, device, inode, attributes in checked:
            after = ancestor.lstat()
            if (after.st_dev, after.st_ino, getattr(after, "st_file_attributes", 0)) != (device, inode, attributes):
                raise OSError(errno.EPERM, "cache_metadata_identity_changed", str(ancestor))
    else:
        resolved = target.resolve(strict=True)
        if os.path.normcase(os.path.commonpath((root, resolved))) != os.path.normcase(str(root)):
            raise OSError(errno.EPERM, "cache_resolved_target_outside_root", str(target))
    return target


def unlink_cache_file(cache_root, path, *, missing_ok=False):
    target = cache_path(cache_root, path, missing_ok=missing_ok)
    if target is None:
        return False
    target.unlink(missing_ok=missing_ok)
    return True


def process_identity(pid):
    """Return (state, creation identity); an unreadable process stays unknown."""
    if type(pid) is not int or pid <= 0:
        return "unknown", None
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ("dead", None) if ctypes.get_last_error() == 87 else ("unknown", None)
        try:
            exit_code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return "unknown", None
            if exit_code.value != 259:
                return "dead", None
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
                return "unknown", None
            created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            return "alive", str(created)
        finally:
            kernel.CloseHandle(handle)
    try:
        raw = Path("/proc") / str(pid) / "stat"
        fields = raw.read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        return ("dead", None) if fields[0] == "Z" else ("alive", fields[19])
    except FileNotFoundError:
        return "dead", None
    except (OSError, ValueError, IndexError):
        return "unknown", None


class CacheLease:
    def __init__(self, cache_root, key, physical_refs=(), cache_keys=(), *, already_locked=False):
        self.root, self.key, self.path = Path(cache_root), str(key), None
        state, created = process_identity(os.getpid())
        self.value = {"schema": "stock-analysis-cache-lease-v1", "key": self.key,
            "pid": os.getpid(), "process_created": created if state == "alive" else None,
            "physical_refs": list(physical_refs), "cache_keys": list(cache_keys)}
        name = self.key + "." + uuid.uuid4().hex + ".lease.json"
        with contextlib.nullcontext() if already_locked else catalog_lock(self.root):
            self.path = self.root / "leases" / name
            atomic_json(self.path, self.value)

    def update_refs(self, physical_refs, cache_keys=(), *, already_locked=False):
        """Replace this reader's exact refs while it owns the catalog lock."""
        if self.path is None:
            raise ValueError("reader_lease_closed")
        value = dict(self.value, physical_refs=list(physical_refs), cache_keys=list(cache_keys))
        with contextlib.nullcontext() if already_locked else catalog_lock(self.root):
            atomic_json(self.path, value)
            self.value = value

    def close(self, *, already_locked=False):
        if self.path is not None:
            with contextlib.nullcontext() if already_locked else catalog_lock(self.root):
                self.path.unlink(missing_ok=True)
            self.path = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        with contextlib.suppress(OSError, AttributeError):
            self.close()


def active_lease_records(cache_root, *, apply=False):
    """Read under catalog_lock for deletion; PID reuse never keeps a dead lease alive."""
    records, unknown, stale, states = [], [], [], {}
    directory = Path(cache_root) / "leases"
    if not directory.exists():
        return records, unknown, stale
    for path in directory.glob("*.lease.json"):
        key = path.name.rsplit(".", 3)[0]
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(value, dict) or value.get("schema") != "stock-analysis-cache-lease-v1"
                    or value.get("key") != key or not isinstance(key, str)):
                raise ValueError("lease shape unknown")
            pid = value.get("pid")
            if type(pid) is not int:
                raise ValueError("lease process unknown")
            if pid not in states:
                states[pid] = process_identity(pid)
            state, created = states[pid]
            original = value.get("process_created")
            known_creation = isinstance(original, str) and original.isdigit()
            if state == "dead" or state == "alive" and known_creation and original != created:
                stale.append(str(path))
                if apply:
                    unlink_cache_file(cache_root, path, missing_ok=True)
                continue
            if not isinstance(value.get("physical_refs", []), list) or not isinstance(value.get("cache_keys", []), list):
                unknown.append({"path": str(path), "key": key, "reason": "lease_references_unknown"})
                value["physical_refs"] = value.get("physical_refs") if isinstance(value.get("physical_refs"), list) else []
                value["cache_keys"] = value.get("cache_keys") if isinstance(value.get("cache_keys"), list) else []
            records.append(value)
            if state != "alive" or not known_creation:
                unknown.append({"path": str(path), "key": key, "reason": "process_identity_unknown"})
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            # The filename still identifies the cache even if its lease JSON is
            # unreadable. Exact physical refs, if recoverable, also stay pinned.
            records.append({"key": key, "physical_refs": [], "cache_keys": []})
            unknown.append({"path": str(path), "key": key, "reason": str(exc)})
    return records, unknown, stale


def active_leases(cache_root, *, apply=False):
    records, unknown, stale = active_lease_records(cache_root, apply=apply)
    keys = {record["key"] for record in records}
    for record in records:
        keys.update(value for value in record.get("cache_keys", ()) if isinstance(value, str))
    return keys, unknown, stale


_PUBLICATIONS = {}


def publication_metadata(shared_root, head=None):
    """Read only manifest metadata; cache it by physical identity within this process."""
    root = Path(shared_root)
    path = root / "_metadata" / "stock_data_current.json" if head is None else root / "_manifests" / (head + ".json")
    stamp = path.stat()
    identity = (str(path.resolve()), stamp.st_dev, stamp.st_ino, stamp.st_size, stamp.st_mtime_ns)
    if identity not in _PUBLICATIONS:
        raw = path.read_bytes()
        after = path.stat()
        if (stamp.st_dev, stamp.st_ino, stamp.st_size, stamp.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError("publication_metadata_changed")
        digest = hashlib.sha256(raw).hexdigest()
        if head is not None and digest != head:
            raise ValueError("publication_manifest_hash_mismatch")
        value = json.loads(raw)
        if not isinstance(value, dict) or not isinstance(value.get("datasets"), list):
            raise ValueError("publication_manifest_shape_unknown")
        compact = {"schema": value.get("schema"), "datasets": [
            {name: entry.get(name) for name in ("route_id", "path", "sha256", "size")}
            for entry in value["datasets"] if isinstance(entry, dict)]}
        for previous in list(_PUBLICATIONS):
            if previous[0] == identity[0]:
                del _PUBLICATIONS[previous]
        _PUBLICATIONS[identity] = (compact, {"ref": path.relative_to(root).as_posix(),
            "sha256": digest, "size": len(raw)})
    return _PUBLICATIONS[identity]


def publication_objects(shared_root):
    document, _ = publication_metadata(shared_root)
    return {entry["sha256"] for entry in document["datasets"] if isinstance(entry, dict)
            and isinstance(entry.get("sha256"), str)}


def live_shared_data_references(shared_root, *, include_current=True):
    """Stdlib-only export of the consumer's real immutable data dependencies."""
    root = Path(shared_root)
    refs = []
    if include_current:
        _, current_ref = publication_metadata(root)
        refs.append(dict(root=str(root), ref=current_ref, expand=True))
    historic, historic_ref = publication_metadata(root, PREOPEN_HISTORIC_HEAD)
    refs.append(dict(root=str(root), ref=historic_ref, expand=False))
    for entry in historic["datasets"]:
        if entry.get("route_id") in {"finmind_price_adj", "official_market_daily"}:
            refs.append(dict(root=str(root), ref={"ref": entry["path"], "sha256": entry["sha256"],
                "size": entry["size"]}, expand=False, route_ids=[entry["route_id"]]))
    records, unknown, _ = active_lease_records(source_registry(root))
    for record in records:
        for reference in record.get("physical_refs", ()):
            if isinstance(reference, dict):
                refs.append(reference)
    # Unknown lease metadata is exposed separately to the GC planner; it is not
    # converted into a fabricated all-data reference or an implicit permission.
    return refs


@contextlib.contextmanager
def shared_data_gc_guard(shared_root, *, include_current=True):
    """Caller owns the producer/store lock first, then this catalog lock."""
    registry = source_registry(shared_root)
    with catalog_lock(registry):
        yield live_shared_data_references(shared_root, include_current=include_current)
