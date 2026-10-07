[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$FixedRoot = 'D:\stock\GitHub'
$Venv = Join-Path $FixedRoot '.venv'
$Scripts = Join-Path $Venv 'Scripts'
$Python = Join-Path $Scripts 'python.exe'
$Activate = Join-Path $Scripts 'activate.bat'

function Normalize-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    try {
        $full = [IO.Path]::GetFullPath($Path)
        $pathRoot = [IO.Path]::GetPathRoot($full)
        if ($pathRoot -and $full -ine $pathRoot) { return $full.TrimEnd('\') }
        return $full
    }
    catch { throw "Unable to resolve managed venv path safely: $Path" }
}

function Assert-ManagedPath {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][ValidateSet('Directory', 'File')][string]$Kind
    )

    $rootFull = Normalize-FullPath $FixedRoot
    $full = Normalize-FullPath $Path
    if ($full -ine $rootFull -and -not $full.StartsWith($rootFull + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label escapes the fixed install root: $full"
    }

    $cursor = $full
    while ($true) {
        $item = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
        if ($null -eq $item) { throw "$Label path is missing: $cursor" }
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Label path is a reparse point and is not accepted: $cursor"
        }
        $parent = [IO.Directory]::GetParent($cursor)
        if ($null -eq $parent) {
            $pathRoot = Normalize-FullPath ([IO.Path]::GetPathRoot($cursor))
            if ($cursor -ine $pathRoot) {
                throw "$Label path could not be bounded by a filesystem root: $full"
            }
            break
        }
        $next = Normalize-FullPath $parent.FullName
        if ($next -ieq $cursor) {
            throw "$Label path could not advance to a filesystem ancestor: $full"
        }
        $cursor = $next
    }

    $leaf = Get-Item -LiteralPath $full -Force -ErrorAction Stop
    if ($Kind -eq 'Directory' -and -not $leaf.PSIsContainer) {
        throw "$Label is not a directory: $full"
    }
    if ($Kind -eq 'File' -and $leaf.PSIsContainer) {
        throw "$Label is not a file: $full"
    }
}

try {
    Assert-ManagedPath -Label 'managed venv' -Path $Venv -Kind Directory
    Assert-ManagedPath -Label 'managed venv Scripts' -Path $Scripts -Kind Directory
    Assert-ManagedPath -Label 'managed venv Python' -Path $Python -Kind File
    Assert-ManagedPath -Label 'managed venv activation script' -Path $Activate -Kind File
    Write-Output "[OK] Managed venv paths verified under $FixedRoot"
    exit 0
}
catch {
    Write-Output '[ERROR] Managed venv path verification failed; no changes were made.'
    Write-Output $_.Exception.Message
    exit 2
}
