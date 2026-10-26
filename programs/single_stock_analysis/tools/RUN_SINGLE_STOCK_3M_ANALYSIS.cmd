@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "TARGET=%~dp0..\RUN_SINGLE_STOCK_3M_ANALYSIS.cmd"
if not exist "%TARGET%" (
    echo [ERROR] C0 H63 close-to-close entrypoint was not found:
    echo %TARGET%
    exit /b 2
)
call "%TARGET%" %*
set "RC=%ERRORLEVEL%"
exit /b %RC%
