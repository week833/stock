[CmdletBinding()]
param(
    [switch]$Plan,
    [switch]$Register,
    [switch]$ConfirmRegister,
    [switch]$Verify
)
# Registers the pre-open daily schedule (ADR-0008) defined in PREOPEN_SCHEDULE_DEFINITION.json.
#   default / -Plan            : print what would be registered (no changes)
#   -Register -ConfirmRegister : register the tasks for the current user (Interactive, Limited)
#   -Verify                    : check every task exists and matches the definition
# Existing tasks with the same name are never overwritten: a matching task is reported as
# already_present, a differing one makes the run fail. Same conventions as
# telegram_report_delivery\INSTALL_MIX_WEEKDAY_0800_TASK.ps1. ASCII only on purpose; all
# human-readable text lives in the UTF-8 definition file.

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$suite = (Resolve-Path -LiteralPath (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path
$definitionPath = Join-Path $suite 'PREOPEN_SCHEDULE_DEFINITION.json'
$taskPath = '\'
$timeZoneId = 'Taipei Standard Time'
$expectedDays = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')

$modeCount = @(
    if ($Plan) { 'Plan' }
    if ($Register) { 'Register' }
    if ($Verify) { 'Verify' }
).Count
if ($modeCount -gt 1) { throw 'Choose only one of -Plan, -Register, or -Verify' }
if ($ConfirmRegister -and -not $Register) { throw '-ConfirmRegister is valid only with -Register' }
if ($Register -and -not $ConfirmRegister) { throw 'Registration is disabled by default; rerun with both -Register -ConfirmRegister' }
if (-not $Plan -and -not $Register -and -not $Verify) { $Plan = $true }
if (-not (Test-Path -LiteralPath $definitionPath -PathType Leaf)) { throw "Definition not found: $definitionPath" }

function Read-Definition {
    try { return Get-Content -LiteralPath $definitionPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "Definition is not valid JSON: $definitionPath" }
}

function Assert-Definition {
    param([Parameter(Mandatory = $true)]$Definition)
    if ([string]$Definition.schema_version -cne 'preopen_daily_schedule_definition_v1') { throw 'Definition schema is invalid' }
    if ([string]$Definition.timezone -cne $timeZoneId) { throw 'Definition timezone must be Taipei Standard Time' }
    if ((@($Definition.common_settings.days_of_week) -join ',') -cne ($expectedDays -join ',')) { throw 'Definition days_of_week must be Monday..Friday' }
    if ([string]$Definition.common_settings.multiple_instances -cne 'IgnoreNew') { throw 'Definition multiple_instances must be IgnoreNew' }
    $tasks = @($Definition.tasks)
    if ($tasks.Count -lt 1) { throw 'Definition has no tasks' }
    $names = @{}
    foreach ($task in $tasks) {
        foreach ($field in @('task_name', 'start_time', 'executable', 'arguments', 'working_directory', 'execution_time_limit')) {
            if ([string]::IsNullOrWhiteSpace([string]$task.$field)) { throw "Task field '$field' is empty in definition" }
        }
        if ($names.ContainsKey([string]$task.task_name)) { throw "Duplicate task name: $($task.task_name)" }
        $names[[string]$task.task_name] = $true
        if ([string]$task.start_time -notmatch '^\d{2}:\d{2}$') { throw "start_time must be HH:mm: $($task.task_name)" }
        if ([string]$task.execution_time_limit -notmatch '^PT\d+[HM]$') { throw "execution_time_limit must be ISO 8601 (PTnH/PTnM): $($task.task_name)" }
        if (-not (Test-Path -LiteralPath ([string]$task.executable) -PathType Leaf)) { throw "Executable not found: $($task.executable)" }
        if (-not (Test-Path -LiteralPath ([string]$task.working_directory) -PathType Container)) { throw "Working directory not found: $($task.working_directory)" }
        # every quoted or bare .ps1/.cmd path inside the arguments must exist
        foreach ($match in [regex]::Matches([string]$task.arguments, '[A-Za-z]:\\[^"\s]+\.(?:ps1|cmd)')) {
            if (-not (Test-Path -LiteralPath $match.Value -PathType Leaf)) { throw "Script referenced by task arguments not found: $($match.Value)" }
        }
    }
}

function Get-CurrentIdentity {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $sid = if ($null -eq $identity.User) { '' } else { [string]$identity.User.Value }
    if ([string]::IsNullOrWhiteSpace($sid) -or [string]::IsNullOrWhiteSpace([string]$identity.Name)) { throw 'Could not determine current Windows identity' }
    return [ordered]@{ sid = $sid; name = [string]$identity.Name }
}

function Resolve-PrincipalSid {
    param([Parameter(Mandatory = $true)][string]$Principal, [Parameter(Mandatory = $true)][string]$TaskUser)
    $candidates = @($Principal)
    if ($Principal -notmatch '^S-\d-' -and $Principal -notmatch '\\') {
        $domain = ($TaskUser -split '\\', 2)[0]
        if (-not [string]::IsNullOrWhiteSpace($domain)) { $candidates = @("$domain\$Principal") + $candidates }
    }
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        try {
            if ($candidate -match '^S-\d-') { return (New-Object System.Security.Principal.SecurityIdentifier($candidate)).Value.ToUpperInvariant() }
            return (New-Object System.Security.Principal.NTAccount($candidate)).Translate([System.Security.Principal.SecurityIdentifier]).Value.ToUpperInvariant()
        }
        catch { }
    }
    throw "Could not resolve scheduled task principal '$Principal'"
}

function Get-StartBoundary {
    param([Parameter(Mandatory = $true)][string]$StartTime)
    $parts = $StartTime -split ':'
    return [datetime]::Today.AddHours([int]$parts[0]).AddMinutes([int]$parts[1])
}

function Test-TaskMatches {
    param([Parameter(Mandatory = $true)]$Existing, [Parameter(Mandatory = $true)]$Spec, [Parameter(Mandatory = $true)][string]$CurrentSid, [Parameter(Mandatory = $true)][string]$TaskUser)
    $problems = @()
    if ([string]$Existing.TaskPath -ne $taskPath) { $problems += 'task path differs' }
    $existingSid = Resolve-PrincipalSid -Principal ([string]$Existing.Principal.UserId) -TaskUser $TaskUser
    if (-not [string]::Equals($existingSid, $CurrentSid, [System.StringComparison]::OrdinalIgnoreCase)) { $problems += 'owner differs' }
    $actions = @($Existing.Actions)
    if ($actions.Count -ne 1) { $problems += 'action count differs' }
    else {
        $a = $actions[0]
        if (-not [string]::Equals([string]$a.Execute, [string]$Spec.executable, [System.StringComparison]::OrdinalIgnoreCase)) { $problems += 'executable differs' }
        if (-not [string]::Equals([string]$a.Arguments, [string]$Spec.arguments, [System.StringComparison]::Ordinal)) { $problems += 'arguments differ' }
        if (-not [string]::Equals([string]$a.WorkingDirectory, [string]$Spec.working_directory, [System.StringComparison]::OrdinalIgnoreCase)) { $problems += 'working directory differs' }
    }
    [xml]$xml = Export-ScheduledTask -TaskName $Existing.TaskName -TaskPath $taskPath -ErrorAction Stop
    $trigger = @($xml.Task.Triggers.CalendarTrigger)
    if ($trigger.Count -ne 1 -or $null -eq $trigger[0].ScheduleByWeek) { $problems += 'trigger is not a single weekly trigger' }
    else {
        $days = @($trigger[0].ScheduleByWeek.DaysOfWeek.ChildNodes | ForEach-Object { [string]$_.LocalName })
        if (($days -join ',') -cne ($expectedDays -join ',')) { $problems += 'trigger days differ' }
        $boundary = [datetime]::Parse([string]$trigger[0].StartBoundary, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AssumeLocal)
        if ($boundary.ToString('HH:mm') -ne [string]$Spec.start_time) { $problems += "start time differs ($($boundary.ToString('HH:mm')))" }
    }
    $settings = $xml.Task.Settings
    if ([string]$settings.ExecutionTimeLimit -ne [string]$Spec.execution_time_limit) { $problems += "execution time limit differs ($($settings.ExecutionTimeLimit))" }
    if ([string]$settings.StartWhenAvailable -ne 'true') { $problems += 'StartWhenAvailable is not true' }
    if ([string]$settings.WakeToRun -ne 'true') { $problems += 'WakeToRun is not true' }
    $multiple = $settings.MultipleInstancesPolicy
    if ($null -eq $multiple) { $multiple = $settings.MultipleInstances }
    if ([string]$multiple -ne 'IgnoreNew') { $problems += 'MultipleInstances is not IgnoreNew' }
    return $problems
}

$definition = Read-Definition
Assert-Definition -Definition $definition
$timeZone = Get-TimeZone
if ($timeZone.Id -ne $timeZoneId) { throw "Windows time zone must be Taipei Standard Time; current value is $($timeZone.Id)" }
$identity = Get-CurrentIdentity
$tasks = @($definition.tasks)

if ($Plan) {
    $rows = foreach ($task in $tasks) {
        [ordered]@{
            TaskName = [string]$task.task_name
            Schedule = ('Monday-Friday {0}' -f $task.start_time)
            Executable = [string]$task.executable
            Arguments = [string]$task.arguments
            WorkingDirectory = [string]$task.working_directory
            ExecutionTimeLimit = [string]$task.execution_time_limit
            Description = [string]$task.description
            Existing = if ($null -ne (Get-ScheduledTask -TaskName ([string]$task.task_name) -TaskPath $taskPath -ErrorAction SilentlyContinue)) { 'yes' } else { 'no' }
        }
    }
    [ordered]@{
        Mode = 'plan_only'
        Definition = $definitionPath
        TimeZone = $timeZone.Id
        RunAs = $identity.name
        LogonType = 'Interactive'
        RunLevel = 'Limited'
        CommonSettings = $definition.common_settings
        Tasks = @($rows)
        ExternalTasks = $definition.external_tasks
        DoNotRegister = $definition.do_not_register
        Registration = 'requires -Register -ConfirmRegister'
    } | ConvertTo-Json -Depth 8
    exit 0
}

$results = @()
$failures = 0
foreach ($task in $tasks) {
    $name = [string]$task.task_name
    $existing = Get-ScheduledTask -TaskName $name -TaskPath $taskPath -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        $problems = @(Test-TaskMatches -Existing $existing -Spec $task -CurrentSid $identity.sid -TaskUser $identity.name)
        if ($problems.Count -gt 0) {
            $failures++
            $results += [pscustomobject][ordered]@{ TaskName = $name; Status = 'mismatch_refusing_to_overwrite'; Problems = @($problems) }
            continue
        }
        $results += [pscustomobject][ordered]@{ TaskName = $name; Status = 'already_present'; State = [string]$existing.State }
        continue
    }
    if ($Verify) {
        $failures++
        $results += [pscustomobject][ordered]@{ TaskName = $name; Status = 'not_registered' }
        continue
    }
    $action = New-ScheduledTaskAction -Execute ([string]$task.executable) -Argument ([string]$task.arguments) -WorkingDirectory ([string]$task.working_directory)
    $trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At (Get-StartBoundary -StartTime ([string]$task.start_time))
    $limit = [System.Xml.XmlConvert]::ToTimeSpan([string]$task.execution_time_limit)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -DontStopOnIdleEnd -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -ExecutionTimeLimit $limit
    $principal = New-ScheduledTaskPrincipal -UserId $identity.name -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $name -TaskPath $taskPath -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description ([string]$task.description) -ErrorAction Stop | Out-Null
    $registered = Get-ScheduledTask -TaskName $name -TaskPath $taskPath -ErrorAction Stop
    $problems = @(Test-TaskMatches -Existing $registered -Spec $task -CurrentSid $identity.sid -TaskUser $identity.name)
    if ($problems.Count -gt 0) { $failures++ }
    $status = if ($problems.Count -eq 0) { 'registered' } else { 'registered_with_mismatch' }
    $results += [pscustomobject][ordered]@{ TaskName = $name; Status = $status; State = [string]$registered.State; Problems = @($problems) }
}

[ordered]@{
    Mode = $(if ($Verify) { 'verify' } else { 'register' })
    RunAs = $identity.name
    Results = @($results)
    Failures = $failures
} | ConvertTo-Json -Depth 8
if ($failures -gt 0) { exit 1 }
exit 0
