@echo off
chcp 65001 >nul
setlocal EnableExtensions

set "SUITE=%~dp0"
set "NO_PAUSE=1"
set "MIX_ENTRYPOINT=%SUITE%RUN_SEND_MIX_REPORT.cmd"
if not exist "%MIX_ENTRYPOINT%" (
    echo [ERROR] MIX formal sender entrypoint is missing: %MIX_ENTRYPOINT%
    set "RC=1"
    goto :End
)
call "%MIX_ENTRYPOINT%" %*
set "RC=%ERRORLEVEL%"

:End
if not "%NO_PAUSE%"=="1" pause
exit /b %RC%
