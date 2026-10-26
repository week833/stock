[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PlanPath,
    [switch]$ConfirmApply,
    [string]$ReceiptPath
)

. (Join-Path $PSScriptRoot 'CLEANUP_COMMON.ps1')

if (-not $ConfirmApply) { throw 'apply_requires_ConfirmApply' }
$planFile = Get-CanonicalPath $PlanPath
if (-not (Test-PathWithinRoot -Path $planFile -Root $script:MaintenanceRoot) -or $planFile -notmatch '(?i)\\plans\\') { throw 'plan_must_be_under_maintenance_plans' }
$plan = Read-JsonFile $planFile
if ($null -eq $plan) { throw 'plan_json_unreadable' }
Assert-PlanObjectShape $plan | Out-Null

$receiptFile = if ($ReceiptPath) {
    Assert-PlanOutputPath $ReceiptPath
} else {
    Join-Path $script:MaintenanceRoot ('receipts\cleanup_receipt_{0}.json' -f ([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')))
}

$started = [DateTime]::UtcNow
$deleted = New-Object System.Collections.Generic.List[object]
$skipped = New-Object System.Collections.Generic.List[object]
$blockers = New-Object System.Collections.Generic.List[string]
$plannedBytes = [int64]$plan.summary.target_bytes

function Add-Blocked {
    param([string]$Reason)
    if (-not $blockers.Contains($Reason)) { $blockers.Add($Reason) }
}

function New-ReceiptObject {
    param([string]$Status)
    $bytesDeleted = [int64](($deleted | Measure-Object -Property bytes -Sum).Sum)
    [ordered]@{
        schema = 'stock-cleanup-receipt-v1'
        tool_version = $script:CleanupToolVersion
        status = $Status
        plan_path = $planFile
        plan_sha256 = [string]$plan.plan_sha256
        started_at_utc = $started.ToString('o')
        finished_at_utc = [DateTime]::UtcNow.ToString('o')
        acceptance = Get-AcceptanceSnapshot
        active_run = Get-ActiveRunSnapshot
        blockers = @($blockers.ToArray())
        planned_target_count = @($plan.targets).Count
        planned_bytes = $plannedBytes
        deleted = @($deleted.ToArray())
        skipped = @($skipped.ToArray())
        bytes_deleted = $bytesDeleted
        recoverability = if ($bytesDeleted -gt 0) { 'not_recoverable_deleted_files_sha256_recorded' } else { 'none_deleted' }
    }
}

function Write-ReceiptAndStop {
    param([string]$Status, [int]$ExitCode)
    $receipt = New-ReceiptObject $Status
    Write-Utf8NoBom -Path $receiptFile -Text (($receipt | ConvertTo-Json -Depth 100) + [Environment]::NewLine)
    Write-Output ("CLEANUP_{0} receipt={1} deleted={2} skipped={3} bytes={4}" -f $Status.ToUpperInvariant(), $receiptFile, $deleted.Count, $skipped.Count, $receipt.bytes_deleted)
    exit $ExitCode
}

function Get-ExpectedRootDefinition {
    param([string]$Name)
    return @(Get-AllowlistedRoots | Where-Object { $_.name -eq $Name } | Select-Object -First 1)
}

function Test-AllowedRootDefinition {
    $expected = @(Get-AllowlistedRoots)
    $actual = @($plan.allowed_roots)
    if ($actual.Count -ne $expected.Count) { return $false }
    foreach ($root in $expected) {
        $found = @($actual | Where-Object { [string]$_.name -eq [string]$root.name })
        if ($found.Count -ne 1) { return $false }
        if ((Get-CanonicalPath ([string]$found[0].path)) -ne (Get-CanonicalPath ([string]$root.path))) { return $false }
        if ([string]$found[0].kind -ne [string]$root.kind) { return $false }
    }
    return $true
}

function Get-TargetRecordByPath {
    param([object]$Target, [string]$Path)
    return @($Target.files | Where-Object { (Get-CanonicalPath ([string]$_.path)).Equals((Get-CanonicalPath $Path), [StringComparison]::OrdinalIgnoreCase) } | Select-Object -First 1)
}

function Test-FileRecordStable {
    param(
        [Parameter(Mandatory = $true)][object]$Record,
        [switch]$AllowFormalStagingFiles
    )
    $path = Get-CanonicalPath ([string]$Record.path)
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return 'target_file_missing' }
    $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
    if ($item.Length -ne [int64]$Record.size) { return 'target_size_drift' }
    if ((Get-Sha256ForFile $path) -ne ([string]$Record.sha256).ToLowerInvariant()) { return 'target_sha256_drift' }
    $excluded = Test-ExcludedPath $path
    if ($null -ne $excluded) { return $excluded }
    if (Test-ReparsePath $path) { return 'target_reparse_point' }
    if (-not $AllowFormalStagingFiles -and $null -ne (Test-FormalProtectedPath $path)) { return 'formal_asset_protected' }
    if ($AllowFormalStagingFiles -and $path -match '(?i)[\\/]receipts?[\\/]|[\\/]evidence(?:[\\/]|$)') { return 'formal_receipt_or_evidence_path' }
    $git = Get-GitTrackingAssessment $path
    if (-not $git.known) { return 'git_status_unknown' }
    if ($git.tracked) { return 'git_tracked_program_file' }
    return $null
}

function Test-DirectoryTargetStable {
    param([Parameter(Mandatory = $true)][object]$Target)
    $directory = Get-CanonicalPath ([string]$Target.path)
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { return 'target_directory_missing' }
    if (Test-ReparsePath $directory) { return 'target_directory_reparse_point' }
    $rootDefinition = @(Get-ExpectedRootDefinition ([string]$Target.root))
    if ($rootDefinition.Count -ne 1) { return 'target_root_definition_missing' }
    if (-not (Test-PathWithinRoot -Path $directory -Root ([string]$rootDefinition[0].path))) { return 'target_directory_outside_root' }
    $expectedFiles = @($Target.files | ForEach-Object { Get-CanonicalPath ([string]$_.path) } | Sort-Object)
    $actualFiles = @(Get-ChildItem -LiteralPath $directory -Force -Recurse -File -ErrorAction Stop | ForEach-Object { Get-CanonicalPath $_.FullName } | Sort-Object)
    if (($expectedFiles -join "`n") -ne ($actualFiles -join "`n")) { return 'target_directory_file_inventory_drift' }
    $expectedDirectories = @($Target.directories | ForEach-Object { Get-CanonicalPath ([string]$_) } | Sort-Object)
    $actualDirectories = @(Get-ChildItem -LiteralPath $directory -Force -Recurse -Directory -ErrorAction Stop | ForEach-Object { Get-CanonicalPath $_.FullName } | Sort-Object)
    if (($expectedDirectories -join "`n") -ne ($actualDirectories -join "`n")) { return 'target_directory_subdirectory_inventory_drift' }
    foreach ($record in @($Target.files)) {
        $allowStaging = ([string]$Target.category -eq 'A-central-failed-staging')
        $reason = Test-FileRecordStable $record -AllowFormalStagingFiles:$allowStaging
        if ($null -ne $reason) { return ("{0}:{1}" -f $reason, $record.path) }
    }
    return $null
}

function Test-MapProofStable {
    param([Parameter(Mandatory = $true)][object]$Target)
    $proof = $Target.proof
    if ($null -eq $proof) { return 'proof_missing' }
    $category = [string]$Target.category
    if ($category -eq 'A-exact-duplicate') {
        if (-not $proof.authority_path -or -not (Test-Path -LiteralPath ([string]$proof.authority_path) -PathType Leaf)) { return 'duplicate_authority_missing' }
        if ($allTargetPaths.ContainsKey((Get-CanonicalPath ([string]$proof.authority_path)).ToLowerInvariant())) { return 'duplicate_authority_is_target' }
        if ((Get-Sha256ForFile ([string]$Target.path)) -ne (Get-Sha256ForFile ([string]$proof.authority_path))) { return 'duplicate_authority_hash_drift' }
    }
    if ($category -eq 'A-damaged-superseded') {
        if ([string]$proof.damage_status -ne 'damaged') { return 'damage_status_not_confirmed' }
        if (-not $proof.superseded_by -or -not (Test-Path -LiteralPath ([string]$proof.superseded_by) -PathType Leaf)) { return 'superseded_pointer_missing' }
        if ($null -eq (Test-FormalProtectedPath ([string]$proof.superseded_by))) { return 'superseded_pointer_not_formal' }
        foreach ($evidence in @($proof.evidence_paths)) {
            if (-not (Test-Path -LiteralPath ([string]$evidence) -PathType Leaf)) { return 'supersession_evidence_missing' }
        }
    }
    return $null
}

if (-not (Test-AllowedRootDefinition)) { Add-Blocked 'allowlisted_roots_changed' }
if (-not (Test-CurrentRunMatchesSnapshot $plan.active_run_snapshot)) { Add-Blocked 'active_run_state_drift' }
$activeCheck = Test-NoActiveCentralRun
if (-not $activeCheck.pass) { Add-Blocked $activeCheck.reason }
$acceptanceCheck = Test-AcceptanceMatchesSnapshot $plan.acceptance_snapshot
if (-not $acceptanceCheck.pass) { Add-Blocked $acceptanceCheck.reason }
if ([int]$plan.summary.target_count -ne @($plan.targets).Count) { Add-Blocked 'plan_summary_target_count_mismatch' }
$calculatedPlanBytes = [int64](($plan.targets | Measure-Object -Property total_bytes -Sum).Sum)
if ($calculatedPlanBytes -ne [int64]$plan.summary.target_bytes) { Add-Blocked 'plan_summary_target_bytes_mismatch' }

$allTargetPaths = @{}
$allListedFilePaths = @{}
foreach ($candidateTarget in @($plan.targets)) {
    try {
        $candidatePath = Get-CanonicalPath ([string]$candidateTarget.path)
        if ($allTargetPaths.ContainsKey($candidatePath.ToLowerInvariant())) { Add-Blocked ('duplicate_target:{0}' -f $candidatePath) }
        $allTargetPaths[$candidatePath.ToLowerInvariant()] = $true
    } catch {
        Add-Blocked ('target_path_invalid:' + [string]$_.Exception.Message)
    }
}
foreach ($target in @($plan.targets)) {
    try {
        $targetPath = Get-CanonicalPath ([string]$target.path)
        if ($targetPath -eq $planFile) { Add-Blocked 'plan_is_target'; continue }
        if ($target.kind -eq 'directory' -and (Test-PathWithinRoot -Path $targetPath -Root $targetPath)) { Add-Blocked "target_root_path:$targetPath"; continue }
        $rootDefinition = @(Get-AllowlistedRoots | Where-Object { $_.name -eq [string]$target.root } | Select-Object -First 1)
        if ($rootDefinition.Count -ne 1) { Add-Blocked "target_root_not_allowlisted:$targetPath"; continue }
        if (-not (Test-PathWithinRoot -Path $targetPath -Root ([string]$rootDefinition[0].path))) { Add-Blocked "target_outside_allowlisted_root:$targetPath"; continue }
        $excluded = Test-ExcludedPath $targetPath
        if ($null -ne $excluded) { Add-Blocked ('{0}:{1}' -f $excluded, $targetPath); continue }
        if ($null -ne (Test-FormalProtectedPath $targetPath)) { Add-Blocked ('formal_target_protected:{0}' -f $targetPath); continue }
        if ($target.kind -eq 'file') {
            if (@($target.files).Count -ne 1) { Add-Blocked "file_target_inventory_invalid:$targetPath"; continue }
            $reason = Test-FileRecordStable $target.files[0]
            if ($null -ne $reason) { Add-Blocked ('{0}:{1}' -f $reason, $targetPath) }
            $proofReason = if ($target.category -in @('A-exact-duplicate', 'A-damaged-superseded')) { Test-MapProofStable $target } else { $null }
            if ($null -ne $proofReason) { Add-Blocked ('{0}:{1}' -f $proofReason, $targetPath) }
            $allListedFilePaths[$targetPath.ToLowerInvariant()] = $true
        } else {
            $reason = Test-DirectoryTargetStable $target
            if ($null -ne $reason) { Add-Blocked ('{0}:{1}' -f $reason, $targetPath) }
            foreach ($record in @($target.files)) {
                $filePath = Get-CanonicalPath ([string]$record.path)
                if ($allListedFilePaths.ContainsKey($filePath.ToLowerInvariant())) { Add-Blocked "overlapping_target_file:$filePath" }
                $allListedFilePaths[$filePath.ToLowerInvariant()] = $true
            }
        }
    } catch {
        Add-Blocked ('target_preflight_exception:' + [string]$_.Exception.Message)
    }
}

if ($blockers.Count -gt 0) {
    foreach ($target in @($plan.targets)) {
        $skipped.Add([ordered]@{ path = [string]$target.path; category = [string]$target.category; reason = 'preflight_blocked'; blockers = @($blockers) })
    }
    Write-ReceiptAndStop -Status 'blocked' -ExitCode 2
}

foreach ($target in @($plan.targets)) {
    try {
        if ($target.kind -eq 'file') {
            $record = $target.files[0]
            Remove-Item -LiteralPath (Get-CanonicalPath ([string]$record.path)) -Force -ErrorAction Stop
            if (Test-Path -LiteralPath ([string]$record.path)) { throw 'delete_verification_failed' }
            $deleted.Add([ordered]@{ path = [string]$record.path; category = [string]$target.category; kind = 'file'; bytes = [int64]$record.size; sha256 = [string]$record.sha256 })
            continue
        }
        foreach ($record in @($target.files)) {
            Remove-Item -LiteralPath (Get-CanonicalPath ([string]$record.path)) -Force -ErrorAction Stop
            if (Test-Path -LiteralPath ([string]$record.path)) { throw 'delete_verification_failed' }
            $deleted.Add([ordered]@{ path = [string]$record.path; category = [string]$target.category; kind = 'directory_file'; bytes = [int64]$record.size; sha256 = [string]$record.sha256 })
        }
        foreach ($directory in @($target.directories | Sort-Object Length -Descending)) {
            $dirPath = Get-CanonicalPath ([string]$directory)
            if (@(Get-ChildItem -LiteralPath $dirPath -Force -ErrorAction Stop).Count -ne 0) { throw ("directory_not_empty:{0}" -f $dirPath) }
            Remove-Item -LiteralPath $dirPath -Force -ErrorAction Stop
            if (Test-Path -LiteralPath $dirPath) { throw 'directory_delete_verification_failed' }
        }
        $targetPath = Get-CanonicalPath ([string]$target.path)
        if (@(Get-ChildItem -LiteralPath $targetPath -Force -ErrorAction Stop).Count -ne 0) { throw ("target_directory_not_empty:{0}" -f $targetPath) }
        Remove-Item -LiteralPath $targetPath -Force -ErrorAction Stop
        if (Test-Path -LiteralPath $targetPath) { throw 'target_directory_delete_verification_failed' }
        $deleted.Add([ordered]@{ path = $targetPath; category = [string]$target.category; kind = 'directory'; bytes = [int64]0; sha256 = $null })
    } catch {
        $skipped.Add([ordered]@{ path = [string]$target.path; category = [string]$target.category; reason = 'delete_failed'; error = [string]$_.Exception.Message })
        foreach ($remaining in @($plan.targets | Where-Object { -not ($deleted.path -contains [string]$_.path) })) {
            if ([string]$remaining.path -ne [string]$target.path) {
                $skipped.Add([ordered]@{ path = [string]$remaining.path; category = [string]$remaining.category; reason = 'not_reached_after_delete_failure' })
            }
        }
        Write-ReceiptAndStop -Status 'partial_failure' -ExitCode 3
    }
}

Write-ReceiptAndStop -Status 'complete' -ExitCode 0
