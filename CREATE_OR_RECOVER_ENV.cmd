@echo off
setlocal EnableExtensions
set "ERRORLEVEL="

title Create or recover D:\stock\GitHub\.env

set "FIXED_ROOT=D:\stock\GitHub"
set "HELPER=%FIXED_ROOT%\scripts\setup\assert_fixed_install_root.ps1"
set "PS_SCRIPT=%FIXED_ROOT%\scripts\setup\create_or_recover_env.ps1"
set "POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "RC=1"

if not "%~1"=="" goto :bad_argument
if not "%~2"=="" goto :bad_argument
if not "%~3"=="" goto :bad_argument

call :guard_powershell
if errorlevel 1 (
    echo [ERROR] Trusted Windows PowerShell was not found or failed the physical-path safety guard.
    pause
    exit /b 2
)

if not exist "%HELPER%" (
    echo [ERROR] Fixed-root identity helper was not found:
    echo %HELPER%
    echo Complete the fixed-root installation first.
    exit /b 2
)

call :guard_fixed_path "%HELPER%" file
if errorlevel 1 (
    echo [ERROR] Fixed-root identity helper failed the physical-path safety guard.
    exit /b 2
)
"%POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%HELPER%"
if errorlevel 1 (
    echo [ERROR] Fixed-root identity verification failed; .env was not changed.
    exit /b 2
)

if not exist "%PS_SCRIPT%" (
    echo [ERROR] Fixed-root .env script was not found:
    echo %PS_SCRIPT%
    exit /b 2
)

call :guard_fixed_path "%PS_SCRIPT%" file
if errorlevel 1 (
    echo [ERROR] Fixed-root .env script failed the physical-path safety guard.
    exit /b 2
)
"%POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$tokens=$null; $errors=$null; [System.Management.Automation.Language.Parser]::ParseFile('%PS_SCRIPT%',[ref]$tokens,[ref]$errors) ^| Out-Null; if($errors.Count -gt 0){$errors ^| ForEach-Object { Write-Host $_.Message }; exit 1}; exit 0"
if errorlevel 1 goto :parse_error

"%POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%"
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo [OK] .env operation completed.
) else (
    echo [ERROR] .env operation failed with exit code %RC%.
)
echo.
pause
exit /b %RC%

:bad_argument
    echo [ERROR] This tool accepts no command-line arguments.
    exit /b 2

:parse_error
echo [ERROR] The PowerShell .env creator failed syntax validation.
pause
exit /b 1

:guard_fixed_path
set "STOCK_GUARD_ROOT=%FIXED_ROOT%"
set "STOCK_GUARD_TARGET=%~1"
set "STOCK_GUARD_KIND=%~2"
"%POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$root=$env:STOCK_GUARD_ROOT; $target=$env:STOCK_GUARD_TARGET; $kind=$env:STOCK_GUARD_KIND; try { $rootFull=[IO.Path]::GetFullPath([string]$root).TrimEnd([char]92); $rootItem=Get-Item -LiteralPath $root -Force -ErrorAction Stop; if(-not $rootItem.PSIsContainer -or (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)){exit 2}; $resolved=Resolve-Path -LiteralPath $target -ErrorAction Stop; $targetFull=[IO.Path]::GetFullPath([string]$resolved.ProviderPath).TrimEnd([char]92); if(-not($targetFull -ieq $rootFull -or $targetFull.StartsWith($rootFull+([char]92),[StringComparison]::OrdinalIgnoreCase))){exit 2}; $cursor=[IO.Path]::GetFullPath([string]$target); while($true){$item=Get-Item -LiteralPath ([string]$cursor) -Force -ErrorAction Stop; if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){exit 2}; $parent=[IO.Directory]::GetParent([string]$cursor); if($null -eq $parent){$pathRoot=[IO.Path]::GetPathRoot([string]$cursor); if($null -eq $pathRoot -or $cursor -ine $pathRoot){exit 2}; break}; $next=$parent.FullName; $nextRoot=[IO.Path]::GetPathRoot($next); if($nextRoot -and $next -ine $nextRoot){$next=$next.TrimEnd([char]92)}; if($next -ieq $cursor){exit 2}; $cursor=$next}; $leaf=Get-Item -LiteralPath $target -Force -ErrorAction Stop; if($kind -ieq 'file' -and (-not ($leaf -is [System.IO.FileInfo]) -or $leaf.PSIsContainer)){exit 2}; if($kind -ieq 'directory' -and -not $leaf.PSIsContainer){exit 2}; exit 0 } catch { exit 2 }"
set "RC=%ERRORLEVEL%"
exit /b %RC%

:guard_powershell
if not exist "%POWERSHELL%" exit /b 2
set "STOCK_POWERSHELL_PATH=%POWERSHELL%"
"%POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$path=$env:STOCK_POWERSHELL_PATH; try { $full=[IO.Path]::GetFullPath([string]$path); $leaf=Get-Item -LiteralPath $full -Force -ErrorAction Stop; if(-not ($leaf -is [System.IO.FileInfo]) -or $leaf.PSIsContainer -or (($leaf.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)){exit 2}; $cursor=$full; while($true){$item=Get-Item -LiteralPath $cursor -Force -ErrorAction Stop; if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){exit 2}; $parent=[IO.Directory]::GetParent($cursor); if($null -eq $parent -or $parent.FullName -ieq $cursor){break}; $cursor=$parent.FullName}; exit 0 } catch { exit 2 }"
exit /b %ERRORLEVEL%
