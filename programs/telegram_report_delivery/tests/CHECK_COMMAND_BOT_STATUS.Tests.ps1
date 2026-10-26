$checker = Join-Path (Split-Path -Parent $PSScriptRoot) "CHECK_COMMAND_BOT_STATUS.ps1"
. $checker

function Assert-ContractValue {
    param($Actual, $Expected, [string]$Label)
    if ($Actual -ne $Expected) { throw "$Label expected '$Expected' but got '$Actual'" }
}

function Assert-ContractTrue {
    param($Actual, [string]$Label)
    if (-not [bool]$Actual) { throw "$Label expected true" }
}

function Assert-ContractFalse {
    param($Actual, [string]$Label)
    if ([bool]$Actual) { throw "$Label expected false" }
}

Describe "CHECK_COMMAND_BOT_STATUS listener decision contract" {
    It "treats an alive owner with inaccessible process fields as running" {
        $result = Resolve-ListenerStatus `
            -HostStateValid $true `
            -OwnerProcessAlive $true `
            -OwnerNameMatch $true `
            -OwnerCreationIdentityMatch $true `
            -OwnerHeartbeatPidMatch $true `
            -OwnerExecutableStatus "unavailable_due_to_access" `
            -OwnerCommandLineStatus "unavailable_due_to_access" `
            -HeartbeatAgeSeconds 5 `
            -HeartbeatStatus "polling" `
            -SourceSha256Match $true

        Assert-ContractValue $result.ListenerHealth "running" "ListenerHealth"
        Assert-ContractValue $result.ListenerReadiness "ready" "ListenerReadiness"
        Assert-ContractTrue $result.IdentityValid "IdentityValid"
    }

    It "reports a live listener with a stale source as restart_required" {
        $result = Resolve-ListenerStatus `
            -HostStateValid $true `
            -OwnerProcessAlive $true `
            -OwnerNameMatch $true `
            -OwnerCreationIdentityMatch $true `
            -OwnerHeartbeatPidMatch $true `
            -OwnerExecutableStatus "unavailable_due_to_access" `
            -OwnerCommandLineStatus "unavailable_due_to_access" `
            -HeartbeatAgeSeconds 5 `
            -HeartbeatStatus "polling" `
            -SourceSha256Match $false

        Assert-ContractValue $result.ListenerHealth "running" "ListenerHealth"
        Assert-ContractValue $result.SourceStatus "stale_source" "SourceStatus"
        Assert-ContractValue $result.ListenerReadiness "restart_required" "ListenerReadiness"
    }

    It "blocks a stale heartbeat" {
        $result = Resolve-ListenerStatus `
            -HostStateValid $true `
            -OwnerProcessAlive $true `
            -OwnerNameMatch $true `
            -OwnerCreationIdentityMatch $true `
            -OwnerHeartbeatPidMatch $true `
            -OwnerExecutableStatus "match" `
            -OwnerCommandLineStatus "match" `
            -HeartbeatAgeSeconds 181 `
            -HeartbeatStatus "polling" `
            -SourceSha256Match $true

        Assert-ContractValue $result.ListenerHealth "stale" "ListenerHealth"
        Assert-ContractValue $result.ListenerReadiness "blocked" "ListenerReadiness"
        Assert-ContractFalse $result.HeartbeatFresh "HeartbeatFresh"
    }

    It "reports a dead owner PID as stopped" {
        $result = Resolve-ListenerStatus `
            -HostStateValid $true `
            -OwnerProcessAlive $false `
            -OwnerNameMatch $false `
            -OwnerCreationIdentityMatch $false `
            -OwnerHeartbeatPidMatch $false `
            -OwnerExecutableStatus "unavailable" `
            -OwnerCommandLineStatus "unavailable" `
            -HeartbeatAgeSeconds 5 `
            -HeartbeatStatus "polling" `
            -SourceSha256Match $true

        Assert-ContractValue $result.ListenerHealth "stopped" "ListenerHealth"
        Assert-ContractValue $result.ListenerReadiness "blocked" "ListenerReadiness"
        Assert-ContractFalse $result.IdentityValid "IdentityValid"
    }

    It "reports full healthy evidence as running and ready" {
        $result = Resolve-ListenerStatus `
            -HostStateValid $true `
            -OwnerProcessAlive $true `
            -OwnerNameMatch $true `
            -OwnerCreationIdentityMatch $true `
            -OwnerHeartbeatPidMatch $true `
            -OwnerExecutableStatus "match" `
            -OwnerCommandLineStatus "match" `
            -HeartbeatAgeSeconds 5 `
            -HeartbeatStatus "polling" `
            -SourceSha256Match $true

        Assert-ContractValue $result.ListenerHealth "running" "ListenerHealth"
        Assert-ContractValue $result.ListenerReadiness "ready" "ListenerReadiness"
        Assert-ContractValue $result.SourceStatus "current" "SourceStatus"
    }
}
