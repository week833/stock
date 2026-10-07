@echo off
chcp 65001 >nul
setlocal EnableExtensions DisableDelayedExpansion
set "ERRORLEVEL="
set "ROOT=D:\stock\GitHub"
set "NO_PAUSE=1"
set "STOCK_TOOLKIT_NO_PAUSE=1"
title Stock Toolkit
:menu
cls
echo ============================================================
echo Stock Toolkit - %ROOT%
echo ============================================================
echo [1] 安裝環境
echo [2] 下載台股研究來源
echo [3] 修復舊路徑
echo [4] 重設環境與 PATH
echo [5] 開啟虛擬環境終端機
echo [6] 基本檢查
echo [Q] 離開
choice /C 123456Q /N /M "Select: "
if errorlevel 7 exit /b 0
if errorlevel 6 goto :check
if errorlevel 5 goto :terminal
if errorlevel 4 goto :configure
if errorlevel 3 goto :repair
if errorlevel 2 goto :sources
set "ACTION=%~dp0INSTALL_D_STOCK_ENV.cmd"
set "ARGS="
goto :run
:sources
set "ACTION=%ROOT%\DOWNLOAD_STOCK_SOURCES.cmd"
set "ARGS="
goto :run
:repair
set "ACTION=%ROOT%\REPAIR_STOCK_PATHS.cmd"
set "ARGS=--apply --confirm"
goto :run
:configure
set "ACTION=%ROOT%\CONFIGURE_WINDOWS_ENV.cmd"
set "ARGS="
goto :run
:terminal
set "ACTION=%ROOT%\OPEN_STOCK_TERMINAL.cmd"
set "ARGS="
goto :run
:check
set "ACTION=%ROOT%\VERIFY_STOCK_ENV.cmd"
set "ARGS="
:run
if not exist "%ACTION%" (
    echo [ERROR] 找不到 %ACTION%
    pause
    goto :menu
)
call "%ACTION%" %ARGS%
echo Exit code: %ERRORLEVEL%
pause
goto :menu
