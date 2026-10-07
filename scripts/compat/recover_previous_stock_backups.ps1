[CmdletBinding()]
param(
    [switch]$ListOnly
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$Backups = @(Get-ChildItem -LiteralPath 'D:\' -Directory -Filter 'stock_backup_*' -ErrorAction SilentlyContinue | Sort-Object Name)

if ($Backups.Count -eq 0) {
    Write-Host '[INFO] No D:\stock_backup_* folders were found.' -ForegroundColor Yellow
    exit 0
}

Write-Host 'Previous installer backup folders:' -ForegroundColor Cyan
foreach ($backup in $Backups) {
    Write-Host ("  {0}" -f $backup.FullName)
}

if (-not $ListOnly) {
    Write-Host ''
    Write-Host '[INFO] Recovery copying is disabled by the project no-extra-backup policy.' -ForegroundColor Yellow
    Write-Host 'Only the existing source folders were listed; no new copy was created.'
}
exit 0
