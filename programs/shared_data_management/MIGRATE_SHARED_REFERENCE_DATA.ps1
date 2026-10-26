[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Error "status=fail_closed"
Write-Error "error=shared_data_migration_retired"
Write-Error "canonical=D:\TWStockData\services\market_update\market_update.cmd"
Write-Error "instruction=use_canonical_central_ensure_or_verify"
exit 78
