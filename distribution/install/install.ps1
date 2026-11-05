<#
ServerOS Fleet — one-command install / update of the commander plugin (Windows).

  powershell -ExecutionPolicy Bypass -File install.ps1            install or update
  powershell -ExecutionPolicy Bypass -File install.ps1 -DryRun    print, change nothing
  powershell -ExecutionPolicy Bypass -File install.ps1 -Version 1.3.1

Claude Code and Codex CLI are installed or updated in place; ChatGPT, Claude
web/desktop and other agents are printed as one-time manual steps. Packages come
from the GitHub Release (`gh` signed in) or from this checkout. No credential is
downloaded, written or asked for.
#>
param([switch]$DryRun, [string]$Version)
$ErrorActionPreference = 'Stop'
$repo = 'ProfessorZhi/serveros'
$tag = if ($Version) { "fleet-v$Version" } else { 'fleet-latest' }
$home_dir = Join-Path $env:LOCALAPPDATA 'ServerOS\fleet-plugins'
$work = Join-Path $env:TEMP ("serveros-fleet-" + [guid]::NewGuid().ToString('N'))
$checkout = $null
$channels = Join-Path $PSScriptRoot '..\channels.yaml'
if (Test-Path -LiteralPath $channels) { $checkout = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path }

# A native command's exit code decides, never its stderr. Under
# $ErrorActionPreference = 'Stop', Windows PowerShell 5 turns redirected stderr
# into a terminating NativeCommandError, so a step that is allowed to fail
# ("uninstall the old plugin, if it is there") would end the script. Each call
# therefore runs with 'Continue' in its own scope and is judged by $LASTEXITCODE.
function Invoke-Native([string]$exe, [string[]]$argv) {
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 0
    $output = & $exe @argv 2>&1 | Out-String
    return @{ code = $LASTEXITCODE; output = $output }
}

function Run([string]$exe, [string[]]$argv, [switch]$Tolerate) {
    Write-Host ("+ $exe " + ($argv -join ' ') + $(if ($Tolerate) { '  (may already be done)' } else { '' }))
    if ($DryRun) { return }
    $result = Invoke-Native $exe $argv
    if ($result.code -ne 0) {
        if ($Tolerate) { Write-Host "  (skipped: exit $($result.code))"; return }
        throw "$exe $($argv -join ' ') failed ($($result.code)):`n$($result.output)"
    }
}

function Fetch([string]$client) {
    if ($checkout) { return (Join-Path $checkout "distribution\$client") }
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw 'gh is required to download from the private release (gh auth login)' }
    $dest = Join-Path $home_dir $client
    if ($DryRun) { return $dest }
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    $result = Invoke-Native 'gh' @('release', 'download', $tag, '--repo', $repo,
        '--pattern', "serveros-fleet-$client-v*.zip", '--dir', $work, '--clobber')
    if ($result.code -ne 0) { throw "gh release download failed ($($result.code)):`n$($result.output)" }
    if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Recurse -Force }
    $zip = Get-ChildItem -LiteralPath $work -Filter "serveros-fleet-$client-v*.zip" | Select-Object -First 1
    Expand-Archive -LiteralPath $zip.FullName -DestinationPath $dest -Force
    return $dest
}

Write-Host "ServerOS Fleet commander plugin - $tag$(if ($checkout) { " (from checkout $checkout)" })"
$found = $false
try {
    if (Get-Command claude -ErrorAction SilentlyContinue) {
        $found = $true; Write-Host "`n== Claude Code"
        $tree = Fetch 'claude-code'
        Run claude @('plugin', 'uninstall', 'serveros-fleet-v1@serveros-v1') -Tolerate
        Run claude @('plugin', 'marketplace', 'remove', 'serveros-v1') -Tolerate
        Run claude @('plugin', 'uninstall', 'serveros-fleet@serveros-fleet') -Tolerate
        Run claude @('plugin', 'marketplace', 'remove', 'serveros-fleet') -Tolerate
        Run claude @('plugin', 'marketplace', 'add', $tree)
        Run claude @('plugin', 'install', 'serveros-fleet@serveros-fleet')
    }
    if (Get-Command codex -ErrorAction SilentlyContinue) {
        $found = $true; Write-Host "`n== Codex CLI"
        $tree = Fetch 'codex'
        Run codex @('plugin', 'marketplace', 'add', $tree) -Tolerate
        Run codex @('plugin', 'add', 'serveros-fleet@serveros-fleet')
    }
} finally {
    if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
}
if (-not $found) { Write-Host 'No Claude Code or Codex CLI on this machine.' }
Write-Host @'

== Clients installed by hand (once; later versions replace the same entry)
  ChatGPT (web) + Codex (desktop):
    upload serveros-fleet-openai-v<version>.zip from the release on
    https://chatgpt.com/plugins/plugin_asdk_app_6aec2811173881918325a3dc82742d8d
  Claude (web / desktop / Cowork):
    Settings -> Connectors -> add custom connector  https://fleet-v1.professorzhi.fun/mcp
  Any other MCP agent (DeepSeek Harness, ...):
    serveros-fleet-mcp-v<version>.zip - mcp.json + skills/, see its README

Each client signs in once on the Fleet consent page: account, password, one-time code.
'@
