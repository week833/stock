[CmdletBinding()]
param([switch]$Full, [switch]$Preflight, [switch]$DryRun, [switch]$ConfigureOnly)

function Assert-StockPath {
    param([string]$Path, [switch]$Directory)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        ($Directory -and -not $item.PSIsContainer) -or
        (-not $Directory -and $item.PSIsContainer)) {
        throw "Managed path is not an ordinary $(if($Directory){'directory'}else{'file'}): $Path"
    }
}

function Get-StockProjectState {
    param([Parameter(Mandatory = $true)][string]$Root)
    Assert-StockPath -Path (Split-Path -Parent $Root) -Directory
    Assert-StockPath -Path $Root -Directory
    if (-not (Test-Path -LiteralPath $Root)) { return 'missing' }
    if (@(Get-ChildItem -LiteralPath $Root -Force -ErrorAction Stop).Count -eq 0) { return 'empty' }
    foreach ($relative in @('STOCK_SETUP_MANAGER.cmd', 'scripts\setup\install_d_stock_env.ps1')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Root $relative) -PathType Leaf)) {
            throw "Nonempty target is not this project; nothing was overwritten: $Root"
        }
    }
    return 'project'
}

function Invoke-StockProbe {
    param([string]$Executable, [string[]]$Arguments)
    try {
        $output = @(& $Executable @Arguments 2>$null | ForEach-Object { [string]$_ })
        return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = $output }
    }
    catch { return [pscustomobject]@{ ExitCode = 1; Output = @() } }
}

function Invoke-StockCommand {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Executable" }
}

function Test-StockPython {
    param([string]$Path)
    if (-not $Path) { return $false }
    $probe = Invoke-StockProbe -Executable $Path -Arguments @('-I', '-S', '-B', '-c', 'import sys; print("%d.%d.%d" % sys.version_info[:3])')
    if ($probe.ExitCode -ne 0 -or $probe.Output.Count -ne 1) { return $false }
    try { $version = [version]$probe.Output[0]; return ($version.Major -eq 3 -and $version -ge [version]'3.10') }
    catch { return $false }
}

function Find-StockPython {
    foreach ($name in @('python.exe', 'python3.exe')) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command -and (Test-StockPython -Path $command.Source)) { return $command.Source }
    }
    $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($launcher) {
        $probe = Invoke-StockProbe -Executable $launcher.Source -Arguments @('-3', '-I', '-S', '-B', '-c', 'import sys; print(sys.executable)')
        if ($probe.ExitCode -eq 0 -and $probe.Output.Count -eq 1 -and (Test-StockPython -Path $probe.Output[0])) { return $probe.Output[0] }
    }
    $localRoot = Join-Path $env:LOCALAPPDATA 'Programs\Python'
    foreach ($directory in @(Get-ChildItem -LiteralPath $localRoot -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue | Sort-Object Name -Descending)) {
        $path = Join-Path $directory.FullName 'python.exe'
        if (Test-StockPython -Path $path) { return $path }
    }
    return $null
}

function Install-StockTool {
    param([string]$PackageId)
    $winget = Get-Command winget.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $winget) { throw "Missing required tool; winget is unavailable. Install $PackageId and retry." }
    Invoke-StockCommand -Executable $winget.Source -Arguments @('install', '--id', $PackageId, '--exact', '--accept-package-agreements', '--accept-source-agreements')
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + $env:Path
}

function Find-StockGit {
    $command = Get-Command git.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return $command.Source }
    foreach ($path in @((Join-Path $env:ProgramFiles 'Git\cmd\git.exe'), (Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd\git.exe'))) {
        if (Test-Path -LiteralPath $path -PathType Leaf) { return $path }
    }
    return $null
}

function Test-StockVenv {
    param([Parameter(Mandatory = $true)][string]$Root)
    $venv = Join-Path $Root '.venv'
    $python = Join-Path $venv 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { return $false }
    $probe = Invoke-StockProbe -Executable $python -Arguments @('-I', '-B', '-c', 'import sys; print("%d.%d.%d" % sys.version_info[:3]); print(sys.prefix); print(sys.base_prefix)')
    if ($probe.ExitCode -ne 0 -or $probe.Output.Count -ne 3) { return $false }
    try {
        $version = [version]$probe.Output[0]
        $prefix = [IO.Path]::GetFullPath($probe.Output[1]).TrimEnd('\')
        $base = [IO.Path]::GetFullPath($probe.Output[2]).TrimEnd('\')
        return ($version.Major -eq 3 -and $version -ge [version]'3.10' -and
            $prefix -ieq [IO.Path]::GetFullPath($venv).TrimEnd('\') -and $prefix -ine $base)
    }
    catch { return $false }
}

function Get-StockInterpreter {
    param([Parameter(Mandatory = $true)][string]$Root, [switch]$Plan)
    $venv = Join-Path $Root '.venv'
    $scripts = Join-Path $venv 'Scripts'
    $python = Join-Path $scripts 'python.exe'
    Assert-StockPath -Path $venv -Directory
    Assert-StockPath -Path $scripts -Directory
    Assert-StockPath -Path $python
    if (Test-Path -LiteralPath $venv) {
        if (-not (Test-StockVenv -Root $Root)) { throw "Existing venv is incomplete or unusable; it was not replaced: $venv" }
        return [pscustomobject]@{ Path = $python; UsesVenv = $true; NeedsPythonInstall = $false }
    }
    $hostPython = Find-StockPython
    if (-not $hostPython -and -not $Plan) {
        Install-StockTool -PackageId 'Python.Python.3.12'
        $hostPython = Find-StockPython
        if (-not $hostPython) { throw 'Python 3.10 or newer within Python 3 was not found after installation.' }
    }
    return [pscustomobject]@{ Path = $hostPython; UsesVenv = $false; NeedsPythonInstall = (-not $hostPython) }
}

function Merge-StockPath {
    param([AllowNull()][string]$CurrentPath, [string[]]$Entries)
    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    $parts = New-Object 'System.Collections.Generic.List[string]'
    foreach ($entry in @($CurrentPath -split ';') + $Entries) {
        if ([string]::IsNullOrWhiteSpace($entry)) { continue }
        if ($seen.Add($entry.Trim().TrimEnd('\'))) { $parts.Add($entry) }
    }
    return ($parts -join ';')
}

function Set-StockUserEnvironment {
    param([string]$Root)
    $venv = Join-Path $Root '.venv'
    $values = [ordered]@{
        STOCK_HOME = $Root; STOCK_REPO = $Root; STOCK_SHARED_ROOT = (Split-Path -Parent $Root)
        STOCK_VENV = $venv; STOCK_PYTHON = (Join-Path $venv 'Scripts\python.exe'); STOCK_EXTERNAL_REPOS = (Join-Path $Root 'repos')
    }
    foreach ($name in $values.Keys) { [Environment]::SetEnvironmentVariable($name, $values[$name], 'User') }
    $path = Merge-StockPath -CurrentPath ([Environment]::GetEnvironmentVariable('Path', 'User')) -Entries @($Root, (Join-Path $venv 'Scripts'), (Join-Path $Root 'scripts'), (Join-Path $Root 'scripts\setup'), (Join-Path $Root 'scripts\sources'), (Join-Path $Root 'scripts\compat'))
    [Environment]::SetEnvironmentVariable('Path', $path, 'User')
}

function Invoke-StockInstall {
    param([string]$Root = 'D:\stock\GitHub', [string]$PackageRoot, [switch]$ConfigureOnly, [switch]$Plan)
    $state = Get-StockProjectState -Root $Root
    if ($ConfigureOnly -and $state -ne 'project') { throw 'ConfigureOnly requires the existing project and its venv.' }
    if ($state -ne 'project') {
        if ([IO.Path]::GetFullPath($PackageRoot).TrimEnd('\') -ieq [IO.Path]::GetFullPath($Root).TrimEnd('\')) { throw 'Bootstrap requires the installer from an external checkout.' }
        if ($Plan) {
            Write-Host "[PLAN] Clone https://github.com/week833/stock.git into empty target $Root; Git is needed only for this download."
            Write-Host '[PLAN] Reuse or create venv, install requirements, then configure User environment and PATH.'
            return
        }
        $git = Find-StockGit
        if (-not $git) { Install-StockTool -PackageId 'Git.Git'; $git = Find-StockGit }
        if (-not $git) { throw 'Git was not found after installation.' }
        Invoke-StockCommand -Executable $git -Arguments @('clone', 'https://github.com/week833/stock.git', $Root)
        if ((Get-StockProjectState -Root $Root) -ne 'project') { throw 'The downloaded project is incomplete.' }
    }
    if ($ConfigureOnly -and -not (Test-Path -LiteralPath (Join-Path $Root '.venv') -PathType Container)) { throw 'ConfigureOnly requires a healthy existing venv; run installation first.' }
    $requirements = Join-Path $Root 'requirements.txt'
    if (-not $ConfigureOnly) {
        Assert-StockPath -Path $requirements
        if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) { throw "Required file is missing: $requirements" }
    }
    $interpreter = Get-StockInterpreter -Root $Root -Plan:$Plan
    if ($Plan) {
        Write-Host "[PLAN] Target: $Root; reuse venv: $($interpreter.UsesVenv); install Python: $($interpreter.NeedsPythonInstall)."
        if (-not $ConfigureOnly) { Write-Host '[PLAN] Create venv if missing, then pip install requirements.txt.' }
        Write-Host '[PLAN] Configure User environment and append/deduplicate User PATH. No changes made.'
        return
    }
    $python = Join-Path $Root '.venv\Scripts\python.exe'
    if (-not $interpreter.UsesVenv) {
        Invoke-StockCommand -Executable $interpreter.Path -Arguments @('-m', 'venv', (Join-Path $Root '.venv'))
        if (-not (Test-StockVenv -Root $Root)) { throw 'The new venv is unusable.' }
    }
    if (-not $ConfigureOnly) { Invoke-StockCommand -Executable $python -Arguments @('-m', 'pip', 'install', '-r', $requirements) }
    Set-StockUserEnvironment -Root $Root
    Write-Host "[OK] Environment ready: $python"
}

if ($MyInvocation.InvocationName -ne '.') {
    $ErrorActionPreference = 'Stop'
    try {
        $package = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
        Invoke-StockInstall -PackageRoot $package -ConfigureOnly:$ConfigureOnly -Plan:($Preflight -or $DryRun)
        exit 0
    }
    catch { Write-Host ('[ERROR] ' + $_.Exception.Message); exit 1 }
}
