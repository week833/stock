[CmdletBinding()]
param(
    [switch]$HistoricalBaseline,
    [switch]$CurrentOwnerSeal
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$pathContractPath = "D:\TWStockData\config\path_contract.json"
if (-not (Test-Path -LiteralPath $pathContractPath -PathType Leaf)) {
    throw "Path contract is missing: $pathContractPath"
}
try {
    $pathContract = Get-Content -LiteralPath $pathContractPath -Raw -ErrorAction Stop | ConvertFrom-Json
}
catch {
    throw "Path contract is not valid JSON: $pathContractPath"
}
if ($null -eq $pathContract -or $null -eq $pathContract.managed_paths) {
    throw "Path contract has no managed_paths: $pathContractPath"
}
# Same rule as shared\path_contract.py: known versions silently, other X.Y.Z with
# major 1 or 2 accepted with a warning, anything else rejected.
$supportedContractVersions = @("1.1.0", "2.0.0")
if ($supportedContractVersions -notcontains [string]$pathContract.contract_version) {
    if ([string]$pathContract.contract_version -notmatch '^(1|2)\.\d+\.\d+$') {
        throw "Path contract version must be 1.x or 2.x (known: $($supportedContractVersions -join ', ')): $pathContractPath"
    }
    Write-Warning "Path contract version $([string]$pathContract.contract_version) is not a known version ($($supportedContractVersions -join ', ')); accepted by major version: $pathContractPath"
}
$contractRoot = [System.IO.Path]::GetFullPath([string]$pathContract.root).TrimEnd("\")
$expectedContractRoot = "D:\TWStockData"
if (-not $contractRoot.Equals($expectedContractRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Path contract root must be ${expectedContractRoot}: $contractRoot"
}

$currentCanonicalSharedRoot = "D:\TWStockData\shared_market_data"
$currentDstockDailyRoot = "D:\TWStockData\program_data\dstock_daily"
$currentControlRoot = Join-Path $currentDstockDailyRoot "state\shared_market_data_control"
$currentManifestPath = Join-Path $currentCanonicalSharedRoot "_cowork_shared_data_manifest.json"
$currentResultPath = Join-Path $currentControlRoot "verification_latest.json"
$currentManifestSchema = "cowork-shared-data-owner-seal-v2"
$currentVerificationSchema = "shared-data-owner-seal-verification-v1"

if ($HistoricalBaseline -and $CurrentOwnerSeal) {
    throw "-HistoricalBaseline and -CurrentOwnerSeal are mutually exclusive."
}

function Get-ContractManagedPath {
    param([Parameter(Mandatory = $true)][string]$Name)

    $value = $pathContract.managed_paths.$Name
    if ([string]::IsNullOrWhiteSpace([string]$value)) {
        throw "Path contract is missing managed_paths.$Name"
    }
    try {
        $resolved = [System.IO.Path]::GetFullPath([string]$value).TrimEnd("\")
    }
    catch {
        throw "Path contract managed_paths.$Name is invalid: $value"
    }
    if (-not $resolved.Equals($contractRoot, [System.StringComparison]::OrdinalIgnoreCase) -and
        -not $resolved.StartsWith($contractRoot + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path contract managed_paths.$Name escaped root: $resolved"
    }
    return $resolved
}

if (-not $HistoricalBaseline) {
    $runtimeHealthCheck = "D:\stock\programs\VERIFY_RUNTIME_DATA_PATHS.cmd"
    Write-Warning (
        "The first-migration SHA-256 baseline is retired because canonical data " +
        "updates incrementally. Running the current runtime data-path health check. " +
        "Use -HistoricalBaseline only for an explicit historical audit."
    )
    if (-not (Test-Path -LiteralPath $runtimeHealthCheck -PathType Leaf)) {
        throw "Runtime data-path health check is missing: $runtimeHealthCheck"
    }
    & $runtimeHealthCheck
    exit $LASTEXITCODE
}

$canonicalSharedRoot = Get-ContractManagedPath -Name "canonical_shared"
$dstockDailyRoot = Get-ContractManagedPath -Name "dstock_daily"
$stateRoot = [System.IO.Path]::GetFullPath((Join-Path $dstockDailyRoot "state")).TrimEnd("\")
$sharedRoot = $canonicalSharedRoot
$manifestPath = Join-Path $sharedRoot "_metadata\shared_reference_manifest.json"
$controlRoot = Join-Path $stateRoot "shared_market_data_control"
$resultPath = Join-Path $controlRoot "verification_latest.json"

function ConvertTo-NormalizedFullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path).TrimEnd("\")
}

function Assert-PathUnderRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root,
        [switch]$AllowRoot
    )

    $candidate = ConvertTo-NormalizedFullPath $Path
    $boundary = ConvertTo-NormalizedFullPath $Root
    if ($candidate.Equals($boundary, [System.StringComparison]::OrdinalIgnoreCase)) {
        if ($AllowRoot) {
            return $candidate
        }
        throw "Path must be below, not equal to, the allowed root: $candidate"
    }
    if (-not $candidate.StartsWith($boundary + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path escaped the allowed root '$boundary': $candidate"
    }
    return $candidate
}

function Assert-NoExistingReparseComponent {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$StopRoot
    )

    $candidate = Assert-PathUnderRoot -Path $Path -Root $StopRoot -AllowRoot
    $stop = ConvertTo-NormalizedFullPath $StopRoot
    $current = $candidate
    while ($true) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Reparse points are not allowed in managed paths: $current"
            }
        }
        if ($current.Equals($stop, [System.StringComparison]::OrdinalIgnoreCase)) {
            break
        }
        $parent = [System.IO.Directory]::GetParent($current)
        if ($null -eq $parent) {
            throw "Could not reach safety boundary '$stop' from '$candidate'."
        }
        $current = $parent.FullName.TrimEnd("\")
    }
}

function Get-SafeFiles {
    param([Parameter(Mandatory = $true)][string]$Root)

    $normalizedRoot = ConvertTo-NormalizedFullPath $Root
    if (-not (Test-Path -LiteralPath $normalizedRoot -PathType Container)) {
        throw "Directory is missing: $normalizedRoot"
    }
    $files = New-Object "System.Collections.Generic.List[System.IO.FileInfo]"
    $stack = New-Object "System.Collections.Generic.Stack[string]"
    $stack.Push($normalizedRoot)
    while ($stack.Count -gt 0) {
        $current = $stack.Pop()
        $currentItem = Get-Item -LiteralPath $current -Force
        if (($currentItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Reparse points are not allowed in canonical data: $current"
        }
        foreach ($item in Get-ChildItem -LiteralPath $current -Force) {
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Reparse points are not allowed in canonical data: $($item.FullName)"
            }
            if ($item.PSIsContainer) {
                $stack.Push($item.FullName)
            }
            else {
                $files.Add($item)
            }
        }
    }
    return $files
}

function Get-RelativePathFromRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $fullPath = Assert-PathUnderRoot -Path $Path -Root $Root
    $fullRoot = ConvertTo-NormalizedFullPath $Root
    return $fullPath.Substring($fullRoot.Length).TrimStart("\")
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    $stream = $null
    $algorithm = $null
    try {
        $stream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::Read
        )
        $algorithm = [System.Security.Cryptography.SHA256]::Create()
        $hashBytes = $algorithm.ComputeHash($stream)
        return [System.BitConverter]::ToString($hashBytes).Replace("-", "")
    }
    finally {
        if ($null -ne $algorithm) {
            $algorithm.Dispose()
        }
        if ($null -ne $stream) {
            $stream.Dispose()
        }
    }
}

function Get-JsonPropertyValue {
    param(
        [Parameter(Mandatory = $true)]$Object,
        [Parameter(Mandatory = $true)][string]$Name
    )

    if ($null -eq $Object) {
        return $null
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Test-Sha256Value {
    param([Parameter(Mandatory = $false)]$Value)

    return ($Value -is [string] -and $Value -match '^[0-9a-fA-F]{64}$')
}

function ConvertTo-CurrentRelativePath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    return $RelativePath.Replace("\", "/")
}

function Get-UnixMtimeNanoseconds {
    param([Parameter(Mandatory = $true)][System.IO.FileInfo]$File)

    $epochTicks = [DateTime]::new(1970, 1, 1, 0, 0, 0, [DateTimeKind]::Utc).Ticks
    return [int64](($File.LastWriteTimeUtc.Ticks - $epochTicks) * 100)
}

function Get-CurrentAggregateSha256 {
    param([Parameter(Mandatory = $true)][System.Collections.IEnumerable]$Records)

    $ordered = New-Object "System.Collections.Generic.List[object]"
    foreach ($record in $Records) {
        [void]$ordered.Add($record)
    }
    $ordered.Sort([System.Comparison[object]]{
            param($left, $right)
            return [System.StringComparer]::Ordinal.Compare(
                [string](Get-JsonPropertyValue -Object $left -Name "relative_path"),
                [string](Get-JsonPropertyValue -Object $right -Name "relative_path")
            )
        })

    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        foreach ($record in $ordered) {
            $relative = [string](Get-JsonPropertyValue -Object $record -Name "relative_path")
            $size = [string](Get-JsonPropertyValue -Object $record -Name "size")
            $sha = [string](Get-JsonPropertyValue -Object $record -Name "sha256")
            $line = "{0}`0{1}`0{2}`n" -f $relative, $size, $sha
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($line)
            [void]$algorithm.TransformBlock($bytes, 0, $bytes.Length, $bytes, 0)
        }
        [void]$algorithm.TransformFinalBlock([byte[]]::new(0), 0, 0)
        return [System.BitConverter]::ToString($algorithm.Hash).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
    }
}

function Add-CurrentVerificationIssue {
    param(
        [Parameter(Mandatory = $true)]$IssueList,
        [Parameter(Mandatory = $true)][string]$Type,
        [Parameter(Mandatory = $false)][string]$RelativePath,
        [Parameter(Mandatory = $true)][string]$Detail
    )

    $IssueList.Add([pscustomobject]@{
            type = $Type
            relative_path = $RelativePath
            detail = $Detail
        })
}

function Invoke-CurrentOwnerSealVerification {
    $startedAt = [DateTime]::UtcNow
    $issues = New-Object "System.Collections.Generic.List[object]"
    $result = [ordered]@{
        schema = $currentVerificationSchema
        schema_version = 1
        status = "running"
        started_at_utc = $startedAt.ToString("o")
        completed_at_utc = $null
        canonical_root = $currentCanonicalSharedRoot
        verification_state_root = $currentControlRoot
        manifest_path = $currentManifestPath
        owner_manifest_path = $currentManifestPath
        manifest_schema = $currentManifestSchema
        hash_algorithm = "SHA256"
        owner_manifest_sha256 = $null
        aggregate = $null
        sealed_at = $null
        lineage = $null
        totals = [ordered]@{
            manifest_files = 0
            actual_files = 0
            checked_files = 0
            checked_bytes = 0
            excluded_files = 0
            issue_count = 0
            manifest_total_bytes = 0
        }
        exclusion_policy = $null
        issues = @()
        error = $null
        error_details = $null
    }
    $exitCode = 0

    try {
        if (-not (Test-Path -LiteralPath $currentCanonicalSharedRoot -PathType Container)) {
            throw "Canonical shared-data root is missing: $currentCanonicalSharedRoot"
        }
        Assert-NoExistingReparseComponent -Path $currentCanonicalSharedRoot -StopRoot $currentCanonicalSharedRoot
        if (-not (Test-Path -LiteralPath $currentManifestPath -PathType Leaf)) {
            throw "Current owner-seal manifest is missing: $currentManifestPath"
        }
        $manifestItem = Get-Item -LiteralPath $currentManifestPath -Force
        if (($manifestItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Current owner-seal manifest is a reparse point."
        }
        $manifestBefore = Get-Item -LiteralPath $currentManifestPath -Force
        $result.owner_manifest_sha256 = Get-Sha256 -Path $currentManifestPath
        $manifestAfter = Get-Item -LiteralPath $currentManifestPath -Force
        if ($manifestBefore.Length -ne $manifestAfter.Length -or
            $manifestBefore.LastWriteTimeUtc.Ticks -ne $manifestAfter.LastWriteTimeUtc.Ticks) {
            Add-CurrentVerificationIssue -IssueList $issues -Type "manifest_changed_during_hash" -Detail "Owner-seal manifest changed while it was hashed."
        }

        $manifest = Get-Content -LiteralPath $currentManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ((Get-JsonPropertyValue -Object $manifest -Name "schema") -ne $currentManifestSchema) {
            Add-CurrentVerificationIssue -IssueList $issues -Type "manifest_schema_invalid" -Detail "Owner-seal manifest schema must be cowork-shared-data-owner-seal-v2."
        }
        if ([int64](Get-JsonPropertyValue -Object $manifest -Name "schema_version") -ne 2) {
            Add-CurrentVerificationIssue -IssueList $issues -Type "manifest_schema_version_invalid" -Detail "Owner-seal manifest schema_version must be 2."
        }

        $destination = Get-JsonPropertyValue -Object $manifest -Name "destination"
        $destinationRoot = [string](Get-JsonPropertyValue -Object $destination -Name "canonical_root")
        if ([string]::IsNullOrWhiteSpace($destinationRoot) -or
            -not ([System.IO.Path]::GetFullPath($destinationRoot).TrimEnd("\")).Equals($currentCanonicalSharedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            Add-CurrentVerificationIssue -IssueList $issues -Type "destination_root_invalid" -Detail "Manifest destination canonical_root does not match the fixed current root."
        }
        $destinationRelative = ([string](Get-JsonPropertyValue -Object $destination -Name "root_relative_to_project")).Replace("\", "/")
        if ($destinationRelative -ne "D:/TWStockData/shared_market_data") {
            Add-CurrentVerificationIssue -IssueList $issues -Type "destination_identity_invalid" -Detail "Manifest destination root_relative_to_project is invalid."
        }

        $policy = Get-JsonPropertyValue -Object $manifest -Name "exclusion_policy"
        $knownLockValues = @(Get-JsonPropertyValue -Object $policy -Name "known_stale_owner_locks")
        $policyVersion = [string](Get-JsonPropertyValue -Object $policy -Name "version")
        $policyManifest = ([string](Get-JsonPropertyValue -Object $policy -Name "manifest")).Replace("\", "/")
        $policySha = [string](Get-JsonPropertyValue -Object $policy -Name "sha256")
        if ($policyVersion -ne "owner-seal-v2-exclusions-v1" -or
            $policyManifest -ne "_cowork_shared_data_manifest.json" -or
            $knownLockValues.Count -eq 0 -or
            [string](Get-JsonPropertyValue -Object $policy -Name "temporary_transaction_policy") -ne "block" -or
            [string](Get-JsonPropertyValue -Object $policy -Name "unknown_lock_policy") -ne "block" -or
            -not (Test-Sha256Value $policySha)) {
            Add-CurrentVerificationIssue -IssueList $issues -Type "exclusion_policy_invalid" -Detail "Owner-seal exclusion policy is missing or invalid."
        }
        $knownLocks = New-Object "System.Collections.Generic.HashSet[string]" ([System.StringComparer]::OrdinalIgnoreCase)
        foreach ($lock in $knownLockValues) {
            $normalizedLock = ConvertTo-CurrentRelativePath ([string]$lock)
            if ([string]::IsNullOrWhiteSpace($normalizedLock) -or
                $normalizedLock.StartsWith("/", [System.StringComparison]::Ordinal) -or
                $normalizedLock.Contains("..") -or
                -not $normalizedLock.EndsWith(".lock", [System.StringComparison]::OrdinalIgnoreCase)) {
                Add-CurrentVerificationIssue -IssueList $issues -Type "exclusion_policy_path_invalid" -Detail "Known stale owner-lock path is invalid."
                continue
            }
            [void]$knownLocks.Add($normalizedLock)
        }
        $result.exclusion_policy = [ordered]@{
            version = $policyVersion
            sha256 = $policySha
            manifest = $policyManifest
            known_stale_owner_locks = @($knownLocks | Sort-Object)
            temporary_transaction_policy = Get-JsonPropertyValue -Object $policy -Name "temporary_transaction_policy"
            unknown_lock_policy = Get-JsonPropertyValue -Object $policy -Name "unknown_lock_policy"
        }

        $ownerSeal = Get-JsonPropertyValue -Object $manifest -Name "owner_seal"
        $sealTime = [string](Get-JsonPropertyValue -Object $ownerSeal -Name "sealed_at")
        $sealReason = [string](Get-JsonPropertyValue -Object $ownerSeal -Name "reason")
        $previousManifestSha = [string](Get-JsonPropertyValue -Object $ownerSeal -Name "previous_manifest_sha256")
        $sourceProvenanceSha = [string](Get-JsonPropertyValue -Object $ownerSeal -Name "source_provenance_sha256")
        if ([string]::IsNullOrWhiteSpace($sealTime) -or [string]::IsNullOrWhiteSpace($sealReason) -or
            -not (Test-Sha256Value $previousManifestSha) -or
            -not (Test-Sha256Value $sourceProvenanceSha) -or
            (Get-JsonPropertyValue -Object $ownerSeal -Name "external_source_opened") -ne $false) {
            Add-CurrentVerificationIssue -IssueList $issues -Type "owner_seal_lineage_invalid" -Detail "Owner-seal lineage is incomplete or records external source access."
        }
        try {
            [void][DateTimeOffset]::Parse($sealTime)
        }
        catch {
            Add-CurrentVerificationIssue -IssueList $issues -Type "owner_seal_timestamp_invalid" -Detail "Owner-seal sealed_at is not a valid timestamp."
        }
        $result.sealed_at = $sealTime
        $result.lineage = [ordered]@{
            reason = $sealReason
            previous_manifest_sha256 = $previousManifestSha
            previous_manifest_identity = Get-JsonPropertyValue -Object $ownerSeal -Name "previous_manifest_identity"
            backup_project_relative_path = Get-JsonPropertyValue -Object $ownerSeal -Name "backup_project_relative_path"
            source_provenance_sha256 = $sourceProvenanceSha
            receipt_sha256 = Get-JsonPropertyValue -Object $ownerSeal -Name "receipt_sha256"
            receipt_size = Get-JsonPropertyValue -Object $ownerSeal -Name "receipt_size"
            exclusion_policy_version = Get-JsonPropertyValue -Object $ownerSeal -Name "exclusion_policy_version"
            external_source_opened = Get-JsonPropertyValue -Object $ownerSeal -Name "external_source_opened"
        }

        $canonical = Get-JsonPropertyValue -Object $manifest -Name "canonical"
        $entries = @(Get-JsonPropertyValue -Object $canonical -Name "files")
        $perFileHashes = Get-JsonPropertyValue -Object $canonical -Name "per_file_hashes"
        $expected = New-Object "System.Collections.Generic.Dictionary[string,object]" ([System.StringComparer]::OrdinalIgnoreCase)
        $expectedRecords = New-Object "System.Collections.Generic.List[object]"
        foreach ($entry in $entries) {
            $rawRelative = [string](Get-JsonPropertyValue -Object $entry -Name "relative_path")
            $relative = ConvertTo-CurrentRelativePath $rawRelative
            $parts = $relative.Split("/")
            $invalidSegment = @($parts | Where-Object { [string]::IsNullOrWhiteSpace($_) -or $_ -eq "." -or $_ -eq ".." }).Count -gt 0
            if ([string]::IsNullOrWhiteSpace($relative) -or
                [System.IO.Path]::IsPathRooted($relative.Replace("/", "\")) -or
                $relative -match '^[A-Za-z]:|:|[\*\?]' -or $invalidSegment) {
                Add-CurrentVerificationIssue -IssueList $issues -Type "invalid_manifest_entry" -RelativePath $rawRelative -Detail "Manifest relative path is rooted, traversing, or otherwise invalid."
                continue
            }
            if ($relative -eq "_cowork_shared_data_manifest.json" -or $knownLocks.Contains($relative)) {
                Add-CurrentVerificationIssue -IssueList $issues -Type "excluded_file_listed" -RelativePath $relative -Detail "Manifest metadata or known owner lock must not be listed in canonical inventory."
                continue
            }
            if ($expected.ContainsKey($relative)) {
                Add-CurrentVerificationIssue -IssueList $issues -Type "duplicate_manifest_entry" -RelativePath $relative -Detail "Manifest contains duplicate relative paths."
                continue
            }
            $sizeValue = Get-JsonPropertyValue -Object $entry -Name "size"
            $shaValue = [string](Get-JsonPropertyValue -Object $entry -Name "sha256")
            if ($sizeValue -is [bool] -or $null -eq $sizeValue -or [int64]$sizeValue -lt 0 -or -not (Test-Sha256Value $shaValue)) {
                Add-CurrentVerificationIssue -IssueList $issues -Type "manifest_entry_identity_invalid" -RelativePath $relative -Detail "Manifest file size or SHA-256 is invalid."
                continue
            }
            $normalizedEntry = [pscustomobject]@{
                relative_path = $relative
                size = [int64]$sizeValue
                mtime_ns = Get-JsonPropertyValue -Object $entry -Name "mtime_ns"
                sha256 = $shaValue.ToLowerInvariant()
            }
            [void]$expected.Add($relative, $normalizedEntry)
            [void]$expectedRecords.Add($normalizedEntry)
        }
        $result.totals.manifest_files = $entries.Count
        $manifestFileCount = [int64](Get-JsonPropertyValue -Object $canonical -Name "file_count")
        $manifestTotalBytes = [int64](Get-JsonPropertyValue -Object $canonical -Name "total_bytes")
        $manifestAggregate = [string](Get-JsonPropertyValue -Object $canonical -Name "aggregate_sha256")
        $result.aggregate = $manifestAggregate.ToLowerInvariant()
        $result.totals.manifest_total_bytes = $manifestTotalBytes
        if ($manifestFileCount -le 0 -or $manifestFileCount -ne $expectedRecords.Count -or
            $manifestTotalBytes -lt 0 -or -not (Test-Sha256Value $manifestAggregate)) {
            Add-CurrentVerificationIssue -IssueList $issues -Type "canonical_summary_invalid" -Detail "Canonical file_count, total_bytes, or aggregate is invalid."
        }
        $computedExpectedAggregate = Get-CurrentAggregateSha256 -Records $expectedRecords
        if ($computedExpectedAggregate -ne $manifestAggregate.ToLowerInvariant()) {
            Add-CurrentVerificationIssue -IssueList $issues -Type "canonical_aggregate_invalid" -Detail "Canonical aggregate does not match canonical file records."
        }
        $hashProperties = @()
        if ($null -ne $perFileHashes) {
            $hashProperties = @($perFileHashes.PSObject.Properties)
        }
        if ($hashProperties.Count -ne $expectedRecords.Count) {
            Add-CurrentVerificationIssue -IssueList $issues -Type "per_file_hashes_count_invalid" -Detail "Canonical per_file_hashes does not match canonical file records."
        }
        foreach ($property in $hashProperties) {
            $hashPath = ConvertTo-CurrentRelativePath ([string]$property.Name)
            if (-not $expected.ContainsKey($hashPath) -or
                [string]$property.Value -ne [string]$expected[$hashPath].sha256) {
                Add-CurrentVerificationIssue -IssueList $issues -Type "per_file_hash_mismatch" -RelativePath $hashPath -Detail "Canonical per_file_hashes is not bound to canonical files."
            }
        }

        $actualFiles = @(Get-SafeFiles -Root $currentCanonicalSharedRoot)
        $result.totals.actual_files = $actualFiles.Count
        $actualByPath = New-Object "System.Collections.Generic.Dictionary[string,System.IO.FileInfo]" ([System.StringComparer]::OrdinalIgnoreCase)
        foreach ($actualFile in $actualFiles) {
            $actualRelative = ConvertTo-CurrentRelativePath (Get-RelativePathFromRoot -Path $actualFile.FullName -Root $currentCanonicalSharedRoot)
            if ($actualRelative -eq "_cowork_shared_data_manifest.json" -or $knownLocks.Contains($actualRelative)) {
                $result.totals.excluded_files++
                continue
            }
            if ($actualRelative.EndsWith(".lock", [System.StringComparison]::OrdinalIgnoreCase)) {
                Add-CurrentVerificationIssue -IssueList $issues -Type "unknown_owner_lock" -RelativePath $actualRelative -Detail "Unknown owner lock is not permitted by the exclusion policy."
                continue
            }
            if ($actualByPath.ContainsKey($actualRelative)) {
                Add-CurrentVerificationIssue -IssueList $issues -Type "duplicate_actual_file" -RelativePath $actualRelative -Detail "Canonical tree contains duplicate relative paths."
                continue
            }
            [void]$actualByPath.Add($actualRelative, $actualFile)
        }
        foreach ($actualPath in $actualByPath.Keys) {
            if (-not $expected.ContainsKey($actualPath)) {
                Add-CurrentVerificationIssue -IssueList $issues -Type "unexpected_file" -RelativePath $actualPath -Detail "Canonical file is not listed in owner-seal inventory."
            }
        }
        foreach ($expectedPath in $expected.Keys) {
            if (-not $actualByPath.ContainsKey($expectedPath)) {
                Add-CurrentVerificationIssue -IssueList $issues -Type "missing_file" -RelativePath $expectedPath -Detail "Owner-seal inventory file is missing."
                continue
            }
            $file = $actualByPath[$expectedPath]
            $before = Get-Item -LiteralPath $file.FullName -Force
            if (($before.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                Add-CurrentVerificationIssue -IssueList $issues -Type "reparse_point" -RelativePath $expectedPath -Detail "Canonical inventory file is a reparse point."
                continue
            }
            $expectedEntry = $expected[$expectedPath]
            if ([int64]$before.Length -ne [int64]$expectedEntry.size) {
                Add-CurrentVerificationIssue -IssueList $issues -Type "length_mismatch" -RelativePath $expectedPath -Detail "Canonical file length differs from owner-seal inventory."
                continue
            }
            $expectedMtime = Get-JsonPropertyValue -Object $expectedEntry -Name "mtime_ns"
            if ($null -ne $expectedMtime -and [int64]$expectedMtime -ne (Get-UnixMtimeNanoseconds -File $before)) {
                Add-CurrentVerificationIssue -IssueList $issues -Type "mtime_mismatch" -RelativePath $expectedPath -Detail "Canonical file mtime differs from owner-seal inventory."
                continue
            }
            $actualHash = (Get-Sha256 -Path $file.FullName).ToLowerInvariant()
            $after = Get-Item -LiteralPath $file.FullName -Force
            if ($before.Length -ne $after.Length -or $before.LastWriteTimeUtc.Ticks -ne $after.LastWriteTimeUtc.Ticks) {
                Add-CurrentVerificationIssue -IssueList $issues -Type "file_changed_during_hash" -RelativePath $expectedPath -Detail "Canonical file changed while it was hashed."
                continue
            }
            $result.totals.checked_files++
            $result.totals.checked_bytes += [int64]$after.Length
            if ($actualHash -ne [string]$expectedEntry.sha256) {
                Add-CurrentVerificationIssue -IssueList $issues -Type "sha256_mismatch" -RelativePath $expectedPath -Detail "Canonical file SHA-256 differs from owner-seal inventory."
            }
        }
        if ($result.totals.actual_files - $result.totals.excluded_files -ne $expectedRecords.Count) {
            Add-CurrentVerificationIssue -IssueList $issues -Type "actual_file_count_mismatch" -Detail "Canonical actual file count differs from owner-seal inventory."
        }
        $actualRecords = New-Object "System.Collections.Generic.List[object]"
        foreach ($expectedPath in $expected.Keys) {
            if ($actualByPath.ContainsKey($expectedPath)) {
                $actualFile = Get-Item -LiteralPath $actualByPath[$expectedPath].FullName -Force
                [void]$actualRecords.Add([pscustomobject]@{
                        relative_path = $expectedPath
                        size = [int64]$actualFile.Length
                        sha256 = ((Get-Sha256 -Path $actualFile.FullName).ToLowerInvariant())
                    })
            }
        }
        $actualTotalBytes = [int64](($actualRecords | Measure-Object -Property size -Sum).Sum)
        if ($actualTotalBytes -ne $manifestTotalBytes) {
            Add-CurrentVerificationIssue -IssueList $issues -Type "total_bytes_mismatch" -Detail "Canonical actual byte total differs from owner-seal inventory."
        }
        $actualAggregate = Get-CurrentAggregateSha256 -Records $actualRecords
        if ($actualAggregate -ne $manifestAggregate.ToLowerInvariant()) {
            Add-CurrentVerificationIssue -IssueList $issues -Type "aggregate_mismatch" -Detail "Canonical actual aggregate differs from owner-seal inventory."
        }
        if ($issues.Count -eq 0 -and $result.totals.checked_files -gt 0) {
            $result.status = "pass"
        }
        else {
            $result.status = "fail"
            $exitCode = 1
        }
    }
    catch {
        $exitCode = 1
        $result.status = "fail"
        $result.error = $_.Exception.Message
        $result.error_details = [ordered]@{
            script_line_number = $_.InvocationInfo.ScriptLineNumber
            position_message = $_.InvocationInfo.PositionMessage
            exception_type = $_.Exception.GetType().FullName
        }
    }
    finally {
        $result.completed_at_utc = [DateTime]::UtcNow.ToString("o")
        $result.issues = $issues.ToArray()
        $result.totals.issue_count = $issues.Count
        try {
            Write-JsonAtomic -Value $result -Path $currentResultPath -Root $currentDstockDailyRoot
        }
        catch {
            Write-Warning "Could not write current owner-seal verification JSON: $($_.Exception.Message)"
            $exitCode = 1
        }
    }

    if ($exitCode -eq 0) {
        Write-Host "[OK] Current shared data owner-seal v2 passed full SHA-256 verification."
        Write-Host "[OK] Files: $($result.totals.checked_files); bytes: $($result.totals.checked_bytes)"
    }
    else {
        Write-Host "[ERROR] Current shared data owner-seal verification failed." -ForegroundColor Red
        if ($null -ne $result.error) {
            Write-Host "[ERROR] $($result.error)" -ForegroundColor Red
        }
        foreach ($issue in @($result.issues) | Select-Object -First 20) {
            Write-Host ("  [{0}] {1}: {2}" -f $issue.type, $issue.relative_path, $issue.detail)
        }
    }
    Write-Host "Machine-readable result: $currentResultPath"
    return $exitCode
}

function Write-JsonAtomic {
    param(
        [Parameter(Mandatory = $true)]$Value,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $false)][string]$Root
    )

    $writeRoot = if ([string]::IsNullOrWhiteSpace($Root)) { $dstockDailyRoot } else { $Root }
    $safePath = Assert-PathUnderRoot -Path $Path -Root $writeRoot
    $parent = Split-Path -Parent $safePath
    Assert-NoExistingReparseComponent -Path $parent -StopRoot $writeRoot
    [void](New-Item -ItemType Directory -Path $parent -Force)
    $temporary = Join-Path $parent (".{0}.{1}.tmp" -f ([System.IO.Path]::GetFileName($safePath)), [guid]::NewGuid().ToString("N"))
    $json = $Value | ConvertTo-Json -Depth 10
    Set-Content -LiteralPath $temporary -Value $json -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $safePath -Force
}

if ($CurrentOwnerSeal) {
    $currentExitCode = Invoke-CurrentOwnerSealVerification
    exit $currentExitCode
}

$startedAt = [DateTime]::UtcNow
$issues = New-Object "System.Collections.Generic.List[object]"
$result = [ordered]@{
    schema_version = 1
    status = "running"
    started_at_utc = $startedAt.ToString("o")
    completed_at_utc = $null
    canonical_root = $sharedRoot
    verification_state_root = $controlRoot
    manifest_path = $manifestPath
    hash_algorithm = "SHA256"
    totals = [ordered]@{
        manifest_files = 0
        actual_files = 0
        checked_files = 0
        checked_bytes = 0
        issue_count = 0
    }
    issues = @()
    error = $null
    error_details = $null
}

$exitCode = 0

try {
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "Ready manifest is missing: $manifestPath"
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([int]$manifest.schema_version -ne 1) {
        throw "Unsupported manifest schema version: $($manifest.schema_version)"
    }
    if ([string]$manifest.status -ne "ready") {
        throw "Manifest status is not ready: $($manifest.status)"
    }
    if (-not ([string]$manifest.canonical_root).Equals($sharedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Manifest canonical root does not match the fixed canonical root."
    }
    if ([string]$manifest.hash_algorithm -ne "SHA256") {
        throw "Manifest hash algorithm must be SHA256."
    }

    $entries = @($manifest.files)
    $expected = New-Object "System.Collections.Generic.Dictionary[string,object]" (
        [System.StringComparer]::OrdinalIgnoreCase
    )
    $result.totals.manifest_files = $entries.Count

    foreach ($entry in $entries) {
        $relativePath = [string]$entry.relative_path
        try {
            if ([string]::IsNullOrWhiteSpace($relativePath)) {
                throw "Relative path is empty."
            }
            if ([System.IO.Path]::IsPathRooted($relativePath)) {
                throw "Relative path is rooted."
            }
            if ($relativePath.StartsWith("_metadata\", [System.StringComparison]::OrdinalIgnoreCase)) {
                throw "Data entries cannot point into _metadata."
            }
            $targetPath = Assert-PathUnderRoot -Path (Join-Path $sharedRoot $relativePath) -Root $sharedRoot
            $normalizedRelative = Get-RelativePathFromRoot -Path $targetPath -Root $sharedRoot
            if (-not $normalizedRelative.Equals($relativePath.Replace("/", "\"), [System.StringComparison]::OrdinalIgnoreCase)) {
                throw "Relative path is not normalized."
            }
            if ($expected.ContainsKey($normalizedRelative)) {
                throw "Duplicate relative path in manifest."
            }
            $expected.Add($normalizedRelative, $entry)

            if (-not (Test-Path -LiteralPath $targetPath -PathType Leaf)) {
                $issues.Add([pscustomobject]@{
                    type = "missing_file"
                    relative_path = $normalizedRelative
                    detail = "Manifest file is missing."
                })
                continue
            }
            $file = Get-Item -LiteralPath $targetPath -Force
            if (($file.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                $issues.Add([pscustomobject]@{
                    type = "reparse_point"
                    relative_path = $normalizedRelative
                    detail = "Manifest file is a reparse point."
                })
                continue
            }
            if ([int64]$file.Length -ne [int64]$entry.length_bytes) {
                $issues.Add([pscustomobject]@{
                    type = "length_mismatch"
                    relative_path = $normalizedRelative
                    detail = "Expected $($entry.length_bytes) bytes; found $($file.Length)."
                })
                continue
            }
            $actualHash = Get-Sha256 $targetPath
            $result.totals.checked_files++
            $result.totals.checked_bytes += [int64]$file.Length
            if ($actualHash -ne ([string]$entry.sha256).ToUpperInvariant()) {
                $issues.Add([pscustomobject]@{
                    type = "sha256_mismatch"
                    relative_path = $normalizedRelative
                    detail = "SHA-256 differs from manifest."
                })
            }
        }
        catch {
            $issues.Add([pscustomobject]@{
                type = "invalid_manifest_entry"
                relative_path = $relativePath
                detail = $_.Exception.Message
            })
        }
    }

    $actualFiles = @(
        Get-SafeFiles $sharedRoot |
            Where-Object {
                $relative = Get-RelativePathFromRoot -Path $_.FullName -Root $sharedRoot
                -not $relative.StartsWith("_metadata\", [System.StringComparison]::OrdinalIgnoreCase)
            }
    )
    $result.totals.actual_files = $actualFiles.Count
    foreach ($file in $actualFiles) {
        $relativePath = Get-RelativePathFromRoot -Path $file.FullName -Root $sharedRoot
        if (-not $expected.ContainsKey($relativePath)) {
            $issues.Add([pscustomobject]@{
                type = "unexpected_file"
                relative_path = $relativePath
                detail = "Canonical file is not listed in manifest."
            })
        }
    }

    if ([int64]$manifest.totals.file_count -ne [int64]$entries.Count) {
        $issues.Add([pscustomobject]@{
            type = "manifest_total_mismatch"
            relative_path = $null
            detail = "Manifest totals.file_count does not equal the number of file entries."
        })
    }
    $entryBytes = [int64](($entries | Measure-Object -Property length_bytes -Sum).Sum)
    if ([int64]$manifest.totals.bytes -ne $entryBytes) {
        $issues.Add([pscustomobject]@{
            type = "manifest_total_mismatch"
            relative_path = $null
            detail = "Manifest totals.bytes does not equal the sum of file entry lengths."
        })
    }

    $result.issues = $issues.ToArray()
    $result.totals.issue_count = $issues.Count
    $result.status = if ($issues.Count -eq 0) { "pass" } else { "fail" }
    if ($issues.Count -gt 0) {
        $exitCode = 1
    }
}
catch {
    $exitCode = 1
    $result.status = "fail"
    $result.error = $_.Exception.Message
    $result.error_details = [ordered]@{
        script_line_number = $_.InvocationInfo.ScriptLineNumber
        position_message = $_.InvocationInfo.PositionMessage
        script_stack_trace = $_.ScriptStackTrace
        exception_type = $_.Exception.GetType().FullName
    }
}
finally {
    $result.completed_at_utc = [DateTime]::UtcNow.ToString("o")
    try {
        Write-JsonAtomic -Value $result -Path $resultPath
    }
    catch {
        Write-Warning "Could not write verification JSON: $($_.Exception.Message)"
        $exitCode = 1
    }
}

if ($exitCode -eq 0) {
    Write-Host "[OK] Shared reference data passed full SHA-256 verification."
    Write-Host "[OK] Files: $($result.totals.checked_files); bytes: $($result.totals.checked_bytes)"
}
else {
    Write-Host "[ERROR] Shared reference data verification failed." -ForegroundColor Red
    if ($null -ne $result.error) {
        Write-Host "[ERROR] $($result.error)" -ForegroundColor Red
    }
    foreach ($issue in @($result.issues) | Select-Object -First 20) {
        Write-Host ("  [{0}] {1}: {2}" -f $issue.type, $issue.relative_path, $issue.detail)
    }
    if (@($result.issues).Count -gt 20) {
        Write-Host "  ... more issues are recorded in JSON."
    }
}
Write-Host "Machine-readable result: $resultPath"
exit $exitCode
