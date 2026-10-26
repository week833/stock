[CmdletBinding()]
param(
    [switch]$VerifyOnly
)

$ErrorActionPreference = "Stop"
$suite = Split-Path -Parent $MyInvocation.MyCommand.Path
$definitionPath = Join-Path $suite "MIX_WEEKDAY_0800_TASK_DEFINITION.json"
$runner = Join-Path $suite "RUN_SEND_MIX_REPORT.cmd"
$contract = Get-Content -LiteralPath $definitionPath -Raw -Encoding UTF8 | ConvertFrom-Json
$schema = [string]($contract.schema_version)
if ($schema -ne 'mix_telegram_weekday_schedule_definition_v1') {
    throw 'MIX schedule definition schema is invalid'
}
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    throw 'MIX scheduled entrypoint is missing'
}
$separator = [System.IO.Path]::DirectorySeparatorChar
$suiteFull = [System.IO.Path]::GetFullPath($suite).TrimEnd($separator)
$workingDirectory = [string]($contract.action.working_directory)
$workingFull = [System.IO.Path]::GetFullPath($workingDirectory).TrimEnd($separator)
if ($workingFull -ne $suiteFull) {
    throw 'MIX schedule working directory does not match sender suite'
}
$expectedSource = 'D:\TWStockData\program_data\mix\reports\LATEST_C0_RUN.json'
$actualSource = [string]($contract.publication_contract.source)
if ($actualSource -ne $expectedSource) {
    throw 'MIX schedule source is not the canonical production pointer'
}
$maxWaitSeconds = [int]($contract.action.max_wait_seconds)
$pollSeconds = [int]($contract.action.poll_seconds)
if ($maxWaitSeconds -ne 0 -or [int]($contract.settings.execution_time_limit_seconds) -ne 0 -or $pollSeconds -lt 30) {
    throw 'MIX schedule must have an unlimited PT0S execution limit and bounded readiness polling'
}
if ([string]$contract.task_name -cne 'MIX Telegram Report 08-00' -or [string]$contract.timezone -cne 'Taipei Standard Time') {
    throw 'MIX schedule task identity/timezone contract is invalid'
}
$expectedDays = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
if ((@($contract.trigger.days_of_week) -join ',') -cne ($expectedDays -join ',') -or [string]$contract.trigger.start_time -cne '08:00' -or $contract.trigger.start_when_available -ne $true) {
    throw 'MIX schedule trigger contract is invalid'
}
$settings = $contract.settings
if ([string]$settings.multiple_instances -cne 'IgnoreNew' -or $settings.allow_start_if_on_batteries -ne $true -or $settings.dont_stop_if_going_on_batteries -ne $true -or $settings.dont_stop_on_idle_end -ne $true) {
    throw 'MIX schedule battery/idle/multiple-instance settings are invalid'
}
if ([string]$contract.action.executable -notmatch '(?i)\\cmd\.exe$' -or $contract.action.no_pause -ne $true -or [string]$contract.action.arguments -ne '/d /c "set NO_PAUSE=1&&call D:\stock\programs\telegram_report_delivery\RUN_SEND_MIX_REPORT.cmd --max-wait-seconds 0 --poll-seconds 60"') {
    throw 'MIX schedule action contract is invalid'
}
$publication = $contract.publication_contract
if ([string]$publication.source -cne $expectedSource -or [string]$publication.required_pointer_schema -cne 'mix-stock-daily-c0-current-report-v1' -or [string]$publication.required_status -cne 'limited_baseline_effect_unverified' -or $publication.required_official -ne $true -or $publication.required_publishable -ne $false -or $publication.publishable_is_quality_indicator -ne $true -or $publication.required_execution_success -ne $true -or $publication.required_artifact_integrity -ne $true -or $publication.requires_central_receipt_and_registry -ne $true -or [string]$publication.selection_policy -cne 'formal_only' -or [string]$publication.quality_conditions_action -cne 'include_unmet_conditions_without_blocking' -or [string]$publication.stale_report_action -cne 'send_with_report_and_latest_data_days_labeled' -or [string]$publication.integrity_failure_action -cne 'exit_nonzero_fail_closed_no_send' -or @($publication.PSObject.Properties.Name) -contains 'stale_or_integrity_failure_action' -or @($publication.PSObject.Properties.Name) -contains 'requires_feedback_publication_receipt' -or [string]$publication.formal_unavailable_action -cne 'exit_nonzero_no_fallback' -or [string]$publication.formal_send_failure_action -cne 'exit_nonzero_no_fallback' -or [string]$publication.no_action_action -cne 'reject_no_action_report') {
    throw 'MIX schedule publication contract is invalid'
}

[ordered]@{
    Ready = $true
    VerifyOnly = [bool]$VerifyOnly
    TaskName = [string]$contract.task_name
    TimeZone = [string]$contract.timezone
    Days = @($contract.trigger.days_of_week)
    StartTime = [string]$contract.trigger.start_time
    Action = [string]$contract.action.arguments
    WorkingDirectory = $workingDirectory
    SourcePointer = $actualSource
    ExecutionTimeLimitSeconds = [int]$contract.settings.execution_time_limit_seconds
    MultipleInstances = [string]$settings.multiple_instances
    AllowStartIfOnBatteries = [bool]$settings.allow_start_if_on_batteries
    DontStopIfGoingOnBatteries = [bool]$settings.dont_stop_if_going_on_batteries
    DontStopOnIdleEnd = [bool]$settings.dont_stop_on_idle_end
    Registered = $false
} | ConvertTo-Json -Depth 6
