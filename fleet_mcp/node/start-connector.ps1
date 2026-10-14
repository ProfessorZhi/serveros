<#
    Start the Fleet node connector on this machine.

    This process serves a closed, signed API on 127.0.0.1 only. It does not
    listen on a routable address and is not reachable from the corporate
    network: the Fleet host reaches it through a reverse tunnel this machine
    opens outward (start-tunnel.ps1).

    The script does not return until the connector exits, so whoever started it
    -- a scheduled task, most of the time -- has a lifetime equal to the
    process's and a real exit code. A launcher that fires a process and returns
    turns "the connector is down" into "the task succeeded a week ago".

    Two things are explicit rather than discovered.

    PATH. A scheduled task runs without a login shell, so it does not read the
    profile that puts python on PATH. The interpreter is resolved up front and a
    missing one is a message, not a task that exits having done nothing.

    The binding. It is named on the command line. "Which devices can this
    machine reach" should be a fact about an argument, not about a working
    directory.
#>
[CmdletBinding()]
param(
    [string]$Config,
    [string]$Python,
    [switch]$Foreground
)

$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$BindingRoot = Join-Path $env:LOCALAPPDATA 'ServerOS\bindings'
if (-not $Config) { $Config = Join-Path $BindingRoot 'fleet-node.local.yaml' }
$LogDir = Join-Path $env:LOCALAPPDATA 'ServerOS\logs'
$ErrorLog = Join-Path $LogDir 'fleet-node-connector.log'
$OutputLog = Join-Path $LogDir 'fleet-node-connector.out.log'

function Find-Python {
    param([string]$Hint)
    if ($Hint) {
        if (Test-Path $Hint) { return (Resolve-Path $Hint).Path }
        throw "Python not found at -Python '$Hint'."
    }
    $fromPath = Get-Command python -ErrorAction SilentlyContinue |
                Select-Object -First 1 -ExpandProperty Source
    if ($fromPath -and (Test-Path $fromPath)) { return $fromPath }
    $fromSdk = Get-ChildItem 'D:\develop\sdk\Python*\python.exe' -ErrorAction SilentlyContinue |
               Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
    if ($fromSdk -and (Test-Path $fromSdk)) { return $fromSdk }
    throw "No python found on PATH or under D:\develop\sdk. Pass -Python explicitly."
}

if (-not (Test-Path $Config)) {
    $message = "No connector binding at $Config. Run install.ps1 first: it writes " +
               "a template whose send allow list is empty, which means every send " +
               "is refused until somebody at this keyboard says otherwise."
    throw $message
}

$Python = Find-Python -Hint $Python
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# -X utf8 because a session title can contain anything, and the default
# encoding on this platform is not UTF-8.
$arguments = @('-X', 'utf8', '-m', 'fleet_mcp.node.connector', '--config', $Config)

$env:PYTHONPATH = $RepoRoot
$env:PYTHONIOENCODING = 'utf-8'
Set-Location $RepoRoot

if ($Foreground) {
    # Hand-debugging: attached, unredirected, Ctrl-C works.
    & $Python @arguments
    exit $LASTEXITCODE
}

# Two files, not one: a launcher that points stdout and stderr at the same path
# is ambiguous about which stream an interleaved line came from.
$process = Start-Process -FilePath $Python -ArgumentList $arguments `
    -WorkingDirectory $RepoRoot -NoNewWindow -PassThru `
    -RedirectStandardOutput $OutputLog -RedirectStandardError $ErrorLog

$process.WaitForExit()
exit $process.ExitCode
