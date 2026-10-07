@echo off
setlocal EnableExtensions
set "ERRORLEVEL="
set "POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
call :guard_powershell
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo [ERROR] Trusted Windows PowerShell is missing or unsafe.
    exit /b %RC%
)

title Recover programs moved by a previous installer

set "FIXED_ROOT=D:\stock\GitHub"
set "RECOVERY_PS1=%FIXED_ROOT%\scripts\compat\recover_previous_stock_backups.ps1"

if not exist "%RECOVERY_PS1%" (
    echo [ERROR] Recovery script was not found:
    echo %RECOVERY_PS1%
    pause
    exit /b 1
)
call :guard_fixed_path "%RECOVERY_PS1%" file
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" exit /b %RC%

echo ============================================================
echo  Recover programs moved by a previous installer
echo ============================================================
echo.
echo This tool only lists legacy D:\stock_backup_* folders.
echo Creating a second recovery copy is disabled by project policy.
echo.

"%POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%RECOVERY_PS1%" -ListOnly
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
    echo [OK] Legacy backup listing completed; no files were copied.
) else (
    echo [ERROR] Backup listing failed with exit code %RC%.
)
pause
exit /b %RC%

:guard_powershell
if not exist "%POWERSHELL%" exit /b 2
set "STOCK_POWERSHELL_PATH=%POWERSHELL%"
"%POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$path=$env:STOCK_POWERSHELL_PATH; try { $full=[IO.Path]::GetFullPath([string]$path); $leaf=Get-Item -LiteralPath $full -Force -ErrorAction Stop; if(-not ($leaf -is [System.IO.FileInfo]) -or $leaf.PSIsContainer -or (($leaf.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)){exit 2}; $cursor=$full; while($true){$item=Get-Item -LiteralPath $cursor -Force -ErrorAction Stop; if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){exit 2}; $parent=[IO.Directory]::GetParent($cursor); if($null -eq $parent -or $parent.FullName -ieq $cursor){break}; $cursor=$parent.FullName}; exit 0 } catch { exit 2 }"
set "RC=%ERRORLEVEL%"
exit /b %RC%

:guard_fixed_path
set "STOCK_GUARD_ROOT=%FIXED_ROOT%"
set "STOCK_GUARD_TARGET=%~1"
set "STOCK_GUARD_KIND=%~2"
"%POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$root=$env:STOCK_GUARD_ROOT; $target=$env:STOCK_GUARD_TARGET; $kind=$env:STOCK_GUARD_KIND; try { $rootFull=[IO.Path]::GetFullPath([string]$root).TrimEnd([char]92); $rootItem=Get-Item -LiteralPath $root -Force -ErrorAction Stop; if(-not $rootItem.PSIsContainer -or (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)){exit 2}; $resolved=Resolve-Path -LiteralPath $target -ErrorAction Stop; $targetFull=[IO.Path]::GetFullPath([string]$resolved.ProviderPath).TrimEnd([char]92); if(-not($targetFull -ieq $rootFull -or $targetFull.StartsWith($rootFull+([char]92),[StringComparison]::OrdinalIgnoreCase))){exit 2}; $cursor=[IO.Path]::GetFullPath([string]$target); while($true){$item=Get-Item -LiteralPath ([string]$cursor) -Force -ErrorAction Stop; if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){exit 2}; $parent=[IO.Directory]::GetParent([string]$cursor); if($null -eq $parent){$pathRoot=[IO.Path]::GetPathRoot([string]$cursor); if($null -eq $pathRoot -or $cursor -ine $pathRoot){exit 2}; break}; $next=$parent.FullName; $nextRoot=[IO.Path]::GetPathRoot($next); if($nextRoot -and $next -ine $nextRoot){$next=$next.TrimEnd([char]92)}; if($next -ieq $cursor){exit 2}; $cursor=$next}; $leaf=Get-Item -LiteralPath $target -Force -ErrorAction Stop; if($kind -ieq 'file' -and (-not ($leaf -is [System.IO.FileInfo]) -or $leaf.PSIsContainer)){exit 2}; if($kind -ieq 'directory' -and -not $leaf.PSIsContainer){exit 2}; exit 0 } catch { exit 2 }"
set "RC=%ERRORLEVEL%"
exit /b %RC%
