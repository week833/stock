[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$ToolkitRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$StockRoot = [IO.Path]::GetFullPath((Join-Path $ToolkitRoot '..\..'))
$VenvPython = Join-Path $StockRoot '.venv\Scripts\python.exe'
$GitHubResearchRoot = Join-Path $StockRoot 'GitHub'
$PathContractPath = 'D:\TWStockData\config\path_contract.json'
$ExpectedDataRoot = [IO.Path]::GetFullPath('D:\TWStockData')
$ExpectedContractVersion = '2.0.0'
if (-not (Test-Path -LiteralPath $PathContractPath -PathType Leaf)) {
    throw "Path contract is missing: $PathContractPath"
}
try {
    $PathContract = Get-Content -LiteralPath $PathContractPath -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json
} catch {
    throw "Path contract is not valid JSON: $PathContractPath"
}
if ($null -eq $PathContract -or $null -eq $PathContract.PSObject.Properties['managed_paths']) {
    throw "Path contract has no managed_paths: $PathContractPath"
}
# Same rule as shared\path_contract.py for this toolkit: any X.Y.Z with major 2 is accepted
# (a version other than the known one only warns); any other major or format is rejected.
$ContractVersion = [string]$PathContract.contract_version
if ($ContractVersion -notmatch '\A2\.[0-9]+\.[0-9]+\z') {
    throw "Path contract version must be 2.x (known: ${ExpectedContractVersion}): $PathContractPath ($ContractVersion)"
}
if ($ContractVersion -ne $ExpectedContractVersion) {
    Write-Warning "Path contract version $ContractVersion is not the known version ${ExpectedContractVersion}; accepted by major version 2: $PathContractPath"
}
if ([string]::IsNullOrWhiteSpace([string]$PathContract.root)) {
    throw "Path contract root is missing: $PathContractPath"
}
$ContractRoot = [IO.Path]::GetFullPath([string]$PathContract.root).TrimEnd('\')
if (-not $ContractRoot.Equals($ExpectedDataRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Path contract root must be ${ExpectedDataRoot}: $ContractRoot"
}

if ($StockRoot -ine [IO.Path]::GetFullPath('D:\stock')) {
    throw "Unexpected stock root: $StockRoot"
}
if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Virtual environment Python was not found: $VenvPython"
}

$variables = @{
    STOCK_PROGRAMS = (Join-Path $StockRoot 'programs')
    PYTHONUTF8 = '1'
    PYTHONIOENCODING = 'utf-8'
}
foreach ($name in $variables.Keys) {
    [Environment]::SetEnvironmentVariable($name, $variables[$name], 'User')
}

foreach ($name in @('STOCK_GITHUB_RESEARCH_ROOT', 'STOCK_EXTERNAL_REPOS')) {
    [Environment]::SetEnvironmentVariable($name, $null, 'User')
}
$obsolete = @(
    (Join-Path $StockRoot 'scripts\sources'),
    (Join-Path $GitHubResearchRoot 'scripts\sources'),
    (Join-Path $ToolkitRoot 'scripts\sources')
)
$additions = @(
    (Join-Path $StockRoot '.venv\Scripts'),
    (Join-Path $StockRoot 'programs\single_stock_analysis'),
    (Join-Path $StockRoot 'programs\shared'),
    $ToolkitRoot,
    (Join-Path $ToolkitRoot 'scripts\setup'),
    (Join-Path $ToolkitRoot 'scripts\compat')
)
$gitCandidates = @(
    (Join-Path $env:ProgramFiles 'Git\cmd\git.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd\git.exe')
)
foreach ($candidate in $gitCandidates) {
    if (Test-Path -LiteralPath $candidate) {
        $additions += Split-Path -Parent $candidate
        break
    }
}

$current = [Environment]::GetEnvironmentVariable('Path', 'User')
$paths = New-Object 'System.Collections.Generic.List[string]'
if ($current) {
    foreach ($entry in $current.Split(';')) {
        $trimmed = $entry.Trim()
        if (-not $trimmed) { continue }
        $isObsolete = $false
        foreach ($old in $obsolete) {
            if ($trimmed.TrimEnd('\') -ieq $old.TrimEnd('\')) {
                $isObsolete = $true
                break
            }
        }
        if (-not $isObsolete) { $paths.Add($trimmed) }
    }
}
foreach ($entry in $additions) {
    $exists = $false
    foreach ($currentEntry in $paths) {
        if ($currentEntry.TrimEnd('\') -ieq $entry.TrimEnd('\')) {
            $exists = $true
            break
        }
    }
    if (-not $exists) { $paths.Add($entry) }
}
[Environment]::SetEnvironmentVariable('Path', ($paths -join ';'), 'User')

& $VenvPython -c "import numpy, pandas, reportlab; print('ANALYSIS_ENV_OK')"
if ($LASTEXITCODE -ne 0) {
    throw 'Required analysis packages could not be imported.'
}

Write-Host "Current runtime selectors are preserved; toolkit Python=$VenvPython"
Write-Host 'Restart open terminals and applications to load the updated user environment.'
