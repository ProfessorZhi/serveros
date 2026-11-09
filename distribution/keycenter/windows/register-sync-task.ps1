<#
Register the key-center sync as a scheduled task for THIS user: the Windows
twin of serveros-secrets-sync.timer (every 5 minutes).

The action is wscript.exe running run-hidden-wait.vbs, which runs
sync-secrets.ps1 without a console window (a task naming powershell.exe itself
flashes one every run). The task runs as the current user, interactive token,
least privilege, and ignores a new start while one is running. -Unregister
removes it.

Before the first run, on this device: `serveros_secret.py keygen` (send the
public key to the owner) and write %USERPROFILE%\.config\serveros\secrets.yaml
(repo, device). This script reads no secret and changes nothing else.

Usage:
  register-sync-task.ps1 [-Python <python.exe>] [-Minutes 5]
  register-sync-task.ps1 -Unregister
#>
param(
    [string]$Python,
    [int]$Minutes = 5,
    [switch]$Unregister
)
$ErrorActionPreference = 'Stop'
$name = 'ServerOS Secrets sync'
$folder = '\ServerOS\'

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $name -TaskPath $folder -Confirm:$false
    Write-Host "removed $folder$name"
    return
}
if (-not $Python) {
    # The task runs with the user's PATH, but name the interpreter found now so
    # a later PATH change does not silently break the sync.
    $found = Get-Command python -ErrorAction SilentlyContinue
    if (-not $found) { throw "python not found; pass -Python <path to python.exe>" }
    $Python = $found.Source
}
$script = Join-Path $PSScriptRoot 'sync-secrets.ps1'
$launcher = Join-Path $PSScriptRoot 'run-hidden-wait.vbs'
$tool = (Resolve-Path (Join-Path $PSScriptRoot '..\serveros_secret.py')).Path
foreach ($path in @($script, $launcher, $tool)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "missing $path" }
}
$config = Join-Path $env:USERPROFILE '.config\serveros\secrets.yaml'
if (-not (Test-Path -LiteralPath $config)) {
    Write-Warning "no $config yet: the task will fail until repo: and device: are written there"
}

$arguments = @('//B', "`"$launcher`"", "`"$script`"", '-Python', "`"$Python`"", '-Tool', "`"$tool`"")
$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ($arguments -join ' ')
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $Minutes)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 3) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $name -TaskPath $folder -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "registered $folder$name (every $Minutes minutes, as $($principal.UserId))"
