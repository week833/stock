@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "SUITE=%~dp0"
for %%I in ("%SUITE%..\..") do set "ROOT=%%~fI"
set "NODE="
if defined STOCK_ANALYSIS_NODE set "NODE=%STOCK_ANALYSIS_NODE%"
if not exist "%NODE%" set "NODE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
set "STOCK_CHECK_ROOT=%ROOT%"
call "%SUITE%find_python.cmd" analysis
echo [Python]
if defined PY (
    echo %PY%
    "%PY%" --version
    if errorlevel 1 set "RC=1"
    "%PY%" -c "import os,sys; from pathlib import Path; root=Path(os.environ['STOCK_CHECK_ROOT']); sys.path[:0]=[str(root/'programs'/'single_stock_analysis'/'src'),str(root/'programs'/'shared')]; import numpy,pandas,reportlab,stock_daily_contract,stock_daily_contract_adoption,stock_daily_application_scope,stock_daily_price_quality,stock_daily_calendar,stock_daily_calendar_base,stock_daily_linear,stock_daily_forward,stock_data_sources,stock_query_projection,date_format; print('Current C0 packages and stock modules: OK')"
    if errorlevel 1 set "RC=1"
) else (
    echo [ERROR] Current stock environment or C0 packages are missing. Run the environment toolkit repair.
    set "RC=1"
)
echo.
echo [Node]
if exist "%NODE%" (
    "%NODE%" --version
) else (
    node --version
    if errorlevel 1 set "RC=1"
)
echo.
echo [Project]
call "%ROOT%\programs\VERIFY_STABLE_PROGRAM_PATHS.cmd"
if errorlevel 1 set "RC=1"
if exist "D:\TWStockData\contracts\shared_market_data\requirements_v1.json" (
    echo Central registry: OK
) else (
    echo [ERROR] Central registry is missing.
    set "RC=1"
)
if exist "%ROOT%\programs\shared\stock_daily_contract.py" (
    echo Current C0 contract: OK
) else (
    echo [ERROR] Current C0 contract is missing.
    set "RC=1"
)
if exist "D:\TWStockData\services\workflow_scheduler\RUN_WORKFLOW_SCHEDULER.cmd" (
    echo Independent workflow scheduler: OK
) else (
    echo [ERROR] Independent workflow scheduler is missing.
    set "RC=1"
)
if exist "D:\MIX All\02_MIX_ALL正式報告.cmd" (
    echo MIX ALL formal entry: OK
) else (
    echo [ERROR] MIX ALL formal entry is missing.
    set "RC=1"
)
if exist "%ROOT%\programs\single_stock_analysis\src\tw_single_stock_c0.py" (
    echo Single-stock program: OK
) else (
    echo [ERROR] Single-stock program is missing.
    set "RC=1"
)
if exist "%ROOT%\programs\shared_data_management\VERIFY_SHARED_DATA_RECEIPT.cmd" (
    echo Shared data receipt verifier: OK
) else (
    echo [ERROR] Shared data receipt verifier is missing.
    set "RC=1"
)
if exist "%ROOT%\programs\telegram_report_delivery\RUN_COMMAND_BOT.cmd" (
    echo Telegram support entry: OK
) else (
    echo [ERROR] Telegram support entry is missing.
    set "RC=1"
)
if exist "%ROOT%\programs\environment_setup\STOCK_SETUP_MANAGER.cmd" (
    echo Environment toolkit: OK
) else (
    echo [ERROR] Environment toolkit is missing.
    set "RC=1"
)
if not defined RC set "RC=0"
if not "%NO_PAUSE%"=="1" if not "%STOCK_TOOLKIT_NO_PAUSE%"=="1" pause
exit /b %RC%
