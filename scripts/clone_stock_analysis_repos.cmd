@echo off
call "%~dp0sources\clone_stock_analysis_repos.cmd" %*
exit /b %ERRORLEVEL%
