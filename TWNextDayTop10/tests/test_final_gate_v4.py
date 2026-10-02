"""True isolated Job and four-format render acceptance; no live model/data."""
import csv
import ctypes
from ctypes import wintypes
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile
from datetime import datetime

import process_control

PROJECT = Path(__file__).resolve().parents[1]
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
# The child joins the job before spawning, so the grandchild inherits membership.
CHILD = r'''
import os, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from process_control import await_assignment
await_assignment()
work = Path(sys.argv[2])
grandchild = subprocess.Popen([sys.executable, "-c", "import time\nwhile True: time.sleep(1)"],
                              creationflags=subprocess.CREATE_NO_WINDOW)
(work / "grandchild.pid.tmp").write_text(str(grandchild.pid))
(work / "grandchild.pid.tmp").replace(work / "grandchild.pid")
time.sleep(600)
'''


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _kernel():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return api


def _wait_for_pid(path, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return int(path.read_text())
        time.sleep(.05)
    raise AssertionError("grandchild_pid_not_reported")


def test_real_own_job_deadline_contains_grandchild(tmp_path):
    work = tmp_path / "job"
    work.mkdir()
    script = work / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    api = _kernel()
    job = process_control.OwnedWindowsJob()
    child = job.start([sys.executable, str(script), str(PROJECT), str(work)],
                      dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    grandchild = None
    try:
        # Hold a handle while the grandchild is alive so PID reuse cannot fool the check.
        grandchild = api.OpenProcess(SYNCHRONIZE, False, _wait_for_pid(work / "grandchild.pid"))
        assert grandchild, ctypes.WinError(ctypes.get_last_error())
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            assert child.poll() is None, "child_exited_before_deadline"
            time.sleep(.05)
        job.close()  # The controller's deadline path: KILL_ON_JOB_CLOSE ends the whole tree.
        child.wait(timeout=10)
        assert api.WaitForSingleObject(grandchild, 10000) == WAIT_OBJECT_0
    finally:
        job.close()
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        if grandchild:
            api.CloseHandle(grandchild)


def test_real_four_formats_same_rank_and_receipt(tmp_path, monkeypatch):
    fixture = load(Path(__file__).with_name("test_reporting_performance_contract.py"), "gate_top10_report_fixtures")
    import reporting
    fixture.clock(monkeypatch, "2026-09-23T07:31:00+08:00")
    frame = fixture.rows()
    coverage = frame[["market", "stock_id", "name"]].assign(status="analyzed")
    meta = {"asof": "2026-09-22", "next_session": "2026-09-23",
        "order21_support_by_identity": {"TWSE:2330": {"order21_confidence_score": 99},
                                       "TWSE:2317": {"order21_confidence_score": 1}}}
    monkeypatch.setenv("TOP10_EXCEL_VERIFY", "1")
    checks = tmp_path / "excel-checks"
    checks.mkdir()
    monkeypatch.setenv("TOP10_EXCEL_CHECK_DIR", str(checks))
    # The real project supplies Node and the workbook builder; the user-visible copy stays in tmp.
    visible = tmp_path / "visible" / "2026-09-23.xlsx"
    monkeypatch.setattr(reporting, "flat_report_path", lambda project, target: visible)
    path = reporting.report(PROJECT, frame, coverage, meta, {"model_version": "fixture"},
        datetime.fromisoformat("2026-09-23T07:00:00+08:00"), namespace_root=tmp_path / "namespace",
        report_context=fixture.context(), performance_state={"status": "pending"})
    payload = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert [row["stock_id"] for row in payload["top10"]] == ["2317", "2330"]
    assert payload["top10"][0]["probability"] == .601235
    html = path.with_suffix(".html").read_text(encoding="utf-8")
    assert html.index("2317") < html.index("2330")
    with path.with_suffix(".csv").open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames[-1] == "order21_support_text"
        assert [row["stock_id"] for row in reader if row["record_type"] == "top10"] == ["2317", "2330"]
    with zipfile.ZipFile(path) as archive:
        content = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist()
            if name.endswith(".xml") and ("worksheets/" in name or "sharedStrings" in name))
    assert "2317" in content and "2330" in content
    assert (checks / "excel_inspection.json").is_file()
    assert list(checks.glob("excel_preview_*.png"))
    assert visible.read_bytes() == path.read_bytes()
    # Registration and latest pointer are real writes exclusively in tmp namespace.
    assert (tmp_path / "namespace/reports/latest.json").is_file()
    assert list((tmp_path / "namespace/logs/performance").rglob("*"))
