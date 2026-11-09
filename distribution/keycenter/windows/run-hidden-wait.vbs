' run-hidden-wait.vbs  <script.ps1> [args...]
'
' A scheduled task whose action names powershell.exe or pwsh.exe flashes a
' console window on every start, whatever -WindowStyle says: Windows allocates
' the console before PowerShell gets control, so by the time it can hide itself
' the window has already been shown.
'
' wscript.exe is a GUI-subsystem binary and allocates no console, so the same
' command started from here has nothing to flash.
'
' Waiting (bWaitOnReturn = True) and propagating the exit code matter as much
' as the hiding: -MultipleInstances IgnoreNew and a restart-on-failure setting
' only do anything while Task Scheduler still owns a live action.
'
' Same launcher as fleet_mcp/node/run-hidden-wait.vbs; the deploy kits are
' kept self-contained rather than sharing a path across the tree.
'
' Keep this file pure ASCII: the machines it lands on may have a GBK code page.
Option Explicit
Dim shell, command, i, result
If WScript.Arguments.Count = 0 Then WScript.Quit 2
command = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ _
        & WScript.Arguments(0) & """"
For i = 1 To WScript.Arguments.Count - 1
    command = command & " """ & WScript.Arguments(i) & """"
Next
Set shell = CreateObject("WScript.Shell")
result = shell.Run(command, 0, True)
WScript.Quit result
