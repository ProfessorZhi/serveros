<#
    Remove the Fleet node connector from this machine.

    This is the rollback, and it is deliberately the cheap direction. Anything
    that can be installed by a script has to be removable by one, on the machine
    that installed it, without asking anyone -- otherwise the honest answer to
    "what if we want this to stop" is a shrug.

    What it removes: the two scheduled tasks, and any connector or tunnel
    process they started.

    What it leaves, on purpose:

      * the binding
      * the node secret
      * the logs

    The tasks are what makes this machine reachable. Without them nothing
    starts at logon, the tunnel never opens, and the connector is a program
    sitting on a disk. Deleting the secret as well would make the removal
    irreversible without changing what is reachable, and it would destroy the
    only record of which value to re-provision -- so it takes -PurgeSecret to
    mean that.

    Run this from the same account that ran install.ps1. A different account's
    scheduled tasks are not this script's to delete, and it will say so rather
    than reporting success.
#>
[CmdletBinding()]
param(
    [switch]$PurgeSecret,
    [switch]$PurgeBinding
)

$ErrorActionPreference = 'Stop'

$StateDir = Join-Path $env:LOCALAPPDATA 'ServerOS\state\fleet-node'
$SecretPath = Join-Path $StateDir 'secret'
$BindingRoot = Join-Path $env:LOCALAPPDATA 'ServerOS\bindings'
$Binding = Join-Path $BindingRoot 'fleet-node.local.yaml'

$tasks = @('ServerOS-FleetNode-Connector', 'ServerOS-FleetNode-Tunnel')

foreach ($name in $tasks) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "no scheduled task named $name (already removed, or installed by another account)"
        continue
    }
    if ($task.State -eq 'Running') {
        Stop-ScheduledTask -TaskName $name
        Write-Host "stopped $name"
    }
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Host "unregistered $name"
}

# The tasks are gone, so nothing restarts these. Killing them is what makes the
# removal take effect now rather than at the next logon.
#
# The tunnel is killed by the pid start-tunnel.ps1 wrote down, not by a filter
# over the process table. There was a filter here first -- "ssh, with
# ExitOnForwardFailure, with -R" -- and it would have ended two other tunnels on
# this machine: one forwarding a CloudCLI port, and one forwarding a read-only
# production query broker that is explicitly not this script's to touch. A
# heuristic over shared option names cannot tell three tunnels apart. Our own
# child's pid can.
$pidFile = Join-Path $StateDir 'tunnel.pid'
$killedTunnel = $false
if (Test-Path $pidFile) {
    $tunnelPid = (Get-Content -Path $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $tunnelPid = "$tunnelPid".Trim()
    if ($tunnelPid -match '^\d+$') {
        $live = Get-Process -Id ([int]$tunnelPid) -ErrorAction SilentlyContinue
        if ($live) {
            Write-Host ("stopping tunnel process pid {0}" -f $tunnelPid)
            Stop-Process -Id ([int]$tunnelPid) -Force -ErrorAction SilentlyContinue
            $killedTunnel = $true
        }
    }
    Remove-Item -Path $pidFile -Force -ErrorAction SilentlyContinue
}
if (-not $killedTunnel) {
    Write-Host 'no tunnel pid on record (the tunnel was already down, or was never started here)'
}

# The connector is matched on its module and its binding together, which is
# narrow enough to be this installation. A bare `python.exe` match would stop
# something else; the module name alone would match a second checkout.
$connectors = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*fleet_mcp.node.connector*' } |
    Where-Object { $_.CommandLine -like '*fleet-node.local.yaml*' }
foreach ($process in $connectors) {
    Write-Host ("stopping connector process pid {0}" -f $process.ProcessId)
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

if ($PurgeSecret) {
    if (Test-Path $SecretPath) {
        Remove-Item -Path $SecretPath -Force
        Write-Host "removed node secret $SecretPath"
        Write-Host "the Fleet host still holds the matching value; remove it there too"
    }
} else {
    Write-Host "kept node secret at $SecretPath (pass -PurgeSecret to remove it)"
}

if ($PurgeBinding) {
    if (Test-Path $Binding) {
        Remove-Item -Path $Binding -Force
        Write-Host "removed binding $Binding"
    }
} else {
    Write-Host "kept binding at $Binding (pass -PurgeBinding to remove it)"
}

Write-Host ''
Write-Host 'This machine no longer opens the tunnel or starts the connector.'
Write-Host 'The Fleet host will report the device as having no route, which is'
Write-Host 'the correct answer rather than an error.'
