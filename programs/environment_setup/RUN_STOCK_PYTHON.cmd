@echo off
chcp 65001 >nul
setlocal EnableExtensions

set "STOCK_HOME=D:\stock"
set "PYTHON_EXE=%STOCK_HOME%\.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo [ERROR] 找不到：%PYTHON_EXE%
    echo 請先執行 programs\environment_setup\INSTALL_D_STOCK_ENV.cmd。
    pause
    exit /b 1
)

cd /d "%STOCK_HOME%"
"%PYTHON_EXE%" %*
exit /b %ERRORLEVEL%
