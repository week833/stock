@echo off
chcp 65001 >nul
setlocal EnableExtensions

set "TOOL_DIR=%~dp0"
for %%I in ("%TOOL_DIR%..") do set "SUITE=%%~fI"
for %%I in ("%SUITE%\..\..") do set "ROOT=%%~fI"
set "PYTHONPATH=%SUITE%\src;%ROOT%\programs\shared;%PYTHONPATH%"
call "%ROOT%\programs\shared\find_python.cmd" analysis-test
if not defined PY (
    echo [ERROR] Python with analysis and pytest dependencies was not found.
    set "RC=1"
    goto :End
)

"%PY%" -m pytest -p no:cacheprovider "%SUITE%\tests" -q
set "RC=%ERRORLEVEL%"

:End
if not "%NO_PAUSE%"=="1" pause
exit /b %RC%
