@echo off
chcp 65001 >nul
title GameProxy (TUN mode) - keep this window open!

REM ========================================================================
REM  Foreground launcher — admin-elevate then run gameproxy.exe directly
REM  Window MUST stay open (do not close this cmd window)
REM  Reboot? Re-run this bat after reboot.
REM ========================================================================

REM === Self-elevate ===
net session >nul 2>&1
if %errorLevel% NEQ 0 (
    echo Not admin. Re-launching elevated via UAC...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

cd /d %~dp0

REM === Cleanup any old task / process ===
echo === Cleanup ===
schtasks /Delete /TN GameProxyAutoStart /F >nul 2>&1
taskkill /IM gameproxy.exe /F >nul 2>&1
del proxy.log 2>nul

REM === Background-start gameproxy.exe but keep cmd open ===
echo === Starting gameproxy.exe -tun-mode ===
start /B "" "%CD%\gameproxy.exe" -host 0.0.0.0 -port 9900 -tun-mode -tun-name gp-tun -log-file "%CD%\proxy.log"

REM === Wait for wintun adapter ===
echo Waiting up to 30s for gp-tun adapter to register...
powershell -Command "for ($i=0; $i -lt 30; $i++) { if (Get-NetAdapter -Name 'gp-tun' -EA SilentlyContinue) { Write-Host (\"gp-tun ready at +\" + $i + \"s\"); break } ; Start-Sleep 1 }"

REM === Configure IP + routes ===
echo.
echo === Configuring wintun IP + routes ===
powershell -Command ^
  "$ifIdx = (Get-NetAdapter -Name 'gp-tun' -EA SilentlyContinue).InterfaceIndex;" ^
  "if ($ifIdx) {" ^
  "  New-NetIPAddress -InterfaceIndex $ifIdx -IPAddress 26.26.26.1 -PrefixLength 30 -EA SilentlyContinue | Out-Null;" ^
  "  $cidrs = @('122.96.96.0/24','36.155.0.0/16','59.83.207.0/24','182.50.10.0/24','180.109.171.0/24','180.102.211.0/24','117.89.177.0/24','222.94.109.0/24','43.135.105.0/24','43.159.233.0/24','129.226.102.0/24','129.226.103.0/24','129.226.107.0/24');" ^
  "  $count = 0;" ^
  "  foreach ($c in $cidrs) { New-NetRoute -InterfaceIndex $ifIdx -DestinationPrefix $c -NextHop '26.26.26.2' -RouteMetric 1 -EA SilentlyContinue | Out-Null; $count++ };" ^
  "  Write-Host ('Configured ' + $count + ' routes')" ^
  "} else { Write-Host 'gp-tun adapter NOT found - gameproxy may have crashed' }"

echo.
echo === Verify ===
tasklist | findstr gameproxy
powershell -Command "Get-NetAdapter -Name 'gp-tun' -EA SilentlyContinue | Format-Table Name,Status,InterfaceIndex -AutoSize"
powershell -Command "Get-NetRoute -EA SilentlyContinue | Where-Object { $_.NextHop -eq '26.26.26.2' } | Measure-Object | Select-Object @{n='RouteCount';e={$_.Count}} | Format-Table -AutoSize"

echo.
echo ============================================
echo  gameproxy + wintun + routes are LIVE.
echo
echo  KEEP THIS WINDOW OPEN. Closing it kills gameproxy.
echo  After reboot: double-click this bat again.
echo
echo  Tail log in another window:
echo    powershell Get-Content "%CD%\proxy.log" -Tail 20 -Wait
echo ============================================
echo.
echo Press Ctrl+C or close window to stop gameproxy.
pause >nul
echo Stopping gameproxy...
taskkill /IM gameproxy.exe /F
