[CmdletBinding()]
param(
    [string]$Root = 'D:\stock\GitHub',
    [switch]$All, [switch]$Legacy,
    [switch]$Check, [switch]$DryRun, [switch]$Verify
)

function Get-SourceDestination {
    param([string]$Root, [string]$RelativeTarget)
    if ([string]::IsNullOrWhiteSpace($RelativeTarget) -or
        [IO.Path]::IsPathRooted($RelativeTarget) -or $RelativeTarget.Contains(':')) {
        throw "Source target must be relative: $RelativeTarget"
    }
    $repos = [IO.Path]::GetFullPath((Join-Path $Root 'repos')).TrimEnd('\')
    $target = [IO.Path]::GetFullPath((Join-Path $repos $RelativeTarget)).TrimEnd('\')
    if (-not $target.StartsWith($repos + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Source target must remain inside repos: $RelativeTarget"
    }
    return $target
}

function Assert-SourceWritePath {
    param([string]$Root, [string]$Target)
    $repos = [IO.Path]::GetFullPath((Join-Path $Root 'repos')).TrimEnd('\')
    if (-not $Target.StartsWith($repos + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Write target must remain inside repos: $Target"
    }
    $cursor = [IO.Path]::GetDirectoryName($Target)
    while ($cursor) {
        $item = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
        if ($null -ne $item -and (-not $item.PSIsContainer -or
            (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0))) {
            throw "Write ancestor is not a regular directory: $cursor"
        }
        $parent = [IO.Directory]::GetParent($cursor)
        if ($null -eq $parent) { break }
        $cursor = $parent.FullName
    }
}

function Get-SourcePlan {
    param([string]$Root, [string]$ManifestPath, [switch]$All, [switch]$Legacy)
    if ($All -and $Legacy) { throw 'Choose --all or --legacy.' }
    if (-not $ManifestPath) { $ManifestPath = Join-Path $Root 'scripts\sources\source_manifest_v1.json' }
    $manifest = [IO.File]::ReadAllText($ManifestPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
    foreach ($source in $manifest.sources) {
        $selected = if ($Legacy) { $source.scope -eq 'legacy' }
            else { $source.scope -eq 'primary' -and ($All -or $source.default_install) }
        if (-not $selected) { continue }
        $target = Get-SourceDestination -Root $Root -RelativeTarget ([string]$source.target)
        $item = Get-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
        $status = if ($null -eq $item) { 'missing' } elseif ($item.PSIsContainer) { 'existing' } else { 'file' }
        [pscustomobject]@{
            Url = [string]$source.url; RelativeTarget = [string]$source.target
            Target = $target; Scope = [string]$source.scope
            DefaultInstall = [bool]$source.default_install; Status = $status
        }
    }
}

function Find-SourceGit {
    $command = Get-Command git.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return $command.Source }
    foreach ($base in @($env:ProgramFiles, $env:LOCALAPPDATA)) {
        if (-not $base) { continue }
        foreach ($relative in @('Git\cmd\git.exe', 'Programs\Git\cmd\git.exe')) {
            $path = Join-Path $base $relative
            if (Test-Path -LiteralPath $path -PathType Leaf) { return $path }
        }
    }
}

function Resolve-SourceGit {
    $git = Find-SourceGit
    if ($git) { return $git }
    $winget = Get-Command winget.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $winget) { throw 'Git is required for a new download. Install Git and retry.' }
    & $winget.Source install --id Git.Git --exact --source winget --accept-package-agreements --accept-source-agreements | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Git installation failed.' }
    $git = Find-SourceGit
    if (-not $git) { throw 'Git was not found after installation. Open a new terminal and retry.' }
    return $git
}

function Invoke-SourceClone {
    param([string]$GitPath, [string]$Url, [string]$PendingPath)
    $savedPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $GitPath -c core.longpaths=true clone --depth 1 -- $Url $PendingPath 2>&1 | Out-Host
        return [int]$LASTEXITCODE
    }
    finally { $ErrorActionPreference = $savedPreference }
}

function Invoke-SourceDownload {
    param([string]$Root, [string]$ManifestPath, [switch]$All, [switch]$Legacy,
        [switch]$Check, [switch]$DryRun, [switch]$Verify)
    $plan = @(Get-SourcePlan -Root $Root -ManifestPath $ManifestPath -All:$All -Legacy:$Legacy)
    foreach ($source in $plan) {
        if ($source.Status -eq 'file') { throw "Source target is a file: $($source.Target)" }
    }
    $git = $null
    foreach ($source in $plan) {
        $status = 'planned'
        $pending = $null
        if ($source.Status -eq 'existing') {
            # Keep this no-BOM script readable by Windows PowerShell 5.1.
            $notice = -join [char[]](26082,26377,36335,24465,65292,26410,19979,36617,47,26410,26356,26032)
            Write-Host "[SKIP] $notice : $($source.Target)"
            $status = 'skipped'
        }
        elseif ($Check -or $DryRun -or $Verify) {
            Write-Host "[PLAN] Missing: $($source.Target) <- $($source.Url)"
        }
        else {
            try {
                if ([string]::IsNullOrWhiteSpace($source.Url)) { throw 'Source URL is empty.' }
                Assert-SourceWritePath -Root $Root -Target $source.Target
                if (-not $git) { $git = Resolve-SourceGit }
                $parent = [IO.Path]::GetDirectoryName($source.Target)
                [IO.Directory]::CreateDirectory($parent) | Out-Null
                $pending = Join-Path $parent ('.stock-source-pending-' + [Guid]::NewGuid().ToString('N'))
                if (Test-Path -LiteralPath $pending) { throw "Pending path already exists: $pending" }
                $code = Invoke-SourceClone -GitPath $git -Url $source.Url -PendingPath $pending
                if ($code -ne 0) { throw "Git clone failed with exit code $code" }
                Assert-SourceWritePath -Root $Root -Target $source.Target
                if (Test-Path -LiteralPath $source.Target) { throw "Target appeared during download: $($source.Target)" }
                [IO.Directory]::Move($pending, $source.Target)
                $pending = $null
                $status = 'downloaded'
                Write-Host "[DOWNLOADED] $($source.Target)"
            }
            catch {
                $status = 'failed'
                Write-Host "[ERROR] $($_.Exception.Message); pending download retained: $pending"
            }
        }
        [pscustomobject]@{ Url = $source.Url; Target = $source.Target; Status = $status; PendingPath = $pending }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        $results = @(Invoke-SourceDownload -Root $Root -All:$All -Legacy:$Legacy -Check:$Check -DryRun:$DryRun -Verify:$Verify)
        if (@($results | Where-Object { $_.Status -eq 'failed' }).Count) { exit 1 }
        exit 0
    }
    catch { Write-Host "[ERROR] $($_.Exception.Message)"; exit 1 }
}
