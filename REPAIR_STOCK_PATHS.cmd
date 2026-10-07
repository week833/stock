@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "ERRORLEVEL="
call "D:\stock\GitHub\scripts\compat\repair_legacy_paths.cmd" %*
exit /b %ERRORLEVEL%
