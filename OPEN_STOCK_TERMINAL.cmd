@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "ERRORLEVEL="
set "STOCK_HOME=D:\stock\GitHub"
set "STOCK_PYTHON=%STOCK_HOME%\.venv\Scripts\python.exe"
set "ACTIVATE=%STOCK_HOME%\.venv\Scripts\activate.bat"
if not exist "%ACTIVATE%" goto :missing
if not exist "%STOCK_PYTHON%" goto :missing
cd /d "%STOCK_HOME%" || exit /b 1
call "%ACTIVATE%"
if errorlevel 1 exit /b %ERRORLEVEL%
start "Stock Toolkit" "%SystemRoot%\System32\cmd.exe" /k
exit /b %ERRORLEVEL%
:missing
echo [ERROR] Managed venv is missing. Run INSTALL_D_STOCK_ENV.cmd first.
exit /b 1
