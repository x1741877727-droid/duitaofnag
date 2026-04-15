' Remote Agent - Stop
Dim ws, fso, pid_file, pid
Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

pid_file = "D:\game-automation\duitaofnag\.agent.pid"

If Not fso.FileExists(pid_file) Then
    MsgBox "Remote Agent is not running.", 48, "Remote Agent"
    WScript.Quit
End If

pid = Trim(fso.OpenTextFile(pid_file).ReadAll())
ws.Run "cmd /c taskkill /PID " & pid & " /F", 0, True
fso.DeleteFile pid_file
MsgBox "Remote Agent stopped.", 64, "Remote Agent"
