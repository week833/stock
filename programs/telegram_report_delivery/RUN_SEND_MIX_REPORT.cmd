@echo off
chcp 65001 >nul
setlocal EnableExtensions

rem MIX-only 08:00 entrypoint.  This file never calls the D-stock sender.
set "SUITE=%~dp0"
for %%I in ("%SUITE%..\..") do set "ROOT=%%~fI"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
call "%ROOT%\programs\shared\find_python.cmd"
if not defined PY (
    echo [ERROR] No usable Python executable was found.
    set "RC=1"
    goto :End
)

"%PY%" "%SUITE%mix_report_sender.py" %*
set "RC=%ERRORLEVEL%"

:End
if not "%NO_PAUSE%"=="1" pause
exit /b %RC%
