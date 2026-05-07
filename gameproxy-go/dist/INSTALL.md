# gameproxy.exe — Windows Service 模式

## 一次性安装（admin UAC 一次）

打开 PowerShell 或 cmd（**右键 → 以管理员身份运行**），cd 到 dist 目录：

```cmd
cd /d D:\game-automation\duitaofnag\gameproxy-go\dist
gameproxy.exe install
```

执行后输出：
```
Service installed.
Service started. Auto-starts on boot. (services.msc to manage)
```

之后**永久无感**：
- 开机自动启服务（SYSTEM 权限，**不弹 UAC**）
- `services.msc` 能看到 `GameProxy AntiBan Network Proxy`，状态 Running
- 服务自己创 wintun 网卡 `gp-tun` + 配 IP `26.26.26.1/30` + 加 13 条游戏服 CIDR 路由
- gameproxy 进程由 SCM 接管（不被 Job Object / session 隔离误杀）

## 子命令

```
gameproxy.exe install      注册 service + 启动 (admin)
gameproxy.exe uninstall    停止 + 卸载 (admin)
gameproxy.exe start        启动 service (admin)
gameproxy.exe stop         停止 service (admin)
gameproxy.exe status       查询状态
```

## 验证

启动后 5-10 秒：

```cmd
gameproxy.exe status
```
应显示 `Service "GameProxy": Running (PID=xxxx)`。

```cmd
ipconfig | findstr gp-tun
```
应看到 `gp-tun` 网卡 + IP `26.26.26.1`。

```cmd
route print 26.26.26.2
```
应看到 13 条 NextHop=26.26.26.2 的路由（122.96.96.0/24 等）。

```cmd
type proxy.log
```
log 含 `[INFO] tun-mode: adapter ready` + `[CONFIG] 配置完成: 13/13 routes -> gp-tun`。

## 卸载（恢复干净状态）

```cmd
gameproxy.exe uninstall
```

之后服务消失、wintun 网卡销毁（process 退出自动）、路由表残留路由 Windows 自动清理失效项。

## 不需要的旧文件

之前实验过的下面这些脚本**已废弃**：
- ~~`install_autostart.bat`~~（Task Scheduler 不稳）
- ~~`install_autostart.ps1`~~
- ~~`start_gameproxy.bat`~~（前台手动跑）
- ~~`boot_gameproxy.ps1`~~（运行时生成）

如果你之前装过 `GameProxyAutoStart` 计划任务，先清掉：
```cmd
schtasks /Delete /TN GameProxyAutoStart /F
```
