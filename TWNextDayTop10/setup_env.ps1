$ErrorActionPreference = 'Stop'
$root = 'D:\stock\TWNextDayTop10'
$baseHome = 'C:\Users\USER\AppData\Local\Programs\Python\Python312'
$basePython = Join-Path $baseHome 'python.exe'
$venv = Join-Path $root '.venv'
$python = Join-Path $venv 'Scripts\python.exe'
$log = Join-Path $root 'installation.log'
try {
    if ([IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') -ine $root) {
        throw 'Run this installer from D:\stock\TWNextDayTop10.'
    }
    if (!(Test-Path -LiteralPath $basePython -PathType Leaf)) {
        throw ('Required Python is missing: ' + $basePython)
    }
    if (Test-Path -LiteralPath $venv) {
        $cfg = Join-Path $venv 'pyvenv.cfg'
        if (!(Test-Path -LiteralPath $cfg -PathType Leaf) -or !(Test-Path -LiteralPath $python -PathType Leaf)) {
            throw 'Existing .venv is not a valid project environment; no files were replaced.'
        }
        $homes = @(Get-Content -LiteralPath $cfg | Where-Object { $_ -match '^home\s*=' })
        if ($homes.Count -ne 1 -or (($homes[0] -split '=', 2)[1].Trim() -ine $baseHome)) {
            throw 'Existing .venv belongs to another Python installation; stopping.'
        }
    } else {
        & $basePython -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw ('venv creation failed: ' + $LASTEXITCODE) }
    }
    $writer = [IO.StreamWriter]::new($log, $false, [Text.UTF8Encoding]::new($false))
    try {
        & $python -m pip install -r (Join-Path $root 'requirements.txt') 2>&1 | ForEach-Object {
            $line = $_.ToString()
            Write-Host $line
            $writer.WriteLine($line)
        }
    } finally {
        $writer.Dispose()
    }
    if ($LASTEXITCODE -ne 0) { throw ('Dependency installation failed; see ' + $log) }
    Write-Host 'Project environment installation completed.'
    exit 0
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}