@echo off
chcp 65001 >nul
setlocal EnableExtensions

set "SUITE=%~dp0"
for %%I in ("%SUITE%..\..") do set "ROOT=%%~fI"
call "%ROOT%\programs\shared\find_python.cmd" test
if not defined PY (
    echo [ERROR] Python with pytest was not found.
    set "RC=1"
    goto :End
)

"%PY%" -m pytest -p no:cacheprovider "%SUITE%tests" -q
set "RC=%ERRORLEVEL%"

:End
if not "%NO_PAUSE%"=="1" pause
exit /b %RC%
