@echo off
chcp 65001 >nul
setlocal EnableExtensions

rem Read-only scheduler readiness output; this does not register or alter a task.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0SHOW_MIX_WEEKDAY_0800_TASK.ps1" -VerifyOnly %*
set "RC=%ERRORLEVEL%"
if not "%NO_PAUSE%"=="1" pause
exit /b %RC%
