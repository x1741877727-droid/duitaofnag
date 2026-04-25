' Remote Agent - Stop
Dim ws, cmd, ret
Set ws = CreateObject("WScript.Shell")
cmd = "powershell -Command ""$conn = Get-NetTCPConnection -LocalPort 9100 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if ($conn) { Stop-Process -Id $conn.OwningProcess -Force; exit 0 } else { exit 1 }"""
ret = ws.Run(cmd, 0, True)
If ret = 0 Then
    MsgBox "Remote Agent stopped.", 64, "Remote Agent"
Else
    MsgBox "Remote Agent is not running.", 48, "Remote Agent"
End If
