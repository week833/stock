@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "HERE=%~dp0"
set "SCRIPT=%HERE%src\verify_stock_programs.py"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if not exist "%SCRIPT%" (
  echo [ERROR] Acceptance verifier not found: %SCRIPT%
  exit /b 2
)

set "MODE=--reports"
if /I "%~1"=="--structure-only" set "MODE="
if /I "%~1"=="--reports" set "MODE="
if /I "%~1"=="--central-mix-live" set "MODE="

where py >nul 2>nul
if not errorlevel 1 goto :run_py

where python >nul 2>nul
if not errorlevel 1 goto :run_python

echo [ERROR] Python 3 was not found. Install Python or add py/python to PATH.
set "RC=2"
goto :done

:run_py
py -3 "%SCRIPT%" %MODE% %*
set "RC=%ERRORLEVEL%"
goto :done

:run_python
python "%SCRIPT%" %MODE% %*
set "RC=%ERRORLEVEL%"
goto :done

:done
echo.
if "%RC%"=="0" (
  echo [OK] Acceptance checks passed.
) else (
  echo [ERROR] Acceptance checks finished with exit code %RC%.
)
if /I not "%STOCK_ACCEPTANCE_NO_PAUSE%"=="1" pause
exit /b %RC%
