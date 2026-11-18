# R-1: the daily directory check on Windows (FJPCOS). Read-only: scans and plans.
#   powershell -ExecutionPolicy Bypass -File register-ws-check.ps1 -Repo D:\work\src\serveros
param(
  [Parameter(Mandatory = $true)][string]$Repo,
  [string]$Device = 'fjpcos',
  [string]$At = '07:40'
)
$ErrorActionPreference = 'Stop'
$python = (Get-Command python -ErrorAction Stop).Source
$script = Join-Path $Repo 'distribution\workspace\ws.py'
if (-not (Test-Path -LiteralPath $script)) { throw "not a serveros checkout: $Repo" }
$action = New-ScheduledTaskAction -Execute $python -Argument "`"$script`" check --device $Device --record" -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName 'ServerOS-ws-check' -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "registered ServerOS-ws-check daily at $At for $Device"
