@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "ROOT=D:\stock\programs\telegram_report_delivery"
set "RC=20"
if not exist "%ROOT%\RUN_SEND_MIX_REPORT.cmd" (
    echo ERROR: canonical MIX Telegram sender is missing: %ROOT%\RUN_SEND_MIX_REPORT.cmd 1>&2
    goto :finish
)
pushd "%ROOT%" >nul 2>&1
if errorlevel 1 (
    echo ERROR: cannot enter canonical MIX sender cwd: %ROOT% 1>&2
    goto :finish
)
set "PUSHED=1"
call "RUN_SEND_MIX_REPORT.cmd" %*
set "RC=%ERRORLEVEL%"
:finish
if defined PUSHED popd
if /i not "%NO_PAUSE%"=="1" pause
exit /b %RC%
