' Remote Agent - Start (background, no window)
Dim ws, fso
Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

Dim py, script
py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe"
script = "D:\game-automation\duitaofnag\remote_agent.py"

' Check if already running (port 9100)
Dim ret
ret = ws.Run("powershell -Command ""(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',9100)""", 0, True)
If ret = 0 Then
    MsgBox "Remote Agent is already running on port 9100.", 64, "Remote Agent"
    WScript.Quit
End If

' Delete stale pid file if exists
Dim pid_file
pid_file = "D:\game-automation\duitaofnag\.agent.pid"
If fso.FileExists(pid_file) Then fso.DeleteFile pid_file

' Start in background
ws.Run Chr(34) & py & Chr(34) & " " & Chr(34) & script & Chr(34), 0, False
WScript.Sleep 2000
MsgBox "Remote Agent started! Running in background.", 64, "Remote Agent"
