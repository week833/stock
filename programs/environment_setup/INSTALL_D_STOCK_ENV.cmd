@echo off
chcp 65001 >nul
setlocal EnableExtensions

set "SCRIPT=%~dp0scripts\setup\install_d_stock_env.ps1"
set "PS_ARGS="
if /I "%~1"=="/CHECK" set "PS_ARGS=-SelfTest"
if /I "%~1"=="/FULL" set "PS_ARGS=-Full"

if not exist "%SCRIPT%" (
    echo [ERROR] Missing installer core: %SCRIPT%
    set "RC=1"
    goto :End
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %PS_ARGS%
set "RC=%ERRORLEVEL%"

:End
if /I not "%~1"=="/CHECK" if not defined STOCK_TOOLKIT_NO_PAUSE pause
exit /b %RC%
