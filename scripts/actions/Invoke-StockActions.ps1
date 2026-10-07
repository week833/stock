[CmdletBinding()]
param(
    [switch]$List,
    [string]$Workflow,
    [string]$Action,
    [switch]$DryRun,
    [string]$Replay,
    [switch]$ReplayLast,
    [string]$Request,
    [switch]$AllowRepairApply,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs,
    [switch]$EnvCli,
    [switch]$Interactive,
    [switch]$TestMode,
    [string]$RootOverride
)

# Semantic action runner for the fixed D:\stock\GitHub checkout.
#
# The action and workflow tables below are deliberately hard-coded.  JSON
# manifests are provided for AI/tooling discovery, but a manifest cannot add a
# command or a script to the executable allow-list.  PowerShell 5.1 syntax is
# used intentionally.

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$script:SchemaVersion = '1.0'
$script:ExpectedRoot = 'D:\stock\GitHub'
$script:ExpectedOrigin = 'https://github.com/week833/stock.git'
$script:Root = $null
$script:SessionId = $null
$script:SessionLogPath = $null
$script:SessionSequence = 0
$script:CurrentWorkflow = $null
$script:DryRunMode = $false
$script:AllowRepairApplyMode = $false
$script:TestModeActive = $false

$script:ActionDefinitions = [ordered]@{
    'environment.preflight' = [ordered]@{
        wrapper = 'INSTALL_D_STOCK_ENV.cmd'; arguments = [string[]]@('/PREFLIGHT');
        risk = 'safe'; replayable = $true; description = 'Read-only environment preflight'
    }
    'environment.install_core' = [ordered]@{
        wrapper = 'INSTALL_D_STOCK_ENV.cmd'; arguments = [string[]]@();
        risk = 'write'; replayable = $true; description = 'Install the core environment'
    }
    'environment.install_full' = [ordered]@{
        wrapper = 'INSTALL_D_STOCK_ENV_FULL.cmd'; arguments = [string[]]@();
        risk = 'write'; replayable = $true; description = 'Install the core environment and sources'
    }
    'sources.check_primary' = [ordered]@{
        wrapper = 'scripts\sources\clone_stock_analysis_repos.cmd'; arguments = [string[]]@('--check');
        risk = 'safe'; replayable = $true; description = 'Read-only primary source check'
    }
    'sources.sync_primary' = [ordered]@{
        wrapper = 'scripts\sources\clone_stock_analysis_repos.cmd'; arguments = [string[]]@();
        risk = 'write'; replayable = $true; description = 'Synchronize primary research sources'
    }
    'sources.check_legacy' = [ordered]@{
        wrapper = 'scripts\sources\clone_legacy_compat_repos.cmd'; arguments = [string[]]@('--check');
        risk = 'safe'; replayable = $true; description = 'Read-only legacy source check'
    }
    'sources.sync_legacy' = [ordered]@{
        wrapper = 'scripts\sources\clone_legacy_compat_repos.cmd'; arguments = [string[]]@();
        risk = 'write'; replayable = $true; description = 'Synchronize legacy research sources'
    }
    'environment.configure_preflight' = [ordered]@{
        wrapper = 'CONFIGURE_WINDOWS_ENV.cmd'; arguments = [string[]]@('/PREFLIGHT');
        risk = 'safe'; replayable = $true; description = 'Read-only environment configuration preview'
    }
    'environment.configure' = [ordered]@{
        wrapper = 'CONFIGURE_WINDOWS_ENV.cmd'; arguments = [string[]]@();
        risk = 'write'; replayable = $true; description = 'Configure user environment and PATH'
    }
    'environment.verify' = [ordered]@{
        wrapper = 'scripts\compat\verify_stock_environment.cmd'; arguments = [string[]]@();
        risk = 'safe'; replayable = $true; description = 'Verify the managed environment'
    }
    'paths.repair_check' = [ordered]@{
        wrapper = 'scripts\compat\repair_legacy_paths.cmd'; arguments = [string[]]@('--check');
        risk = 'safe'; replayable = $true; description = 'Read-only path repair check'
    }
    'paths.repair_apply' = [ordered]@{
        wrapper = 'scripts\compat\repair_legacy_paths.cmd'; arguments = [string[]]@('--apply', '--confirm');
        risk = 'high'; replayable = $true; description = 'Create approved compatibility junctions'
    }
    'terminal.open' = [ordered]@{
        wrapper = 'OPEN_STOCK_TERMINAL.cmd'; arguments = [string[]]@();
        risk = 'interactive'; replayable = $false; description = 'Open a managed terminal (never replayed)'
    }
}

$script:WorkflowDefinitions = [ordered]@{
    'verify-only' = [string[]]@('environment.verify')
    'safe-setup' = [string[]]@('environment.preflight', 'sources.check_primary', 'environment.verify')
    'full-install' = [string[]]@('environment.install_full', 'environment.verify')
}

$script:SensitiveKeyPattern = '(?i)(token|secret|password|api[_-]?key|authorization|credential|\.env|user.?path|path)'

function Get-ObjectPropertyNames {
    param([AllowNull()]$Value)
    if ($null -eq $Value) { return @() }
    return @($Value.PSObject.Properties | ForEach-Object { $_.Name })
}

function Assert-AllowedProperties {
    param(
        [Parameter(Mandatory = $true)]$Object,
        [Parameter(Mandatory = $true)][string[]]$Allowed,
        [Parameter(Mandatory = $true)][string]$Context
    )
    foreach ($name in (Get-ObjectPropertyNames -Value $Object)) {
        if ($Allowed -notcontains $name) {
            throw "$Context contains unknown field '$name'."
        }
    }
}

function Normalize-RepoUrl {
    param([AllowNull()][string]$Url)
    if ([string]::IsNullOrWhiteSpace($Url)) { return '' }
    $value = $Url.Trim().TrimEnd('/')
    if ($value.EndsWith('.git', [StringComparison]::OrdinalIgnoreCase)) {
        $value = $value.Substring(0, $value.Length - 4)
    }
    return $value.ToLowerInvariant()
}

function Resolve-ActionRoot {
    if ($TestMode) {
        if ($env:STOCK_ACTIONS_TEST_MODE -ne '1') {
            throw '-TestMode is restricted to the self-test process.'
        }
        if ([string]::IsNullOrWhiteSpace($RootOverride)) {
            throw '-TestMode requires -RootOverride.'
        }
        return [IO.Path]::GetFullPath($RootOverride)
    }
    if (-not [string]::IsNullOrWhiteSpace($RootOverride)) {
        throw '-RootOverride is only available in self-test mode.'
    }
    return [IO.Path]::GetFullPath($script:ExpectedRoot)
}

function Test-OrdinaryGitExecutable {
    param([AllowNull()][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) { return $null }
    try {
        if (-not [IO.Path]::IsPathRooted($Path)) { return $null }
        $full = [IO.Path]::GetFullPath($Path)
        $leaf = Get-Item -LiteralPath $full -Force -ErrorAction Stop
        if (-not ($leaf -is [System.IO.FileInfo]) -or $leaf.PSIsContainer -or
            (($leaf.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
            return $null
        }
        $extension = ([string]$leaf.Extension).ToLowerInvariant()
        if (@('.exe', '.cmd', '.bat') -notcontains $extension) { return $null }

        $cursor = $leaf.FullName
        while ($true) {
            $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return $null }
            $parent = [IO.Directory]::GetParent($cursor)
            if ($null -eq $parent) {
                $pathRoot = [IO.Path]::GetPathRoot($cursor)
                if ($null -eq $pathRoot -or $cursor -ine $pathRoot) { return $null }
                break
            }
            if ($parent.FullName -ieq $cursor) { return $null }
            $cursor = $parent.FullName
        }
        return [IO.Path]::GetFullPath($leaf.FullName)
    }
    catch { return $null }
}

function Test-OrdinaryComSpec {
    param([AllowNull()][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) { return $null }
    try {
        if (-not [IO.Path]::IsPathRooted($Path)) { return $null }
        $full = [IO.Path]::GetFullPath($Path)
        $leaf = Get-Item -LiteralPath $full -Force -ErrorAction Stop
        if (-not ($leaf -is [System.IO.FileInfo]) -or $leaf.PSIsContainer -or
            $leaf.Name -ine 'cmd.exe' -or
            (($leaf.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
            return $null
        }

        $cursor = $leaf.FullName
        while ($true) {
            $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return $null }
            $parent = [IO.Directory]::GetParent($cursor)
            if ($null -eq $parent) {
                $pathRoot = [IO.Path]::GetPathRoot($cursor)
                if ($null -eq $pathRoot -or $cursor -ine $pathRoot) { return $null }
                break
            }
            if ($parent.FullName -ieq $cursor) { return $null }
            $cursor = $parent.FullName
        }
        return [IO.Path]::GetFullPath($leaf.FullName)
    }
    catch { return $null }
}

function Resolve-VerifiedComSpec {
    $systemRoot = [Environment]::GetEnvironmentVariable('SystemRoot', 'Process')
    if ([string]::IsNullOrWhiteSpace($systemRoot)) {
        throw 'SystemRoot is unavailable; refusing to resolve a command processor.'
    }
    $candidate = Join-Path $systemRoot 'System32\cmd.exe'
    $verified = Test-OrdinaryComSpec -Path $candidate
    if (-not $verified) {
        throw 'The fixed Windows command processor is missing or physically unsafe.'
    }
    return $verified
}

function Assert-ApprovedWrapperPath {
    param(
        [Parameter(Mandatory = $true)][string]$RootPath,
        [Parameter(Mandatory = $true)][string]$Path
    )

    try {
        $rootFull = [IO.Path]::GetFullPath([string]$RootPath).TrimEnd('\')
        $full = [IO.Path]::GetFullPath([string]$Path)
        if (-not ($full -ieq $rootFull -or
                $full.StartsWith($rootFull + '\', [StringComparison]::OrdinalIgnoreCase))) {
            throw 'outside the approved fixed root'
        }
        $leaf = Get-Item -LiteralPath $full -Force -ErrorAction Stop
        if (-not ($leaf -is [System.IO.FileInfo]) -or $leaf.PSIsContainer -or
            (($leaf.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
            throw 'not an ordinary non-reparse file'
        }
        $cursor = [IO.Path]::GetFullPath([string]$leaf.FullName)
        while ($true) {
            $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'a wrapper ancestor is a reparse point'
            }
            $parent = [IO.Directory]::GetParent($cursor)
            if ($null -eq $parent) {
                $pathRoot = [IO.Path]::GetPathRoot($cursor)
                if ($null -eq $pathRoot -or $cursor -ine $pathRoot) { throw 'wrapper path has no stable volume root' }
                break
            }
            if ($parent.FullName -ieq $cursor) { throw 'wrapper ancestor traversal did not progress' }
            $cursor = $parent.FullName
        }
        return [IO.Path]::GetFullPath([string]$leaf.FullName)
    }
    catch {
        throw "Approved wrapper physical safety check failed: $Path"
    }
}

function Resolve-VerifiedGitExecutable {
    $candidates = New-Object 'System.Collections.Generic.List[string]'
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if (-not [string]::IsNullOrWhiteSpace([string]$base)) {
            [void]$candidates.Add((Join-Path $base 'Git\cmd\git.exe'))
        }
    }
    if (-not [string]::IsNullOrWhiteSpace([string]$env:LOCALAPPDATA)) {
        [void]$candidates.Add((Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd\git.exe'))
    }

    foreach ($candidate in $candidates) {
        $verified = Test-OrdinaryGitExecutable -Path ([string]$candidate)
        if ($verified) { return $verified }
    }

    # Get-Command may expose an Alias/Function/ApplicationInfo.  Only an
    # Application with a validated ordinary absolute path is executable here.
    foreach ($command in @(Get-Command git.exe -All -ErrorAction SilentlyContinue)) {
        if ($null -eq $command -or [string]$command.CommandType -ne 'Application') { continue }
        $source = [string]$command.Source
        if ([string]::IsNullOrWhiteSpace($source)) { $source = [string]$command.Path }
        $verified = Test-OrdinaryGitExecutable -Path $source
        if ($verified) { return $verified }
    }
    throw 'git.exe is required for the policy precondition, but no safe ordinary executable was found.'
}

function Assert-PolicyRoot {
    param([Parameter(Mandatory = $true)][string]$RootPath)

    $rootItem = Get-Item -LiteralPath $RootPath -Force -ErrorAction Stop
    if (-not $rootItem.PSIsContainer -or
        (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw "Project root is not an ordinary directory: $RootPath"
    }
    foreach ($marker in @('STOCK_SETUP_MANAGER.cmd', 'scripts\setup\install_d_stock_env.ps1')) {
        if (-not (Test-Path -LiteralPath (Join-Path $RootPath $marker) -PathType Leaf)) {
            throw "Project marker is missing: $marker"
        }
    }
    return [pscustomobject]@{
        root = $RootPath
        origin = $null
        git = $null
        valid = $true
    }
}

function Convert-ToSafeLogValue {
    param(
        [AllowNull()]$Value,
        [string]$Key = ''
    )
    if ($Key -match $script:SensitiveKeyPattern) { return '[REDACTED]' }
    if ($null -eq $Value) { return $null }
    if ($Value -is [string]) {
        $text = [string]$Value
        if ($text -match '(?i)\.env|api[_-]?key|bearer\s+') { return '[REDACTED]' }
        return $text
    }
    if (($Value -is [bool]) -or ($Value -is [byte]) -or ($Value -is [int16]) -or
        ($Value -is [int32]) -or ($Value -is [int64]) -or ($Value -is [decimal]) -or
        ($Value -is [double])) { return $Value }
    if ($Value -is [pscustomobject]) {
        $customObjectResult = [ordered]@{}
        foreach ($name in (Get-ObjectPropertyNames -Value $Value)) {
            $customObjectResult[$name] = Convert-ToSafeLogValue -Value $Value.$name -Key $name
        }
        return $customObjectResult
    }
    if ($Value -is [System.Collections.IDictionary]) {
        $result = [ordered]@{}
        foreach ($entry in $Value.GetEnumerator()) {
            $result[[string]$entry.Key] = Convert-ToSafeLogValue -Value $entry.Value -Key ([string]$entry.Key)
        }
        return $result
    }
    if ($Value -is [System.Collections.IEnumerable] -and -not ($Value -is [string])) {
        $items = @()
        foreach ($item in $Value) { $items += ,(Convert-ToSafeLogValue -Value $item) }
        return $items
    }
    $properties = @(Get-ObjectPropertyNames -Value $Value)
    if ($properties.Count -gt 0) {
        $objectResult = [ordered]@{}
        foreach ($name in $properties) {
            $objectResult[$name] = Convert-ToSafeLogValue -Value $Value.$name -Key $name
        }
        return $objectResult
    }
    return [string]$Value
}

function Get-ByteArrayHash {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)
    $sha = New-Object System.Security.Cryptography.SHA256Managed
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally { $sha.Dispose() }
}

function Assert-RegularDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) {
        New-Item -ItemType Directory -Path $Path | Out-Null
        $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    }
    if (-not $item.PSIsContainer -or (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw "Session path is not a regular directory: $Path"
    }
    return $item
}

function Get-SessionPointerState {
    param(
        [Parameter(Mandatory = $true)][string]$PointerPath,
        [Parameter(Mandatory = $true)][string]$SessionDirectory
    )
    $item = Get-Item -LiteralPath $PointerPath -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) {
        return [pscustomobject]@{ Exists = $false; Bytes = $null; Hash = $null; Record = $null }
    }
    if ($item.PSIsContainer -or (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw 'last-session.json must be a regular file.'
    }
    try {
        $bytes = [IO.File]::ReadAllBytes($PointerPath)
        $text = [Text.Encoding]::UTF8.GetString($bytes).TrimStart([char]0xFEFF)
        $record = $text | ConvertFrom-Json -ErrorAction Stop
    }
    catch { throw 'last-session.json is not valid JSON.' }
    if ($null -eq $record -or $record -is [string] -or
        ($record -is [System.Collections.IEnumerable] -and -not ($record -is [pscustomobject]))) {
        throw 'last-session.json must contain a JSON object.'
    }
    Assert-AllowedProperties -Object $record -Allowed @('schema_version', 'session_id', 'session_file', 'updated_utc') -Context 'last-session pointer'
    foreach ($required in @('schema_version', 'session_id', 'session_file', 'updated_utc')) {
        if ($null -eq $record.PSObject.Properties[$required]) {
            throw "last-session pointer is missing '$required'."
        }
    }
    foreach ($required in @('schema_version', 'session_id', 'session_file', 'updated_utc')) {
        if ($record.$required -isnot [string]) { throw "last-session pointer '$required' must be a string." }
    }
    if ([string]$record.schema_version -ne $script:SchemaVersion) {
        throw 'last-session pointer schema_version is not supported.'
    }
    $sessionId = [string]$record.session_id
    if ($sessionId -notmatch '^[0-9a-fA-F]{32}$') {
        throw 'last-session pointer session_id is invalid.'
    }
    $sessionFile = [string]$record.session_file
    $fileMatch = [regex]::Match($sessionFile, '^([0-9]{8}T[0-9]{6}Z)-([0-9a-fA-F]{32})\.jsonl$')
    if (-not $fileMatch.Success -or $fileMatch.Groups[2].Value -ine $sessionId) {
        throw 'last-session pointer session_file is invalid.'
    }
    $updated = [string]$record.updated_utc
    try { [DateTime]::Parse($updated) | Out-Null }
    catch { throw 'last-session pointer updated_utc is invalid.' }
    $sessionDirectoryFull = [IO.Path]::GetFullPath($SessionDirectory).TrimEnd('\')
    $targetPath = [IO.Path]::GetFullPath((Join-Path $sessionDirectoryFull $sessionFile))
    $prefix = $sessionDirectoryFull + '\'
    if (-not $targetPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'last-session pointer target is outside logs\sessions.'
    }
    $target = Get-Item -LiteralPath $targetPath -Force -ErrorAction SilentlyContinue
    if ($null -ne $target -and ($target.PSIsContainer -or (($target.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0))) {
        throw 'last-session pointer target is not a regular JSONL file.'
    }
    return [pscustomobject]@{
        Exists = $true
        Bytes = $bytes
        Hash = Get-ByteArrayHash -Bytes $bytes
        Record = $record
    }
}

function Test-ByteArraysEqual {
    param([AllowNull()][byte[]]$Left, [AllowNull()][byte[]]$Right)
    if ($null -eq $Left -or $null -eq $Right) { return ($null -eq $Left -and $null -eq $Right) }
    if ($Left.Length -ne $Right.Length) { return $false }
    for ($i = 0; $i -lt $Left.Length; $i++) { if ($Left[$i] -ne $Right[$i]) { return $false } }
    return $true
}

function Write-SessionPointerAtomic {
    param(
        [Parameter(Mandatory = $true)][string]$PointerPath,
        [Parameter(Mandatory = $true)][string]$SessionDirectory,
        [Parameter(Mandatory = $true)]$Pointer,
        [Parameter(Mandatory = $true)]$ExistingState
    )
    $tempPath = Join-Path $SessionDirectory ('.last-session.json.tmp.' + [Guid]::NewGuid().ToString('N'))
    if (Test-Path -LiteralPath $tempPath) { throw 'Pointer temporary path already exists.' }
    $utf8 = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false
    $backupPath = $null
    try {
        [IO.File]::WriteAllText($tempPath, (($Pointer | ConvertTo-Json -Compress -Depth 4) + [Environment]::NewLine), $utf8)
        if ($ExistingState.Exists) {
            $currentBytes = [IO.File]::ReadAllBytes($PointerPath)
            if (-not (Test-ByteArraysEqual -Left $ExistingState.Bytes -Right $currentBytes)) {
                throw 'last-session.json changed while preparing its atomic update.'
            }
            $backupPath = Join-Path $SessionDirectory ('.last-session.json.bak.' + [Guid]::NewGuid().ToString('N'))
            if (Test-Path -LiteralPath $backupPath) { throw 'Pointer backup path already exists.' }
            [IO.File]::Replace($tempPath, $PointerPath, $backupPath)
        }
        else {
            if (Test-Path -LiteralPath $PointerPath) { throw 'last-session.json appeared while preparing its creation.' }
            [IO.File]::Move($tempPath, $PointerPath)
        }
    }
    finally {
        if (Test-Path -LiteralPath $tempPath -PathType Leaf) {
            Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
        }
        if ($backupPath -and (Test-Path -LiteralPath $backupPath -PathType Leaf)) {
            Remove-Item -LiteralPath $backupPath -Force -ErrorAction SilentlyContinue
        }
    }
}

function New-SessionLog {
    if (-not $script:Root) { throw 'Action root was not initialized.' }
    $logsDirectory = Join-Path $script:Root 'logs'
    $sessionDirectory = Join-Path $logsDirectory 'sessions'
    Assert-RegularDirectory -Path $logsDirectory | Out-Null
    Assert-RegularDirectory -Path $sessionDirectory | Out-Null
    $pointerPath = Join-Path $sessionDirectory 'last-session.json'
    $pointerState = Get-SessionPointerState -PointerPath $pointerPath -SessionDirectory $sessionDirectory

    $script:SessionId = [Guid]::NewGuid().ToString('N')
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $script:SessionLogPath = Join-Path $sessionDirectory ("{0}-{1}.jsonl" -f $stamp, $script:SessionId)
    if (Test-Path -LiteralPath $script:SessionLogPath) { throw 'Generated session filename already exists.' }
    $pointer = [ordered]@{
        schema_version = $script:SchemaVersion
        session_id = $script:SessionId
        session_file = (Split-Path -Leaf $script:SessionLogPath)
        updated_utc = (Get-Date).ToUniversalTime().ToString('o')
    }
    $stream = [IO.File]::Open($script:SessionLogPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    $stream.Dispose()
    try {
        Write-SessionPointerAtomic -PointerPath $pointerPath -SessionDirectory $sessionDirectory -Pointer $pointer -ExistingState $pointerState
    }
    catch {
        Remove-Item -LiteralPath $script:SessionLogPath -Force -ErrorAction SilentlyContinue
        throw
    }
}

function Write-SessionEvent {
    param(
        [Parameter(Mandatory = $true)][string]$ActionName,
        [Parameter(Mandatory = $true)][string]$WorkflowName,
        [Parameter(Mandatory = $true)][string]$StartedUtc,
        [Parameter(Mandatory = $true)][string]$FinishedUtc,
        [Parameter(Mandatory = $true)][int]$ExitCode,
        [Parameter(Mandatory = $true)][string]$Status,
        [AllowNull()]$Parameters,
        [AllowNull()]$Precondition,
        [AllowNull()]$Evidence
    )
    if (-not $script:SessionLogPath) { throw 'Session log was not initialized.' }
    $script:SessionSequence++
    $record = [ordered]@{
        schema_version = $script:SchemaVersion
        session_id = $script:SessionId
        sequence = $script:SessionSequence
        event = 'action'
        action = $ActionName
        workflow = $WorkflowName
        started_utc = $StartedUtc
        finished_utc = $FinishedUtc
        parameters = Convert-ToSafeLogValue -Value $Parameters
        precondition = Convert-ToSafeLogValue -Value $Precondition
        exit_code = $ExitCode
        status = $Status
        evidence = Convert-ToSafeLogValue -Value $Evidence
    }
    $utf8 = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false
    $writer = New-Object -TypeName System.IO.StreamWriter -ArgumentList @($script:SessionLogPath, $true, $utf8)
    try {
        $writer.WriteLine(($record | ConvertTo-Json -Compress -Depth 10))
    }
    finally {
        $writer.Dispose()
    }
}

function Assert-Parameters {
    param([AllowNull()]$Parameters)
    if ($null -eq $Parameters) { return [pscustomobject]@{} }
    if ($Parameters -is [string] -or $Parameters -is [System.Collections.IEnumerable] -and
        -not ($Parameters -is [pscustomobject])) {
        throw 'parameters must be a JSON object.'
    }
    Assert-AllowedProperties -Object $Parameters -Allowed @('allow_repair_apply') -Context 'parameters'
    $allowed = [ordered]@{}
    if ($null -ne $Parameters.PSObject.Properties['allow_repair_apply']) {
        if ($Parameters.allow_repair_apply -isnot [bool]) {
            throw 'parameters.allow_repair_apply must be a boolean.'
        }
        $allowed['allow_repair_apply'] = [bool]$Parameters.allow_repair_apply
    }
    return [pscustomobject]$allowed
}

function Assert-ActionName {
    param([Parameter(Mandatory = $true)][string]$Name)
    if (-not $script:ActionDefinitions.Contains($Name)) {
        throw "Unknown semantic action '$Name'."
    }
}

function Assert-RequestObject {
    param([Parameter(Mandatory = $true)]$RequestObject)
    Assert-AllowedProperties -Object $RequestObject -Allowed @('schema_version', 'action', 'workflow', 'parameters') -Context 'request'
    if ([string]$RequestObject.schema_version -ne $script:SchemaVersion) {
        throw "Unsupported schema_version '$($RequestObject.schema_version)'."
    }
    $hasAction = ($null -ne $RequestObject.PSObject.Properties['action']) -and
        -not [string]::IsNullOrWhiteSpace([string]$RequestObject.action)
    $hasWorkflow = ($null -ne $RequestObject.PSObject.Properties['workflow']) -and
        -not [string]::IsNullOrWhiteSpace([string]$RequestObject.workflow)
    if ($hasAction -eq $hasWorkflow) { throw 'A request must contain exactly one action or workflow.' }
    $rawParameters = $null
    if ($null -ne $RequestObject.PSObject.Properties['parameters']) { $rawParameters = $RequestObject.parameters }
    $parameters = Assert-Parameters -Parameters $rawParameters
    if ($hasAction) {
        Assert-ActionName -Name ([string]$RequestObject.action)
        return [pscustomobject]@{ kind = 'action'; name = [string]$RequestObject.action; parameters = $parameters }
    }
    if (-not $script:WorkflowDefinitions.Contains([string]$RequestObject.workflow)) {
        throw "Unknown semantic workflow '$($RequestObject.workflow)'."
    }
    return [pscustomobject]@{ kind = 'workflow'; name = [string]$RequestObject.workflow; parameters = $parameters }
}

function Read-RequestFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Request file was not found: $Path" }
    try {
        $json = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop
        $object = $json | ConvertFrom-Json -ErrorAction Stop
    }
    catch { throw "Request JSON could not be parsed: $($_.Exception.Message)" }
    return Assert-RequestObject -RequestObject $object
}

function Get-WorkflowActions {
    param([Parameter(Mandatory = $true)][string]$Name)
    if (-not $script:WorkflowDefinitions.Contains($Name)) { throw "Unknown semantic workflow '$Name'." }
    return @($script:WorkflowDefinitions[$Name])
}

function Invoke-SemanticAction {
    param(
        [Parameter(Mandatory = $true)][string]$ActionName,
        [Parameter(Mandatory = $true)][string]$WorkflowName,
        [AllowNull()]$Parameters
    )
    Assert-ActionName -Name $ActionName
    $definition = $script:ActionDefinitions[$ActionName]
    if ($definition.risk -eq 'high' -and -not $script:AllowRepairApplyMode) {
        throw "High-risk action '$ActionName' requires --allow-repair-apply."
    }
    if (-not $definition.replayable -and $WorkflowName -like 'replay*') {
        throw "Action '$ActionName' is not replayable."
    }

    $policy = Assert-PolicyRoot -RootPath $script:Root
    $started = (Get-Date).ToUniversalTime().ToString('o')
    $exitCode = 0
    $status = 'success'
    $evidence = [ordered]@{
        wrapper = $definition.wrapper
        mode = $(if ($script:DryRunMode) { 'dry-run' } else { 'execute' })
        policy_root = $policy.root
        origin_validated = $true
    }
    try {
        $scriptPath = Join-Path $script:Root $definition.wrapper
        $scriptPath = Assert-ApprovedWrapperPath -RootPath $policy.root -Path $scriptPath
        if ($script:DryRunMode) {
            $status = 'dry_run'
            $evidence['executed'] = $false
        }
        else {
            $oldNoPause = $env:STOCK_TOOLKIT_NO_PAUSE
            $oldNoPause2 = $env:NO_PAUSE
            try {
                $env:STOCK_TOOLKIT_NO_PAUSE = '1'
                $env:NO_PAUSE = '1'
                $cmdArgs = New-Object 'System.Collections.Generic.List[string]'
                $cmdArgs.Add('/d')
                $cmdArgs.Add('/c')
                $cmdArgs.Add('call')
                $cmdArgs.Add(('"{0}"' -f $scriptPath))
                foreach ($argument in $definition.arguments) { $cmdArgs.Add($argument) }
                $trustedCmd = Resolve-VerifiedComSpec
                $scriptPath = Assert-ApprovedWrapperPath -RootPath $policy.root -Path $scriptPath
                & $trustedCmd $cmdArgs.ToArray()
                $exitCode = [int]$LASTEXITCODE
            }
            finally {
                $env:STOCK_TOOLKIT_NO_PAUSE = $oldNoPause
                $env:NO_PAUSE = $oldNoPause2
            }
            if ($exitCode -ne 0) { $status = 'failed' }
            $evidence['executed'] = $true
        }
    }
    catch {
        $exitCode = 1
        $status = 'failed'
        $evidence['executed'] = $false
        $evidence['error'] = $_.Exception.Message
    }
    $finished = (Get-Date).ToUniversalTime().ToString('o')
    Write-SessionEvent -ActionName $ActionName -WorkflowName $WorkflowName -StartedUtc $started -FinishedUtc $finished `
        -ExitCode $exitCode -Status $status -Parameters $Parameters `
        -Precondition ([ordered]@{ root = $policy.root; origin = $policy.origin; validated = $true }) -Evidence $evidence
    if ($status -eq 'failed') { throw "Action '$ActionName' failed with exit code $exitCode." }
}

function Assert-ReplayRecord {
    param(
        [Parameter(Mandatory = $true)]$Record,
        [Parameter(Mandatory = $true)][int]$ExpectedSequence,
        [Parameter(Mandatory = $true)][string]$SessionId
    )
    Assert-AllowedProperties -Object $Record -Allowed @(
        'schema_version', 'session_id', 'sequence', 'event', 'action', 'workflow',
        'started_utc', 'finished_utc', 'parameters', 'precondition', 'exit_code',
        'status', 'evidence'
    ) -Context 'replay event'
    if ([string]$Record.schema_version -ne $script:SchemaVersion) { throw 'Replay schema version is not supported.' }
    if ([string]$Record.session_id -ne $SessionId) { throw 'Replay session_id is inconsistent.' }
    if ([int]$Record.sequence -ne $ExpectedSequence) { throw 'Replay sequence is not contiguous.' }
    if ([string]$Record.event -ne 'action') { throw 'Replay contains a non-action event.' }
    Assert-ActionName -Name ([string]$Record.action)
    if ([string]$Record.status -ne 'success' -or [int]$Record.exit_code -ne 0) {
        throw "Replay stopped at non-success action '$($Record.action)'."
    }
    $rawParameters = $null
    if ($null -ne $Record.PSObject.Properties['parameters']) { $rawParameters = $Record.parameters }
    $parameters = Assert-Parameters -Parameters $rawParameters
    return [pscustomobject]@{ action = [string]$Record.action; workflow = [string]$Record.workflow; parameters = $parameters }
}

function Get-ReplayRecords {
    param([Parameter(Mandatory = $true)][string]$ReplayPath)
    $sessionDirectory = Join-Path $script:Root 'logs\sessions'
    $fullReplay = [IO.Path]::GetFullPath($ReplayPath)
    $sessionPrefix = $sessionDirectory.TrimEnd('\') + '\'
    if (-not $fullReplay.StartsWith($sessionPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Replay file must be under logs\sessions.'
    }
    if (-not (Test-Path -LiteralPath $fullReplay -PathType Leaf)) { throw "Replay file was not found: $fullReplay" }
    $lines = [IO.File]::ReadAllLines($fullReplay)
    if ($lines.Count -eq 0) { throw 'Replay file is empty.' }
    $records = @()
    $sessionId = $null
    $sequence = 0
    foreach ($line in $lines) {
        if ([string]::IsNullOrWhiteSpace($line)) { throw 'Replay contains a blank JSONL line.' }
        $text = $line.Trim().TrimStart([char]0xFEFF)
        try { $record = $text | ConvertFrom-Json -ErrorAction Stop }
        catch { throw "Replay JSONL parse failed: $($_.Exception.Message)" }
        if (-not $sessionId) { $sessionId = [string]$record.session_id }
        $sequence++
        $records += ,(Assert-ReplayRecord -Record $record -ExpectedSequence $sequence -SessionId $sessionId)
    }
    return $records
}

function Resolve-ReplayLast {
    $pointerPath = Join-Path (Join-Path $script:Root 'logs\sessions') 'last-session.json'
    if (-not (Test-Path -LiteralPath $pointerPath -PathType Leaf)) { throw 'last-session pointer was not found.' }
    try { $pointer = (Get-Content -LiteralPath $pointerPath -Raw | ConvertFrom-Json -ErrorAction Stop) }
    catch { throw 'last-session pointer is not valid JSON.' }
    Assert-AllowedProperties -Object $pointer -Allowed @('schema_version', 'session_id', 'session_file', 'updated_utc') -Context 'last-session pointer'
    if ([string]$pointer.schema_version -ne $script:SchemaVersion) { throw 'last-session pointer version is not supported.' }
    if ([string]$pointer.session_file -notmatch '^[0-9]{8}T[0-9]{6}Z-[0-9a-fA-F]{32}\.jsonl$') {
        throw 'last-session pointer contains an invalid session filename.'
    }
    return Join-Path (Join-Path $script:Root 'logs\sessions') ([string]$pointer.session_file)
}

function Show-List {
    Write-Host 'Semantic actions (allow-list):'
    foreach ($name in $script:ActionDefinitions.Keys) {
        $definition = $script:ActionDefinitions[$name]
        Write-Host ("  {0} [{1}] replayable={2} - {3}" -f $name, $definition.risk, $definition.replayable, $definition.description)
    }
    Write-Host ''
    Write-Host 'Semantic workflows:'
    foreach ($name in $script:WorkflowDefinitions.Keys) {
        Write-Host ("  {0}: {1}" -f $name, ($script:WorkflowDefinitions[$name] -join ', '))
    }
}

function Parse-CliArgs {
    param([string[]]$Tokens)
    if ($Tokens.Count -eq 1 -and $Tokens[0] -match '\s') {
        $expanded = @()
        $matches = [regex]::Matches($Tokens[0], '(?:"([^"]*)"|(\S+))')
        foreach ($match in $matches) {
            if ($match.Groups[1].Success) { $expanded += $match.Groups[1].Value }
            else { $expanded += $match.Groups[2].Value }
        }
        if ($expanded.Count -gt 0) { $Tokens = [string[]]$expanded }
    }
    $parsed = [ordered]@{ list = $false; workflow = $null; action = $null; dry_run = $false;
        replay = $null; replay_last = $false; request = $null; allow_repair_apply = $false }
    $index = 0
    while ($index -lt $Tokens.Count) {
        $token = [string]$Tokens[$index]
        switch ($token.ToLowerInvariant()) {
            '--list' { $parsed.list = $true }
            '-list' { $parsed.list = $true }
            '--dry-run' { $parsed.dry_run = $true }
            '-dryrun' { $parsed.dry_run = $true }
            '--replay-last' { $parsed.replay_last = $true }
            '-replaylast' { $parsed.replay_last = $true }
            '--allow-repair-apply' { $parsed.allow_repair_apply = $true }
            '-allowrepairapply' { $parsed.allow_repair_apply = $true }
            '--workflow' {
                $index++; if ($index -ge $Tokens.Count) { throw '--workflow requires a name.' }; $parsed.workflow = [string]$Tokens[$index]
            }
            '-workflow' {
                $index++; if ($index -ge $Tokens.Count) { throw '-Workflow requires a name.' }; $parsed.workflow = [string]$Tokens[$index]
            }
            '--action' {
                $index++; if ($index -ge $Tokens.Count) { throw '--action requires a name.' }; $parsed.action = [string]$Tokens[$index]
            }
            '-action' {
                $index++; if ($index -ge $Tokens.Count) { throw '-Action requires a name.' }; $parsed.action = [string]$Tokens[$index]
            }
            '--replay' {
                $index++; if ($index -ge $Tokens.Count) { throw '--replay requires a session file.' }; $parsed.replay = [string]$Tokens[$index]
            }
            '-replay' {
                $index++; if ($index -ge $Tokens.Count) { throw '-Replay requires a session file.' }; $parsed.replay = [string]$Tokens[$index]
            }
            '--request' {
                $index++; if ($index -ge $Tokens.Count) { throw '--request requires a JSON file.' }; $parsed.request = [string]$Tokens[$index]
            }
            '-request' {
                $index++; if ($index -ge $Tokens.Count) { throw '-Request requires a JSON file.' }; $parsed.request = [string]$Tokens[$index]
            }
            default { throw "Unknown option '$token'." }
        }
        $index++
    }
    return [pscustomobject]$parsed
}

function Get-ExecutionRequest {
    param(
        [Parameter(Mandatory = $true)][bool]$ListMode,
        [AllowNull()][string]$WorkflowName,
        [AllowNull()][string]$ActionName,
        [AllowNull()][string]$RequestPath
    )
    if ($ListMode) { return $null }
    $count = 0
    if (-not [string]::IsNullOrWhiteSpace($WorkflowName)) { $count++ }
    if (-not [string]::IsNullOrWhiteSpace($ActionName)) { $count++ }
    if (-not [string]::IsNullOrWhiteSpace($RequestPath)) { $count++ }
    if ($count -ne 1) { throw 'Specify exactly one of --workflow, --action or --request.' }
    if ($RequestPath) { return Read-RequestFile -Path $RequestPath }
    if ($WorkflowName) {
        if (-not $script:WorkflowDefinitions.Contains($WorkflowName)) { throw "Unknown semantic workflow '$WorkflowName'." }
        return [pscustomobject]@{ kind = 'workflow'; name = $WorkflowName; parameters = [pscustomobject]@{} }
    }
    Assert-ActionName -Name $ActionName
    return [pscustomobject]@{ kind = 'action'; name = $ActionName; parameters = [pscustomobject]@{} }
}

function Invoke-Interactive {
    Write-Host 'AI stock semantic actions'
    Write-Host '[1] verify-only (read-only)'
    Write-Host '[2] safe-setup (preflight, source check, verify)'
    Write-Host '[3] full-install (environment and verify)'
    Write-Host '[4] list actions'
    Write-Host '[Q] exit'
    $choice = (Read-Host 'Select').Trim()
    switch ($choice.ToUpperInvariant()) {
        '1' { return [pscustomobject]@{ workflow = 'verify-only' } }
        '2' { return [pscustomobject]@{ workflow = 'safe-setup' } }
        '3' { return [pscustomobject]@{ workflow = 'full-install' } }
        '4' { Show-List; return $null }
        default { return $null }
    }
}

function Invoke-Main {
    try {
        $effectiveList = [bool]$List
        $effectiveWorkflow = $Workflow
        $effectiveAction = $Action
        $effectiveDryRun = [bool]$DryRun
        $effectiveReplay = $Replay
        $effectiveReplayLast = [bool]$ReplayLast
        $effectiveRequest = $Request
        $effectiveAllow = [bool]$AllowRepairApply

        $rawCliArgs = $CliArgs
        if ($EnvCli) {
            if ([string]::IsNullOrWhiteSpace($env:STOCK_ACTION_CLI)) { throw '-EnvCli requires STOCK_ACTION_CLI.' }
            $rawCliArgs = [string[]]@($env:STOCK_ACTION_CLI)
        }
        if ($rawCliArgs -and $rawCliArgs.Count -gt 0) {
            $parsed = Parse-CliArgs -Tokens $rawCliArgs
            if ($List -or $Workflow -or $Action -or $DryRun -or $Replay -or $ReplayLast -or $Request -or $AllowRepairApply) {
                throw 'Do not mix CLI option text with named action options.'
            }
            $effectiveList = [bool]$parsed.list
            $effectiveWorkflow = $parsed.workflow
            $effectiveAction = $parsed.action
            $effectiveDryRun = [bool]$parsed.dry_run
            $effectiveReplay = $parsed.replay
            $effectiveReplayLast = [bool]$parsed.replay_last
            $effectiveRequest = $parsed.request
            $effectiveAllow = [bool]$parsed.allow_repair_apply
        }
        if ($Interactive) {
            if ($effectiveList -or $effectiveWorkflow -or $effectiveAction -or $effectiveReplay -or $effectiveReplayLast -or $effectiveRequest) {
                throw '-Interactive cannot be combined with another request.'
            }
            $interactiveRequest = Invoke-Interactive
            if ($null -eq $interactiveRequest) { return 0 }
            $effectiveWorkflow = $interactiveRequest.workflow
        }
        if ($effectiveList) {
            Show-List
            return 0
        }
        if ($effectiveReplay -and $effectiveReplayLast) { throw 'Use either --replay or --replay-last, not both.' }
        if ($effectiveReplay -and ($effectiveWorkflow -or $effectiveAction -or $effectiveRequest)) { throw 'Replay cannot be combined with action, workflow or request.' }
        if ($effectiveReplayLast -and ($effectiveWorkflow -or $effectiveAction -or $effectiveRequest)) { throw 'Replay-last cannot be combined with action, workflow or request.' }

        $script:DryRunMode = $effectiveDryRun
        $script:AllowRepairApplyMode = $effectiveAllow
        $script:TestModeActive = $TestMode
        $script:Root = Resolve-ActionRoot
        $null = Assert-PolicyRoot -RootPath $script:Root

        if ($effectiveReplayLast) { $effectiveReplay = Resolve-ReplayLast }
        if ($effectiveReplay) {
            $records = @(Get-ReplayRecords -ReplayPath $effectiveReplay)
            New-SessionLog
            foreach ($record in $records) {
                Invoke-SemanticAction -ActionName $record.action -WorkflowName ('replay:' + $record.workflow) -Parameters $record.parameters
            }
            Write-Host ("Replay completed: {0} action(s)." -f $records.Count)
            return 0
        }

        $requestObject = Get-ExecutionRequest -ListMode $false -WorkflowName $effectiveWorkflow -ActionName $effectiveAction -RequestPath $effectiveRequest
        New-SessionLog
        if ($requestObject.kind -eq 'action') {
            if ($requestObject.name -eq 'paths.repair_apply' -and -not $effectiveAllow) {
                throw "High-risk action '$($requestObject.name)' requires --allow-repair-apply."
            }
            Invoke-SemanticAction -ActionName $requestObject.name -WorkflowName 'single-action' -Parameters $requestObject.parameters
        }
        else {
            foreach ($name in (Get-WorkflowActions -Name $requestObject.name)) {
                Invoke-SemanticAction -ActionName $name -WorkflowName $requestObject.name -Parameters $requestObject.parameters
            }
        }
        if ($script:DryRunMode) { Write-Host 'Dry-run completed; approved wrappers were not executed.' }
        else { Write-Host 'Action execution completed.' }
        return 0
    }
    catch {
        Write-Error ("Action runner stopped: " + $_.Exception.Message)
        return 1
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    $mainOutput = @(Invoke-Main)
    if ($mainOutput.Count -eq 0) { exit 0 }
    $mainCode = $mainOutput[$mainOutput.Count - 1]
    if ($mainOutput.Count -gt 1) {
        foreach ($item in $mainOutput[0..($mainOutput.Count - 2)]) { Write-Output $item }
    }
    if ($mainCode -is [int]) { exit $mainCode }
    Write-Output $mainCode
    exit 0
}
