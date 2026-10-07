@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "ERRORLEVEL="
call "D:\stock\GitHub\CONFIGURE_WINDOWS_ENV.cmd" %*
exit /b %ERRORLEVEL%
