<#
Publish this Windows device's Core-Lite status: the Windows twin of
deploy/publish-status.sh.

Runs the collector and the status builder and atomically replaces one status
document that only this account can read. It writes nothing else, starts
nothing and reaches no network.

  collector ─► node-resource-snapshot.v1 ─► core-lite-status ─► status.json
                                                                   │
            Fleet node connector  /v1/core/status  ◄─ core_snapshot_path

Usage:
  publish-status.ps1 -Bin <dir with the two .exe> -ProfilePath <profile.json> [-Out <path>]
                     [-Filesystem ROLE=PATH ...] [-NetworkTarget NAME=HOST:PORT ...]

With -NetworkTarget, this device also measures its network paths (TCP connect
time and failures, probe-network.ps1) into network.json next to the status, at
most every five minutes and AFTER the status is in place, and folds the last
fresh measurement into the next document as `network.readonly`. Only the path
names reach the document; hosts and ports stay in this task's arguments.
#>
param(
    [Parameter(Mandatory = $true)][string]$Bin,
    [Parameter(Mandatory = $true)][string]$ProfilePath,
    [string]$Out = (Join-Path $env:LOCALAPPDATA 'ServerOS\state\core-lite\status.json'),
    [string[]]$Filesystem = @(),
    [string[]]$NetworkTarget = @()
)
$ErrorActionPreference = 'Stop'
# A scheduled task passes one comma-joined -NetworkTarget: a [string[]]
# parameter cannot be repeated on a -File command line.
$NetworkTarget = @($NetworkTarget | ForEach-Object { $_ -split ',' } | Where-Object { $_ })

if (-not [System.IO.Path]::IsPathRooted($Out)) { throw "-Out must be absolute" }
$collector = Join-Path $Bin 'serveros-node-resource-snapshot.exe'
$builder = Join-Path $Bin 'core-lite-status.exe'
foreach ($exe in @($collector, $builder)) {
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "not found: $exe" }
}

$dir = Split-Path -Parent $Out
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$work = Join-Path $dir (".publish." + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $fsArgs = @()
    foreach ($spec in $Filesystem) { $fsArgs += @('--filesystem', $spec) }
    $snapshot = Join-Path $work 'snapshot.json'
    $status = Join-Path $work 'status.json'

    # UTF-8 without a BOM on both Windows PowerShell 5.1 and PowerShell 7.
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $text = (& $collector --compact @fsArgs | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "the collector failed ($LASTEXITCODE)" }
    [System.IO.File]::WriteAllText($snapshot, $text, $utf8)
    $text = (& $builder --profile $ProfilePath --snapshot $snapshot | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "the status builder failed ($LASTEXITCODE)" }
    [System.IO.File]::WriteAllText($status, $text, $utf8)

    $doc = Get-Content -LiteralPath $status -Raw | ConvertFrom-Json
    if ($doc.schema -ne 'serveros/core-lite-status.v1') { throw "the status is not a core-lite-status.v1" }

    $network = Join-Path $dir 'network.json'
    if ($NetworkTarget.Count -gt 0 -and (Test-Path -LiteralPath $network -PathType Leaf)) {
        # Folded as text, not re-serialised: the builder's document is kept
        # byte for byte and only gains one observation row.
        try {
            $net = Get-Content -LiteralPath $network -Raw | ConvertFrom-Json
            $age = ((Get-Date).ToUniversalTime() - ([datetime]$net.sample_time).ToUniversalTime()).TotalSeconds
            if ($net.paths -and $age -ge 0 -and $age -le 900 -and $text -notmatch '"network\.readonly"') {
                $value = (Get-Content -LiteralPath $network -Raw).Trim()
                $row = '{"name":"network.readonly","state":"PRESENT","completeness":"COMPLETE","value":' + $value + '},'
                $folded = ([regex]'"observations"\s*:\s*\[').Replace($text, '$0' + $row.Replace('$', '$$'), 1)
                if ($folded -ne $text) { [System.IO.File]::WriteAllText($status, $folded, $utf8) }
            }
        } catch { }
    }

    # Same volume, so this is a rename: a reader sees the old document or the
    # new one, never half of either.
    Move-Item -LiteralPath $status -Destination $Out -Force
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}

# The probe runs after the document is in place, so it never makes one late.
if ($NetworkTarget.Count -gt 0) {
    $network = Join-Path $dir 'network.json'
    $old = -not (Test-Path -LiteralPath $network -PathType Leaf) -or `
        ((Get-Item -LiteralPath $network).LastWriteTimeUtc -lt (Get-Date).ToUniversalTime().AddMinutes(-5))
    if ($old) {
        try {
            & (Join-Path $PSScriptRoot 'probe-network.ps1') -Out $network -Target $NetworkTarget
        } catch { }
    }
}
