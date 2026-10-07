@echo off
setlocal
set "MODE_ARGS="
:options
if "%~1"=="" goto :run
if /i "%~1"=="--all" (set "MODE_ARGS=%MODE_ARGS% -All"&goto :next)
if /i "%~1"=="--legacy" (set "MODE_ARGS=%MODE_ARGS% -Legacy"&goto :next)
if /i "%~1"=="--check" (set "MODE_ARGS=%MODE_ARGS% -Check"&goto :next)
if /i "%~1"=="--dry-run" (set "MODE_ARGS=%MODE_ARGS% -DryRun"&goto :next)
if /i "%~1"=="--verify" (set "MODE_ARGS=%MODE_ARGS% -Verify"&goto :next)
echo [ERROR] Use --all, --legacy, --check, --dry-run, or --verify.
exit /b 2
:next
shift
goto :options
:run
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "D:\stock\GitHub\scripts\sources\source_manager.ps1" -Root "D:\stock\GitHub" %MODE_ARGS%
exit /b %ERRORLEVEL%
