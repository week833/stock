@echo off
setlocal EnableExtensions
set "ERRORLEVEL="
set "PLAN=0"
if not "%~3"=="" goto :bad_argument
for %%A in ("%~1" "%~2") do if not "%%~A"=="" if /I not "%%~A"=="/FULL" if /I not "%%~A"=="/PREFLIGHT" if /I not "%%~A"=="/DRY-RUN" if /I not "%%~A"=="/CHECK" goto :bad_argument
for %%A in ("%~1" "%~2") do (
    if /I "%%~A"=="/PREFLIGHT" set "PLAN=1"
    if /I "%%~A"=="/DRY-RUN" set "PLAN=1"
    if /I "%%~A"=="/CHECK" set "PLAN=1"
)
if "%PLAN%"=="1" goto :plan
call "%~dp0INSTALL_D_STOCK_ENV.cmd"
if errorlevel 1 exit /b %ERRORLEVEL%
if not exist "D:\stock\GitHub\DOWNLOAD_STOCK_SOURCES.cmd" (
    echo [ERROR] Default core source downloader is missing.
    exit /b 1
)
call "D:\stock\GitHub\DOWNLOAD_STOCK_SOURCES.cmd"
exit /b %ERRORLEVEL%
:plan
call "%~dp0INSTALL_D_STOCK_ENV.cmd" /PREFLIGHT
if errorlevel 1 exit /b %ERRORLEVEL%
echo [PLAN] Download default core sources after installation; no legacy/all download or extra verification.
exit /b 0
:bad_argument
echo [ERROR] Allowed options: /FULL, /PREFLIGHT, /DRY-RUN, /CHECK
exit /b 2
