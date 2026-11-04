<#
Register the Windows Core-Lite publisher as a scheduled task for THIS user:
the Windows twin of serveros-core-lite-publish.timer.

Task Scheduler's smallest repetition is one minute (systemd's timer runs every
30 s on Linux). The task runs as the current user, interactive token, least
privilege, and ignores a new start while one is running. It does nothing until
this script is run, and -Unregister removes it.

Usage:
  register-publish-task.ps1 -Bin <dir> -ProfilePath <profile.json> [-Filesystem ROLE=PATH ...]
  register-publish-task.ps1 -Unregister
#>
param(
    [string]$Bin,
    [string]$ProfilePath,
    [string[]]$Filesystem = @(),
    [switch]$Unregister
)
$ErrorActionPreference = 'Stop'
$name = 'ServerOS Core-Lite publish'
$folder = '\ServerOS\'

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $name -TaskPath $folder -Confirm:$false
    Write-Host "removed $folder$name"
    return
}
if (-not $Bin -or -not $ProfilePath) { throw "-Bin and -ProfilePath are required" }

$script = Join-Path $PSScriptRoot 'publish-status.ps1'
$arguments = @('-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass',
               '-File', "`"$script`"", '-Bin', "`"$Bin`"", '-ProfilePath', "`"$ProfilePath`"")
foreach ($spec in $Filesystem) { $arguments += @('-Filesystem', "`"$spec`"") }

$pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $pwsh) { $pwsh = (Get-Command powershell).Source }
$action = New-ScheduledTaskAction -Execute $pwsh -Argument ($arguments -join ' ')
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $name -TaskPath $folder -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "registered $folder$name (every minute, as $($principal.UserId))"
