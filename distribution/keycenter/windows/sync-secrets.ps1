<#
One key-center sync on Windows: what the scheduled task runs (through
run-hidden-wait.vbs, so no console window appears).

  python serveros_secret.py sync     pull, decrypt this device's bundles, write
                                     owner-only env files, restart named tasks

The tool itself puts sops / age / git (Git for Windows, WinGet, Scoop,
~/.local/bin, and `path:` from ~/.config/serveros/secrets.yaml) on PATH and sets
HOME for ssh, so the task needs no environment of its own.

Its output - names, bundles, commit, errors; never a value - is appended to
%LOCALAPPDATA%\ServerOS\state\secrets-sync.log (last 200 lines kept).

Why this is not a plain `& $Python $Tool sync`. A pull that hangs on ssh used to
outlive the scheduled task: Task Scheduler stops the launcher (wscript.exe) at
the task's ExecutionTimeLimit, but nothing below it dies, so powershell, python,
git and ssh all stayed alive while the next 5-minute run stacked another copy on
top. Seven of those held 11 GB of commit between them and the pagefile on a slow
disk turned the machine into a swap storm. So a run is now bounded by
-TimeoutSeconds (default 150): when it expires the whole child tree is killed
with `taskkill /T /F`, one line is logged, and the script exits 124.

The child runs through cmd.exe with `> log 2>&1` rather than `| Out-String`.
Buffering it in memory is what let a stuck run grow this process to several
gigabytes, and Windows PowerShell's Start-Process reports no exit code at all
when its output is redirected, so the exit code has to come from a Process
object that actually has one.

GIT_SSH_COMMAND (set only when the environment names no ssh of its own) makes
a dead ssh session give up after ~15 s of missed keepalives instead of waiting
on TCP forever, so `git pull` returns on its own in the common case. It only
adds -o options: the ~/.ssh/config host block (IdentityFile, IdentitiesOnly)
still applies.

Keep this file pure ASCII: the machines it lands on may have a GBK code page.
#>
param(
    [string]$Python = 'python',
    [string]$Tool = (Join-Path $PSScriptRoot '..\serveros_secret.py'),
    [int]$TimeoutSeconds = 150
)
$ErrorActionPreference = 'Continue'
$state = Join-Path $env:LOCALAPPDATA 'ServerOS\state'
New-Item -ItemType Directory -Force -Path $state | Out-Null
$log = Join-Path $state 'secrets-sync.log'
$env:PYTHONIOENCODING = 'utf-8'
if (-not $env:GIT_SSH_COMMAND -and -not $env:GIT_SSH) {
    $env:GIT_SSH_COMMAND = 'ssh -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=3'
}

# A stopped task leaves its tree running. Clear anything an earlier run left
# behind before starting another; a live run is under TimeoutSeconds, so only
# clearly stale ones (twice that) are taken.
$stale = (Get-Date).AddSeconds(-2 * $TimeoutSeconds)
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $_.ProcessId -ne $PID -and
        $_.CommandLine -and $_.CommandLine.Contains('sync-secrets.ps1') -and
        ($_.CreationDate -lt $stale)
    } |
    ForEach-Object { & taskkill.exe /PID $_.ProcessId /T /F 2>&1 | Out-Null }

$outFile = Join-Path $state 'secrets-sync.out.tmp'
if (Test-Path -LiteralPath $outFile) { Remove-Item -LiteralPath $outFile -Force -ErrorAction SilentlyContinue }

$code = 1
$output = ''
try {
    # cmd wraps the command so stdout and stderr land in one file in order, and
    # so the exit code is a real Process.ExitCode (Start-Process with redirected
    # output gives $null on Windows PowerShell 5.1).
    $inner = '/c ""' + $Python + '" "' + $Tool + '" sync > "' + $outFile + '" 2>&1"'
    $comspec = if ($env:ComSpec) { $env:ComSpec } else { 'cmd.exe' }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $comspec
    $psi.Arguments = $inner
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
    if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
        & taskkill.exe /PID $proc.Id /T /F 2>&1 | Out-Null
        $code = 124
        $output = "sync timed out after ${TimeoutSeconds}s; process tree killed"
    } else {
        $code = $proc.ExitCode
        if (Test-Path -LiteralPath $outFile) { $output = [System.IO.File]::ReadAllText($outFile) }
    }
} catch {
    $code = 1
    $output = "sync launcher failed: $($_.Exception.Message)"
}

$stamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
$lines = @()
if (Test-Path -LiteralPath $log) { $lines = @(Get-Content -LiteralPath $log -Tail 199) }
$lines += "$stamp exit=$code $(([string]$output).Trim() -replace '\r?\n', ' | ')"
[System.IO.File]::WriteAllLines($log, [string[]]$lines, (New-Object System.Text.UTF8Encoding($false)))

if (Test-Path -LiteralPath $outFile) { Remove-Item -LiteralPath $outFile -Force -ErrorAction SilentlyContinue }
exit $code
