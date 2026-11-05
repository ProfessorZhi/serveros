<#
In-place update of a Windows device's ServerOS layers: the Windows twin of
`device_kit.py update`. Run it on the device, from the serveros checkout the
node connector runs from (the script finds the checkout from its own path).

  1 pull         git pull --ff-only origin main (this checkout)
  2 family       control_plane.py rollout --profile <family> --rev <pinned> --apply, then readback
  3 collector    -FromRelease: download serveros-core-lite-windows-x64.zip from the
                 `fleet-latest` GitHub Release (needs `gh` logged in); otherwise build with CMake/MSVC
  4 publish      publish once (must produce a core-lite-status.v1), re-register the 1-minute task
  5 connector    print the restart of the ServerOS-FleetNode-Connector task (run it to
                 pick up the new code; the update itself stops nothing)
  6 versions     write %LOCALAPPDATA%\ServerOS\state\device-versions.json

Without -Apply it prints the steps and changes nothing. It never reads, prints or
copies a credential, and stops at the first failing step.

Usage:
  powershell -ExecutionPolicy Bypass -File distribution\windows\update.ps1 -Device work-fj [-AbilityRev main] [-FromRelease] [-Apply]
#>
param(
    [string]$Device = 'work-fj',
    [string]$AbilityRev = 'main',
    [string]$AbilityRepo,
    [switch]$FromRelease,
    [switch]$NoPull,
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'

$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $AbilityRepo) { $AbilityRepo = Join-Path (Split-Path $root -Parent) 'ability-management' }
$kitPath = Join-Path $root "distribution\devices\$Device\DEVICE.json"
if (-not (Test-Path -LiteralPath $kitPath)) { throw "no device kit for '$Device' at $kitPath" }
$kit = Get-Content -LiteralPath $kitPath -Raw | ConvertFrom-Json
if ($kit.os -ne 'windows') { throw "$Device is a $($kit.os) device; use device_kit.py update" }
$family = $kit.layers.family.profile
$coreLite = $kit.layers.monitor.core_lite
$state = Join-Path $env:LOCALAPPDATA 'ServerOS\state'
$binRelease = Join-Path $env:LOCALAPPDATA 'ServerOS\bin\core-lite'
$buildDir = Join-Path $env:LOCALAPPDATA 'ServerOS\build\core-lite'
$bin = if ($FromRelease) { $binRelease } else { Join-Path $buildDir 'Release' }
$controlPlane = Join-Path $AbilityRepo 'skills\ability-management\payload\control_plane.py'

Write-Host "# update $Device ($($kit.device_ref)) from $root"
Write-Host (" 1. git pull --ff-only origin main" + $(if ($NoPull) { '  (skipped: -NoPull)' } else { '' }))
Write-Host " 2. ability family '$family' at $AbilityRev (pinned to a commit), then readback"
if ($coreLite.profile) {
    Write-Host " 3. collector: $(if ($FromRelease) { 'download from release fleet-latest' } else { 'build with CMake/MSVC' }) -> $bin"
    Write-Host " 4. publish once, re-register the 1-minute publish task"
} else {
    Write-Host " 3-4. no resource collector declared for this device"
}
Write-Host " 5. print the connector restart (if the task is registered)"
Write-Host " 6. write $state\device-versions.json"
if (-not $Apply) { Write-Host "`n(dry run - add -Apply to run these steps)"; return }

function Invoke-Step([string]$title, [scriptblock]$body) {
    Write-Host "== $title"
    $global:LASTEXITCODE = 0
    & $body
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "$title failed ($LASTEXITCODE)" }
}

Push-Location $root
try {
    if (-not $NoPull) { Invoke-Step 'pull' { git pull --ff-only origin main } }

    $rev = $AbilityRev
    if ($rev -eq 'main') {
        Invoke-Step 'fetch ability-management' { git -C $AbilityRepo fetch -q origin main }
        $rev = (git -C $AbilityRepo rev-parse origin/main).Trim()
    }
    Invoke-Step "family rollout $family @ $rev" {
        python $controlPlane rollout --profile $family --rev $rev --agent all --apply
    }
    Invoke-Step 'family readback' { python $controlPlane readback --profile $family --agent all }

    $collectorVersion = $null
    if ($coreLite.profile) {
        if ($FromRelease) {
            $zip = Join-Path $env:TEMP 'serveros-core-lite-windows-x64.zip'
            Invoke-Step 'download collector' {
                gh release download fleet-latest --repo ProfessorZhi/serveros `
                    --pattern 'serveros-core-lite-windows-x64.zip' --output $zip --clobber
            }
            New-Item -ItemType Directory -Force -Path $binRelease | Out-Null
            Expand-Archive -LiteralPath $zip -DestinationPath $binRelease -Force
        } else {
            Invoke-Step 'configure collector' {
                cmake -S (Join-Path $root 'native\node-core\core-lite\windows') -B $buildDir -A x64
            }
            Invoke-Step 'build collector' { cmake --build $buildDir --config Release }
        }
        $prof = (Resolve-Path (Join-Path $root $coreLite.profile.path)).Path
        $deploy = Join-Path $root 'native\node-core\core-lite\windows\deploy'
        Invoke-Step 'publish once' {
            powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $deploy 'publish-status.ps1') -Bin $bin -ProfilePath $prof
        }
        Invoke-Step 'register publish task' {
            powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $deploy 'register-publish-task.ps1') -Bin $bin -ProfilePath $prof
        }
        $collectorVersion = (& (Join-Path $bin 'serveros-node-resource-snapshot.exe') --version | Out-String).Trim()
    }

    $connectorTask = Get-ScheduledTask -TaskName 'ServerOS-FleetNode-Connector' -ErrorAction SilentlyContinue

    $record = [ordered]@{
        schema              = 'serveros/device-versions.v1'
        device              = $kit.device
        device_ref          = $kit.device_ref
        recorded_at         = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        serveros_commit     = (git rev-parse HEAD).Trim()
        core_plugin         = $null
        ability_family      = [ordered]@{ profile = $family; revision = $rev }
        core_lite_collector = $collectorVersion
        commander           = $null
    }
    New-Item -ItemType Directory -Force -Path $state | Out-Null
    $out = Join-Path $state 'device-versions.json'
    [System.IO.File]::WriteAllText($out, ($record | ConvertTo-Json -Depth 4), (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "versions: $out"
    if ($connectorTask) {
        Write-Host "`nthe connector runs the new code after a restart:"
        Write-Host "  Stop-ScheduledTask -TaskName 'ServerOS-FleetNode-Connector'; Start-ScheduledTask -TaskName 'ServerOS-FleetNode-Connector'"
    }
} finally {
    Pop-Location
}
