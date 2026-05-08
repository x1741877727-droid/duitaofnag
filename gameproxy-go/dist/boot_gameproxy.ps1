$gp = 'D:\game-automation\duitaofnag\gameproxy-go\dist\gameproxy.exe'
$log = 'D:\game-automation\duitaofnag\gameproxy-go\dist\proxy.log'
Remove-Item -Force $log -ErrorAction SilentlyContinue
$proc = Start-Process -FilePath $gp -ArgumentList '-host','0.0.0.0','-port','9900','-tun-mode','-tun-name','gp-tun','-rule2-max-body','50','-log-file',$log -WindowStyle Hidden -PassThru
$waited = 0
while (-not (Get-NetAdapter -Name 'gp-tun' -ErrorAction SilentlyContinue) -and $waited -lt 30) {
    Start-Sleep -Seconds 1
    $waited++
}
$ifIdx = (Get-NetAdapter -Name 'gp-tun' -ErrorAction SilentlyContinue).InterfaceIndex
if ($ifIdx) {
    New-NetIPAddress -InterfaceIndex $ifIdx -IPAddress 26.26.26.1 -PrefixLength 30 -ErrorAction SilentlyContinue | Out-Null
    $cidrs = @('122.96.96.0/24','36.155.0.0/16','59.83.207.0/24','182.50.10.0/24','180.109.171.0/24','180.102.211.0/24','117.89.177.0/24','222.94.109.0/24','43.135.105.0/24','43.159.233.0/24','129.226.102.0/24','129.226.103.0/24','129.226.107.0/24')
    foreach ($c in $cidrs) {
        New-NetRoute -InterfaceIndex $ifIdx -DestinationPrefix $c -NextHop '26.26.26.2' -RouteMetric 1 -ErrorAction SilentlyContinue | Out-Null
    }
}
$proc.WaitForExit()
