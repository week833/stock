@echo off
chcp 65001 >nul
setlocal EnableExtensions

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0REMOVE_COMMAND_BOT_SERVICE.ps1"
set "RC=%ERRORLEVEL%"
if not "%NO_PAUSE%"=="1" pause
exit /b %RC%
