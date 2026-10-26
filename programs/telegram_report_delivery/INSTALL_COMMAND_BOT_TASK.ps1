param(
    [string]$TaskName = "D-Stock Telegram Stock Command Bot"
)

$ErrorActionPreference = "Stop"
$serviceName = "DStockTelegramCommandBot"
$suite = Split-Path -Parent $MyInvocation.MyCommand.Path
$listener = Join-Path $suite "telegram_stock_command_bot.py"
$root = (Resolve-Path -LiteralPath (Join-Path $suite "..\..")).Path
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$pathContractPath = "D:\TWStockData\config\path_contract.json"
$contract = Get-Content -LiteralPath $pathContractPath -Raw -ErrorAction Stop | ConvertFrom-Json
# Same rule as shared\path_contract.py / telegram_report_sender.py / CHECK_COMMAND_BOT_STATUS.ps1 /
# INSTALL_COMMAND_BOT_SERVICE.ps1: known versions silently, other X.Y.Z with major 1 or 2 accepted
# with a warning, anything else rejected.
$contractVersion = [string]$contract.contract_version
if ($contractVersion -notin @("1.1.0", "2.0.0")) {
    if ($contractVersion -notmatch '^(1|2)\.\d+\.\d+$') {
        throw "Path contract version must be 1.x or 2.x (known: 1.1.0, 2.0.0): $pathContractPath ($contractVersion)"
    }
    Write-Warning "Path contract version $contractVersion is not a known version (1.1.0, 2.0.0); accepted by major version: $pathContractPath"
}
if ([string]::IsNullOrWhiteSpace([string]$contract.root)) {
    throw "Path contract root is missing: $pathContractPath"
}
$contractRoot = [System.IO.Path]::GetFullPath([string]$contract.root).TrimEnd("\")
if (-not $contractRoot.Equals("D:\TWStockData", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Path contract root must be D:\TWStockData: $contractRoot"
}
$telegramRoot = [string]$contract.managed_paths.telegram
if ([string]::IsNullOrWhiteSpace($telegramRoot)) {
    throw "Path contract is missing managed_paths.telegram: $pathContractPath"
}
$ownerPath = Join-Path (Join-Path $telegramRoot "data") "stock_command_bot.owner.json"
if ($null -ne (Get-Service -Name $serviceName -ErrorAction SilentlyContinue)) {
    throw "Windows service $serviceName is installed. Remove it before installing the logon task."
}
if (-not (Test-Path -LiteralPath $listener -PathType Leaf)) {
    throw "Command bot listener not found: $listener"
}
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
    throw "Python windowless executable not found: $pythonw"
}
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $existingTask) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

if (Test-Path -LiteralPath $ownerPath) {
    try {
        $record = Get-Content -LiteralPath $ownerPath -Raw | ConvertFrom-Json
        $ownerProcessId = [int]$record.processId
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$ownerProcessId" -ErrorAction SilentlyContinue
        if ($null -ne $owner -and $owner.Name -match '^(python|pythonw)(\.exe)?$') {
            $recordedStart = [datetime]::ParseExact([string]$record.startedAt, "yyyy-MM-ddTHH:mm:ss", [Globalization.CultureInfo]::InvariantCulture)
            $actualStart = (Get-Process -Id $ownerProcessId -ErrorAction Stop).StartTime
            if ([Math]::Abs(($actualStart - $recordedStart).TotalSeconds) -le 15) {
                Stop-Process -Id $ownerProcessId -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {
        Write-Warning "Could not validate the recorded listener process: $($_.Exception.Message)"
    }
}
$existingProcesses = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^(python|pythonw)(\.exe)?$' -and
    $_.CommandLine -like "*$listener*" -and
    $_.CommandLine -notmatch '\s--(?:check|bootstrap-only)(?:\s|$)'
})
foreach ($process in $existingProcesses) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}
if ($existingProcesses.Count) {
    Start-Sleep -Seconds 1
}
if ($existingProcesses.Count -eq 0) {
    Remove-Item -LiteralPath $ownerPath -Force -ErrorAction SilentlyContinue
}

$arguments = '"{0}"' -f $listener
$taskAction = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $suite
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -WakeToRun `
    -MultipleInstances IgnoreNew `
    -RestartCount 20 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $taskAction `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Listen for private Telegram stock commands and return single-stock PDF reports." `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 2
$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
[pscustomobject]@{
    TaskName = $task.TaskName
    State = $task.State
    User = $task.Principal.UserId
    LogonType = $task.Principal.LogonType
    LastRunTime = $info.LastRunTime
    LastTaskResult = $info.LastTaskResult
    Action = $task.Actions.Execute
    Arguments = $task.Actions.Arguments
}
