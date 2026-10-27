' Keep Task Scheduler attached to the real launcher and return its exit code.
' The window stays hidden; waiting lets IgnoreNew and restart policy work.
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
