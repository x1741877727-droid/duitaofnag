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

REM === Write boot PS1 (no Chinese inside, only ASCII) ===
> "%PS_BOOT%" echo $gp = "%GP_EXE%"
>> "%PS_BOOT%" echo $log = "%GP_LOG%"
>> "%PS_BOOT%" echo Remove-Item -Force $log -ErrorAction SilentlyContinue
>> "%PS_BOOT%" echo Start-Process -FilePath $gp -ArgumentList "-host","0.0.0.0","-port","9900","-tun-mode","-tun-name","gp-tun","-log-file",$log -WindowStyle Hidden
>> "%PS_BOOT%" echo Start-Sleep 5
>> "%PS_BOOT%" echo $ifIdx = (Get-NetAdapter -Name "gp-tun" -ErrorAction SilentlyContinue).InterfaceIndex
>> "%PS_BOOT%" echo if ($ifIdx) {
>> "%PS_BOOT%" echo   New-NetIPAddress -InterfaceIndex $ifIdx -IPAddress 26.26.26.1 -PrefixLength 30 -ErrorAction SilentlyContinue ^| Out-Null
>> "%PS_BOOT%" echo   $cidrs = @("122.96.96.0/24","36.155.0.0/16","59.83.207.0/24","182.50.10.0/24","180.109.171.0/24","180.102.211.0/24","117.89.177.0/24","222.94.109.0/24","43.135.105.0/24","43.159.233.0/24","129.226.102.0/24","129.226.103.0/24","129.226.107.0/24")
>> "%PS_BOOT%" echo   foreach ($c in $cidrs) {
>> "%PS_BOOT%" echo     New-NetRoute -InterfaceIndex $ifIdx -DestinationPrefix $c -NextHop "26.26.26.2" -RouteMetric 1 -ErrorAction SilentlyContinue ^| Out-Null
>> "%PS_BOOT%" echo   }
>> "%PS_BOOT%" echo }

echo.
echo === Register Scheduled Task (SYSTEM, on boot) ===
schtasks /Delete /TN "GameProxyAutoStart" /F >nul 2>&1
schtasks /Create /TN "GameProxyAutoStart" /TR "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File \"%PS_BOOT%\"" /SC ONSTART /RU SYSTEM /RL HIGHEST /F
if %errorLevel% NEQ 0 (
    echo [FAIL] schtasks register error
    pause
    exit /b 1
)

echo.
echo === Run task now (no need to wait next boot) ===
taskkill /IM gameproxy.exe /F 2>nul
schtasks /Run /TN "GameProxyAutoStart"
timeout 5 >nul

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
echo  Setup done. gameproxy auto-starts on boot.
echo  Uninstall: schtasks /Delete /TN GameProxyAutoStart /F
echo ============================================
pause
