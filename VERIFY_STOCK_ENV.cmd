@echo off
setlocal EnableExtensions
set "ERRORLEVEL="
if not "%~1"=="" if /I not "%~1"=="/CHECK" if /I not "%~1"=="/PREFLIGHT" if /I not "%~1"=="/DRY-RUN" (
    echo [ERROR] Unknown option. Allowed: /CHECK, /PREFLIGHT or /DRY-RUN
    exit /b 2
)
set "TARGET=D:\stock\GitHub\scripts\compat\verify_stock_environment.cmd"
if not exist "%TARGET%" (
    echo [ERROR] Environment verification script was not found: %TARGET%
    exit /b 2
)
call "%TARGET%"
exit /b %ERRORLEVEL%
