"""Path-contract version gate of the environment_setup scripts.

Rule (same as shared\\path_contract.py for this toolkit): any ``2.<int>.<int>``
is accepted; a version other than the known ``2.0.0`` only warns; any other
major version or format is rejected.

The gate is not copied here.  Each test reads the *current* production script,
cuts out its version-gate block and runs only that block, pointed at a
temporary contract file under pytest's ``tmp_path``:

* ``scripts\\setup\\configure_windows_environment.ps1`` and
  ``scripts\\setup\\install_d_stock_env.ps1``: the lines from
  ``$PathContractPath =`` through the ``Path contract root must be`` check,
  run with Windows PowerShell 5.1 under ``Set-StrictMode -Version 2.0``.
* ``scripts\\compat\\verify_stock_environment.cmd``: the single
  ``PATH_CONTRACT_OK`` PowerShell line, run through ``cmd.exe`` as written.

The blocks only read the contract file; the parts of the scripts that install
packages or change user environment variables are never executed (asserted by
``test_extracted_blocks_have_no_side_effects``).  The real
``D:\\TWStockData\\config\\path_contract.json`` is never read.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
REAL_CONTRACT = r"D:\TWStockData\config\path_contract.json"
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
PS1_TARGETS = ("configure_windows_environment.ps1", "install_d_stock_env.ps1")
CMD_TARGET = "verify_stock_environment.cmd"
TARGETS = PS1_TARGETS + (CMD_TARGET,)
WARNING_TEXT = "accepted by major version 2"
SIDE_EFFECT_PATTERNS = (
    "SetEnvironmentVariable", "Set-ItemProperty", "New-ItemProperty", "Remove-Item", "New-Item",
    "Set-Content", "Add-Content", "Out-File", "pip", "venv", "Invoke-Logged", "& $", "HKCU:", "HKLM:",
    "setx", "reg ",
)

# (contract_version, expect_accepted, expect_warning); ``MISSING`` = key absent.
MISSING = object()
CASES = [
    ("2.0.0", True, False),
    ("2.1.0", True, True),
    ("2.0.10", True, True),
    ("2.15.3", True, True),
    ("1.1.0", False, False),
    ("1.9.9", False, False),
    ("3.0.0", False, False),
    ("3.1.0", False, False),
    ("12.0.0", False, False),
    ("02.0.0", False, False),
    ("2.0", False, False),
    ("2.0.0.0", False, False),
    ("2.0.0-rc1", False, False),
    ("v2.0.0", False, False),
    ("2.0.0\n", False, False),
    (" 2.0.0", False, False),
    ("", False, False),
    (2, False, False),
    (None, False, False),
    (MISSING, False, False),
]


def _case_id(case) -> str:
    version = case[0]
    return "missing" if version is MISSING else repr(version)


def _ps1_lines(name: str) -> list[str]:
    return (SCRIPTS / "setup" / name).read_text(encoding="utf-8").splitlines()


def ps1_block(name: str) -> str:
    lines = _ps1_lines(name)
    starts = [i for i, line in enumerate(lines) if line.startswith("$PathContractPath =")]
    roots = [i for i, line in enumerate(lines) if "Path contract root must be" in line]
    assert len(starts) == 1 and len(roots) == 1, f"{name}: gate markers not unique"
    end = roots[0] + 1  # closing brace of the root check
    assert lines[end].strip() == "}", f"{name}: unexpected gate end: {lines[end]!r}"
    return "\n".join(lines[starts[0]:end + 1])


def cmd_gate_line() -> str:
    lines = (SCRIPTS / "compat" / CMD_TARGET).read_bytes().decode("utf-8").split("\r\n")
    gates = [line for line in lines if "PATH_CONTRACT_OK" in line]
    assert len(gates) == 1, "verify_stock_environment.cmd: gate line not unique"
    return gates[0]


def write_contract(directory: Path, version) -> Path:
    payload = {
        "contract_id": "pytest",
        "root": r"D:\TWStockData",
        "managed_paths": {"dstock_daily": r"D:\TWStockData\program_data\dstock_daily"},
    }
    if version is not MISSING:
        payload["contract_version"] = version
    path = directory / "path_contract.json"
    path.write_bytes(json.dumps(payload).encode("utf-8"))
    return path


def _redirect(text: str, contract: Path) -> str:
    assert REAL_CONTRACT in text, "gate no longer references the central contract path"
    redirected = text.replace(REAL_CONTRACT, str(contract))
    assert REAL_CONTRACT not in redirected
    return redirected


def run_ps1_gate(name: str, contract: Path, work: Path) -> tuple[bool, bool, str]:
    script = work / f"gate_{name}"
    body = ("Set-StrictMode -Version 2.0\r\n$ErrorActionPreference = 'Stop'\r\n"
            + _redirect(ps1_block(name), contract) + "\r\n'GATE_OK'\r\n")
    script.write_bytes(body.encode("utf-8-sig"))
    proc = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, cwd=str(work),
    )
    out = proc.stdout + proc.stderr
    accepted = proc.returncode == 0 and "GATE_OK" in proc.stdout
    return accepted, "WARNING:" in out and WARNING_TEXT in out, out


def run_cmd_gate(contract: Path, work: Path) -> tuple[bool, bool, str]:
    script = work / "gate_verify_stock_environment.cmd"
    body = ("@echo off\r\nchcp 65001 >nul\r\nsetlocal EnableExtensions EnableDelayedExpansion\r\n"
            + _redirect(cmd_gate_line(), contract) + "\r\necho GATE_RC=!ERRORLEVEL!\r\n")
    script.write_bytes(body.encode("utf-8"))
    proc = subprocess.run(
        ["cmd.exe", "/d", "/c", str(script)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, cwd=str(work),
    )
    out = proc.stdout + proc.stderr
    accepted = "GATE_RC=0" in out and "PATH_CONTRACT_OK" in out
    return accepted, "WARNING:" in out and WARNING_TEXT in out, out


def test_windows_powershell_available() -> None:
    # No skip: these scripts are Windows-only, so a missing PowerShell 5.1 must fail loudly.
    assert os.name == "nt" and POWERSHELL.is_file(), f"Windows PowerShell 5.1 is required: {POWERSHELL}"


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_version_gate(target: str, case, tmp_path: Path) -> None:
    version, want_accepted, want_warning = case
    contract = write_contract(tmp_path, version)
    if target == CMD_TARGET:
        accepted, warned, out = run_cmd_gate(contract, tmp_path)
    else:
        accepted, warned, out = run_ps1_gate(target, contract, tmp_path)
    assert (accepted, warned) == (want_accepted, want_warning), out[-800:]


@pytest.mark.parametrize("target", PS1_TARGETS)
def test_rejection_message_names_version(target: str, tmp_path: Path) -> None:
    contract = write_contract(tmp_path, "3.0.0")
    accepted, _, out = run_ps1_gate(target, contract, tmp_path)
    assert not accepted
    assert "Path contract version must be 2.x" in out and "3.0.0" in out, out[-800:]


@pytest.mark.parametrize("target", PS1_TARGETS)
def test_root_check_still_enforced_after_version_gate(target: str, tmp_path: Path) -> None:
    contract = write_contract(tmp_path, "2.0.0")
    payload = json.loads(contract.read_bytes())
    payload["root"] = r"D:\Elsewhere"
    contract.write_bytes(json.dumps(payload).encode("utf-8"))
    accepted, _, out = run_ps1_gate(target, contract, tmp_path)
    assert not accepted and "Path contract root must be" in out, out[-800:]


def test_extracted_blocks_have_no_side_effects() -> None:
    blocks = {name: ps1_block(name) for name in PS1_TARGETS}
    blocks[CMD_TARGET] = cmd_gate_line()
    for name, block in blocks.items():
        for pattern in SIDE_EFFECT_PATTERNS:
            assert pattern not in block, f"{name}: gate block contains side effect {pattern!r}"
        assert re.search(r"\\A2\\\.\[0-9\]\+\\\.\[0-9\]\+\\z", block), f"{name}: 2.x pattern not found"


@pytest.mark.parametrize("target", PS1_TARGETS)
def test_gate_runs_before_any_environment_change(target: str) -> None:
    lines = _ps1_lines(target)
    gate_end = next(i for i, line in enumerate(lines) if "Path contract root must be" in line)
    effects = [i for i, line in enumerate(lines)
               if any(p in line for p in ("SetEnvironmentVariable", "Invoke-Logged -FilePath", "New-Item"))]
    assert effects and gate_end < min(effects)


def test_cmd_gate_failure_is_counted() -> None:
    lines = (SCRIPTS / "compat" / CMD_TARGET).read_bytes().decode("utf-8").split("\r\n")
    index = next(i for i, line in enumerate(lines) if "PATH_CONTRACT_OK" in line)
    follow = "\n".join(lines[index + 1:index + 5])
    assert "if !ERRORLEVEL! NEQ 0" in follow and "set /a ERRORS+=1" in follow
