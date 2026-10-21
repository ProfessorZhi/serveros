<#
    Keep the reverse tunnel to the Fleet host open.

    This machine dials out; the Fleet host listens. That direction is the point:
    a corporate workstation should not have to accept an inbound connection, and
    opening a port on it to make one possible is a worse trade than the
    convenience it buys.

    What this does NOT do, and the reason it is stated here
    ------------------------------------------------------
    The tunnel does not make this machine reachable. It makes ONE port on the
    Fleet host's loopback reach ONE port on this machine's loopback, and that
    port answers nothing except signed requests carrying this device's node
    secret. A stolen secret yields seven named questions and no way to widen
    them -- see fleet/fleet-mcp-contract.yaml, and the reconciliation there with
    controller-contract.yaml's prohibition on reaching a device over SSH.

    ExitOnForwardFailure is deliberate. Without it ssh stays up while quietly
    running with no forward, which presents to the Fleet host as a tunnel that
    is established and a device that answers nothing -- the "presence is not
    capability" failure this project has already been bitten by once. With it,
    a port already bound is an exit, and the exit is what the retry loop sees.

    A bound port is not always a configuration mistake, and calling it one cost
    fifteen hours. On 2026-10-10 the client process here went away without a
    clean TCP teardown, the Fleet host's sshd kept 127.0.0.1:3402 bound to the
    dead connection, and every retry from then on exited in two seconds on
    "remote port forwarding failed for listen port 3402". The loop below backed
    off and said "retrying", which is true and useless: the condition cannot
    clear itself, because the listener belongs to the root monitor of an sshd
    session whose client no longer exists, and the host cannot tell that
    session apart from a live one. Nothing here can fix it. What this file can
    do is name it, so the log says what is wrong and what to run -- see
    INCIDENT remote-port-stuck below, and the same page in README.md.

    The host stays the public address on purpose. Moving this tunnel to the
    tailnet name was measured rather than assumed: ten sequential connections
    over each path both succeeded ten for ten, at a mean of 2856 ms over the
    public address against 4462 ms over the tailnet, and the tailnet produced
    both of the worst single attempts (15.0 s and 7.5 s). The path is not what
    broke the tunnel, so it is not changed on the strength of a feeling.

    The pid file is not bookkeeping, it is the safety property. "Kill the ssh
    processes that look like this one" is a heuristic, and this machine already
    runs other tunnels: one forwarding a CloudCLI port, and one forwarding a
    read-only production query broker that must not be disturbed. A filter
    written against the option list matches all three. Writing our own child's
    pid down and killing only that makes the uninstaller incapable of ending
    someone else's session.
#>
[CmdletBinding()]
param(
    [string]$SshHost = 'vince',
    [int]$RemotePort = 3402,
    [int]$LocalPort = 3402,
    [int]$RetrySeconds = 15,
    [string]$SshExe = 'ssh'
)

$ErrorActionPreference = 'Stop'

$LogDir = Join-Path $env:LOCALAPPDATA 'ServerOS\logs'
$StateDir = Join-Path $env:LOCALAPPDATA 'ServerOS\state\fleet-node'
$LogFile = Join-Path $LogDir 'fleet-node-tunnel.log'
$SshErrLog = Join-Path $LogDir 'fleet-node-tunnel.ssh.log'
$SshOutLog = Join-Path $LogDir 'fleet-node-tunnel.ssh.out.log'
$PidFile = Join-Path $StateDir 'tunnel.pid'
foreach ($dir in @($LogDir, $StateDir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}

# Appended through .NET rather than Add-Content: Windows PowerShell writes a
# UTF-8 BOM on file creation, and a log whose first line begins with an
# invisible byte is a log that breaks the first tool that tries to read it.
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
function Write-Log {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format s), $Message
    [System.IO.File]::AppendAllText($LogFile, $line + "`r`n", $utf8NoBom)
    Write-Host $line
}

function Clear-PidFile {
    if (Test-Path $PidFile) { Remove-Item -Path $PidFile -Force -ErrorAction SilentlyContinue }
}

# The forward is written as an explicit address pair rather than derived, so
# that the log line names the same thing the Fleet host was configured with.
$forward = "127.0.0.1:{0}:127.0.0.1:{1}" -f $RemotePort, $LocalPort
$options = @(
    '-N'
    '-o', 'ExitOnForwardFailure=yes'
    '-o', 'ConnectTimeout=15'
    '-o', 'ServerAliveInterval=30'
    '-o', 'ServerAliveCountMax=3'
    '-o', 'TCPKeepAlive=yes'
    '-o', 'BatchMode=yes'
    '-R', $forward
    $SshHost
)

Write-Log ("tunnel supervisor starting: {0} -> {1} (retry {2}s)" -f $forward, $SshHost, $RetrySeconds)

# Consecutive attempts that failed because the remote port is held. Reset by any
# other outcome, so the count means what it says.
$stuckStreak = 0

while ($true) {
    $started = Get-Date
    Write-Log ("opening {0}" -f $forward)
    # Each attempt gets a clean stderr file, so what is read back below is this
    # attempt's reason rather than the first one's.
    [System.IO.File]::WriteAllText($SshErrLog, '', $utf8NoBom)
    [System.IO.File]::WriteAllText($SshOutLog, '', $utf8NoBom)

    try {
        $proc = Start-Process -FilePath $SshExe -ArgumentList $options `
            -NoNewWindow -PassThru `
            -RedirectStandardError $SshErrLog -RedirectStandardOutput $SshOutLog
    } catch {
        Write-Log ("ssh could not be started: {0}" -f $_.Exception.Message)
        Start-Sleep -Seconds $RetrySeconds
        continue
    }

    # Written as soon as the process exists, so an uninstaller running while the
    # tunnel is up has something to read rather than a process table to guess at.
    [System.IO.File]::WriteAllText($PidFile, [string]$proc.Id, $utf8NoBom)
    $proc.WaitForExit()
    $code = $proc.ExitCode
    Clear-PidFile

    $lifetime = ((Get-Date) - $started).TotalSeconds
    Write-Log ("tunnel exited after {0:N0}s with code {1}" -f $lifetime, $code)

    $reason = ''
    try { $reason = ([System.IO.File]::ReadAllText($SshErrLog, $utf8NoBom)).Trim() } catch { }
    if ($reason) {
        foreach ($line in ($reason -split "`r?`n")) {
            if ($line.Trim()) { Write-Log ("ssh: {0}" -f $line.Trim()) }
        }
    }

    # The one short-lived exit that is not a configuration problem and does not
    # clear itself. Retrying is still correct -- the port frees the moment
    # someone ends the stale session -- but a reader of this log has to be told
    # that waiting is not the fix. Reported once and then every half hour,
    # rather than every retry, so that a fifteen-hour outage is three loud
    # lines instead of nine hundred quiet ones.
    if ($reason -match ('remote port forwarding failed for listen port ' + [regex]::Escape([string]$RemotePort))) {
        $stuckStreak++
        if ($stuckStreak -eq 1 -or ($stuckStreak % 30) -eq 0) {
            Write-Log ("INCIDENT remote-port-stuck: {0} on {1} is held by the sshd session of a tunnel that is gone -- consecutive failed attempts: {2}. Waiting does not fix this." -f $RemotePort, $SshHost, $stuckStreak)
            Write-Log '  clear it on the Fleet host, as the account this tunnel logs in as:'
            Write-Log '    ssh <host> "ps -u <user> -o pid,etimes,cmd | grep ''sshd: <user>$''"'
            Write-Log '    ssh <host> "pkill -f ''^sshd: <user>\s*$''"'
            Write-Log '  that ends every session-less ssh -N login for that user, including any other tunnel, which reopens on its own schedule.'
            Write-Log '  the listener belongs to the root monitor of that session, so it cannot be cleared from the account itself -- the session has to end.'
        }
    } else {
        $stuckStreak = 0
    }

    # A tunnel that never stayed up for longer than the retry interval is a
    # configuration problem -- a wrong host, a refused key, a port already
    # bound -- and hammering it produces a log nobody can read. Back off.
    $sleep = if ($lifetime -lt 5) { [Math]::Max($RetrySeconds * 4, 60) } else { $RetrySeconds }
    Write-Log ("retrying in {0}s" -f $sleep)
    Start-Sleep -Seconds $sleep
}
