@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion

set "PROGRAMS=%~dp0"
set "NO_PAUSE=1"
set "PYTHONDONTWRITEBYTECODE=1"

call "%PROGRAMS%VERIFY_STABLE_PROGRAM_PATHS.cmd"
if errorlevel 1 (
    set "RC=!ERRORLEVEL!"
    goto :End
)

call "%PROGRAMS%single_stock_analysis\tools\RUN_TESTS.cmd"
if errorlevel 1 (
    set "RC=!ERRORLEVEL!"
    goto :End
)

call "%PROGRAMS%telegram_report_delivery\RUN_TESTS.cmd"
if errorlevel 1 (
    set "RC=!ERRORLEVEL!"
    goto :End
)

set "STOCK_PYTHON=%PROGRAMS%..\.venv\Scripts\python.exe"
if not exist "%STOCK_PYTHON%" (
    echo [ERROR] D-Stock virtual-environment Python is missing: %STOCK_PYTHON%
    set "RC=2"
    goto :End
)

"%STOCK_PYTHON%" -m pytest -q -p no:cacheprovider "%PROGRAMS%shared_data_management\test_verify_shared_data_receipt.py"
if errorlevel 1 (
    set "RC=!ERRORLEVEL!"
    goto :End
)

"%STOCK_PYTHON%" -m pytest -q -p no:cacheprovider "%PROGRAMS%shared_data_management\test_verify_runtime_data_paths.py"
if errorlevel 1 (
    set "RC=!ERRORLEVEL!"
    goto :End
)

"%STOCK_PYTHON%" -m pytest -q -p no:cacheprovider "%PROGRAMS%shared\tests"
if errorlevel 1 (
    set "RC=!ERRORLEVEL!"
    goto :End
)

"%STOCK_PYTHON%" -m pytest -q -p no:cacheprovider "%PROGRAMS%environment_setup\tests"
if errorlevel 1 (
    set "RC=!ERRORLEVEL!"
    goto :End
)

"%STOCK_PYTHON%" -m pytest -q -p no:cacheprovider "%PROGRAMS%cross_system_validation\tests"
if errorlevel 1 (
    set "RC=!ERRORLEVEL!"
    goto :End
)

set "TOP10_TESTS=%PROGRAMS%..\TWNextDayTop10\tests"
if not exist "%TOP10_TESTS%" (
    echo [ERROR] TWNextDayTop10 tests are missing: %TOP10_TESTS%
    set "RC=2"
    goto :End
)
"%STOCK_PYTHON%" -m pytest -q -p no:cacheprovider "%TOP10_TESTS%"
if errorlevel 1 (
    set "RC=!ERRORLEVEL!"
    goto :End
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Import-Module Pester; $r = Invoke-Pester -Script '%PROGRAMS%telegram_report_delivery\tests\CHECK_COMMAND_BOT_STATUS.Tests.ps1' -PassThru; if ($r.TotalCount -lt 1 -or $r.FailedCount -gt 0) { exit 1 }; exit 0"
if errorlevel 1 (
    set "RC=!ERRORLEVEL!"
    goto :End
)

set "SCHEDULER_PYTHON=D:\TWStockData\services\market_update\.venv\Scripts\python.exe"
set "SCHEDULER_TEST=D:\TWStockData\services\workflow_scheduler\tests\test_workflow_scheduler_contract.py"
if not exist "%SCHEDULER_PYTHON%" (
    echo [ERROR] Scheduler test Python is missing: %SCHEDULER_PYTHON%
    set "RC=2"
    goto :End
)
if not exist "%SCHEDULER_TEST%" (
    echo [ERROR] Scheduler contract test is missing: %SCHEDULER_TEST%
    set "RC=2"
    goto :End
)

"%SCHEDULER_PYTHON%" -m pytest -q -p no:cacheprovider "%SCHEDULER_TEST%"
set "RC=!ERRORLEVEL!"

:End
set "NO_PAUSE="
if not defined RC set "RC=0"
if not "%STOCK_TOOLKIT_NO_PAUSE%"=="1" pause
exit /b %RC%
