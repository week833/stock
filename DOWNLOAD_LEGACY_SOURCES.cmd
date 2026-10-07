@echo off
call "%~dp0scripts\sources\clone_legacy_compat_repos.cmd" %*
exit /b %ERRORLEVEL%
