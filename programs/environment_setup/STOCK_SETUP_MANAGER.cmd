@echo off
chcp 65001 >nul
setlocal EnableExtensions

title D:\stock environment setup manager
set "TOOLKIT=%~dp0"

:Menu
cls
echo ============================================================
echo  D:\stock environment setup manager
echo ============================================================
echo [1] Install or repair Python packages
echo [4] Verify the current environment
echo [5] Full setup: environment and verification
echo [Q] Exit
echo.
choice /C 145Q /N /M "Select: "

if errorlevel 4 exit /b 0
if errorlevel 3 goto :Full
if errorlevel 2 goto :Verify
if errorlevel 1 goto :Install

:Install
call :Run "%TOOLKIT%INSTALL_D_STOCK_ENV.cmd"
goto :Menu

:Verify
call :Run "%TOOLKIT%VERIFY_STOCK_ENV.cmd"
goto :Menu

:Full
call :Run "%TOOLKIT%INSTALL_D_STOCK_ENV_FULL.cmd"
goto :Menu

:Run
set "STOCK_TOOLKIT_NO_PAUSE=1"
call "%~1"
set "RC=%ERRORLEVEL%"
set "STOCK_TOOLKIT_NO_PAUSE="
echo.
echo Exit code: %RC%
pause
exit /b %RC%
