@echo off
chcp 65001 >nul
setlocal EnableExtensions

set "TARGET=%~dp0scripts\setup\configure_windows_environment.cmd"
if not exist "%TARGET%" (
    echo [ERROR] Missing environment configuration tool: %TARGET%
    set "RC=1"
    goto :End
)

set "LOCAL_NO_PAUSE="
if not defined STOCK_TOOLKIT_NO_PAUSE (
    set "STOCK_TOOLKIT_NO_PAUSE=1"
    set "LOCAL_NO_PAUSE=1"
)
call "%TARGET%" %*
set "RC=%ERRORLEVEL%"
if defined LOCAL_NO_PAUSE set "STOCK_TOOLKIT_NO_PAUSE="

:End
if not defined STOCK_TOOLKIT_NO_PAUSE pause
exit /b %RC%
