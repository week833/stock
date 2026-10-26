[CmdletBinding()]
param(
    [switch]$Plan,
    [switch]$Register,
    [switch]$ConfirmRegister,
    [switch]$Verify
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$suite = (Resolve-Path -LiteralPath (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path
$definitionPath = Join-Path $suite 'MIX_WEEKDAY_0800_TASK_DEFINITION.json'
$taskName = 'MIX Telegram Report 08-00'
$taskPath = '\'
$timeZoneId = 'Taipei Standard Time'

$modeCount = @(
    if ($Plan) { 'Plan' }
    if ($Register) { 'Register' }
    if ($Verify) { 'Verify' }
).Count
if ($modeCount -gt 1) { throw 'Choose only one of -Plan, -Register, or -Verify' }
if ($ConfirmRegister -and -not $Register) { throw '-ConfirmRegister is valid only with -Register' }
if ($Register -and -not $ConfirmRegister) {
    throw 'Registration is disabled by default; rerun with both -Register -ConfirmRegister'
}
if (-not $Plan -and -not $Register -and -not $Verify) { $Plan = $true }
if (-not (Test-Path -LiteralPath $definitionPath -PathType Leaf)) {
    throw "MIX 08:00 definition not found: $definitionPath"
}

function Read-Definition {
    try {
        return Get-Content -LiteralPath $definitionPath -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        throw "MIX 08:00 definition is not valid JSON: $definitionPath"
    }
}

function Assert-Definition {
    param([Parameter(Mandatory=$true)]$Definition)

    if ([string]$Definition.schema_version -cne 'mix_telegram_weekday_schedule_definition_v1') { throw 'MIX schedule definition schema is invalid' }
    if ([string]$Definition.task_name -cne $taskName -or [string]$Definition.timezone -cne $timeZoneId) { throw 'MIX schedule task identity/timezone contract is invalid' }
    $expectedDays = @('Monday','Tuesday','Wednesday','Thursday','Friday')
    if ((@($Definition.trigger.days_of_week) -join ',') -cne ($expectedDays -join ',') -or
        [string]$Definition.trigger.start_time -cne '08:00' -or
        $Definition.trigger.start_when_available -ne $true) { throw 'MIX schedule trigger contract is invalid' }

    $expectedArguments = '/d /c "set NO_PAUSE=1&&call D:\stock\programs\telegram_report_delivery\RUN_SEND_MIX_REPORT.cmd --max-wait-seconds 0 --poll-seconds 60"'
    if ([string]$Definition.action.executable -notmatch '(?i)\\cmd\.exe$' -or
        [string]$Definition.action.arguments -cne $expectedArguments -or
        [string]$Definition.action.working_directory -ne $suite -or
        $Definition.action.no_pause -ne $true -or
        [int]$Definition.action.max_wait_seconds -ne 0) {
        throw 'MIX schedule action contract is invalid'
    }
    $arguments = [string]$Definition.action.arguments
    if ($arguments -match '(?i)D-stock|RUN_SCHEDULED_SEND|RUN_SEND_LATEST_REPORT|ensure|refresh|update|allow-network' -or
        $arguments -notmatch '(?i)RUN_SEND_MIX_REPORT\.cmd') {
        throw 'MIX schedule action is not the formal-only MIX sender'
    }

    $settings = $Definition.settings
    if ([string]$settings.multiple_instances -cne 'IgnoreNew' -or
        [int]$settings.execution_time_limit_seconds -ne 0 -or
        $settings.allow_start_if_on_batteries -ne $true -or
        $settings.dont_stop_if_going_on_batteries -ne $true -or
        $settings.dont_stop_on_idle_end -ne $true) {
        throw 'MIX schedule PT0S/battery/idle settings are invalid'
    }

    $publication = $Definition.publication_contract
    if ([string]$publication.source -cne 'D:\TWStockData\program_data\mix\reports\LATEST_C0_RUN.json' -or
        [string]$publication.required_pointer_schema -cne 'mix-stock-daily-c0-current-report-v1' -or
        [string]$publication.required_status -cne 'limited_baseline_effect_unverified' -or
        $publication.required_official -ne $true -or
        $publication.required_publishable -ne $false -or
        $publication.publishable_is_quality_indicator -ne $true -or
        [string]$publication.quality_conditions_action -cne 'include_unmet_conditions_without_blocking' -or
        $publication.required_execution_success -ne $true -or
        $publication.required_artifact_integrity -ne $true -or
        $publication.requires_central_receipt_and_registry -ne $true -or
        [string]$publication.selection_policy -cne 'formal_only' -or
        [string]$publication.no_action_action -cne 'reject_no_action_report' -or
        [string]$publication.stale_report_action -cne 'send_with_report_and_latest_data_days_labeled' -or
        [string]$publication.integrity_failure_action -cne 'exit_nonzero_fail_closed_no_send' -or
        @($publication.PSObject.Properties.Name) -contains 'stale_or_integrity_failure_action' -or
        @($publication.PSObject.Properties.Name) -contains 'requires_feedback_publication_receipt' -or
        [string]$publication.formal_unavailable_action -cne 'exit_nonzero_no_fallback' -or
        [string]$publication.formal_send_failure_action -cne 'exit_nonzero_no_fallback') {
        throw 'MIX formal-only publication contract is invalid'
    }
}

function Resolve-PrincipalSid {
    param(
        [Parameter(Mandatory=$true)][string]$Principal,
        [Parameter(Mandatory=$true)][string]$TaskUser
    )
    if ([string]::IsNullOrWhiteSpace($Principal)) { throw 'Scheduled task principal is empty' }
    $candidates = @($Principal)
    if ($Principal -notmatch '^S-\d-' -and $Principal -notmatch '\\') {
        $domain = ($TaskUser -split '\\', 2)[0]
        if (-not [string]::IsNullOrWhiteSpace($domain)) { $candidates = @("$domain\$Principal") + $candidates }
    }
    $lastError = $null
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        try {
            if ($candidate -match '^S-\d-') {
                return (New-Object System.Security.Principal.SecurityIdentifier($candidate)).Value.ToUpperInvariant()
            }
            $account = New-Object -TypeName System.Security.Principal.NTAccount -ArgumentList $candidate
            return $account.Translate([System.Security.Principal.SecurityIdentifier]).Value.ToUpperInvariant()
        }
        catch { $lastError = $_ }
    }
    throw "Could not resolve scheduled task principal '$Principal': $($lastError.Exception.Message)"
}

function Get-CurrentIdentity {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $sid = if ($null -eq $identity.User) { '' } else { [string]$identity.User.Value }
    $name = [string]$identity.Name
    if ([string]::IsNullOrWhiteSpace($sid) -or [string]::IsNullOrWhiteSpace($name)) { throw 'Could not determine current Windows identity' }
    return [ordered]@{ sid = $sid; name = $name }
}

function Get-ExpectedAction {
    param([Parameter(Mandatory=$true)]$Definition)
    return [ordered]@{
        Execute = [string]$Definition.action.executable
        Arguments = [string]$Definition.action.arguments
        WorkingDirectory = [string]$Definition.action.working_directory
    }
}

function Test-TaskAction {
    param([Parameter(Mandatory=$true)]$Task, [Parameter(Mandatory=$true)]$Expected)
    $actions = @($Task.Actions)
    if ($actions.Count -ne 1) { return $false }
    $action = $actions[0]
    return (
        [string]::Equals([string]$action.Execute, [string]$Expected.Execute, [System.StringComparison]::OrdinalIgnoreCase) -and
        [string]::Equals([string]$action.Arguments, [string]$Expected.Arguments, [System.StringComparison]::Ordinal) -and
        [string]::Equals([string]$action.WorkingDirectory, [string]$Expected.WorkingDirectory, [System.StringComparison]::OrdinalIgnoreCase)
    )
}

function Get-TaskSettingsSnapshot {
    param([Parameter(Mandatory=$true)][string]$Name, [Parameter(Mandatory=$true)][string]$TaskPath)
    [xml]$xml = Export-ScheduledTask -TaskName $Name -TaskPath $TaskPath -ErrorAction Stop
    $settings = $xml.Task.Settings
    $idle = $settings.IdleSettings
    $multiple = $settings.MultipleInstancesPolicy
    if ($null -eq $multiple) { $multiple = $settings.MultipleInstances }
    return [ordered]@{
        execution_time_limit = [string]$settings.ExecutionTimeLimit
        disallow_start_if_on_batteries = [string]$settings.DisallowStartIfOnBatteries
        stop_if_going_on_batteries = [string]$settings.StopIfGoingOnBatteries
        start_when_available = [string]$settings.StartWhenAvailable
        multiple_instances = [string]$multiple
        stop_on_idle_end = if ($null -eq $idle) { $null } else { [string]$idle.StopOnIdleEnd }
    }
}

function Assert-TaskSettings {
    param([Parameter(Mandatory=$true)]$Snapshot)
    if ($Snapshot.execution_time_limit -ne 'PT0S') { throw "ExecutionTimeLimit must be PT0S; found $($Snapshot.execution_time_limit)" }
    if ($Snapshot.disallow_start_if_on_batteries -eq 'true' -or $Snapshot.stop_if_going_on_batteries -eq 'true') { throw 'Task must not stop or be blocked by battery state' }
    if ($Snapshot.start_when_available -ne 'true') { throw 'StartWhenAvailable must be true' }
    if ($Snapshot.multiple_instances -ne 'IgnoreNew') { throw 'MultipleInstances must be IgnoreNew' }
    if ($Snapshot.stop_on_idle_end -eq 'true') { throw 'Task must not stop at idle end' }
}

function Get-TaskTriggerSnapshot {
    param([Parameter(Mandatory=$true)][string]$Name, [Parameter(Mandatory=$true)][string]$TaskPath)
    [xml]$xml = Export-ScheduledTask -TaskName $Name -TaskPath $TaskPath -ErrorAction Stop
    $triggers = @($xml.Task.Triggers.CalendarTrigger)
    if ($triggers.Count -ne 1) { throw 'Task must have exactly one weekly calendar trigger' }
    $weekly = $triggers[0].ScheduleByWeek
    if ($null -eq $weekly) { throw 'Task trigger must use a weekly schedule' }
    return [ordered]@{
        days = @($weekly.DaysOfWeek.ChildNodes | ForEach-Object { [string]$_.LocalName })
        weeks_interval = [int]$weekly.WeeksInterval
        start_boundary = [string]$triggers[0].StartBoundary
    }
}

function Assert-TaskTrigger {
    param([Parameter(Mandatory=$true)]$Snapshot)
    $expectedDays = @('Monday','Tuesday','Wednesday','Thursday','Friday')
    if ($Snapshot.weeks_interval -ne 1 -or (@($Snapshot.days) -join ',') -cne ($expectedDays -join ',')) { throw 'Task trigger must be weekdays every one week' }
    $boundary = [datetime]::Parse($Snapshot.start_boundary, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AssumeLocal)
    if ($boundary.Hour -ne 8 -or $boundary.Minute -ne 0) { throw "Task trigger must start at 08:00; found $($boundary.ToString('HH:mm'))" }
}

function Get-TaskContractSnapshot {
    param([Parameter(Mandatory=$true)]$Task, [Parameter(Mandatory=$true)]$Expected, [Parameter(Mandatory=$true)][string]$CurrentSid, [Parameter(Mandatory=$true)][string]$TaskUser)
    if ([string]$Task.TaskPath -ne $taskPath) { throw "TaskPath mismatch; refusing to overwrite: $taskName" }
    $existingPrincipal = [string]$Task.Principal.UserId
    $existingSid = Resolve-PrincipalSid -Principal $existingPrincipal -TaskUser $TaskUser
    if (-not [string]::Equals($existingSid, $CurrentSid, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Task owner SID mismatch; refusing to overwrite: $taskName" }
    if (-not (Test-TaskAction -Task $Task -Expected $Expected)) { throw "Task action mismatch; refusing to overwrite: $taskName" }
    $settings = Get-TaskSettingsSnapshot -Name $taskName -TaskPath $taskPath
    Assert-TaskSettings -Snapshot $settings
    $trigger = Get-TaskTriggerSnapshot -Name $taskName -TaskPath $taskPath
    Assert-TaskTrigger -Snapshot $trigger
    return [ordered]@{
        TaskName = [string]$Task.TaskName
        TaskPath = [string]$Task.TaskPath
        State = [string]$Task.State
        User = $existingPrincipal
        PrincipalSid = $existingSid
        Action = [string]$Task.Actions.Execute
        Arguments = [string]$Task.Actions.Arguments
        WorkingDirectory = [string]$Task.Actions.WorkingDirectory
        Settings = $settings
        Trigger = $trigger
    }
}

$definition = Read-Definition
Assert-Definition -Definition $definition
$expected = Get-ExpectedAction -Definition $definition
$timeZone = Get-TimeZone
if ($timeZone.Id -ne $timeZoneId) { throw "Windows time zone must be Taipei Standard Time; current value is $($timeZone.Id)" }

if ($Plan) {
    [ordered]@{
        Mode = 'plan_only'
        TaskName = $taskName
        TaskPath = $taskPath
        Definition = $definitionPath
        TimeZone = $timeZone.Id
        Schedule = 'Monday-Friday 08:00'
        Action = $expected.Execute
        Arguments = $expected.Arguments
        WorkingDirectory = $expected.WorkingDirectory
        ExecutionTimeLimit = 'PT0S'
        MultipleInstances = 'IgnoreNew'
        StartWhenAvailable = $true
        AllowStartIfOnBatteries = $true
        DontStopIfGoingOnBatteries = $true
        StopOnIdleEnd = $false
        Registration = 'requires -Register -ConfirmRegister'
    } | ConvertTo-Json -Depth 8
    exit 0
}

$identity = Get-CurrentIdentity
$existing = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    $snapshot = Get-TaskContractSnapshot -Task $existing -Expected $expected -CurrentSid $identity.sid -TaskUser $identity.name
    if ($Verify) {
        $snapshot | ConvertTo-Json -Depth 10
        exit 0
    }
    if ($Register) {
        [ordered]@{
            Mode = 'already_present'
            TaskName = $taskName
            TaskPath = $taskPath
            State = $snapshot.State
            Contract = $snapshot
            Message = 'Existing MIX task already matches the definition; no overwrite or immediate start was performed.'
        } | ConvertTo-Json -Depth 12
        exit 0
    }
}
elseif ($Verify) {
    throw "Scheduled task is not registered: $taskName"
}

if (-not $Register) { exit 0 }

$action = New-ScheduledTaskAction -Execute $expected.Execute -Argument $expected.Arguments -WorkingDirectory $expected.WorkingDirectory
$trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At ([datetime]::Today.AddHours(8))
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -DontStopOnIdleEnd -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId $identity.name -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -TaskPath $taskPath -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description ([string]$definition.purpose) -ErrorAction Stop | Out-Null

$registered = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction Stop
$registeredSnapshot = Get-TaskContractSnapshot -Task $registered -Expected $expected -CurrentSid $identity.sid -TaskUser $identity.name
$registeredSnapshot | ConvertTo-Json -Depth 10
