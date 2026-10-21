[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $PSScriptRoot 'Invoke-StockActions.ps1'
$tempRoot = Join-Path $env:TEMP ('stock-actions-selftest-' + [Guid]::NewGuid().ToString('N'))
$passed = 0
$failed = 0

function Assert-Test {
    param([Parameter(Mandatory = $true)][bool]$Condition, [Parameter(Mandatory = $true)][string]$Message)
    if (-not $Condition) { throw $Message }
    $script:passed++
    Write-Host ('[PASS] ' + $Message)
}

function Invoke-RunnerTest {
    param(
        [Parameter(Mandatory = $true)][string]$TestRoot,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $old = $env:STOCK_ACTIONS_TEST_MODE
    $oldPreference = $ErrorActionPreference
    $env:STOCK_ACTIONS_TEST_MODE = '1'
    $ErrorActionPreference = 'Continue'
    try {
        $output = @(& powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $runner -TestMode -RootOverride $TestRoot @Arguments 2>&1)
        $code = [int]$LASTEXITCODE
        return [pscustomobject]@{ code = $code; output = ($output -join "`n") }
    }
    finally {
        $ErrorActionPreference = $oldPreference
        if ($null -eq $old) { Remove-Item Env:STOCK_ACTIONS_TEST_MODE -ErrorAction SilentlyContinue }
        else { $env:STOCK_ACTIONS_TEST_MODE = $old }
    }
}

function New-TestCheckout {
    param([Parameter(Mandatory = $true)][string]$Path)
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    $setupPath = Join-Path $Path 'scripts\setup'
    New-Item -ItemType Directory -Path $setupPath -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $Path 'STOCK_SETUP_MANAGER.cmd') -Value '@echo off' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $setupPath 'install_d_stock_env.ps1') -Value '# stock toolkit fixture marker' -Encoding ASCII
    $wrappers = @(
        'INSTALL_D_STOCK_ENV.cmd', 'INSTALL_D_STOCK_ENV_FULL.cmd', 'CONFIGURE_WINDOWS_ENV.cmd',
        'OPEN_STOCK_TERMINAL.cmd', 'scripts\sources\clone_stock_analysis_repos.cmd',
        'scripts\sources\clone_legacy_compat_repos.cmd', 'scripts\compat\verify_stock_environment.cmd',
        'scripts\compat\repair_legacy_paths.cmd'
    )
    foreach ($relative in $wrappers) {
        $wrapper = Join-Path $Path $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $wrapper) -Force | Out-Null
        Set-Content -LiteralPath $wrapper -Value '@echo off' -Encoding ASCII
    }
}

function New-SessionLine {
    param(
        [Parameter(Mandatory = $true)][string]$SessionId,
        [Parameter(Mandatory = $true)][int]$Sequence,
        [Parameter(Mandatory = $true)][string]$ActionName
    )
    return [ordered]@{
        schema_version = '1.0'; session_id = $SessionId; sequence = $Sequence; event = 'action';
        action = $ActionName; workflow = 'self-test'; started_utc = (Get-Date).ToUniversalTime().ToString('o');
        finished_utc = (Get-Date).ToUniversalTime().ToString('o'); parameters = [pscustomobject]@{};
        precondition = [pscustomobject]@{ root = 'test'; validated = $true }; exit_code = 0;
        status = 'success'; evidence = [pscustomobject]@{ self_test = $true }
    }
}

try {
    New-TestCheckout -Path $tempRoot

    # JSON files and required schema fields parse before any action is run.
    $schema = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'action-schema.json') -Raw | ConvertFrom-Json
    $manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'action-manifest.json') -Raw | ConvertFrom-Json
    Assert-Test ($schema.required -contains 'schema_version') 'action schema has required schema_version'
    Assert-Test ([string]$manifest.schema_version -eq '1.0' -and $manifest.actions.Count -ge 13) 'action manifest JSON parses with all actions'
    foreach ($workflowFile in (Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'workflows') -Filter '*.json' -File)) {
        $workflow = Get-Content -LiteralPath $workflowFile.FullName -Raw | ConvertFrom-Json
        Assert-Test ([string]$workflow.schema_version -eq '1.0' -and $workflow.actions.Count -gt 0) ('workflow parses: ' + $workflowFile.Name)
    }
    $policyJson = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'action-policy.json') -Raw | ConvertFrom-Json
    Assert-Test ([string]$policyJson.origin_check -eq 'not_checked' -and [string]$manifest.origin_check -eq 'not_checked' -and
        (@($policyJson.root_markers) -join '|') -eq (@($manifest.root_markers) -join '|') -and
        $null -eq $policyJson.PSObject.Properties['expected_origin'] -and $null -eq $manifest.PSObject.Properties['expected_origin']) 'policy and manifest declare marker identity without an origin check'

    # PowerShell 5.1 parser check for every action script.
    foreach ($scriptFile in (Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1' -File)) {
        $tokens = $null; $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile($scriptFile.FullName, [ref]$tokens, [ref]$errors) | Out-Null
        Assert-Test ($errors.Count -eq 0) ('PowerShell syntax parses: ' + $scriptFile.Name)
    }

    . $runner

    # The discovery manifest mirrors the hard-coded allow-list and markers, and
    # source actions keep the DOWNLOAD_* default selection (no --all, no sync claim).
    Assert-Test ((@($script:ProjectMarkers) -join '|') -eq (@($manifest.root_markers) -join '|')) 'runner project markers match policy and manifest'
    Assert-Test ($manifest.actions.Count -eq $script:ActionDefinitions.Count) 'manifest lists every allow-listed action'
    foreach ($entry in $manifest.actions) {
        $definition = $script:ActionDefinitions[[string]$entry.name]
        Assert-Test ($null -ne $definition -and [string]$definition.wrapper -eq [string]$entry.wrapper -and
            ((@($definition.arguments) -join ' ') -eq (@($entry.arguments) -join ' ')) -and
            [string]$definition.risk -eq [string]$entry.risk -and [bool]$definition.replayable -eq [bool]$entry.replayable) ('manifest matches allow-list: ' + $entry.name)
    }
    foreach ($name in @('sources.check_primary', 'sources.sync_primary', 'sources.check_legacy', 'sources.sync_legacy')) {
        $definition = $script:ActionDefinitions[$name]
        Assert-Test (@($definition.arguments) -notcontains '--all' -and [string]$definition.description -notmatch 'synchroni') ('source action keeps default selection and wording: ' + $name)
    }

    # Identity evidence keys must not collide with the secret/PATH mask.
    $identityEvidence = Convert-ToSafeLogValue -Value ([ordered]@{ identity = 'project_markers'; origin_check = 'not_checked'; origin_enforced = $false; markers_validated = $true })
    Assert-Test ($identityEvidence.identity -eq 'project_markers' -and $identityEvidence.origin_check -eq 'not_checked' -and
        $identityEvidence.origin_enforced -eq $false -and $identityEvidence.markers_validated -eq $true) 'identity and origin evidence keys are not masked'

    $safeRun = Invoke-RunnerTest -TestRoot $tempRoot -Arguments @('-Workflow', 'safe-setup', '-DryRun')
    Assert-Test ($safeRun.code -eq 0) 'safe-setup dry-run completes'
    Assert-Test (-not (Test-Path -LiteralPath (Join-Path $tempRoot 'invoked.marker'))) 'dry-run does not execute a wrapper'
    $sessionFiles = @(Get-ChildItem -LiteralPath (Join-Path $tempRoot 'logs\sessions') -Filter '*.jsonl' -File)
    Assert-Test ($sessionFiles.Count -eq 1) 'dry-run creates one JSONL session'
    foreach ($sessionFile in $sessionFiles) {
        foreach ($line in [IO.File]::ReadAllLines($sessionFile.FullName)) {
            $record = $line | ConvertFrom-Json
            $parametersObject = $record.parameters
            Assert-Test ($parametersObject -is [pscustomobject] -and @($parametersObject.PSObject.Properties).Count -eq 0) 'fresh session parameters are an empty JSON object'
            Assert-Test ($null -eq $record.evidence.PSObject.Properties['origin_validated'] -and
                [string]$record.evidence.identity -eq 'project_markers' -and
                [string]$record.evidence.origin_check -eq 'not_checked' -and $record.evidence.origin_enforced -eq $false) 'evidence records origin as not checked, never as validated'
            Assert-Test ($null -eq $record.precondition.PSObject.Properties['origin'] -and
                [string]$record.precondition.identity -eq 'project_markers' -and $record.precondition.markers_validated -eq $true) 'precondition records project markers only'
        }
    }
    Assert-Test $true 'every generated JSONL line parses'

    # Existing pointer content is fail-closed and must remain byte-for-byte
    # unchanged when malformed or out of scope.
    $pointerPath = Join-Path $tempRoot 'logs\sessions\last-session.json'
    $validPointerBytes = [IO.File]::ReadAllBytes($pointerPath)
    $invalidPointerBytes = [Text.Encoding]::UTF8.GetBytes('{"schema_version":"1.0","command":"whoami"}')
    [IO.File]::WriteAllBytes($pointerPath, $invalidPointerBytes)
    $invalidHash = Get-ByteArrayHash -Bytes ([IO.File]::ReadAllBytes($pointerPath))
    $invalidPointerRun = Invoke-RunnerTest -TestRoot $tempRoot -Arguments @('-Workflow', 'verify-only', '-DryRun')
    Assert-Test ($invalidPointerRun.code -ne 0 -and $invalidHash -eq (Get-ByteArrayHash -Bytes ([IO.File]::ReadAllBytes($pointerPath)))) 'unknown pointer is blocked and bytes are preserved'
    [IO.File]::WriteAllBytes($pointerPath, $validPointerBytes)

    $outOfRootPointer = [ordered]@{
        schema_version = '1.0'; session_id = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
        session_file = 'C:\outside\evil.jsonl'; updated_utc = (Get-Date).ToUniversalTime().ToString('o')
    }
    $pointerUtf8 = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false
    [IO.File]::WriteAllText($pointerPath, ($outOfRootPointer | ConvertTo-Json -Compress), $pointerUtf8)
    $outOfRootHash = Get-ByteArrayHash -Bytes ([IO.File]::ReadAllBytes($pointerPath))
    $outOfRootRun = Invoke-RunnerTest -TestRoot $tempRoot -Arguments @('-Workflow', 'verify-only', '-DryRun')
    Assert-Test ($outOfRootRun.code -ne 0 -and $outOfRootHash -eq (Get-ByteArrayHash -Bytes ([IO.File]::ReadAllBytes($pointerPath)))) 'out-of-root pointer is blocked and bytes are preserved'
    [IO.File]::WriteAllBytes($pointerPath, $validPointerBytes)

    $beforePointerHash = Get-ByteArrayHash -Bytes ([IO.File]::ReadAllBytes($pointerPath))
    $validPointerRun = Invoke-RunnerTest -TestRoot $tempRoot -Arguments @('-Workflow', 'verify-only', '-DryRun')
    $afterPointerHash = Get-ByteArrayHash -Bytes ([IO.File]::ReadAllBytes($pointerPath))
    Assert-Test ($validPointerRun.code -eq 0 -and $beforePointerHash -ne $afterPointerHash) 'valid pointer is atomically updated'
    $sessionScratch = @(Get-ChildItem -LiteralPath (Join-Path $tempRoot 'logs\sessions') -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -like '.last-session.json.tmp.*' -or $_.Name -like '.last-session.json.bak.*' })
    Assert-Test ($sessionScratch.Count -eq 0) 'pointer temporary files do not remain'

    # Execute one test-only wrapper to verify semantic dispatch and exit code
    # handling; this wrapper exists only in the temporary checkout.
    $verifyStub = Join-Path $tempRoot 'scripts\compat\verify_stock_environment.cmd'
    Set-Content -LiteralPath $verifyStub -Value @('@echo off', 'echo invoked>"%~dp0..\..\invoked.marker"', 'exit /b 0') -Encoding ASCII
    $execRun = Invoke-RunnerTest -TestRoot $tempRoot -Arguments @('-Workflow', 'verify-only')
    Assert-Test ($execRun.code -eq 0 -and (Test-Path -LiteralPath (Join-Path $tempRoot 'invoked.marker'))) 'approved wrapper dispatch executes only in non-dry-run test'

    # Replay a fresh successful semantic session and inspect the newly created
    # replay session; parameters must remain an object, never an empty string.
    $sessionDirectory = Join-Path $tempRoot 'logs\sessions'
    $freshPointer = Get-Content -LiteralPath $pointerPath -Raw | ConvertFrom-Json
    $freshSessionPath = Join-Path $sessionDirectory ([string]$freshPointer.session_file)
    $freshReplayRun = Invoke-RunnerTest -TestRoot $tempRoot -Arguments @('-Replay', $freshSessionPath, '-DryRun')
    $replayPointer = Get-Content -LiteralPath $pointerPath -Raw | ConvertFrom-Json
    $replaySessionPath = Join-Path $sessionDirectory ([string]$replayPointer.session_file)
    $replayRecord = ([IO.File]::ReadAllLines($replaySessionPath) | Select-Object -Last 1) | ConvertFrom-Json
    Assert-Test ($freshReplayRun.code -eq 0) 'fresh successful semantic session replays in dry-run'
    Assert-Test ($replayRecord.parameters -is [pscustomobject] -and @($replayRecord.parameters.PSObject.Properties).Count -eq 0) 'replay session parameters remain an empty JSON object'

    # A failing wrapper stops the runner with exit code 1; output the wrapper
    # printed before failing is still shown after the error.
    $checkStub = Join-Path $tempRoot 'scripts\sources\clone_stock_analysis_repos.cmd'
    Set-Content -LiteralPath $checkStub -Value @('@echo off', 'echo STUB-WRAPPER-OUTPUT', 'exit /b 3') -Encoding ASCII
    $failedRun = Invoke-RunnerTest -TestRoot $tempRoot -Arguments @('-Action', 'sources.check_primary')
    Set-Content -LiteralPath $checkStub -Value '@echo off' -Encoding ASCII
    Assert-Test ($failedRun.code -eq 1 -and $failedRun.output -match 'STUB-WRAPPER-OUTPUT') 'failed wrapper exits 1 and its output is shown'

    # Request schema is fail-closed for unknown actions and raw command fields.
    $unknownRequest = Join-Path $tempRoot 'unknown.json'
    Set-Content -LiteralPath $unknownRequest -Value '{"schema_version":"1.0","action":"run.anything"}' -Encoding UTF8
    $unknownRun = Invoke-RunnerTest -TestRoot $tempRoot -Arguments @('-Request', $unknownRequest, '-DryRun')
    Assert-Test ($unknownRun.code -ne 0) 'unknown action is rejected'
    $rawRequest = Join-Path $tempRoot 'raw.json'
    Set-Content -LiteralPath $rawRequest -Value '{"schema_version":"1.0","action":"environment.verify","command":"whoami"}' -Encoding UTF8
    $rawRun = Invoke-RunnerTest -TestRoot $tempRoot -Arguments @('-Request', $rawRequest, '-DryRun')
    Assert-Test ($rawRun.code -ne 0) 'raw command field is rejected'

    # Replay parameters must remain strict. Tampering with a session to use a
    # string or array is rejected before a new session/pointer update begins.
    $replayDirectory = Join-Path $tempRoot 'logs\sessions'
    $stringParametersPath = Join-Path $replayDirectory '20260809T000003Z-dddddddddddddddddddddddddddddddd.jsonl'
    $stringParametersRecord = New-SessionLine -SessionId 'dddddddddddddddddddddddddddddddd' -Sequence 1 -ActionName 'environment.verify'
    $stringParametersRecord.parameters = ''
    Set-Content -LiteralPath $stringParametersPath -Value ($stringParametersRecord | ConvertTo-Json -Compress -Depth 8) -Encoding UTF8
    $pointerBeforeString = [IO.File]::ReadAllBytes($pointerPath)
    $pointerBeforeStringHash = Get-ByteArrayHash -Bytes $pointerBeforeString
    $stringParametersRun = Invoke-RunnerTest -TestRoot $tempRoot -Arguments @('-Replay', $stringParametersPath, '-DryRun')
    $pointerAfterString = [IO.File]::ReadAllBytes($pointerPath)
    Assert-Test ($stringParametersRun.code -ne 0 -and $pointerBeforeStringHash -eq (Get-ByteArrayHash -Bytes $pointerAfterString) -and (Test-ByteArraysEqual -Left $pointerBeforeString -Right $pointerAfterString)) 'string parameters replay is rejected before session creation'

    $arrayParametersPath = Join-Path $replayDirectory '20260809T000004Z-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee.jsonl'
    $arrayParametersRecord = New-SessionLine -SessionId 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee' -Sequence 1 -ActionName 'environment.verify'
    $arrayParametersRecord.parameters = @('unsafe')
    Set-Content -LiteralPath $arrayParametersPath -Value ($arrayParametersRecord | ConvertTo-Json -Compress -Depth 8) -Encoding UTF8
    $pointerBeforeArray = [IO.File]::ReadAllBytes($pointerPath)
    $pointerBeforeArrayHash = Get-ByteArrayHash -Bytes $pointerBeforeArray
    $arrayParametersRun = Invoke-RunnerTest -TestRoot $tempRoot -Arguments @('-Replay', $arrayParametersPath, '-DryRun')
    $pointerAfterArray = [IO.File]::ReadAllBytes($pointerPath)
    Assert-Test ($arrayParametersRun.code -ne 0 -and $pointerBeforeArrayHash -eq (Get-ByteArrayHash -Bytes $pointerAfterArray) -and (Test-ByteArraysEqual -Left $pointerBeforeArray -Right $pointerAfterArray)) 'array parameters replay is rejected before session creation'

    # A missing project marker stops before session creation; replay paths are scoped.
    $missingMarkerRoot = Join-Path $env:TEMP ('stock-actions-missing-marker-' + [Guid]::NewGuid().ToString('N'))
    New-TestCheckout -Path $missingMarkerRoot
    try {
        Remove-Item -LiteralPath (Join-Path $missingMarkerRoot 'STOCK_SETUP_MANAGER.cmd') -Force
        $missingMarkerRun = Invoke-RunnerTest -TestRoot $missingMarkerRoot -Arguments @('-Workflow', 'verify-only', '-DryRun')
        Assert-Test ($missingMarkerRun.code -ne 0) 'missing project marker is rejected'
        Assert-Test (-not (Test-Path -LiteralPath (Join-Path $missingMarkerRoot 'logs'))) 'missing project marker creates no session log'
    }
    finally { Remove-Item -LiteralPath $missingMarkerRoot -Recurse -Force -ErrorAction SilentlyContinue }
    $outsideReplay = Join-Path $tempRoot 'outside.jsonl'
    Set-Content -LiteralPath $outsideReplay -Value '{}' -Encoding UTF8
    $outsideRun = Invoke-RunnerTest -TestRoot $tempRoot -Arguments @('-Replay', $outsideReplay, '-DryRun')
    Assert-Test ($outsideRun.code -ne 0) 'replay path outside sessions is rejected'

    # Secret masking is tested directly against the runner helper.
    $masked = Convert-ToSafeLogValue -Value ([pscustomobject]@{ FINMIND_TOKEN = 'secret'; API_KEY = 'secret2'; PATH = 'secret3'; safe = 'ok' })
    Assert-Test ($masked.FINMIND_TOKEN -eq '[REDACTED]' -and $masked.API_KEY -eq '[REDACTED]' -and $masked.PATH -eq '[REDACTED]' -and $masked.safe -eq 'ok') 'secret and PATH values are masked'

    # Replay consumes semantic success events only; high-risk and terminal actions block by default.
    $replayDirectory = Join-Path $tempRoot 'logs\sessions'
    $replayPath = Join-Path $replayDirectory '20260809T000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jsonl'
    $sessionId = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    $successLine = New-SessionLine -SessionId $sessionId -Sequence 1 -ActionName 'environment.verify'
    Set-Content -LiteralPath $replayPath -Value ($successLine | ConvertTo-Json -Compress -Depth 8) -Encoding UTF8
    $replayRun = Invoke-RunnerTest -TestRoot $tempRoot -Arguments @('-Replay', $replayPath, '-DryRun')
    Assert-Test ($replayRun.code -eq 0) 'replay reruns a semantic success action in dry-run'
    # Records written by the earlier runner (origin null, origin_validated true)
    # still replay; replay does not interpret precondition or evidence.
    $earlierPath = Join-Path $replayDirectory '20260809T000005Z-ffffffffffffffffffffffffffffffff.jsonl'
    $earlierLine = New-SessionLine -SessionId 'ffffffffffffffffffffffffffffffff' -Sequence 1 -ActionName 'environment.verify'
    $earlierLine.workflow = 'verify-only'
    $earlierLine.precondition = [ordered]@{ root = $tempRoot; origin = $null; validated = $true }
    $earlierLine.evidence = [ordered]@{ wrapper = 'scripts\compat\verify_stock_environment.cmd'; mode = 'execute'; policy_root = $tempRoot; origin_validated = $true; executed = $true }
    Set-Content -LiteralPath $earlierPath -Value ($earlierLine | ConvertTo-Json -Compress -Depth 8) -Encoding UTF8
    $earlierRun = Invoke-RunnerTest -TestRoot $tempRoot -Arguments @('-Replay', $earlierPath, '-DryRun')
    Assert-Test ($earlierRun.code -eq 0) 'earlier-format record with origin_validated replays in dry-run'
    $highRiskPath = Join-Path $replayDirectory '20260809T000001Z-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.jsonl'
    $highRiskLine = New-SessionLine -SessionId 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' -Sequence 1 -ActionName 'paths.repair_apply'
    Set-Content -LiteralPath $highRiskPath -Value ($highRiskLine | ConvertTo-Json -Compress -Depth 8) -Encoding UTF8
    $highRiskRun = Invoke-RunnerTest -TestRoot $tempRoot -Arguments @('-Replay', $highRiskPath, '-DryRun')
    Assert-Test ($highRiskRun.code -ne 0) 'high-risk action is blocked during default replay'
    $terminalPath = Join-Path $replayDirectory '20260809T000002Z-cccccccccccccccccccccccccccccccc.jsonl'
    $terminalLine = New-SessionLine -SessionId 'cccccccccccccccccccccccccccccccc' -Sequence 1 -ActionName 'terminal.open'
    Set-Content -LiteralPath $terminalPath -Value ($terminalLine | ConvertTo-Json -Compress -Depth 8) -Encoding UTF8
    $terminalRun = Invoke-RunnerTest -TestRoot $tempRoot -Arguments @('-Replay', $terminalPath, '-DryRun')
    Assert-Test ($terminalRun.code -ne 0) 'terminal.open is not replayable'

    Write-Host ("Self-test passed: {0} checks." -f $passed) -ForegroundColor Green
    exit 0
}
catch {
    Write-Error ('Self-test failed: ' + $_.Exception.Message)
    exit 1
}
finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
