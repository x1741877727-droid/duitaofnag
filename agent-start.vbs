' Remote Agent 静默启动 (无控制台窗口)
Dim ws, fso, py, script, pid_file, pid
Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe"
script = "D:\game-automation\duitaofnag\remote_agent.py"
pid_file = "D:\game-automation\duitaofnag\.agent.pid"

' 检查是否已在运行
If fso.FileExists(pid_file) Then
    pid = Trim(fso.OpenTextFile(pid_file).ReadAll())
    If ws.Run("cmd /c tasklist /FI ""PID eq " & pid & """ | findstr " & pid, 0, True) = 0 Then
        MsgBox "Remote Agent 已在运行 (PID=" & pid & ")", 64, "Remote Agent"
        WScript.Quit
    End If
End If

' 后台启动
ws.Run Chr(34) & py & Chr(34) & " " & Chr(34) & script & Chr(34), 0, False
WScript.Sleep 3000

' 保存 PID
Dim proc_pid
proc_pid = ""
Dim ts
Set ts = fso.OpenTextFile(pid_file, 2, True)
' 用 tasklist 找最新的 pythonw PID
Dim stdout
stdout = ws.Exec("powershell -Command ""(Get-Process pythonw | Sort-Object StartTime | Select-Object -Last 1).Id""").StdOut.ReadAll()
ts.Write Trim(stdout)
ts.Close

MsgBox "Remote Agent 已启动！" & vbCrLf & "Token 不变，后台运行中", 64, "Remote Agent"
