@echo off
chcp 65001 >nul
setlocal EnableExtensions

set "SUITE=%~dp0"
for %%I in ("%SUITE%..\..") do set "ROOT=%%~fI"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "NO_PAUSE=1"
set "PY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [ERROR] Canonical stock analysis Python was not found: %PY% 1>&2
    exit /b 1
)

"%PY%" "%SUITE%telegram_stock_command_bot.py" %*
exit /b %ERRORLEVEL%
