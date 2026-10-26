[CmdletBinding()]
param(
    [string]$OutputPath,
    [switch]$WritePlan,
    [string]$ProofMapPath
)

. (Join-Path $PSScriptRoot 'CLEANUP_COMMON.ps1')

$roots = @(Get-AllowlistedRoots)
$rootPaths = @($roots | ForEach-Object { [string]$_.path })
$active = Get-ActiveRunSnapshot
$acceptance = Get-AcceptanceSnapshot
$targets = New-Object System.Collections.Generic.List[object]
$skipped = New-Object System.Collections.Generic.List[object]
$targetPaths = @{}

function Add-Skipped {
    param(
        [string]$Path,
        [string]$Category,
        [string]$Reason,
        [string]$EvidencePath
    )
    $skipped.Add([ordered]@{
        path = if ($Path) { Get-CanonicalPath $Path } else { $null }
        category = $Category
        reason = $Reason
        evidence_path = $EvidencePath
    })
}

function Add-Target {
    param([Parameter(Mandatory = $true)][object]$Target)
    $canonical = Get-CanonicalPath ([string]$Target.path)
    $key = $canonical.ToLowerInvariant()
    if ($targetPaths.ContainsKey($key)) {
        Add-Skipped -Path $canonical -Category ([string]$Target.category) -Reason 'duplicate_target_path_in_plan' -EvidencePath $null
        return
    }
    $targetPaths[$key] = $true
    $Target.path = $canonical
    $targets.Add($Target)
}

function Get-MapPathAssessment {
    param([Parameter(Mandatory = $true)][object]$Entry)
    $targetPath = Get-CanonicalPath ([string]$Entry.target_path)
    $authorityPath = Get-CanonicalPath ([string]$Entry.authority_path)
    if (-not (Test-Path -LiteralPath $targetPath -PathType Leaf)) { return [pscustomobject]@{ safe = $false; reason = 'proof_target_missing'; target = $targetPath } }
    if (-not (Test-Path -LiteralPath $authorityPath -PathType Leaf)) { return [pscustomobject]@{ safe = $false; reason = 'proof_authority_missing'; target = $targetPath } }
    if ($targetPath.Equals($authorityPath, [StringComparison]::OrdinalIgnoreCase)) { return [pscustomobject]@{ safe = $false; reason = 'proof_target_equals_authority'; target = $targetPath } }
    $category = [string]$Entry.category
    if ($category -notin @('A-exact-duplicate', 'A-damaged-superseded')) { return [pscustomobject]@{ safe = $false; reason = 'proof_category_invalid'; target = $targetPath } }
    $assessment = Get-SafeFileAssessment -Path $targetPath -AllowlistedRootPaths $rootPaths -Category $category
    if (-not $assessment.safe) { return [pscustomobject]@{ safe = $false; reason = $assessment.reason; target = $targetPath } }
    $targetRecord = $assessment.record
    $authorityHash = Get-Sha256ForFile $authorityPath
    if ($category -eq 'A-exact-duplicate') {
        if ($targetRecord.sha256 -ne $authorityHash) { return [pscustomobject]@{ safe = $false; reason = 'proof_hash_mismatch'; target = $targetPath } }
    } else {
        if ([string]::IsNullOrWhiteSpace([string]$Entry.superseded_by)) { return [pscustomobject]@{ safe = $false; reason = 'superseded_pointer_missing'; target = $targetPath } }
        $supersededBy = Get-CanonicalPath ([string]$Entry.superseded_by)
        if (-not (Test-Path -LiteralPath $supersededBy -PathType Leaf)) { return [pscustomobject]@{ safe = $false; reason = 'superseded_pointer_missing'; target = $targetPath } }
        if ($null -eq (Test-FormalProtectedPath $supersededBy)) { return [pscustomobject]@{ safe = $false; reason = 'superseded_by_not_formal_metadata'; target = $targetPath } }
        if ([string]$Entry.damage_status -ne 'damaged') { return [pscustomobject]@{ safe = $false; reason = 'damage_status_not_confirmed'; target = $targetPath } }
    }
    return [pscustomobject]@{
        safe = $true
        reason = $null
        record = $targetRecord
        proof = [ordered]@{
            authority_path = $authorityPath
            authority_sha256 = $authorityHash
            evidence_paths = @($Entry.evidence_paths)
            superseded_by = if ($Entry.superseded_by) { Get-CanonicalPath ([string]$Entry.superseded_by) } else { $null }
            damage_status = if ($Entry.damage_status) { [string]$Entry.damage_status } else { $null }
        }
    }
}

if ($ProofMapPath) {
    $proofPath = Get-CanonicalPath $ProofMapPath
    if (-not (Test-PathWithinRoot -Path $proofPath -Root $script:MaintenanceRoot)) { throw 'proof_map_outside_maintenance_root' }
    $proofMap = Read-JsonFile $proofPath
    if ($null -eq $proofMap -or [string]$proofMap.schema -ne 'stock-cleanup-proof-map-v1') { throw 'proof_map_schema_mismatch' }
    foreach ($entry in @($proofMap.entries)) {
        try {
            $mapAssessment = Get-MapPathAssessment $entry
            if (-not $mapAssessment.safe) {
                Add-Skipped -Path ([string]$entry.target_path) -Category ([string]$entry.category) -Reason $mapAssessment.reason -EvidencePath $proofPath
                continue
            }
            $targetRecord = $mapAssessment.record
            $target = [ordered]@{
                id = 'target-' + $targets.Count.ToString('000000')
                kind = 'file'
                category = [string]$entry.category
                path = $targetRecord.path
                root = ($roots | Where-Object { Test-PathWithinRoot -Path $targetRecord.path -Root $_.path } | Select-Object -First 1).name
                files = @($targetRecord)
                directories = @()
                total_bytes = [int64]$targetRecord.size
                reason_code = if ($entry.category -eq 'A-exact-duplicate') { 'exact_hash_duplicate_with_authority' } else { 'damaged_copy_superseded_by_formal_pointer_or_manifest' }
                proof = $mapAssessment.proof
            }
            Add-Target ([pscustomobject]$target)
        } catch {
            Add-Skipped -Path ([string]$entry.target_path) -Category ([string]$entry.category) -Reason 'proof_map_entry_invalid' -EvidencePath $proofPath
        }
    }
}

$centralRoot = ($roots | Where-Object { $_.kind -eq 'central_staging' } | Select-Object -First 1).path
if (Test-Path -LiteralPath $centralRoot -PathType Container) {
    $runDirectories = @(Get-ChildItem -LiteralPath $centralRoot -Directory -Force -ErrorAction SilentlyContinue)
    foreach ($runDirectory in $runDirectories) {
        $runId = [string]$runDirectory.Name
        if ($runId -notmatch '^market-') {
            Add-Skipped -Path $runDirectory.FullName -Category 'central_staging' -Reason 'staging_directory_name_not_run_id' -EvidencePath $null
            continue
        }
        if ([string]$active.run_id -eq $runId) {
            Add-Skipped -Path $runDirectory.FullName -Category 'central_staging' -Reason 'active_run_excluded' -EvidencePath $null
            continue
        }
        $receipt = Get-ReceiptForRun $runId
        if ($null -ne $receipt) {
            Add-Skipped -Path $runDirectory.FullName -Category 'central_staging' -Reason 'formal_receipt_present' -EvidencePath $receipt
            continue
        }
        $failure = Get-FailureEvidenceForRun $runId
        if ($null -eq $failure) {
            Add-Skipped -Path $runDirectory.FullName -Category 'central_staging' -Reason 'failure_evidence_missing' -EvidencePath $null
            continue
        }
        $inventory = Get-ExplicitDirectoryInventory -Directory $runDirectory.FullName -AllowlistedRootPaths $rootPaths -Category 'A-central-failed-staging' -AllowFormalStagingFiles
        if (-not $inventory.safe) {
            Add-Skipped -Path $runDirectory.FullName -Category 'central_staging' -Reason ([string]$inventory.reason) -EvidencePath $failure
            continue
        }
        $totalBytes = [int64](($inventory.files | Measure-Object -Property size -Sum).Sum)
        Add-Target ([pscustomobject]([ordered]@{
            id = 'target-' + $targets.Count.ToString('000000')
            kind = 'directory'
            category = 'A-central-failed-staging'
            path = Get-CanonicalPath $runDirectory.FullName
            root = 'central_failed_staging'
            files = @($inventory.files)
            directories = @($inventory.directories)
            total_bytes = $totalBytes
            reason_code = 'failed_central_staging_without_formal_receipt'
            proof = [ordered]@{ run_id = $runId; formal_receipt = $null; failure_evidence = $failure; active_run_excluded = $true }
        }))
    }
}

foreach ($root in @($roots | Where-Object { $_.kind -eq 'explicit_temp' })) {
    if (-not (Test-Path -LiteralPath $root.path -PathType Container)) { continue }
    try { $files = @(Get-ChildItem -LiteralPath $root.path -File -Force -Recurse -ErrorAction Stop) }
    catch {
        Add-Skipped -Path $root.path -Category 'explicit_temp' -Reason 'root_enumeration_failed' -EvidencePath $null
        continue
    }
    foreach ($file in $files) {
        if (-not (Test-ExplicitTemporaryMarker $file.FullName)) { continue }
        $assessment = Get-SafeFileAssessment -Path $file.FullName -AllowlistedRootPaths $rootPaths -Category 'A-explicit-temporary'
        if (-not $assessment.safe) {
            Add-Skipped -Path $file.FullName -Category 'explicit_temp' -Reason $assessment.reason -EvidencePath $null
            continue
        }
        $record = $assessment.record
        $target = [ordered]@{
            id = 'target-' + $targets.Count.ToString('000000')
            kind = 'file'
            category = 'A-explicit-temporary'
            path = $record.path
            root = $root.name
            files = @($record)
            directories = @()
            total_bytes = [int64]$record.size
            reason_code = 'explicit_preview_tmp_qa_candidate_or_smoke'
            proof = [ordered]@{ marker = $true; marker_path = $record.path }
        }
        Add-Target ([pscustomobject]$target)
    }
}

$plan = [ordered]@{
    schema = $script:CleanupSchema
    tool_version = $script:CleanupToolVersion
    generated_at_utc = [DateTime]::UtcNow.ToString('o')
    mode = 'preview'
    allowlisted_roots = @($roots)
    exclusions = @(
        'D:\TWStockData\shared_market_data',
        'D:\claude code\twmix',
        'D:\cowork\premarket_daytrade_top10',
        'venv',
        'node_modules',
        '.git tracked files',
        'formal receipt/evidence/pointer/manifest/report assets',
        'unknown C candidates',
        'active Central run and its staging directory'
    )
    active_run_snapshot = $active
    acceptance_snapshot = $acceptance
    targets = @($targets.ToArray())
    skipped = @($skipped.ToArray())
    summary = [ordered]@{
        target_count = $targets.Count
        skipped_count = $skipped.Count
        target_bytes = [int64](($targets | Measure-Object -Property total_bytes -Sum).Sum)
        category_counts = [ordered]@{
            A_central_failed_staging = @($targets | Where-Object { $_.category -eq 'A-central-failed-staging' }).Count
            A_explicit_temporary = @($targets | Where-Object { $_.category -eq 'A-explicit-temporary' }).Count
            A_exact_duplicate = @($targets | Where-Object { $_.category -eq 'A-exact-duplicate' }).Count
            A_damaged_superseded = @($targets | Where-Object { $_.category -eq 'A-damaged-superseded' }).Count
        }
    }
    plan_sha256 = $null
}
$planObject = [pscustomobject]$plan
$plan.plan_sha256 = Get-PlanContentHash $planObject
$planObject = [pscustomobject]$plan

if ($WritePlan) {
    if ([string]::IsNullOrWhiteSpace($OutputPath)) { throw 'write_plan_requires_output_path' }
    $destination = Assert-PlanOutputPath $OutputPath
    Write-Utf8NoBom -Path $destination -Text (($planObject | ConvertTo-Json -Depth 100) + [Environment]::NewLine)
    Write-Output ("PLAN_WRITTEN path={0} sha256={1} targets={2} bytes={3}" -f $destination, $plan.plan_sha256, $targets.Count, $plan.summary.target_bytes)
} else {
    if ($OutputPath) { throw 'output_path_requires_write_plan' }
    Write-Output ("PREVIEW_ONLY sha256={0} targets={1} skipped={2} bytes={3}" -f $plan.plan_sha256, $targets.Count, $skipped.Count, $plan.summary.target_bytes)
    foreach ($target in $targets.ToArray()) {
        Write-Output ("TARGET category={0} kind={1} bytes={2} path={3}" -f $target.category, $target.kind, $target.total_bytes, $target.path)
    }
}
