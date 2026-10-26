from __future__ import annotations

import hashlib
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


SUITE_DIR = Path(__file__).resolve().parents[1]
# 服務與排程定義寫的是正式安裝路徑；檔案內容仍從目前的套件目錄讀取。
FORMAL_SUITE_DIR = Path(r"D:\stock\programs\telegram_report_delivery")
SERVICE_DIR = SUITE_DIR / "service"
MIX_SCHEDULE_DEFINITION = SUITE_DIR / "MIX_WEEKDAY_0800_TASK_DEFINITION.json"
MIX_TASK_NAME = "MIX Telegram Report 08-00"
MIX_RUNNER_NAME = "RUN_SEND_MIX_REPORT.cmd"
MIX_INSTALLER_NAME = "INSTALL_MIX_WEEKDAY_0800_TASK.ps1"
MIX_PRODUCTION_POINTER = (
    r"D:\TWStockData\program_data\mix\reports\LATEST_C0_RUN.json"
)
FORBIDDEN_0800_TOKENS = (
    "D-Stock",
    "RUN_SCHEDULED_SEND",
    "RUN_SEND_LATEST_REPORT",
)


def test_service_configuration_is_headless_and_self_restarting() -> None:
    root = ET.parse(SERVICE_DIR / "DStockTelegramCommandBot.xml").getroot()
    assert root.findtext("id") == "DStockTelegramCommandBot"
    assert root.findtext("executable") == r"D:\stock\.venv\Scripts\python.exe"
    expected_listener = '"' + str(FORMAL_SUITE_DIR / "telegram_stock_command_bot.py") + '"'
    assert root.findtext("arguments") == expected_listener
    assert root.findtext("workingdirectory") == str(FORMAL_SUITE_DIR)
    assert root.findtext("logpath") == r"D:\TWStockData\services\telegram\service_logs"
    assert root.findtext("startmode") == "Automatic"
    assert root.find("delayedAutoStart") is not None
    assert root.findtext("serviceaccount/user") == "LocalSystem"
    assert root.find("interactive") is None
    failure = root.find("onfailure")
    assert failure is not None
    assert failure.attrib["action"] == "restart"


def test_service_installer_enforces_listener_executable_and_lineage() -> None:
    installer = (SUITE_DIR / "INSTALL_COMMAND_BOT_SERVICE.ps1").read_text(encoding="utf-8")
    assert "$expectedPython" in installer
    assert "$configuredExecutable" in installer
    assert "$configuredWorkingDirectory" in installer
    assert "$configuredArguments" in installer
    assert "$listenerExecutableMatch" in installer
    assert "$listenerCommandLineMatch" in installer
    assert "$listenerSourceSha256Match" in installer


def test_status_check_reports_canonical_listener_process_identity() -> None:
    checker = (SUITE_DIR / "CHECK_COMMAND_BOT_STATUS.ps1").read_text(encoding="utf-8")
    assert "$expectedServicePython" in checker
    assert "$expectedTaskPython" in checker
    assert "$listenerExecutableMatch" in checker
    assert "$listenerCommandLineMatch" in checker
    assert "unavailable_due_to_access" in checker
    assert "OwnerCreationIdentityMatch" in checker
    assert "OwnerHeartbeatPidMatch" in checker
    assert "ListenerReadiness" in checker
    assert "restart_required" in checker
    assert "stale_source" in checker
    assert "Resolve-ListenerStatus" in checker
    assert "TaskActionExecutable" in checker


@pytest.mark.skipif(os.name != "nt", reason="PowerShell listener decision helper is Windows-only")
def test_verified_venv_service_owner_requires_native_helper_evidence() -> None:
    checker = (SUITE_DIR / "CHECK_COMMAND_BOT_STATUS.ps1").resolve()
    source = checker.read_text(encoding="utf-8")
    assert "System32\\WindowsPowerShell\\v1.0\\powershell.exe" in source
    assert "-NoProfile" in source
    assert "-VerifyOnly" in source
    assert 'EnvironmentVariables["PSModulePath"]' in source
    assert "Test-StrictVenvServiceOwnerEvidence" in source
    assert "WaitForExit(30000)" in source
    assert "$process.Kill()" in source
    assert source.index("$sourceSha256Match =") < source.index("$verifiedVenvServiceOwner = $false")
    restart_helper = (SUITE_DIR / "RESTART_COMMAND_BOT_SERVICE.ps1").read_text(encoding="utf-8")
    assert '"venv_launcher"' in restart_helper
    assert "ProcessLineagePids" in restart_helper

    escaped_checker = str(checker).replace("'", "''")
    command = r"""
$ErrorActionPreference = 'Stop'
. '__CHECKER__'
$sha = 'a' * 64
$owner = [pscustomobject]@{ processId = 57292; startedAt = '2026-09-30T01:00:00Z'; sourceSha256 = $sha }
$heartbeat = [pscustomobject]@{ pid = 57292; startedAt = '2026-09-30T01:00:00Z'; updatedAt = '2026-09-30T01:05:00Z'; sourceSha256 = $sha }
$ownerProcess = [pscustomobject]@{ ProcessId = 57292; CreationDate = '2026-09-30T00:59:59Z'; ExecutablePath = 'C:\Python312\python.exe' }
$evidence = [pscustomobject]@{
    Operation = 'verify'
    ServiceName = 'DStockTelegramCommandBot'
    ServiceState = 'Running'
    OwnerPid = 57292
    OwnerStartedAt = '2026-09-30T01:00:00Z'
    OwnerCreationDate = '2026-09-30T00:59:59Z'
    HeartbeatStartedAt = '2026-09-30T01:00:00Z'
    ResidentIdentityStartedAt = '2026-09-30T01:00:00Z'
    HeartbeatAgeSeconds = 5
    HeartbeatStatus = 'polling'
    OwnerSourceSHA256 = $sha
    HeartbeatSourceSHA256 = $sha
    ResidentSourceSHA256 = $sha
    LoadedSourceSHA256 = $sha
    ListenerSHA256 = $sha
    DiskListenerSHA256 = $sha
    SourceMismatch = $false
    SourceEvidence = [pscustomobject]@{
        OwnerHeartbeatSourceMatch = $true
        DiskContractSourceMatch = $true
        DiskSourceIdentityStable = $true
        ResidentDiskSourceMatch = $true
        ResidentPidIdentity = $true
        ResidentPidHeartbeatIdentity = $true
        ResidentPidOwnerIdentity = $true
    }
}
$common = @{
    HelperExitCode = 0
    Evidence = $evidence
    ExpectedServicePython = 'D:\stock\.venv\Scripts\python.exe'
    ObservedOwnerExecutablePath = $ownerProcess.ExecutablePath
    Owner = $owner
    Heartbeat = $heartbeat
    OwnerProcess = $ownerProcess
    DiskSourceSHA256 = $sha
    ObservedServiceName = 'DStockTelegramCommandBot'
    ObservedServiceState = 'Running'
}
$validVenvChain = Test-StrictVenvServiceOwnerEvidence @common
$unknownBaseArgs = $common.Clone()
$unknownBaseArgs.HelperExitCode = 2
$unknownBaseArgs.ObservedOwnerExecutablePath = 'C:\Unrecognized\python.exe'
$unknownBase = Test-StrictVenvServiceOwnerEvidence @unknownBaseArgs
$wrongParentArgs = $common.Clone()
$wrongParentArgs.HelperExitCode = 2
$wrongParentChain = Test-StrictVenvServiceOwnerEvidence @wrongParentArgs
$evidence.SourceEvidence.ResidentPidOwnerIdentity = $false
$identityMismatch = Test-StrictVenvServiceOwnerEvidence @common
$evidence.SourceEvidence.ResidentPidOwnerIdentity = $true
$helperFailedArgs = $common.Clone()
$helperFailedArgs.HelperExitCode = 1
$helperFailed = Test-StrictVenvServiceOwnerEvidence @helperFailedArgs
[pscustomobject]@{
    ValidVenvChain = $validVenvChain
    UnknownBase = $unknownBase
    WrongParentChain = $wrongParentChain
    IdentityMismatch = $identityMismatch
    HelperFailed = $helperFailed
} | ConvertTo-Json -Compress
""".replace("__CHECKER__", escaped_checker)
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload == {
        "ValidVenvChain": True,
        "UnknownBase": False,
        "WrongParentChain": False,
        "IdentityMismatch": False,
        "HelperFailed": False,
    }


def test_explicit_service_restart_requires_exact_owner_replacement_proof() -> None:
    restart = (SUITE_DIR / "RESTART_COMMAND_BOT_SERVICE.ps1").read_text(encoding="utf-8")
    wrapper = (SUITE_DIR / "RESTART_COMMAND_BOT_SERVICE.cmd").read_text(encoding="utf-8")
    assert "-ConfirmRestart" in restart
    assert "-ConfirmDeploy" in restart
    assert "Test-ProcessCreationIdentity" in restart
    assert "Wait-ExactProcessExit" in restart
    assert "OldOwnerPid" in restart
    assert "NewOwnerPid" in restart
    assert "LoadedSourceSHA256" in restart
    assert "DStockTelegramCommandBot" in restart
    assert "Stop-Process" not in restart
    assert "RESTART_COMMAND_BOT_SERVICE.ps1" in wrapper


@pytest.mark.skipif(os.name != "nt", reason="PowerShell listener decision helper is Windows-only")
def test_listener_status_helper_is_deterministic_for_access_sha_and_liveness_cases() -> None:
    checker = (SUITE_DIR / "CHECK_COMMAND_BOT_STATUS.ps1").resolve()
    escaped_checker = str(checker).replace("'", "''")
    cases = [
        (
            "access_denied_alive",
            True,
            True,
            True,
            True,
            True,
            "unavailable_due_to_access",
            "unavailable_due_to_access",
            5,
            "polling",
            True,
            "running",
            "ready",
        ),
        (
            "sha_mismatch",
            True,
            True,
            True,
            True,
            True,
            "unavailable_due_to_access",
            "unavailable_due_to_access",
            5,
            "polling",
            False,
            "running",
            "restart_required",
        ),
        (
            "stale_heartbeat",
            True,
            True,
            True,
            True,
            True,
            "match",
            "match",
            181,
            "polling",
            True,
            "stale",
            "blocked",
        ),
        (
            "dead_pid",
            True,
            False,
            False,
            False,
            False,
            "unavailable",
            "unavailable",
            5,
            "polling",
            True,
            "stopped",
            "blocked",
        ),
        (
            "full_healthy",
            True,
            True,
            True,
            True,
            True,
            "match",
            "match",
            5,
            "polling",
            True,
            "running",
            "ready",
        ),
    ]
    for (
        name,
        host_valid,
        owner_alive,
        name_match,
        creation_match,
        heartbeat_pid_match,
        executable_status,
        command_status,
        heartbeat_age,
        heartbeat_status,
        source_match,
        expected_health,
        expected_readiness,
    ) in cases:
        command = f"""
$ErrorActionPreference = 'Stop'
. '{escaped_checker}'
Resolve-ListenerStatus -HostStateValid ${str(host_valid).lower()} -OwnerProcessAlive ${str(owner_alive).lower()} -OwnerNameMatch ${str(name_match).lower()} -OwnerCreationIdentityMatch ${str(creation_match).lower()} -OwnerHeartbeatPidMatch ${str(heartbeat_pid_match).lower()} -OwnerExecutableStatus '{executable_status}' -OwnerCommandLineStatus '{command_status}' -HeartbeatAgeSeconds {heartbeat_age} -HeartbeatStatus '{heartbeat_status}' -SourceSha256Match ${str(source_match).lower()} | ConvertTo-Json -Compress
"""
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{name}: {result.stderr or result.stdout}"
        payload = json.loads(result.stdout)
        assert payload["ListenerHealth"] == expected_health, name
        assert payload["ListenerReadiness"] == expected_readiness, name


def test_manual_command_bot_entrypoint_uses_service_venv() -> None:
    wrapper = (SUITE_DIR / "RUN_COMMAND_BOT.cmd").read_text(encoding="utf-8")
    assert r'set "PY=%ROOT%\.venv\Scripts\python.exe"' in wrapper
    assert "find_python.cmd" not in wrapper
    assert "telegram_stock_command_bot.py" in wrapper


def test_service_wrapper_matches_recorded_sha256() -> None:
    wrapper = SERVICE_DIR / "DStockTelegramCommandBot.exe"
    expected = (SERVICE_DIR / "DStockTelegramCommandBot.sha256").read_text(encoding="ascii").split()[0]
    actual = hashlib.sha256(wrapper.read_bytes()).hexdigest()
    assert actual.casefold() == expected.casefold()


def _assert_mix_schedule_definition(contract: dict[str, object]) -> None:
    assert contract["schema_version"] == "mix_telegram_weekday_schedule_definition_v1"
    assert contract["task_name"] == MIX_TASK_NAME
    assert contract["timezone"] == "Taipei Standard Time"
    trigger = contract["trigger"]
    assert isinstance(trigger, dict)
    assert trigger["days_of_week"] == [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
    ]
    assert trigger["start_time"] == "08:00"

    action = contract["action"]
    assert isinstance(action, dict)
    assert action["executable"].casefold().endswith("\\cmd.exe")
    assert action["working_directory"] == str(FORMAL_SUITE_DIR)
    assert action["no_pause"] is True
    assert action["max_wait_seconds"] == 0
    arguments = str(action["arguments"])
    assert "NO_PAUSE=1" in arguments
    assert MIX_RUNNER_NAME in arguments
    assert "--max-wait-seconds 0" in arguments
    assert "--poll-seconds 60" in arguments
    assert all(token not in arguments for token in FORBIDDEN_0800_TOKENS)

    settings = contract["settings"]
    assert isinstance(settings, dict)
    assert settings["multiple_instances"] == "IgnoreNew"
    assert settings["execution_time_limit_seconds"] == 0
    assert settings["allow_start_if_on_batteries"] is True
    assert settings["dont_stop_if_going_on_batteries"] is True
    assert settings["dont_stop_on_idle_end"] is True

    publication = contract["publication_contract"]
    assert isinstance(publication, dict)
    assert publication["source"] == MIX_PRODUCTION_POINTER
    assert publication["required_pointer_schema"] == "mix-stock-daily-c0-current-report-v1"
    assert publication["required_status"] == "limited_baseline_effect_unverified"
    assert publication["required_official"] is True
    assert publication["required_publishable"] is False
    assert publication["publishable_is_quality_indicator"] is True
    assert publication["required_execution_success"] is True
    assert publication["required_artifact_integrity"] is True
    assert publication["requires_central_receipt_and_registry"] is True
    assert publication["selection_policy"] == "formal_only"
    assert publication["no_action_action"] == "reject_no_action_report"
    assert publication["quality_conditions_action"] == "include_unmet_conditions_without_blocking"
    assert publication["formal_unavailable_action"] == "exit_nonzero_no_fallback"
    assert publication["formal_send_failure_action"] == "exit_nonzero_no_fallback"
    # Non-blocking: an older report is sent with both data days; integrity/tamper/ETF failures stay fail closed.
    assert publication["stale_report_action"] == "send_with_report_and_latest_data_days_labeled"
    assert publication["integrity_failure_action"] == "exit_nonzero_fail_closed_no_send"
    assert "stale_or_integrity_failure_action" not in publication
    # MIX v1 residue: the C0 sender writes no feedback publication receipt, so the field must be gone.
    assert "requires_feedback_publication_receipt" not in publication


def test_c0_sender_has_no_feedback_publication_receipt() -> None:
    """The removed requires_feedback_publication_receipt field matches the C0 sender (no feedback receipt)."""
    sender_source = (SUITE_DIR / "mix_report_sender.py").read_text(encoding="utf-8")
    assert "feedback" not in sender_source.casefold()
    for name in (MIX_INSTALLER_NAME, "SHOW_MIX_WEEKDAY_0800_TASK.ps1"):
        script = (SUITE_DIR / name).read_text(encoding="utf-8")
        assert "@($publication.PSObject.Properties.Name) -contains 'requires_feedback_publication_receipt'" in script, name
        assert "$publication.requires_feedback_publication_receipt -ne" not in script, name


@pytest.mark.parametrize("legacy_field", ["requires_feedback_publication_receipt", "stale_or_integrity_failure_action"])
def test_mix_definition_scripts_reject_legacy_publication_fields(tmp_path: Path, legacy_field: str) -> None:
    """INSTALL -Plan and SHOW -VerifyOnly accept the current definition and refuse a legacy field (read-only)."""
    for name in (MIX_INSTALLER_NAME, "SHOW_MIX_WEEKDAY_0800_TASK.ps1", MIX_RUNNER_NAME):
        (tmp_path / name).write_bytes((SUITE_DIR / name).read_bytes())
    definition = json.loads(MIX_SCHEDULE_DEFINITION.read_text(encoding="utf-8"))
    # The scripts bind working_directory to their own folder; everything else is the production definition.
    definition["action"]["working_directory"] = str(tmp_path.resolve())
    target = tmp_path / "MIX_WEEKDAY_0800_TASK_DEFINITION.json"

    def run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(tmp_path / script), *args],
            capture_output=True,
            text=True,
            # PowerShell 5.1 writes stderr in the console code page (e.g. cp950); the asserted
            # message is ASCII, so decode leniently instead of crashing the reader thread.
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    target.write_text(json.dumps(definition, ensure_ascii=False, indent=2), encoding="utf-8")
    for script, flag in ((MIX_INSTALLER_NAME, "-Plan"), ("SHOW_MIX_WEEKDAY_0800_TASK.ps1", "-VerifyOnly")):
        accepted = run(script, flag)
        assert accepted.returncode == 0, (script, accepted.stderr or accepted.stdout)
    definition["publication_contract"][legacy_field] = True if legacy_field.startswith("requires_") else "exit_nonzero_no_send"
    target.write_text(json.dumps(definition, ensure_ascii=False, indent=2), encoding="utf-8")
    for script, flag in ((MIX_INSTALLER_NAME, "-Plan"), ("SHOW_MIX_WEEKDAY_0800_TASK.ps1", "-VerifyOnly")):
        rejected = run(script, flag)
        assert rejected.returncode != 0, (script, legacy_field)
        assert "publication contract is invalid" in (rejected.stderr + rejected.stdout), (script, rejected.stderr)


def test_mix_pointer_is_consistent_across_definition_scripts_and_sender() -> None:
    """Definition, installer -Verify/-Plan, read-only SHOW and the sender name one pointer."""
    publication = json.loads(MIX_SCHEDULE_DEFINITION.read_text(encoding="utf-8"))["publication_contract"]
    sender_source = (SUITE_DIR / "mix_report_sender.py").read_text(encoding="utf-8")
    assert 'MIX_POINTER_PATH = MIX_REPORTS_ROOT / "LATEST_C0_RUN.json"' in sender_source
    assert 'MIX_POINTER_SCHEMA = "' + publication["required_pointer_schema"] + '"' in sender_source
    assert 'MIX_STATUS = "' + publication["required_status"] + '"' in sender_source
    for name in (MIX_INSTALLER_NAME, "SHOW_MIX_WEEKDAY_0800_TASK.ps1"):
        script = (SUITE_DIR / name).read_text(encoding="utf-8")
        assert "'" + publication["source"] + "'" in script, name
        assert "-cne '" + publication["required_pointer_schema"] + "'" in script, name
        assert "-cne '" + publication["required_status"] + "'" in script, name
        for legacy in ("LATEST_PRODUCTION_RUN", "mix_latest_pointer_v1", "final-before-open"):
            assert legacy not in script, (name, legacy)
    readme = (SUITE_DIR / "README.md").read_text(encoding="utf-8")
    assert "central source pointer：`" + publication["source"] + "`" in readme


def test_mix_weekday_0800_definition_is_mixed_only() -> None:
    contract = json.loads(MIX_SCHEDULE_DEFINITION.read_text(encoding="utf-8"))
    assert isinstance(contract, dict)
    _assert_mix_schedule_definition(contract)
    runner = (SUITE_DIR / MIX_RUNNER_NAME).read_text(encoding="utf-8")
    assert all(token not in runner for token in FORBIDDEN_0800_TOKENS)


def test_retired_dstock_0800_installer_cannot_register_or_redirect_a_task() -> None:
    # Current-only cleanup removed the fail-closed D-stock 08:00 shim entirely (it is no longer
    # shipped).  The guarantee is unchanged: the only time-triggered (08:00) task registration in
    # the suite is the canonical MIX installer, and no file routes to the retired D-stock shim.
    for retired in ("INSTALL_WEEKDAY_0800_TASK.ps1", "INSTALL_WEEKDAY_0800_TASK.cmd", "RUN_SCHEDULED_SEND.cmd"):
        assert not (SUITE_DIR / retired).exists(), retired
    scripts = [
        path for path in SUITE_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in {".ps1", ".cmd", ".bat", ".py"}
    ]
    sources = {path.name: path.read_text(encoding="utf-8", errors="replace") for path in scripts}
    registering = sorted(name for name, source in sources.items() if "Register-ScheduledTask" in source)
    assert registering == ["INSTALL_COMMAND_BOT_TASK.ps1", MIX_INSTALLER_NAME]
    # The command-bot task is the logon listener fallback, never a report-sending schedule.
    logon = sources["INSTALL_COMMAND_BOT_TASK.ps1"]
    assert "New-ScheduledTaskTrigger -AtLogOn" in logon
    assert all(token not in logon for token in ("-Daily", "-Weekly", "08:00", "RUN_SEND", "mix_report_sender"))
    assert "Start-ScheduledTask" not in sources[MIX_INSTALLER_NAME]
    for name, source in sources.items():
        assert "INSTALL_WEEKDAY_0800_TASK" not in source, name
    # The MIX installer still refuses the retired D-stock tokens in an action it reads back.
    assert "RUN_SCHEDULED_SEND" in sources[MIX_INSTALLER_NAME]


def test_mix_0800_installer_is_explicit_plan_verify_register_and_fail_closed() -> None:
    installer = (SUITE_DIR / MIX_INSTALLER_NAME).read_text(encoding="utf-8")
    wrapper = (SUITE_DIR / "INSTALL_MIX_WEEKDAY_0800_TASK.cmd").read_text(encoding="utf-8")
    for required in (
        "[switch]$Plan",
        "[switch]$Register",
        "[switch]$ConfirmRegister",
        "[switch]$Verify",
        "MIX Telegram Report 08-00",
        "MIX_WEEKDAY_0800_TASK_DEFINITION.json",
        "set NO_PAUSE=1&&call",
        "RUN_SEND_MIX_REPORT.cmd",
        "-TaskPath $taskPath",
        "ExecutionTimeLimit ([TimeSpan]::Zero)",
        "MultipleInstances IgnoreNew",
        "already_present",
    ):
        assert required in installer
    assert "Register-ScheduledTask" in installer
    assert "Start-ScheduledTask" not in installer
    assert "-Force" not in installer
    assert "expectedArguments = '/d /c \"set NO_PAUSE=1&&call D:\\stock\\programs\\telegram_report_delivery\\RUN_SEND_MIX_REPORT.cmd --max-wait-seconds 0 --poll-seconds 60\"'" in installer
    assert "INSTALL_MIX_WEEKDAY_0800_TASK.ps1" in wrapper


def test_readiness_script_exposes_explicit_mixed_only_task_settings() -> None:
    checker = (SUITE_DIR / "SHOW_MIX_WEEKDAY_0800_TASK.ps1").read_text(encoding="utf-8")
    assert "MIX Telegram Report 08-00" in checker
    assert "execution_time_limit_seconds" in checker
    assert "allow_start_if_on_batteries" in checker
    assert "dont_stop_if_going_on_batteries" in checker
    assert "dont_stop_on_idle_end" in checker
    assert "RUN_SEND_MIX_REPORT.cmd" in checker
    assert "--max-wait-seconds 0" in checker
    assert "--poll-seconds 60" in checker
    assert all(token not in checker for token in FORBIDDEN_0800_TOKENS)


def _assert_task_readback(readback: dict[str, object]) -> None:
    assert readback["TaskName"] == MIX_TASK_NAME
    assert str(readback["Execute"]).casefold().endswith("\\cmd.exe")
    assert "NO_PAUSE=1" in str(readback["Arguments"])
    assert MIX_RUNNER_NAME in str(readback["Arguments"])
    assert all(token not in str(readback["Arguments"]) for token in FORBIDDEN_0800_TOKENS)
    assert readback["WorkingDirectory"] == str(FORMAL_SUITE_DIR)
    assert "T08:00" in str(readback["StartBoundary"])
    assert readback["DaysOfWeek"] == 62


REGISTERED_TASK_READBACK = {
    "TaskName": MIX_TASK_NAME,
    "Execute": r"C:\Windows\System32\cmd.exe",
    "Arguments": (
        r'/d /c "set NO_PAUSE=1&&call D:\stock\programs\telegram_report_delivery\RUN_SEND_MIX_REPORT.cmd'
        r' --max-wait-seconds 0 --poll-seconds 60"'
    ),
    "WorkingDirectory": str(FORMAL_SUITE_DIR),
    "StartBoundary": "2026-10-05T08:00:00",
    "DaysOfWeek": 62,
}


def test_task_readback_assertions_accept_the_registered_definition() -> None:
    _assert_task_readback(dict(REGISTERED_TASK_READBACK))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("TaskName", "Another Task"),
        ("Execute", r"C:\Python312\python.exe"),
        ("Arguments", r'/d /c "call D:\stock\programs\telegram_report_delivery\RUN_SEND_MIX_REPORT.cmd"'),
        ("Arguments", r'/d /c "set NO_PAUSE=1&&call D:\elsewhere\other.cmd"'),
        ("Arguments", r'/d /c "set NO_PAUSE=1&&call RUN_SEND_MIX_REPORT.cmd RUN_SCHEDULED_SEND"'),
        ("WorkingDirectory", r"D:\elsewhere"),
        ("StartBoundary", "2026-10-05T09:30:00"),
        ("DaysOfWeek", 127),
    ],
)
def test_task_readback_assertions_reject_drift(field: str, value: object) -> None:
    with pytest.raises(AssertionError):
        _assert_task_readback({**REGISTERED_TASK_READBACK, field: value})


@pytest.mark.skipif(os.name != "nt", reason="Windows Task Scheduler is not available")
def test_registered_weekday_0800_task_readback() -> None:
    command = r'''
$task = Get-ScheduledTask -TaskName "MIX Telegram Report 08-00" -ErrorAction SilentlyContinue
if ($null -eq $task) { exit 42 }
$trigger = $task.Triggers | Select-Object -First 1
[pscustomobject]@{
    TaskName = [string]$task.TaskName
    Execute = [string]$task.Actions[0].Execute
    Arguments = [string]$task.Actions[0].Arguments
    WorkingDirectory = [string]$task.Actions[0].WorkingDirectory
    StartBoundary = [string]$trigger.StartBoundary
    DaysOfWeek = [int]$trigger.DaysOfWeek
} | ConvertTo-Json -Compress
'''
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 42:
        pytest.skip("MIX 08:00 task is not registered; registration is an explicit external operation")
    assert result.returncode == 0, result.stderr or result.stdout
    _assert_task_readback(json.loads(result.stdout))
