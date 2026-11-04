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
                     [-Filesystem ROLE=PATH ...]
#>
param(
    [Parameter(Mandatory = $true)][string]$Bin,
    [Parameter(Mandatory = $true)][string]$ProfilePath,
    [string]$Out = (Join-Path $env:LOCALAPPDATA 'ServerOS\state\core-lite\status.json'),
    [string[]]$Filesystem = @()
)
$ErrorActionPreference = 'Stop'

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

    # Same volume, so this is a rename: a reader sees the old document or the
    # new one, never half of either.
    Move-Item -LiteralPath $status -Destination $Out -Force
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
