@echo off
chcp 65001 >nul
setlocal EnableExtensions

set "SCRIPT=%~dp0configure_windows_environment.ps1"
if not exist "%SCRIPT%" (
    echo [ERROR] Missing configuration script: %SCRIPT%
    exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
set "RC=%ERRORLEVEL%"
if not defined STOCK_TOOLKIT_NO_PAUSE pause
exit /b %RC%
