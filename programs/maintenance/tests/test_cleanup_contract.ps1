[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$maintenance = Split-Path -Parent $PSScriptRoot
$failures = New-Object System.Collections.Generic.List[string]

function Assert-Contract {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { $failures.Add($Message) }
}

$common = Get-Content -LiteralPath (Join-Path $maintenance 'CLEANUP_COMMON.ps1') -Raw -Encoding UTF8
$plan = Get-Content -LiteralPath (Join-Path $maintenance 'PLAN_CLEANUP.ps1') -Raw -Encoding UTF8
$apply = Get-Content -LiteralPath (Join-Path $maintenance 'APPLY_CLEANUP.ps1') -Raw -Encoding UTF8
$schema = Get-Content -LiteralPath (Join-Path $maintenance 'cleanup_plan.schema.json') -Raw -Encoding UTF8 | ConvertFrom-Json

Assert-Contract ($common -match "stock-cleanup-plan-v1") 'common_schema_constant_missing'
Assert-Contract ($common -match 'D:\\TWStockData\\services\\market_update\\staging') 'central_staging_root_missing'
Assert-Contract ($common -match 'D:\\MIX All') 'mix_root_missing'
Assert-Contract ($common -match 'D:\\cowork') 'cowork_root_missing'
Assert-Contract ($common -match 'D:\\TWStockData\\shared_market_data') 'canonical_shared_data_exclusion_missing'
Assert-Contract ($common -match 'D:\\claude code\\twmix') 'twmix_exclusion_missing'
Assert-Contract ($common -match 'node_modules') 'node_modules_exclusion_missing'
Assert-Contract ($common -match 'venv') 'venv_exclusion_missing'
Assert-Contract ($common -match "'.venv'") 'dot_venv_exclusion_missing'
Assert-Contract ($common -match 'python\\.recover-backup') 'python_recover_backup_exclusion_missing'
Assert-Contract ($common -match 'status --porcelain') 'git_dirty_status_check_missing'
Assert-Contract ($common -match 'git_dirty_or_untracked') 'git_dirty_untracked_block_missing'
Assert-Contract ($common -match 'canonical_shared_market_data') 'canonical_shared_data_reason_missing'
Assert-Contract ($common -notmatch 'Remove-Item') 'common_must_not_delete'
Assert-Contract ($apply -match 'ConfirmApply') 'apply_confirmation_missing'
Assert-Contract ($apply -match 'Test-CurrentRunMatchesSnapshot') 'active_run_drift_check_missing'
Assert-Contract ($apply -match 'Test-NoActiveCentralRun') 'active_run_block_missing'
Assert-Contract ($apply -match 'Test-AcceptanceMatchesSnapshot') 'acceptance_drift_check_missing'
Assert-Contract ($apply -match 'Remove-Item -LiteralPath') 'literal_delete_missing'
Assert-Contract ($apply -notmatch 'Remove-Item\s+[^\r\n]*-Recurse') 'recursive_delete_forbidden'
Assert-Contract ($apply -notmatch 'Remove-Item\s+[^\r\n]*-Path\s') 'wildcard_delete_forbidden'
Assert-Contract ($plan -match "mode = 'preview'") 'preview_default_missing'
Assert-Contract ($plan -match 'Assert-PlanOutputPath') 'plan_output_boundary_missing'
Assert-Contract ($plan -match 'active_run_excluded') 'active_staging_exclusion_missing'
Assert-Contract ($plan -match 'failure_evidence_missing') 'central_failure_evidence_gate_missing'
Assert-Contract ($plan -match 'formal_receipt_present') 'central_receipt_gate_missing'
Assert-Contract ($plan -match 'A-exact-duplicate') 'duplicate_category_missing'
Assert-Contract ($plan -match 'A-damaged-superseded') 'supersession_category_missing'
Assert-Contract ($plan -match 'targets = @\(\$targets\.ToArray\(\)\)') 'plan_generic_target_list_conversion_missing'
Assert-Contract ($plan -match 'skipped = @\(\$skipped\.ToArray\(\)\)') 'plan_generic_skipped_list_conversion_missing'
Assert-Contract ($apply -match 'blockers = @\(\$blockers\.ToArray\(\)\)') 'apply_generic_blocker_list_conversion_missing'
Assert-Contract ($apply -match 'deleted = @\(\$deleted\.ToArray\(\)\)') 'apply_generic_deleted_list_conversion_missing'
Assert-Contract ($apply -match 'skipped = @\(\$skipped\.ToArray\(\)\)') 'apply_generic_skipped_list_conversion_missing'
Assert-Contract ($schema.properties.targets.items.'$ref' -eq '#/$defs/target') 'schema_target_definition_missing'
Assert-Contract ($schema.'$defs'.target.properties.kind.enum -contains 'directory') 'directory_target_schema_missing'
Assert-Contract ($schema.'$defs'.target.properties.category.enum -contains 'A-central-failed-staging') 'central_category_schema_missing'

if ($failures.Count -gt 0) {
    $failures | ForEach-Object { Write-Error $_ }
    exit 1
}
Write-Output ('PASS cleanup_contract_static_assertions={0}' -f 36)
exit 0
