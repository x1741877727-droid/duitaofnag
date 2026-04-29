' Remote Agent - Stop (also kills any cloudflared spawned by it)
Dim ws, cmd, ret
Set ws = CreateObject("WScript.Shell")
cmd = "powershell -Command ""$conn = Get-NetTCPConnection -LocalPort 9100 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if ($conn) { Stop-Process -Id $conn.OwningProcess -Force; exit 0 } else { exit 1 }"""
ret = ws.Run(cmd, 0, True)

' agent 起的 cloudflared 不会随父退出 (Popen 没绑 job object), 这里清掉, 避免下次启动叠加
ws.Run "cmd /c taskkill /F /IM cloudflared.exe >nul 2>nul", 0, True

If ret = 0 Then
    MsgBox "Remote Agent stopped (cloudflared 隧道也已清理).", 64, "Remote Agent"
Else
    MsgBox "Remote Agent is not running.", 48, "Remote Agent"
End If
