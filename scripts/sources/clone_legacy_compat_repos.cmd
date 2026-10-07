@echo off
call "%~dp0clone_stock_analysis_repos.cmd" --legacy %*
exit /b %ERRORLEVEL%
