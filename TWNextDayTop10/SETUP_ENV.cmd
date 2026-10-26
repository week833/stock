@echo off
powershell.exe -NoProfile -File "%~dp0setup_env.ps1"
exit /b %errorlevel%
