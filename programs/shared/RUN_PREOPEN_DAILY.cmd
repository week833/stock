@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
rem Pre-open H1 daily flow (stock_daily_preopen, ADR-0008): settle yesterday, monthly refit check, write today's snapshot.
rem Prerequisite: central data already holds last night's US close and day t chips. Finish before 08:30 Taipei.
rem Usage: RUN_PREOPEN_DAILY.cmd [--allow-missing-us] [--require-fresh-chips] [--target-date YYYY-MM-DD]
for %%I in ("%~dp0..\..") do set "ROOT=%%~fI"
set "ENTRY=%ROOT%\programs\shared\stock_daily_preopen.py"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONPATH=%ROOT%\programs\shared"
call "%ROOT%\programs\shared\find_python.cmd" analysis
if not defined PY (
    echo [ERROR] analysis Python not found: D:\stock\.venv
    exit /b 2
)
if not exist "%ENTRY%" (
    echo [ERROR] missing %ENTRY%
    exit /b 2
)
"%PY%" -B "%ENTRY%" settle
if errorlevel 1 echo [WARN] settle failed; continuing with today's snapshot.
"%PY%" -B "%ENTRY%" train --authorization "monthly refit per ADR-0008 (skips unless 21 sessions old)"
if errorlevel 1 echo [ERROR] monthly refit failed; keeping the active model.
"%PY%" -B "%ENTRY%" predict %*
exit /b %ERRORLEVEL%
