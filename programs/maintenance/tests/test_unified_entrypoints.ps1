[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$maintenance = Split-Path -Parent $PSScriptRoot
$programs = Split-Path -Parent $maintenance
$menuPath = Join-Path $programs 'STOCK_TOOLS.cmd'
$manifestPath = Join-Path $programs 'STABLE_PROGRAM_PATHS.txt'
$failures = New-Object System.Collections.Generic.List[string]

function Assert-Contract {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { $failures.Add($Message) }
}

Assert-Contract (Test-Path -LiteralPath $menuPath -PathType Leaf) 'unified_menu_missing'
Assert-Contract (Test-Path -LiteralPath $manifestPath -PathType Leaf) 'stable_manifest_missing'

$menu = Get-Content -LiteralPath $menuPath -Raw -Encoding UTF8
$manifestLines = @(
    Get-Content -LiteralPath $manifestPath -Encoding UTF8 |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -and -not $_.StartsWith('#') }
)

$targetValues = @(
    [regex]::Matches($menu, '(?im)^\s*set "TARGET=([^"]+)"') |
        ForEach-Object { $_.Groups[1].Value.Trim() }
)
$duplicateTargets = @($targetValues | Group-Object | Where-Object Count -gt 1)
Assert-Contract ($targetValues.Count -gt 0) 'menu_has_no_canonical_targets'
Assert-Contract ($duplicateTargets.Count -eq 0) 'menu_target_reused_for_multiple_actions'

foreach ($requiredLabel in @(
    'D-Stock',
    'Central',
    'MIX ALL',
    'Scheduler',
    'Telegram',
    'M1.',
    'pointer'
)) {
    Assert-Contract ($menu.Contains($requiredLabel)) ("menu_label_missing:{0}" -f $requiredLabel)
}

# The unified maintenance menu must not smuggle excluded twmix/Premarket/day-trading
# workflows into the canonical one-click path.
Assert-Contract ($menu -notmatch '(?i)twmix|premarket|daytrade') 'excluded_workflow_in_unified_menu'

$requiredStablePaths = @(
    'programs\STOCK_TOOLS.cmd',
    'programs\VERIFY_STABLE_PROGRAM_PATHS.cmd',
    'programs\maintenance\PLAN_CLEANUP.ps1',
    'programs\maintenance\APPLY_CLEANUP.ps1',
    'programs\maintenance\tests\test_cleanup_contract.ps1'
)
foreach ($requiredPath in $requiredStablePaths) {
    Assert-Contract ($manifestLines -contains $requiredPath) ("stable_path_missing:{0}" -f $requiredPath)
}

$duplicateManifestPaths = @($manifestLines | Group-Object | Where-Object Count -gt 1)
Assert-Contract ($duplicateManifestPaths.Count -eq 0) 'duplicate_stable_manifest_path'
Assert-Contract (
    @($manifestLines | Where-Object { $_ -match '(?i)(^|\\)(?:19|20)\d{6}(?:\\|[._-])' }).Count -eq 0
) 'dateified_stable_path_present'

if ($failures.Count -gt 0) {
    $failures | ForEach-Object { Write-Error $_ }
    exit 1
}

Write-Output ('PASS unified_entrypoint_contracts={0} targets={1} stable_paths={2}' -f 7, $targetValues.Count, $manifestLines.Count)
exit 0
