from __future__ import annotations

import hashlib
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


PROJECT = Path(__file__).resolve().parents[1]
INSTALLER = PROJECT / "tools" / "INSTALL_SELECTED_CACHE_TASK.ps1"
LAUNCHER = INSTALLER.with_suffix(".cmd")
TASK = "DStockSingleSelectedCachePreparation"
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
POWERSHELL = Path(r"C:\Users\USER\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe")


def _quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run_ps(body: str) -> dict:
    if os.name != "nt" or not POWERSHELL.is_file():
        pytest.skip("The approved existing PowerShell 7 host is required")
    # Load function ASTs only, never the production entrypoint or an installer mode.
    prefix = r"""
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(__INSTALLER__, [ref]$tokens, [ref]$parseErrors)
if (@($parseErrors).Count -ne 0) { throw ($parseErrors | Out-String) }
foreach ($statement in $ast.EndBlock.Statements) {
    if ($statement -is [System.Management.Automation.Language.FunctionDefinitionAst]) {
        Invoke-Expression $statement.Extent.Text
    }
}
$contract = Get-SelectedCacheTaskContract
""".replace("__INSTALLER__", _quote(INSTALLER))
    result = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command", prefix + body],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=40, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


MOCK_IO = r"""
$script:events = New-Object 'System.Collections.Generic.List[string]'
$script:valid = Get-DesiredTaskDefinition -Contract $contract
$script:currentXml = $script:valid
$script:currentDefinitionXml = $null
$script:currentLogonType = 5
$script:currentSecurity = 'O:SYG:SYD:(A;;FA;;;SY)'
$script:admin = $true
$script:readCount = 0
$script:registered = 0
$script:oldXml = $null
$script:driftOnThirdRead = $false
$script:nativeFails = $false
$script:registeredLogonType = 5
function Assert-InstallerInputs {
    param($Contract, $InvokedPath, [switch]$RestartRequested, $ExpectedSha256)
    $proof = [ordered]@{}
    $proof[$Contract.Installer] = ('a' * 64)
    return $proof
}
function Assert-InputProofUnchanged { param($Proof); [void]$script:events.Add('proof') }
function Open-TaskSchedulerRoot { return [pscustomobject]@{ Fake = $true } }
function Test-InstallerAdministrator { return $script:admin }
function Get-SelectedTaskSnapshot {
    param($Folder, $Contract, [switch]$IncludeSecurity)
    $script:readCount++
    if ($null -eq $script:currentXml) { return $null }
    $xml = $script:currentXml
    if ($script:driftOnThirdRead -and $script:readCount -eq 3) { $xml += ' ' }
    $normalized = if ($null -eq $script:currentDefinitionXml) { $script:currentXml } else { $script:currentDefinitionXml }
    return [pscustomobject]@{ Xml = $xml; DefinitionXml = $normalized; PrincipalLogonType = $script:currentLogonType
        SecurityDescriptor = $script:currentSecurity; Path = ($Contract.TaskPath + $Contract.TaskName) }
}
function Invoke-NativeTaskSchemaPreflight {
    param($Xml, $Contract)
    [void]$script:events.Add('native_schema')
    if ($script:nativeFails) { throw 'native_task_schema_rejected' }
    return [pscustomobject]@{ SchemaValidated = $true; PrincipalLogonType = 5 }
}
function Enter-SelectedCacheInstallerLock { [void]$script:events.Add('lock'); return [pscustomobject]@{ Fake = $true } }
function Exit-SelectedCacheInstallerLock { param($Mutex); [void]$script:events.Add('unlock') }
function Save-TaskRollbackReceipt {
    param($Contract, $Previous, $DesiredXml, $InputProof)
    [void]$script:events.Add('receipt')
    $script:oldXml = if ($null -eq $Previous) { $null } else { $Previous.Xml }
    return 'D:\unused-test-receipt.rollback.json'
}
function Register-SelectedTaskDefinition {
    param($Folder, $Contract, $Xml, [switch]$Updating)
    [void]$script:events.Add('register')
    $script:registered++
    $script:currentXml = $Xml
    $script:currentDefinitionXml = $null
    $script:currentLogonType = $script:registeredLogonType
}
function Write-ExclusiveUtf8File { param($Path, $Text); [void]$script:events.Add('completion_receipt') }
function Save-InstallerOperationReceipt {
    param($Contract, $Operation, $InputProof, $Snapshot, $Result, $Failure, $RollbackReceipt,
        $Administrator, $RegistrationVerified, [switch]$ParentError)
    [void]$script:events.Add('result_receipt')
    $script:lastOperationReceipt = [pscustomobject]@{ Result = $Result; Failure = $Failure; Snapshot = $Snapshot
        RegistrationVerified = $RegistrationVerified; Administrator = $Administrator; ParentError = [bool]$ParentError }
    return 'D:\unused-test-operation.result.json'
}
function Invoke-ConfirmedTelegramRestart { param($Contract); [void]$script:events.Add('restart'); return 'mock_restart_success' }
function Invoke-ElevatedSelectedCacheInstall {
    param($Contract, $InstallerSha256, $Operation, $InputProof, [switch]$RestartRequested)
    [void]$script:events.Add('uac')
    $script:currentXml = $script:valid
    $script:forwardedRestart = [bool]$RestartRequested
    return [pscustomobject]@{ result = [pscustomobject]@{
        DesiredEnablement = (Get-TaskEnablement -Xml $script:valid -Contract $contract)
        Status = 'already_matches'; Changed = $false; TelegramRestartResult = 'mock_child_restart' } }
}
"""


# Captured task.Xml from the first installation. Scheduler omitted eight defaults;
# tests obtain their values from the native COM definition, never Python defaults.
OBSERVED_REGISTERED_XML = r"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Version>1.0</Version><Source>dstock-single-selected-cache-task-installer-v1</Source>
    <Author>D-stock single-stock selected cache preparation</Author>
    <Description>Independent offline single-stock selected cache preparation only.</Description>
    <URI>\DStockSingleSelectedCachePreparation</URI>
  </RegistrationInfo>
  <Principals><Principal id="SelectedCachePrincipal"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings>
    <AllowHardTerminate>false</AllowHardTerminate><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Hidden>true</Hidden><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><StartWhenAvailable>true</StartWhenAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
  </Settings>
  <Triggers>
    <BootTrigger id="SelectedCacheBoot"><ExecutionTimeLimit>PT0S</ExecutionTimeLimit></BootTrigger>
    <TimeTrigger id="SelectedCacheEveryFiveMinutes"><StartBoundary>2026-01-01T00:00:00+08:00</StartBoundary>
      <ExecutionTimeLimit>PT0S</ExecutionTimeLimit><Repetition><Interval>PT5M</Interval></Repetition>
    </TimeTrigger>
  </Triggers>
  <Actions Context="SelectedCachePrincipal"><Exec><Command>D:\stock\.venv\Scripts\pythonw.exe</Command>
    <Arguments>-B "D:\stock\programs\shared\stock_daily_selected_cache_worker.py" --once</Arguments><WorkingDirectory>D:\stock</WorkingDirectory>
  </Exec></Actions>
</Task>"""


def _observed_snapshot_ps() -> str:
    return r"""
$script:raw = __RAW__
$scheduler = New-Object -ComObject 'Schedule.Service'
$scheduler.Connect()
$definition = $scheduler.NewTask(0)
$definition.XmlText = $script:raw
$definition.Principal.LogonType = 5
$script:task = [pscustomobject]@{ Path = ($contract.TaskPath + $contract.TaskName); Xml = $script:raw; Definition = $definition }
$folder = New-Object PSObject
$folder | Add-Member ScriptMethod GetTask { param($name); if ($name -cne 'DStockSingleSelectedCachePreparation') { throw 'unexpected task' }; return $script:task }
$snapshot = Get-SelectedTaskSnapshot -Folder $folder -Contract $contract
""".replace("__RAW__", _quote(OBSERVED_REGISTERED_XML))


RECEIPT_SETUP = r"""
$contract.ReceiptRoot = __ROOT__
$proof = [ordered]@{}
$proof[$contract.Installer] = ('a' * 64)
$operation = New-InstallerOperation -Id ('1' * 32) -Token ('b' * 64) -InstallerSha256 ('a' * 64)
$xml = Get-DesiredTaskDefinition -Contract $contract
$snapshot = [pscustomobject]@{ Xml = $xml; DefinitionXml = $xml; PrincipalLogonType = 5
    Path = ($contract.TaskPath + $contract.TaskName); SecurityDescriptor = 'O:SYG:SYD:(A;;FA;;;SY)' }
$result = [pscustomobject]@{ Mode = 'install'; Status = 'already_matches'; Verified = $true; Changed = $false
    DesiredEnablement = (Get-TaskEnablement -Xml $xml -Contract $contract)
    RestartTelegramRequested = $false; TelegramRestartResult = $null }
$receiptPath = Save-InstallerOperationReceipt -Contract $contract -Operation $operation -InputProof $proof -Snapshot $snapshot `
    -Result $result -Failure $null -Administrator $true -RegistrationVerified $true
"""


def test_source_has_fixed_action_and_no_xml_serviceaccount_or_task_start():
    source = INSTALLER.read_text(encoding="utf-8")
    launcher = LAUNCHER.read_text(encoding="utf-8")
    assert r"D:\stock\.venv\Scripts\pythonw.exe" in source
    assert '-B "D:\stock\programs\shared\stock_daily_selected_cache_worker.py" --once' in source
    assert "<LogonType>ServiceAccount</LogonType>" not in source
    assert "'Task/Principals/Principal/LogonType' =" not in source
    assert "Definition.Principal.LogonType" in source
    assert "DefinitionXml = $definitionXml" in source
    assert "-Xml $Snapshot.DefinitionXml" in source
    assert "'-OperationId', $Operation.Id, '-OperationToken', $Operation.Token" in source
    assert "-Verb RunAs -WindowStyle Hidden" in source
    assert "-Restart -ConfirmRestart -TimeoutSeconds 180" in source
    assert "-ExpectedInstallerSha256" in source
    assert "CreateNew" in source and "UTF8Encoding($false)" in source
    assert r'-File "D:\stock\programs\single_stock_analysis\tools\INSTALL_SELECTED_CACHE_TASK.ps1"' in launcher
    assert "setlocal EnableExtensions DisableDelayedExpansion" in launcher
    assert 'set "RC=%ERRORLEVEL%"' in launcher and "exit /b %RC%" in launcher
    for forbidden in ("Start-ScheduledTask", "Stop-ScheduledTask", "Unregister-ScheduledTask", "SetSecurityDescriptor", "Set-Acl", "sc.exe"):
        assert forbidden not in source


def test_definition_has_only_system_boot_and_five_minute_offline_action():
    result = _run_ps("[pscustomobject]@{ Xml = (Get-DesiredTaskDefinition -Contract $contract) } | ConvertTo-Json -Compress")
    root = ET.fromstring(result["Xml"])
    assert root.findtext("t:Principals/t:Principal/t:UserId", namespaces=NS) == "S-1-5-18"
    assert root.find("t:Principals/t:Principal/t:LogonType", NS) is None
    assert root.findtext("t:Actions/t:Exec/t:Command", namespaces=NS) == r"D:\stock\.venv\Scripts\pythonw.exe"
    assert root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=NS) == '-B "D:\stock\programs\shared\stock_daily_selected_cache_worker.py" --once'
    triggers = list(root.find("t:Triggers", NS))
    assert {node.tag.rsplit("}", 1)[-1] for node in triggers} == {"BootTrigger", "TimeTrigger"}
    assert len(triggers) == 2
    assert root.findtext("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval", namespaces=NS) == "PT5M"
    assert root.find("t:Triggers/t:TimeTrigger/t:Repetition/t:Duration", NS) is None
    assert root.findtext("t:Settings/t:MultipleInstancesPolicy", namespaces=NS) == "IgnoreNew"
    assert root.findtext("t:Settings/t:Hidden", namespaces=NS) == "true"
    assert root.findtext("t:Settings/t:ExecutionTimeLimit", namespaces=NS) == "PT0S"
    assert root.findtext("t:Settings/t:AllowHardTerminate", namespaces=NS) == "false"
    assert all(node.findtext("t:ExecutionTimeLimit", namespaces=NS) == "PT0S" for node in triggers)


def test_real_windows_com_schema_preflight_is_read_only_and_accepts_system_definition():
    # This is the native schema gate, not a mocked XML parser. NewTask(0) is an
    # unregistered in-memory definition; no RegisterTask/ValidateOnly call occurs.
    result = _run_ps(r"""
$xml = Get-DesiredTaskDefinition -Contract $contract
$proof = Invoke-NativeTaskSchemaPreflight -Xml $xml -Contract $contract
[pscustomobject]@{ Valid = $proof.SchemaValidated; LogonType = $proof.PrincipalLogonType } | ConvertTo-Json -Compress
""")
    assert result == {"Valid": True, "LogonType": 5}


def test_real_windows_com_schema_rejects_invalid_xml_logon_enum_without_registering():
    result = _run_ps(r"""
$xml = Get-DesiredTaskDefinition -Contract $contract
$xml = $xml.Replace('<UserId>S-1-5-18</UserId>', '<UserId>S-1-5-18</UserId><LogonType>ServiceAccount</LogonType>')
try { [void](Invoke-NativeTaskSchemaPreflight -Xml $xml -Contract $contract); $rejected = $false }
catch { $rejected = $true }
[pscustomobject]@{ Rejected = $rejected } | ConvertTo-Json -Compress
""")
    assert result["Rejected"] is True


def test_observed_registered_xml_defaults_use_same_task_native_definition_for_validation():
    result = _run_ps(_observed_snapshot_ps() + r"""
Assert-OwnedTaskSnapshot -Snapshot $snapshot -Contract $contract -Expected
[pscustomobject]@{ Raw = $snapshot.Xml; Normalized = $snapshot.DefinitionXml; Logon = $snapshot.PrincipalLogonType } | ConvertTo-Json -Compress
""")
    assert result["Raw"] == OBSERVED_REGISTERED_XML
    assert result["Logon"] == 5
    raw, normalized = ET.fromstring(result["Raw"]), ET.fromstring(result["Normalized"])
    omitted = {
        "t:Triggers/t:BootTrigger/t:Enabled": "true",
        "t:Triggers/t:TimeTrigger/t:Enabled": "true",
        "t:Triggers/t:TimeTrigger/t:Repetition/t:StopAtDurationEnd": "false",
        "t:Settings/t:RunOnlyIfNetworkAvailable": "false",
        "t:Settings/t:AllowStartOnDemand": "true",
        "t:Settings/t:Enabled": "true",
        "t:Settings/t:RunOnlyIfIdle": "false",
        "t:Settings/t:WakeToRun": "false",
    }
    for path, value in omitted.items():
        assert raw.find(path, NS) is None
        assert normalized.findtext(path, namespaces=NS) == value


def test_observed_existing_task_is_idempotent_without_registration_or_raw_rewrite():
    result = _run_ps(_observed_snapshot_ps() + r"""
$observedSnapshot = $snapshot
""" + MOCK_IO + r"""
$script:currentXml = $observedSnapshot.Xml
$script:currentDefinitionXml = $observedSnapshot.DefinitionXml
$result = Invoke-SelectedCacheTaskInstaller -InvokedPath $contract.Installer -Install -ConfirmInstall
[pscustomobject]@{ Result = $result; Registered = $script:registered; Raw = $script:currentXml
    Readback = $script:lastOperationReceipt.Snapshot; Events = $script:events.ToArray() } | ConvertTo-Json -Depth 10 -Compress
""")
    assert result["Result"]["Status"] == "already_matches"
    assert result["Result"]["Changed"] is False and result["Registered"] == 0
    assert result["Raw"] == result["Readback"]["Xml"] == OBSERVED_REGISTERED_XML
    assert not {"receipt", "register", "completion_receipt", "restart"}.intersection(result["Events"])
    assert result["Events"].count("result_receipt") == 1


def test_snapshot_without_com_normalized_definition_does_not_guess_missing_defaults():
    result = _run_ps(r"""
$snapshot = [pscustomobject]@{ Xml = (Get-DesiredTaskDefinition $contract); PrincipalLogonType = 5 }
try { Assert-OwnedTaskSnapshot -Snapshot $snapshot -Contract $contract -Expected; $rejected = $false }
catch { $rejected = $true }
[pscustomobject]@{ Rejected = $rejected } | ConvertTo-Json -Compress
""")
    assert result["Rejected"] is True


@pytest.mark.parametrize("mutation", [
    "Set-TaskXmlValue $doc 'Task/RegistrationInfo/Source' 'foreign-owner' $contract",
    "Set-TaskXmlValue $doc 'Task/Actions/Exec/Arguments' '--foreign-action' $contract",
    "Set-TaskXmlValue $doc 'Task/Principals/Principal/UserId' 'S-1-5-19' $contract",
    "Set-TaskXmlValue $doc 'Task/Triggers/TimeTrigger/Repetition/Interval' 'PT1M' $contract",
])
def test_normalized_definition_does_not_relax_owner_action_system_or_trigger_identity(mutation):
    result = _run_ps(r"""
$raw = Get-DesiredTaskDefinition $contract
$doc = ConvertTo-TaskXmlDocument $raw
__MUTATION__
$snapshot = [pscustomobject]@{ Xml = $raw; DefinitionXml = $doc.OuterXml; PrincipalLogonType = 5 }
try { Assert-OwnedTaskSnapshot -Snapshot $snapshot -Contract $contract -Expected; $rejected = $false }
catch { $rejected = $true }
[pscustomobject]@{ Rejected = $rejected; RawUnchanged = ($snapshot.Xml -ceq $raw) } | ConvertTo-Json -Compress
""".replace("__MUTATION__", mutation))
    assert result == {"Rejected": True, "RawUnchanged": True}


@pytest.mark.parametrize("flags", [
    "-Install", "-ConfirmInstall", "-VerifyOnly -Install -ConfirmInstall", "-RestartTelegram",
    "-Install -ConfirmInstall -RestartTelegram", "-ConfirmRestart", "-ExpectedInstallerSha256 ('a' * 64)",
])
def test_mutation_flags_require_complete_explicit_authorization_before_io(flags):
    result = _run_ps(r"""
$script:io = 0
function Assert-InstallerInputs { $script:io++; throw 'unexpected_io' }
function Save-InstallerOperationReceipt { return 'D:\unused-test-authorization-error.json' }
try { [void](Invoke-SelectedCacheTaskInstaller -InvokedPath $contract.Installer __FLAGS__); $rejected = $false }
catch { $rejected = $true }
[pscustomobject]@{ Rejected = $rejected; Io = $script:io } | ConvertTo-Json -Compress
""".replace("__FLAGS__", flags))
    assert result == {"Rejected": True, "Io": 0}


@pytest.mark.parametrize("present", [False, True])
def test_default_verify_only_never_registers_elevates_restarts_or_writes_receipts(present):
    result = _run_ps(MOCK_IO + ("" if present else "$script:currentXml = $null\n") + r"""
$result = Invoke-SelectedCacheTaskInstaller -InvokedPath $contract.Installer
[pscustomobject]@{ Result = $result; Events = @($script:events); Registered = $script:registered } | ConvertTo-Json -Depth 8 -Compress
""")
    assert result["Result"]["Mode"] == "verify_only"
    assert result["Result"]["Verified"] is present
    assert result["Registered"] == 0
    assert result["Events"] == ["native_schema"]


@pytest.mark.parametrize("mutation", [
    "Set-TaskXmlValue $doc 'Task/RegistrationInfo/Source' 'other-tool' $contract",
    "Set-TaskXmlValue $doc 'Task/RegistrationInfo/Author' 'other-owner' $contract",
    "Set-TaskXmlValue $doc 'Task/Principals/Principal/UserId' 'S-1-5-19' $contract",
    "Set-TaskXmlValue $doc 'Task/Actions/Exec/Command' 'C:\\Windows\\System32\\cmd.exe' $contract",
    "Set-TaskXmlValue $doc 'Task/Actions/Exec/Arguments' '--other-worker' $contract",
    "Set-TaskXmlValue $doc 'Task/Triggers/TimeTrigger/Repetition/Interval' 'PT1M' $contract",
    "Set-TaskXmlValue $doc 'Task/Triggers/TimeTrigger/Repetition/Duration' 'PT1H' $contract",
    "$node = Get-TaskXmlNode $doc 'Task/Actions' $contract; [void]$node.AppendChild($node.FirstChild.CloneNode($true))",
    "$node = Get-TaskXmlNode $doc 'Task/Triggers' $contract; [void]$node.AppendChild($doc.CreateElement('RegistrationTrigger', $contract.Namespace))",
])
def test_same_name_foreign_action_owner_or_definition_stops_before_registration_or_rollback(mutation):
    result = _run_ps(MOCK_IO + r"""
$doc = ConvertTo-TaskXmlDocument -Xml $script:valid
__MUTATION__
$script:currentXml = $doc.OuterXml
try { [void](Invoke-SelectedCacheTaskInstaller -InvokedPath $contract.Installer -Install -ConfirmInstall); $rejected = $false }
catch { $rejected = $true }
[pscustomobject]@{ Rejected = $rejected; Events = @($script:events); Registered = $script:registered } | ConvertTo-Json -Compress
""".replace("__MUTATION__", mutation))
    assert result == {"Rejected": True, "Events": ["result_receipt"], "Registered": 0}


def test_native_schema_failure_precedes_any_uac_even_for_a_new_task():
    result = _run_ps(MOCK_IO + r"""
$script:currentXml = $null
$script:admin = $false
$script:nativeFails = $true
try { [void](Invoke-SelectedCacheTaskInstaller -InvokedPath $contract.Installer -Install -ConfirmInstall); $rejected = $false }
catch { $rejected = $true }
[pscustomobject]@{ Rejected = $rejected; Events = @($script:events) } | ConvertTo-Json -Compress
""")
    assert result == {"Rejected": True, "Events": ["native_schema", "result_receipt"]}


@pytest.mark.parametrize("logon_type", [0, 2, 3])
def test_existing_task_requires_real_com_service_account_readback(logon_type):
    result = _run_ps(MOCK_IO + f"$script:currentLogonType = {logon_type}\n" + r"""
try { [void](Invoke-SelectedCacheTaskInstaller -InvokedPath $contract.Installer -Install -ConfirmInstall); $rejected = $false }
catch { $rejected = $true }
[pscustomobject]@{ Rejected = $rejected; Events = @($script:events) } | ConvertTo-Json -Compress
""")
    assert result == {"Rejected": True, "Events": ["result_receipt"]}


def test_owned_update_preserves_other_settings_and_original_xml_before_register_then_restart():
    result = _run_ps(MOCK_IO + r"""
$doc = ConvertTo-TaskXmlDocument -Xml $script:valid
Set-TaskXmlValue $doc 'Task/Settings/Hidden' 'false' $contract
Set-TaskXmlValue $doc 'Task/Settings/Priority' '6' $contract
Set-TaskXmlValue $doc 'Task/Settings/RestartOnFailure/Interval' 'PT10M' $contract
Set-TaskXmlValue $doc 'Task/Settings/RestartOnFailure/Count' '2' $contract
Set-TaskXmlValue $doc 'Task/RegistrationInfo/Description' 'preserve-user-description' $contract
$script:currentXml = $doc.OuterXml
$original = $script:currentXml
$result = Invoke-SelectedCacheTaskInstaller -InvokedPath $contract.Installer -Install -ConfirmInstall -RestartTelegram -ConfirmRestart
[pscustomobject]@{ Result = $result; Events = @($script:events); Xml = $script:currentXml; BackupExact = ($script:oldXml -ceq $original) } | ConvertTo-Json -Depth 8 -Compress
""")
    assert result["Result"]["Status"] == "updated"
    assert result["BackupExact"] is True
    events = result["Events"]
    assert events.index("receipt") < events.index("register") < events.index("completion_receipt") < events.index("restart")
    root = ET.fromstring(result["Xml"])
    assert root.findtext("t:Settings/t:Priority", namespaces=NS) == "6"
    assert root.findtext("t:Settings/t:RestartOnFailure/t:Count", namespaces=NS) == "2"
    assert root.findtext("t:RegistrationInfo/t:Description", namespaces=NS) == "preserve-user-description"


def test_already_matching_own_task_is_noop_with_result_receipt_but_no_rollback_or_restart():
    result = _run_ps(MOCK_IO + r"""
$result = Invoke-SelectedCacheTaskInstaller -InvokedPath $contract.Installer -Install -ConfirmInstall
[pscustomobject]@{ Result = $result; Events = @($script:events) } | ConvertTo-Json -Depth 8 -Compress
""")
    assert result["Result"]["Status"] == "already_matches"
    assert result["Result"]["Changed"] is False
    assert not {"receipt", "register", "restart", "completion_receipt"}.intersection(result["Events"])
    assert result["Events"].count("result_receipt") == 1


def test_definition_race_preserves_receipt_but_does_not_register_or_restart():
    result = _run_ps(MOCK_IO + r"""
$doc = ConvertTo-TaskXmlDocument -Xml $script:valid
Set-TaskXmlValue $doc 'Task/Settings/Hidden' 'false' $contract
$script:currentXml = $doc.OuterXml
$script:driftOnThirdRead = $true
try { [void](Invoke-SelectedCacheTaskInstaller -InvokedPath $contract.Installer -Install -ConfirmInstall -RestartTelegram -ConfirmRestart); $rejected = $false }
catch { $rejected = $true; $message = $_.Exception.Message }
[pscustomobject]@{ Rejected = $rejected; Events = @($script:events); Message = $message } | ConvertTo-Json -Compress
""")
    assert result["Rejected"] is True
    assert "receipt" in result["Events"]
    assert "register" not in result["Events"] and "restart" not in result["Events"]
    assert "Rollback receipt" in result["Message"]


def test_uac_forwards_optional_restart_once_without_parent_second_restart():
    result = _run_ps(MOCK_IO + r"""
$script:currentXml = $null
$script:admin = $false
$result = Invoke-SelectedCacheTaskInstaller -InvokedPath $contract.Installer -Install -ConfirmInstall -RestartTelegram -ConfirmRestart
[pscustomobject]@{ Result = $result; Events = @($script:events); Forwarded = $script:forwardedRestart } | ConvertTo-Json -Depth 8 -Compress
""")
    assert result["Result"]["Status"] == "verified_after_uac"
    assert result["Forwarded"] is True
    assert result["Events"].count("uac") == 1 and "restart" not in result["Events"]
    assert result["Events"].index("native_schema") < result["Events"].index("uac")


def test_failed_principal_readback_after_registration_prevents_restart():
    result = _run_ps(MOCK_IO + r"""
$script:currentXml = $null
$script:registeredLogonType = 0
try { [void](Invoke-SelectedCacheTaskInstaller -InvokedPath $contract.Installer -Install -ConfirmInstall -RestartTelegram -ConfirmRestart); $rejected = $false }
catch { $rejected = $true }
[pscustomobject]@{ Rejected = $rejected; Events = @($script:events) } | ConvertTo-Json -Compress
""")
    assert result["Rejected"] is True
    assert "register" in result["Events"] and "restart" not in result["Events"]


def test_registration_is_create_only_or_update_without_acl_override():
    result = _run_ps(r"""
$script:calls = New-Object 'System.Collections.Generic.List[object]'
$folder = New-Object PSObject
$folder | Add-Member ScriptMethod RegisterTask {
    param($name, $xml, $flags, $user, $password, $logon, $sddl)
    [void]$script:calls.Add([pscustomobject]@{ Name = $name; Flags = $flags; User = $user; Logon = $logon; NullSddl = ($null -eq $sddl); NullPassword = ($null -eq $password) })
}
$xml = Get-DesiredTaskDefinition -Contract $contract
Register-SelectedTaskDefinition -Folder $folder -Contract $contract -Xml $xml
Register-SelectedTaskDefinition -Folder $folder -Contract $contract -Xml $xml -Updating
[pscustomobject]@{ Calls = $script:calls.ToArray() } | ConvertTo-Json -Depth 6 -Compress
""")
    assert [call["Flags"] for call in result["Calls"]] == [34, 52]
    assert all(call["Name"] == TASK and call["User"] == "SYSTEM" and call["Logon"] == 5 for call in result["Calls"])
    assert all(call["NullSddl"] and call["NullPassword"] for call in result["Calls"])


def test_rollback_receipt_preserves_exact_xml_security_and_utf8_backup(tmp_path):
    result = _run_ps(r"""
$contract.ReceiptRoot = __ROOT__
$xml = '<?xml version="1.0" encoding="UTF-16"?>' + (Get-DesiredTaskDefinition -Contract $contract)
$previous = [pscustomobject]@{ Xml = $xml; DefinitionXml = $xml; PrincipalLogonType = 5; SecurityDescriptor = 'O:SYG:SYD:(A;;FA;;;SY)' }
$receiptPath = Save-TaskRollbackReceipt -Contract $contract -Previous $previous -DesiredXml $xml -InputProof ([ordered]@{ installer = ('a' * 64) })
$receipt = [System.IO.File]::ReadAllText($receiptPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
[pscustomobject]@{ Path = $receiptPath; Original = $xml; BackupFile = $receipt.previous_xml_file } | ConvertTo-Json -Compress
""".replace("__ROOT__", _quote(tmp_path / "receipts")))
    raw = Path(result["Path"]).read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    receipt = json.loads(raw.decode("utf-8"))
    assert receipt["previous_xml"] == result["Original"]
    assert receipt["previous_xml_utf8_sha256"] == hashlib.sha256(result["Original"].encode("utf-8")).hexdigest()
    assert receipt["previous_security_descriptor"] == "O:SYG:SYD:(A;;FA;;;SY)"
    assert receipt["previous_principal_logon_type"] == receipt["registration_logon_type"] == 5
    backup = Path(result["BackupFile"]).read_bytes()
    assert not backup.startswith(b"\xef\xbb\xbf")
    assert b'encoding="utf-8"' in backup
    assert ET.fromstring(backup).findtext("t:Principals/t:Principal/t:UserId", namespaces=NS) == "S-1-5-18"


def test_operation_receipt_is_exclusive_utf8_and_preserves_verified_raw_and_normalized_readback(tmp_path):
    result = _run_ps(RECEIPT_SETUP.replace("__ROOT__", _quote(tmp_path / "receipts")) + r"""
$read = Read-VerifiedInstallerChildReceipt -Contract $contract -Operation $operation -InputProof $proof -ChildProcessId $PID -ChildExitCode 0
try {
    [void](Save-InstallerOperationReceipt -Contract $contract -Operation $operation -InputProof $proof -Snapshot $snapshot `
        -Result $result -Failure $null -Administrator $true -RegistrationVerified $true)
    $replayRejected = $false
} catch { $replayRejected = $true }
[pscustomobject]@{ Path = $receiptPath; Receipt = $read; ReplayRejected = $replayRejected } | ConvertTo-Json -Depth 20 -Compress
""")
    raw = Path(result["Path"]).read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    receipt = json.loads(raw.decode("utf-8"))
    assert result["ReplayRejected"] is True
    assert receipt["operation_id"] == "1" * 32 and receipt["operation_token"] == "b" * 64
    assert receipt["installer_sha256"] == "a" * 64
    assert receipt["success"] is True and receipt["registration_verified"] is True and receipt["error"] is None
    for field, xml in (("raw_xml_utf8_sha256", "Xml"), ("definition_xml_utf8_sha256", "DefinitionXml")):
        assert receipt[field] == hashlib.sha256(receipt["readback"][xml].encode("utf-8")).hexdigest()


def test_failure_receipt_is_durable_with_actual_readback_and_full_exception(tmp_path):
    result = _run_ps(RECEIPT_SETUP.replace("__ROOT__", _quote(tmp_path / "receipts")) + r"""
$operation = New-InstallerOperation -InstallerSha256 ('a' * 64)
try { throw (New-Object InvalidOperationException('actual_hidden_child_error', (New-Object UnauthorizedAccessException('inner_access_denied')))) }
catch { $failure = Get-InstallerErrorDetail -Record $_ }
$failurePath = Save-InstallerOperationReceipt -Contract $contract -Operation $operation -InputProof $proof -Snapshot $snapshot `
    -Result $null -Failure $failure -Administrator $true -RegistrationVerified $false
[pscustomobject]@{ Path = $failurePath } | ConvertTo-Json -Compress
""")
    raw = Path(result["Path"]).read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    receipt = json.loads(raw.decode("utf-8"))
    assert receipt["success"] is False and receipt["exit_code"] == 2
    assert receipt["result"] is None and receipt["registration_verified"] is False
    assert receipt["readback"]["PrincipalLogonType"] == 5
    error = receipt["error"]
    assert "actual_hidden_child_error" in error["message"]
    assert error["script_stack_trace"] and error["position"] and error["invocation"]
    assert any("inner_access_denied" in item["text"] for item in error["exception_chain"])


@pytest.mark.parametrize("mutation", [
    "$receipt.operation_id = ('2' * 32)",
    "$receipt.operation_token = ('c' * 64)",
    "$receipt.installer_sha256 = ('c' * 64)",
    "$receipt.process_id = -1",
    "$receipt.administrator = $false",
    "$receipt.restart_requested = $true",
    "$receipt.task_name = 'UnrelatedTask'",
    "$receipt.task_path = '\\Other\\'",
    "$receipt.source_sha256.($contract.Installer) = ('c' * 64)",
    "$receipt.raw_xml_utf8_sha256 = ('c' * 64)",
    "$receipt.definition_xml_utf8_sha256 = ('c' * 64)",
    "$receipt.readback.PrincipalLogonType = 0",
    "$receipt.readback.Path = '\\OtherTask'",
    "$receipt.registration_verified = $false",
    "$receipt.result.Verified = $false",
    "$receipt.result.Changed = $true",
    "$receipt.result.RestartTelegramRequested = 'False'",
    "$receipt.result.DesiredEnablement = $null",
    "$receipt.result.DesiredEnablement.TaskEnabled = 'false'",
    "$receipt.success = $false; $receipt.exit_code = 2; $receipt.error = [pscustomobject]@{ message = 'actual_child_failure' }",
])
def test_child_receipt_identity_source_readback_and_failure_mismatches_fail_closed(tmp_path, mutation):
    result = _run_ps(RECEIPT_SETUP.replace("__ROOT__", _quote(tmp_path / "receipts")) + r"""
$receipt = [IO.File]::ReadAllText($receiptPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
__MUTATION__
[IO.File]::WriteAllText($receiptPath, ($receipt | ConvertTo-Json -Depth 20), (New-Object Text.UTF8Encoding($false)))
try {
    [void](Read-VerifiedInstallerChildReceipt -Contract $contract -Operation $operation -InputProof $proof -ChildProcessId $PID -ChildExitCode 0)
    $rejected = $false
} catch { $rejected = $true; $message = $_.Exception.Message }
[pscustomobject]@{ Rejected = $rejected; Message = $message } | ConvertTo-Json -Compress
""".replace("__MUTATION__", mutation))
    assert result["Rejected"] is True
    if "actual_child_failure" in mutation:
        assert "actual_child_failure" in result["Message"]


@pytest.mark.parametrize("exit_code", ["2", "$null"])
def test_success_receipt_cannot_override_failed_or_missing_child_exit_code(tmp_path, exit_code):
    result = _run_ps(RECEIPT_SETUP.replace("__ROOT__", _quote(tmp_path / "receipts")) + r"""
try {
    [void](Read-VerifiedInstallerChildReceipt -Contract $contract -Operation $operation -InputProof $proof -ChildProcessId $PID -ChildExitCode __EXIT__)
    $rejected = $false
} catch { $rejected = $true }
[pscustomobject]@{ Rejected = $rejected } | ConvertTo-Json -Compress
""".replace("__EXIT__", exit_code))
    assert result["Rejected"] is True


def test_nonadmin_parent_never_reads_task_and_accepts_only_verified_child_receipt(tmp_path):
    result = _run_ps(r"""
$script:realSave = ${function:Save-InstallerOperationReceipt}
$script:realWrite = ${function:Write-ExclusiveUtf8File}
""" + MOCK_IO + r"""
Set-Item Function:\Write-ExclusiveUtf8File -Value $script:realWrite
$script:admin = $false
$script:testRoot = __ROOT__
function Open-TaskSchedulerRoot { throw 'nonadmin_GetTask_forbidden_0x80070005' }
function Get-SelectedTaskSnapshot { throw 'nonadmin_GetTask_forbidden_0x80070005' }
function Invoke-ElevatedSelectedCacheInstall {
    param($Contract, $InstallerSha256, $Operation, $InputProof, [switch]$RestartRequested)
    [void]$script:events.Add('uac')
    $Contract.ReceiptRoot = $script:testRoot
    $snapshot = [pscustomobject]@{ Xml = $script:valid; DefinitionXml = $script:valid; PrincipalLogonType = 5
        Path = ($Contract.TaskPath + $Contract.TaskName); SecurityDescriptor = $script:currentSecurity }
    $result = [pscustomobject]@{ Status = 'already_matches'; Verified = $true; Changed = $false
        DesiredEnablement = (Get-TaskEnablement -Xml $snapshot.DefinitionXml -Contract $Contract)
        RestartTelegramRequested = [bool]$RestartRequested; TelegramRestartResult = $null }
    [void](& $script:realSave -Contract $Contract -Operation $Operation -InputProof $InputProof -Snapshot $snapshot `
        -Result $result -Failure $null -Administrator $true -RegistrationVerified $true)
    return Read-VerifiedInstallerChildReceipt -Contract $Contract -Operation $Operation -InputProof $InputProof -ChildProcessId $PID -ChildExitCode 0
}
$result = Invoke-SelectedCacheTaskInstaller -InvokedPath $contract.Installer -Install -ConfirmInstall
[pscustomobject]@{ Result = $result; Reads = $script:readCount; Registered = $script:registered; Events = $script:events.ToArray() } | ConvertTo-Json -Depth 10 -Compress
""".replace("__ROOT__", _quote(tmp_path / "receipts")))
    assert result["Result"]["Verified"] is True
    assert result["Result"]["Status"] == "verified_after_uac"
    assert result["Result"]["ChildStatus"] == "already_matches"
    assert result["Result"]["DesiredEnablement"] == {
        "TaskEnabled": "true", "BootEnabled": "true", "IntervalEnabled": "true"}
    assert result["Reads"] == result["Registered"] == 0
    assert result["Events"].count("uac") == 1
    assert not {"receipt", "register", "restart"}.intersection(result["Events"])


def test_cancelled_uac_fails_closed_without_task_access_registration_or_restart(tmp_path):
    result = _run_ps(r"""
$contract.ReceiptRoot = __ROOT__
$proof = [ordered]@{ installer = ('a' * 64) }
$operation = New-InstallerOperation -InstallerSha256 ('a' * 64)
$script:launches = 0
function Start-Process {
    param($FilePath, $ArgumentList, $Verb, $WindowStyle, [switch]$Wait, [switch]$PassThru, $ErrorAction)
    $script:launches++
    throw (New-Object ComponentModel.Win32Exception(1223))
}
function Read-VerifiedInstallerChildReceipt { throw 'must_not_read_after_cancel' }
try {
    [void](Invoke-ElevatedSelectedCacheInstall -Contract $contract -InstallerSha256 ('a' * 64) -Operation $operation -InputProof $proof)
    $rejected = $false
} catch { $rejected = $true; $message = $_.Exception.Message }
[pscustomobject]@{ Rejected = $rejected; Launches = $script:launches; Message = $message } | ConvertTo-Json -Compress
""".replace("__ROOT__", _quote(tmp_path / "receipts")))
    assert result["Rejected"] is True and result["Launches"] == 1
    assert "UAC installation failed or was cancelled" in result["Message"]
    assert "must_not_read_after_cancel" not in result["Message"]


@pytest.mark.parametrize("parent", [False, True])
def test_installation_errors_preserve_full_durable_diagnostic_and_never_retry(parent):
    result = _run_ps(MOCK_IO + r"""
__MODE__
function Register-SelectedTaskDefinition { throw 'registration_child_actual_failure' }
function Invoke-ElevatedSelectedCacheInstall { throw 'uac_actual_failure_or_cancel' }
try {
    [void](Invoke-SelectedCacheTaskInstaller -InvokedPath $contract.Installer -Install -ConfirmInstall -RestartTelegram -ConfirmRestart)
    $rejected = $false
} catch { $rejected = $true }
[pscustomobject]@{ Rejected = $rejected; Events = $script:events.ToArray(); Saved = $script:lastOperationReceipt } | ConvertTo-Json -Depth 20 -Compress
""".replace("__MODE__", "$script:admin = $false" if parent else "$script:currentXml = $null"))
    assert result["Rejected"] is True
    assert result["Events"].count("result_receipt") == 1
    assert "restart" not in result["Events"]
    saved = result["Saved"]
    assert saved["Result"] is None and saved["RegistrationVerified"] is False
    assert saved["ParentError"] is parent
    expected = "uac_actual_failure_or_cancel" if parent else "registration_child_actual_failure"
    assert expected in saved["Failure"]["message"]
    assert saved["Failure"]["script_stack_trace"] and saved["Failure"]["exception_chain"]


def test_failed_task_read_is_never_treated_as_absent_or_created():
    result = _run_ps(MOCK_IO + r"""
function Get-SelectedTaskSnapshot { throw (New-Object UnauthorizedAccessException('actual_task_access_denied')) }
try { [void](Invoke-SelectedCacheTaskInstaller -InvokedPath $contract.Installer -Install -ConfirmInstall); $rejected = $false }
catch { $rejected = $true }
[pscustomobject]@{ Rejected = $rejected; Events = $script:events.ToArray(); Registered = $script:registered } | ConvertTo-Json -Compress
""")
    assert result == {"Rejected": True, "Events": ["result_receipt"], "Registered": 0}


def test_restart_failure_records_verified_existing_task_without_rewriting_or_claiming_success():
    result = _run_ps(MOCK_IO + r"""
function Invoke-ConfirmedTelegramRestart { throw 'actual_restart_failure_after_task_verified' }
try {
    [void](Invoke-SelectedCacheTaskInstaller -InvokedPath $contract.Installer -Install -ConfirmInstall -RestartTelegram -ConfirmRestart)
    $rejected = $false
} catch { $rejected = $true }
[pscustomobject]@{ Rejected = $rejected; Registered = $script:registered; Saved = $script:lastOperationReceipt } | ConvertTo-Json -Depth 20 -Compress
""")
    assert result["Rejected"] is True and result["Registered"] == 0
    saved = result["Saved"]
    assert saved["Result"] is None and saved["RegistrationVerified"] is True
    assert saved["Snapshot"]["PrincipalLogonType"] == 5
    assert "actual_restart_failure_after_task_verified" in saved["Failure"]["message"]


@pytest.mark.parametrize("enabled", [
    ("false", "true", "true"), ("true", "false", "true"),
    ("true", "true", "false"), ("false", "false", "false"), ("false", "true", "false"),
])
@pytest.mark.parametrize("mode", ["verify", "install", "repair"])
def test_disabled_settings_and_triggers_are_preserved_during_verify_install_and_repair(enabled, mode):
    mutations = "\n".join(
        f"Set-TaskXmlValue $doc '{path}' '{value}' $contract" for path, value in zip(
            ("Task/Settings/Enabled", "Task/Triggers/BootTrigger/Enabled", "Task/Triggers/TimeTrigger/Enabled"), enabled)
    )
    repair = "Set-TaskXmlValue $doc 'Task/Settings/Hidden' 'false' $contract" if mode == "repair" else ""
    flags = "-VerifyOnly" if mode == "verify" else "-Install -ConfirmInstall"
    result = _run_ps(MOCK_IO + r"""
$doc = ConvertTo-TaskXmlDocument -Xml $script:valid
__MUTATIONS__
__REPAIR__
$script:currentXml = $doc.OuterXml
$before = Get-TaskEnablement -Xml $script:currentXml -Contract $contract
$result = Invoke-SelectedCacheTaskInstaller -InvokedPath $contract.Installer __FLAGS__
$after = Get-TaskEnablement -Xml $script:currentXml -Contract $contract
[pscustomobject]@{ Result = $result; Before = $before; After = $after; Registered = $script:registered
    Events = $script:events.ToArray() } | ConvertTo-Json -Depth 10 -Compress
""".replace("__MUTATIONS__", mutations).replace("__REPAIR__", repair).replace("__FLAGS__", flags))
    assert tuple(result["After"][name] for name in ("TaskEnabled", "BootEnabled", "IntervalEnabled")) == enabled
    assert result["Before"] == result["After"]
    assert result["Registered"] == (1 if mode == "repair" else 0)
    assert not {"restart", "uac"}.intersection(result["Events"])


@pytest.mark.parametrize("path", ["Task/Settings/Enabled", "Task/Triggers/BootTrigger/Enabled", "Task/Triggers/TimeTrigger/Enabled"])
def test_final_readback_cannot_silently_enable_a_disabled_value(path):
    result = _run_ps(MOCK_IO + r"""
$doc = ConvertTo-TaskXmlDocument -Xml $script:valid
Set-TaskXmlValue $doc '__PATH__' 'false' $contract
Set-TaskXmlValue $doc 'Task/Settings/Hidden' 'false' $contract
$script:currentXml = $doc.OuterXml
function Register-SelectedTaskDefinition {
    param($Folder, $Contract, $Xml, [switch]$Updating)
    $script:registered++
    $changed = ConvertTo-TaskXmlDocument -Xml $Xml
    Set-TaskXmlValue $changed '__PATH__' 'true' $Contract
    $script:currentXml = $changed.OuterXml
    $script:currentDefinitionXml = $null
}
try { [void](Invoke-SelectedCacheTaskInstaller -InvokedPath $contract.Installer -Install -ConfirmInstall); $rejected = $false }
catch { $rejected = $true }
[pscustomobject]@{ Rejected = $rejected; Registered = $script:registered; Saved = $script:lastOperationReceipt
    Events = $script:events.ToArray() } | ConvertTo-Json -Depth 20 -Compress
""".replace("__PATH__", path))
    assert result["Rejected"] is True and result["Registered"] == 1
    assert result["Saved"]["RegistrationVerified"] is False
    assert "enabled state changed unexpectedly" in result["Saved"]["Failure"]["message"]
    assert not {"restart", "uac", "completion_receipt"}.intersection(result["Events"])


@pytest.mark.parametrize("value", ["", "False", "1", "unknown"])
def test_invalid_enabled_value_is_never_guessed_true(value):
    result = _run_ps(MOCK_IO + r"""
$doc = ConvertTo-TaskXmlDocument -Xml $script:valid
Set-TaskXmlValue $doc 'Task/Settings/Enabled' '__VALUE__' $contract
$script:currentXml = $doc.OuterXml
try { [void](Invoke-SelectedCacheTaskInstaller -InvokedPath $contract.Installer -Install -ConfirmInstall); $rejected = $false }
catch { $rejected = $true }
[pscustomobject]@{ Rejected = $rejected; Registered = $script:registered; Events = $script:events.ToArray() } | ConvertTo-Json -Compress
""".replace("__VALUE__", value))
    assert result["Rejected"] is True and result["Registered"] == 0
    assert not {"register", "restart", "uac"}.intersection(result["Events"])
