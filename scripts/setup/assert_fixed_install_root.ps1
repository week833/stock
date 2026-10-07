[CmdletBinding()]
param(
    [Parameter(DontShow = $true)]
    [string]$TestRoot
)

$ErrorActionPreference = 'Stop'
$Root = 'D:\stock\GitHub'
try {
    if ($TestRoot) {
        $Root = [IO.Path]::GetFullPath($TestRoot)
    }
    $item = Get-Item -LiteralPath $Root -Force -ErrorAction Stop
    if (-not $item.PSIsContainer -or (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw "Project root is missing or is not an ordinary directory: $Root"
    }
    foreach ($marker in @('STOCK_SETUP_MANAGER.cmd', 'scripts\setup\install_d_stock_env.ps1')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Root $marker) -PathType Leaf)) {
            throw "Project marker is missing: $marker"
        }
    }
    Write-Output "[OK] Stock toolkit project root: $Root"
    exit 0
} catch {
    Write-Output '[ERROR] Stock toolkit project root check failed.'
    Write-Output $_.Exception.Message
    exit 2
}
