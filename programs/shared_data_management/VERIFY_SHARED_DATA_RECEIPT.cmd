@echo off
setlocal
set "PROJECT_ROOT=%~dp0..\.."
set "RUNTIME_PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"
if not exist "%RUNTIME_PYTHON%" (
    echo [shared-data] missing fixed runtime: "%RUNTIME_PYTHON%" 1>&2
    exit /b 21
)
"%RUNTIME_PYTHON%" "%~dp0VERIFY_SHARED_DATA_RECEIPT.py" %*
exit /b %ERRORLEVEL%
