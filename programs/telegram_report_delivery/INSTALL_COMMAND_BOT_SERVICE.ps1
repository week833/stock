param(
    [switch]$Elevated
)

$ErrorActionPreference = "Stop"
$serviceName = "DStockTelegramCommandBot"
$taskName = "D-Stock Telegram Stock Command Bot"
$suite = (Resolve-Path -LiteralPath (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path
$serviceDir = Join-Path $suite "service"
$wrapper = Join-Path $serviceDir "DStockTelegramCommandBot.exe"
$config = Join-Path $serviceDir "DStockTelegramCommandBot.xml"
$checksumPath = Join-Path $serviceDir "DStockTelegramCommandBot.sha256"
$listener = Join-Path $suite "telegram_stock_command_bot.py"
$stockRoot = (Resolve-Path -LiteralPath (Join-Path $suite "..\..")).Path
$expectedPython = Join-Path $stockRoot ".venv\Scripts\python.exe"
$expectedArguments = '"{0}"' -f $listener
$expectedListenerSourceSha256 = (Get-FileHash -LiteralPath $listener -Algorithm SHA256).Hash.ToUpperInvariant()
$taskInstaller = Join-Path $suite "INSTALL_COMMAND_BOT_TASK.ps1"
$pathContractPath = "D:\TWStockData\config\path_contract.json"
$contract = Get-Content -LiteralPath $pathContractPath -Raw -ErrorAction Stop | ConvertFrom-Json
# Same rule as shared\path_contract.py / telegram_report_sender.py / CHECK_COMMAND_BOT_STATUS.ps1:
# known versions silently, other X.Y.Z with major 1 or 2 accepted with a warning, anything else rejected.
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

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-BenignWinSwStopRace {
    param([System.Management.Automation.ErrorRecord]$ErrorRecord)

    if ($null -eq $ErrorRecord) { return $false }
    $message = [string]$ErrorRecord
    if ($message -match '(?i)(invalid handle|handle is invalid|win32exception\s*\(\s*6\s*\)|error code\s*6|already stopped)') {
        return $true
    }

    $exception = $ErrorRecord.Exception
    while ($null -ne $exception) {
        $nativeErrorCode = $exception.PSObject.Properties['NativeErrorCode']
        if ($null -ne $nativeErrorCode) {
            try {
                if ([int]$nativeErrorCode.Value -eq 6) { return $true }
            } catch {
            }
        }
        $exception = $exception.InnerException
    }
    return $false
}

function Wait-ServiceState {
    param(
        [string]$Name,
        [string]$DesiredState,
        [int]$TimeoutSeconds = 45,
        [switch]$AllowMissing
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $current = Get-Service -Name $Name -ErrorAction SilentlyContinue
        if ($null -eq $current) {
            if ($AllowMissing -and $DesiredState -eq 'Stopped') { return }
            throw "Service disappeared while waiting for state ${DesiredState}: $Name"
        }
        if ([string]$current.Status -eq $DesiredState) { return }
        Start-Sleep -Seconds 1
    }

    $current = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($null -eq $current -and $AllowMissing -and $DesiredState -eq 'Stopped') { return }
    $actualState = if ($null -eq $current) { 'missing' } else { [string]$current.Status }
    throw "Service did not reach state $DesiredState within $TimeoutSeconds seconds: $Name (actual $actualState)"
}

function Stop-ServiceThroughScm {
    param(
        [string]$Name,
        [int]$TimeoutSeconds = 45
    )

    $current = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($null -eq $current) { return }
    if ([string]$current.Status -eq 'Stopped') {
        Write-Verbose "SCM already reports the service stopped; WinSW stopwait is not needed."
        return
    }

    $stopError = $null
    try {
        Stop-Service -Name $Name -ErrorAction Stop
    } catch {
        $stopError = $_
    }

    try {
        Wait-ServiceState -Name $Name -DesiredState 'Stopped' -TimeoutSeconds $TimeoutSeconds -AllowMissing
    } catch {
        if ($null -ne $stopError) {
            throw "SCM stop failed for ${Name}: $($stopError.Exception.Message); $($_.Exception.Message)"
        }
        throw
    }

    if ($null -ne $stopError) {
        if (-not (Test-BenignWinSwStopRace -ErrorRecord $stopError)) {
            throw "SCM stop returned an unexpected error for $Name after waiting for Stopped: $($stopError.Exception.Message)"
        }
        Write-Warning "Accepted benign WinSW stop race for $Name because SCM reached Stopped: $($stopError.Exception.Message)"
    }
}

if (-not (Test-Administrator)) {
    if ($Elevated) {
        throw "Administrator elevation was requested but is not active."
    }
    $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Elevated' -f $PSCommandPath
    $process = Start-Process -FilePath $powershell -Verb RunAs -ArgumentList $arguments -Wait -PassThru
    exit $process.ExitCode
}

foreach ($path in @($wrapper, $config, $checksumPath, $listener, $expectedPython, $taskInstaller)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required service file is missing: $path"
    }
}

$expectedHash = ((Get-Content -LiteralPath $checksumPath -Raw).Trim() -split '\s+')[0].ToUpperInvariant()
$actualHash = (Get-FileHash -LiteralPath $wrapper -Algorithm SHA256).Hash.ToUpperInvariant()
if ($actualHash -ne $expectedHash) {
    throw "WinSW checksum mismatch. Expected $expectedHash, got $actualHash"
}
$configXml = [xml](Get-Content -LiteralPath $config -Raw)
$configuredExecutable = [System.IO.Path]::GetFullPath([string]$configXml.service.executable)
$configuredWorkingDirectory = [System.IO.Path]::GetFullPath([string]$configXml.service.workingdirectory)
$configuredArguments = [string]$configXml.service.arguments
if (-not [System.String]::Equals($configuredExecutable, $expectedPython, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "WinSW executable must be the canonical analysis venv Python: $expectedPython (configured $configuredExecutable)"
}
if (-not [System.String]::Equals($configuredWorkingDirectory, $suite, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "WinSW workingdirectory must be the Telegram suite: $suite (configured $configuredWorkingDirectory)"
}
if (-not [System.String]::Equals($configuredArguments, $expectedArguments, [System.StringComparison]::Ordinal)) {
    throw "WinSW arguments must launch the canonical listener: $expectedArguments"
}
$expectedServiceLogPath = Join-Path $telegramRoot "service_logs"
if ([string]$configXml.service.logpath -ne $expectedServiceLogPath) {
    throw "Service log path is not in the contract Telegram namespace: $expectedServiceLogPath"
}

$service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$serviceWasPresent = $null -ne $service
$serviceWasRunning = $serviceWasPresent -and [string]$service.Status -ne 'Stopped'
$taskWasPresent = $null -ne $task
$heartbeatPath = Join-Path (Join-Path $telegramRoot "data") "stock_command_bot.heartbeat.json"
$previousHeartbeat = $null
if (Test-Path -LiteralPath $heartbeatPath) {
    try { $previousHeartbeat = Get-Content -LiteralPath $heartbeatPath -Raw | ConvertFrom-Json } catch { $previousHeartbeat = $null }
}

try {
    if ($serviceWasPresent) {
        $serviceInfo = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
        if ($null -eq $serviceInfo) {
            throw "Could not read existing service configuration: $serviceName"
        }
        $installedWrapperPath = ([string]$serviceInfo.PathName).Trim().Trim('"')
        if (-not [string]::Equals($installedWrapperPath, $wrapper, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Existing service points to an unexpected wrapper; refusing to overwrite: $($serviceInfo.PathName)"
        }
        if ([string]$serviceInfo.StartName -ne 'LocalSystem') {
            throw "Existing service is not LocalSystem; refusing to overwrite: $($serviceInfo.StartName)"
        }
        Stop-ServiceThroughScm -Name $serviceName
    }

    if (-not $serviceWasPresent) {
        & $wrapper install | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "Service installation failed (exit $LASTEXITCODE)" }
    }

    if ($taskWasPresent) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    $oldProcesses = @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $_.Name -match '^(python|pythonw)(\.exe)?$' -and
            $_.CommandLine -and
            $_.CommandLine -like "*$listener*" -and
            $_.CommandLine -notmatch '\s--(?:check|bootstrap-only)(?:\s|$)'
        }
    )
    foreach ($process in $oldProcesses) {
        Stop-Process -Id ([int]$process.ProcessId) -Force -ErrorAction SilentlyContinue
    }
    if ($oldProcesses.Count) { Start-Sleep -Seconds 2 }

    $service = Get-Service -Name $serviceName -ErrorAction Stop
    if ([string]$service.Status -ne 'Running') {
        Start-Service -Name $serviceName -ErrorAction Stop
    }
    Wait-ServiceState -Name $serviceName -DesiredState 'Running'

    $deadline = (Get-Date).AddSeconds(45)
    $healthy = $false
    $heartbeatProcess = $null
    $observedExecutable = $null
    $observedCommandLine = $null
    $listenerExecutableMatch = $false
    $listenerCommandLineMatch = $false
    $listenerSourceSha256Match = $false
    do {
        Start-Sleep -Seconds 2
        $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
        $heartbeat = $null
        if (Test-Path -LiteralPath $heartbeatPath) {
            try { $heartbeat = Get-Content -LiteralPath $heartbeatPath -Raw | ConvertFrom-Json } catch { $heartbeat = $null }
        }
        if ($null -ne $service -and $service.Status -eq "Running" -and $null -ne $heartbeat) {
            try {
                $age = ((Get-Date) - ([DateTimeOffset]::Parse([string]$heartbeat.updatedAt).LocalDateTime)).TotalSeconds
                $heartbeatProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$heartbeat.pid)" -ErrorAction SilentlyContinue
                $observedExecutable = if ($null -ne $heartbeatProcess) { [string]$heartbeatProcess.ExecutablePath } else { $null }
                $observedCommandLine = if ($null -ne $heartbeatProcess) { [string]$heartbeatProcess.CommandLine } else { $null }
                $listenerExecutableMatch = $null -ne $heartbeatProcess -and
                    [System.String]::Equals($observedExecutable, $expectedPython, [System.StringComparison]::OrdinalIgnoreCase)
                $listenerCommandLineMatch = $null -ne $heartbeatProcess -and
                    -not [string]::IsNullOrWhiteSpace($observedCommandLine) -and
                    $observedCommandLine.IndexOf($listener, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
                $listenerSourceSha256Match = [System.String]::Equals(
                    [string]$heartbeat.sourceSha256,
                    $expectedListenerSourceSha256,
                    [System.StringComparison]::OrdinalIgnoreCase
                )
                $isNewHeartbeat = $null -eq $previousHeartbeat -or
                    [string]$heartbeat.pid -ne [string]$previousHeartbeat.pid -or
                    [string]$heartbeat.startedAt -ne [string]$previousHeartbeat.startedAt
                $healthy = $age -le 180 -and
                    $null -ne $heartbeatProcess -and
                    $isNewHeartbeat -and
                    [string]$heartbeat.status -in @("booting", "running", "polling", "working") -and
                    $listenerExecutableMatch -and
                    $listenerCommandLineMatch -and
                    $listenerSourceSha256Match
            } catch { $healthy = $false }
        }
    } while (-not $healthy -and (Get-Date) -lt $deadline)

    if (-not $healthy) {
        throw "Service did not reach a healthy heartbeat within 45 seconds"
    }

    if ($taskWasPresent) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }

    $serviceInfo = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
    if ([string]$serviceInfo.StartName -ne "LocalSystem") {
        throw "Service is not running as LocalSystem: $($serviceInfo.StartName)"
    }
    [pscustomobject]@{
        ServiceName = $serviceInfo.Name
        DisplayName = $serviceInfo.DisplayName
        State = $serviceInfo.State
        StartMode = $serviceInfo.StartMode
        StartName = $serviceInfo.StartName
        ProcessId = $serviceInfo.ProcessId
        ListenerExecutable = $observedExecutable
        ListenerCommandLine = $observedCommandLine
        ExpectedPython = $expectedPython
        ListenerExecutableMatch = $listenerExecutableMatch
        ListenerCommandLineMatch = $listenerCommandLineMatch
        ListenerSourceSha256 = $expectedListenerSourceSha256
        HeartbeatSourceSha256 = if ($null -ne $heartbeat) { [string]$heartbeat.sourceSha256 } else { $null }
        ListenerSourceSha256Match = $listenerSourceSha256Match
        HeartbeatStatus = $heartbeat.status
        HeartbeatPid = $heartbeat.pid
        ScheduledTaskRemoved = -not [bool](Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)
        WrapperSHA256 = $actualHash
    }
} catch {
    Write-Error $_
    try {
        $installed = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
        if ($null -ne $installed -and [string]$installed.Status -ne 'Stopped') {
            Stop-ServiceThroughScm -Name $serviceName
        }
        if (-not $serviceWasPresent -and $null -ne (Get-Service -Name $serviceName -ErrorAction SilentlyContinue)) {
            & $wrapper uninstall | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "WinSW uninstall returned exit $LASTEXITCODE while cleaning up a newly installed service."
            }
        }
        if ($serviceWasPresent -and $serviceWasRunning) {
            $installed = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
            if ($null -ne $installed -and [string]$installed.Status -eq 'Stopped') {
                Start-Service -Name $serviceName -ErrorAction Stop
                Wait-ServiceState -Name $serviceName -DesiredState 'Running'
            }
        }
    } catch {
        Write-Warning "Could not clean up failed service installation: $($_.Exception.Message)"
    }
    if ($taskWasPresent) {
        try {
            & $taskInstaller | Out-Host
        } catch {
            Write-Warning "Could not restore the logon task: $($_.Exception.Message)"
        }
    }
    exit 1
}
