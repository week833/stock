@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
rem Pre-open H1 daily flow (stock_daily_preopen, ADR-0008): settle yesterday, monthly refit check (the calibrator is
rem refitted inside train every 21 sessions, the cadence validated by the research engine), write today's snapshot.
rem Every step's stdout/stderr is appended to <root>\logs\preopen_daily_<YYYYMMDD>.log when that log is writable;
rem otherwise the steps still run and print to the console.
rem Prerequisite: central data already holds last night's US close and day t chips. Finish before 08:30 Taipei.
rem Usage: RUN_PREOPEN_DAILY.cmd [--allow-missing-us] [--require-fresh-chips] [--target-date YYYY-MM-DD]
rem                              [--closed-day YYYY-MM-DD ...] [--allow-stale-signal (no-op)]
rem v3 (user ruling 2026-11-01: stale data must never block the run or the report): the verified-calendar gate only
rem ANNOTATES. With usable inputs and writable output, predict publishes and updates latest.json; stale central
rem prices, an unexplained weekday between signal and target, a weekend/holiday/closed target or an unusable calendar
rem are recorded in the manifest and the reader's Traditional Chinese notes. settle uses the verified calendar then;
rem unresolved rows stay pending and verified void / multi-session rows are not H1-scored. A non-zero predict exit means a real
rem failure (no active model, central data unreadable, ...) or a manual --strict-calendar QA run (never pass it here).
rem Every step runs even when an earlier step failed (settle / train failures are logged and the flow
rem continues to predict); the final exit code is predict's.
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
rem Log stamp (local YYYYMMDD) and data root from the analysis Python: %DATE% is locale dependent and the root default
rem lives in stock_daily_preopen_reader.root_dir (STOCK_DAILY_PREOPEN_ROOT honoured there). No %-sequences inside the
rem python one-liner: a child cmd expands %X% even inside quotes. PY has no spaces (fixed root), so it is deliberately
rem unquoted: a quoted command inside for /f loses its outer quotes.
set "STAMP="
set "PREOPEN_ROOT="
for /f "usebackq tokens=1,*" %%A in (`%PY% -c "import datetime,stock_daily_preopen_reader as r;print(datetime.date.today().isoformat().replace('-',''),r.root_dir())"`) do (
    set "STAMP=%%A"
    set "PREOPEN_ROOT=%%B"
)
if not defined STAMP set "STAMP=unknown_date"
if not defined PREOPEN_ROOT (
    if defined STOCK_DAILY_PREOPEN_ROOT (set "PREOPEN_ROOT=%STOCK_DAILY_PREOPEN_ROOT%") else set "PREOPEN_ROOT=D:\TWStockData\program_data\stock_daily_preopen"
)
if defined STOCK_DAILY_PREOPEN_LOG_DIR (set "LOG_DIR=%STOCK_DAILY_PREOPEN_LOG_DIR%") else set "LOG_DIR=%PREOPEN_ROOT%\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
set "LOG=%LOG_DIR%\preopen_daily_%STAMP%.log"
set "LOG_OK=1"
>>"%LOG%" echo [%DATE% %TIME%] == RUN_PREOPEN_DAILY start (root %PREOPEN_ROOT%)|| set "LOG_OK=0"
if not exist "%LOG%" set "LOG_OK=0"
if "%LOG_OK%"=="1" (echo [INFO] log: %LOG%) else echo [WARN] log %LOG% is not writable; steps run with console output only
rem Each :Step returns its exit code but never stops the flow: a failing settle / train still reaches predict.
call :Step settle settle
call :Step train train --authorization "monthly refit per ADR-0008 (skips unless 21 sessions old)"
call :Step predict predict %*
set "RC=%ERRORLEVEL%"
if "%LOG_OK%"=="1" >>"%LOG%" echo [%DATE% %TIME%] == end (predict exit %RC%)
exit /b %RC%

:Step
rem %1 = step name, %2.. = stock_daily_preopen.py arguments. Output goes to the log (or the console when LOG_OK=0);
rem the console gets one line per step. Redirections are written at the start of the echo lines so a trailing exit
rem code is never read as a stream number.
set "STEP=%~1"
shift
set "ARGS="
:StepArgs
if "%~1"=="" goto :StepRun
set "ARGS=%ARGS% %1"
shift
goto :StepArgs
:StepRun
if "%LOG_OK%"=="1" (
    >>"%LOG%" echo [%DATE% %TIME%] == %STEP%%ARGS%
    "%PY%" -B "%ENTRY%"%ARGS% >>"%LOG%" 2>&1
) else (
    "%PY%" -B "%ENTRY%"%ARGS%
)
set "STEP_RC=%ERRORLEVEL%"
if "%LOG_OK%"=="1" >>"%LOG%" echo [%DATE% %TIME%] == %STEP% exit %STEP_RC%
if "%STEP%"=="settle" if not "%STEP_RC%"=="0" echo [WARN] settle failed (exit %STEP_RC%); continuing with today's snapshot. See %LOG%
if "%STEP%"=="train" if not "%STEP_RC%"=="0" echo [ERROR] monthly refit failed (exit %STEP_RC%); keeping the active model. See %LOG%
if "%STEP%"=="predict" if not "%STEP_RC%"=="0" echo [ERROR] predict failed (exit %STEP_RC%); check latest.json and the log for the publication result. See %LOG%
if "%STEP_RC%"=="0" echo [INFO] %STEP% ok
exit /b %STEP_RC%
