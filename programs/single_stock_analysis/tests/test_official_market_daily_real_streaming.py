"""Real-data checks: the published official_market_daily object is read without a byte cap,
strictly, and with bounded memory.

On Windows, the configured canonical shared root supplies the object that the current published head
(``_metadata/stock_data_current.json``) declares for official_market_daily.  The test used to pin the immutable
object c19c4ee7... (1,295,859,469 bytes, sessions up to 2026-09-29); the current-only retention removed it (the
253-session window publishes a one-year object), so the identity (path, sha256, size, record_count) is now read
from the current head and the expected 2330 rows come from an independent strict full read without any row cache.
``TW_SINGLE_STOCK_REAL_OFFICIAL_MARKET_DAILY`` can explicitly override the object path; it must still have the
declared size and sha256.  A missing head entry, a missing input, a wrong size or a wrong sha256 fails.

Each case runs in a fresh child process inside a Windows job object.  The job's per-process memory limit
makes a regression fail with MemoryError instead of exhausting the machine; the job's peak process commit
and the child's own peak working set (RSS) and elapsed time are reported and asserted.  Everything is
written below pytest's tmp_path (a consumer row cache and a synthetic manifest_json head); the object
itself is only read.

The every-stock batch case always runs on Windows (about 2.5 minutes).
``TW_SINGLE_STOCK_REAL_OFFICIAL_MARKET_DAILY_REPORT=<dir>`` also writes each case's measurements there.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ENV_PAYLOAD = "TW_SINGLE_STOCK_REAL_OFFICIAL_MARKET_DAILY"
ENV_REPORT = "TW_SINGLE_STOCK_REAL_OFFICIAL_MARKET_DAILY_REPORT"
ROUTE = "official_market_daily"
HEAD_NAME = "stock_data_current.json"
STOCK = "2330"
# 2330 trades on every session; a one-year (253-session) object has at least this many 2330 rows.
MIN_STOCK_ROWS = 200
MIN_MARKET_STOCKS = 1500
OTHER_STOCKS = (
    "1101", "1102", "1216", "1301", "1303", "1326", "1402", "2002", "2105", "2207", "2301", "2303", "2308",
    "2317", "2327", "2345", "2357", "2379", "2382", "2395", "2408", "2412", "2454", "2603", "2609", "2615",
    "2801", "2880", "2881", "2882", "2883", "2884", "2885", "2886", "2887", "2890", "2891", "2892", "2912",
    "3008", "3034", "3037", "3045", "3231", "3711", "4904", "5871", "5880", "6505",
)
MB = 1024 * 1024


def _current_identity():
    """The official_market_daily entry of the current published head: path, sha256, size and record count."""
    shared = str(Path(__file__).resolve().parents[2] / "shared")
    if shared not in sys.path:
        sys.path.insert(0, shared)
    try:
        from path_contract import CONTRACT

        head = CONTRACT.shared("_metadata", HEAD_NAME)
        document = json.loads(head.read_bytes())
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        pytest.fail(f"cannot read the current published head {HEAD_NAME}: {exc}", pytrace=False)
    entries = [entry for entry in document.get("datasets") or [] if entry.get("route_id") == ROUTE]
    if len(entries) != 1:
        pytest.fail(f"current published head declares {len(entries)} {ROUTE} entries: {head}", pytrace=False)
    entry = entries[0]
    sha256, size, rows = entry.get("sha256"), entry.get("size"), entry.get("record_count")
    if not (isinstance(sha256, str) and len(sha256) == 64 and type(size) is int and size > 0
            and type(rows) is int and rows > 0 and isinstance(entry.get("path"), str)):
        pytest.fail(f"current published head has no usable {ROUTE} identity: {head}", pytrace=False)
    return dict(path=str(head.parent.parent / entry["path"]), sha256=sha256, size=size, record_count=rows,
                actual_max_date=entry.get("actual_max_date"), head=str(head))


def _payload(identity):
    value = os.environ.get(ENV_PAYLOAD)
    if value is not None:
        if not value.strip():
            pytest.fail(f"{ENV_PAYLOAD} override is empty", pytrace=False)
        return Path(value)
    return Path(identity["path"])


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the memory limit uses a Windows job object")


# ---------------------------------------------------------------------------------------------------- child
def _process_memory():
    import ctypes
    import ctypes.wintypes as wt

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = wt.HANDLE
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(Counters), wt.DWORD]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return dict(working_set_mb=round(counters.WorkingSetSize / MB, 1),
                peak_working_set_mb=round(counters.PeakWorkingSetSize / MB, 1),
                commit_mb=round(counters.PagefileUsage / MB, 1),
                peak_commit_mb=round(counters.PeakPagefileUsage / MB, 1))


def _rows_sha256(rows):
    import stock_data_sources as data_sources

    digest = hashlib.sha256()
    for row in rows:
        digest.update(data_sources._canonical(row) + b"\n")
    return digest.hexdigest()


def _child_sources(payload, work, cache=True):
    import stock_data_sources as data_sources

    head = work / "head.json"
    if not head.exists():
        identity = json.loads((work / "identity.json").read_text(encoding="utf-8"))
        entry = dict(route_id=ROUTE, path=payload.name, sha256=identity["sha256"], size=identity["size"],
                     record_count=identity["record_count"], status="PARTIAL")
        head.write_text(json.dumps(dict(schema="stock-data-publication-v1", datasets=[entry])), encoding="utf-8")
    spec = data_sources.SourceSpec("manifest_json", str(payload.parent), str(head), source_id="shared_market_data")
    return data_sources.PublishedSources([spec], row_cache_dir=(work / "cache") if cache else None)


def _cached_rows(sources, stock):
    cached = sources.cached_stock_artifact_rows(ROUTE, stock, pinned_snapshot_fingerprint=sources.fingerprint)
    return None if cached is None else [row for _artifact, rows in cached["artifacts"] for row in rows]


def _child(case, payload, work):
    import stock_data_sources as data_sources

    result = dict(case=case, memory_after_imports=_process_memory())
    started = time.perf_counter()
    if case == "single_prepare":
        sources = _child_sources(payload, work)
        prepared = sources.prewarm_stock_rows([STOCK], [ROUTE], single_stock_prepare=True)
        rows = _cached_rows(sources, STOCK)
        result.update(gaps=list(prepared["gaps"]), source_bytes_read=prepared["source_bytes_read"],
                      rows=len(rows), rows_sha256=_rows_sha256(rows))
    elif case in ("batch50", "batch_all"):
        sources = _child_sources(payload, work)
        stocks = ([STOCK, *OTHER_STOCKS] if case == "batch50"
                  else json.loads((work / "stock_ids.json").read_text(encoding="utf-8")))
        prepared = sources.prewarm_stock_rows(stocks, [ROUTE])
        rows = _cached_rows(sources, STOCK)
        result.update(stocks=len(stocks), gaps=list(prepared["gaps"]), prepared_files=prepared["prepared_files"],
                      source_bytes_read=prepared["source_bytes_read"], rows=len(rows),
                      rows_sha256=_rows_sha256(rows),
                      cache_files=sum(1 for _ in (work / "cache").glob("*.json")),
                      spool_files_left=sum(1 for _ in (work / "cache").glob(".published-rows-stage-*")))
    elif case == "reference":
        # Independent expectation: one strict full read without a row cache, filtered here (not by the reader).
        sources = _child_sources(payload, work, cache=False)
        rows, total = [], 0
        for _artifact, items in sources.iter_artifact_rows(ROUTE):
            for row in items:
                total += 1
                if not isinstance(row, dict) or str(row.get("code") or row.get("stock_id") or row.get("Code")
                                                     or row.get("SecuritiesCompanyCode") or "") == STOCK:
                    rows.append(row)
        result.update(rows=len(rows), rows_sha256=_rows_sha256(rows), total_rows=total,
                      diagnostics=list(sources.diagnostics))
    elif case == "stock_ids":
        sources = _child_sources(payload, work, cache=False)
        codes = set()
        for _artifact, rows in sources.iter_artifact_rows(ROUTE):
            for row in rows:
                if isinstance(row, dict):
                    codes.add(str(row.get("code") or row.get("stock_id") or ""))
        codes.discard("")
        (work / "stock_ids.json").write_text(json.dumps(sorted(codes)), encoding="utf-8")
        result.update(stocks=len(codes), diagnostics=list(sources.diagnostics))
    elif case == "artifacts_contract":
        sources = _child_sources(payload, work)
        try:
            sources.artifacts(ROUTE)
        except data_sources.StreamedRouteRequiredError as exc:
            result.update(raised=type(exc).__name__)
        else:
            result.update(raised=None)
        result.update(diagnostics=list(sources.diagnostics), route_errors=dict(sources._sources[0]["errors"]))
    else:
        raise SystemExit(f"unknown case {case}")
    result.update(seconds=round(time.perf_counter() - started, 2), memory_at_end=_process_memory())
    (work / f"result_{case}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")


# --------------------------------------------------------------------------------------------------- parent
def _run_case(case, work, limit_mb, verified):
    payload = verified["payload"]
    (work / "identity.json").write_text(json.dumps(verified["identity"]), encoding="utf-8")
    import ctypes
    import ctypes.wintypes as wt

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]

    class Io(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("r", "w", "o", "rb", "wb", "ob")]

    class Extended(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("Io", Io), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wt.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
    kernel.QueryInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p]
    kernel.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = [wt.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    info = Extended()
    info.Basic.LimitFlags = 0x100 | 0x2000          # process memory limit, kill on job close
    info.ProcessMemoryLimit = limit_mb * MB
    assert kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
    tests = Path(__file__).resolve().parent
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(tests.parent / "src"), str(tests.parents[1] / "shared"),
                                         env.get("PYTHONPATH", "")])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    started = time.perf_counter()
    try:
        with open(work / f"log_{case}.txt", "w", encoding="utf-8") as log:
            child = subprocess.Popen([sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--child",
                                      case, str(payload), str(work)], stdout=log, stderr=subprocess.STDOUT,
                                     cwd=str(work), env=env, creationflags=0x4)      # CREATE_SUSPENDED
            if not kernel.AssignProcessToJobObject(job, int(child._handle)):
                child.kill()
                raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject")
            ntdll.NtResumeProcess(int(child._handle))
            child.wait(timeout=1800)
        peak = Extended()
        kernel.QueryInformationJobObject(job, 9, ctypes.byref(peak), ctypes.sizeof(peak), None)
    finally:
        kernel.CloseHandle(job)
    wall = round(time.perf_counter() - started, 2)
    log_text = (work / f"log_{case}.txt").read_text(encoding="utf-8", errors="replace")
    assert child.returncode == 0, f"{case} child failed ({child.returncode}):\n{log_text[-3000:]}"
    result = json.loads((work / f"result_{case}.json").read_text(encoding="utf-8"))
    result.update(wall_seconds=wall, job_peak_process_commit_mb=round(peak.PeakProcessMemoryUsed / MB, 1),
                  job_memory_limit_mb=limit_mb)
    report = os.environ.get(ENV_REPORT)
    if report:
        Path(report).mkdir(parents=True, exist_ok=True)
        (Path(report) / f"real_streaming_{case}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result))
    return result


@pytest.fixture(scope="module")
def verified_payload(tmp_path_factory):
    identity = _current_identity()
    payload = _payload(identity)
    expected_size, expected_sha256 = identity["size"], identity["sha256"]
    try:
        if not payload.is_file():
            pytest.fail(f"required published official_market_daily object is missing: {payload}", pytrace=False)
        size = payload.stat().st_size
        if size != expected_size:
            pytest.fail(f"published official_market_daily size mismatch: {payload}; expected {expected_size}, got {size}", pytrace=False)
        digest = hashlib.sha256()
        with payload.open("rb") as handle:
            for block in iter(lambda: handle.read(16 * MB), b""):
                digest.update(block)
    except (OSError, ValueError) as exc:
        pytest.fail(f"cannot read the published official_market_daily object: {payload}: {exc}", pytrace=False)
    actual_sha256 = digest.hexdigest()
    if actual_sha256 != expected_sha256:
        pytest.fail(f"published official_market_daily sha256 mismatch: {payload}; expected {expected_sha256}, got {actual_sha256}", pytrace=False)
    verified = dict(payload=payload, identity=identity)
    reference = _run_case("reference", tmp_path_factory.mktemp("reference"), 1024, verified)
    assert reference["diagnostics"] == []
    assert reference["total_rows"] == identity["record_count"]
    assert reference["rows"] >= MIN_STOCK_ROWS
    verified.update(stock_rows=reference["rows"], stock_rows_sha256=reference["rows_sha256"])
    return verified


def _growth(result):
    return result["job_peak_process_commit_mb"] - result["memory_after_imports"]["commit_mb"]


def _expected_rows(verified):
    return verified["stock_rows"], verified["stock_rows_sha256"]


def test_single_stock_prepare_streams_the_whole_object_with_bounded_memory(verified_payload, tmp_path):
    result = _run_case("single_prepare", tmp_path, 1024, verified_payload)
    assert result["gaps"] == []
    assert result["source_bytes_read"] == verified_payload["identity"]["size"]
    assert (result["rows"], result["rows_sha256"]) == _expected_rows(verified_payload)
    assert result["memory_at_end"]["peak_working_set_mb"] < 256
    assert result["job_peak_process_commit_mb"] < 256
    assert result["seconds"] < 120


def test_batch_of_fifty_stocks_reads_official_route_without_byte_cap(verified_payload, tmp_path):
    result = _run_case("batch50", tmp_path, 1024, verified_payload)
    assert result["gaps"] == []
    assert result["prepared_files"] == 1
    assert result["source_bytes_read"] == verified_payload["identity"]["size"]
    assert (result["rows"], result["rows_sha256"]) == _expected_rows(verified_payload)
    assert result["cache_files"] == 50
    assert result["spool_files_left"] == 0
    assert result["job_peak_process_commit_mb"] < 512
    assert result["seconds"] < 180


def test_every_stock_batch_stays_within_the_memory_budget(verified_payload, tmp_path):
    listed = _run_case("stock_ids", tmp_path, 1024, verified_payload)
    # The current-only capture scope publishes the ordinary-stock universe (1,978 codes on the 2026-10-08 object;
    # the retired full-market object had more than 2,000): still a whole-market batch.
    assert listed["stocks"] > MIN_MARKET_STOCKS
    result = _run_case("batch_all", tmp_path, 1024, verified_payload)
    assert result["gaps"] == []
    assert result["stocks"] == listed["stocks"]
    assert result["cache_files"] == listed["stocks"]
    assert result["spool_files_left"] == 0
    assert (result["rows"], result["rows_sha256"]) == _expected_rows(verified_payload)
    assert result["job_peak_process_commit_mb"] < 512


def test_artifacts_refuses_to_materialize_the_object(verified_payload, tmp_path):
    result = _run_case("artifacts_contract", tmp_path, 512, verified_payload)
    assert result["raised"] == "StreamedRouteRequiredError"
    assert result["diagnostics"] == [] and result["route_errors"] == {}
    assert result["job_peak_process_commit_mb"] < 100
    assert result["seconds"] < 5


if __name__ == "__main__" and len(sys.argv) == 5 and sys.argv[1] == "--child":
    tests_dir = Path(__file__).resolve().parent
    for entry in (str(tests_dir.parents[1] / "shared"), str(tests_dir.parent / "src")):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    _child(sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]))
