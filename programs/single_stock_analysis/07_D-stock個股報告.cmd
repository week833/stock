@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "ROOT=D:\stock\programs\single_stock_analysis"
set "RC=20"
set "TARGET="
for %%F in ("%ROOT%\1_*.cmd") do if exist "%%~fF" (
    if defined TARGET (
        echo [錯誤] 找到多個 D-stock 個股分析正式入口（1_*.cmd）。 1>&2
        set "RC=21"
        goto :finish
    )
    set "TARGET=%%~fF"
)
if not defined TARGET (
    echo [錯誤] 在 %ROOT% 下找不到 D-stock 個股分析正式入口（1_*.cmd）。 1>&2
    goto :finish
)
pushd "%ROOT%" >nul 2>&1
if errorlevel 1 (
    echo [錯誤] 無法進入 D-stock 個股分析的固定工作目錄：%ROOT% 1>&2
    goto :finish
)
set "PUSHED=1"
call "%TARGET%" %*
set "RC=%ERRORLEVEL%"
:finish
if defined PUSHED popd
if /i not "%NO_PAUSE%"=="1" pause
exit /b %RC%
