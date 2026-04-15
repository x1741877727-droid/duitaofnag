' Remote Agent - Start (background, no window)
Dim ws, fso, py, script, pid_file, pid
Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe"
script = "D:\game-automation\duitaofnag\remote_agent.py"
pid_file = "D:\game-automation\duitaofnag\.agent.pid"

' Check if already running
If fso.FileExists(pid_file) Then
    pid = Trim(fso.OpenTextFile(pid_file).ReadAll())
    If ws.Run("cmd /c tasklist /FI ""PID eq " & pid & """ | findstr " & pid, 0, True) = 0 Then
        MsgBox "Remote Agent is already running (PID=" & pid & ")", 64, "Remote Agent"
        WScript.Quit
    End If
End If

' Start in background
ws.Run Chr(34) & py & Chr(34) & " " & Chr(34) & script & Chr(34), 0, False
WScript.Sleep 3000

' Save PID
Dim ts, stdout
Set ts = fso.OpenTextFile(pid_file, 2, True)
stdout = ws.Exec("powershell -Command ""(Get-Process pythonw | Sort-Object StartTime | Select-Object -Last 1).Id""").StdOut.ReadAll()
ts.Write Trim(stdout)
ts.Close

MsgBox "Remote Agent started! Running in background." & vbCrLf & "Token is unchanged.", 64, "Remote Agent"
