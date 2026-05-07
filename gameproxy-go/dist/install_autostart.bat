@echo off
chcp 65001 >nul

REM ========================================================================
REM  Step 2 one-time setup. Double-click to run.
REM  After this, gameproxy auto-starts as SYSTEM on every boot. No more UAC.
REM ========================================================================

REM === Self-elevate ===
net session >nul 2>&1
if %errorLevel% NEQ 0 (
    echo Not admin. Re-launching elevated via UAC...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo ============================================
echo  Running as Administrator
echo ============================================

set "GP_EXE=D:\game-automation\duitaofnag\gameproxy-go\dist\gameproxy.exe"
set "GP_LOG=D:\game-automation\duitaofnag\gameproxy-go\dist\proxy.log"
set "PS_BOOT=D:\game-automation\duitaofnag\gameproxy-go\dist\boot_gameproxy.ps1"

REM === Write boot PS1 — sync-wait so task stays running, gameproxy stays alive ===
> "%PS_BOOT%" echo $gp = "%GP_EXE%"
>> "%PS_BOOT%" echo $log = "%GP_LOG%"
>> "%PS_BOOT%" echo Remove-Item -Force $log -ErrorAction SilentlyContinue
>> "%PS_BOOT%" echo $proc = Start-Process -FilePath $gp -ArgumentList "-host","0.0.0.0","-port","9900","-tun-mode","-tun-name","gp-tun","-log-file",$log -WindowStyle Hidden -PassThru
>> "%PS_BOOT%" echo $waited = 0
>> "%PS_BOOT%" echo while (-not (Get-NetAdapter -Name "gp-tun" -ErrorAction SilentlyContinue) -and $waited -lt 30) {
>> "%PS_BOOT%" echo   Start-Sleep -Seconds 1
>> "%PS_BOOT%" echo   $waited++
>> "%PS_BOOT%" echo }
>> "%PS_BOOT%" echo $ifIdx = (Get-NetAdapter -Name "gp-tun" -ErrorAction SilentlyContinue).InterfaceIndex
>> "%PS_BOOT%" echo if ($ifIdx) {
>> "%PS_BOOT%" echo   New-NetIPAddress -InterfaceIndex $ifIdx -IPAddress 26.26.26.1 -PrefixLength 30 -ErrorAction SilentlyContinue ^| Out-Null
>> "%PS_BOOT%" echo   $cidrs = @("122.96.96.0/24","36.155.0.0/16","59.83.207.0/24","182.50.10.0/24","180.109.171.0/24","180.102.211.0/24","117.89.177.0/24","222.94.109.0/24","43.135.105.0/24","43.159.233.0/24","129.226.102.0/24","129.226.103.0/24","129.226.107.0/24")
>> "%PS_BOOT%" echo   foreach ($c in $cidrs) {
>> "%PS_BOOT%" echo     New-NetRoute -InterfaceIndex $ifIdx -DestinationPrefix $c -NextHop "26.26.26.2" -RouteMetric 1 -ErrorAction SilentlyContinue ^| Out-Null
>> "%PS_BOOT%" echo   }
>> "%PS_BOOT%" echo }
>> "%PS_BOOT%" echo $proc.WaitForExit()

echo.
echo === Stop old gameproxy + delete old task ===
taskkill /IM gameproxy.exe /F 2>nul
schtasks /Delete /TN "GameProxyAutoStart" /F >nul 2>&1

echo.
echo === Register Scheduled Task via PowerShell (unlimited execution time) ===
powershell -Command ^
  "$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-ExecutionPolicy Bypass -WindowStyle Hidden -File \"%PS_BOOT%\"';" ^
  "$trigger = New-ScheduledTaskTrigger -AtStartup;" ^
  "$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest;" ^
  "$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 9999) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1);" ^
  "Register-ScheduledTask -TaskName 'GameProxyAutoStart' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force ^| Out-Null;" ^
  "Write-Host 'Task registered.'"

echo.
echo === Run task now ===
schtasks /Run /TN "GameProxyAutoStart"
echo Waiting 12s for wintun + routes to apply...
timeout 12 >nul

echo.
echo === Verify gameproxy.exe running ===
tasklist | findstr gameproxy

echo.
echo === Verify gp-tun adapter ===
powershell -Command "Get-NetAdapter | Where-Object { $_.Name -eq 'gp-tun' } | Format-Table Name,Status,InterfaceIndex,InterfaceDescription -AutoSize"

echo.
echo === Verify routes pointing to 26.26.26.2 ===
powershell -Command "Get-NetRoute -ErrorAction SilentlyContinue | Where-Object { $_.NextHop -eq '26.26.26.2' } | Format-Table DestinationPrefix,NextHop,RouteMetric -AutoSize"

echo.
echo === gameproxy.exe log tail ===
powershell -Command "if (Test-Path '%GP_LOG%') { Get-Content '%GP_LOG%' -Tail 12 } else { Write-Host 'log not found' }"

echo.
echo ============================================
echo  Setup done.
echo  - gameproxy auto-starts on boot (SYSTEM, no UAC)
echo  - PS1 sync-waits so task + process stay alive
echo  - Routes for 13 game-server CIDRs point to gp-tun
echo  Uninstall: schtasks /Delete /TN GameProxyAutoStart /F
echo ============================================
pause
