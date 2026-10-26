param(
    [switch]$VerifyOnly,
    [switch]$Restart,
    [switch]$ConfirmRestart,
    [switch]$Deploy,
    [switch]$ConfirmDeploy,
    # VerifyOnly only: report a resident that runs an older listener source as
    # SourceMismatch=true instead of failing.  Used by CHECK_COMMAND_BOT_STATUS.ps1
    # so stale_source is a warning; restart/deploy verification is unchanged.
    [switch]$AllowStaleSource,
    [ValidateRange(10, 600)]
    [int]$TimeoutSeconds = 60
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-CanonicalExactPath {
    param(
        [Parameter(Mandatory)][string]$Path,
        [ValidateSet("Leaf", "Container")][string]$PathType = "Leaf"
    )
    if ([string]::IsNullOrWhiteSpace($Path) -or $Path -notmatch '^[A-Za-z]:\\' -or
        $Path.Contains('"') -or $Path -match '[*?\[\]]') {
        throw "Path is not a safe absolute path: $Path"
    }
    try { $fullPath = [IO.Path]::GetFullPath($Path) }
    catch { throw "Path cannot be canonicalized: $Path" }
    if ($fullPath -notmatch '^[A-Za-z]:\\') { throw "Path is not a safe absolute path: $Path" }

    $root = [IO.Path]::GetPathRoot($fullPath)
    if ([string]::IsNullOrWhiteSpace($root)) { throw "Path root is unavailable: $Path" }
    try { $currentItem = Get-Item -LiteralPath $root -Force -ErrorAction Stop }
    catch { throw "Path root is unavailable: $Path" }
    if (($currentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Path root is a reparse point: $root"
    }

    $relativePath = $fullPath.Substring($root.Length)
    $segments = @($relativePath -split '\\' | Where-Object { $_ -ne "" })
    foreach ($segment in $segments) {
        $currentPath = Join-Path -Path ([string]$currentItem.FullName) -ChildPath $segment
        try { $currentItem = Get-Item -LiteralPath $currentPath -Force -ErrorAction Stop }
        catch { throw "Path component is unavailable: $currentPath" }
        if (($currentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Path component is a reparse point: $currentPath"
        }
    }

    $isContainer = [bool]$currentItem.PSIsContainer
    if ($PathType -eq "Leaf" -and ($isContainer -or $currentItem -isnot [IO.FileInfo])) {
        throw "Required path is not a regular file: $Path"
    }
    if ($PathType -eq "Container" -and -not $isContainer) { throw "Required path is not a directory: $Path" }
    $canonicalPath = [string]$currentItem.FullName
    if (-not [System.String]::Equals(
            $canonicalPath.TrimEnd([char]0x5c),
            $fullPath.TrimEnd([char]0x5c),
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "Canonical path mismatch: $Path"
    }
    return $canonicalPath
}

function Get-FileSha256 {
    param([Parameter(Mandatory)][string]$Path)
    $canonicalPath = Get-CanonicalExactPath -Path $Path -PathType Leaf
    return (Get-FileHash -LiteralPath $canonicalPath -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Skip-JsonWhitespace {
    param([Parameter(Mandatory)]$Context)
    while ($Context.Index -lt $Context.Text.Length -and
        [char]::IsWhiteSpace([char]$Context.Text[$Context.Index])) {
        $Context.Index++
    }
}

function Read-JsonStringToken {
    param([Parameter(Mandatory)]$Context)
    Skip-JsonWhitespace -Context $Context
    if ($Context.Index -ge $Context.Text.Length -or
        [char]$Context.Text[$Context.Index] -ne [char]0x22) {
        throw "JSON string token is missing."
    }
    $Context.Index++
    $builder = New-Object System.Text.StringBuilder
    while ($Context.Index -lt $Context.Text.Length) {
        $character = [char]$Context.Text[$Context.Index]
        $Context.Index++
        if ($character -eq [char]0x22) { return $builder.ToString() }
        if ($character -eq [char]0x5c) {
            if ($Context.Index -ge $Context.Text.Length) { throw "JSON string escape is incomplete." }
            $escape = [char]$Context.Text[$Context.Index]
            $Context.Index++
            if ($escape -eq [char]0x22 -or $escape -eq [char]0x5c -or $escape -eq [char]0x2f) {
                [void]$builder.Append($escape)
            } elseif ($escape -eq [char]0x62) {
                [void]$builder.Append([char]0x08)
            } elseif ($escape -eq [char]0x66) {
                [void]$builder.Append([char]0x0c)
            } elseif ($escape -eq [char]0x6e) {
                [void]$builder.Append([char]0x0a)
            } elseif ($escape -eq [char]0x72) {
                [void]$builder.Append([char]0x0d)
            } elseif ($escape -eq [char]0x74) {
                [void]$builder.Append([char]0x09)
            } elseif ($escape -eq [char]0x75) {
                if ($Context.Index + 4 -gt $Context.Text.Length) { throw "JSON unicode escape is incomplete." }
                $hex = $Context.Text.Substring($Context.Index, 4)
                if ($hex -notmatch '^[0-9A-Fa-f]{4}$') { throw "JSON unicode escape is invalid." }
                [void]$builder.Append([char]([Convert]::ToInt32($hex, 16)))
                $Context.Index += 4
            } else {
                throw "JSON string escape is invalid."
            }
            continue
        }
        if ([int]$character -lt 0x20) { throw "JSON string contains an unescaped control character." }
        [void]$builder.Append($character)
    }
    throw "JSON string token is unterminated."
}

function Read-JsonValueForValidation {
    param([Parameter(Mandatory)]$Context)
    Skip-JsonWhitespace -Context $Context
    if ($Context.Index -ge $Context.Text.Length) { throw "JSON value is missing." }
    $character = [char]$Context.Text[$Context.Index]
    if ($character -eq [char]0x22) {
        [void](Read-JsonStringToken -Context $Context)
        return
    }
    if ($character -eq [char]0x7b) {
        $Context.Index++
        $keys = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
        Skip-JsonWhitespace -Context $Context
        if ($Context.Index -lt $Context.Text.Length -and [char]$Context.Text[$Context.Index] -eq [char]0x7d) {
            $Context.Index++
            return
        }
        while ($true) {
            $key = Read-JsonStringToken -Context $Context
            if (-not $keys.Add($key)) { throw "JSON object contains a duplicate key: $key" }
            Skip-JsonWhitespace -Context $Context
            if ($Context.Index -ge $Context.Text.Length -or [char]$Context.Text[$Context.Index] -ne [char]0x3a) {
                throw "JSON object key/value separator is missing."
            }
            $Context.Index++
            Read-JsonValueForValidation -Context $Context
            Skip-JsonWhitespace -Context $Context
            if ($Context.Index -ge $Context.Text.Length) { throw "JSON object is unterminated." }
            $separator = [char]$Context.Text[$Context.Index]
            if ($separator -eq [char]0x7d) {
                $Context.Index++
                return
            }
            if ($separator -ne [char]0x2c) { throw "JSON object member separator is invalid." }
            $Context.Index++
            Skip-JsonWhitespace -Context $Context
        }
    }
    if ($character -eq [char]0x5b) {
        $Context.Index++
        Skip-JsonWhitespace -Context $Context
        if ($Context.Index -lt $Context.Text.Length -and [char]$Context.Text[$Context.Index] -eq [char]0x5d) {
            $Context.Index++
            return
        }
        while ($true) {
            Read-JsonValueForValidation -Context $Context
            Skip-JsonWhitespace -Context $Context
            if ($Context.Index -ge $Context.Text.Length) { throw "JSON array is unterminated." }
            $separator = [char]$Context.Text[$Context.Index]
            if ($separator -eq [char]0x5d) {
                $Context.Index++
                return
            }
            if ($separator -ne [char]0x2c) { throw "JSON array member separator is invalid." }
            $Context.Index++
            Skip-JsonWhitespace -Context $Context
        }
    }

    $start = $Context.Index
    while ($Context.Index -lt $Context.Text.Length) {
        $current = [char]$Context.Text[$Context.Index]
        if ([char]::IsWhiteSpace($current) -or $current -eq [char]0x2c -or
            $current -eq [char]0x5d -or $current -eq [char]0x7d -or $current -eq [char]0x3a) { break }
        $Context.Index++
    }
    if ($Context.Index -eq $start) { throw "JSON primitive value is missing." }
    $primitive = $Context.Text.Substring($start, $Context.Index - $start)
    if ($primitive -in @("NaN", "Infinity", "+Infinity", "-Infinity")) {
        throw "JSON nonfinite number is not allowed."
    }
}

function Assert-ResidentJsonStructure {
    param([Parameter(Mandatory)][string]$Json)
    if ([string]::IsNullOrWhiteSpace($Json)) { throw "JSON document is empty." }
    $context = [pscustomobject]@{ Text = $Json; Index = 0 }
    Read-JsonValueForValidation -Context $context
    Skip-JsonWhitespace -Context $context
    if ($context.Index -ne $context.Text.Length) { throw "JSON document contains trailing data." }
}

function ConvertFrom-JsonElementPreservingStrings {
    param([Parameter(Mandatory)]$Element)
    switch ([string]$Element.ValueKind) {
        "Object" {
            $result = [ordered]@{}
            foreach ($property in $Element.EnumerateObject()) {
                $result[$property.Name] = ConvertFrom-JsonElementPreservingStrings -Element $property.Value
            }
            return [pscustomobject]$result
        }
        "Array" {
            $result = New-Object System.Collections.Generic.List[object]
            foreach ($item in $Element.EnumerateArray()) {
                $converted = ConvertFrom-JsonElementPreservingStrings -Element $item
                [void]$result.Add($converted)
            }
            return ,($result.ToArray())
        }
        "String" { return [string]$Element.GetString() }
        "True" { return $true }
        "False" { return $false }
        "Null" { return $null }
        "Number" {
            $raw = [string]$Element.GetRawText()
            $integer = [long]0
            if ([long]::TryParse($raw, [Globalization.NumberStyles]::Integer, [Globalization.CultureInfo]::InvariantCulture, [ref]$integer)) {
                return $integer
            }
            $decimal = [decimal]0
            if ([decimal]::TryParse($raw, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$decimal)) {
                return $decimal
            }
            $double = [double]0
            if ([double]::TryParse($raw, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$double) -and
                -not [double]::IsNaN($double) -and -not [double]::IsInfinity($double)) {
                return $double
            }
            throw "JSON number is invalid or nonfinite."
        }
        default { throw "JSON value kind is unsupported." }
    }
}

function Assert-NoNonFiniteJsonValue {
    param([AllowNull()][Parameter(Mandatory)]$Value)
    if ($null -eq $Value) { return }
    if ($Value -is [DateTime] -or $Value -is [DateTimeOffset]) {
        throw "JSON parser materialized a timestamp instead of preserving its string."
    }
    if ($Value -is [double] -or $Value -is [single]) {
        $number = [double]$Value
        if ([double]::IsNaN($number) -or [double]::IsInfinity($number)) {
            throw "JSON nonfinite number is not allowed."
        }
        return
    }
    if ($Value -is [string] -or $Value.GetType().IsValueType) { return }
    if ($Value -is [System.Collections.IDictionary]) {
        foreach ($key in $Value.Keys) { Assert-NoNonFiniteJsonValue -Value $Value[$key] }
        return
    }
    if ($Value -is [System.Collections.IEnumerable] -and $Value -isnot [string]) {
        foreach ($item in $Value) { Assert-NoNonFiniteJsonValue -Value $item }
        return
    }
    foreach ($property in @($Value.PSObject.Properties)) {
        Assert-NoNonFiniteJsonValue -Value $property.Value
    }
}

function ConvertFrom-ResidentJsonString {
    param([Parameter(Mandatory)][string]$Json)
    Assert-ResidentJsonStructure -Json $Json
    $convertFromJson = Get-Command ConvertFrom-Json -CommandType Cmdlet -ErrorAction Stop
    $value = $null
    if ($convertFromJson.Parameters.ContainsKey("DateKind")) {
        try { $value = $Json | ConvertFrom-Json -DateKind String -ErrorAction Stop }
        catch { throw "Resident evidence JSON parsing failed: $($_.Exception.Message)" }
    } else {
        $jsonDocumentType = [Type]::GetType("System.Text.Json.JsonDocument, System.Text.Json", $false)
        if ($null -ne $jsonDocumentType) {
            $document = $null
            try {
                $parseMethod = $jsonDocumentType.GetMethod("Parse", [Type[]]@([string]))
                if ($null -eq $parseMethod) { throw "System.Text.Json string parser is unavailable." }
                $document = $parseMethod.Invoke($null, [object[]]@($Json))
                $value = ConvertFrom-JsonElementPreservingStrings -Element $document.RootElement
            } catch {
                throw "Resident evidence JSON parsing failed: $($_.Exception.Message)"
            } finally {
                if ($null -ne $document) { $document.Dispose() }
            }
        } else {
            try {
                Add-Type -AssemblyName System.Web.Extensions -ErrorAction Stop
                $serializer = New-Object System.Web.Script.Serialization.JavaScriptSerializer
                $value = $serializer.DeserializeObject($Json)
            } catch {
                throw "Resident evidence JSON parser cannot preserve strings on this PowerShell version: $($_.Exception.Message)"
            }
        }
    }
    Assert-NoNonFiniteJsonValue -Value $value
    if ($null -eq $value) { throw "Resident evidence JSON root is null." }
    return $value
}

function Get-ResidentJson {
    param([Parameter(Mandatory)][string]$Path)
    $canonicalPath = Get-CanonicalExactPath -Path $Path -PathType Leaf
    try {
        $json = Get-Content -LiteralPath $canonicalPath -Raw -Encoding UTF8 -ErrorAction Stop
        return ConvertFrom-ResidentJsonString -Json $json
    } catch {
        throw "Resident evidence is not valid JSON: $Path ($($_.Exception.Message))"
    }
}

function Get-ProcessSnapshot {
    param([Parameter(Mandatory)][int]$ProcessId)
    $matches = @(Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop)
    if ($matches.Count -ne 1) { return $null }
    return $matches[0]
}

function ConvertTo-UtcDateTimeOffset {
    param([Parameter(Mandatory)]$Value)

    if ($Value -is [DateTimeOffset]) {
        return ([DateTimeOffset]$Value).ToUniversalTime()
    }
    if ($Value -is [DateTime]) {
        $dateTime = [DateTime]$Value
        if ($dateTime.Kind -eq [DateTimeKind]::Utc) {
            return ([DateTimeOffset]::new($dateTime)).ToUniversalTime()
        }
        throw "DateTime timestamp must have Kind=Utc."
    }
    if ($Value -isnot [string]) { throw "Timestamp type is unsupported." }

    $text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($text)) { throw "Timestamp is missing." }
    if ($text -notmatch '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,7})?(?:Z|[+-][0-9]{2}:[0-9]{2})$') {
        throw "Timestamp is not a strict ISO UTC/offset value."
    }
    $formats = [string[]]@(
        "yyyy-MM-dd'T'HH:mm:ss'Z'",
        "yyyy-MM-dd'T'HH:mm:ss.FFFFFFF'Z'",
        "yyyy-MM-dd'T'HH:mm:sszzz",
        "yyyy-MM-dd'T'HH:mm:ss.FFFFFFFzzz"
    )
    $styles = [Globalization.DateTimeStyles]::AssumeUniversal -bor
        [Globalization.DateTimeStyles]::AdjustToUniversal
    $parsed = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParseExact(
            $text,
            $formats,
            [Globalization.CultureInfo]::InvariantCulture,
            $styles,
            [ref]$parsed
        )) {
        throw "Timestamp is not a strict ISO UTC/offset value."
    }
    return $parsed.ToUniversalTime()
}

function ConvertTo-CanonicalUtcTimestamp {
    param([Parameter(Mandatory)]$Value)
    $utc = ConvertTo-UtcDateTimeOffset -Value $Value
    return $utc.ToString("yyyy-MM-dd'T'HH:mm:ss.fffffff'Z'", [Globalization.CultureInfo]::InvariantCulture)
}

function Test-ProcessCreationIdentity {
    param(
        [Parameter(Mandatory)]$Process,
        [Parameter(Mandatory)]$Identity
    )
    if ($null -eq $Process -or $null -eq $Identity -or $null -eq $Identity.startedAt) { return $false }
    try {
        $identityStarted = ConvertTo-UtcDateTimeOffset -Value $Identity.startedAt
        $processCreated = Get-NormalizedCimCreationDate -Process $Process
        return [math]::Abs([double]($processCreated.UtcTicks - $identityStarted.UtcTicks)) -le [TimeSpan]::FromSeconds(5).Ticks
    } catch { throw "Process creation identity timestamp is invalid." }
}

function Get-NormalizedCimCreationDate {
    param([Parameter(Mandatory)]$Process)
    try {
        $creationDate = $Process.CreationDate
        if ($creationDate -is [DateTime] -and $creationDate.Kind -eq [DateTimeKind]::Local) {
            return ([DateTimeOffset]::new($creationDate)).ToUniversalTime()
        }
        return ConvertTo-UtcDateTimeOffset -Value $creationDate
    } catch {
        throw "CIM process CreationDate is invalid."
    }
}

function Assert-ProcessInstanceIdentity {
    param(
        [Parameter(Mandatory)]$Expected,
        [Parameter(Mandatory)]$Observed,
        [Parameter(Mandatory)][string]$Role
    )
    $expectedPid = 0
    $observedPid = 0
    if (-not [int]::TryParse([string]$Expected.ProcessId, [ref]$expectedPid) -or
        -not [int]::TryParse([string]$Observed.ProcessId, [ref]$observedPid) -or
        $expectedPid -le 0 -or $observedPid -ne $expectedPid) {
        throw "$Role process PID identity changed."
    }
    $expectedCreationUtc = Get-NormalizedCimCreationDate -Process $Expected
    $observedCreationUtc = Get-NormalizedCimCreationDate -Process $Observed
    if ($expectedCreationUtc.UtcTicks -ne $observedCreationUtc.UtcTicks) {
        throw "$Role process CIM CreationDate identity changed."
    }
    foreach ($propertyName in @("ExecutablePath", "CommandLine")) {
        $expectedValue = [string](Get-ResidentPropertyValue -Object $Expected -Name $propertyName)
        $observedValue = [string](Get-ResidentPropertyValue -Object $Observed -Name $propertyName)
        if ($propertyName -eq "ExecutablePath") {
            if ([string]::IsNullOrWhiteSpace($expectedValue) -or [string]::IsNullOrWhiteSpace($observedValue)) {
                throw "$Role process executable path is empty."
            }
            try {
                $expectedCanonicalPath = Get-CanonicalExactPath -Path $expectedValue -PathType Leaf
                $observedCanonicalPath = Get-CanonicalExactPath -Path $observedValue -PathType Leaf
            } catch {
                throw "$Role process executable path is not canonical."
            }
            if (-not [System.String]::Equals(
                    $expectedCanonicalPath,
                    $observedCanonicalPath,
                    [System.StringComparison]::OrdinalIgnoreCase
                )) {
                throw "$Role process executable path identity changed."
            }
            continue
        }
        if (-not [string]::IsNullOrWhiteSpace($expectedValue) -and
            ([string]::IsNullOrWhiteSpace($observedValue) -or
                -not [System.String]::Equals($expectedValue, $observedValue, [System.StringComparison]::OrdinalIgnoreCase))) {
            throw "$Role process $propertyName identity changed."
        }
    }
    return $observedCreationUtc.UtcTicks
}

function Test-MicrosoftCertificateOrganization {
    param(
        [Parameter(Mandatory)]
        [System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate
    )
    $subject = [string]$Certificate.Subject
    return $subject -match '(?i)(?:^|,\s*)O\s*=\s*Microsoft Corporation(?:,|$)'
}

function Assert-MicrosoftPublisherIdentity {
    param(
        [Parameter(Mandatory)]$Signature,
        [Parameter(Mandatory)][string]$Path
    )
    if ($null -eq $Signature -or [string]$Signature.Status -ne "Valid" -or
        $null -eq $Signature.SignerCertificate -or
        $Signature.SignerCertificate -isnot [System.Security.Cryptography.X509Certificates.X509Certificate2]) {
        throw "Trusted fsutil.exe Authenticode signature is unavailable or invalid."
    }
    $certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]$Signature.SignerCertificate
    try {
        $versionInfo = [Diagnostics.FileVersionInfo]::GetVersionInfo($Path)
    } catch {
        throw "Trusted fsutil.exe fixed file version information is unavailable."
    }
    if ([string]$versionInfo.CompanyName -ne "Microsoft Corporation") {
        throw "Trusted fsutil.exe fixed file version company is not Microsoft Corporation."
    }
    if (-not (Test-MicrosoftCertificateOrganization -Certificate $certificate)) {
        throw "Trusted fsutil.exe signer certificate is not a Microsoft publisher certificate."
    }

    $chain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
    try {
        $chain.ChainPolicy.RevocationMode = [System.Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
        $chain.ChainPolicy.VerificationFlags = [System.Security.Cryptography.X509Certificates.X509VerificationFlags]::NoFlag
        # Windows already verified this Authenticode signature (Status=Valid) against its trusted
        # timestamp, so the signer certificate may legitimately be expired today.  Build the
        # ownership chain at a time inside the signer's validity window instead of "now";
        # otherwise every OS binary fails on its certificate's expiry day (seen 2026-10-18).
        if ($null -ne $Signature.TimeStamperCertificate) {
            $verificationTime = Get-Date
            if ($verificationTime -gt $certificate.NotAfter) {
                $verificationTime = $certificate.NotAfter.AddSeconds(-1)
            } elseif ($verificationTime -lt $certificate.NotBefore) {
                $verificationTime = $certificate.NotBefore.AddSeconds(1)
            }
            $chain.ChainPolicy.VerificationTime = $verificationTime
        }
        if (-not $chain.Build($certificate)) {
            throw "Trusted fsutil.exe signer certificate chain is not trusted."
        }
        $chainCertificates = @($chain.ChainElements | ForEach-Object { $_.Certificate })
        if ($chainCertificates.Count -lt 2) { throw "Trusted fsutil.exe signer certificate chain is incomplete." }
        $chainMicrosoftEvidence = @($chainCertificates | Where-Object {
                Test-MicrosoftCertificateOrganization -Certificate $_
            }).Count -ge 2
        $rootCertificate = $chainCertificates[$chainCertificates.Count - 1]
        $rootName = [string]$rootCertificate.GetNameInfo(
            [System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName,
            $false
        )
        $rootMicrosoftEvidence = (Test-MicrosoftCertificateOrganization -Certificate $rootCertificate) -and
            $rootName -match '(?i)^Microsoft .*Root Certificate Authority'
        if (-not $chainMicrosoftEvidence -or -not $rootMicrosoftEvidence) {
            throw "Trusted fsutil.exe signer certificate chain is not Microsoft-owned."
        }
    } catch {
        throw $_
    } finally {
        $chain.Dispose()
    }
}

function Get-FsutilFileId {
    param(
        [Parameter(Mandatory)][string]$FsutilPath,
        [Parameter(Mandatory)][string]$ResolvedPath
    )
    $fileIdOutput = @(& $FsutilPath file queryfileid $ResolvedPath 2>$null)
    $fsutilExitCode = $LASTEXITCODE
    if ($fsutilExitCode -ne 0) { throw "Unable to read file identity: $ResolvedPath" }
    $fileIdMatch = [regex]::Match([string]::Join("`n", [string[]]$fileIdOutput), '0x[0-9A-Fa-f]+')
    if (-not $fileIdMatch.Success) { throw "File identity is missing: $ResolvedPath" }
    return $fileIdMatch.Value.ToLowerInvariant()
}

function Get-FileIdentity {
    param([Parameter(Mandatory)][string]$Path)

    $resolvedPath = Get-CanonicalExactPath -Path $Path -PathType Leaf
    $systemDirectory = [Environment]::SystemDirectory
    if ([string]::IsNullOrWhiteSpace($systemDirectory)) { throw "Trusted system directory is unavailable." }
    $canonicalSystemDirectory = Get-CanonicalExactPath -Path $systemDirectory -PathType Container
    $fsutilPath = Get-CanonicalExactPath -Path (Join-Path $canonicalSystemDirectory "fsutil.exe") -PathType Leaf
    $fsutilSignature = Get-AuthenticodeSignature -LiteralPath $fsutilPath
    Assert-MicrosoftPublisherIdentity -Signature $fsutilSignature -Path $fsutilPath

    $identityBefore = Get-FsutilFileId -FsutilPath $fsutilPath -ResolvedPath $resolvedPath
    try {
        $sha1 = Get-FileSha256 -Path $resolvedPath
        $fileInfo1 = Get-Item -LiteralPath $resolvedPath -Force -ErrorAction Stop
        $size1 = [int64]$fileInfo1.Length
    } catch {
        throw "Unable to read file SHA1/size1: $resolvedPath ($($_.Exception.Message))"
    }
    $identityAfter = Get-FsutilFileId -FsutilPath $fsutilPath -ResolvedPath $resolvedPath
    if (-not [System.String]::Equals($identityBefore, $identityAfter, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "File identity/hash/size changed during evidence read: $resolvedPath"
    }
    try {
        $sha2 = Get-FileSha256 -Path $resolvedPath
        $fileInfo2 = Get-Item -LiteralPath $resolvedPath -Force -ErrorAction Stop
        $size2 = [int64]$fileInfo2.Length
    } catch {
        throw "Unable to read file SHA2/size2: $resolvedPath ($($_.Exception.Message))"
    }
    $identityAfterSecond = Get-FsutilFileId -FsutilPath $fsutilPath -ResolvedPath $resolvedPath
    if (-not [System.String]::Equals($identityAfter, $identityAfterSecond, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "File identity changed during second evidence read: $resolvedPath"
    }
    if (-not [System.String]::Equals($sha1, $sha2, [System.StringComparison]::OrdinalIgnoreCase) -or
        $size1 -ne $size2) {
        throw "File SHA/size changed during evidence read: $resolvedPath"
    }
    [pscustomobject]@{
        Path = $resolvedPath
        FileId = $identityAfterSecond
        SHA256 = $sha2
        Size = $size2
    }
}

function Assert-FixedRestartScript {
    param(
        [Parameter(Mandatory)][string]$FixedPath,
        [Parameter(Mandatory)][string]$CurrentPath
    )

    $canonicalFixedPath = Get-CanonicalExactPath -Path $FixedPath -PathType Leaf
    $canonicalCurrentPath = Get-CanonicalExactPath -Path $CurrentPath -PathType Leaf
    if (-not [System.String]::Equals(
            $canonicalCurrentPath,
            $canonicalFixedPath,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "Current script path is not the fixed restart script path."
    }

    $fixedIdentity = Get-FileIdentity -Path $canonicalFixedPath
    $currentIdentity = Get-FileIdentity -Path $canonicalCurrentPath
    $identityEvidence =
        [System.String]::Equals($fixedIdentity.Path, $canonicalFixedPath, [System.StringComparison]::OrdinalIgnoreCase) -and
        [System.String]::Equals($currentIdentity.Path, $canonicalCurrentPath, [System.StringComparison]::OrdinalIgnoreCase) -and
        [System.String]::Equals($fixedIdentity.FileId, $currentIdentity.FileId, [System.StringComparison]::OrdinalIgnoreCase) -and
        [System.String]::Equals($fixedIdentity.SHA256, $currentIdentity.SHA256, [System.StringComparison]::OrdinalIgnoreCase)
    if (-not $identityEvidence) { throw "Current script file identity/hash does not match the fixed restart script." }

    [pscustomobject]@{
        CanonicalPath = $canonicalFixedPath
        FileId = $fixedIdentity.FileId
        SHA256 = $fixedIdentity.SHA256
    }
}

$serviceName = "DStockTelegramCommandBot"
$fixedScriptPath = "D:\stock\programs\telegram_report_delivery\RESTART_COMMAND_BOT_SERVICE.ps1"
$currentScriptPath = [string]$PSCommandPath
$fixedScriptEvidence = Assert-FixedRestartScript -FixedPath $fixedScriptPath -CurrentPath $currentScriptPath
$scriptPath = [string]$fixedScriptEvidence.CanonicalPath
$suite = Get-CanonicalExactPath -Path ([IO.Path]::GetDirectoryName($scriptPath)) -PathType Container
$serviceDir = Join-Path $suite "service"
$serviceXmlPath = Join-Path $serviceDir "DStockTelegramCommandBot.xml"
$wrapperPath = Join-Path $serviceDir "DStockTelegramCommandBot.exe"
$wrapperShaPath = Join-Path $serviceDir "DStockTelegramCommandBot.sha256"
$listenerPath = Join-Path $suite "telegram_stock_command_bot.py"
$ownerPath = "D:\TWStockData\services\telegram\data\stock_command_bot.owner.json"
$heartbeatPath = "D:\TWStockData\services\telegram\data\stock_command_bot.heartbeat.json"
$projectRoot = Get-CanonicalExactPath -Path (Join-Path $suite "..\..") -PathType Container
$expectedPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$expectedArguments = '"{0}"' -f $listenerPath
    $liveStatuses = @("booting", "running", "polling", "working")
$allowedClockSkewSeconds = 5
$allowedClockSkewTicks = [TimeSpan]::FromSeconds($allowedClockSkewSeconds).Ticks

function Get-VenvInterpreterContract {
    $venvConfigPath = Join-Path $projectRoot ".venv\pyvenv.cfg"
    $canonicalVenvConfigPath = Get-CanonicalExactPath -Path $venvConfigPath -PathType Leaf
    try {
        $configText = Get-Content -LiteralPath $canonicalVenvConfigPath -Raw -Encoding ASCII -ErrorAction Stop
    } catch {
        throw "Venv pyvenv.cfg cannot be read: $canonicalVenvConfigPath"
    }
    $homeMatches = [regex]::Matches($configText, '(?m)^\s*home\s*=\s*(?<value>[^\r\n]+)\s*$')
    $executableMatches = [regex]::Matches($configText, '(?m)^\s*executable\s*=\s*(?<value>[^\r\n]+)\s*$')
    if ($homeMatches.Count -ne 1 -or $executableMatches.Count -ne 1) {
        throw "Venv pyvenv.cfg must contain exactly one home and executable entry."
    }
    $homeValue = $homeMatches[0].Groups["value"].Value.Trim()
    $executableValue = $executableMatches[0].Groups["value"].Value.Trim()
    $canonicalHomePath = Get-CanonicalExactPath -Path $homeValue -PathType Container
    $canonicalBasePythonPath = Get-CanonicalExactPath -Path $executableValue -PathType Leaf
    $canonicalBaseDirectory = Get-CanonicalExactPath `
        -Path ([IO.Path]::GetDirectoryName($canonicalBasePythonPath)) `
        -PathType Container
    if (-not [System.String]::Equals(
            $canonicalBaseDirectory,
            $canonicalHomePath,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "Venv base interpreter directory does not match pyvenv.cfg home."
    }
    $basePythonIdentity = Get-FileIdentity -Path $canonicalBasePythonPath
    $configIdentity = Get-FileIdentity -Path $canonicalVenvConfigPath
    [pscustomobject]@{
        ConfigPath = $canonicalVenvConfigPath
        ConfigFileId = $configIdentity.FileId
        ConfigSize = $configIdentity.Size
        ConfigSHA256 = $configIdentity.SHA256
        HomePath = $canonicalHomePath
        BasePythonPath = $canonicalBasePythonPath
        BasePythonFileId = $basePythonIdentity.FileId
        BasePythonSize = $basePythonIdentity.Size
        BasePythonSHA256 = $basePythonIdentity.SHA256
    }
}

function Assert-ProcessExecutableAndCommandLine {
    param(
        [Parameter(Mandatory)]$Process,
        [Parameter(Mandatory)][string]$ExpectedPath,
        [Parameter(Mandatory)][string[]]$ExpectedCommandLines,
        [Parameter(Mandatory)][string]$Role,
        [switch]$RequireCommandLine
    )
    $observedPath = [string]$Process.ExecutablePath
    if ([string]::IsNullOrWhiteSpace($observedPath)) {
        throw "$Role executable path is empty."
    }
    try {
        $canonicalObservedPath = Get-CanonicalExactPath -Path $observedPath -PathType Leaf
        $canonicalExpectedPath = Get-CanonicalExactPath -Path $ExpectedPath -PathType Leaf
    } catch {
        throw "$Role executable path is not canonical."
    }
    if (-not [System.String]::Equals(
            $canonicalObservedPath,
            $canonicalExpectedPath,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "$Role executable path identity mismatch."
    }
    $commandLine = [string]$Process.CommandLine
    if ($RequireCommandLine -or -not [string]::IsNullOrWhiteSpace($commandLine)) {
        if ([string]::IsNullOrWhiteSpace($commandLine)) {
            throw "$Role command line is empty."
        }
        $commandLineMatch = $false
        foreach ($candidate in @($ExpectedCommandLines)) {
            if ([System.String]::Equals(
                    $commandLine,
                    [string]$candidate,
                    [System.StringComparison]::OrdinalIgnoreCase
                )) {
                $commandLineMatch = $true
                break
            }
        }
        if (-not $commandLineMatch) { throw "$Role command line identity mismatch." }
    }
    [pscustomobject]@{
        ExecutablePath = $canonicalObservedPath
        CommandLine = $commandLine
    }
}

function Get-ProcessLineageToService {
    param(
        [Parameter(Mandatory)][int]$OwnerProcessId,
        [Parameter(Mandatory)][int]$ServiceProcessId,
        [Parameter(Mandatory)]$ExpectedOwnerProcess,
        [Parameter(Mandatory)]$ExpectedServiceProcess,
        [Parameter(Mandatory)][string]$ExpectedLauncherPath,
        [Parameter(Mandatory)][string[]]$ExpectedLauncherCommandLines,
        [Parameter(Mandatory)][string]$ExpectedBaseInterpreterPath,
        [Parameter(Mandatory)][string[]]$ExpectedBaseInterpreterCommandLines
    )
    if ($OwnerProcessId -le 0 -or $ServiceProcessId -le 0 -or $OwnerProcessId -eq $ServiceProcessId) {
        throw "Owner/service PID lineage is invalid."
    }
    $expectedOwnerPid = 0
    $expectedServicePid = 0
    if (-not [int]::TryParse([string]$ExpectedOwnerProcess.ProcessId, [ref]$expectedOwnerPid) -or
        -not [int]::TryParse([string]$ExpectedServiceProcess.ProcessId, [ref]$expectedServicePid) -or
        $expectedOwnerPid -ne $OwnerProcessId -or $expectedServicePid -ne $ServiceProcessId) {
        throw "Expected process instance PID binding is invalid."
    }
    $serviceEndpoint = @(Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction Stop)
    if ($serviceEndpoint.Count -ne 1 -or
        -not [System.String]::Equals([string]$serviceEndpoint[0].Name, $serviceName, [System.StringComparison]::Ordinal) -or
        -not [System.String]::Equals([string]$serviceEndpoint[0].State, "Running", [System.StringComparison]::Ordinal) -or
        -not [System.String]::Equals([string]$serviceEndpoint[0].StartName, "LocalSystem", [System.StringComparison]::Ordinal) -or
        -not [System.String]::Equals(([string]$serviceEndpoint[0].PathName).Trim(), $wrapperPath, [System.StringComparison]::OrdinalIgnoreCase) -and
        -not [System.String]::Equals(([string]$serviceEndpoint[0].PathName).Trim(), ('"' + $wrapperPath + '"'), [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Service endpoint identity changed during process lineage verification."
    }
    $endpointPid = 0
    if (-not [int]::TryParse([string]$serviceEndpoint[0].ProcessId, [ref]$endpointPid) -or $endpointPid -ne $ServiceProcessId) {
        throw "Service endpoint PID identity changed during process lineage verification."
    }
    $lineage = @()
    $ownerProcess = Get-ProcessSnapshot -ProcessId $OwnerProcessId
    if ($null -eq $ownerProcess) { throw "Owner process is not alive during lineage verification." }
    [void](Assert-ProcessInstanceIdentity -Expected $ExpectedOwnerProcess -Observed $ownerProcess -Role "Owner")
    if (-not [System.String]::Equals([string]$ownerProcess.Name, "python.exe", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Owner executable name mismatch."
    }
    $ownerParentPid = 0
    if (-not [int]::TryParse([string]$ownerProcess.ParentProcessId, [ref]$ownerParentPid) -or $ownerParentPid -le 0) {
        throw "Owner process parent PID is invalid."
    }
    $lineage += $ownerProcess
    if ($ownerParentPid -ne $ServiceProcessId) {
        $launcherProcess = Get-ProcessSnapshot -ProcessId $ownerParentPid
        if ($null -eq $launcherProcess) { throw "Venv launcher process is not alive during lineage verification." }
        $launcherPid = 0
        if (-not [int]::TryParse([string]$launcherProcess.ProcessId, [ref]$launcherPid) -or $launcherPid -ne $ownerParentPid) {
            throw "Venv launcher PID identity is invalid."
        }
        if ($launcherPid -eq $OwnerProcessId -or $launcherPid -eq $ServiceProcessId) {
            throw "Venv launcher PID is not a distinct process instance."
        }
        if (-not [System.String]::Equals([string]$launcherProcess.Name, "python.exe", [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Venv launcher executable name mismatch."
        }
        $launcherParentPid = 0
        if (-not [int]::TryParse([string]$launcherProcess.ParentProcessId, [ref]$launcherParentPid) -or
            $launcherParentPid -ne $ServiceProcessId) {
            throw "Venv launcher is not a direct child of the service endpoint."
        }
        [void](Assert-ProcessExecutableAndCommandLine `
            -Process $launcherProcess `
            -ExpectedPath $ExpectedLauncherPath `
            -ExpectedCommandLines $ExpectedLauncherCommandLines `
            -Role "Venv launcher" `
            -RequireCommandLine)
        [void](Assert-ProcessExecutableAndCommandLine `
            -Process $ownerProcess `
            -ExpectedPath $ExpectedBaseInterpreterPath `
            -ExpectedCommandLines $ExpectedBaseInterpreterCommandLines `
            -Role "Base interpreter owner" `
            -RequireCommandLine)
        $serviceCreationUtc = Get-NormalizedCimCreationDate -Process $ExpectedServiceProcess
        $launcherCreationUtc = Get-NormalizedCimCreationDate -Process $launcherProcess
        $ownerCreationUtc = Get-NormalizedCimCreationDate -Process $ownerProcess
        if ($serviceCreationUtc.UtcTicks -gt $launcherCreationUtc.UtcTicks -or
            $launcherCreationUtc.UtcTicks -gt $ownerCreationUtc.UtcTicks) {
            throw "Venv launcher lineage creation UTC ticks are not monotonic."
        }
        $lineage += $launcherProcess
    } else {
        $launcherProcess = $null
    }
    $serviceProcess = Get-ProcessSnapshot -ProcessId $ServiceProcessId
    if ($null -eq $serviceProcess) { throw "Service process is not alive during lineage verification." }
    [void](Assert-ProcessInstanceIdentity -Expected $ExpectedServiceProcess -Observed $serviceProcess -Role "Service")
    $lineage += $serviceProcess
    if ($lineage.Count -ne 2 -and $lineage.Count -ne 3) {
        throw "Fixed process lineage contains an unsupported number of processes."
    }
    $finalServiceEndpoint = @(Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction Stop)
    if ($finalServiceEndpoint.Count -ne 1 -or
        -not [System.String]::Equals([string]$finalServiceEndpoint[0].Name, $serviceName, [System.StringComparison]::Ordinal) -or
        -not [System.String]::Equals([string]$finalServiceEndpoint[0].State, "Running", [System.StringComparison]::Ordinal) -or
        -not [System.String]::Equals([string]$finalServiceEndpoint[0].StartName, "LocalSystem", [System.StringComparison]::Ordinal) -or
        -not [System.String]::Equals(([string]$finalServiceEndpoint[0].PathName).Trim(), $wrapperPath, [System.StringComparison]::OrdinalIgnoreCase) -and
        -not [System.String]::Equals(([string]$finalServiceEndpoint[0].PathName).Trim(), ('"' + $wrapperPath + '"'), [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Service endpoint identity changed during process lineage verification."
    }
    $finalEndpointPid = 0
    if (-not [int]::TryParse([string]$finalServiceEndpoint[0].ProcessId, [ref]$finalEndpointPid) -or $finalEndpointPid -ne $ServiceProcessId) {
        throw "Service endpoint PID identity changed during process lineage verification."
    }
    return @($lineage)
}

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Quote-AbsolutePath {
    param([Parameter(Mandatory)][string]$Path)
    $resolvedPath = Get-CanonicalExactPath -Path $Path -PathType Leaf
    if (-not $resolvedPath.EndsWith(".ps1", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Script path is not a PowerShell script."
    }
    return '"' + $resolvedPath + '"'
}

function Assert-ServiceContract {
    $canonicalServiceXmlPath = Get-CanonicalExactPath -Path $serviceXmlPath -PathType Leaf
    $canonicalWrapperShaPath = Get-CanonicalExactPath -Path $wrapperShaPath -PathType Leaf
    $canonicalWrapperPath = Get-CanonicalExactPath -Path $wrapperPath -PathType Leaf
    $canonicalListenerPath = Get-CanonicalExactPath -Path $listenerPath -PathType Leaf
    $canonicalPythonPath = Get-CanonicalExactPath -Path $expectedPython -PathType Leaf
    $venvInterpreter = Get-VenvInterpreterContract
    $xml = [xml](Get-Content -LiteralPath $canonicalServiceXmlPath -Raw -Encoding UTF8)
    $configuredExecutable = [string]$xml.service.executable
    $configuredArguments = [string]$xml.service.arguments
    $configuredWorkingDirectory = [string]$xml.service.workingdirectory
    $configuredAccount = [string]$xml.service.serviceaccount.user
    if (-not [System.String]::Equals([string]$xml.service.id, $serviceName, [System.StringComparison]::Ordinal)) { throw "Service XML id mismatch." }
    if (-not [System.String]::Equals($configuredExecutable, $canonicalPythonPath, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Service XML executable mismatch." }
    if (-not [System.String]::Equals($configuredArguments, $expectedArguments, [System.StringComparison]::Ordinal)) { throw "Service XML arguments mismatch." }
    if (-not [System.String]::Equals($configuredWorkingDirectory, $suite, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Service XML working directory mismatch." }
    if (-not [System.String]::Equals($configuredAccount, "LocalSystem", [System.StringComparison]::Ordinal)) { throw "Service XML account mismatch." }
    $expectedWrapperSha = (Get-Content -LiteralPath $canonicalWrapperShaPath -Raw -Encoding ASCII).Trim().Split()[0].ToLowerInvariant()
    if ($expectedWrapperSha -notmatch '^[0-9a-f]{64}$') { throw "WinSW wrapper SHA declaration is invalid." }
    $actualWrapperSha = Get-FileSha256 -Path $canonicalWrapperPath
    if ($expectedWrapperSha -ne $actualWrapperSha) { throw "WinSW wrapper SHA mismatch." }
    $pythonIdentity = Get-FileIdentity -Path $canonicalPythonPath
    $listenerIdentity = Get-FileIdentity -Path $canonicalListenerPath
    if ([int64]$listenerIdentity.Size -le 0) { throw "Current listener source file is empty." }
    $service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction Stop
    if ($null -eq $service) { throw "Service is not installed: $serviceName" }
    if (-not [System.String]::Equals([string]$service.StartName, "LocalSystem", [System.StringComparison]::Ordinal)) { throw "Installed service account mismatch." }
    $servicePathName = ([string]$service.PathName).Trim()
    $servicePathMatches = [System.String]::Equals($servicePathName, $wrapperPath, [System.StringComparison]::OrdinalIgnoreCase) -or
        [System.String]::Equals($servicePathName, ('"' + $wrapperPath + '"'), [System.StringComparison]::OrdinalIgnoreCase)
    if (-not $servicePathMatches) { throw "Installed service executable/config mismatch." }
    $expectedCommandLine = $expectedPython + " " + $expectedArguments
    $quotedExpectedCommandLine = '"' + $expectedPython + '" ' + $expectedArguments
    $baseExpectedCommandLine = [string]$venvInterpreter.BasePythonPath + " " + $expectedArguments
    $quotedBaseExpectedCommandLine = '"' + [string]$venvInterpreter.BasePythonPath + '" ' + $expectedArguments
    [pscustomobject]@{
        Service = $service
        ServiceXmlSHA256 = Get-FileSha256 -Path $serviceXmlPath
        WrapperSHA256 = $actualWrapperSha
        ListenerPath = $listenerIdentity.Path
        ListenerFileId = $listenerIdentity.FileId
        ListenerSize = $listenerIdentity.Size
        ListenerSHA256 = $listenerIdentity.SHA256
        PythonPath = $canonicalPythonPath
        PythonFileId = $pythonIdentity.FileId
        PythonSHA256 = $pythonIdentity.SHA256
        VenvConfigPath = $venvInterpreter.ConfigPath
        VenvConfigFileId = $venvInterpreter.ConfigFileId
        VenvConfigSize = $venvInterpreter.ConfigSize
        VenvConfigSHA256 = $venvInterpreter.ConfigSHA256
        VenvHomePath = $venvInterpreter.HomePath
        BasePythonPath = $venvInterpreter.BasePythonPath
        BasePythonFileId = $venvInterpreter.BasePythonFileId
        BasePythonSize = $venvInterpreter.BasePythonSize
        BasePythonSHA256 = $venvInterpreter.BasePythonSHA256
        XmlExecutable = $configuredExecutable
        XmlArguments = $configuredArguments
        XmlWorkingDirectory = $configuredWorkingDirectory
        XmlServiceAccount = $configuredAccount
        ExpectedCommandLine = $expectedCommandLine
        ExpectedCommandLines = @($expectedCommandLine, $quotedExpectedCommandLine)
        BaseExpectedCommandLines = @($baseExpectedCommandLine, $quotedBaseExpectedCommandLine)
    }
}

function Get-ResidentPropertyValue {
    param(
        [Parameter(Mandatory)]$Object,
        [Parameter(Mandatory)][string]$Name
    )
    if ($Object -is [System.Collections.IDictionary]) {
        if ($Object.Keys -notcontains $Name) { return $null }
        return $Object[$Name]
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Get-ResidentEvidence {
    param(
        [Parameter(Mandatory)]$Contract,
        [string]$OldOwnerPid = "",
        [string]$OldOwnerCreationDate = "",
        [string]$OldHeartbeatStartedAt = "",
        [string]$OldHeartbeatUpdatedAt = "",
        [string]$RestartBoundary = "",
        [switch]$AllowSourceMismatch,
        [switch]$AllowLegacyOwnerStartedAt,
        [switch]$SkipFinalResnapshot
    )
    $service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction Stop
    if ($null -eq $service) { throw "Service is not installed: $serviceName" }
    $servicePid = 0
    $servicePathName = ([string]$service.PathName).Trim()
    $servicePathEvidence = [System.String]::Equals($servicePathName, $wrapperPath, [System.StringComparison]::OrdinalIgnoreCase) -or
        [System.String]::Equals($servicePathName, ('"' + $wrapperPath + '"'), [System.StringComparison]::OrdinalIgnoreCase)
    $serviceFixedEvidence =
        [System.String]::Equals([string]$service.Name, $serviceName, [System.StringComparison]::Ordinal) -and
        [System.String]::Equals([string]$service.State, "Running", [System.StringComparison]::Ordinal) -and
        [System.String]::Equals([string]$service.StartName, "LocalSystem", [System.StringComparison]::Ordinal) -and
        $servicePathEvidence -and
        [int]::TryParse([string]$service.ProcessId, [ref]$servicePid) -and
        $servicePid -gt 0
    if (-not $serviceFixedEvidence) { throw "Fixed LocalSystem service PID/account evidence is invalid." }
    $serviceProcess = Get-ProcessSnapshot -ProcessId $servicePid
    if ($null -eq $serviceProcess) { throw "Fixed service PID is not alive." }
    $serviceExecutablePath = [string]$serviceProcess.ExecutablePath
    if ([string]::IsNullOrWhiteSpace($serviceExecutablePath)) {
        throw "Fixed service PID executable path is empty."
    }
    try {
        $serviceExecutablePath = Get-CanonicalExactPath -Path $serviceExecutablePath -PathType Leaf
        $canonicalWrapperPath = Get-CanonicalExactPath -Path $wrapperPath -PathType Leaf
    } catch {
        throw "Fixed service PID executable path is not canonical."
    }
    if (-not [System.String]::Equals(
            $serviceExecutablePath,
            $canonicalWrapperPath,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "Fixed service PID executable path mismatch."
    }
    $serviceCommandLine = [string]$serviceProcess.CommandLine
    if (-not [string]::IsNullOrWhiteSpace($serviceCommandLine)) {
        $serviceCommandLineMatch = [System.String]::Equals($serviceCommandLine, $wrapperPath, [System.StringComparison]::OrdinalIgnoreCase) -or
            [System.String]::Equals($serviceCommandLine, ('"' + $wrapperPath + '"'), [System.StringComparison]::OrdinalIgnoreCase)
        if (-not $serviceCommandLineMatch) { throw "Fixed service PID command line mismatch." }
    }

    $owner = Get-ResidentJson -Path $ownerPath
    $heartbeat = Get-ResidentJson -Path $heartbeatPath
    $ownerPid = 0
    if (-not [int]::TryParse([string]$owner.processId, [ref]$ownerPid) -or $ownerPid -le 0) { throw "Owner PID is invalid." }
    $heartbeatPid = 0
    if (-not [int]::TryParse([string]$heartbeat.pid, [ref]$heartbeatPid) -or $heartbeatPid -le 0) { throw "Heartbeat PID is invalid." }
    if ($heartbeatPid -ne $ownerPid) { throw "Owner and heartbeat PID mismatch." }
    if ($ownerPid -eq $servicePid) { throw "Owner PID cannot be the fixed service PID." }
    $process = Get-ProcessSnapshot -ProcessId $ownerPid
    if ($null -eq $process) { throw "Owner process is not alive." }
    if (-not [System.String]::Equals([string]$process.Name, "python.exe", [System.StringComparison]::OrdinalIgnoreCase)) { throw "Owner executable name mismatch." }
    # A pre-restart legacy owner.startedAt is ambiguous and is never parsed or emitted.
    $ownerStartedAtValue = Get-ResidentPropertyValue -Object $owner -Name "startedAt"
    $heartbeatStartedAtValue = Get-ResidentPropertyValue -Object $heartbeat -Name "startedAt"
    $verificationTimeUtc = [DateTimeOffset]::UtcNow
    $verificationTimeUtcTicks = $verificationTimeUtc.UtcTicks
    $restartBoundaryUtc = $null
    $restartBoundaryUtcTicks = $null
    if (-not [string]::IsNullOrWhiteSpace($RestartBoundary)) {
        if ($RestartBoundary -notmatch '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,7})?Z$') {
            throw "Restart boundary must be a strict UTC Z timestamp."
        }
        try {
            $restartBoundaryUtc = ConvertTo-UtcDateTimeOffset -Value $RestartBoundary
            $restartBoundaryUtcTicks = $restartBoundaryUtc.UtcTicks
        } catch {
            throw "Restart boundary timestamp is invalid."
        }
    }

    $heartbeatStartedAtUtc = $null
    $heartbeatStartedAtCanonical = $null
    try {
        $heartbeatUpdatedAtUtc = ConvertTo-UtcDateTimeOffset -Value $heartbeat.updatedAt
        $heartbeatUpdatedAtUtcTicks = $heartbeatUpdatedAtUtc.UtcTicks
        $processCreatedAtUtc = Get-NormalizedCimCreationDate -Process $process
        if ($null -ne $heartbeatStartedAtValue) {
            $heartbeatStartedAtUtc = ConvertTo-UtcDateTimeOffset -Value $heartbeatStartedAtValue
            $heartbeatStartedAtCanonical = ConvertTo-CanonicalUtcTimestamp -Value $heartbeatStartedAtUtc
        }
    } catch {
        throw "Heartbeat/CIM process creation timestamp evidence is invalid."
    }
    if ($heartbeatUpdatedAtUtcTicks -gt $verificationTimeUtcTicks) {
        throw "Heartbeat updatedAt is later than the verification time."
    }
    $ownerStartedAtUtc = $null
    $ownerStartedAtCanonical = $null
    $ownerStartedAtAuthority = "ignored_untrusted_ambiguous"
    if (-not $AllowLegacyOwnerStartedAt) {
        if ($ownerStartedAtValue -isnot [string]) { throw "Owner startedAt is missing or ambiguous." }
        try {
            $ownerStartedAtUtc = ConvertTo-UtcDateTimeOffset -Value $ownerStartedAtValue
            $ownerStartedAtCanonical = ConvertTo-CanonicalUtcTimestamp -Value $ownerStartedAtUtc
        } catch {
            throw "Owner startedAt is missing or ambiguous."
        }
        if ([math]::Abs([double]($processCreatedAtUtc.UtcTicks - $ownerStartedAtUtc.UtcTicks)) -gt $allowedClockSkewTicks) {
            throw "Owner startedAt does not match CIM process CreationDate."
        }
        $ownerStartedAtAuthority = "strict_authoritative"
    }

    $ownerProcessIdentityEvidence = $false
    if ($null -ne $ownerStartedAtUtc) {
        $ownerProcessIdentityEvidence = [math]::Abs([double]($processCreatedAtUtc.UtcTicks - $ownerStartedAtUtc.UtcTicks)) -le $allowedClockSkewTicks
        if (-not $ownerProcessIdentityEvidence) { throw "Owner startedAt does not match CIM process CreationDate." }
    }
    if ($null -ne $heartbeatStartedAtUtc) {
        $creationIdentityEvidence = [math]::Abs([double]($processCreatedAtUtc.UtcTicks - $heartbeatStartedAtUtc.UtcTicks)) -le $allowedClockSkewTicks
        if (-not $creationIdentityEvidence) { throw "Owner PID creation identity does not match strict heartbeat startedAt." }
    } elseif ($AllowLegacyOwnerStartedAt) {
        throw "Heartbeat startedAt is required while owner startedAt is legacy/ambiguous."
    } else {
        # No heartbeat startedAt is fabricated; strict owner.startedAt plus CIM is the identity gate.
        $creationIdentityEvidence = $ownerProcessIdentityEvidence
        if (-not $creationIdentityEvidence) { throw "Owner/CIM process creation identity is unavailable." }
    }

    if ($processCreatedAtUtc.UtcTicks -gt $verificationTimeUtcTicks -or
        ($null -ne $ownerStartedAtUtc -and $ownerStartedAtUtc.UtcTicks -gt $verificationTimeUtcTicks) -or
        ($null -ne $heartbeatStartedAtUtc -and $heartbeatStartedAtUtc.UtcTicks -gt $verificationTimeUtcTicks)) {
        throw "Process identity timestamp is later than the verification time."
    }
    if ($null -ne $restartBoundaryUtc) {
        if ($restartBoundaryUtcTicks -gt $verificationTimeUtcTicks) { throw "Restart boundary is later than the verification time." }
        $restartFloorUtcTicks = $restartBoundaryUtcTicks - $allowedClockSkewTicks
        if ($processCreatedAtUtc.UtcTicks -lt $restartFloorUtcTicks -or
            $null -eq $ownerStartedAtUtc -or $ownerStartedAtUtc.UtcTicks -lt $restartFloorUtcTicks -or
            ($null -ne $heartbeatStartedAtUtc -and $heartbeatStartedAtUtc.UtcTicks -lt $restartFloorUtcTicks) -or
            $heartbeatUpdatedAtUtcTicks -lt $restartFloorUtcTicks) {
            throw "Post-restart process identity timestamp is earlier than the restart boundary."
        }
    }

    $lineage = @(Get-ProcessLineageToService `
        -OwnerProcessId $ownerPid `
        -ServiceProcessId $servicePid `
        -ExpectedOwnerProcess $process `
        -ExpectedServiceProcess $serviceProcess `
        -ExpectedLauncherPath ([string]$Contract.PythonPath) `
        -ExpectedLauncherCommandLines @($Contract.ExpectedCommandLines) `
        -ExpectedBaseInterpreterPath ([string]$Contract.BasePythonPath) `
        -ExpectedBaseInterpreterCommandLines @($Contract.BaseExpectedCommandLines))
    $parentTreeEvidence = ($lineage.Count -eq 2 -or $lineage.Count -eq 3) -and
        [int]$lineage[0].ProcessId -eq $ownerPid -and
        [int]$lineage[$lineage.Count - 1].ProcessId -eq $servicePid
    if (-not $parentTreeEvidence) { throw "Owner parent-child tree is incomplete or does not reach the service PID." }
    $lineageMode = if ($lineage.Count -eq 3) { "venv_launcher" } else { "direct" }
    $lineageCreationUtcTicks = (@(
        foreach ($lineageProcess in $lineage) {
            (Get-NormalizedCimCreationDate -Process $lineageProcess).UtcTicks
        }
    ) -join "->")
    $launcherPid = if ($lineage.Count -eq 3) { [int]$lineage[1].ProcessId } else { $null }
    $launcherCreationDate = if ($lineage.Count -eq 3) {
        ConvertTo-CanonicalUtcTimestamp -Value (Get-NormalizedCimCreationDate -Process $lineage[1])
    } else { $null }
    $launcherExecutablePath = if ($lineage.Count -eq 3) {
        Get-CanonicalExactPath -Path ([string]$lineage[1].ExecutablePath) -PathType Leaf
    } else { $null }
    $launcherCommandLine = if ($lineage.Count -eq 3) { [string]$lineage[1].CommandLine } else { $null }
    $expectedOwnerExecutablePath = if ($lineageMode -eq "venv_launcher") {
        [string]$Contract.BasePythonPath
    } else {
        [string]$Contract.PythonPath
    }
    $expectedOwnerCommandLines = if ($lineageMode -eq "venv_launcher") {
        @($Contract.BaseExpectedCommandLines)
    } else {
        @($Contract.ExpectedCommandLines)
    }

    $pythonIdentity = Get-FileIdentity -Path $expectedPython
    $pythonFileEvidence =
        [System.String]::Equals($pythonIdentity.Path, [string]$Contract.PythonPath, [System.StringComparison]::OrdinalIgnoreCase) -and
        [System.String]::Equals($pythonIdentity.FileId, [string]$Contract.PythonFileId, [System.StringComparison]::OrdinalIgnoreCase) -and
        [System.String]::Equals($pythonIdentity.SHA256, [string]$Contract.PythonSHA256, [System.StringComparison]::OrdinalIgnoreCase)
    if (-not $pythonFileEvidence) { throw "Fixed Python path file identity/hash changed." }

    $venvConfigIdentity = Get-FileIdentity -Path ([string]$Contract.VenvConfigPath)
    $venvConfigFileEvidence =
        [System.String]::Equals($venvConfigIdentity.Path, [string]$Contract.VenvConfigPath, [System.StringComparison]::OrdinalIgnoreCase) -and
        [System.String]::Equals($venvConfigIdentity.FileId, [string]$Contract.VenvConfigFileId, [System.StringComparison]::OrdinalIgnoreCase) -and
        [int64]$venvConfigIdentity.Size -eq [int64]$Contract.VenvConfigSize -and
        [System.String]::Equals($venvConfigIdentity.SHA256, [string]$Contract.VenvConfigSHA256, [System.StringComparison]::OrdinalIgnoreCase)
    if (-not $venvConfigFileEvidence) { throw "Venv pyvenv.cfg identity/hash changed." }

    $basePythonIdentity = Get-FileIdentity -Path ([string]$Contract.BasePythonPath)
    $basePythonFileEvidence =
        [System.String]::Equals($basePythonIdentity.Path, [string]$Contract.BasePythonPath, [System.StringComparison]::OrdinalIgnoreCase) -and
        [System.String]::Equals($basePythonIdentity.FileId, [string]$Contract.BasePythonFileId, [System.StringComparison]::OrdinalIgnoreCase) -and
        [int64]$basePythonIdentity.Size -eq [int64]$Contract.BasePythonSize -and
        [System.String]::Equals($basePythonIdentity.SHA256, [string]$Contract.BasePythonSHA256, [System.StringComparison]::OrdinalIgnoreCase)
    if (-not $basePythonFileEvidence) { throw "Venv base interpreter identity/hash changed." }

    $wrapperSha = Get-FileSha256 -Path $wrapperPath
    $wrapperHashEvidence = [System.String]::Equals($wrapperSha, [string]$Contract.WrapperSHA256, [System.StringComparison]::OrdinalIgnoreCase)
    if (-not $wrapperHashEvidence) { throw "WinSW wrapper hash changed." }

    $xmlEvidence =
        [System.String]::Equals([string]$Contract.XmlExecutable, $expectedPython, [System.StringComparison]::OrdinalIgnoreCase) -and
        [System.String]::Equals([string]$Contract.XmlArguments, $expectedArguments, [System.StringComparison]::Ordinal) -and
        [System.String]::Equals([string]$Contract.XmlWorkingDirectory, $suite, [System.StringComparison]::OrdinalIgnoreCase) -and
        [System.String]::Equals([string]$Contract.XmlServiceAccount, "LocalSystem", [System.StringComparison]::Ordinal)
    if (-not $xmlEvidence) { throw "WinSW XML executable/arguments/workingdirectory/account evidence is invalid." }

    $diskSourceIdentityBefore = Get-FileIdentity -Path $listenerPath
    if ([int64]$diskSourceIdentityBefore.Size -le 0) { throw "Current listener source file is empty." }
    $diskSourceIdentityAfter = Get-FileIdentity -Path $listenerPath
    if ([int64]$diskSourceIdentityAfter.Size -le 0) { throw "Current listener source file is empty." }
    $diskSourceIdentityStable =
        [System.String]::Equals($diskSourceIdentityBefore.Path, $diskSourceIdentityAfter.Path, [System.StringComparison]::OrdinalIgnoreCase) -and
        [System.String]::Equals($diskSourceIdentityBefore.FileId, $diskSourceIdentityAfter.FileId, [System.StringComparison]::OrdinalIgnoreCase) -and
        [int64]$diskSourceIdentityBefore.Size -eq [int64]$diskSourceIdentityAfter.Size -and
        [System.String]::Equals($diskSourceIdentityBefore.SHA256, $diskSourceIdentityAfter.SHA256, [System.StringComparison]::OrdinalIgnoreCase)
    if (-not $diskSourceIdentityStable) { throw "Current listener source identity/hash/size changed during verification." }
    $currentDiskSourceIdentity = $diskSourceIdentityAfter
    $diskSourceEvidence =
        [System.String]::Equals($currentDiskSourceIdentity.Path, [string]$Contract.ListenerPath, [System.StringComparison]::OrdinalIgnoreCase) -and
        [System.String]::Equals($currentDiskSourceIdentity.FileId, [string]$Contract.ListenerFileId, [System.StringComparison]::OrdinalIgnoreCase) -and
        [int64]$currentDiskSourceIdentity.Size -eq [int64]$Contract.ListenerSize -and
        [System.String]::Equals($currentDiskSourceIdentity.SHA256, [string]$Contract.ListenerSHA256, [System.StringComparison]::OrdinalIgnoreCase)
    if (-not $diskSourceEvidence) { throw "Current listener source SHA changed during verification." }
    $ownerSourceSha = [string]$owner.sourceSha256
    $heartbeatSourceSha = [string]$heartbeat.sourceSha256
    $residentSourceEvidence =
        $ownerSourceSha -match '^[0-9a-fA-F]{64}$' -and
        $heartbeatSourceSha -match '^[0-9a-fA-F]{64}$' -and
        [System.String]::Equals($ownerSourceSha, $heartbeatSourceSha, [System.StringComparison]::OrdinalIgnoreCase)
    if (-not $residentSourceEvidence) { throw "Owner/heartbeat source SHA evidence is invalid." }
    $currentSourceEvidence = [System.String]::Equals($heartbeatSourceSha, $currentDiskSourceIdentity.SHA256, [System.StringComparison]::OrdinalIgnoreCase)
    $sourceMismatch = -not $currentSourceEvidence
    if (-not $AllowSourceMismatch -and $sourceMismatch) { throw "Loaded listener source SHA mismatch." }

    $heartbeatAgeSeconds = [math]::Round(
        ($verificationTimeUtcTicks - $heartbeatUpdatedAtUtcTicks) / [double][TimeSpan]::TicksPerSecond,
        1
    )
    $heartbeatFreshLowerBoundUtcTicks = $verificationTimeUtcTicks - [TimeSpan]::FromSeconds(180).Ticks
    $heartbeatFresh = $heartbeatUpdatedAtUtcTicks -ge $heartbeatFreshLowerBoundUtcTicks -and
        $heartbeatUpdatedAtUtcTicks -le $verificationTimeUtcTicks
    $heartbeatPollingEvidence = [string]$heartbeat.status -in $liveStatuses
    $heartbeatSourceEvidence = if ($AllowSourceMismatch) { $residentSourceEvidence } else { $currentSourceEvidence }
    $heartbeatEvidence = $heartbeatPollingEvidence -and $heartbeatFresh -and $heartbeatSourceEvidence
    if (-not $heartbeatEvidence) { throw "Heartbeat is missing, stale, non-live, or source-inconsistent." }

    $executablePath = [string]$process.ExecutablePath
    if ([string]::IsNullOrWhiteSpace($executablePath)) {
        throw "Owner CIM executable path is empty."
    }
    try {
        $executablePath = Get-CanonicalExactPath -Path $executablePath -PathType Leaf
        $canonicalExpectedOwnerPath = Get-CanonicalExactPath -Path $expectedOwnerExecutablePath -PathType Leaf
    } catch {
        throw "Owner CIM executable path is not canonical."
    }
    if (-not [System.String]::Equals(
            $executablePath,
            $canonicalExpectedOwnerPath,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "Owner executable path mismatch."
    }
    $commandLine = [string]$process.CommandLine
    $metadataMode = "explicit"
    if (-not [string]::IsNullOrWhiteSpace($commandLine)) {
        $commandLineMatch = $false
        foreach ($candidate in @($expectedOwnerCommandLines)) {
            if ([System.String]::Equals($commandLine, [string]$candidate, [System.StringComparison]::OrdinalIgnoreCase)) {
                $commandLineMatch = $true
                break
            }
        }
        if (-not $commandLineMatch) { throw "Owner command line mismatch." }
    }

    if ($OldOwnerCreationDate) {
        try {
            $oldCreationDateUtcTicks = (ConvertTo-UtcDateTimeOffset -Value $OldOwnerCreationDate).UtcTicks
            $creationIdentityToleranceTicks = [TimeSpan]::FromMilliseconds(1).Ticks
            if ([math]::Abs([double]($processCreatedAtUtc.UtcTicks - $oldCreationDateUtcTicks)) -le $creationIdentityToleranceTicks) { throw "Restart reused the previous owner creation identity." }
        } catch {
            if ($_.Exception.Message -like "Restart reused*") { throw }
            throw "New owner creation identity is unavailable."
        }
    }
    if ($OldOwnerPid -and [string]$owner.processId -eq $OldOwnerPid) { throw "Restart did not create a new owner PID." }
    if ($OldHeartbeatStartedAt) {
        $oldHeartbeatStartedAtUtc = ConvertTo-UtcDateTimeOffset -Value $OldHeartbeatStartedAt
        if ($null -ne $heartbeatStartedAtUtc -and $heartbeatStartedAtUtc.UtcTicks -eq $oldHeartbeatStartedAtUtc.UtcTicks) { throw "Restart did not publish a new heartbeat identity." }
        if ($null -eq $heartbeatStartedAtUtc) {
            if (-not $OldOwnerCreationDate) { throw "Restart did not publish a new owner/CIM identity for the heartbeat." }
            $oldHeartbeatFallbackCreationDateUtc = ConvertTo-UtcDateTimeOffset -Value $OldOwnerCreationDate
            if ($processCreatedAtUtc.UtcTicks -le $oldHeartbeatFallbackCreationDateUtc.UtcTicks) { throw "Restart did not publish a new owner/CIM identity for the heartbeat." }
        }
    }
    if ($OldHeartbeatUpdatedAt) {
        $oldHeartbeatUpdatedAtUtc = ConvertTo-UtcDateTimeOffset -Value $OldHeartbeatUpdatedAt
        if ($heartbeatUpdatedAtUtc.UtcTicks -le $oldHeartbeatUpdatedAtUtc.UtcTicks) { throw "Restart did not publish a fresh heartbeat." }
    }
    $diskSourceIdentityFinal = Get-FileIdentity -Path $listenerPath
    if ([int64]$diskSourceIdentityFinal.Size -le 0) { throw "Current listener source file is empty." }
    $diskSourceIdentityFinalStable =
        [System.String]::Equals($currentDiskSourceIdentity.Path, $diskSourceIdentityFinal.Path, [System.StringComparison]::OrdinalIgnoreCase) -and
        [System.String]::Equals($currentDiskSourceIdentity.FileId, $diskSourceIdentityFinal.FileId, [System.StringComparison]::OrdinalIgnoreCase) -and
        [int64]$currentDiskSourceIdentity.Size -eq [int64]$diskSourceIdentityFinal.Size -and
        [System.String]::Equals($currentDiskSourceIdentity.SHA256, $diskSourceIdentityFinal.SHA256, [System.StringComparison]::OrdinalIgnoreCase)
    if (-not $diskSourceIdentityFinalStable) { throw "Current listener source identity/hash/size changed before evidence return." }
    $diskSourceIdentityStable = $diskSourceIdentityStable -and $diskSourceIdentityFinalStable
    $currentDiskSourceIdentity = $diskSourceIdentityFinal
    if (-not $SkipFinalResnapshot) {
        $finalContract = Assert-ServiceContract
        $finalEvidence = Get-ResidentEvidence `
            -Contract $finalContract `
            -OldOwnerPid $OldOwnerPid `
            -OldOwnerCreationDate $OldOwnerCreationDate `
            -OldHeartbeatStartedAt $OldHeartbeatStartedAt `
            -OldHeartbeatUpdatedAt $OldHeartbeatUpdatedAt `
            -RestartBoundary $RestartBoundary `
            -AllowSourceMismatch:$AllowSourceMismatch `
            -AllowLegacyOwnerStartedAt:$AllowLegacyOwnerStartedAt `
            -SkipFinalResnapshot
        try {
            $finalOwnerCreationUtc = ConvertTo-UtcDateTimeOffset -Value $finalEvidence.OwnerCreationDate
            $finalServiceCreationUtc = ConvertTo-UtcDateTimeOffset -Value $finalEvidence.ServiceCreationDate
            $finalOwnerStartedAtPresent = -not [string]::IsNullOrWhiteSpace([string]$finalEvidence.OwnerStartedAt)
            $finalHeartbeatStartedAtPresent = -not [string]::IsNullOrWhiteSpace([string]$finalEvidence.HeartbeatStartedAt)
            $finalOwnerStartedAtUtc = if ($finalOwnerStartedAtPresent) {
                ConvertTo-UtcDateTimeOffset -Value $finalEvidence.OwnerStartedAt
            } else { $null }
            $finalHeartbeatStartedAtUtc = if ($finalHeartbeatStartedAtPresent) {
                ConvertTo-UtcDateTimeOffset -Value $finalEvidence.HeartbeatStartedAt
            } else { $null }
        } catch {
            throw "Final resident process creation identity evidence is invalid."
        }
        if ([int]$finalEvidence.OwnerPid -ne $ownerPid -or
            $finalOwnerCreationUtc.UtcTicks -ne $processCreatedAtUtc.UtcTicks -or
            (($null -ne $ownerStartedAtUtc) -ne $finalOwnerStartedAtPresent) -or
            ($null -ne $ownerStartedAtUtc -and $finalOwnerStartedAtUtc.UtcTicks -ne $ownerStartedAtUtc.UtcTicks) -or
            (($null -ne $heartbeatStartedAtUtc) -ne $finalHeartbeatStartedAtPresent) -or
            ($null -ne $heartbeatStartedAtUtc -and $finalHeartbeatStartedAtUtc.UtcTicks -ne $heartbeatStartedAtUtc.UtcTicks) -or
            [int]$finalEvidence.ServiceProcessId -ne $servicePid -or
            $finalServiceCreationUtc.UtcTicks -ne
                (Get-NormalizedCimCreationDate -Process $serviceProcess).UtcTicks -or
            [string]$finalEvidence.ProcessLineagePids -ne
                (($lineage | ForEach-Object { [string]$_.ProcessId }) -join "->") -or
            [string]$finalEvidence.LineageMode -ne [string]$lineageMode -or
            [string]$finalEvidence.ProcessLineageCreationUtcTicks -ne [string]$lineageCreationUtcTicks -or
            -not [System.String]::Equals(
                [string]$finalEvidence.OwnerCommandLine,
                [string]$commandLine,
                [System.StringComparison]::OrdinalIgnoreCase
            ) -or
            [string]$finalEvidence.LauncherPid -ne [string]$launcherPid -or
            [string]$finalEvidence.LauncherCreationDate -ne [string]$launcherCreationDate -or
            -not [System.String]::Equals(
                [string]$finalEvidence.LauncherExecutablePath,
                [string]$launcherExecutablePath,
                [System.StringComparison]::OrdinalIgnoreCase
            ) -or
            -not [System.String]::Equals(
                [string]$finalEvidence.LauncherCommandLine,
                [string]$launcherCommandLine,
                [System.StringComparison]::OrdinalIgnoreCase
            ) -or
            -not [System.String]::Equals(
                [string]$finalEvidence.OwnerExecutablePath,
                [string]$executablePath,
                [System.StringComparison]::OrdinalIgnoreCase
            ) -or
            -not [System.String]::Equals(
                [string]$finalEvidence.ServiceExecutablePath,
                [string]$serviceExecutablePath,
                [System.StringComparison]::OrdinalIgnoreCase
            ) -or
            -not [System.String]::Equals(
                [string]$finalEvidence.VenvConfigPath,
                [string]$venvConfigIdentity.Path,
                [System.StringComparison]::OrdinalIgnoreCase
            ) -or
            [string]$finalEvidence.VenvConfigFileId -ne [string]$venvConfigIdentity.FileId -or
            [int64]$finalEvidence.VenvConfigSize -ne [int64]$venvConfigIdentity.Size -or
            -not [System.String]::Equals(
                [string]$finalEvidence.VenvConfigSHA256,
                [string]$venvConfigIdentity.SHA256,
                [System.StringComparison]::OrdinalIgnoreCase
            ) -or
            -not [System.String]::Equals(
                [string]$finalEvidence.VenvHomePath,
                [string]$Contract.VenvHomePath,
                [System.StringComparison]::OrdinalIgnoreCase
            ) -or
            -not [System.String]::Equals(
                [string]$finalEvidence.BasePythonPath,
                [string]$basePythonIdentity.Path,
                [System.StringComparison]::OrdinalIgnoreCase
            ) -or
            [string]$finalEvidence.BasePythonFileId -ne [string]$basePythonIdentity.FileId -or
            [int64]$finalEvidence.BasePythonSize -ne [int64]$basePythonIdentity.Size -or
            -not [System.String]::Equals(
                [string]$finalEvidence.BasePythonSHA256,
                [string]$basePythonIdentity.SHA256,
                [System.StringComparison]::OrdinalIgnoreCase
            ) -or
            [string]$finalEvidence.DiskListenerFileId -ne [string]$currentDiskSourceIdentity.FileId -or
            [int64]$finalEvidence.DiskListenerSize -ne [int64]$currentDiskSourceIdentity.Size -or
            -not [System.String]::Equals(
                [string]$finalEvidence.DiskListenerSHA256,
                [string]$currentDiskSourceIdentity.SHA256,
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
            throw "Resident owner/service/lineage/source identity changed before evidence return."
        }
        return $finalEvidence
    }
    [pscustomobject]@{
        OwnerPid = $ownerPid
        OwnerStartedAt = $ownerStartedAtCanonical
        OwnerStartedAtAuthority = $ownerStartedAtAuthority
        VerificationTime = ConvertTo-CanonicalUtcTimestamp -Value $verificationTimeUtc
        RestartBoundary = if ($null -ne $restartBoundaryUtc) { ConvertTo-CanonicalUtcTimestamp -Value $restartBoundaryUtc } else { $null }
        ResidentIdentityStartedAt = $heartbeatStartedAtCanonical
        OwnerCreationDate = ConvertTo-CanonicalUtcTimestamp -Value $processCreatedAtUtc
        OwnerExecutablePath = $executablePath
        OwnerCommandLine = $commandLine
        LineageMode = $lineageMode
        LauncherPid = $launcherPid
        LauncherCreationDate = $launcherCreationDate
        LauncherExecutablePath = $launcherExecutablePath
        LauncherCommandLine = $launcherCommandLine
        ProcessLineageCreationUtcTicks = $lineageCreationUtcTicks
        ServiceCreationDate = ConvertTo-CanonicalUtcTimestamp -Value (Get-NormalizedCimCreationDate -Process $serviceProcess)
        ServiceExecutablePath = $serviceExecutablePath
        HeartbeatStartedAt = $heartbeatStartedAtCanonical
        HeartbeatUpdatedAt = ConvertTo-CanonicalUtcTimestamp -Value $heartbeatUpdatedAtUtc
        HeartbeatAgeSeconds = $heartbeatAgeSeconds
        HeartbeatStatus = [string]$heartbeat.status
        OwnerSourceSHA256 = $ownerSourceSha
        HeartbeatSourceSHA256 = $heartbeatSourceSha
        ResidentSourceSHA256 = $heartbeatSourceSha
        DiskListenerFileId = $currentDiskSourceIdentity.FileId
        DiskListenerSize = $currentDiskSourceIdentity.Size
        DiskListenerSHA256 = $currentDiskSourceIdentity.SHA256
        SourceMismatch = [bool]$sourceMismatch
        SourceMismatchAllowed = [bool]$AllowSourceMismatch
        SourceEvidence = [pscustomobject]@{
            OwnerHeartbeatSourceMatch = [bool]$residentSourceEvidence
            DiskContractSourceMatch = [bool]$diskSourceEvidence
            DiskSourceIdentityStable = [bool]$diskSourceIdentityStable
            ResidentDiskSourceMatch = [bool]$currentSourceEvidence
            ResidentPidIdentity = [bool]$creationIdentityEvidence
            ResidentPidHeartbeatIdentity = [bool]($null -ne $heartbeatStartedAtUtc -and $creationIdentityEvidence)
            ResidentPidOwnerIdentity = [bool]$ownerProcessIdentityEvidence
        }
        LoadedSourceSHA256 = $heartbeatSourceSha
        ListenerSHA256 = $currentDiskSourceIdentity.SHA256
        ServiceState = [string]$service.State
        ServiceProcessId = $servicePid
        ProcessLineagePids = ($lineage | ForEach-Object { [string]$_.ProcessId }) -join "->"
        ProcessMetadataMode = $metadataMode
        PythonFileId = $pythonIdentity.FileId
        PythonSHA256 = $pythonIdentity.SHA256
        VenvConfigPath = $venvConfigIdentity.Path
        VenvConfigFileId = $venvConfigIdentity.FileId
        VenvConfigSize = $venvConfigIdentity.Size
        VenvConfigSHA256 = $venvConfigIdentity.SHA256
        VenvHomePath = $Contract.VenvHomePath
        BasePythonPath = $basePythonIdentity.Path
        BasePythonFileId = $basePythonIdentity.FileId
        BasePythonSize = $basePythonIdentity.Size
        BasePythonSHA256 = $basePythonIdentity.SHA256
        WrapperSHA256 = $wrapperSha
    }
}

function Wait-ExactProcessExit {
    param(
        [Parameter(Mandatory)][int]$ProcessId,
        [string]$StartedAt,
        [Parameter(Mandatory)][DateTimeOffset]$Deadline
    )
    do {
        $current = Get-ProcessSnapshot -ProcessId $ProcessId
        if ($null -eq $current) { return }
        if ($StartedAt) {
            $identity = [pscustomobject]@{ startedAt = $StartedAt }
            if (-not (Test-ProcessCreationIdentity -Process $current -Identity $identity)) { return }
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTimeOffset]::UtcNow.UtcTicks -lt $Deadline.UtcTicks)
    throw "Previous exact owner PID did not exit before timeout: $ProcessId"
}

function Invoke-ElevatedRestart {
    param([Parameter(Mandatory)][ValidateRange(10, 600)][int]$TimeoutSeconds)

    $systemDirectory = [Environment]::SystemDirectory
    if ([string]::IsNullOrWhiteSpace($systemDirectory)) { throw "Trusted system directory is unavailable." }
    $canonicalSystemDirectory = Get-CanonicalExactPath -Path $systemDirectory -PathType Container
    $powershellPath = Get-CanonicalExactPath -Path (Join-Path $canonicalSystemDirectory "WindowsPowerShell\v1.0\powershell.exe") -PathType Leaf
    $verifiedScriptEvidence = Assert-FixedRestartScript -FixedPath $fixedScriptPath -CurrentPath $currentScriptPath
    $verifiedScriptPath = [string]$verifiedScriptEvidence.CanonicalPath
    if (-not [System.String]::Equals($verifiedScriptPath, $scriptPath, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not [System.String]::Equals($verifiedScriptEvidence.FileId, $fixedScriptEvidence.FileId, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not [System.String]::Equals($verifiedScriptEvidence.SHA256, $fixedScriptEvidence.SHA256, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Fixed restart script path or identity/hash changed."
    }
    $scriptArgument = Quote-AbsolutePath -Path $verifiedScriptPath
    $argumentList = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $scriptArgument,
        "-Restart",
        "-ConfirmRestart",
        "-TimeoutSeconds",
        ([string]$TimeoutSeconds)
    )
    try {
        $child = Start-Process -FilePath $powershellPath -Verb RunAs -Wait -PassThru -ArgumentList $argumentList -ErrorAction Stop
        if ($null -eq $child) { throw "Elevated child process was not created." }
        $child.Refresh()
        if ($null -eq $child.ExitCode) { throw "Elevated child exit code is unavailable." }
        return [int]$child.ExitCode
    } catch {
        throw "UAC elevation failed or was cancelled: $($_.Exception.Message)"
    }
}

function Invoke-SafeRestart {
    param(
        [Parameter(Mandatory)]$Contract,
        [Parameter(Mandatory)][ValidateSet("restart", "deploy")][string]$Operation
    )
    if (-not (Test-Administrator)) {
        throw "Administrator elevation is required for a service $Operation."
    }
    $oldEvidence = Get-ResidentEvidence -Contract $Contract -AllowSourceMismatch -AllowLegacyOwnerStartedAt
    $oldPid = [string]$oldEvidence.OwnerPid
    $oldCreationDate = [string]$oldEvidence.OwnerCreationDate
    $oldHeartbeatStartedAt = [string]$oldEvidence.HeartbeatStartedAt
    $oldHeartbeatUpdatedAt = [string]$oldEvidence.HeartbeatUpdatedAt
    $oldPidInt = [int]$oldEvidence.OwnerPid
    $restartBoundaryUtc = [DateTimeOffset]::UtcNow
    $restartBoundary = ConvertTo-CanonicalUtcTimestamp -Value $restartBoundaryUtc
    Stop-Service -Name $serviceName -ErrorAction Stop
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
        if ($null -eq $service -or [string]$service.State -eq "Stopped") { break }
        Start-Sleep -Milliseconds 250
    } while ([DateTimeOffset]::UtcNow.UtcTicks -lt $deadline.UtcTicks)
    if ($null -ne $service -and [string]$service.State -ne "Stopped") { throw "Service did not stop in time." }
    if ($oldPidInt -gt 0) { Wait-ExactProcessExit -ProcessId $oldPidInt -StartedAt $oldHeartbeatStartedAt -Deadline $deadline }
    Start-Service -Name $serviceName -ErrorAction Stop
    do {
        try {
            $freshContract = Assert-ServiceContract
            $evidence = Get-ResidentEvidence `
                -Contract $freshContract `
                -OldOwnerPid $oldPid `
                -OldOwnerCreationDate $oldCreationDate `
                -OldHeartbeatStartedAt $oldHeartbeatStartedAt `
                -OldHeartbeatUpdatedAt $oldHeartbeatUpdatedAt `
                -RestartBoundary $restartBoundary
            return [pscustomobject]@{
                Operation = $Operation
                ServiceName = $serviceName
                OldOwnerPid = if ($oldPid) { [int]$oldPid } else { $null }
                OldOwnerStartedAt = $oldEvidence.OwnerStartedAt
                OldOwnerStartedAtAuthority = [string]$oldEvidence.OwnerStartedAtAuthority
                OldOwnerCreationDate = $oldCreationDate
                OldHeartbeatStartedAt = $oldHeartbeatStartedAt
                OldHeartbeatUpdatedAt = $oldHeartbeatUpdatedAt
                RestartBoundary = $restartBoundary
                PreRestartResidentSourceSHA256 = $oldEvidence.ResidentSourceSHA256
                PreRestartDiskListenerSHA256 = $oldEvidence.DiskListenerSHA256
                PreRestartSourceMismatch = [bool]$oldEvidence.SourceMismatch
                PreRestartSourceEvidence = $oldEvidence.SourceEvidence
                PreRestartLineageMode = [string]$oldEvidence.LineageMode
                PreRestartProcessLineagePids = [string]$oldEvidence.ProcessLineagePids
                PreRestartProcessLineageCreationUtcTicks = [string]$oldEvidence.ProcessLineageCreationUtcTicks
                PreRestartOwnerExecutablePath = [string]$oldEvidence.OwnerExecutablePath
                PreRestartOwnerCommandLine = [string]$oldEvidence.OwnerCommandLine
                PreRestartLauncherPid = $oldEvidence.LauncherPid
                PreRestartLauncherCreationDate = $oldEvidence.LauncherCreationDate
                PreRestartLauncherExecutablePath = $oldEvidence.LauncherExecutablePath
                PreRestartLauncherCommandLine = $oldEvidence.LauncherCommandLine
                NewOwnerPid = $evidence.OwnerPid
                NewOwnerStartedAt = $evidence.OwnerStartedAt
                NewOwnerStartedAtAuthority = [string]$evidence.OwnerStartedAtAuthority
                NewResidentIdentityStartedAt = $evidence.ResidentIdentityStartedAt
                NewOwnerCreationDate = $evidence.OwnerCreationDate
                NewOwnerExecutablePath = [string]$evidence.OwnerExecutablePath
                NewOwnerCommandLine = [string]$evidence.OwnerCommandLine
                NewLineageMode = [string]$evidence.LineageMode
                NewProcessLineagePids = [string]$evidence.ProcessLineagePids
                NewProcessLineageCreationUtcTicks = [string]$evidence.ProcessLineageCreationUtcTicks
                NewLauncherPid = $evidence.LauncherPid
                NewLauncherCreationDate = $evidence.LauncherCreationDate
                NewLauncherExecutablePath = $evidence.LauncherExecutablePath
                NewLauncherCommandLine = $evidence.LauncherCommandLine
                NewHeartbeatStartedAt = $evidence.HeartbeatStartedAt
                VerificationTime = $evidence.VerificationTime
                HeartbeatUpdatedAt = $evidence.HeartbeatUpdatedAt
                HeartbeatAgeSeconds = $evidence.HeartbeatAgeSeconds
                PostRestartResidentSourceSHA256 = $evidence.ResidentSourceSHA256
                PostRestartDiskListenerSHA256 = $evidence.DiskListenerSHA256
                PostRestartDiskListenerFileId = $evidence.DiskListenerFileId
                PostRestartDiskListenerSize = $evidence.DiskListenerSize
                PostRestartSourceMatch = -not [bool]$evidence.SourceMismatch
                PostRestartSourceEvidence = $evidence.SourceEvidence
                LoadedSourceSHA256 = $evidence.LoadedSourceSHA256
                ListenerSHA256 = $freshContract.ListenerSHA256
                ServiceState = $evidence.ServiceState
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    } while ([DateTimeOffset]::UtcNow.UtcTicks -lt $deadline.UtcTicks)
    throw "Service restarted but exact owner/heartbeat/source proof did not become ready."
}

try {
    if (($Restart -and $Deploy) -or ($ConfirmRestart -and -not $Restart) -or ($ConfirmDeploy -and -not $Deploy)) { throw "Choose exactly one operation and its matching confirmation." }
    if (-not $VerifyOnly -and -not $Restart -and -not $Deploy) { $VerifyOnly = $true }
    if ($VerifyOnly -and ($Restart -or $Deploy)) { throw "VerifyOnly cannot be combined with restart/deploy." }
    if ($AllowStaleSource -and -not $VerifyOnly) { throw "AllowStaleSource is only valid with VerifyOnly." }
    if ($Restart -and -not $ConfirmRestart) { throw "Restart requires -ConfirmRestart." }
    if ($Deploy -and -not $ConfirmDeploy) { throw "Deploy requires -ConfirmDeploy." }
    if ($Restart -and -not (Test-Administrator)) {
        $elevatedExitCode = Invoke-ElevatedRestart -TimeoutSeconds $TimeoutSeconds
        exit $elevatedExitCode
    }
    $contract = Assert-ServiceContract
    if ($VerifyOnly) {
        $evidence = Get-ResidentEvidence -Contract $contract -AllowSourceMismatch:$AllowStaleSource
        [pscustomobject]@{
            Operation = "verify"
            ServiceName = $serviceName
            ServiceState = $evidence.ServiceState
            OwnerPid = $evidence.OwnerPid
            OwnerStartedAt = $evidence.OwnerStartedAt
            OwnerStartedAtAuthority = $evidence.OwnerStartedAtAuthority
            ResidentIdentityStartedAt = $evidence.ResidentIdentityStartedAt
            OwnerCreationDate = $evidence.OwnerCreationDate
            HeartbeatStartedAt = $evidence.HeartbeatStartedAt
            VerificationTime = $evidence.VerificationTime
            HeartbeatUpdatedAt = $evidence.HeartbeatUpdatedAt
            HeartbeatAgeSeconds = $evidence.HeartbeatAgeSeconds
            OwnerSourceSHA256 = $evidence.OwnerSourceSHA256
            HeartbeatSourceSHA256 = $evidence.HeartbeatSourceSHA256
            ResidentSourceSHA256 = $evidence.ResidentSourceSHA256
            DiskListenerSHA256 = $evidence.DiskListenerSHA256
            DiskListenerFileId = $evidence.DiskListenerFileId
            DiskListenerSize = $evidence.DiskListenerSize
            SourceMismatch = $evidence.SourceMismatch
            SourceMismatchAllowed = $evidence.SourceMismatchAllowed
            SourceEvidence = $evidence.SourceEvidence
            LoadedSourceSHA256 = $evidence.LoadedSourceSHA256
            ListenerSHA256 = $contract.ListenerSHA256
            ServiceXmlSHA256 = $contract.ServiceXmlSHA256
            WrapperSHA256 = $contract.WrapperSHA256
        } | ConvertTo-Json -Depth 8
    } else {
        Invoke-SafeRestart -Contract $contract -Operation $(if ($Restart) { "restart" } else { "deploy" }) | ConvertTo-Json -Depth 8
    }
} catch {
    Write-Error $_
    exit 2
}
