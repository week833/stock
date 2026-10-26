[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('full', 'global')][string]$Mode,
    [int]$MaxAttempts = 3,
    [int]$RetryDelaySeconds = 1800,
    [switch]$DryRun
)
# Central market-data update wrapper for the pre-open daily schedule (ADR-0008).
#   full   : enabled-route incremental update (prices, chips, global, revenue, ...). Retries when the
#            exit code is not 0 (success) or 10 (published with a partial route).
#   global : single-route update of global_market_history (last night's US close). No retry.
# Both run D:\TWStockUpdater with its own venv and TWSTOCK_UPDATER_ROOT=D:\TWStockData\updater_runtime,
# exactly the commands documented in PREOPEN_DAILY_SCHEDULE_PLAN_20261018.md. ASCII only on purpose.

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$updaterRoot = 'D:\TWStockUpdater'
$python = Join-Path $updaterRoot 'runtime\.venv\Scripts\python.exe'
$runtimeRoot = 'D:\TWStockData\updater_runtime'
$logDir = 'D:\TWStockData\program_data\stock_daily_preopen\logs'
$okExitCodes = @(0, 10)

foreach ($required in @($updaterRoot, $python, $runtimeRoot)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Required path is missing: $required" }
}
if ($Mode -eq 'global') { $MaxAttempts = 1 }

$arguments = @('-B', '-m', 'twstock_updater', 'update', '--mode', 'incremental')
if ($Mode -eq 'global') { $arguments += @('--routes', 'global_market_history') }
$arguments += '--headless'

$env:PYTHONPATH = Join-Path $updaterRoot 'src'
$env:TWSTOCK_UPDATER_ROOT = $runtimeRoot
Remove-Item Env:\TWSTOCKUPDATER_ROOT -ErrorAction SilentlyContinue   # user env var conflicts with the one above
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$logPath = Join-Path $logDir ("central_{0}_{1}.log" -f $Mode, $stamp)

function Write-Log {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
    Write-Host $line
}

$collectionDoc = Get-Content -LiteralPath (Join-Path $updaterRoot "config\route_registry.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$activeRows = @($collectionDoc.shared_raw_requirements | Where-Object { $_.enabled -eq $true })
$disabledRows = @($collectionDoc.shared_raw_requirements | Where-Object { $_.enabled -eq $false })
$collectionMessage = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("5pS26ZuG5pS/562W77ya5a+m6Zqb5oqT5Y+WezB96aCF44CB5YGc5pS2ezF96aCF77yb5a+m6Zqb5oqT5Y+W6Lev55Sx77yaezJ9"))
Write-Log ($collectionMessage -f $activeRows.Count, $disabledRows.Count, (($activeRows | ForEach-Object { $_.route_id }) -join ","))
Write-Log ("mode={0} cwd={1} python={2} args={3}" -f $Mode, $updaterRoot, $python, ($arguments -join ' '))
if ($DryRun) {
    Write-Log 'dry-run: no update executed'
    exit 0
}

Set-Location -LiteralPath $updaterRoot
$exitCode = 1
try {
    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        Write-Log ("attempt {0}/{1} start" -f $attempt, $MaxAttempts)
        $nativeFailure = $null
        $previousErrorPreference = $ErrorActionPreference
        try {
            # Windows PowerShell 5 turns redirected native stderr into errors.
            # Preserve the stream and the process exit; log I/O still fails fast.
            $ErrorActionPreference = 'Continue'
            $global:LASTEXITCODE = $null
            & $python @arguments 2>&1 | ForEach-Object {
                $line = [string]$_
                Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8 -ErrorAction Stop
            }
            $exitCode = if ($null -eq $global:LASTEXITCODE) { 1 } else { [int]$global:LASTEXITCODE }
        }
        catch {
            $nativeFailure = $_.Exception.Message
            $exitCode = 1
        }
        finally {
            $ErrorActionPreference = $previousErrorPreference
        }
        Write-Log ("attempt {0}/{1} exit={2}" -f $attempt, $MaxAttempts, $exitCode)
        if ($null -ne $nativeFailure) {
            Write-Log ("FAILED: native invocation or log write failed: {0}" -f $nativeFailure)
            break
        }
        if ($exitCode -eq 10) {
            Write-Log 'PARTIAL: usable data published or retained; exit=10; not a complete 25-route update'
        }
        if ($okExitCodes -contains $exitCode) { break }
        if ($exitCode -eq 28) {
            Write-Log 'WAITING_CAPACITY: stopping automatic retries; non-success exit preserved'
            break
        }
        if ($attempt -lt $MaxAttempts) {
            Write-Log ("waiting {0}s before retry" -f $RetryDelaySeconds)
            Start-Sleep -Seconds $RetryDelaySeconds
        }
    }
}
finally {
    Write-Log ("final exit={0}" -f $exitCode)
}
exit $exitCode
