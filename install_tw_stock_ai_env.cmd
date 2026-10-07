@echo off
setlocal EnableExtensions
set "ERRORLEVEL="
if not exist "D:\stock\GitHub\INSTALL_D_STOCK_ENV.cmd" (
    echo [ERROR] Project installer is missing: D:\stock\GitHub\INSTALL_D_STOCK_ENV.cmd
    exit /b 1
)
call "D:\stock\GitHub\INSTALL_D_STOCK_ENV.cmd" %*
exit /b %ERRORLEVEL%
