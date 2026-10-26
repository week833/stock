[CmdletBinding()]
param(
    [switch]$VerifyOnly,
    [switch]$Install,
    [switch]$ConfirmInstall,
    [switch]$RestartTelegram,
    [switch]$ConfirmRestart,
    [ValidatePattern('^[0-9a-fA-F]{64}$')][string]$ExpectedInstallerSha256,
    [ValidatePattern('^[0-9a-f]{32}$')][string]$OperationId,
    [ValidatePattern('^[0-9a-f]{64}$')][string]$OperationToken
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Get-SelectedCacheTaskContract {
    return [pscustomobject]@{
        TaskName = 'DStockSingleSelectedCachePreparation'
        TaskPath = '\'
        Namespace = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
        Source = 'dstock-single-selected-cache-task-installer-v1'
        Author = 'D-stock single-stock selected cache preparation'
        Version = '1.0'
        PrincipalId = 'SelectedCachePrincipal'
        BootId = 'SelectedCacheBoot'
        IntervalId = 'SelectedCacheEveryFiveMinutes'
        StartBoundary = '2026-01-01T00:00:00+08:00'
        Installer = 'D:\stock\programs\single_stock_analysis\tools\INSTALL_SELECTED_CACHE_TASK.ps1'
        Python = 'D:\stock\.venv\Scripts\pythonw.exe'
        Worker = 'D:\stock\programs\shared\stock_daily_selected_cache_worker.py'
        Arguments = '-B "D:\stock\programs\shared\stock_daily_selected_cache_worker.py" --once'
        WorkingDirectory = 'D:\stock'
        RestartScript = 'D:\stock\programs\telegram_report_delivery\RESTART_COMMAND_BOT_SERVICE.ps1'
        ReceiptRoot = 'D:\TWStockData\program_data\dstock_single\state\selected_cache_task_installer\receipts'
    }
}

function Get-ManagedTaskSettings {
    return [ordered]@{
        'MultipleInstancesPolicy' = 'IgnoreNew'
        'DisallowStartIfOnBatteries' = 'false'
        'StopIfGoingOnBatteries' = 'false'
        'AllowHardTerminate' = 'false'
        'StartWhenAvailable' = 'true'
        'RunOnlyIfNetworkAvailable' = 'false'
        'IdleSettings/StopOnIdleEnd' = 'false'
        'IdleSettings/RestartOnIdle' = 'false'
        'AllowStartOnDemand' = 'true'
        'Hidden' = 'true'
        'RunOnlyIfIdle' = 'false'
        'WakeToRun' = 'false'
        'ExecutionTimeLimit' = 'PT0S'
    }
}

function ConvertTo-TaskXmlDocument {
    param([Parameter(Mandatory)][string]$Xml)
    $settings = New-Object System.Xml.XmlReaderSettings
    $settings.DtdProcessing = [System.Xml.DtdProcessing]::Prohibit
    $settings.XmlResolver = $null
    $settings.MaxCharactersInDocument = 1048576
    $textReader = New-Object System.IO.StringReader($Xml)
    $reader = [System.Xml.XmlReader]::Create($textReader, $settings)
    try {
        $document = New-Object System.Xml.XmlDocument
        $document.PreserveWhitespace = $true
        $document.XmlResolver = $null
        $document.Load($reader)
        return ,$document
    } finally {
        $reader.Dispose()
        $textReader.Dispose()
    }
}

function Get-TaskXmlNamespaces {
    param([Parameter(Mandatory)][System.Xml.XmlDocument]$Document, [Parameter(Mandatory)]$Contract)
    $manager = New-Object System.Xml.XmlNamespaceManager($Document.NameTable)
    $manager.AddNamespace('t', $Contract.Namespace)
    return ,$manager
}

function Get-TaskXmlNode {
    param([Parameter(Mandatory)][System.Xml.XmlDocument]$Document, [Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Contract)
    $manager = Get-TaskXmlNamespaces -Document $Document -Contract $Contract
    $xpath = '/' + (($Path.Split('/') | ForEach-Object { 't:' + $_ }) -join '/')
    $nodes = $Document.SelectNodes($xpath, $manager)
    if ($nodes.Count -ne 1) { throw "Task definition must contain exactly one $Path." }
    return ,$nodes[0]
}

function Get-TaskXmlValue {
    param([Parameter(Mandatory)][System.Xml.XmlDocument]$Document, [Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Contract)
    return [string](Get-TaskXmlNode -Document $Document -Path $Path -Contract $Contract).InnerText
}

function Set-TaskXmlValue {
    param([Parameter(Mandatory)][System.Xml.XmlDocument]$Document, [Parameter(Mandatory)][string]$Path, [AllowEmptyString()][string]$Value, [Parameter(Mandatory)]$Contract)
    $parts = $Path.Split('/')
    $node = $Document.DocumentElement
    if ($parts[0] -cne 'Task') { throw 'Only Task-rooted XML paths are allowed.' }
    $manager = Get-TaskXmlNamespaces -Document $Document -Contract $Contract
    for ($index = 1; $index -lt $parts.Count; $index++) {
        $children = $node.SelectNodes('t:' + $parts[$index], $manager)
        if ($children.Count -gt 1) { throw "Duplicate task setting: $Path" }
        if ($children.Count -eq 0) {
            $child = $Document.CreateElement($parts[$index], $Contract.Namespace)
            [void]$node.AppendChild($child)
            $node = $child
        } else { $node = $children[0] }
    }
    $node.InnerText = $Value
}

function Assert-TaskXmlChildren {
    param([Parameter(Mandatory)][System.Xml.XmlNode]$Node, [Parameter(Mandatory)][string[]]$Allowed, [Parameter(Mandatory)]$Contract)
    foreach ($child in $Node.ChildNodes) {
        if ($child.NodeType -ne [System.Xml.XmlNodeType]::Element) { continue }
        if ($child.NamespaceURI -cne $Contract.Namespace -or $Allowed -cnotcontains $child.LocalName) {
            throw "Unrecognized task definition element: $($child.Name)"
        }
    }
}

function Assert-OwnedTaskDefinition {
    param([Parameter(Mandatory)][string]$Xml, [Parameter(Mandatory)]$Contract)
    $document = ConvertTo-TaskXmlDocument -Xml $Xml
    $root = $document.DocumentElement
    if ($root.LocalName -cne 'Task' -or $root.NamespaceURI -cne $Contract.Namespace -or $root.GetAttribute('version') -cne '1.2') {
        throw 'Same-name task has an unrecognized definition; refusing to overwrite.'
    }
    Assert-TaskXmlChildren -Node $root -Allowed @('RegistrationInfo','Triggers','Principals','Settings','Actions','Data') -Contract $Contract
    [void](Get-TaskXmlNode -Document $document -Path 'Task/RegistrationInfo' -Contract $Contract)
    $identity = [ordered]@{
        'Task/RegistrationInfo/Source' = $Contract.Source
        'Task/RegistrationInfo/Author' = $Contract.Author
        'Task/RegistrationInfo/URI' = $Contract.TaskPath + $Contract.TaskName
        'Task/RegistrationInfo/Version' = $Contract.Version
        'Task/Principals/Principal/RunLevel' = 'HighestAvailable'
        'Task/Actions/Exec/Arguments' = $Contract.Arguments
        'Task/Triggers/TimeTrigger/StartBoundary' = $Contract.StartBoundary
        'Task/Triggers/TimeTrigger/Repetition/Interval' = 'PT5M'
        'Task/Triggers/TimeTrigger/Repetition/StopAtDurationEnd' = 'false'
    }
    foreach ($path in $identity.Keys) {
        if ((Get-TaskXmlValue -Document $document -Path $path -Contract $Contract) -cne $identity[$path]) {
            throw "Same-name task owner/action/definition mismatch: $path; refusing to overwrite."
        }
    }
    $principal = Get-TaskXmlNode -Document $document -Path 'Task/Principals/Principal' -Contract $Contract
    $actions = Get-TaskXmlNode -Document $document -Path 'Task/Actions' -Contract $Contract
    if ($principal.GetAttribute('id') -cne $Contract.PrincipalId -or $actions.GetAttribute('Context') -cne $Contract.PrincipalId) {
        throw 'Same-name task principal/context mismatch; refusing to overwrite.'
    }
    $user = Get-TaskXmlValue -Document $document -Path 'Task/Principals/Principal/UserId' -Contract $Contract
    if (@('S-1-5-18','SYSTEM','NT AUTHORITY\SYSTEM') -notcontains $user) {
        throw 'Same-name task is not owned by the SYSTEM principal; refusing to overwrite.'
    }
    foreach ($entry in @(@('Command', $Contract.Python), @('WorkingDirectory', $Contract.WorkingDirectory))) {
        $actual = Get-TaskXmlValue -Document $document -Path ('Task/Actions/Exec/' + $entry[0]) -Contract $Contract
        if (-not [string]::Equals($actual, $entry[1], [System.StringComparison]::OrdinalIgnoreCase)) {
            throw 'Same-name task action path mismatch; refusing to overwrite.'
        }
    }
    Assert-TaskXmlChildren -Node (Get-TaskXmlNode $document 'Task/Principals' $Contract) -Allowed @('Principal') -Contract $Contract
    Assert-TaskXmlChildren -Node $principal -Allowed @('UserId','LogonType','RunLevel') -Contract $Contract
    Assert-TaskXmlChildren -Node $actions -Allowed @('Exec') -Contract $Contract
    Assert-TaskXmlChildren -Node (Get-TaskXmlNode $document 'Task/Actions/Exec' $Contract) -Allowed @('Command','Arguments','WorkingDirectory') -Contract $Contract
    $triggers = Get-TaskXmlNode -Document $document -Path 'Task/Triggers' -Contract $Contract
    Assert-TaskXmlChildren -Node $triggers -Allowed @('BootTrigger','TimeTrigger') -Contract $Contract
    $boot = Get-TaskXmlNode -Document $document -Path 'Task/Triggers/BootTrigger' -Contract $Contract
    $interval = Get-TaskXmlNode -Document $document -Path 'Task/Triggers/TimeTrigger' -Contract $Contract
    if ($boot.GetAttribute('id') -cne $Contract.BootId -or $interval.GetAttribute('id') -cne $Contract.IntervalId) {
        throw 'Same-name task trigger identity mismatch; refusing to overwrite.'
    }
    Assert-TaskXmlChildren -Node $boot -Allowed @('Enabled','ExecutionTimeLimit') -Contract $Contract
    Assert-TaskXmlChildren -Node $interval -Allowed @('Repetition','StartBoundary','Enabled','ExecutionTimeLimit') -Contract $Contract
    Assert-TaskXmlChildren -Node (Get-TaskXmlNode $document 'Task/Triggers/TimeTrigger/Repetition' $Contract) -Allowed @('Interval','StopAtDurationEnd') -Contract $Contract
    [void](Get-TaskXmlNode -Document $document -Path 'Task/Settings' -Contract $Contract)
}

function Get-TaskEnablement {
    param([Parameter(Mandatory)][string]$Xml, [Parameter(Mandatory)]$Contract)
    $document = ConvertTo-TaskXmlDocument -Xml $Xml
    $paths = [ordered]@{
        TaskEnabled = 'Task/Settings/Enabled'
        BootEnabled = 'Task/Triggers/BootTrigger/Enabled'
        IntervalEnabled = 'Task/Triggers/TimeTrigger/Enabled'
    }
    $values = [ordered]@{}
    foreach ($name in $paths.Keys) {
        $value = Get-TaskXmlValue -Document $document -Path $paths[$name] -Contract $Contract
        if ($value -cnotin @('true','false')) { throw "Task enabled value is missing or invalid: $name" }
        $values[$name] = $value
    }
    return [pscustomobject]$values
}

function Get-DesiredTaskDefinition {
    param([Parameter(Mandatory)]$Contract, [string]$PreviousXml = '')
    if ($PreviousXml) {
        Assert-OwnedTaskDefinition -Xml $PreviousXml -Contract $Contract
        [void](Get-TaskEnablement -Xml $PreviousXml -Contract $Contract)
        $document = ConvertTo-TaskXmlDocument -Xml $PreviousXml
    } else {
        $document = ConvertTo-TaskXmlDocument -Xml @'
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Version>1.0</Version><Author/><Description>Independent offline single-stock selected cache preparation only.</Description><URI/><Source/></RegistrationInfo>
  <Triggers>
    <BootTrigger id="SelectedCacheBoot"><Enabled>true</Enabled><ExecutionTimeLimit>PT0S</ExecutionTimeLimit></BootTrigger>
    <TimeTrigger id="SelectedCacheEveryFiveMinutes"><Enabled>true</Enabled><StartBoundary/><Repetition><Interval>PT5M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition><ExecutionTimeLimit>PT0S</ExecutionTimeLimit></TimeTrigger>
  </Triggers>
  <Principals><Principal id="SelectedCachePrincipal"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings><Enabled>true</Enabled></Settings>
  <Actions Context="SelectedCachePrincipal"><Exec><Command/><Arguments/><WorkingDirectory/></Exec></Actions>
</Task>
'@
        $fields = [ordered]@{
            'Task/RegistrationInfo/Author' = $Contract.Author
            'Task/RegistrationInfo/Source' = $Contract.Source
            'Task/RegistrationInfo/URI' = $Contract.TaskPath + $Contract.TaskName
            'Task/Triggers/TimeTrigger/StartBoundary' = $Contract.StartBoundary
            'Task/Actions/Exec/Command' = $Contract.Python
            'Task/Actions/Exec/Arguments' = $Contract.Arguments
            'Task/Actions/Exec/WorkingDirectory' = $Contract.WorkingDirectory
        }
        foreach ($path in $fields.Keys) { Set-TaskXmlValue $document $path $fields[$path] $Contract }
    }
    $managed = Get-ManagedTaskSettings
    foreach ($path in $managed.Keys) { Set-TaskXmlValue $document ('Task/Settings/' + $path) $managed[$path] $Contract }
    Set-TaskXmlValue $document 'Task/Triggers/BootTrigger/ExecutionTimeLimit' 'PT0S' $Contract
    Set-TaskXmlValue $document 'Task/Triggers/TimeTrigger/ExecutionTimeLimit' 'PT0S' $Contract
    return $document.OuterXml
}

function Assert-ExpectedTaskDefinition {
    param([Parameter(Mandatory)][string]$Xml, [Parameter(Mandatory)]$Contract, [AllowNull()]$ExpectedEnablement = $null)
    Assert-OwnedTaskDefinition -Xml $Xml -Contract $Contract
    $document = ConvertTo-TaskXmlDocument -Xml $Xml
    $enablement = Get-TaskEnablement -Xml $Xml -Contract $Contract
    if ($null -ne $ExpectedEnablement) {
        foreach ($name in @('TaskEnabled','BootEnabled','IntervalEnabled')) {
            if ($enablement.$name -cne $ExpectedEnablement.$name) { throw "Task enabled state changed unexpectedly: $name" }
        }
    }
    $managed = Get-ManagedTaskSettings
    foreach ($path in $managed.Keys) {
        if ((Get-TaskXmlValue $document ('Task/Settings/' + $path) $Contract) -cne $managed[$path]) {
            throw "Task managed setting mismatch: $path"
        }
    }
    foreach ($type in @('BootTrigger','TimeTrigger')) {
        if ((Get-TaskXmlValue $document ('Task/Triggers/' + $type + '/ExecutionTimeLimit') $Contract) -cne 'PT0S') {
            throw 'Task trigger execution time limit must be PT0S.'
        }
    }
}

function Invoke-NativeTaskSchemaPreflight {
    param([Parameter(Mandatory)][string]$Xml, [Parameter(Mandatory)]$Contract)
    # NewTask creates an in-memory definition only; it does not register a task.
    $scheduler = New-Object -ComObject 'Schedule.Service'
    $scheduler.Connect()
    $definition = $scheduler.NewTask(0)
    $definition.XmlText = $Xml
    if (@('S-1-5-18','SYSTEM','NT AUTHORITY\SYSTEM') -notcontains [string]$definition.Principal.UserId) {
        throw 'Native task schema preflight did not retain the SYSTEM principal.'
    }
    $definition.Principal.LogonType = 5
    if ([int]$definition.Principal.LogonType -ne 5) { throw 'Native task schema preflight rejected service-account logon type 5.' }
    [void][string]$definition.XmlText
    return [pscustomobject]@{ SchemaValidated = $true; PrincipalLogonType = 5; TaskName = $Contract.TaskName }
}

function Assert-OwnedTaskSnapshot {
    param([Parameter(Mandatory)]$Snapshot, [Parameter(Mandatory)]$Contract, [switch]$Expected,
        [AllowNull()]$ExpectedEnablement = $null)
    if ([int]$Snapshot.PrincipalLogonType -ne 5) {
        throw 'Same-name task COM principal logon type is not 5; refusing to overwrite.'
    }
    if ($Expected) { Assert-ExpectedTaskDefinition -Xml $Snapshot.DefinitionXml -Contract $Contract -ExpectedEnablement $ExpectedEnablement }
    else { Assert-OwnedTaskDefinition -Xml $Snapshot.DefinitionXml -Contract $Contract }
}

function Assert-SafeAbsolutePath {
    param([Parameter(Mandatory)][string]$Path, [switch]$AllowMissing)
    if ($Path -notmatch '^[A-Za-z]:\\' -or $Path -match '["\r\n\x00]' -or $Path.Contains('/')) { throw "Unsafe fixed path: $Path" }
    $full = [System.IO.Path]::GetFullPath($Path)
    if (-not [string]::Equals($full, $Path, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Noncanonical fixed path: $Path" }
    $current = [System.IO.Path]::GetPathRoot($full)
    $parts = $full.Substring($current.Length).Split('\')
    foreach ($part in @('') + $parts) {
        if ($part) { $current = [System.IO.Path]::Combine($current, $part) }
        try { $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop }
        catch {
            if ($AllowMissing -and $_.CategoryInfo.Category -eq [System.Management.Automation.ErrorCategory]::ObjectNotFound) { continue }
            throw
        }
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Reparse point is not allowed: $current" }
        if ($current -ne $full -and -not $item.PSIsContainer) { throw "Fixed path parent is not a directory: $current" }
    }
}

function Get-Utf8Sha256 {
    param([AllowEmptyString()][Parameter(Mandatory)][string]$Text)
    $encoding = New-Object System.Text.UTF8Encoding($false)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return ([System.BitConverter]::ToString($sha.ComputeHash($encoding.GetBytes($Text)))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Get-FixedFileSha256 {
    param([Parameter(Mandatory)][string]$Path)
    Assert-SafeAbsolutePath -Path $Path
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Required regular file is missing: $Path" }
    $stream = [System.IO.File]::OpenRead($Path)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        if ($stream.Length -eq 0) { throw "Required file is empty: $Path" }
        return ([System.BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
    } finally { $sha.Dispose(); $stream.Dispose() }
}

function Assert-InstallerInputs {
    param([Parameter(Mandatory)]$Contract, [Parameter(Mandatory)][string]$InvokedPath, [switch]$RestartRequested, [string]$ExpectedSha256)
    if (-not [string]::Equals([System.IO.Path]::GetFullPath($InvokedPath), $Contract.Installer, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Installer must run from its fixed absolute path.'
    }
    $proof = [ordered]@{}
    foreach ($path in @($Contract.Installer, $Contract.Python, $Contract.Worker)) { $proof[$path] = Get-FixedFileSha256 -Path $path }
    if ($RestartRequested) { $proof[$Contract.RestartScript] = Get-FixedFileSha256 -Path $Contract.RestartScript }
    if ($ExpectedSha256 -and $proof[$Contract.Installer] -cne $ExpectedSha256.ToLowerInvariant()) { throw 'Installer source changed across UAC elevation.' }
    return $proof
}

function Assert-InputProofUnchanged {
    param([Parameter(Mandatory)]$Proof)
    foreach ($path in $Proof.Keys) {
        if ((Get-FixedFileSha256 -Path $path) -cne $Proof[$path]) { throw "Installer input changed before completion: $path" }
    }
}

function Test-InstallerAdministrator {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Open-TaskSchedulerRoot {
    $scheduler = New-Object -ComObject 'Schedule.Service'
    $scheduler.Connect()
    return $scheduler.GetFolder('\')
}

function Get-SelectedTaskSnapshot {
    param([Parameter(Mandatory)]$Folder, [Parameter(Mandatory)]$Contract, [switch]$IncludeSecurity)
    try { $task = $Folder.GetTask($Contract.TaskName) }
    catch {
        $errorObject = $_.Exception
        while ($null -ne $errorObject) {
            if ($errorObject.HResult -eq -2147024894) { return $null }
            $errorObject = $errorObject.InnerException
        }
        throw
    }
    $expectedPath = $Contract.TaskPath + $Contract.TaskName
    if (-not [string]::Equals([string]$task.Path, $expectedPath, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Scheduled task path mismatch.' }
    $xml = [string]$task.Xml
    $definition = $task.Definition
    $definitionXml = [string]$definition.XmlText
    $logonType = [int]$definition.Principal.LogonType
    $security = if ($IncludeSecurity) { [string]$task.GetSecurityDescriptor(7) } else { $null }
    if ($xml -cne [string]$task.Xml -or $definitionXml -cne [string]$task.Definition.XmlText -or $logonType -ne [int]$task.Definition.Principal.LogonType) { throw 'Scheduled task changed while being read.' }
    return [pscustomobject]@{
        Xml = $xml; DefinitionXml = $definitionXml; PrincipalLogonType = $logonType
        SecurityDescriptor = $security; Path = $expectedPath
    }
}

function Write-ExclusiveUtf8File {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Text)
    $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($Text)
    $stream = New-Object System.IO.FileStream($Path, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) }
    finally { $stream.Dispose() }
}

function Save-TaskRollbackReceipt {
    param([Parameter(Mandatory)]$Contract, [AllowNull()]$Previous, [Parameter(Mandatory)][string]$DesiredXml, [Parameter(Mandatory)]$InputProof)
    Assert-SafeAbsolutePath -Path $Contract.ReceiptRoot -AllowMissing
    [void][System.IO.Directory]::CreateDirectory($Contract.ReceiptRoot)
    Assert-SafeAbsolutePath -Path $Contract.ReceiptRoot
    $prefix = [datetime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ', [Globalization.CultureInfo]::InvariantCulture) + '-' + [guid]::NewGuid().ToString('N')
    $receiptPath = Join-Path $Contract.ReceiptRoot ($prefix + '.rollback.json')
    $previousFile = $null
    if ($null -ne $Previous) {
        $previousFile = Join-Path $Contract.ReceiptRoot ($prefix + '.previous.xml')
        $document = ConvertTo-TaskXmlDocument -Xml $Previous.Xml
        if ($document.FirstChild -is [System.Xml.XmlDeclaration]) { $document.FirstChild.Encoding = 'utf-8' }
        Write-ExclusiveUtf8File -Path $previousFile -Text $document.OuterXml
    }
    $receipt = [ordered]@{
        schema = 'single-stock-selected-cache-task-rollback-v1'
        recorded_at = [datetime]::UtcNow.ToString('o')
        task_name = $Contract.TaskName
        task_path = $Contract.TaskPath
        operation = if ($null -eq $Previous) { 'create' } else { 'update' }
        previous_xml = if ($null -eq $Previous) { $null } else { $Previous.Xml }
        previous_definition_xml = if ($null -eq $Previous) { $null } else { $Previous.DefinitionXml }
        previous_xml_utf8_sha256 = if ($null -eq $Previous) { $null } else { Get-Utf8Sha256 -Text $Previous.Xml }
        previous_xml_file = $previousFile
        previous_security_descriptor = if ($null -eq $Previous) { $null } else { $Previous.SecurityDescriptor }
        previous_principal_logon_type = if ($null -eq $Previous) { $null } else { $Previous.PrincipalLogonType }
        registration_logon_type = 5
        desired_xml = $DesiredXml
        desired_xml_utf8_sha256 = Get-Utf8Sha256 -Text $DesiredXml
        source_sha256 = $InputProof
        registration_verified = $false
    }
    Write-ExclusiveUtf8File -Path $receiptPath -Text (($receipt | ConvertTo-Json -Depth 10) + "`n")
    return $receiptPath
}

function Assert-TaskSnapshotUnchanged {
    param([AllowNull()]$Before, [AllowNull()]$After)
    if (($null -eq $Before) -ne ($null -eq $After)) { throw 'Scheduled task appeared/disappeared before registration.' }
    if ($null -ne $Before -and ($Before.Xml -cne $After.Xml -or $Before.DefinitionXml -cne $After.DefinitionXml -or $Before.SecurityDescriptor -cne $After.SecurityDescriptor -or $Before.PrincipalLogonType -ne $After.PrincipalLogonType)) {
        throw 'Scheduled task definition/security changed before registration; rollback receipt retained.'
    }
}

function Register-SelectedTaskDefinition {
    param([Parameter(Mandatory)]$Folder, [Parameter(Mandatory)]$Contract, [Parameter(Mandatory)][string]$Xml, [switch]$Updating)
    # CREATE is exclusive. UPDATE keeps the existing ACL (NULL SDDL) and principal ACEs.
    $flags = if ($Updating) { 4 -bor 16 -bor 32 } else { 2 -bor 32 }
    [void]$Folder.RegisterTask($Contract.TaskName, $Xml, $flags, 'SYSTEM', $null, 5, $null)
}

function Invoke-ConfirmedTelegramRestart {
    param([Parameter(Mandatory)]$Contract)
    $global:LASTEXITCODE = 0
    $output = & $Contract.RestartScript -Restart -ConfirmRestart -TimeoutSeconds 180
    if ($LASTEXITCODE -ne 0) { throw "Task verified, but Telegram restart failed with exit code $LASTEXITCODE." }
    return $output
}

function New-InstallerOperation {
    param([string]$Id, [string]$Token, [string]$InstallerSha256, [switch]$RestartRequested)
    if (-not $Id) { $Id = [guid]::NewGuid().ToString('N') }
    if (-not $Token) {
        $bytes = New-Object byte[] 32
        $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try { $random.GetBytes($bytes) } finally { $random.Dispose() }
        $Token = ([System.BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
    }
    if ($Id -cnotmatch '^[0-9a-f]{32}$' -or $Token -cnotmatch '^[0-9a-f]{64}$') { throw 'Invalid installer operation identity.' }
    return [pscustomobject]@{ Id = $Id; Token = $Token; InstallerSha256 = $InstallerSha256; RestartRequested = [bool]$RestartRequested }
}

function Get-InstallerErrorDetail {
    param([Parameter(Mandatory)][System.Management.Automation.ErrorRecord]$Record)
    $chain = New-Object 'System.Collections.Generic.List[object]'
    $exception = $Record.Exception
    while ($null -ne $exception) {
        $chain.Add([ordered]@{ type = $exception.GetType().FullName; message = $exception.Message
            hresult = ('0x{0:X8}' -f $exception.HResult); text = $exception.ToString() })
        $exception = $exception.InnerException
    }
    return [ordered]@{
        message = $Record.ToString(); fully_qualified_error_id = $Record.FullyQualifiedErrorId
        category = [string]$Record.CategoryInfo; script_stack_trace = $Record.ScriptStackTrace
        position = $Record.InvocationInfo.PositionMessage
        invocation = $Record.InvocationInfo | Format-List * -Force | Out-String -Width 4096
        record = $Record | Format-List * -Force | Out-String -Width 4096
        exception_chain = $chain.ToArray()
    }
}

function Get-InstallerOperationReceiptPath {
    param([Parameter(Mandatory)]$Contract, [Parameter(Mandatory)]$Operation, [switch]$ParentError)
    if ($Operation.Id -cnotmatch '^[0-9a-f]{32}$') { throw 'Invalid receipt operation identity.' }
    $suffix = if ($ParentError) { '.parent-error.json' } else { '.result.json' }
    return Join-Path $Contract.ReceiptRoot ($Operation.Id + $suffix)
}

function Save-InstallerOperationReceipt {
    param([Parameter(Mandatory)]$Contract, [Parameter(Mandatory)]$Operation, [AllowNull()]$InputProof,
        [AllowNull()]$Snapshot, [AllowNull()]$Result, [AllowNull()]$Failure, [string]$RollbackReceipt,
        [bool]$Administrator, [bool]$RegistrationVerified, [switch]$ParentError)
    Assert-SafeAbsolutePath -Path $Contract.ReceiptRoot -AllowMissing
    [void][System.IO.Directory]::CreateDirectory($Contract.ReceiptRoot)
    Assert-SafeAbsolutePath -Path $Contract.ReceiptRoot
    $path = Get-InstallerOperationReceiptPath -Contract $Contract -Operation $Operation -ParentError:$ParentError
    $receipt = [ordered]@{
        schema = 'single-stock-selected-cache-task-operation-v1'
        operation_id = $Operation.Id; operation_token = $Operation.Token
        installer_sha256 = $Operation.InstallerSha256; source_sha256 = $InputProof
        recorded_at = [datetime]::UtcNow.ToString('o'); process_id = $PID; administrator = $Administrator
        task_name = $Contract.TaskName; task_path = $Contract.TaskPath
        restart_requested = $Operation.RestartRequested; success = $null -eq $Failure
        exit_code = if ($null -eq $Failure) { 0 } else { 2 }
        registration_verified = $RegistrationVerified; readback = $Snapshot
        raw_xml_utf8_sha256 = if ($null -eq $Snapshot) { $null } else { Get-Utf8Sha256 -Text $Snapshot.Xml }
        definition_xml_utf8_sha256 = if ($null -eq $Snapshot) { $null } else { Get-Utf8Sha256 -Text $Snapshot.DefinitionXml }
        rollback_receipt = $RollbackReceipt; result = $Result; error = $Failure
    }
    Write-ExclusiveUtf8File -Path $path -Text (($receipt | ConvertTo-Json -Depth 20) + "`n")
    return $path
}

function Read-VerifiedInstallerChildReceipt {
    param([Parameter(Mandatory)]$Contract, [Parameter(Mandatory)]$Operation, [Parameter(Mandatory)]$InputProof,
        [Parameter(Mandatory)][int]$ChildProcessId, [AllowNull()]$ChildExitCode)
    $path = Get-InstallerOperationReceiptPath -Contract $Contract -Operation $Operation
    $fileHash = Get-FixedFileSha256 -Path $path
    if ((Get-Item -LiteralPath $path -Force).Length -gt 4194304) { throw 'Elevated result receipt is too large.' }
    $receipt = [System.IO.File]::ReadAllText($path, (New-Object System.Text.UTF8Encoding($false, $true))) | ConvertFrom-Json
    if ($fileHash -cne (Get-FixedFileSha256 -Path $path)) { throw 'Elevated result receipt changed while being read.' }
    if ($receipt.schema -cne 'single-stock-selected-cache-task-operation-v1' -or
        $receipt.operation_id -cne $Operation.Id -or $receipt.operation_token -cne $Operation.Token -or
        $receipt.installer_sha256 -cne $Operation.InstallerSha256 -or
        $receipt.task_name -cne $Contract.TaskName -or $receipt.task_path -cne $Contract.TaskPath -or
        $receipt.process_id -ne $ChildProcessId -or $receipt.administrator -isnot [bool] -or -not $receipt.administrator -or
        $receipt.restart_requested -isnot [bool] -or $receipt.restart_requested -ne $Operation.RestartRequested) {
        throw "Elevated result receipt identity/source/request mismatch: $path"
    }
    if ($receipt.success -isnot [bool] -or -not $receipt.success -or $null -eq $ChildExitCode -or
        $ChildExitCode -ne 0 -or $receipt.exit_code -ne 0 -or $null -ne $receipt.error) {
        $detail = if ($null -ne $receipt.error) { [string]$receipt.error.message } else { 'Child did not finish successfully.' }
        throw "Elevated installer failed; exit code: $ChildExitCode. $detail Result receipt: $path"
    }
    foreach ($sourcePath in $InputProof.Keys) {
        $property = $receipt.source_sha256.PSObject.Properties[$sourcePath]
        if ($null -eq $property -or $property.Value -cne $InputProof[$sourcePath]) { throw 'Elevated result input proof mismatch.' }
    }
    if ($receipt.registration_verified -isnot [bool] -or -not $receipt.registration_verified -or
        $null -eq $receipt.readback -or $receipt.readback.Path -cne ($Contract.TaskPath + $Contract.TaskName) -or
        $receipt.raw_xml_utf8_sha256 -cne (Get-Utf8Sha256 -Text $receipt.readback.Xml) -or
        $receipt.definition_xml_utf8_sha256 -cne (Get-Utf8Sha256 -Text $receipt.readback.DefinitionXml)) {
        throw 'Elevated result readback proof mismatch.'
    }
    if ($null -eq $receipt.result.DesiredEnablement) { throw 'Elevated result desired enabled state is missing.' }
    Assert-OwnedTaskSnapshot -Snapshot $receipt.readback -Contract $Contract -Expected -ExpectedEnablement $receipt.result.DesiredEnablement
    if ($receipt.result.Verified -isnot [bool] -or -not $receipt.result.Verified -or
        $receipt.result.Changed -isnot [bool] -or $receipt.result.RestartTelegramRequested -isnot [bool] -or
        $receipt.result.Status -cnotin @('created','updated','already_matches') -or
        $receipt.result.RestartTelegramRequested -ne $Operation.RestartRequested -or
        $receipt.result.Changed -ne ($receipt.result.Status -cne 'already_matches')) { throw 'Elevated result is not verified.' }
    return $receipt
}

function Invoke-ElevatedSelectedCacheInstall {
    param([Parameter(Mandatory)]$Contract, [Parameter(Mandatory)][string]$InstallerSha256,
        [Parameter(Mandatory)]$Operation, [Parameter(Mandatory)]$InputProof, [switch]$RestartRequested)
    $systemDirectory = [Environment]::SystemDirectory
    $powershell = [System.IO.Path]::Combine($systemDirectory, 'WindowsPowerShell\v1.0\powershell.exe')
    Assert-SafeAbsolutePath -Path $powershell
    $arguments = @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File', ('"' + $Contract.Installer + '"'),
        '-Install','-ConfirmInstall','-ExpectedInstallerSha256', $InstallerSha256,
        '-OperationId', $Operation.Id, '-OperationToken', $Operation.Token)
    if ($RestartRequested) { $arguments += @('-RestartTelegram','-ConfirmRestart') }
    $receiptPath = Get-InstallerOperationReceiptPath -Contract $Contract -Operation $Operation
    Assert-SafeAbsolutePath -Path $receiptPath -AllowMissing
    if (Test-Path -LiteralPath $receiptPath -ErrorAction Stop) { throw 'Operation result receipt already exists; refusing replay.' }
    $oldModulePath = $env:PSModulePath
    try {
        $env:PSModulePath = [System.IO.Path]::Combine($systemDirectory, 'WindowsPowerShell\v1.0\Modules') + ';' +
            [System.IO.Path]::Combine([Environment]::GetFolderPath('ProgramFiles'), 'WindowsPowerShell\Modules')
        $process = Start-Process -FilePath $powershell -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ErrorAction Stop
        if ($null -eq $process) { throw 'UAC did not create an elevated installer process.' }
        $process.Refresh()
        $childReceipt = Read-VerifiedInstallerChildReceipt -Contract $Contract -Operation $Operation -InputProof $InputProof `
            -ChildProcessId $process.Id -ChildExitCode $process.ExitCode
        return $childReceipt
    } catch {
        throw (New-Object System.InvalidOperationException("UAC installation failed or was cancelled: $($_.Exception.Message)", $_.Exception))
    }
    finally { $env:PSModulePath = $oldModulePath }
}

function Enter-SelectedCacheInstallerLock {
    $mutex = New-Object System.Threading.Mutex($false, 'Global\DStockSingleSelectedCacheTaskInstallerV1')
    try {
        if (-not $mutex.WaitOne(0)) { throw 'Another selected-cache task installer is already running.' }
        return $mutex
    } catch { $mutex.Dispose(); throw }
}

function Exit-SelectedCacheInstallerLock {
    param([Parameter(Mandatory)]$Mutex)
    try { $Mutex.ReleaseMutex() } finally { $Mutex.Dispose() }
}

function Invoke-SelectedCacheTaskInstaller {
    param([Parameter(Mandatory)][string]$InvokedPath, [switch]$VerifyOnly, [switch]$Install, [switch]$ConfirmInstall,
        [switch]$RestartTelegram, [switch]$ConfirmRestart, [string]$ExpectedInstallerSha256,
        [string]$OperationId, [string]$OperationToken)
    $contract = Get-SelectedCacheTaskContract
    $operation = New-InstallerOperation -Id $OperationId -Token $OperationToken -InstallerSha256 $ExpectedInstallerSha256 -RestartRequested:$RestartTelegram
    $proof = $null
    $mutex = $null
    $receiptPath = $null
    $verified = $null
    $readback = $null
    $registrationVerified = $false
    $administrator = $false
    $parent = -not [bool]$OperationId
    try {
        if ($VerifyOnly -and $Install) { throw 'Choose VerifyOnly or Install, not both.' }
        if ($ConfirmInstall -and -not $Install) { throw 'ConfirmInstall requires Install.' }
        if ($Install -and -not $ConfirmInstall) { throw 'Installation requires both -Install -ConfirmInstall.' }
        if ($RestartTelegram -and (-not $Install -or -not $ConfirmRestart)) { throw 'RestartTelegram requires -Install -ConfirmInstall -ConfirmRestart.' }
        if ($ConfirmRestart -and -not $RestartTelegram) { throw 'ConfirmRestart requires RestartTelegram.' }
        if ($ExpectedInstallerSha256 -and -not $Install) { throw 'ExpectedInstallerSha256 is only valid with Install.' }
        if ([bool]$OperationId -ne [bool]$OperationToken -or ($OperationId -and (-not $Install -or -not $ExpectedInstallerSha256))) {
            throw 'Elevated operation requires Install, frozen source hash, operation id and token together.'
        }
        $administrator = Test-InstallerAdministrator
        $proof = Assert-InstallerInputs -Contract $contract -InvokedPath $InvokedPath -RestartRequested:$RestartTelegram -ExpectedSha256 $ExpectedInstallerSha256
        $operation.InstallerSha256 = $proof[$contract.Installer]
        if ($Install -and -not $administrator) {
            if (-not $parent) { throw 'Elevated child is not an administrator; refusing another UAC request.' }
            # An unprivileged caller need not read the SYSTEM task. Only the child
            # inspects its existence/ownership; absence is never inferred here.
            $candidate = Get-DesiredTaskDefinition -Contract $contract
            [void](Invoke-NativeTaskSchemaPreflight -Xml $candidate -Contract $contract)
            Assert-InputProofUnchanged -Proof $proof
            $child = Invoke-ElevatedSelectedCacheInstall -Contract $contract -InstallerSha256 $operation.InstallerSha256 `
                -Operation $operation -InputProof $proof -RestartRequested:$RestartTelegram
            Assert-InputProofUnchanged -Proof $proof
            return [pscustomobject]@{
                Mode = 'install'; Status = 'verified_after_uac'; Verified = $true; Changed = $child.result.Changed
                TaskName = $contract.TaskName; TaskPath = $contract.TaskPath
                OperationId = $operation.Id; OperationReceipt = (Get-InstallerOperationReceiptPath $contract $operation)
                ChildStatus = $child.result.Status; RestartTelegramRequested = [bool]$RestartTelegram
                DesiredEnablement = $child.result.DesiredEnablement
                TelegramRestartResult = $child.result.TelegramRestartResult
            }
        }
        $folder = Open-TaskSchedulerRoot
        $snapshot = Get-SelectedTaskSnapshot -Folder $folder -Contract $contract
        $readback = $snapshot
        if ($null -ne $snapshot) { Assert-OwnedTaskSnapshot -Snapshot $snapshot -Contract $contract }
        $candidate = Get-DesiredTaskDefinition -Contract $contract -PreviousXml $(if ($null -eq $snapshot) { '' } else { $snapshot.DefinitionXml })
        [void](Invoke-NativeTaskSchemaPreflight -Xml $candidate -Contract $contract)
        if (-not $Install) {
            if ($null -ne $snapshot) { Assert-OwnedTaskSnapshot -Snapshot $snapshot -Contract $contract -Expected }
            return [pscustomobject]@{
                Mode = 'verify_only'; TaskName = $contract.TaskName; TaskPath = $contract.TaskPath
                Status = if ($null -eq $snapshot) { 'not_installed' } else { 'verified' }
                Verified = $null -ne $snapshot; Changed = $false
                Python = $contract.Python; Arguments = $contract.Arguments
            }
        }
        $mutex = Enter-SelectedCacheInstallerLock
        $before = Get-SelectedTaskSnapshot -Folder $folder -Contract $contract -IncludeSecurity
        $readback = $before
        if ($null -ne $before) { Assert-OwnedTaskSnapshot -Snapshot $before -Contract $contract }
        $desired = Get-DesiredTaskDefinition -Contract $contract -PreviousXml $(if ($null -eq $before) { '' } else { $before.DefinitionXml })
        $desiredEnablement = Get-TaskEnablement -Xml $desired -Contract $contract
        [void](Invoke-NativeTaskSchemaPreflight -Xml $desired -Contract $contract)
        $changed = $true
        if ($null -ne $before) {
            try { Assert-OwnedTaskSnapshot -Snapshot $before -Contract $contract -Expected; $changed = $false }
            catch { $changed = $true }
        }
        $status = 'already_matches'
        if ($changed) {
            $receiptPath = Save-TaskRollbackReceipt -Contract $contract -Previous $before -DesiredXml $desired -InputProof $proof
            Assert-InputProofUnchanged -Proof $proof
            $current = Get-SelectedTaskSnapshot -Folder $folder -Contract $contract -IncludeSecurity
            $readback = $current
            Assert-TaskSnapshotUnchanged -Before $before -After $current
            Register-SelectedTaskDefinition -Folder $folder -Contract $contract -Xml $desired -Updating:($null -ne $before)
            $status = if ($null -eq $before) { 'created' } else { 'updated' }
        }
        $verified = Get-SelectedTaskSnapshot -Folder $folder -Contract $contract -IncludeSecurity
        $readback = $verified
        if ($null -eq $verified) { throw 'Task was not found after registration.' }
        Assert-OwnedTaskSnapshot -Snapshot $verified -Contract $contract -Expected -ExpectedEnablement $desiredEnablement
        if ($null -ne $before -and $verified.SecurityDescriptor -cne $before.SecurityDescriptor) { throw 'Task ACL changed unexpectedly; original XML/security receipt retained.' }
        Assert-InputProofUnchanged -Proof $proof
        $registrationVerified = $true
        if ($receiptPath) {
            $completion = [ordered]@{
                schema = 'single-stock-selected-cache-task-installation-v1'; recorded_at = [datetime]::UtcNow.ToString('o')
                task_name = $contract.TaskName; status = $status; registration_verified = $true
                rollback_receipt = $receiptPath; registered_xml_utf8_sha256 = Get-Utf8Sha256 -Text $verified.Xml
                definition_xml_utf8_sha256 = Get-Utf8Sha256 -Text $verified.DefinitionXml
            }
            Write-ExclusiveUtf8File -Path ($receiptPath + '.verified.json') -Text (($completion | ConvertTo-Json -Depth 8) + "`n")
        }
        $restartResult = if ($RestartTelegram) { Invoke-ConfirmedTelegramRestart -Contract $contract } else { $null }
        Assert-InputProofUnchanged -Proof $proof
        $result = [pscustomobject]@{
            Mode = 'install'; Status = $status; Verified = $true; Changed = $changed
            DesiredEnablement = $desiredEnablement
            TaskName = $contract.TaskName; TaskPath = $contract.TaskPath; RollbackReceipt = $receiptPath
            RestartTelegramRequested = [bool]$RestartTelegram; TelegramRestartResult = $restartResult
            OperationId = $operation.Id; OperationReceipt = (Get-InstallerOperationReceiptPath $contract $operation)
        }
        [void](Save-InstallerOperationReceipt -Contract $contract -Operation $operation -InputProof $proof -Snapshot $verified `
            -Result $result -Failure $null -RollbackReceipt $receiptPath -Administrator $administrator -RegistrationVerified $registrationVerified)
        return $result
    } catch {
        $original = $_
        $errorReceipt = $null
        try {
            $errorReceipt = Save-InstallerOperationReceipt -Contract $contract -Operation $operation -InputProof $proof -Snapshot $readback `
                -Result $null -Failure (Get-InstallerErrorDetail -Record $original) -RollbackReceipt $receiptPath `
                -Administrator $administrator -RegistrationVerified $registrationVerified -ParentError:($parent -and -not $administrator)
        } catch { [Console]::Error.WriteLine('Could not persist installer error receipt: ' + $_.Exception.ToString()) }
        $errorObject = New-Object System.InvalidOperationException("Selected-cache task installation stopped. Rollback receipt: $receiptPath. Result receipt: $errorReceipt. $($original.Exception.Message)", $original.Exception)
        if ($errorReceipt) { $errorObject.Data['InstallerErrorReceipt'] = $errorReceipt }
        throw $errorObject
    } finally { if ($null -ne $mutex) { Exit-SelectedCacheInstallerLock -Mutex $mutex } }
}

try {
    $result = Invoke-SelectedCacheTaskInstaller -InvokedPath $PSCommandPath -VerifyOnly:$VerifyOnly -Install:$Install -ConfirmInstall:$ConfirmInstall `
        -RestartTelegram:$RestartTelegram -ConfirmRestart:$ConfirmRestart -ExpectedInstallerSha256 $ExpectedInstallerSha256 `
        -OperationId $OperationId -OperationToken $OperationToken
    $result | ConvertTo-Json -Depth 12
    if (-not $result.Verified) { exit 2 }
    exit 0
} catch {
    $fatal = $_
    if (-not $fatal.Exception.Data.Contains('InstallerErrorReceipt')) {
        try {
            $contract = Get-SelectedCacheTaskContract
            $operation = New-InstallerOperation -Id $OperationId -Token $OperationToken `
                -InstallerSha256 (Get-FixedFileSha256 -Path $contract.Installer) -RestartRequested:$RestartTelegram
            [void](Save-InstallerOperationReceipt -Contract $contract -Operation $operation -InputProof $null -Snapshot $null `
                -Result $null -Failure (Get-InstallerErrorDetail -Record $fatal) -Administrator (Test-InstallerAdministrator) -RegistrationVerified $false -ParentError)
        } catch { [Console]::Error.WriteLine('Could not persist fatal installer error: ' + $_.Exception.ToString()) }
    }
    [Console]::Error.WriteLine($fatal.Exception.ToString())
    exit 2
}
