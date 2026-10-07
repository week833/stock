@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "ERRORLEVEL="
set "STOCK_HOME=D:\stock\GitHub"
set "STOCK_PYTHON=%STOCK_HOME%\.venv\Scripts\python.exe"
if "%~1"=="" (
    echo Usage: %~nx0 /VERSION or script.py [arguments]
    exit /b 2
)
if not exist "%STOCK_PYTHON%" (
    echo [ERROR] Managed venv is missing. Run INSTALL_D_STOCK_ENV.cmd first.
    exit /b 1
)
cd /d "%STOCK_HOME%" || exit /b 1
if /I "%~1"=="/VERSION" goto :version
"%STOCK_PYTHON%" %*
exit /b %ERRORLEVEL%
:version
if not "%~2"=="" (
    echo [ERROR] /VERSION does not accept additional arguments.
    exit /b 2
)
"%STOCK_PYTHON%" --version
exit /b %ERRORLEVEL%
