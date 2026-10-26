Set-StrictMode -Version Latest

$script:CleanupSchema = 'stock-cleanup-plan-v1'
$script:CleanupToolVersion = '1.0.0'
$script:MaintenanceRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
$script:CentralRoot = 'D:\TWStockData\services\market_update'
$script:CentralActiveRunPath = 'D:\TWStockData\services\market_update\state\active_run.json'
$script:AcceptancePath = 'D:\TWStockData\services\acceptance\latest.json'

function Get-CanonicalPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { throw 'path_empty' }
    if (-not [IO.Path]::IsPathRooted($Path)) { throw "path_not_absolute:$Path" }
    $full = [IO.Path]::GetFullPath($Path)
    if ($full.Length -gt 3) { $full = $full.TrimEnd('\') }
    return $full
}

function Test-PathWithinRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )
    $p = Get-CanonicalPath $Path
    $r = Get-CanonicalPath $Root
    if ($p.Equals($r, [StringComparison]::OrdinalIgnoreCase)) { return $false }
    $prefix = if ($r.EndsWith('\')) { $r } else { $r + '\' }
    return $p.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
}

function Test-PathWithinOrEqualsRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )
    $p = Get-CanonicalPath $Path
    $r = Get-CanonicalPath $Root
    if ($p.Equals($r, [StringComparison]::OrdinalIgnoreCase)) { return $true }
    $prefix = if ($r.EndsWith('\')) { $r } else { $r + '\' }
    return $p.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
}

function Get-RelativePathCompat {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )
    $p = Get-CanonicalPath $Path
    $r = Get-CanonicalPath $Root
    $rootUri = New-Object Uri (($r.TrimEnd('\') + '\'))
    $pathUri = New-Object Uri $p
    $relative = $rootUri.MakeRelativeUri($pathUri).ToString()
    return [Uri]::UnescapeDataString($relative).Replace('/', '\')
}

function Get-Sha256ForFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "file_missing:$Path" }
    return ((Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash).ToLowerInvariant()
}

function Get-Sha256ForText {
    param([Parameter(Mandatory = $true)][string]$Text)
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $bytes = $utf8.GetBytes($Text)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function ConvertTo-CanonicalJson {
    param([Parameter(Mandatory = $true)][object]$Value)
    return ($Value | ConvertTo-Json -Depth 100 -Compress)
}

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Text
    )
    $parent = Split-Path -Parent (Get-CanonicalPath $Path)
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force -ErrorAction Stop | Out-Null
    }
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText((Get-CanonicalPath $Path), $Text, $utf8)
}

function Read-JsonFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    try { return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop) }
    catch { return $null }
}

function Get-JsonPropertyValue {
    param(
        [Parameter(Mandatory = $false)][object]$Object,
        [Parameter(Mandatory = $true)][string]$Name
    )
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Get-FileRecord {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )
    $canonical = Get-CanonicalPath $Path
    $item = Get-Item -LiteralPath $canonical -Force -ErrorAction Stop
    if ($item.PSIsContainer) { throw "not_file:$canonical" }
    [ordered]@{
        path = $canonical
        relative_path = Get-RelativePathCompat -Path $canonical -Root $Root
        size = [int64]$item.Length
        sha256 = Get-Sha256ForFile $canonical
        last_write_time_utc = $item.LastWriteTimeUtc.ToString('o')
    }
}

function Get-GitRepositoryRoot {
    param([Parameter(Mandatory = $true)][string]$Path)
    $current = if ((Test-Path -LiteralPath $Path -PathType Container)) {
        Get-CanonicalPath $Path
    } else {
        Split-Path -Parent (Get-CanonicalPath $Path)
    }
    while (-not [string]::IsNullOrWhiteSpace($current)) {
        $gitMarker = Join-Path $current '.git'
        if (Test-Path -LiteralPath $gitMarker) { return $current }
        $parent = Split-Path -Parent $current
        if ($parent -eq $current) { break }
        $current = $parent
    }
    return $null
}

function Get-GitTrackingAssessment {
    param([Parameter(Mandatory = $true)][string]$Path)
    $repo = Get-GitRepositoryRoot $Path
    if ($null -eq $repo) {
        return [pscustomobject]@{ known = $true; tracked = $false; dirty = $false; repository = $null; relative_path = $null; reason = 'not_in_git_repository' }
    }
    if ($null -eq (Get-Command git -ErrorAction SilentlyContinue)) {
        return [pscustomobject]@{ known = $false; tracked = $null; dirty = $null; repository = $repo; relative_path = $null; reason = 'git_unavailable' }
    }
    $relative = (Get-RelativePathCompat -Path (Get-CanonicalPath $Path) -Root $repo).Replace('\', '/')
    try {
        $null = @(& git -C $repo ls-files --error-unmatch -- $relative 2>$null)
        $trackingExit = $LASTEXITCODE
        $statusLines = @(& git -C $repo status --porcelain=v1 --untracked-files=all -- $relative 2>$null)
        $statusExit = $LASTEXITCODE
        if ($statusExit -ne 0) {
            return [pscustomobject]@{ known = $false; tracked = $null; dirty = $null; repository = $repo; relative_path = $relative; reason = 'git_status_query_failed' }
        }
        $tracked = ($trackingExit -eq 0)
        $dirty = ($statusLines.Count -gt 0)
        if ($dirty) {
            return [pscustomobject]@{ known = $true; tracked = $tracked; dirty = $true; repository = $repo; relative_path = $relative; reason = 'git_dirty_or_untracked' }
        }
        if ($tracked) {
            return [pscustomobject]@{ known = $true; tracked = $true; dirty = $false; repository = $repo; relative_path = $relative; reason = 'git_tracked' }
        }
        return [pscustomobject]@{ known = $true; tracked = $false; dirty = $false; repository = $repo; relative_path = $relative; reason = 'git_clean_untracked' }
    }
    catch {
        return [pscustomobject]@{ known = $false; tracked = $null; dirty = $null; repository = $repo; relative_path = $relative; reason = 'git_query_failed' }
    }
}

function Get-AllowlistedRoots {
    return @(
        [ordered]@{ name = 'central_failed_staging'; path = 'D:\TWStockData\services\market_update\staging'; kind = 'central_staging' },
        [ordered]@{ name = 'dstock_program_data'; path = 'D:\TWStockData\program_data\dstock_daily'; kind = 'explicit_temp' },
        [ordered]@{ name = 'mix_all'; path = 'D:\MIX All'; kind = 'explicit_temp' },
        [ordered]@{ name = 'cowork'; path = 'D:\cowork'; kind = 'explicit_temp' }
    )
}

function Get-ActiveRunSnapshot {
    $path = Get-CanonicalPath $script:CentralActiveRunPath
    $hash = $null
    $payload = $null
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        try { $hash = Get-Sha256ForFile $path } catch { $hash = $null }
        $payload = Read-JsonFile $path
    }
    $processId = $null
    $runId = $null
    $status = $null
    if ($null -ne $payload) {
        $processId = [string](Get-JsonPropertyValue -Object $payload -Name 'pid')
        $runId = [string](Get-JsonPropertyValue -Object $payload -Name 'run_id')
        $status = [string](Get-JsonPropertyValue -Object $payload -Name 'status')
    }
    $processAlive = $false
    if ($processId -match '^\d+$' -and [int64]$processId -gt 0) {
        try { $processAlive = $null -ne (Get-Process -Id ([int]$processId) -ErrorAction Stop) } catch { $processAlive = $false }
    }
    return [ordered]@{
        path = $path
        exists = (Test-Path -LiteralPath $path -PathType Leaf)
        sha256 = $hash
        run_id = $runId
        pid = $processId
        status = $status
        process_alive = $processAlive
        observed_at_utc = [DateTime]::UtcNow.ToString('o')
    }
}

function Get-AcceptanceSnapshot {
    $path = Get-CanonicalPath $script:AcceptancePath
    $payload = Read-JsonFile $path
    $hash = $null
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        try { $hash = Get-Sha256ForFile $path } catch { $hash = $null }
    }
    $overall = if ($null -ne $payload) { [string](Get-JsonPropertyValue -Object $payload -Name 'overall_status') } else { $null }
    $status = if ($null -ne $payload) { [string](Get-JsonPropertyValue -Object $payload -Name 'status') } else { $null }
    $blockingCount = 0
    $blocking = if ($null -ne $payload) { Get-JsonPropertyValue -Object $payload -Name 'blocking_failures' } else { $null }
    if ($null -ne $blocking) { $blockingCount = @($blocking).Count }
    $declaredPass = if ($null -ne $payload) { Get-JsonPropertyValue -Object $payload -Name 'pass' } else { $false }
    $isPass = (($overall -in @('complete', 'pass', 'passed')) -or (($status -eq 'complete') -and ($declaredPass -eq $true))) -and ($blockingCount -eq 0)
    return [ordered]@{
        path = $path
        exists = (Test-Path -LiteralPath $path -PathType Leaf)
        sha256 = $hash
        overall_status = $overall
        status = $status
        blocking_failure_count = $blockingCount
        pass = [bool]$isPass
        observed_at_utc = [DateTime]::UtcNow.ToString('o')
    }
}

function Test-ExcludedPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $canonical = Get-CanonicalPath $Path
    $segments = @($canonical -split '[\\/]')
    $blockedExact = @('.git', '.venv', 'venv', '__pycache__', '.pytest_cache', 'node_modules', 'twmix', 'premarket', 'premarket_daytrade_top10', 'daytrade')
    foreach ($segment in $segments) {
        if ($blockedExact -contains $segment.ToLowerInvariant()) { return 'excluded_program_or_runtime_path' }
        if ($segment -match '(?i)^\.python\.recover-backup(?:-|$)') { return 'excluded_program_or_runtime_path' }
    }
    if ($canonical.StartsWith('D:\claude code\twmix', [StringComparison]::OrdinalIgnoreCase)) { return 'excluded_twmix_root' }
    if ($canonical.StartsWith('D:\TWStockData\shared_market_data', [StringComparison]::OrdinalIgnoreCase)) { return 'canonical_shared_market_data' }
    return $null
}

function Test-FormalProtectedPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $canonical = Get-CanonicalPath $Path
    $segments = @($canonical -split '[\\/]')
    $protectedSegments = @('receipt', 'receipts', 'evidence', 'evidence_store', 'pointer', 'pointers', 'manifest', 'manifests', 'formal', 'acceptance')
    foreach ($segment in $segments) {
        if ($protectedSegments -contains $segment.ToLowerInvariant()) { return 'formal_receipt_evidence_pointer_manifest_path' }
    }
    $leaf = Split-Path -Leaf $canonical
    if ($leaf -match '(?i)(^|[._-])(latest|manifest|receipt|evidence|pointer)([._-]|$)') { return 'formal_metadata_filename' }
    return $null
}

function Test-ReparsePath {
    param([Parameter(Mandatory = $true)][string]$Path)
    try {
        $item = Get-Item -LiteralPath (Get-CanonicalPath $Path) -Force -ErrorAction Stop
        return (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)
    } catch { return $true }
}

function Test-ExplicitTemporaryMarker {
    param([Parameter(Mandatory = $true)][string]$Path)
    $canonical = Get-CanonicalPath $Path
    $parts = @($canonical -split '[\\/]')
    foreach ($part in $parts) {
        if ($part -match '(?i)^(preview|tmp|qa|candidate|smoke)$') { return $true }
    }
    $leaf = Split-Path -Leaf $canonical
    return ($leaf -match '(?i)(^|[._ -])(preview|tmp|qa|candidate|smoke)([._ -]|$)')
}

function Get-SafeFileAssessment {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string[]]$AllowlistedRootPaths,
        [Parameter(Mandatory = $true)][string]$Category,
        [switch]$AllowFormalStagingFiles
    )
    $canonical = Get-CanonicalPath $Path
    $root = $null
    foreach ($candidateRoot in $AllowlistedRootPaths) {
        if (Test-PathWithinRoot -Path $canonical -Root $candidateRoot) { $root = Get-CanonicalPath $candidateRoot; break }
    }
    if ($null -eq $root) { return [pscustomobject]@{ safe = $false; reason = 'outside_allowlisted_roots'; path = $canonical } }
    $excluded = Test-ExcludedPath $canonical
    if ($null -ne $excluded) { return [pscustomobject]@{ safe = $false; reason = $excluded; path = $canonical } }
    if (Test-ReparsePath $canonical) { return [pscustomobject]@{ safe = $false; reason = 'reparse_point_or_unreadable'; path = $canonical } }
    if (-not $AllowFormalStagingFiles) {
        $protected = Test-FormalProtectedPath $canonical
        if ($null -ne $protected) { return [pscustomobject]@{ safe = $false; reason = $protected; path = $canonical } }
    } elseif ($canonical -match '(?i)[\\/]receipts?[\\/]|[\\/]evidence(?:[\\/]|$)') {
        return [pscustomobject]@{ safe = $false; reason = 'formal_receipt_or_evidence_path'; path = $canonical }
    }
    $git = Get-GitTrackingAssessment $canonical
    if (-not $git.known) { return [pscustomobject]@{ safe = $false; reason = 'git_status_unknown'; path = $canonical; git = $git } }
    if ($git.dirty) { return [pscustomobject]@{ safe = $false; reason = 'git_dirty_or_untracked'; path = $canonical; git = $git } }
    if (-not $git.tracked -and $null -ne $git.repository) { return [pscustomobject]@{ safe = $false; reason = 'git_untracked'; path = $canonical; git = $git } }
    try {
        $record = Get-FileRecord -Path $canonical -Root $root
        $record.category = $Category
        $record.git_tracked = [bool]$git.tracked
        $record.git_dirty = [bool]$git.dirty
        $record.git_repository = $git.repository
        return [pscustomobject]@{ safe = $true; reason = $null; path = $canonical; root = $root; record = $record; git = $git }
    } catch {
        return [pscustomobject]@{ safe = $false; reason = 'file_read_or_hash_failed'; path = $canonical }
    }
}

function Get-ExplicitDirectoryInventory {
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [Parameter(Mandatory = $true)][string[]]$AllowlistedRootPaths,
        [Parameter(Mandatory = $true)][string]$Category,
        [switch]$AllowFormalStagingFiles
    )
    $dir = Get-CanonicalPath $Directory
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) { throw "directory_missing:$dir" }
    if (Test-ReparsePath $dir) { return [pscustomobject]@{ safe = $false; reason = 'directory_reparse_point'; files = @(); directories = @() } }
    $files = New-Object System.Collections.Generic.List[object]
    $directories = New-Object System.Collections.Generic.List[string]
    try { $items = @(Get-ChildItem -LiteralPath $dir -Force -Recurse -ErrorAction Stop) }
    catch { return [pscustomobject]@{ safe = $false; reason = 'directory_enumeration_failed'; files = @(); directories = @() } }
    foreach ($item in $items) {
        if (Test-ReparsePath $item.FullName) { return [pscustomobject]@{ safe = $false; reason = 'reparse_point_in_directory'; files = @(); directories = @() } }
        if ($item.PSIsContainer) {
            $directories.Add((Get-CanonicalPath $item.FullName))
            continue
        }
        $assessment = Get-SafeFileAssessment -Path $item.FullName -AllowlistedRootPaths $AllowlistedRootPaths -Category $Category -AllowFormalStagingFiles:$AllowFormalStagingFiles
        if (-not $assessment.safe) { return [pscustomobject]@{ safe = $false; reason = $assessment.reason; files = @(); directories = @() } }
        $files.Add($assessment.record)
    }
    if ($files.Count -eq 0) { return [pscustomobject]@{ safe = $false; reason = 'empty_directory_not_confirmed'; files = @(); directories = @() } }
    return [pscustomobject]@{ safe = $true; reason = $null; files = @($files | Sort-Object path); directories = @($directories | Sort-Object Length -Descending) }
}

function Get-ReceiptForRun {
    param([Parameter(Mandatory = $true)][string]$RunId)
    $receiptsRoot = Join-Path $script:CentralRoot 'receipts'
    if (-not (Test-Path -LiteralPath $receiptsRoot -PathType Container)) { return $null }
    $matches = @(Get-ChildItem -LiteralPath $receiptsRoot -File -Recurse -ErrorAction SilentlyContinue)
    foreach ($match in $matches) {
        $payload = Read-JsonFile $match.FullName
        if ($null -ne $payload -and ([string](Get-JsonPropertyValue -Object $payload -Name 'run_id')) -eq $RunId) { return (Get-CanonicalPath $match.FullName) }
    }
    return $null
}

function Get-FailureEvidenceForRun {
    param([Parameter(Mandatory = $true)][string]$RunId)
    $roots = @('D:\TWStockData\migration_snapshots', $script:CentralRoot)
    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root -PathType Container)) { continue }
        $matches = @(Get-ChildItem -LiteralPath $root -File -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "*$RunId*" -or $_.DirectoryName -like "*$RunId*" })
        foreach ($match in $matches) {
            $payload = Read-JsonFile $match.FullName
            if ($null -eq $payload) { continue }
            $status = [string](Get-JsonPropertyValue -Object $payload -Name 'status')
            $childStatus = [string](Get-JsonPropertyValue -Object $payload -Name 'child_status')
            $run = [string](Get-JsonPropertyValue -Object $payload -Name 'run_id')
            if (($run -eq $RunId -or $match.DirectoryName -like "*$RunId*") -and ($status -in @('failed', 'child_failed', 'partial') -or $childStatus -in @('failed', 'child_failed', 'partial'))) {
                return (Get-CanonicalPath $match.FullName)
            }
        }
    }
    return $null
}

function Remove-PlanHashProperty {
    param([Parameter(Mandatory = $true)][object]$Plan)
    $copy = $Plan.PSObject.Copy()
    $copy.PSObject.Properties.Remove('plan_sha256')
    return $copy
}

function Get-PlanContentHash {
    param([Parameter(Mandatory = $true)][object]$Plan)
    return Get-Sha256ForText (ConvertTo-CanonicalJson (Remove-PlanHashProperty $Plan))
}

function Assert-PlanOutputPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $canonical = Get-CanonicalPath $Path
    if (-not (Test-PathWithinRoot -Path $canonical -Root $script:MaintenanceRoot)) { throw 'plan_output_outside_maintenance_root' }
    if ($canonical -match '(?i)\\(?:plans|receipts)\\') { return $canonical }
    throw 'plan_output_must_be_under_plans_or_receipts'
}

function Assert-PlanObjectShape {
    param([Parameter(Mandatory = $true)][object]$Plan)
    if ([string]$Plan.schema -ne $script:CleanupSchema) { throw 'plan_schema_mismatch' }
    if ([string]$Plan.tool_version -ne $script:CleanupToolVersion) { throw 'plan_tool_version_mismatch' }
    if ($null -eq $Plan.plan_sha256 -or [string]::IsNullOrWhiteSpace([string]$Plan.plan_sha256)) { throw 'plan_hash_missing' }
    $calculated = Get-PlanContentHash $Plan
    if ($calculated -ne ([string]$Plan.plan_sha256).ToLowerInvariant()) { throw 'plan_hash_mismatch' }
    if ($null -eq $Plan.allowed_roots -or $null -eq $Plan.targets) { throw 'plan_shape_missing_roots_or_targets' }
    $seen = @{}
    foreach ($target in @($Plan.targets)) {
        if ($null -eq $target.path) { throw 'target_path_missing' }
        $path = Get-CanonicalPath ([string]$target.path)
        if ($seen.ContainsKey($path.ToLowerInvariant())) { throw "target_duplicate:$path" }
        $seen[$path.ToLowerInvariant()] = $true
        if ($target.kind -notin @('file', 'directory')) { throw "target_kind_invalid:$path" }
        if ($target.category -notin @('A-central-failed-staging', 'A-explicit-temporary', 'A-exact-duplicate', 'A-damaged-superseded')) { throw "target_category_invalid:$path" }
        foreach ($propertyName in @('root', 'files', 'directories', 'total_bytes', 'reason_code', 'proof')) {
            if ($null -eq $target.PSObject.Properties[$propertyName]) { throw ('target_property_missing:{0}:{1}' -f $propertyName, $path) }
        }
    }
    return $true
}

function Test-CurrentRunMatchesSnapshot {
    param([Parameter(Mandatory = $true)][object]$PlanSnapshot)
    $current = Get-ActiveRunSnapshot
    if ([bool]$current.exists -ne [bool]$PlanSnapshot.exists) { return $false }
    if ([string]$current.sha256 -ne [string]$PlanSnapshot.sha256) { return $false }
    if ([string]$current.run_id -ne [string]$PlanSnapshot.run_id) { return $false }
    if ([string]$current.status -ne [string]$PlanSnapshot.status) { return $false }
    if ([string]$current.pid -ne [string]$PlanSnapshot.pid) { return $false }
    return $true
}

function Test-AcceptanceMatchesSnapshot {
    param([Parameter(Mandatory = $true)][object]$PlanSnapshot)
    $current = Get-AcceptanceSnapshot
    if (-not $current.exists -or -not $current.pass) { return [pscustomobject]@{ pass = $false; reason = 'acceptance_not_complete_pass'; current = $current } }
    if ([string]$current.sha256 -ne [string]$PlanSnapshot.sha256) { return [pscustomobject]@{ pass = $false; reason = 'acceptance_hash_drift'; current = $current } }
    return [pscustomobject]@{ pass = $true; reason = $null; current = $current }
}

function Test-NoActiveCentralRun {
    $current = Get-ActiveRunSnapshot
    if ($current.status -eq 'running' -or $current.process_alive) {
        return [pscustomobject]@{ pass = $false; reason = 'central_active_run'; snapshot = $current }
    }
    return [pscustomobject]@{ pass = $true; reason = $null; snapshot = $current }
}
