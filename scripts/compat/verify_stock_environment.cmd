@echo off
setlocal EnableExtensions
set "ERRORLEVEL="
set "FIXED_ROOT=D:\stock\GitHub"
set "HELPER=%FIXED_ROOT%\scripts\setup\assert_fixed_install_root.ps1"
set "PYTHON=%FIXED_ROOT%\.venv\Scripts\python.exe"
if not exist "%HELPER%" (
    echo [FAIL] Project root checker is missing: %HELPER%
    exit /b 2
)
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%HELPER%"
if errorlevel 1 exit /b 2
if not exist "%PYTHON%" (
    echo [FAIL] Virtual environment Python is missing: %PYTHON%
    exit /b 1
)
set "PYTHONDONTWRITEBYTECODE=1"
"%PYTHON%" -c "import sys; print('Python ' + sys.version.split()[0]); print('Executable: ' + sys.executable)"
if errorlevel 1 (
    echo [FAIL] Virtual environment Python could not start.
    exit /b 1
)
echo [OK] Stock toolkit environment is ready.
exit /b 0
