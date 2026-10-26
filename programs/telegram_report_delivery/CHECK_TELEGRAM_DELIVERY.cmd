@echo off
chcp 65001 >nul
setlocal EnableExtensions

set "NO_PAUSE=1"
call "%~dp0RUN_SEND_LATEST_REPORT.cmd" --check %*
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
