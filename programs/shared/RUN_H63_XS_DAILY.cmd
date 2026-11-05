@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
rem H63 U7 three-month relative-strength daily flow (stock_h63_xs, ADR-0009): settle the forward record ->
rem routine slope refit only when the active model is 63 sessions old -> today's full-market snapshot (the
rem single-stock H63 display AND the immutable forward-record block) -> review, all in ONE pinned run.
rem Run after the 23:00 central full update and before 09:00 Taipei (a later record is kept but not judged).
rem Output is appended to <root>\logs\h63_xs_daily_<YYYYMMDD>.log when that log is writable, else the console.
rem Exit code = the snapshot step: 0 recorded / already recorded, 2 usage, 3 data not ready, 4 gate / integrity /
rem model, 5 IO. A failed settle or refit is logged and never blocks the snapshot.
rem Usage: RUN_H63_XS_DAILY.cmd [--trigger manual]
for %%I in ("%~dp0..\..") do set "ROOT=%%~fI"
set "ENTRY=%ROOT%\programs\shared\stock_h63_xs.py"
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
rem Log stamp (local YYYYMMDD) and data root from the analysis Python (STOCK_H63_XS_ROOT honoured by the reader).
rem No %-sequences inside the python one-liner; PY has no spaces, so it is deliberately unquoted inside for /f.
set "STAMP="
set "XS_ROOT="
for /f "usebackq tokens=1,*" %%A in (`%PY% -c "import datetime,stock_h63_xs_reader as r;print(datetime.date.today().isoformat().replace('-',''),r.root_dir())"`) do (
    set "STAMP=%%A"
    set "XS_ROOT=%%B"
)
if not defined STAMP set "STAMP=unknown_date"
if not defined XS_ROOT (
    if defined STOCK_H63_XS_ROOT (set "XS_ROOT=%STOCK_H63_XS_ROOT%") else set "XS_ROOT=D:\TWStockData\program_data\stock_h63_xs"
)
set "LOG_DIR=%XS_ROOT%\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
set "LOG=%LOG_DIR%\h63_xs_daily_%STAMP%.log"
set "LOG_OK=1"
>>"%LOG%" echo [%DATE% %TIME%] == RUN_H63_XS_DAILY start (root %XS_ROOT%)|| set "LOG_OK=0"
if not exist "%LOG%" set "LOG_OK=0"
if "%LOG_OK%"=="1" (
    echo [INFO] log: %LOG%
    "%PY%" -B "%ENTRY%" daily %* >>"%LOG%" 2>&1
) else (
    echo [WARN] log %LOG% is not writable; console output only
    "%PY%" -B "%ENTRY%" daily %*
)
set "RC=%ERRORLEVEL%"
if "%LOG_OK%"=="1" >>"%LOG%" echo [%DATE% %TIME%] == end (exit %RC%)
if "%RC%"=="0" (echo [INFO] H63 U7 daily ok) else echo [ERROR] H63 U7 daily exit %RC%; see %LOG%
exit /b %RC%
