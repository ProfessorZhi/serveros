<#
Register the Windows Core-Lite publisher as a scheduled task for THIS user:
the Windows twin of serveros-core-lite-publish.timer.

Task Scheduler's smallest repetition is one minute (systemd's timer runs every
30 s on Linux). The task runs as the current user, interactive token, least
privilege, and ignores a new start while one is running. It does nothing until
this script is run, and -Unregister removes it.

The action is wscript.exe running run-hidden-wait.vbs, not PowerShell itself.
A task that names pwsh.exe or powershell.exe pops a console window every
minute, because the console is allocated before PowerShell can hide it; see
docs/fleet-reverse-tunnel-recovery.md, "Windows process lifetime". Re-running
this script therefore keeps the window away instead of bringing it back.

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
$launcher = Join-Path $PSScriptRoot 'run-hidden-wait.vbs'
foreach ($path in @($script, $launcher)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "missing $path" }
}

$arguments = @('//B', "`"$launcher`"", "`"$script`"",
               '-Bin', "`"$Bin`"", '-ProfilePath', "`"$ProfilePath`"")
foreach ($spec in $Filesystem) { $arguments += @('-Filesystem', "`"$spec`"") }

$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ($arguments -join ' ')
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $name -TaskPath $folder -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "registered $folder$name (every minute, as $($principal.UserId))"
