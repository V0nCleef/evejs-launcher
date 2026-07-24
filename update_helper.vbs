' EveJS Launcher V2 — silent update helper
' Wraps the batch logic silently — no console window at all.
' Does: wait → delete old → move new → wait → launch via explorer.exe

Dim oldExe, newExe, restart, fso, attempt, wsh

Set wsh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

oldExe = WScript.Arguments(0)
newExe = WScript.Arguments(1)
restart = (WScript.Arguments.Count >= 3 And WScript.Arguments(2) = "--restart")

If Not fso.FileExists(newExe) Then WScript.Quit 1

' Wait for old launcher to fully exit
WScript.Sleep 5000

' Delete old exe (retry if locked)
For attempt = 1 To 30
    On Error Resume Next
    fso.DeleteFile oldExe, True
    If Not fso.FileExists(oldExe) Then Exit For
    On Error GoTo 0
    WScript.Sleep 1000
Next

If fso.FileExists(oldExe) Then WScript.Quit 2

' Move new exe into place
On Error Resume Next
fso.MoveFile newExe, oldExe
On Error GoTo 0

If Not fso.FileExists(oldExe) Then WScript.Quit 3

' Let filesystem settle
WScript.Sleep 3000

' Launch via explorer.exe — the only method proven to work without
' triggering the "Failed to load Python DLL" dialog.
If restart Then
    wsh.Run "explorer.exe " & Chr(34) & oldExe & Chr(34), 0, False
End If

WScript.Quit 0
