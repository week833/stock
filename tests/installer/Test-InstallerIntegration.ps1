[CmdletBinding()]
param([switch]$CiMode)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$installerPath = Join-Path $projectRoot 'scripts\setup\install_d_stock_env.ps1'
$sourcePath = Join-Path $projectRoot 'scripts\sources\source_manager.ps1'
$repairPath = Join-Path $projectRoot 'scripts\compat\repair_legacy_paths_guard.ps1'

function Assert-Smoke([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Write-FixtureText([string]$Path, [string]$Text) {
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Path)) | Out-Null
    [IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($false))
}

function Test-ScriptSyntax {
    $paths = @($PSCommandPath)
    foreach ($relative in @('scripts\setup', 'scripts\sources', 'scripts\compat', 'scripts\actions')) {
        $directory = Join-Path $projectRoot $relative
        if (Test-Path -LiteralPath $directory -PathType Container) {
            $paths += @(Get-ChildItem -LiteralPath $directory -Filter '*.ps1' -File | ForEach-Object { $_.FullName })
        }
    }
    foreach ($path in $paths) {
        $tokens = $null
        $errors = $null
        [Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors) | Out-Null
        Assert-Smoke ($errors.Count -eq 0) ("PowerShell syntax error in {0}: {1}" -f $path, ($errors | Out-String))
    }
    Write-Host ('syntax_parse: PASS ({0} scripts)' -f $paths.Count)
}

function Test-EmptyKeyTemplate {
    $template = [IO.File]::ReadAllText((Join-Path $projectRoot '.env.example'), [Text.Encoding]::UTF8)
    foreach ($name in @('FINMIND_TOKEN', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY')) {
        Assert-Smoke ($template -match ('(?m)^' + [regex]::Escape($name) + '=\r?$')) ("Template must leave {0} empty" -f $name)
    }
    $ignore = [IO.File]::ReadAllLines((Join-Path $projectRoot '.gitignore'), [Text.Encoding]::UTF8)
    Assert-Smoke ($ignore -contains '.env') '.gitignore must contain .env'
    Write-Host 'empty_key_template: PASS'
}

function Test-NonProjectTarget([string]$Fixture) {
    . $installerPath
    $target = Join-Path $Fixture 'unrelated-project'
    $sentinel = Join-Path $target 'keep.txt'
    Write-FixtureText $sentinel 'unrelated files must stay here'
    $rejected = $false
    try { $null = Get-StockProjectState -Root $target }
    catch { $rejected = $true }
    Assert-Smoke $rejected 'Non-project target was accepted'
    Assert-Smoke ([IO.File]::ReadAllText($sentinel) -ceq 'unrelated files must stay here') 'Non-project contents changed'
    Assert-Smoke (@(Get-ChildItem -LiteralPath $target -Force).Count -eq 1) 'Non-project target gained files'
    Write-Host 'non_project_target_preserved: PASS'
}

function Test-HealthyVenv([string]$Fixture) {
    . $installerPath
    Write-FixtureText (Join-Path $Fixture 'STOCK_SETUP_MANAGER.cmd') '@echo off'
    Write-FixtureText (Join-Path $Fixture 'scripts\setup\install_d_stock_env.ps1') '# private project marker'
    $venv = Join-Path $Fixture '.venv'
    $python = Join-Path $venv 'Scripts\python.exe'
    Write-FixtureText $python 'ordinary private path; execution delegated to the probe below'
    $calls = [Collections.Generic.List[string]]::new()
    function Invoke-StockProbe {
        param([string]$Executable, [string[]]$Arguments)
        $calls.Add('probe')
        Assert-Smoke ($Executable -ceq $python) 'Probe did not use the private venv path'
        [pscustomobject]@{ ExitCode=0; Output=@('3.13.7', $venv, (Join-Path $Fixture 'host-python')) }
    }
    function Find-StockPython { $calls.Add('host-discovery'); throw 'Healthy venv must not search host Python' }
    function Install-StockTool { $calls.Add('bootstrap'); throw 'Healthy venv must not bootstrap tools' }
    $selection = Get-StockInterpreter -Root $Fixture
    Assert-Smoke ($selection.Path -ceq $python -and $selection.UsesVenv -and -not $selection.NeedsPythonInstall) 'Healthy venv was not selected'
    Assert-Smoke ($calls.Count -gt 0 -and @($calls | Where-Object { $_ -ne 'probe' }).Count -eq 0) 'Healthy venv used discovery or bootstrap'
    Write-Host 'healthy_venv_selected_without_bootstrap: PASS'
}

function Test-ExistingSource([string]$Fixture) {
    . $sourcePath
    $target = Join-Path $Fixture 'repos\existing'
    $sentinel = Join-Path $target 'keep.txt'
    Write-FixtureText $sentinel 'existing source stays untouched'
    $manifest = Join-Path $Fixture 'sources.json'
    $spec = @{ sources = @(@{ id='existing'; url='https://example.invalid/source.git'; target='existing'; scope='primary'; default_install=$true }) }
    Write-FixtureText $manifest ($spec | ConvertTo-Json -Depth 5)
    $externalCalls = [Collections.Generic.List[string]]::new()
    function Resolve-SourceGit { $externalCalls.Add('resolve-git'); throw 'Existing source must not need Git' }
    function Invoke-SourceClone { param($GitPath, $Url, $PendingPath); $externalCalls.Add('clone'); throw 'Existing source must not clone' }
    $plan = @(Get-SourcePlan -Root $Fixture -ManifestPath $manifest)
    $result = @(Invoke-SourceDownload -Root $Fixture -ManifestPath $manifest)
    Assert-Smoke ($plan.Count -eq 1 -and $plan[0].Status -eq 'existing') 'Existing target plan was not recognized'
    Assert-Smoke ($result.Count -eq 1 -and $result[0].Status -eq 'skipped') 'Existing source was not skipped'
    Assert-Smoke ($externalCalls.Count -eq 0) 'Existing source invoked Git or clone'
    Assert-Smoke ([IO.File]::ReadAllText($sentinel) -ceq 'existing source stays untouched') 'Existing source contents changed'
    Assert-Smoke (@(Get-ChildItem -LiteralPath $target -Force).Count -eq 1) 'Existing source target gained files'
    Write-Host 'existing_source_not_cloned: PASS'
}

function Test-MissingOptionalRepair([string]$Fixture) {
    . $repairPath
    $repository = Join-Path $Fixture 'repository'
    $legacy = Join-Path $Fixture 'legacy'
    [IO.Directory]::CreateDirectory((Join-Path $repository 'repos')) | Out-Null
    [IO.Directory]::CreateDirectory($legacy) | Out-Null
    $link = Join-Path $repository 'views\optional-source'
    $target = Join-Path $repository 'repos\not-downloaded'
    $map = @([pscustomobject]@{ Link=$link; Target=$target; Optional=$true })
    $result = Invoke-LegacyPathRepair -Mode Apply -ConfirmApply -Map $map -RepositoryRoot $repository -LegacyRoot $legacy
    Assert-Smoke ($result.ExitCode -eq 0 -and $result.CreatedCount -eq 0 -and $result.SkippedCount -eq 1) 'Missing optional source blocked repair'
    Assert-Smoke ($result.Items.Count -eq 1 -and $result.Items[0].Status -eq 'SkippedOptional') 'Missing optional source was not reported as skipped'
    Assert-Smoke (-not (Test-Path -LiteralPath $link) -and -not (Test-Path -LiteralPath $target)) 'Repair created an absent optional source or link'
    Write-Host 'missing_optional_repair_not_blocking: PASS'

    $available = Join-Path $repository 'repos\available'
    Write-FixtureText (Join-Path $available 'keep.txt') 'available source survives junction cleanup'
    $newLink = Join-Path $repository 'new-parent\available'
    $requiredMap = @([pscustomobject]@{ Link=$newLink; Target=$available; Optional=$false })
    try {
        $created = Invoke-LegacyPathRepair -Mode Apply -ConfirmApply -Map $requiredMap -RepositoryRoot $repository -LegacyRoot $legacy
        Assert-Smoke ($created.ExitCode -eq 0 -and $created.CreatedCount -eq 1) 'Repair did not create the missing parent and junction'
        $item = Get-Item -LiteralPath $newLink -Force
        Assert-Smoke ($item.LinkType -eq 'Junction' -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) 'Repair did not create a native directory junction'
        $junctionTarget = [string](@($item.Target)[0])
        Assert-Smoke ([IO.Path]::GetFullPath($junctionTarget) -ieq [IO.Path]::GetFullPath($available)) 'Repair junction points to the wrong source'
        Assert-Smoke ([IO.File]::ReadAllText((Join-Path $newLink 'keep.txt')) -ceq 'available source survives junction cleanup') 'Repaired junction does not expose its source'
    }
    finally {
        $item = Get-Item -LiteralPath $newLink -Force -ErrorAction SilentlyContinue
        if ($item) {
            Assert-Smoke (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) 'Expected an owned junction for cleanup'
            [IO.Directory]::Delete($newLink, $false)
        }
    }
    Assert-Smoke ([IO.File]::ReadAllText((Join-Path $available 'keep.txt')) -ceq 'available source survives junction cleanup') 'Junction cleanup changed the source'
    Write-Host 'repair_missing_parent_native_junction: PASS'
}

$fixture = $null
try {
    Test-ScriptSyntax
    Test-EmptyKeyTemplate
    $temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
    $fixture = Join-Path $temporaryRoot ('stock-installer-smoke-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $fixture | Out-Null
    Test-NonProjectTarget (Join-Path $fixture 'non-project')
    Test-HealthyVenv (Join-Path $fixture 'healthy-venv')
    Test-ExistingSource (Join-Path $fixture 'existing-source')
    Test-MissingOptionalRepair (Join-Path $fixture 'missing-optional')
    Write-Host 'installer private smoke: PASS (4 cases + repair junction positive)' -ForegroundColor Green
    exit 0
}
catch {
    Write-Host ('installer private smoke: FAIL - ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
finally {
    if ($fixture -and (Test-Path -LiteralPath $fixture)) {
        $resolved = [IO.Path]::GetFullPath($fixture)
        if (-not $resolved.StartsWith($temporaryRoot + '\stock-installer-smoke-', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Unexpected private fixture cleanup path'
        }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
