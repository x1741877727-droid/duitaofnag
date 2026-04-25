' Remote Agent - Start (minimized window)
Dim ws, fso, scriptDir, projectRoot
Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

Dim py, script
py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
projectRoot = fso.GetAbsolutePathName(fso.BuildPath(scriptDir, "..\.."))
script = fso.BuildPath(projectRoot, "agents\remote_agent.py")

' Check if already running (port 9100)
Dim ret
ret = ws.Run("powershell -Command ""(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',9100)""", 0, True)
If ret = 0 Then
    MsgBox "Remote Agent is already running on port 9100.", 64, "Remote Agent"
    WScript.Quit
End If

' Start minimized (7 = minimized, no focus)
ws.Run "cmd /c cd /d " & Chr(34) & projectRoot & Chr(34) & " && " & Chr(34) & py & Chr(34) & " " & Chr(34) & script & Chr(34), 7, False
MsgBox "Remote Agent started (minimized in taskbar).", 64, "Remote Agent"
