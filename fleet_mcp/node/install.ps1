<#
    Install the Fleet node connector on this machine.

    What it writes, and what it deliberately will not
    ------------------------------------------------
    It writes a binding and a node secret, and registers two scheduled tasks.
    It does NOT write a send allow list -- the template ships with
    `allow_send_to: []`, which means every send is refused here until somebody
    at this keyboard names a session. That is the one decision this script
    declines to make on the operator's behalf.

    It also never prints the secret. It prints a fingerprint, so that the two
    ends can be confirmed to hold the same value without either of them writing
    it to a terminal, a log or a transcript. If the fingerprints differ, the
    copy is wrong; if they match, neither end had to be told what it was.

    Re-running is safe: an existing binding and an existing secret are left
    exactly as they are. Overwriting a binding would silently re-point a device
    at a different Fleet host, and overwriting a secret would silently break a
    tunnel that is working.
#>
[CmdletBinding()]
param(
    [string]$DeviceRef = 'device:work-fj',
    [string]$CloudCliEndpoint = 'http://127.0.0.1:3001/mcp',
    [string]$CloudCliCredential,
    [string]$SshHost = 'vince',
    [int]$RemotePort = 3402,
    [int]$LocalPort = 3402,
    [string]$Python
)

$ErrorActionPreference = 'Stop'

$BindingRoot = Join-Path $env:LOCALAPPDATA 'ServerOS\bindings'
$StateDir = Join-Path $env:LOCALAPPDATA 'ServerOS\state\fleet-node'
$Binding = Join-Path $BindingRoot 'fleet-node.local.yaml'
$SecretPath = Join-Path $StateDir 'secret'
# Anchored at the file CloudCLI itself owns, not at a copy. Copying a live
# credential into a second location would create a second thing to revoke and a
# second place to leak from, for no benefit: the connector reads it at the
# point of use and never stores or returns it.
if (-not $CloudCliCredential) {
    $CloudCliCredential = Join-Path $env:USERPROFILE '.cloudcli\mcp-pat-claude-code.txt'
}

foreach ($dir in @($BindingRoot, $StateDir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}

# -- the node secret -------------------------------------------------------
#
# A per-device secret, never a shared one. Two machines holding the same value
# would be a value that authenticates as whichever of them presented it, and
# the DEVICE_MISMATCH check further up would start refusing requests for a
# reason nobody could see.

if (-not (Test-Path $SecretPath)) {
    $bytes = New-Object 'byte[]' 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $secret = -join ($bytes | ForEach-Object { $_.ToString('x2') })
    # No trailing newline: the connector strips whitespace, but a file whose
    # content depends on how it was written is a file that will be edited
    # wrongly once.
    [System.IO.File]::WriteAllText($SecretPath, $secret, [System.Text.Encoding]::ASCII)
    Write-Host "wrote node secret to $SecretPath"
} else {
    Write-Host "keeping existing node secret at $SecretPath"
}

$fingerprint = (Get-FileHash -Path $SecretPath -Algorithm SHA256).Hash.Substring(0, 12)
Write-Host "secret fingerprint (sha256, first 12 hex): $fingerprint"

# -- the binding -----------------------------------------------------------

if (-not (Test-Path $Binding)) {
    $template = @"
# Fleet node connector binding -- local to THIS machine, never committed.
#
# Records a device ref and credential ANCHORS (paths). No credential value
# appears in this file, and none may be added: fleet/transport.py makes that a
# malformed binding rather than merely an unwise one.
schema: serveros/fleet-node.local/v1

device_ref: $DeviceRef
secret_anchor: $SecretPath

# The CloudCLI native MCP gateway on this machine. Loopback only.
cloudcli_endpoint: $CloudCliEndpoint
cloudcli_credential_anchor: $CloudCliCredential

bind: 127.0.0.1
port: $LocalPort

# Sessions this connector may be told to send to, by native id or full
# ``device:<id>|<provider>|<id>`` ref. EMPTY MEANS EVERY SEND IS REFUSED, here,
# including a send from the Fleet owner -- and this list is what decides, not
# the other machine's configuration.
allow_send_to: []

notes: >-
  Installed by fleet_mcp/node/install.ps1. The reverse tunnel to the Fleet host
  is owned by this machine and can be dropped from here at any time.
"@
    [System.IO.File]::WriteAllText($Binding, $template, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "wrote binding to $Binding"
} else {
    Write-Host "keeping existing binding at $Binding"
}

if (-not (Test-Path $CloudCliCredential)) {
    Write-Warning ("No CloudCLI credential at {0}. The connector will refuse to " +
                   "start until one is there. That file is CloudCLI's own " +
                   "personal access token for this machine, and the anchor is a " +
                   "path rather than a copy so that there is exactly one of it." -f $CloudCliCredential)
}

# -- the tasks -------------------------------------------------------------
#
# Two, because they fail differently. A dead connector is a device that answers
# nothing; a dead tunnel is a device nothing can reach. One task covering both
# would report whichever failed first and hide the other.
#
# -ExecutionTimeLimit 0 is "run indefinitely". The default three days would
# stop a working connector, which is the kind of outage that looks like a
# network problem for a week.

function Register-ConnectorTask {
    param([string]$Name, [string]$ScriptPath, [object[]]$ScriptArgs)
    $argument = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" {1}' -f `
        $ScriptPath, ($ScriptArgs -join ' ')
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argument
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Force | Out-Null
    Write-Host "registered scheduled task $Name"
}

$connectorArgs = @()
if ($Python) { $connectorArgs += @('-Python', "`"$Python`"") }
$connectorArgs += @('-Config', "`"$Binding`"")

$tunnelArgs = @('-SshHost', $SshHost, '-RemotePort', $RemotePort, '-LocalPort', $LocalPort)

Register-ConnectorTask -Name 'ServerOS-FleetNode-Connector' `
    -ScriptPath (Join-Path $PSScriptRoot 'start-connector.ps1') -ScriptArgs $connectorArgs
Register-ConnectorTask -Name 'ServerOS-FleetNode-Tunnel' `
    -ScriptPath (Join-Path $PSScriptRoot 'start-tunnel.ps1') -ScriptArgs $tunnelArgs

Write-Host ''
Write-Host 'Installed. What to check next:'
Write-Host ("  binding          {0}" -f $Binding)
Write-Host ("  connector log    {0}" -f (Join-Path $env:LOCALAPPDATA 'ServerOS\logs\fleet-node-connector.log'))
Write-Host ("  tunnel log       {0}" -f (Join-Path $env:LOCALAPPDATA 'ServerOS\logs\fleet-node-tunnel.log'))
Write-Host ("  secret fingerprint {0}  <- must match the value on the Fleet host" -f $fingerprint)
Write-Host ''
Write-Host 'Start now, without logging off:'
Write-Host "  Start-ScheduledTask -TaskName 'ServerOS-FleetNode-Connector'"
Write-Host "  Start-ScheduledTask -TaskName 'ServerOS-FleetNode-Tunnel'"
Write-Host ''
Write-Host 'The connector binds 127.0.0.1 only. Reaching it from the Fleet host is'
Write-Host 'the tunnel''s job, and the tunnel is this machine''s to drop.'
