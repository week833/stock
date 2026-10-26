@echo off
chcp 65001 >nul
setlocal EnableExtensions

set "STOCK_HOME=D:\stock"
set "ACTIVATE=%STOCK_HOME%\.venv\Scripts\activate.bat"

if not exist "%ACTIVATE%" (
    echo [ERROR] 尚未建立 D:\stock 虛擬環境。
    echo 請先執行 programs\environment_setup\INSTALL_D_STOCK_ENV.cmd。
    pause
    exit /b 1
)

start "D:\stock Python Environment" cmd /k "cd /d D:\stock && call .venv\Scripts\activate.bat && echo STOCK_HOME=D:\stock"
exit /b 0
