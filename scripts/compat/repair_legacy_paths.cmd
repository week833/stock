@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "ERRORLEVEL="
set "ENGINE=%~dp0repair_legacy_paths_guard.ps1"
if /I "%~1"=="--check" if "%~2"=="" goto :check
if /I "%~1"=="--apply" if /I "%~2"=="--confirm" if "%~3"=="" goto :apply
echo Usage: repair_legacy_paths.cmd --check or --apply --confirm
exit /b 2
:check
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ENGINE%" -Mode Check
exit /b %ERRORLEVEL%
:apply
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ENGINE%" -Mode Apply -ConfirmApply
exit /b %ERRORLEVEL%
