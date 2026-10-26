[CmdletBinding()]
param(
    [switch]$Full,
    [switch]$SelfTest
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$ToolkitRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$StockRoot = [IO.Path]::GetFullPath((Join-Path $ToolkitRoot '..\..'))
$ExpectedRoot = [IO.Path]::GetFullPath('D:\stock')
$PathContractPath = 'D:\TWStockData\config\path_contract.json'
$ExpectedDataRoot = [IO.Path]::GetFullPath('D:\TWStockData')
$ExpectedContractVersion = '2.0.0'
$PathContract = $null
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
$DStockDailyRoot = [string]$PathContract.managed_paths.dstock_daily
if ([string]::IsNullOrWhiteSpace($DStockDailyRoot)) {
    throw "Path contract is missing managed_paths.dstock_daily: $PathContractPath"
}
$DStockDailyRoot = [IO.Path]::GetFullPath($DStockDailyRoot).TrimEnd('\')
if (-not $DStockDailyRoot.Equals($ExpectedDataRoot, [StringComparison]::OrdinalIgnoreCase) -and
    -not $DStockDailyRoot.StartsWith($ExpectedDataRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw "managed_paths.dstock_daily escaped the contract root: $DStockDailyRoot"
}
$VenvDir = Join-Path $StockRoot '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$Requirements = Join-Path $ToolkitRoot 'requirements.txt'
$ConfigureScript = Join-Path $PSScriptRoot 'configure_windows_environment.ps1'
$LogDir = Join-Path $DStockDailyRoot 'tmp'
$LogFile = Join-Path $LogDir 'environment_setup.log'

function Write-Status {
    param([string]$Message)
    Write-Host $Message
    if (Test-Path -LiteralPath $LogDir) {
        $Message | Add-Content -LiteralPath $LogFile -Encoding UTF8
    }
}

function Invoke-Logged {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )

    Write-Status ("> {0} {1}" -f $FilePath, ($Arguments -join ' '))
    & $FilePath @Arguments 2>&1 | Tee-Object -FilePath $LogFile -Append
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $FilePath"
    }
}

function Get-BasePython {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($selector in @('-3.12', '-3.11', '-3.10', '-3.13')) {
            $candidate = & $launcher.Source $selector -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $candidate) {
                return [pscustomobject]@{
                    FilePath = $launcher.Source
                    PrefixArguments = @($selector)
                }
            }
        }
    }

    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        return [pscustomobject]@{
            FilePath = $python.Source
            PrefixArguments = @()
        }
    }

    throw 'Python 3.10 or newer was not found. Install Python, then run this installer again.'
}

function Assert-ToolkitLayout {
    param([switch]$RequireCanonicalRoot)

    if ($RequireCanonicalRoot -and $StockRoot -ine $ExpectedRoot) {
        throw "This toolkit must remain under D:\stock\programs\environment_setup. Current root: $StockRoot"
    }

    $required = @(
        $Requirements,
        $ConfigureScript,
        (Join-Path $StockRoot 'programs\shared\stock_daily_contract.py'),
        (Join-Path $ExpectedDataRoot 'contracts\shared_market_data\requirements_v1.json'),
        'D:\TWStockData\services\workflow_scheduler\RUN_WORKFLOW_SCHEDULER.cmd',
        (Join-Path $StockRoot 'programs\single_stock_analysis\src\tw_single_stock_c0.py'),
        (Join-Path $StockRoot 'programs\shared\stock_daily_linear.py')
    )
    foreach ($path in $required) {
        if (-not (Test-Path -LiteralPath $path)) {
            throw "Required file is missing: $path"
        }
    }
}

if ($SelfTest) {
    Assert-ToolkitLayout
    Write-Host 'INSTALLER_SELF_TEST_OK'
    exit 0
}
Assert-ToolkitLayout -RequireCanonicalRoot

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
"Environment setup started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Set-Content -LiteralPath $LogFile -Encoding UTF8

try {
    Write-Status '============================================================'
    Write-Status ' D:\stock analysis environment setup'
    Write-Status '============================================================'
    Write-Status "Toolkit: $ToolkitRoot"
    Write-Status "Stock root: $StockRoot"
    Write-Status 'The installer will not clone, replace, move, or update the analysis source tree.'

    if (-not (Test-Path -LiteralPath $VenvPython)) {
        if (Test-Path -LiteralPath $VenvDir) {
            throw "An incomplete virtual environment exists at $VenvDir. Rename it, then run this installer again."
        }
        $basePython = Get-BasePython
        $venvArguments = @($basePython.PrefixArguments) + @('-m', 'venv', $VenvDir)
        Invoke-Logged -FilePath $basePython.FilePath -Arguments $venvArguments
    }

    Invoke-Logged -FilePath $VenvPython -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools', 'wheel')
    Invoke-Logged -FilePath $VenvPython -Arguments @('-m', 'pip', 'install', '-r', $Requirements)
    Invoke-Logged -FilePath $VenvPython -Arguments @('-m', 'pip', 'check')

    & $ConfigureScript
    if ($LASTEXITCODE -ne 0) {
        throw 'Windows environment configuration failed.'
    }


    Write-Status ''
    Write-Status 'Environment setup completed.'
    Write-Status "Python: $VenvPython"
    Write-Status "Log: $LogFile"
    exit 0
}
catch {
    Remove-Item Env:STOCK_TOOLKIT_NO_PAUSE -ErrorAction SilentlyContinue
    Write-Status "[ERROR] $($_.Exception.Message)"
    Write-Status "Log: $LogFile"
    exit 1
}
