[CmdletBinding()]
param([ValidateSet('Check', 'Apply')][string]$Mode = 'Check', [switch]$ConfirmApply)

# Dot-source exposes these functions only; it never runs a repair.
function Get-LegacyPathMap {
    param([string]$RepositoryRoot = 'D:\stock\GitHub', [string]$LegacyRoot = 'D:\Downloads\stock')
    $pairs = @(
        @('@legacy', ''),
        @('repos\tw_data', 'repos\taiwan_market_data'),
        @('repos\market_data', 'repos\global_market_data'),
        @('repos\ml', 'repos\machine_learning_forecasting'),
        @('repos\backtests', 'repos\backtesting_engines'),
        @('repos\quant_tools', 'repos\quant_portfolio_risk'),
        @('repos\ai_tools', 'repos\ai_agent_tools'),
        @('repos\legacy', 'repos\legacy_compat'),
        @('stock_related\01_taiwan_market_data', 'repos\taiwan_market_data'),
        @('stock_related\02_global_market_data', 'repos\global_market_data'),
        @('stock_related\03_machine_learning_forecasting', 'repos\machine_learning_forecasting'),
        @('stock_related\04_backtesting_engines', 'repos\backtesting_engines'),
        @('stock_related\05_quant_factor_portfolio_risk', 'repos\quant_portfolio_risk'),
        @('stock_related\06_stock_ai_tools\finmind-twstock-research-skill', 'repos\ai_agent_tools\finmind-twstock-research-skill'),
        @('stock_related\06_stock_ai_tools\stock-mitake-ui-reference', 'repos\ai_agent_tools\stock-mitake-ui-reference'),
        @('stock_related\07_market_data_engines\free-stockdb', 'free-stockdb'),
        @('stock_related\08_taiwan_trading_tools\shioaji-pro-app', 'shioaji-pro-app'),
        @('stock_related\09_strategy_research\Trading-Masters-Strategies', 'Trading-Masters-Strategies'),
        @('stock_related\10_legacy_compat', 'repos\legacy_compat'),
        @('repos\FinMind', 'repos\taiwan_market_data\FinMind'),
        @('repos\daily_stock_analysis', 'repos\taiwan_market_data\daily_stock_analysis'),
        @('repos\stock-strategies-only', 'repos\taiwan_market_data\stock-strategies-only'),
        @('repos\CasualMarket', 'repos\taiwan_market_data\CasualMarket'),
        @('repos\twstock', 'repos\taiwan_market_data\twstock'),
        @('repos\PyConTW2018Tutorial', 'repos\taiwan_market_data\PyConTW2018Tutorial'),
        @('repos\yfinance', 'repos\global_market_data\yfinance'),
        @('repos\OpenBB', 'repos\global_market_data\OpenBB'),
        @('repos\qlib', 'repos\machine_learning_forecasting\qlib'),
        @('repos\FinRL', 'repos\machine_learning_forecasting\FinRL'),
        @('repos\darts', 'repos\machine_learning_forecasting\darts'),
        @('repos\mlforecast', 'repos\machine_learning_forecasting\mlforecast'),
        @('repos\neuralforecast', 'repos\machine_learning_forecasting\neuralforecast'),
        @('repos\statsforecast', 'repos\machine_learning_forecasting\statsforecast'),
        @('repos\LightGBM', 'repos\machine_learning_forecasting\LightGBM'),
        @('repos\xgboost', 'repos\machine_learning_forecasting\xgboost'),
        @('repos\catboost', 'repos\machine_learning_forecasting\catboost'),
        @('repos\optuna', 'repos\machine_learning_forecasting\optuna'),
        @('repos\shap', 'repos\machine_learning_forecasting\shap'),
        @('repos\freqtrade', 'repos\machine_learning_forecasting\freqtrade'),
        @('repos\vectorbt', 'repos\backtesting_engines\vectorbt'),
        @('repos\backtesting.py', 'repos\backtesting_engines\backtesting-py'),
        @('repos\Lean', 'repos\backtesting_engines\Lean'),
        @('repos\bt', 'repos\backtesting_engines\bt'),
        @('repos\zipline-reloaded', 'repos\backtesting_engines\zipline-reloaded'),
        @('repos\backtrader', 'repos\backtesting_engines\backtrader'),
        @('repos\ta-lib-python', 'repos\quant_portfolio_risk\ta-lib-python'),
        @('repos\ta', 'repos\quant_portfolio_risk\ta'),
        @('repos\alphalens-reloaded', 'repos\quant_portfolio_risk\alphalens-reloaded'),
        @('repos\quantstats', 'repos\quant_portfolio_risk\quantstats'),
        @('repos\pyfolio-reloaded', 'repos\quant_portfolio_risk\pyfolio-reloaded'),
        @('repos\empyrical-reloaded', 'repos\quant_portfolio_risk\empyrical-reloaded'),
        @('repos\ffn', 'repos\quant_portfolio_risk\ffn'),
        @('repos\PyPortfolioOpt', 'repos\quant_portfolio_risk\PyPortfolioOpt'),
        @('repos\Riskfolio-Lib', 'repos\quant_portfolio_risk\Riskfolio-Lib'),
        @('repos\skfolio', 'repos\quant_portfolio_risk\skfolio'),
        @('andrej-karpathy-skills', 'repos\ai_agent_tools\andrej-karpathy-skills'),
        @('audio-tldr-skill', 'repos\ai_agent_tools\audio-tldr-skill'),
        @('claude-code', 'repos\ai_agent_tools\claude-code'),
        @('claude-code-i18n', 'repos\ai_agent_tools\claude-code-i18n'),
        @('claude-code-resources', 'repos\ai_agent_tools\claude-code-resources'),
        @('de-ai-tone', 'repos\ai_agent_tools\de-ai-tone'),
        @('harness-engineering', 'repos\ai_agent_tools\harness-engineering'),
        @('skills', 'repos\ai_agent_tools\skills'),
        @('stocktw', 'repos\taiwan_market_data\stocktw'),
        @('github_sources\twstock', 'repos\taiwan_market_data\twstock'),
        @('github_sources\tw_stocker', 'repos\legacy_compat\tw_stocker'),
        @('github_sources\python-stock-radar-', 'repos\legacy_compat\python-stock-radar-'),
        @('github_sources\TW-stock', 'repos\legacy_compat\TW-stock')
    )
    foreach ($pair in $pairs) {
        $link = if ($pair[0] -eq '@legacy') { $LegacyRoot } else { Join-Path $RepositoryRoot $pair[0] }
        $target = if ($pair[1]) { Join-Path $RepositoryRoot $pair[1] } else { $RepositoryRoot }
        [pscustomobject]@{ Link = $link; Target = $target; Optional = [bool]$pair[1] }
    }
}
function Get-LegacyPathState {
    param([string]$Path)
    try { $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop }
    catch {
        if ($_.FullyQualifiedErrorId -like '*PathNotFound*' -or $_.FullyQualifiedErrorId -like '*ItemNotFound*') {
            return [pscustomobject]@{ Kind = 'Missing'; Item = $null }
        }
        throw
    }
    $kind = if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        if ($item.LinkType -eq 'Junction') { 'Junction' } else { 'Reparse' }
    } elseif ($item.PSIsContainer) { 'Directory' } else { 'File' }
    return [pscustomobject]@{ Kind = $kind; Item = $item }
}
function Assert-LegacyPhysicalDirectory {
    param([string]$Path)
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
        if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Expected an ordinary directory: $cursor"
        }
        $parent = [IO.Directory]::GetParent($cursor)
        if ($null -eq $parent) { break }
        $cursor = $parent.FullName
    }
}
function Assert-LegacyParentPath {
    param([string]$Path)
    $cursor = [IO.Path]::GetFullPath($Path)
    while ((Get-LegacyPathState $cursor).Kind -eq 'Missing') {
        $parent = [IO.Directory]::GetParent($cursor)
        if ($null -eq $parent) { throw "No existing ancestor for parent: $Path" }
        $cursor = $parent.FullName
    }
    Assert-LegacyPhysicalDirectory $cursor
}
function Invoke-LegacyPathRepair {
    [CmdletBinding()]
    param(
        [ValidateSet('Check', 'Apply')][string]$Mode = 'Check', [switch]$ConfirmApply,
        [object[]]$Map = @(Get-LegacyPathMap),
        [string]$RepositoryRoot = 'D:\stock\GitHub', [string]$LegacyRoot = 'D:\Downloads\stock'
    )
    $records = [Collections.Generic.List[object]]::new()
    $pending = [Collections.Generic.List[object]]::new()
    $created = 0; $skipped = 0; $existing = 0; $missing = 0; $failed = 0
    try {
        if ($Mode -eq 'Apply' -and -not $ConfirmApply) { throw 'Apply requires -ConfirmApply.' }
        $repo = [IO.Path]::GetFullPath($RepositoryRoot).TrimEnd('\')
        $legacy = [IO.Path]::GetFullPath($LegacyRoot).TrimEnd('\')
        Assert-LegacyPhysicalDirectory $repo
        foreach ($entry in $Map) {
            $link = [IO.Path]::GetFullPath([string]$entry.Link).TrimEnd('\')
            $target = [IO.Path]::GetFullPath([string]$entry.Target).TrimEnd('\')
            if (($link -ine $legacy -and -not $link.StartsWith($repo + '\', [StringComparison]::OrdinalIgnoreCase)) -or
                ($target -ine $repo -and -not $target.StartsWith($repo + '\', [StringComparison]::OrdinalIgnoreCase)) -or
                $link -ieq $target -or $target.StartsWith($link + '\', [StringComparison]::OrdinalIgnoreCase)) {
                throw "Map path is outside scope or cyclic: $link -> $target"
            }
            $source = Get-LegacyPathState $target
            $state = Get-LegacyPathState $link
            if ($source.Kind -eq 'Missing' -and $entry.Optional -and $state.Kind -eq 'Missing') {
                Write-Host "[SKIP] Source is not downloaded: $target"
                $records.Add([pscustomobject]@{ Link = $link; Target = $target; Status = 'SkippedOptional' })
                $skipped++; continue
            }
            if ($source.Kind -ne 'Directory') { throw "Source target is missing or not an ordinary directory: $target" }
            Assert-LegacyPhysicalDirectory $target
            if ($state.Kind -eq 'Junction') {
                $targets = @($state.Item.Target)
                if ($targets.Count -ne 1) { throw "Junction target is ambiguous: $link" }
                $actual = [string]$targets[0]
                if ($actual.StartsWith('\??\') -or $actual.StartsWith('\\?\')) { $actual = $actual.Substring(4) }
                if (-not [IO.Path]::IsPathRooted($actual)) { $actual = Join-Path ([IO.Path]::GetDirectoryName($link)) $actual }
                if ([IO.Path]::GetFullPath($actual).TrimEnd('\') -ine $target) { throw "Existing junction points elsewhere: $link" }
                $existing++; $records.Add([pscustomobject]@{ Link = $link; Target = $target; Status = 'ExistingJunction' }); continue
            }
            if ($state.Kind -ne 'Missing') { throw "Existing path will not be overwritten: $link" }
            $missing++
            $record = [pscustomobject]@{ Link = $link; Target = $target; Status = 'MissingLink' }
            $records.Add($record); $pending.Add($record)
            Write-Host "[MISSING] $link -> $target"
            if ($Mode -eq 'Apply') { Assert-LegacyParentPath ([IO.Path]::GetDirectoryName($link)) }
        }
        # Finish every conflict check before the first write. Check never creates anything.
        if ($Mode -eq 'Apply') {
            foreach ($record in $pending) {
                if ((Get-LegacyPathState $record.Link).Kind -ne 'Missing') { throw "Link appeared; not overwritten: $($record.Link)" }
                $parent = [IO.Path]::GetDirectoryName($record.Link)
                Assert-LegacyParentPath $parent
                [IO.Directory]::CreateDirectory($parent) | Out-Null
                New-Item -ItemType Junction -Path $record.Link -Target $record.Target -ErrorAction Stop | Out-Null
                $record.Status = 'Created'; $created++
                Write-Host "[LINK] $($record.Link) -> $($record.Target)"
            }
        }
    }
    catch { $failed = 1; Write-Host "[ERROR] $($_.Exception.Message)" }
    return [pscustomobject]@{
        ExitCode = $failed; CreatedCount = $created; SkippedCount = $skipped
        ExistingCount = $existing; MissingCount = $missing; Items = @($records.ToArray())
    }
}
if ($MyInvocation.InvocationName -ne '.') {
    $result = Invoke-LegacyPathRepair -Mode $Mode -ConfirmApply:$ConfirmApply
    Write-Host "Repair: created $($result.CreatedCount), existing $($result.ExistingCount), optional skipped $($result.SkippedCount), exit $($result.ExitCode)."
    exit $result.ExitCode
}
