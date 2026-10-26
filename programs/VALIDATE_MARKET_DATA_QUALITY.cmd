@echo off
chcp 65001 >nul
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"

call "%ROOT%\programs\shared\find_python.cmd"
if not defined PY (
    echo [ERROR] No usable Python runtime was found.
    exit /b 1
)

"%PY%" "%~dp0VALIDATE_MARKET_DATA_QUALITY.py" %*
exit /b %ERRORLEVEL%
