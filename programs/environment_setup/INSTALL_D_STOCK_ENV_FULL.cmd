@echo off
chcp 65001 >nul
setlocal EnableExtensions

set "SCRIPT=%~dp0scripts\setup\install_d_stock_env.ps1"
if not exist "%SCRIPT%" (
    echo [ERROR] Missing installer core: %SCRIPT%
    exit /b 1
)
if /I "%~1"=="/CHECK" (
    powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -SelfTest
) else (
    powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -Full
)
set "RC=%ERRORLEVEL%"
if /I not "%~1"=="/CHECK" if not defined STOCK_TOOLKIT_NO_PAUSE pause
exit /b %RC%
