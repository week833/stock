@echo off
setlocal EnableExtensions
set "ERRORLEVEL="
set "PLAN=0"
set "FULL=0"
if not "%~3"=="" goto :bad_argument
for %%A in ("%~1" "%~2") do if not "%%~A"=="" if /I not "%%~A"=="/FULL" if /I not "%%~A"=="/PREFLIGHT" if /I not "%%~A"=="/DRY-RUN" if /I not "%%~A"=="/CHECK" goto :bad_argument
for %%A in ("%~1" "%~2") do (
    if /I "%%~A"=="/FULL" set "FULL=1"
    if /I "%%~A"=="/PREFLIGHT" set "PLAN=1"
    if /I "%%~A"=="/DRY-RUN" set "PLAN=1"
    if /I "%%~A"=="/CHECK" set "PLAN=1"
)
if "%FULL%"=="1" goto :full
set "CORE=%~dp0scripts\setup\install_d_stock_env.ps1"
if not exist "%CORE%" exit /b 1
set "ARGS="
if "%PLAN%"=="1" set "ARGS=-Preflight"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%CORE%" %ARGS%
exit /b %ERRORLEVEL%
:full
if "%PLAN%"=="1" (
    call "%~dp0INSTALL_D_STOCK_ENV_FULL.cmd" /PREFLIGHT
) else (
    call "%~dp0INSTALL_D_STOCK_ENV_FULL.cmd"
)
exit /b %ERRORLEVEL%
:bad_argument
echo [ERROR] Allowed options: /FULL, /PREFLIGHT, /DRY-RUN, /CHECK
exit /b 2
