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
Keep this file pure ASCII: the machines it lands on may have a GBK code page.
#>
param(
    [string]$Python = 'python',
    [string]$Tool = (Join-Path $PSScriptRoot '..\serveros_secret.py')
)
$ErrorActionPreference = 'Continue'
$state = Join-Path $env:LOCALAPPDATA 'ServerOS\state'
New-Item -ItemType Directory -Force -Path $state | Out-Null
$log = Join-Path $state 'secrets-sync.log'
$env:PYTHONIOENCODING = 'utf-8'
$out = & $Python $Tool sync 2>&1 | Out-String
$code = $LASTEXITCODE
$stamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
$lines = @()
if (Test-Path -LiteralPath $log) { $lines = @(Get-Content -LiteralPath $log -Tail 199) }
$lines += "$stamp exit=$code $($out.Trim() -replace '\r?\n', ' | ')"
[System.IO.File]::WriteAllLines($log, [string[]]$lines, (New-Object System.Text.UTF8Encoding($false)))
exit $code
