' Remote Agent 停止脚本
Dim ws, fso, pid_file, pid
Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

pid_file = "D:\game-automation\duitaofnag\.agent.pid"

If Not fso.FileExists(pid_file) Then
    MsgBox "Remote Agent 未在运行", 48, "Remote Agent"
    WScript.Quit
End If

pid = Trim(fso.OpenTextFile(pid_file).ReadAll())
ws.Run "cmd /c taskkill /PID " & pid & " /F", 0, True
fso.DeleteFile pid_file
MsgBox "Remote Agent 已停止", 64, "Remote Agent"
