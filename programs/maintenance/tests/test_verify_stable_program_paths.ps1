[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$maintenance = Split-Path -Parent $PSScriptRoot
$programs = Split-Path -Parent $maintenance
$verifier = Join-Path $programs 'VERIFY_STABLE_PROGRAM_PATHS.ps1'
$failures = New-Object System.Collections.Generic.List[string]

function Assert-Contract {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { $failures.Add($Message) }
}

function New-Fixture {
    param([string[]]$Registered, [string[]]$Files)

    $root = Join-Path ([System.IO.Path]::GetTempPath()) ('svpp_' + [guid]::NewGuid().ToString('N').Substring(0, 12))
    $fixturePrograms = Join-Path $root 'programs'
    New-Item -ItemType Directory -Path $fixturePrograms -Force | Out-Null
    Copy-Item -LiteralPath $verifier -Destination (Join-Path $fixturePrograms 'VERIFY_STABLE_PROGRAM_PATHS.ps1')
    $manifest = @('# fixture manifest', 'programs\VERIFY_STABLE_PROGRAM_PATHS.ps1') + $Registered
    [System.IO.File]::WriteAllText(
        (Join-Path $fixturePrograms 'STABLE_PROGRAM_PATHS.txt'),
        ($manifest -join "`n") + "`n",
        (New-Object System.Text.UTF8Encoding($false))
    )
    foreach ($relative in $Files) {
        $path = Join-Path $root $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $path) -Force | Out-Null
        [System.IO.File]::WriteAllText($path, "x = 1`n")
    }
    return $root
}

function Invoke-Verifier {
    param([string]$Root)

    $script = Join-Path $Root 'programs\VERIFY_STABLE_PROGRAM_PATHS.ps1'
    $output = & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $script -Root $Root -SkipMixPaths 2>&1 |
        ForEach-Object { "$_" }
    return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Text = ($output -join "`n") }
}

function Test-Case {
    param([string]$Name, [string[]]$Registered, [string[]]$Files, [int]$ExpectedExit, [string[]]$Expect = @())

    $root = New-Fixture -Registered $Registered -Files $Files
    try {
        $result = Invoke-Verifier -Root $root
        Assert-Contract ($result.ExitCode -eq $ExpectedExit) ("{0}:exit={1}:expected={2}:{3}" -f $Name, $result.ExitCode, $ExpectedExit, $result.Text)
        foreach ($fragment in $Expect) {
            Assert-Contract ($result.Text.Contains($fragment)) ("{0}:output_missing:{1}" -f $Name, $fragment)
        }
    }
    finally {
        Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$longName = 'a' * 49
$scratchFolder = 'run_' + ('b' * 60)

Test-Case 'registered_files_pass' `
    -Registered @('programs\tool.py') `
    -Files @('programs\tool.py') `
    -ExpectedExit 0 -Expect @('[OK] Stable program paths: 2 files')

Test-Case 'unregistered_program_is_reported' `
    -Registered @('programs\tool.py') `
    -Files @('programs\tool.py', 'programs\extra.py') `
    -ExpectedExit 1 -Expect @('not registered', 'programs\extra.py')

Test-Case 'missing_registered_program_is_reported' `
    -Registered @('programs\tool.py', 'programs\gone.py') `
    -Files @('programs\tool.py') `
    -ExpectedExit 1 -Expect @('deleted, moved, or renamed', 'programs\gone.py')

Test-Case 'long_program_name_is_reported' `
    -Registered @("programs\$longName.py") `
    -Files @("programs\$longName.py") `
    -ExpectedExit 1 -Expect @('program name > 48')

Test-Case 'long_folder_name_outside_scratch_is_reported' `
    -Registered @('programs\tool.py') `
    -Files @('programs\tool.py', "docs\$scratchFolder\note.py") `
    -ExpectedExit 1 -Expect @('folder name > 48')

Test-Case 'generated_test_scratch_tree_is_not_managed_source' `
    -Registered @('programs\tool.py') `
    -Files @('programs\tool.py', "test_scratch\session\$scratchFolder\case.py") `
    -ExpectedExit 0 -Expect @('[OK] Path policy')

if ($failures.Count -gt 0) {
    $failures | ForEach-Object { Write-Error $_ }
    exit 1
}
Write-Output ('PASS verify_stable_program_paths_cases={0}' -f 6)
exit 0
