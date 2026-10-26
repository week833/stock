@echo off
rem Sets PY in the caller to the usable interpreter in this stock root.
rem Use "analysis" for current C0 packages; test modes also require pytest.
rem A dedicated analysis override is fail-closed and must remain in this env.

set "PY="
set "_PY_CHECK=import sys"
set "_PY_MODE=%~1"
set "_PY_REQUIRE_FIXED_ROOT=1"
set "_PY_GUARD_POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
rem Availability only; CHECK_ENV.cmd performs the full-import diagnostic.
if /I "%~1"=="analysis" set "_PY_CHECK=import importlib.util,sys;sys.exit(0 if all(importlib.util.find_spec(m) for m in ('numpy','pandas','reportlab')) else 1)"
if /I "%~1"=="test" set "_PY_CHECK=import pytest,sys"
if /I "%~1"=="analysis-test" set "_PY_CHECK=import pytest,importlib.util,sys;sys.exit(0 if all(importlib.util.find_spec(m) for m in ('numpy','pandas','reportlab')) else 1)"
for %%I in ("%~dp0..\..") do set "_STOCK_ROOT=%%~fI"

if /I "%~1"=="analysis" goto :ResolveAnalysis
if /I "%~1"=="analysis-test" goto :ResolveAnalysis
call :TryCandidate "%_STOCK_ROOT%\.venv\Scripts\python.exe"
goto :Cleanup

:ResolveAnalysis
if defined STOCK_ANALYSIS_PYTHON goto :ResolveAnalysisOverride
call :TryCandidate "%_STOCK_ROOT%\.venv\Scripts\python.exe"
goto :Cleanup

:ResolveAnalysisOverride
rem Read raw env before expanding it into a CMD command. Reject shell syntax,
rem relative paths and any path outside the current root's single env.
if not exist "%_PY_GUARD_POWERSHELL%" goto :Cleanup
"%_PY_GUARD_POWERSHELL%" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try {$p=[Environment]::GetEnvironmentVariable('STOCK_ANALYSIS_PYTHON','Process'); $r=[Environment]::GetEnvironmentVariable('_STOCK_ROOT','Process'); if([string]::IsNullOrWhiteSpace($p) -or [string]::IsNullOrWhiteSpace($r)){exit 2}; if($p.IndexOfAny([char[]]@([char]34,[char]37,[char]33,[char]38,[char]124,[char]60,[char]62,[char]94,[char]13,[char]10)) -ge 0){exit 2}; if($p -notmatch '^[A-Za-z]:\\' -or $p -match '(^|\\)\.{1,2}(\\|$)'){exit 2}; $full=[IO.Path]::GetFullPath($p); $expected=[IO.Path]::GetFullPath([IO.Path]::Combine($r,'.venv\Scripts\python.exe')); if($full -ine $expected){exit 2}; exit 0} catch {exit 2}"
if errorlevel 1 goto :Cleanup
call :TryCandidate "%STOCK_ANALYSIS_PYTHON%"
goto :Cleanup

:Cleanup
set "_PY_CHECK="
set "_STOCK_ROOT="
set "_PY_MODE="
set "_PY_REQUIRE_FIXED_ROOT="
set "_PY_GUARD_POWERSHELL="
if defined PY exit /b 0
exit /b 1

:TryCandidate
if defined PY exit /b 0
if "%~1"=="" exit /b 0
if not exist "%~1" exit /b 0
call :GuardCandidate "%~1"
if errorlevel 1 exit /b 0
"%~1" -c "%_PY_CHECK%" >nul 2>nul
if errorlevel 1 exit /b 0
rem Re-check before publishing PY so replacement or reparse drift fails closed.
call :GuardCandidate "%~1"
if errorlevel 1 exit /b 0
set "PY=%~1"
exit /b 0

:GuardCandidate
if "%~1"=="" exit /b 1
if not exist "%_PY_GUARD_POWERSHELL%" exit /b 1
set "_PY_GUARD_PATH=%~1"
set "_PY_GUARD_ROOTS="
if "%_PY_REQUIRE_FIXED_ROOT%"=="1" set "_PY_GUARD_ROOTS=%_STOCK_ROOT%\.venv\Scripts"
"%_PY_GUARD_POWERSHELL%" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $p=[Environment]::GetEnvironmentVariable('_PY_GUARD_PATH','Process'); $rootText=[Environment]::GetEnvironmentVariable('_PY_GUARD_ROOTS','Process'); if([string]::IsNullOrWhiteSpace($p)){exit 2}; $full=[IO.Path]::GetFullPath($p); $roots=@($rootText -split ';' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | ForEach-Object { [IO.Path]::GetFullPath($_).TrimEnd('\') }); if($roots.Count -gt 0 -and @($roots | Where-Object { $full -ieq $_ -or $full.StartsWith($_+'\',[StringComparison]::OrdinalIgnoreCase) }).Count -eq 0){exit 2}; $item=Get-Item -LiteralPath $full -Force; if(-not ($item -is [IO.FileInfo]) -or $item.PSIsContainer -or (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)){exit 2}; $cursor=$full; while($cursor){$node=Get-Item -LiteralPath $cursor -Force; if(($node.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){exit 2}; $parent=[IO.Directory]::GetParent($cursor); if($null -eq $parent -or $parent.FullName -ieq $cursor){break}; $cursor=$parent.FullName}; if($item.PSObject.Properties.Name -contains 'LinkType' -and $item.LinkType){exit 2}; $fsutil=Join-Path $env:SystemRoot 'System32\fsutil.exe'; $fsutilItem=Get-Item -LiteralPath $fsutil -Force; if(-not ($fsutilItem -is [IO.FileInfo]) -or $fsutilItem.PSIsContainer -or (($fsutilItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)){exit 2}; $fsutilCursor=$fsutilItem.FullName; while($fsutilCursor){$fsutilNode=Get-Item -LiteralPath $fsutilCursor -Force; if(($fsutilNode.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){exit 2}; $fsutilParent=[IO.Directory]::GetParent($fsutilCursor); if($null -eq $fsutilParent -or $fsutilParent.FullName -ieq $fsutilCursor){break}; $fsutilCursor=$fsutilParent.FullName}; $hard=@(& $fsutilItem.FullName hardlink list $full 2>$null); $hardRc=$LASTEXITCODE; $hardLines=@($hard | ForEach-Object { [string]$_ } | ForEach-Object { $_.Trim() } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }); if($hardRc -ne 0 -or $hardLines.Count -ne 1){exit 2}; $line=$hardLines[0]; $line=$line -replace '^(?:\\\?\?\\|\\\\\?\\)',''; if($line -match '(?i)(^|\\)\.{1,2}(\\|$)' -or $line.Contains('/') -or $line.Contains('\\') -or $line.EndsWith('\')){exit 2}; $listed=$null; if($line -match '^[A-Za-z]:\\'){$listed=$line} elseif($line -match '^\\[^\\]'){$volumeRoot=[IO.Path]::GetPathRoot($full); if([string]::IsNullOrWhiteSpace($volumeRoot)){exit 2}; $listed=$volumeRoot.TrimEnd('\')+$line} else {exit 2}; $listedFull=[IO.Path]::GetFullPath($listed); if([IO.Path]::GetPathRoot($listedFull) -ine [IO.Path]::GetPathRoot($full) -or $listedFull -ine $full){exit 2}; exit 0"
set "_PY_GUARD_RC=%ERRORLEVEL%"
set "_PY_GUARD_PATH="
set "_PY_GUARD_ROOTS="
if not "%_PY_GUARD_RC%"=="0" exit /b 1
exit /b 0
