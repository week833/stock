@echo off
setlocal EnableExtensions
chcp 65001 >nul

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0VERIFY_STABLE_PROGRAM_PATHS.ps1" -Root "%ROOT%"
exit /b %ERRORLEVEL%
