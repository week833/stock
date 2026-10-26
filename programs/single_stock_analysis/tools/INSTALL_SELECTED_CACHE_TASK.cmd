@echo off
chcp 65001 >nul
setlocal EnableExtensions DisableDelayedExpansion
set "PSModulePath=%SystemRoot%\System32\WindowsPowerShell\v1.0\Modules;%ProgramFiles%\WindowsPowerShell\Modules"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "D:\stock\programs\single_stock_analysis\tools\INSTALL_SELECTED_CACHE_TASK.ps1" %*
set "RC=%ERRORLEVEL%"
if not "%NO_PAUSE%"=="1" pause
exit /b %RC%
