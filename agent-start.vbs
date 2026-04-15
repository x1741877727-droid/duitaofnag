' Remote Agent 静默启动 (无控制台窗口)
' 双击启动，后台运行，不会出现黑窗口

Dim ws, py, script, pid_file, pid
ws = CreateObject("WScript.Shell")
py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe"
script = "D:\game-automation\duitaofnag\remote_agent.py"
pid_file = "D:\game-automation\duitaofnag\.agent.pid"

' 检查是否已在运行
If CreateObject("Scripting.FileSystemObject").FileExists(pid_file) Then
    pid = Trim(CreateObject("Scripting.FileSystemObject").OpenTextFile(pid_file).ReadAll())
    ' 检查进程是否还活着
    Dim result
    result = ws.Run("cmd /c tasklist /FI ""PID eq " & pid & """ | findstr " & pid, 0, True)
    If result = 0 Then
        MsgBox "Remote Agent 已在运行 (PID=" & pid & ")", 64, "Remote Agent"
        WScript.Quit
    End If
End If

' 启动
ws.Run Chr(34) & py & Chr(34) & " " & Chr(34) & script & Chr(34), 0, False
WScript.Sleep 2000

' 找到新启动的 pythonw PID 存起来
ws.Run "cmd /c for /f ""tokens=2"" %i in ('tasklist /fi ""imagename eq pythonw.exe"" /fo list ^| findstr PID') do echo %i > " & Chr(34) & pid_file & Chr(34), 0, True
MsgBox "Remote Agent 已启动！", 64, "Remote Agent"
