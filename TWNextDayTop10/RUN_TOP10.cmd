@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "OMP_NUM_THREADS=2"
set "OPENBLAS_NUM_THREADS=2"
set "MKL_NUM_THREADS=2"
set "NOPAUSE="
set "OPENARG="
set "SOURCEARG="
:args
if "%~1"=="" goto ready
if /i "%~1"=="--no-pause" set "NOPAUSE=1"
if /i "%~1"=="--no-open" set "OPENARG=--no-open"
if /i "%~1"=="--model-source" (
  if "%~2"=="" goto missing_source
  set "SOURCEARG=--model-source %~2"
  shift
)
shift
goto args
:missing_source
echo --model-source requires auto, preopen or c0.
set "RESULT=2"
goto finish
:ready
if exist "%~dp0.venv\Scripts\python.exe" goto run
echo Project Python is missing. Run SETUP_ENV.cmd explicitly.
set "RESULT=2"
goto finish
:run
"%~dp0.venv\Scripts\python.exe" "%~dp0app.py" predict %OPENARG% %SOURCEARG%
set "RESULT=%ERRORLEVEL%"
goto finish
:finish
if not defined NOPAUSE pause
exit /b %RESULT%
