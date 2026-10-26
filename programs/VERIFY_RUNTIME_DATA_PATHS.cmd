@echo off
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"

call "%ROOT%\programs\shared\find_python.cmd"
if not defined PY (
    echo [ERROR] No usable Python runtime was found.
    exit /b 1
)

"%PY%" "%~dp0VERIFY_RUNTIME_DATA_PATHS.py" %*
exit /b %ERRORLEVEL%
