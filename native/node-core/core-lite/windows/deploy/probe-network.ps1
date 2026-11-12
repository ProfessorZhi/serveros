<#
Measure this Windows device's network paths into one small JSON file: the
twin of net/probe_network.py.

  probe-network.ps1 -Out <absolute path> -Target NAME=HOST:PORT [-Target ...]
                    [-Samples 3] [-TimeoutMs 1500]

For each target: SAMPLES TCP connects (no ICMP, no privilege), the median
connect time of those that succeeded and the share that failed. Only the path
NAME (lower-case word: tokyo-public, tokyo-tailnet, local) is written; hosts
and ports stay in this task's arguments. Observe only: it opens and closes TCP
connections and sends nothing on them.
#>
param(
    [Parameter(Mandatory = $true)][string]$Out,
    [Parameter(Mandatory = $true)][string[]]$Target,
    [int]$Samples = 3,
    [int]$TimeoutMs = 1500
)
$ErrorActionPreference = 'Stop'
if (-not [System.IO.Path]::IsPathRooted($Out)) { throw "-Out must be absolute" }
if ($Samples -lt 1 -or $Samples -gt 20 -or $Target.Count -gt 8) { throw "1-20 samples, at most 8 targets" }

$paths = @()
$names = @{}
foreach ($spec in $Target) {
    if ($spec -notmatch '^(?<name>[a-z0-9][a-z0-9-]{0,23})=\[?(?<host>[^\]]+?)\]?:(?<port>\d{1,5})$') {
        throw "not NAME=HOST:PORT with a lower-case name: $spec"
    }
    $name = $Matches['name']; $hostName = $Matches['host']; $port = [int]$Matches['port']
    if ($names.ContainsKey($name)) { throw "target names must differ" }
    $names[$name] = $true
    $times = @()
    for ($i = 0; $i -lt $Samples; $i++) {
        $client = New-Object System.Net.Sockets.TcpClient
        $watch = [System.Diagnostics.Stopwatch]::StartNew()
        try {
            $task = $client.ConnectAsync($hostName, $port)
            if ($task.Wait($TimeoutMs) -and $client.Connected) { $times += $watch.Elapsed.TotalMilliseconds }
        } catch { } finally { $client.Dispose() }
    }
    $rtt = $null
    if ($times.Count -gt 0) {
        $sorted = $times | Sort-Object
        $mid = [math]::Floor($sorted.Count / 2)
        $rtt = if ($sorted.Count % 2) { $sorted[$mid] } else { ($sorted[$mid - 1] + $sorted[$mid]) / 2 }
        $rtt = [math]::Round($rtt, 1)
    }
    $paths += [ordered]@{ name = $name; rtt_ms = $rtt
                          loss_percent = [math]::Round(100.0 * ($Samples - $times.Count) / $Samples, 1)
                          samples = $Samples }
}
$doc = [ordered]@{ paths = $paths; sample_time = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') }
$dir = Split-Path -Parent $Out
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$tmp = Join-Path $dir (".network." + [guid]::NewGuid().ToString('N'))
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($tmp, (ConvertTo-Json -InputObject $doc -Depth 5 -Compress), $utf8)
Move-Item -LiteralPath $tmp -Destination $Out -Force
