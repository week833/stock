param(
    [string]$TaskName = "D-Stock Telegram Stock Command Bot",
    [switch]$Json
)

function Resolve-ListenerStatus {
    param(
        [bool]$HostStateValid,
        [bool]$OwnerProcessAlive,
        [bool]$OwnerNameMatch,
        [bool]$OwnerCreationIdentityMatch,
        [bool]$OwnerHeartbeatPidMatch,
        [string]$OwnerExecutableStatus,
        [string]$OwnerCommandLineStatus,
        [object]$HeartbeatAgeSeconds,
        [string]$HeartbeatStatus,
        [bool]$SourceSha256Match
    )

    $heartbeatStatusValid = $HeartbeatStatus -in @("booting", "running", "polling", "working")
    $heartbeatFresh = $null -ne $HeartbeatAgeSeconds -and
        [double]$HeartbeatAgeSeconds -le 180 -and $heartbeatStatusValid
    $identityValid = $OwnerProcessAlive -and $OwnerNameMatch -and
        $OwnerCreationIdentityMatch -and $OwnerHeartbeatPidMatch -and
        $HostStateValid -and $OwnerExecutableStatus -ne "mismatch" -and
        $OwnerCommandLineStatus -ne "mismatch"
    $health = if (-not $identityValid) {
        if (-not $OwnerProcessAlive) { "stopped" } else { "owner_identity_mismatch" }
    } elseif (-not $heartbeatFresh) {
        "stale"
    } else {
        "running"
    }
    [pscustomobject]@{
        IdentityValid = $identityValid
        HeartbeatFresh = $heartbeatFresh
        ListenerHealth = $health
        SourceStatus = if ($SourceSha256Match) { "current" } else { "stale_source" }
        ListenerReadiness = if ($health -eq "running" -and $SourceSha256Match) {
            "ready"
        } elseif ($health -eq "running") {
            "restart_required"
        } else {
            "blocked"
        }
    }
}

function Test-SameUtcTimestamp {
    param([object]$Left, [object]$Right)
    if ($null -eq $Left -or $null -eq $Right) { return $false }
    try {
        return ([datetimeoffset]$Left).UtcTicks -eq ([datetimeoffset]$Right).UtcTicks
    } catch {
        return $false
    }
}

function Get-PathContractVersionLabel {
    param([string]$Version)
    # Returns $null for a known version, a version label for an unknown X.Y.Z whose
    # major is 1 or 2, and throws for anything else.
    if ($Version -in @("1.1.0", "2.0.0")) { return $null }
    if ($Version -notmatch '^(1|2)\.\d+\.\d+$') { throw "contract_version must be 1.x or 2.x (known: 1.1.0, 2.0.0): $Version" }
    [pscustomobject]@{
        code = "path_contract_version_unknown"
        recorded = @("1.1.0", "2.0.0")
        actual = $Version
        detail = "accepted because major version is supported; root and managed_paths checks still apply"
    }
}

function Test-StrictVenvServiceOwnerEvidence {
    param(
        [int]$HelperExitCode,
        [object]$Evidence,
        [string]$ExpectedServicePython,
        [string]$ObservedOwnerExecutablePath,
        [object]$Owner,
        [object]$Heartbeat,
        [object]$OwnerProcess,
        [string]$DiskSourceSHA256,
        [string]$ObservedServiceName,
        [string]$ObservedServiceState,
        # Stale source (resident loaded an older listener than the disk file) is a
        # version label, not an identity failure.  Only with this switch is a
        # helper result with SourceMismatch=true accepted, and then only when the
        # helper explicitly allowed it and the loaded SHA really differs from disk.
        [switch]$AllowStaleSource
    )

    if ($HelperExitCode -ne 0 -or $null -eq $Evidence -or
        [string]::IsNullOrWhiteSpace($ObservedOwnerExecutablePath) -or
        [System.String]::Equals($ObservedOwnerExecutablePath, $ExpectedServicePython, [System.StringComparison]::OrdinalIgnoreCase) -or
        [string]$Evidence.Operation -ne "verify" -or
        [string]$Evidence.ServiceName -ne $ObservedServiceName -or
        [string]$Evidence.ServiceState -ne "Running" -or
        [string]$ObservedServiceState -ne "Running") {
        return $false
    }
    $staleSourceAccepted = $false
    if ([bool]$Evidence.SourceMismatch) {
        $mismatchAllowedProperty = $Evidence.PSObject.Properties["SourceMismatchAllowed"]
        if (-not $AllowStaleSource -or $null -eq $mismatchAllowedProperty -or -not [bool]$mismatchAllowedProperty.Value -or
            [System.String]::Equals([string]$Evidence.LoadedSourceSHA256, $DiskSourceSHA256, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $false
        }
        $staleSourceAccepted = $true
    }

    if ([string]$Evidence.OwnerPid -ne [string]$Owner.processId -or
        [string]$Evidence.OwnerPid -ne [string]$Heartbeat.pid -or
        [string]$Evidence.OwnerPid -ne [string]$OwnerProcess.ProcessId -or
        -not (Test-SameUtcTimestamp $Evidence.OwnerStartedAt $Owner.startedAt) -or
        -not (Test-SameUtcTimestamp $Evidence.OwnerCreationDate $OwnerProcess.CreationDate) -or
        -not (Test-SameUtcTimestamp $Evidence.HeartbeatStartedAt $Heartbeat.startedAt) -or
        -not (Test-SameUtcTimestamp $Evidence.ResidentIdentityStartedAt $Heartbeat.startedAt)) {
        return $false
    }

    $heartbeatSha = [string]$Heartbeat.sourceSha256
    $ownerSha = [string]$Owner.sourceSha256
    if ($heartbeatSha -notmatch '^[0-9a-fA-F]{64}$' -or
        -not [System.String]::Equals([string]$Evidence.OwnerSourceSHA256, $ownerSha, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not [System.String]::Equals([string]$Evidence.HeartbeatSourceSHA256, $heartbeatSha, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not [System.String]::Equals([string]$Evidence.ResidentSourceSHA256, $heartbeatSha, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not [System.String]::Equals([string]$Evidence.LoadedSourceSHA256, $heartbeatSha, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not [System.String]::Equals([string]$Evidence.DiskListenerSHA256, $DiskSourceSHA256, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not [System.String]::Equals([string]$Evidence.ListenerSHA256, $DiskSourceSHA256, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $false
    }

    $sourceEvidence = $Evidence.SourceEvidence
    foreach ($name in @(
        "OwnerHeartbeatSourceMatch",
        "DiskContractSourceMatch",
        "DiskSourceIdentityStable",
        "ResidentDiskSourceMatch",
        "ResidentPidIdentity",
        "ResidentPidHeartbeatIdentity",
        "ResidentPidOwnerIdentity"
    )) {
        if ($staleSourceAccepted -and $name -eq "ResidentDiskSourceMatch") { continue }
        $property = if ($null -ne $sourceEvidence) { $sourceEvidence.PSObject.Properties[$name] } else { $null }
        if ($null -eq $property -or -not [bool]$property.Value) { return $false }
    }

    return $true
}

function Test-StrictVenvServiceOwner {
    param(
        [object]$Owner,
        [object]$Heartbeat,
        [object]$OwnerProcess,
        [string]$ExpectedServicePython,
        [string]$DiskSourceSHA256,
        [string]$ObservedServiceName,
        [string]$ObservedServiceState,
        [switch]$AllowStaleSource
    )

    $process = $null
    try {
        $windowsPowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
        $helperPath = Join-Path $PSScriptRoot "RESTART_COMMAND_BOT_SERVICE.ps1"
        if (-not (Test-Path -LiteralPath $windowsPowerShell -PathType Leaf) -or
            -not (Test-Path -LiteralPath $helperPath -PathType Leaf)) {
            return $false
        }

        $modulePaths = @()
        if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
            $programModules = Join-Path $env:ProgramFiles "WindowsPowerShell\Modules"
            if (Test-Path -LiteralPath $programModules -PathType Container) { $modulePaths += $programModules }
        }
        $systemModules = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\Modules"
        if (Test-Path -LiteralPath $systemModules -PathType Container) { $modulePaths += $systemModules }
        if ($modulePaths.Count -eq 0) { return $false }

        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = $windowsPowerShell
        $startInfo.Arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -VerifyOnly' -f $helperPath
        if ($AllowStaleSource) { $startInfo.Arguments += ' -AllowStaleSource' }
        $startInfo.WorkingDirectory = $PSScriptRoot
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $startInfo.EnvironmentVariables["PSModulePath"] = $modulePaths -join ";"

        $process = [System.Diagnostics.Process]::Start($startInfo)
        if ($null -eq $process) { return $false }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(30000)) {
            try {
                $process.Kill()
                $process.WaitForExit()
            } catch { }
            return $false
        }
        $output = $stdoutTask.GetAwaiter().GetResult()
        $null = $stderrTask.GetAwaiter().GetResult()
        $exitCode = [int]$process.ExitCode
        if ($exitCode -ne 0 -or [string]::IsNullOrWhiteSpace($output)) { return $false }

        $evidence = ConvertFrom-Json -InputObject $output -ErrorAction Stop
        return Test-StrictVenvServiceOwnerEvidence `
            -HelperExitCode $exitCode `
            -Evidence $evidence `
            -ExpectedServicePython $ExpectedServicePython `
            -ObservedOwnerExecutablePath ([string]$OwnerProcess.ExecutablePath) `
            -Owner $Owner `
            -Heartbeat $Heartbeat `
            -OwnerProcess $OwnerProcess `
            -DiskSourceSHA256 $DiskSourceSHA256 `
            -ObservedServiceName $ObservedServiceName `
            -ObservedServiceState $ObservedServiceState `
            -AllowStaleSource:$AllowStaleSource
    } catch {
        if ($null -ne $process) {
            try {
                if (-not $process.HasExited) {
                    $process.Kill()
                    $process.WaitForExit()
                }
            } catch { }
        }
        return $false
    } finally {
        if ($null -ne $process) { $process.Dispose() }
    }
}

function Invoke-StatusCheck {
param(
    [string]$TaskName,
    [switch]$Json
)

$serviceName = "DStockTelegramCommandBot"
$suite = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$stockRoot = (Resolve-Path -LiteralPath (Join-Path $suite "..\..")).Path
$expectedServicePython = Join-Path $stockRoot ".venv\Scripts\python.exe"
$expectedTaskPython = Join-Path $stockRoot ".venv\Scripts\pythonw.exe"
$pathContractPath = "D:\TWStockData\config\path_contract.json"
$versionLabels = @()
try {
    $contract = Get-Content -LiteralPath $pathContractPath -Raw -ErrorAction Stop | ConvertFrom-Json
    # Same rule as shared\path_contract.py / telegram_report_sender.py / INSTALL_COMMAND_BOT_SERVICE.ps1:
    # known versions silently, other X.Y.Z with major 1 or 2 accepted with a warning, anything else rejected.
    $contractVersionLabel = Get-PathContractVersionLabel -Version ([string]$contract.contract_version)
    if ($null -ne $contractVersionLabel) {
        $versionLabels += $contractVersionLabel
        Write-Warning "Path contract version $([string]$contract.contract_version) is not a known version (1.1.0, 2.0.0); accepted by major version."
    }
    if ([string]::IsNullOrWhiteSpace([string]$contract.root)) { throw "root is missing" }
    $contractRoot = [System.IO.Path]::GetFullPath([string]$contract.root).TrimEnd("\")
    if (-not $contractRoot.Equals("D:\TWStockData", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "root must be D:\TWStockData"
    }
    $telegramRoot = [string]$contract.managed_paths.telegram
    if ([string]::IsNullOrWhiteSpace($telegramRoot)) { throw "managed_paths.telegram is missing" }
}
catch {
    Write-Error "Unable to load Telegram path contract: $pathContractPath"
    exit 1
}
$dataRoot = Join-Path $telegramRoot "data"
$statePath = Join-Path $dataRoot "stock_command_state.json"
$ownerPath = Join-Path $dataRoot "stock_command_bot.owner.json"
$heartbeatPath = Join-Path $dataRoot "stock_command_bot.heartbeat.json"
$listener = Join-Path $suite "telegram_stock_command_bot.py"

function Get-FileSHA256 {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    $stream = $null
    $hasher = $null
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        $hasher = [System.Security.Cryptography.SHA256]::Create()
        return ([System.BitConverter]::ToString($hasher.ComputeHash($stream))).Replace("-", "").ToUpperInvariant()
    } catch {
        return $null
    } finally {
        if ($null -ne $hasher) { $hasher.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

$diskSourceSha256 = $null
$diskSourceSha256 = Get-FileSHA256 -Path $listener
$service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction SilentlyContinue
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $service -and $null -eq $task) {
    Write-Output "Telegram command bot host is not installed."
    exit 1
}
$info = if ($null -ne $task) { Get-ScheduledTaskInfo -TaskName $TaskName } else { $null }
$queued = 0
$running = 0
$lastJob = ""
$nextUpdateId = $null
if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw -Encoding utf8 | ConvertFrom-Json
    $queued = @($state.jobs | Where-Object { $_.status -eq "queued" }).Count
    $running = @($state.jobs | Where-Object { $_.status -eq "running" }).Count
    $nextUpdateId = $state.nextUpdateId
    $last = @($state.jobs | Select-Object -Last 1)
    if ($last.Count) {
        $lastJob = "$($last[0].jobId) / $($last[0].status) / $($last[0].query)"
    }
}
$owner = $null
$heartbeat = $null
if (Test-Path -LiteralPath $ownerPath) {
    try { $owner = Get-Content -LiteralPath $ownerPath -Raw -Encoding utf8 | ConvertFrom-Json } catch { $owner = $null }
}
if (Test-Path -LiteralPath $heartbeatPath) {
    try { $heartbeat = Get-Content -LiteralPath $heartbeatPath -Raw -Encoding utf8 | ConvertFrom-Json } catch { $heartbeat = $null }
}
$listenerProcesses = @(
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -match '^(python|pythonw)(\.exe)?$' -and
        -not [string]::IsNullOrWhiteSpace([string]$_.CommandLine) -and
        [string]$_.CommandLine -like "*$listener*" -and
        [string]$_.CommandLine -notmatch '\s--(?:check|bootstrap-only)(?:\s|$)'
    }
)
$ownerProcess = $null
if ($null -ne $owner -and $owner.processId) {
    # LocalSystem process command lines and executable paths can be hidden from
    # a non-elevated checker.  The owner file plus PID/creation identity is the
    # authoritative liveness evidence; an unreadable field is not a dead PID.
    $ownerProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$owner.processId)" -ErrorAction SilentlyContinue
    if ($null -ne $ownerProcess -and $ownerProcess.Name -match '^(python|pythonw)(\.exe)?$') {
        $knownPids = @($listenerProcesses | Select-Object -ExpandProperty ProcessId)
        if ([int]$ownerProcess.ProcessId -notin $knownPids) {
            $listenerProcesses += $ownerProcess
        }
    }
}
$serviceProcess = $null
$serviceProcessAlive = $true
if ($null -ne $service -and $service.State -eq "Running") {
    if ([int]$service.ProcessId -gt 0) {
        $serviceProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$service.ProcessId)" -ErrorAction SilentlyContinue
        $serviceProcessAlive = $null -ne $serviceProcess
    } else {
        $serviceProcessAlive = $false
    }
}
$hostStateValid = if ($null -ne $service) {
    [string]$service.State -eq "Running" -and $serviceProcessAlive
} else {
    $null -ne $task -and [string]$task.State -in @("Ready", "Running")
}
$expectedListenerPython = if ($null -ne $service) { $expectedServicePython } else { $expectedTaskPython }
$listenerExecutablePaths = @($listenerProcesses | ForEach-Object { [string]$_.ExecutablePath } | Where-Object { $_ })
$listenerCommandLines = @($listenerProcesses | ForEach-Object { [string]$_.CommandLine } | Where-Object { $_ })
$ownerProcessAlive = $null -ne $ownerProcess
$ownerNameMatch = $ownerProcessAlive -and $ownerProcess.Name -match '^(python|pythonw)(\.exe)?$'
$ownerCreationIdentityMatch = $false
if ($ownerProcessAlive -and $owner.startedAt -and $ownerProcess.CreationDate) {
    try {
        $ownerStartedAt = [datetimeoffset]$owner.startedAt
        $processCreatedAt = [datetimeoffset]$ownerProcess.CreationDate
        $ownerCreationIdentityMatch = [math]::Abs(($processCreatedAt.UtcDateTime - $ownerStartedAt.UtcDateTime).TotalSeconds) -le 5
    } catch { $ownerCreationIdentityMatch = $false }
}
$ownerHeartbeatPidMatch = $null -ne $owner -and $null -ne $heartbeat -and
    [string]$owner.processId -eq [string]$heartbeat.pid
$ownerExecutablePath = if ($ownerProcessAlive) { [string]$ownerProcess.ExecutablePath } else { $null }
$ownerCommandLine = if ($ownerProcessAlive) { [string]$ownerProcess.CommandLine } else { $null }
$ownerExecutableStatus = if (-not $ownerProcessAlive) {
    "unavailable"
} elseif ([string]::IsNullOrWhiteSpace($ownerExecutablePath)) {
    "unavailable_due_to_access"
} elseif ([System.String]::Equals($ownerExecutablePath, $expectedListenerPython, [System.StringComparison]::OrdinalIgnoreCase)) {
    "match"
} else {
    "mismatch"
}
$ownerCommandLineStatus = if (-not $ownerProcessAlive) {
    "unavailable"
} elseif ([string]::IsNullOrWhiteSpace($ownerCommandLine)) {
    "unavailable_due_to_access"
} elseif ($ownerCommandLine -like "*$listener*" -and $ownerCommandLine -notmatch '\s--(?:check|bootstrap-only)(?:\s|$)') {
    "match"
} else {
    "mismatch"
}
$listenerExecutableMatch = if ($ownerProcessAlive) {
    $ownerExecutableStatus -eq "match"
} else {
    @(
        $listenerProcesses | Where-Object {
            [System.String]::Equals([string]$_.ExecutablePath, $expectedListenerPython, [System.StringComparison]::OrdinalIgnoreCase)
        }
    ).Count -gt 0
}
$listenerCommandLineMatch = if ($ownerProcessAlive) {
    $ownerCommandLineStatus -eq "match"
} else {
    @(
        $listenerProcesses | Where-Object {
            -not [string]::IsNullOrWhiteSpace([string]$_.CommandLine) -and
            [string]$_.CommandLine -like "*$listener*"
        }
    ).Count -gt 0
}
$listenerCommandLineStatus = if ($ownerCommandLineStatus -eq "mismatch") {
    "mismatch"
} elseif ($ownerCommandLineStatus -eq "unavailable_due_to_access") {
    "unavailable_due_to_access"
} elseif ($listenerCommandLineMatch) {
    "match"
} else {
    "unavailable"
}
$heartbeatAgeSeconds = $null
if ($null -ne $heartbeat -and $heartbeat.updatedAt) {
    try {
        $heartbeatAgeSeconds = [math]::Round(((Get-Date) - ([DateTimeOffset]::Parse([string]$heartbeat.updatedAt).LocalDateTime)).TotalSeconds, 1)
    } catch { $heartbeatAgeSeconds = $null }
}
$heartbeatStatus = if ($null -ne $heartbeat) { [string]$heartbeat.status } else { "missing" }
$loadedSourceSha256 = if ($null -ne $heartbeat) { [string]$heartbeat.sourceSha256 } else { $null }
$sourceSha256Match = $null -ne $diskSourceSha256 -and
    -not [string]::IsNullOrWhiteSpace($loadedSourceSha256) -and
    [string]::Equals($diskSourceSha256, $loadedSourceSha256, [System.StringComparison]::OrdinalIgnoreCase)
# stale_source: the resident process declares a valid but older listener SHA than
# the disk file.  That is a code-version label ("restart required"), not a
# liveness/identity failure; a missing or malformed loaded SHA is still invalid.
$sourceStale = $null -ne $diskSourceSha256 -and -not $sourceSha256Match -and
    [string]$loadedSourceSha256 -match '^[0-9a-fA-F]{64}$'
$verifiedVenvServiceOwner = $false
if ($null -ne $service -and $ownerProcessAlive -and $ownerExecutableStatus -eq "mismatch" -and
    $ownerNameMatch -and $ownerCreationIdentityMatch -and $ownerHeartbeatPidMatch -and
    $hostStateValid -and ($sourceSha256Match -or $sourceStale) -and $ownerCommandLineStatus -ne "mismatch") {
    $verifiedVenvServiceOwner = Test-StrictVenvServiceOwner `
        -Owner $owner `
        -Heartbeat $heartbeat `
        -OwnerProcess $ownerProcess `
        -ExpectedServicePython $expectedServicePython `
        -DiskSourceSHA256 $diskSourceSha256 `
        -ObservedServiceName $serviceName `
        -ObservedServiceState ([string]$service.State) `
        -AllowStaleSource:$sourceStale
}
if ($sourceStale) {
    $versionLabels += [pscustomobject]@{
        code = "resident_listener_source_stale"
        recorded = $loadedSourceSha256
        actual = $diskSourceSha256
        detail = "resident process runs an older listener source; restart is required to load the current file"
    }
}
$ownerExecutableStatusForDecision = if ($verifiedVenvServiceOwner) {
    "verified_venv_launcher"
} else {
    $ownerExecutableStatus
}
$listenerExecutableStatus = if ($verifiedVenvServiceOwner) {
    "verified_venv_launcher"
} elseif ($ownerExecutableStatus -eq "mismatch") {
    "mismatch"
} elseif ($ownerExecutableStatus -eq "unavailable_due_to_access") {
    "unavailable_due_to_access"
} elseif ($listenerExecutableMatch) {
    "match"
} else {
    "unavailable"
}
$runtimeRevision = if ($null -ne $heartbeat) { [string]$heartbeat.runtimeRevision } else { $null }
$listenerDecision = Resolve-ListenerStatus `
    -HostStateValid $hostStateValid `
    -OwnerProcessAlive $ownerProcessAlive `
    -OwnerNameMatch $ownerNameMatch `
    -OwnerCreationIdentityMatch $ownerCreationIdentityMatch `
    -OwnerHeartbeatPidMatch $ownerHeartbeatPidMatch `
    -OwnerExecutableStatus $ownerExecutableStatusForDecision `
    -OwnerCommandLineStatus $ownerCommandLineStatus `
    -HeartbeatAgeSeconds $heartbeatAgeSeconds `
    -HeartbeatStatus $heartbeatStatus `
    -SourceSha256Match $sourceSha256Match
$heartbeatFresh = [bool]$listenerDecision.HeartbeatFresh
$listenerIdentityValid = [bool]$listenerDecision.IdentityValid
$listenerHealth = [string]$listenerDecision.ListenerHealth
$sourceStatus = [string]$listenerDecision.SourceStatus
$listenerReadiness = [string]$listenerDecision.ListenerReadiness
$result = [pscustomobject]@{
    HostMode = if ($null -ne $service) { "WindowsService" } else { "LogonTask" }
    ServiceName = if ($null -ne $service) { $service.Name } else { $null }
    ServiceState = if ($null -ne $service) { $service.State } else { $null }
    ServiceStartMode = if ($null -ne $service) { $service.StartMode } else { $null }
    ServiceAccount = if ($null -ne $service) { $service.StartName } else { $null }
    ServiceProcessId = if ($null -ne $service) { $service.ProcessId } else { $null }
    TaskName = if ($null -ne $task) { $task.TaskName } else { $null }
    TaskState = if ($null -ne $task) { $task.State } else { $null }
    TaskLastRunTime = if ($null -ne $info) { $info.LastRunTime } else { $null }
    TaskLastTaskResult = if ($null -ne $info) { $info.LastTaskResult } else { $null }
    QueuedJobs = $queued
    RunningJobs = $running
    LastJob = $lastJob
    NextUpdateId = $nextUpdateId
    ListenerHealth = $listenerHealth
    ListenerReadiness = $listenerReadiness
    SourceStatus = $sourceStatus
    ListenerProcessIds = (@($listenerProcesses | Select-Object -ExpandProperty ProcessId) -join ",")
    ExpectedListenerPython = $expectedListenerPython
    ListenerExecutablePaths = ($listenerExecutablePaths -join ",")
    ListenerExecutableMatch = ($listenerExecutableMatch -or $verifiedVenvServiceOwner)
    ListenerExecutableStatus = $listenerExecutableStatus
    VerifiedVenvLauncherOwner = $verifiedVenvServiceOwner
    ListenerCommandLineMatch = $listenerCommandLineMatch
    ListenerCommandLineStatus = $listenerCommandLineStatus
    OwnerPid = if ($null -ne $owner) { $owner.processId } else { $null }
    OwnerProcessAlive = $ownerProcessAlive
    OwnerCreationIdentityMatch = $ownerCreationIdentityMatch
    OwnerHeartbeatPidMatch = $ownerHeartbeatPidMatch
    ServiceProcessAlive = $serviceProcessAlive
    HostStateValid = $hostStateValid
    HeartbeatStatus = if ($null -ne $heartbeat) { $heartbeat.status } else { "missing" }
    HeartbeatUpdatedAt = if ($null -ne $heartbeat) { $heartbeat.updatedAt } else { $null }
    HeartbeatAgeSeconds = $heartbeatAgeSeconds
    DiskSourceSHA256 = $diskSourceSha256
    LoadedSourceSHA256 = $loadedSourceSha256
    SourceSHA256Match = $sourceSha256Match
    RuntimeRevision = $runtimeRevision
    Action = if ($null -ne $service) { $service.PathName } else { $task.Actions.Arguments }
    TaskActionExecutable = if ($null -ne $task) { $task.Actions.Execute } else { $null }
    PathContractVersion = [string]$contract.contract_version
    VersionLabels = @($versionLabels)
    Warnings = @($versionLabels | ForEach-Object { [string]$_.code })
}
if ($Json) {
    $result | ConvertTo-Json -Depth 8 -Compress
} else {
    $result
}
if (-not $listenerIdentityValid) {
    Write-Error "Resident Telegram listener owner PID/creation identity/service process is not valid."
    exit 2
}
if (-not $heartbeatFresh) {
    Write-Error "Resident Telegram listener heartbeat is missing, stale, or not in a live status."
    exit 2
}
if ($sourceStale) {
    # Converted from a blocking error: the listener is alive and healthy but runs an
    # older source.  Report it and exit 0; RESTART_COMMAND_BOT_SERVICE.cmd reloads it.
    Write-Warning "Resident Telegram listener runs an older source (stale_source; loaded $loadedSourceSha256, disk $diskSourceSha256); restart is required to load the current file."
    exit 0
}
if (-not $sourceSha256Match) {
    Write-Error "Resident Telegram listener source SHA evidence is missing or invalid."
    exit 2
}
}

if ($MyInvocation.InvocationName -ne ".") {
    Invoke-StatusCheck -TaskName $TaskName -Json:$Json
}
