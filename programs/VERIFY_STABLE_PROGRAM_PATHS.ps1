[CmdletBinding()]
param(
    [string]$Root = "",
    [switch]$IncludeVendorPaths,
    [switch]$SkipMixPaths
)

$ErrorActionPreference = "Stop"
$programsDirectory = Split-Path -Parent $PSCommandPath
if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Split-Path -Parent $programsDirectory
}

$rootPath = (Resolve-Path -LiteralPath $Root).Path.TrimEnd("\")
$manifestPath = Join-Path $programsDirectory "STABLE_PROGRAM_PATHS.txt"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Canonical stable program path manifest was not found: $manifestPath"
}
$manifestFiles = @((Get-Item -LiteralPath $manifestPath))

$registeredPaths = New-Object "System.Collections.Generic.HashSet[string]" (
    [System.StringComparer]::OrdinalIgnoreCase
)
$duplicatePaths = New-Object "System.Collections.Generic.List[string]"
$missingPaths = New-Object "System.Collections.Generic.List[string]"

foreach ($manifest in $manifestFiles) {
    foreach ($rawLine in Get-Content -LiteralPath $manifest.FullName -Encoding UTF8) {
        $relativePath = $rawLine.Trim()
        if (-not $relativePath -or $relativePath.StartsWith("#")) {
            continue
        }
        $relativePath = $relativePath.Replace("/", "\").TrimStart("\")
        if ([System.IO.Path]::IsPathRooted($relativePath) -or -not $relativePath.StartsWith("programs\")) {
            throw "Invalid stable path in $($manifest.Name): $rawLine"
        }
        if (-not $registeredPaths.Add($relativePath)) {
            $duplicatePaths.Add($relativePath)
            continue
        }
        $absolutePath = Join-Path $rootPath $relativePath
        if (-not (Test-Path -LiteralPath $absolutePath -PathType Leaf)) {
            $missingPaths.Add($relativePath)
        }
    }
}

$retiredRootRelativePaths = @(
    "CONFIGURE_WINDOWS_ENV.cmd",
    "DOWNLOAD_LEGACY_SOURCES.cmd",
    "DOWNLOAD_STOCK_SOURCES.cmd",
    "INSTALL_D_STOCK_ENV.cmd",
    "INSTALL_D_STOCK_ENV_FULL.cmd",
    "OPEN_STOCK_TERMINAL.cmd",
    "REPAIR_STOCK_PATHS.cmd",
    "RUN_STOCK_PYTHON.cmd",
    "STOCK_SETUP_MANAGER.cmd",
    "VERIFY_STOCK_ENV.cmd",
    "install_tw_stock_ai_env.cmd",
    "requirements.txt",
    "requirements_tw_stock_ai.txt",
    "external_stock_repositories.md",
    "docs\external_stock_repositories.md",
    "docs\installer_troubleshooting.md",
    "docs\legacy_compatibility.md",
    "docs\repository_structure.md",
    "scripts",
    "sources"
)
$presentRetiredRootPaths = New-Object "System.Collections.Generic.List[string]"
foreach ($relativePath in $retiredRootRelativePaths) {
    if (Test-Path -LiteralPath (Join-Path $rootPath $relativePath)) {
        $presentRetiredRootPaths.Add($relativePath)
    }
}

$programExtensions = New-Object "System.Collections.Generic.HashSet[string]" (
    [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($extension in ".py", ".cmd", ".mjs", ".ps1") {
    [void]$programExtensions.Add($extension)
}

$skippedDirectoryNames = New-Object "System.Collections.Generic.HashSet[string]" (
    [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($name in @(
    "backup",
    "backups",
    "cache",
    ".cache",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "venv",
    "node_modules",
    ".git",
    "archive",
    "archives",
    "generated",
    "log",
    "logs",
    "temp",
    "tmp"
)) {
    [void]$skippedDirectoryNames.Add($name)
}

$unregisteredPaths = New-Object "System.Collections.Generic.List[string]"
$scanStack = New-Object "System.Collections.Generic.Stack[string]"
$scanStack.Push($programsDirectory)
while ($scanStack.Count -gt 0) {
    $current = $scanStack.Pop()
    foreach ($item in Get-ChildItem -LiteralPath $current -Force) {
        if ($item.PSIsContainer) {
            if (
                $skippedDirectoryNames.Contains($item.Name) -or
                ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
            ) {
                continue
            }
            $scanStack.Push($item.FullName)
            continue
        }
        if (-not $programExtensions.Contains($item.Extension)) {
            continue
        }
        $relativePath = $item.FullName.Substring($rootPath.Length).TrimStart("\")
        if (-not $registeredPaths.Contains($relativePath)) {
            $unregisteredPaths.Add($relativePath)
        }
    }
}

$maxManagedNameLength = 48
$maxManagedPathLength = 200
$maxVendorPathLength = 240
$pathPolicyViolations = New-Object "System.Collections.Generic.List[string]"
function Test-IsExcludedPath {
    param(
        [string]$Path,
        [string[]]$ExcludedRoots
    )
    foreach ($excludedRoot in $ExcludedRoots) {
        $normalized = $excludedRoot.TrimEnd("\")
        if ($Path -ieq $normalized -or $Path.StartsWith($normalized + "\", [StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

function Test-ManagedPathTree {
    param(
        [string]$TreeRoot,
        [string[]]$ExcludedRoots
    )
    $stack = New-Object "System.Collections.Generic.Stack[string]"
    $stack.Push($TreeRoot)
    while ($stack.Count -gt 0) {
        $current = $stack.Pop()
        foreach ($item in Get-ChildItem -LiteralPath $current -Force -ErrorAction SilentlyContinue) {
            $fullName = $item.FullName.TrimEnd("\")
            if ($item.PSIsContainer) {
                if ($skippedDirectoryNames.Contains($item.Name) -or (Test-IsExcludedPath $fullName $ExcludedRoots)) {
                    continue
                }
                if ($item.Name.Length -gt $maxManagedNameLength) {
                    $pathPolicyViolations.Add("folder name > ${maxManagedNameLength}: $fullName")
                }
                if ($fullName.Length -gt $maxManagedPathLength) {
                    $pathPolicyViolations.Add("managed path > ${maxManagedPathLength}: $fullName")
                }
                if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) {
                    $stack.Push($fullName)
                }
                continue
            }
            if ($fullName.Length -gt $maxManagedPathLength) {
                $pathPolicyViolations.Add("managed path > ${maxManagedPathLength}: $fullName")
            }
            if ($programExtensions.Contains($item.Extension) -and $item.BaseName.Length -gt $maxManagedNameLength) {
                $pathPolicyViolations.Add("program name > ${maxManagedNameLength}: $fullName")
            }
        }
    }
}

function Test-VendorPathTree {
    param([string]$TreeRoot)
    if (-not (Test-Path -LiteralPath $TreeRoot -PathType Container)) {
        return
    }
    $stack = New-Object "System.Collections.Generic.Stack[string]"
    $stack.Push($TreeRoot)
    while ($stack.Count -gt 0) {
        $current = $stack.Pop()
        foreach ($item in Get-ChildItem -LiteralPath $current -Force -ErrorAction SilentlyContinue) {
            if ($item.FullName.Length -gt $maxVendorPathLength) {
                $pathPolicyViolations.Add("vendor path > ${maxVendorPathLength}: $($item.FullName)")
            }
            if ($item.PSIsContainer -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) {
                $stack.Push($item.FullName)
            }
        }
    }
}

$vendorRoot = Join-Path $rootPath "GitHub\repos"
Test-ManagedPathTree -TreeRoot $rootPath -ExcludedRoots @(
    (Join-Path $rootPath "backup"),
    (Join-Path $rootPath "GitHub"),
    (Join-Path $rootPath ".agents"),
    (Join-Path $rootPath ".codex"),
    (Join-Path $rootPath ".codex-tmp"),
    (Join-Path $rootPath "data"),
    (Join-Path $rootPath "logs"),
    (Join-Path $rootPath "_DELETE_OLD_CONTENT_20260830"),
    (Join-Path $rootPath "programs\single_stock_analysis\research_snapshots"),
    (Join-Path $rootPath "outputs"),
    (Join-Path $rootPath "reports"),
    (Join-Path $rootPath "single_stock_analize"),
    (Join-Path $rootPath "single_stock_tomorrow_open"),
    (Join-Path $rootPath "test_scratch"),
    (Join-Path $rootPath "tmp")
)
if ($IncludeVendorPaths) {
    Test-VendorPathTree -TreeRoot (Join-Path $rootPath "GitHub")
}

if (-not $SkipMixPaths) {
    $mixRoot = "D:\MIX All"
    if (Test-Path -LiteralPath $mixRoot -PathType Container) {
        Test-ManagedPathTree -TreeRoot $mixRoot -ExcludedRoots @(
            (Join-Path $mixRoot "backup"),
            (Join-Path $mixRoot "data"),
            (Join-Path $mixRoot "logs"),
            (Join-Path $mixRoot "models"),
            (Join-Path $mixRoot "outputs"),
            (Join-Path $mixRoot "reports"),
            (Join-Path $mixRoot "runtime\verification-temp"),
            (Join-Path $mixRoot "tmp"),
            (Join-Path $mixRoot "cache\mitake_watchlist\attempts")
        )
    }
}

if ($duplicatePaths.Count -gt 0) {
    Write-Host "[ERROR] Duplicate stable paths:" -ForegroundColor Red
    $duplicatePaths | Sort-Object | ForEach-Object { Write-Host "  $_" }
}
if ($missingPaths.Count -gt 0) {
    Write-Host "[ERROR] Stable paths were deleted, moved, or renamed:" -ForegroundColor Red
    $missingPaths | Sort-Object | ForEach-Object { Write-Host "  $_" }
}
if ($unregisteredPaths.Count -gt 0) {
    Write-Host "[ERROR] New program files are not registered in a stable path manifest:" -ForegroundColor Red
    $unregisteredPaths | Sort-Object | ForEach-Object { Write-Host "  $_" }
}
if ($presentRetiredRootPaths.Count -gt 0) {
    Write-Host "[ERROR] Retired root paths reappeared; use the canonical paths under programs\environment_setup:" -ForegroundColor Red
    $presentRetiredRootPaths | Sort-Object | ForEach-Object { Write-Host "  $_" }
}
if ($pathPolicyViolations.Count -gt 0) {
    Write-Host "[ERROR] Path naming policy violations:" -ForegroundColor Red
    $pathPolicyViolations | Sort-Object | Select-Object -First 50 | ForEach-Object { Write-Host "  $_" }
    if ($pathPolicyViolations.Count -gt 50) {
        Write-Host "  ... and $($pathPolicyViolations.Count - 50) more"
    }
}
if (
    $duplicatePaths.Count -gt 0 -or
    $missingPaths.Count -gt 0 -or
    $unregisteredPaths.Count -gt 0 -or
    $presentRetiredRootPaths.Count -gt 0 -or
    $pathPolicyViolations.Count -gt 0
) {
    exit 1
}

Write-Host "[OK] Stable program paths: $($registeredPaths.Count) files; manifests: $($manifestFiles.Count)."
Write-Host "[OK] Retired root program, setup, source, and compatibility paths remain absent."
if ($IncludeVendorPaths) {
    Write-Host "[OK] Path policy: managed names <= $maxManagedNameLength, managed paths <= $maxManagedPathLength, vendor paths <= $maxVendorPathLength."
}
else {
    Write-Host "[OK] Path policy: managed source/config names <= $maxManagedNameLength and paths <= $maxManagedPathLength."
    Write-Host "[INFO] Generated data/artifact trees and external GitHub repositories were skipped; use -IncludeVendorPaths for the optional vendor audit."
}
Write-Host "Existing paths are fixed. Add intentionally adopted files to the canonical manifest."
exit 0
