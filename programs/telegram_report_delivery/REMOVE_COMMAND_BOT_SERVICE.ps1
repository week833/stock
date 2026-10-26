param(
    [switch]$Elevated
)

$ErrorActionPreference = "Stop"
$serviceName = "DStockTelegramCommandBot"
$suite = Split-Path -Parent $MyInvocation.MyCommand.Path
$wrapper = Join-Path $suite "service\DStockTelegramCommandBot.exe"

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Administrator)) {
    if ($Elevated) {
        throw "Administrator elevation was requested but is not active."
    }
    $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Elevated' -f $PSCommandPath
    $process = Start-Process -FilePath $powershell -Verb RunAs -ArgumentList $arguments -Wait -PassThru
    exit $process.ExitCode
}

$service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($null -eq $service) {
    Write-Output "Windows service is not installed: $serviceName"
    exit 0
}
if (-not (Test-Path -LiteralPath $wrapper -PathType Leaf)) {
    throw "Service wrapper is missing: $wrapper"
}

if ($service.Status -ne "Stopped") {
    & $wrapper stopwait | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Could not stop service (exit $LASTEXITCODE)" }
}
& $wrapper uninstall | Out-Host
if ($LASTEXITCODE -ne 0) { throw "Could not uninstall service (exit $LASTEXITCODE)" }
Write-Output "Windows service removed: $serviceName"
