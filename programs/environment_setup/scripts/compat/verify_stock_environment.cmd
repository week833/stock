@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion

set "TOOLKIT=%~dp0..\.."
for %%I in ("%TOOLKIT%") do set "TOOLKIT=%%~fI"
set "STOCK_ROOT=D:\stock"
set "PYTHON=%STOCK_ROOT%\.venv\Scripts\python.exe"
set /a ERRORS=0

echo ============================================================
echo  D:\stock environment verification
echo ============================================================
echo Toolkit: %TOOLKIT%
echo Stock root: %STOCK_ROOT%
echo.

"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -Command "$p='D:\TWStockData\config\path_contract.json'; if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { exit 1 }; try { $c=Get-Content -LiteralPath $p -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json } catch { exit 1 }; $v=[string]$c.contract_version; if ($v -notmatch '\A2\.[0-9]+\.[0-9]+\z') { exit 1 }; if ($v -ne '2.0.0') { Write-Warning ('Path contract version ' + $v + ' is not the known version 2.0.0; accepted by major version 2.') };if ([string]::IsNullOrWhiteSpace([string]$c.root)) { exit 1 }; $r=[IO.Path]::GetFullPath([string]$c.root).TrimEnd('\'); if (-not $r.Equals('D:\TWStockData',[StringComparison]::OrdinalIgnoreCase)) { exit 1 }; 'PATH_CONTRACT_OK'"
if !ERRORLEVEL! NEQ 0 (
    echo [ERROR] Central path contract 2.x validation failed.
    set /a ERRORS+=1
)

call :check "%TOOLKIT%\requirements.txt"
call :check "%TOOLKIT%\scripts\setup\install_d_stock_env.ps1"
call :check "%TOOLKIT%\scripts\setup\configure_windows_environment.ps1"
call :check "D:\TWStockData\contracts\shared_market_data\requirements_v1.json"
call :check "%STOCK_ROOT%\programs\shared\stock_daily_contract.py"
call :check "D:\TWStockData\services\workflow_scheduler\RUN_WORKFLOW_SCHEDULER.cmd"
call :check "D:\MIX All\02_MIX_ALL正式報告.cmd"
call :check "%STOCK_ROOT%\programs\single_stock_analysis\src\tw_single_stock_c0.py"
call :check "%STOCK_ROOT%\programs\shared\stock_daily_linear.py"
call :check "%STOCK_ROOT%\programs\shared_data_management\VERIFY_SHARED_DATA_RECEIPT.cmd"
call :check "%STOCK_ROOT%\programs\telegram_report_delivery\RUN_COMMAND_BOT.cmd"
call :check "%PYTHON%"

if exist "%PYTHON%" (
    "%PYTHON%" -c "import sys; sys.path.insert(0,r'%STOCK_ROOT%\programs\shared'); import numpy,pandas,reportlab,stock_daily_contract,stock_daily_linear; print('CURRENT_C0_IMPORTS_OK')"
    if !ERRORLEVEL! NEQ 0 set /a ERRORS+=1
    "%PYTHON%" -m pip check
    if !ERRORLEVEL! NEQ 0 set /a ERRORS+=1
)

set "GIT_EXE="
for /f "delims=" %%G in ('where git.exe 2^>nul') do if not defined GIT_EXE set "GIT_EXE=%%G"
if not defined GIT_EXE if exist "%ProgramFiles%\Git\cmd\git.exe" set "GIT_EXE=%ProgramFiles%\Git\cmd\git.exe"
if not defined GIT_EXE if exist "%LOCALAPPDATA%\Programs\Git\cmd\git.exe" set "GIT_EXE=%LOCALAPPDATA%\Programs\Git\cmd\git.exe"
if defined GIT_EXE (
    echo [OK] Git: !GIT_EXE!
    "!GIT_EXE!" --version
) else (
    echo [ERROR] Git for Windows was not found.
    set /a ERRORS+=1
)

where gh.exe >nul 2>nul
if !ERRORLEVEL! EQU 0 (
    echo [OK] GitHub CLI is available.
) else (
    echo [INFO] GitHub CLI is not installed; prediction and local Git source use are unaffected.
)

for /f "usebackq delims=" %%V in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('STOCK_HOME','User')"`) do set "USER_STOCK_HOME=%%V"
if /I "!USER_STOCK_HOME!"=="%STOCK_ROOT%" (
    echo [OK] STOCK_HOME=%STOCK_ROOT%
) else (
    echo [WARN] STOCK_HOME is not configured for newly opened applications.
)

echo.
if !ERRORS! EQU 0 (
    echo Verification completed successfully.
) else (
    echo [ERROR] Verification found !ERRORS! required-file or import problems.
)
if not defined STOCK_TOOLKIT_NO_PAUSE pause
exit /b !ERRORS!

:check
if exist "%~1" (
    echo [OK] %~1
) else (
    echo [ERROR] Missing: %~1
    set /a ERRORS+=1
)
exit /b 0
