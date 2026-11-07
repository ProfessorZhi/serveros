<#
ServerOS Fleet - one-command install / update of the commander plugin (Windows).

  powershell -ExecutionPolicy Bypass -File install.ps1            install or update (from GitHub main)
  powershell -ExecutionPolicy Bypass -File install.ps1 -DryRun    print, change nothing
  powershell -ExecutionPolicy Bypass -File install.ps1 -Version 1.3.2   pin a release (tag fleet-v1.3.2)
  powershell -ExecutionPolicy Bypass -File install.ps1 -Local     from this checkout

Claude Code and Codex CLI install from GitHub: the repository root is the
`serveros-fleet` marketplace for both, so later updates are
  claude plugin marketplace update serveros-fleet; claude plugin update serveros-fleet@serveros-fleet
  codex plugin marketplace upgrade serveros-fleet; codex plugin add serveros-fleet@serveros-fleet
ChatGPT, Claude web/desktop and other agents are printed as one-time manual
steps. The repository is public; no credential is downloaded, written or asked for.
#>
param([switch]$DryRun, [string]$Version, [switch]$Local)
$ErrorActionPreference = 'Stop'
$repo = 'ProfessorZhi/serveros'
$ref = if ($Version) { "fleet-v$Version" } else { 'main' }
$source = $repo
if ($Local) {
    $channels = Join-Path $PSScriptRoot '..\channels.yaml'
    if (-not (Test-Path -LiteralPath $channels)) { throw '-Local needs this script inside a serveros checkout' }
    $source = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
}

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

Write-Host "ServerOS Fleet commander plugin - $source$(if (-not $Local) { " @ $ref" })"
$found = $false
if (Get-Command claude -ErrorAction SilentlyContinue) {
    $found = $true; Write-Host "`n== Claude Code"
    Run claude @('plugin', 'uninstall', 'serveros-fleet-v1@serveros-v1') -Tolerate
    Run claude @('plugin', 'marketplace', 'remove', 'serveros-v1') -Tolerate
    Run claude @('plugin', 'uninstall', 'serveros-fleet-ingress@serveros') -Tolerate
    Run claude @('plugin', 'marketplace', 'remove', 'serveros') -Tolerate
    Run claude @('plugin', 'uninstall', 'serveros-fleet@serveros-fleet') -Tolerate
    Run claude @('plugin', 'marketplace', 'remove', 'serveros-fleet') -Tolerate
    if ($Local) {
        Run claude @('plugin', 'marketplace', 'add', $source)
    } else {
        Run claude @('plugin', 'marketplace', 'add', "https://github.com/$source.git#$ref",
            '--sparse', '.claude-plugin', 'distribution/claude-code')
    }
    Run claude @('plugin', 'install', 'serveros-fleet@serveros-fleet')
}
if (Get-Command codex -ErrorAction SilentlyContinue) {
    $found = $true; Write-Host "`n== Codex CLI"
    Run codex @('plugin', 'remove', 'serveros-fleet@serveros-fleet') -Tolerate
    Run codex @('plugin', 'marketplace', 'remove', 'serveros-fleet') -Tolerate
    if ($Local) {
        Run codex @('plugin', 'marketplace', 'add', $source)
    } else {
        Run codex @('plugin', 'marketplace', 'add', $source, '--ref', $ref,
            '--sparse', '.agents', '--sparse', 'distribution/codex')
    }
    Run codex @('plugin', 'add', 'serveros-fleet@serveros-fleet')
}
if (-not $found) { Write-Host 'No Claude Code or Codex CLI on this machine.' }
Write-Host @'

== Update later (or run this script again)
  claude plugin marketplace update serveros-fleet; claude plugin update serveros-fleet@serveros-fleet
  codex plugin marketplace upgrade serveros-fleet; codex plugin add serveros-fleet@serveros-fleet

== Clients installed by hand (once; a later upload replaces the same entry)
  ChatGPT (web) + Codex (desktop): upload
    https://github.com/ProfessorZhi/serveros/releases/download/fleet-latest/serveros-fleet-openai.zip
    on https://chatgpt.com/plugins/plugin_asdk_app_6aed2ec2e0e48191b438082e444c3210
  Claude (web / desktop / Cowork):
    Settings -> Connectors -> add custom connector  https://fleet-v1.professorzhi.fun/mcp
  Any other MCP agent (DeepSeek Harness, ...):
    https://github.com/ProfessorZhi/serveros/releases/download/fleet-latest/serveros-fleet-mcp.zip
    - mcp.json + skills/, see its README

Each client signs in once on the Fleet consent page: account, password, one-time code.
'@
