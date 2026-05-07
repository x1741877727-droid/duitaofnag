@echo off
REM ========================================================================
REM  Step 2 一次性配置脚本 — 双击即可
REM  做完后开机自动 admin 启 gameproxy + 创 wintun + 配路由表, 永远不用再管
REM ========================================================================

REM === Self-elevate 检测 ===
net session >nul 2>&1
if %errorLevel% NEQ 0 (
    echo 需要管理员权限, 自动重启提权...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo ============================================
echo  以管理员身份运行中
echo ============================================

REM === 路径常量 ===
set "GP_EXE=D:\game-automation\duitaofnag\gameproxy-go\dist\gameproxy.exe"
set "GP_LOG=D:\game-automation\duitaofnag\gameproxy-go\dist\proxy.log"
set "PS_BOOT=D:\game-automation\duitaofnag\gameproxy-go\dist\boot_gameproxy.ps1"

REM === 写一个开机启动用的 PS1 ===
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
echo === 注册开机自启 Scheduled Task (SYSTEM) ===
schtasks /Delete /TN "GameProxyAutoStart" /F >nul 2>&1
schtasks /Create /TN "GameProxyAutoStart" /TR "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File \"%PS_BOOT%\"" /SC ONSTART /RU SYSTEM /RL HIGHEST /F
if %errorLevel% NEQ 0 (
    echo [失败] 注册任务出错
    pause
    exit /b 1
)

echo.
echo === 立即跑一次 (不用等下次开机) ===
taskkill /IM gameproxy.exe /F 2>nul
schtasks /Run /TN "GameProxyAutoStart"
timeout 5 >nul

echo.
echo === 验证 wintun 创建 ===
powershell -Command "Get-NetAdapter | Where-Object { $_.Name -eq 'gp-tun' } | Format-Table Name,Status,InterfaceIndex"

echo.
echo === gameproxy 日志 tail ===
powershell -Command "Get-Content '%GP_LOG%' -Tail 10"

echo.
echo ============================================
echo  完成! 以后开机自动 admin 启 gameproxy
echo  每次重启电脑后无需任何操作
echo  要卸载: schtasks /Delete /TN GameProxyAutoStart /F
echo ============================================
pause
