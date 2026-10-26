@echo off
chcp 65001 >nul
setlocal EnableExtensions

set "TARGET=%~dp0scripts\compat\verify_stock_environment.cmd"
if not exist "%TARGET%" (
    echo [ERROR] 找不到環境檢查程式：%TARGET%
    pause
    exit /b 1
)

call "%TARGET%" %*
exit /b %ERRORLEVEL%
