# ============================================================
#  Step 2 one-time install — registers GameProxyAutoStart task,
#  configures wintun + routes, runs once for verification.
#  Called from install_autostart.bat (which self-elevates).
# ============================================================

$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$gpExe = Join-Path $here 'gameproxy.exe'
$gpLog = Join-Path $here 'proxy.log'
$bootPs1 = Join-Path $here 'boot_gameproxy.ps1'

Write-Host '============================================'
Write-Host '  Running as Administrator'
Write-Host '============================================'
Write-Host ''

# === Verify gameproxy.exe + wintun.dll present ===
if (-not (Test-Path $gpExe)) { Write-Host "[FAIL] gameproxy.exe not found at $gpExe"; exit 1 }
if (-not (Test-Path (Join-Path $here 'wintun.dll'))) { Write-Host "[FAIL] wintun.dll not found"; exit 1 }
Write-Host "[OK] gameproxy.exe + wintun.dll present"

# === Stop old gameproxy + remove old task ===
Write-Host ''
Write-Host '=== Stopping old gameproxy + removing old task ==='
Get-Process -Name gameproxy -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'GameProxyAutoStart' -Confirm:$false -ErrorAction SilentlyContinue

# === Write boot PS1 (sync-wait so task stays running) ===
Write-Host ''
Write-Host "=== Writing $bootPs1 ==="
$cidrs = @(
    '122.96.96.0/24','36.155.0.0/16','59.83.207.0/24','182.50.10.0/24',
    '180.109.171.0/24','180.102.211.0/24','117.89.177.0/24','222.94.109.0/24',
    '43.135.105.0/24','43.159.233.0/24','129.226.102.0/24','129.226.103.0/24',
    '129.226.107.0/24'
)
$cidrsJoined = ($cidrs | ForEach-Object { "'$_'" }) -join ','
$bootContent = @"
`$gp = '$gpExe'
`$log = '$gpLog'
Remove-Item -Force `$log -ErrorAction SilentlyContinue
`$proc = Start-Process -FilePath `$gp -ArgumentList '-host','0.0.0.0','-port','9900','-tun-mode','-tun-name','gp-tun','-log-file',`$log -WindowStyle Hidden -PassThru
`$waited = 0
while (-not (Get-NetAdapter -Name 'gp-tun' -ErrorAction SilentlyContinue) -and `$waited -lt 30) {
    Start-Sleep -Seconds 1
    `$waited++
}
`$ifIdx = (Get-NetAdapter -Name 'gp-tun' -ErrorAction SilentlyContinue).InterfaceIndex
if (`$ifIdx) {
    New-NetIPAddress -InterfaceIndex `$ifIdx -IPAddress 26.26.26.1 -PrefixLength 30 -ErrorAction SilentlyContinue | Out-Null
    `$cidrs = @($cidrsJoined)
    foreach (`$c in `$cidrs) {
        New-NetRoute -InterfaceIndex `$ifIdx -DestinationPrefix `$c -NextHop '26.26.26.2' -RouteMetric 1 -ErrorAction SilentlyContinue | Out-Null
    }
}
`$proc.WaitForExit()
"@
$bootContent | Out-File -FilePath $bootPs1 -Encoding UTF8 -Force
Write-Host "[OK] boot script written ($(Get-Item $bootPs1).Length bytes)"

# === Register Scheduled Task ===
Write-Host ''
Write-Host '=== Registering Scheduled Task GameProxyAutoStart ==='
$taskAction = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File `"$bootPs1`""
$taskTrigger = New-ScheduledTaskTrigger -AtStartup
$taskPrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest
$taskSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 9999) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
try {
    Register-ScheduledTask -TaskName 'GameProxyAutoStart' `
        -Action $taskAction -Trigger $taskTrigger `
        -Principal $taskPrincipal -Settings $taskSettings -Force | Out-Null
    Write-Host '[OK] Task registered'
} catch {
    Write-Host "[FAIL] Register-ScheduledTask: $_"
    exit 1
}

# === Run task now ===
Write-Host ''
Write-Host '=== Running task now ==='
Start-ScheduledTask -TaskName 'GameProxyAutoStart'
Write-Host 'Waiting 12s for wintun + routes...'
Start-Sleep -Seconds 12

# === Verify ===
Write-Host ''
Write-Host '=== Verify gameproxy.exe running ==='
$proc = Get-Process -Name gameproxy -ErrorAction SilentlyContinue
if ($proc) {
    Write-Host "[OK] gameproxy.exe running (PID $($proc.Id))"
} else {
    Write-Host '[FAIL] gameproxy.exe NOT running'
}

Write-Host ''
Write-Host '=== Verify gp-tun adapter ==='
$adp = Get-NetAdapter -Name 'gp-tun' -ErrorAction SilentlyContinue
if ($adp) {
    $adp | Format-Table Name,Status,InterfaceIndex,InterfaceDescription -AutoSize | Out-String | Write-Host
} else {
    Write-Host '[FAIL] gp-tun adapter NOT found'
}

Write-Host ''
Write-Host '=== Verify routes pointing to 26.26.26.2 ==='
$routes = Get-NetRoute -ErrorAction SilentlyContinue | Where-Object { $_.NextHop -eq '26.26.26.2' }
if ($routes) {
    $routes | Format-Table DestinationPrefix,NextHop,RouteMetric -AutoSize | Out-String | Write-Host
    Write-Host "[OK] $($routes.Count) routes installed"
} else {
    Write-Host '[FAIL] no routes found for 26.26.26.2'
}

Write-Host ''
Write-Host '=== gameproxy.exe log tail ==='
if (Test-Path $gpLog) {
    Get-Content $gpLog -Tail 12 | Out-String | Write-Host
} else {
    Write-Host '[FAIL] log not found'
}

Write-Host ''
Write-Host '============================================'
Write-Host '  Setup done.'
Write-Host '  - gameproxy auto-starts on boot (SYSTEM)'
Write-Host '  - Routes for 13 game CIDRs go to gp-tun'
Write-Host '  Uninstall: Unregister-ScheduledTask -TaskName GameProxyAutoStart -Confirm:$false'
Write-Host '============================================'
