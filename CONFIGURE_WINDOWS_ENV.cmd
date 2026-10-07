@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "ERRORLEVEL="
set "CORE=D:\stock\GitHub\scripts\setup\install_d_stock_env.ps1"
set "ARGS=-ConfigureOnly"
if "%~1"=="" goto :run
if not "%~2"=="" goto :bad_argument
if /I "%~1"=="/PREFLIGHT" goto :check
if /I "%~1"=="/DRY-RUN" goto :check
if /I "%~1"=="/CHECK" goto :check
goto :bad_argument
:check
set "ARGS=-ConfigureOnly -Preflight -DryRun"
:run
if not exist "%CORE%" (
    echo [ERROR] Installer core is missing: %CORE%
    exit /b 1
)
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%CORE%" %ARGS%
exit /b %ERRORLEVEL%
:bad_argument
echo [ERROR] Allowed: /PREFLIGHT, /DRY-RUN, /CHECK, or no option.
exit /b 2
