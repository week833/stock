[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$RepoRoot = 'D:\stock\GitHub'
$TargetEnv = Join-Path $RepoRoot '.env'

function Write-Info {
    param([AllowEmptyString()][string]$Message = '', [ConsoleColor]$Color = [ConsoleColor]::Gray)
    Write-Host $Message -ForegroundColor $Color
}

function ConvertFrom-SecureValue {
    param([Parameter(Mandatory = $true)][Security.SecureString]$SecureValue)

    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}

function Read-SecretValue {
    param([Parameter(Mandatory = $true)][string]$Prompt)
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    return ConvertFrom-SecureValue -SecureValue $secure
}

function Format-DotEnvValue {
    param([AllowEmptyString()][string]$Value = '')

    if ($Value -eq '') {
        return ''
    }

    if ($Value -match '^[A-Za-z0-9_./:@+\-=\\]+$') {
        return $Value
    }

    $escaped = $Value.Replace('\\', '\\\\').Replace('"', '\"')
    $escaped = $escaped.Replace("`r", '').Replace("`n", '\n')
    return '"' + $escaped + '"'
}

function Test-DotEnvFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }

    $lineNumber = 0
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        $lineNumber++
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) {
            continue
        }

        if ($trimmed.StartsWith('export ')) {
            $trimmed = $trimmed.Substring(7).TrimStart()
        }

        $equals = $trimmed.IndexOf('=')
        if ($equals -lt 1) {
            Write-Info "[WARN] Invalid .env syntax at line $lineNumber." ([ConsoleColor]::Yellow)
            return $false
        }

        $name = $trimmed.Substring(0, $equals).Trim()
        if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
            Write-Info "[WARN] Invalid variable name at line $lineNumber." ([ConsoleColor]::Yellow)
            return $false
        }
    }

    return $true
}

function New-EnvTemporaryPath {
    $name = ".env.tmp.$PID.$([Guid]::NewGuid().ToString('N'))"
    return Join-Path (Split-Path -Parent $TargetEnv) $name
}

function Commit-EnvFile {
    param([Parameter(Mandatory = $true)][string]$PreparedPath)

    if (-not (Test-DotEnvFile -Path $PreparedPath)) {
        throw 'The prepared .env failed validation.'
    }
    if (Test-Path -LiteralPath $TargetEnv -PathType Leaf) {
        [IO.File]::Replace($PreparedPath, $TargetEnv, $null)
    }
    else {
        [IO.File]::Move($PreparedPath, $TargetEnv)
    }
}

function Add-Candidate {
    param(
        [Parameter(Mandatory = $true)][System.Collections.Generic.List[object]]$Candidates,
        [Parameter(Mandatory = $true)][System.Collections.Generic.HashSet[string]]$Seen,
        [Parameter(Mandatory = $true)][string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return
    }

    $file = Get-Item -LiteralPath $Path -Force
    if ($file.Length -eq 0) {
        return
    }

    $key = $file.FullName.ToLowerInvariant()
    if ($key -eq $TargetEnv.ToLowerInvariant()) {
        return
    }

    if ($Seen.Add($key)) {
        $Candidates.Add($file)
    }
}

function Find-EnvCandidates {
    $candidates = New-Object 'System.Collections.Generic.List[object]'
    $seen = New-Object 'System.Collections.Generic.HashSet[string]'

    foreach ($path in @('D:\stock\.env', 'D:\Downloads\stock\.env')) {
        Add-Candidate -Candidates $candidates -Seen $seen -Path $path
    }

    $backupRoots = @(Get-ChildItem -LiteralPath 'D:\' -Directory -Filter 'stock_backup_*' -ErrorAction SilentlyContinue)
    foreach ($root in $backupRoots) {
        foreach ($file in @(Get-ChildItem -LiteralPath $root.FullName -File -Filter '.env' -Recurse -Force -ErrorAction SilentlyContinue)) {
            Add-Candidate -Candidates $candidates -Seen $seen -Path $file.FullName
        }
    }

    if (Test-Path -LiteralPath 'D:\stock') {
        $recoveryRoots = @(Get-ChildItem -LiteralPath 'D:\stock' -Directory -Filter 'Recovered_from_previous_installer_*' -ErrorAction SilentlyContinue)
        foreach ($root in $recoveryRoots) {
            foreach ($file in @(Get-ChildItem -LiteralPath $root.FullName -File -Filter '.env' -Recurse -Force -ErrorAction SilentlyContinue)) {
                Add-Candidate -Candidates $candidates -Seen $seen -Path $file.FullName
            }
        }
    }

    return @($candidates | Sort-Object LastWriteTime -Descending)
}

function Restore-EnvCandidate {
    param([Parameter(Mandatory = $true)][string]$SourcePath)

    if (-not (Test-DotEnvFile -Path $SourcePath)) {
        throw 'The selected source is not a valid .env file.'
    }

    $temporary = New-EnvTemporaryPath
    try {
        Copy-Item -LiteralPath $SourcePath -Destination $temporary -Force
        Commit-EnvFile -PreparedPath $temporary
    }
    finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
    Write-Info "[OK] Recovered .env to $TargetEnv" ([ConsoleColor]::Green)
}

function Create-NewEnv {
    $values = [ordered]@{}

    $values['STOCK_HOME'] = $RepoRoot
    $values['STOCK_EXTERNAL_REPOS'] = (Join-Path $RepoRoot 'repos')
    $values['PYTHONUTF8'] = '1'
    $values['PYTHONIOENCODING'] = 'utf-8'
    $values['TZ'] = 'Asia/Taipei'
    $values['FINMIND_AUTH_MODE'] = 'header'

    Write-Info ''
    Write-Info 'Enter secret values. Input is hidden. Press Enter to leave an optional value blank.' ([ConsoleColor]::Cyan)

    $values['FINMIND_TOKEN'] = Read-SecretValue -Prompt 'FINMIND_TOKEN'
    $values['ANTHROPIC_API_KEY'] = Read-SecretValue -Prompt 'ANTHROPIC_API_KEY (optional)'
    $values['OPENAI_API_KEY'] = Read-SecretValue -Prompt 'OPENAI_API_KEY (optional)'

    while ($true) {
        $answer = Read-Host 'Add another custom variable? [y/N]'
        if ($answer -notmatch '^[Yy]$') {
            break
        }

        $name = (Read-Host 'Variable name').Trim()
        if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
            Write-Info '[WARN] Invalid variable name. Use letters, numbers and underscores; the first character cannot be a number.' ([ConsoleColor]::Yellow)
            continue
        }

        $values[$name] = Read-SecretValue -Prompt "$name value"
    }

    $lines = New-Object 'System.Collections.Generic.List[string]'
    $lines.Add('# Generated by CREATE_OR_RECOVER_ENV.cmd')
    $lines.Add('# Secrets are ignored by Git through .gitignore.')
    $lines.Add('')

    foreach ($entry in $values.GetEnumerator()) {
        $formatted = Format-DotEnvValue -Value ([string]$entry.Value)
        $lines.Add("$($entry.Key)=$formatted")
    }

    $temporary = New-EnvTemporaryPath
    try {
        $encoding = New-Object Text.UTF8Encoding($false)
        [IO.File]::WriteAllLines($temporary, $lines, $encoding)
        Commit-EnvFile -PreparedPath $temporary
    }
    finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }

    Write-Info "[OK] Created $TargetEnv" ([ConsoleColor]::Green)
    Write-Info '[OK] Secret values were not displayed or written to a log.' ([ConsoleColor]::Green)
}

try {
    Write-Info '============================================================' ([ConsoleColor]::Cyan)
    Write-Info ' Create or recover D:\stock\GitHub\.env' ([ConsoleColor]::Cyan)
    Write-Info '============================================================' ([ConsoleColor]::Cyan)

    if (-not (Test-Path -LiteralPath $RepoRoot -PathType Container)) {
        throw 'D:\stock\GitHub does not exist. Complete the safe installer first, then run this tool.'
    }

    if (-not ((Test-Path -LiteralPath (Join-Path $RepoRoot '.git')) -or (Test-Path -LiteralPath (Join-Path $RepoRoot 'requirements.txt')))) {
        throw 'D:\stock\GitHub is not the stock toolkit repository. No file was created.'
    }

    if (Test-Path -LiteralPath $TargetEnv -PathType Leaf) {
        Write-Info "[FOUND] Current .env already exists: $TargetEnv" ([ConsoleColor]::Green)
        Write-Info 'It will not be overwritten unless you explicitly choose recovery or creation.' ([ConsoleColor]::Yellow)
    }

    $candidates = @(Find-EnvCandidates)

    Write-Info ''
    Write-Info '[1] Recover a previous .env backup' ([ConsoleColor]::White)
    Write-Info '[2] Create a new .env with hidden input' ([ConsoleColor]::White)
    Write-Info '[3] Keep the current .env and exit' ([ConsoleColor]::White)
    Write-Info ''

    $choice = Read-Host 'Choose 1, 2 or 3'

    switch ($choice) {
        '1' {
            if ($candidates.Count -eq 0) {
                Write-Info '[INFO] No previous .env backup was found.' ([ConsoleColor]::Yellow)
                Write-Info 'Starting new .env creation instead.' ([ConsoleColor]::Yellow)
                Create-NewEnv
                break
            }

            Write-Info ''
            Write-Info 'Recovered candidates (contents are not displayed):' ([ConsoleColor]::Cyan)
            for ($i = 0; $i -lt $candidates.Count; $i++) {
                $file = $candidates[$i]
                Write-Info ("[{0}] {1}  Modified={2}" -f ($i + 1), $file.FullName, $file.LastWriteTime)
            }

            $selectionText = Read-Host 'Choose a candidate number, or 0 to cancel'
            $selection = 0
            if (-not [int]::TryParse($selectionText, [ref]$selection)) {
                throw 'Invalid selection.'
            }
            if ($selection -eq 0) {
                Write-Info '[INFO] Recovery cancelled.' ([ConsoleColor]::Yellow)
                break
            }
            if ($selection -lt 1 -or $selection -gt $candidates.Count) {
                throw 'Selection is outside the available range.'
            }

            Restore-EnvCandidate -SourcePath $candidates[$selection - 1].FullName
        }
        '2' {
            Create-NewEnv
        }
        '3' {
            Write-Info '[INFO] Current .env was not changed.' ([ConsoleColor]::Yellow)
        }
        default {
            throw 'Invalid menu choice.'
        }
    }

    if (Test-Path -LiteralPath $TargetEnv -PathType Leaf) {
        if (Test-DotEnvFile -Path $TargetEnv) {
            $keys = New-Object 'System.Collections.Generic.List[string]'
            foreach ($line in [IO.File]::ReadAllLines($TargetEnv)) {
                $trimmed = $line.Trim()
                if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
                if ($trimmed.StartsWith('export ')) { $trimmed = $trimmed.Substring(7).TrimStart() }
                $equals = $trimmed.IndexOf('=')
                if ($equals -gt 0) { $keys.Add($trimmed.Substring(0, $equals).Trim()) }
            }
            Write-Info ''
            Write-Info '[OK] .env validation passed.' ([ConsoleColor]::Green)
            Write-Info ('Configured variable names: ' + (($keys | Sort-Object -Unique) -join ', '))
            Write-Info 'Values are intentionally not displayed.' ([ConsoleColor]::DarkGray)
        }
    }

    exit 0
}
catch {
    Write-Info ''
    Write-Info '[ERROR] .env creation or recovery failed safely.' ([ConsoleColor]::Red)
    Write-Info $_.Exception.Message ([ConsoleColor]::Red)
    Write-Info 'No secret value was logged.' ([ConsoleColor]::Yellow)
    exit 1
}
