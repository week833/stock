[CmdletBinding()]
param(
    [switch]$Remove,
    [switch]$ConfirmRemove
)
# Removes only the tasks listed in PREOPEN_SCHEDULE_DEFINITION.json (never other tasks).
#   default                : list which of the defined tasks currently exist (no changes)
#   -Remove -ConfirmRemove : unregister them

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$suite = (Resolve-Path -LiteralPath (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path
$definitionPath = Join-Path $suite 'PREOPEN_SCHEDULE_DEFINITION.json'
$taskPath = '\'
if ($ConfirmRemove -and -not $Remove) { throw '-ConfirmRemove is valid only with -Remove' }
if ($Remove -and -not $ConfirmRemove) { throw 'Removal is disabled by default; rerun with both -Remove -ConfirmRemove' }

$definition = Get-Content -LiteralPath $definitionPath -Raw -Encoding UTF8 | ConvertFrom-Json
$results = @()
foreach ($task in @($definition.tasks)) {
    $name = [string]$task.task_name
    $existing = Get-ScheduledTask -TaskName $name -TaskPath $taskPath -ErrorAction SilentlyContinue
    if ($null -eq $existing) { $results += [pscustomobject][ordered]@{ TaskName = $name; Status = 'absent' }; continue }
    if (-not $Remove) { $results += [pscustomobject][ordered]@{ TaskName = $name; Status = 'present'; State = [string]$existing.State }; continue }
    Unregister-ScheduledTask -TaskName $name -TaskPath $taskPath -Confirm:$false -ErrorAction Stop
    $results += [pscustomobject][ordered]@{ TaskName = $name; Status = 'removed' }
}
[ordered]@{ Mode = $(if ($Remove) { 'remove' } else { 'list_only' }); Results = @($results) } | ConvertTo-Json -Depth 6
