@echo off
call "%~dp0scripts\sources\clone_stock_analysis_repos.cmd" %*
exit /b %ERRORLEVEL%
