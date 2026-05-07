//go:build windows

// Windows Service support — gameproxy.exe 注册成 Windows Service.
//
// Subcommands (admin required):
//   gameproxy.exe install     — 注册自身为系统服务, 开机自启 (LocalSystem)
//   gameproxy.exe uninstall   — 卸载服务
//   gameproxy.exe start       — 启动服务
//   gameproxy.exe stop        — 停止服务
//   gameproxy.exe status      — 查询服务状态
//
// Service mode (SCM 自动调):
//   - svc.Run() 注册 SCM 回调
//   - gpService.Execute() 跑 runProxyMain + 等 SCM Stop
//   - 进程 lifecycle 由 SCM 接管, 不被 Job Object / session 隔离误杀
//
// 一次 install 后:
//   - 开机自启 (start type Automatic), 不弹 UAC
//   - services.msc 可见
//   - 服务自己配 wintun IP + 路由表 (在 startTunInbound 内调用)

package main

import (
	"context"
	"flag"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/eventlog"
	"golang.org/x/sys/windows/svc/mgr"
)

const (
	svcName        = "GameProxy"
	svcDisplay     = "GameProxy AntiBan Network Proxy"
	svcDescription = "和平精英对掏房 防封代理 (SOCKS5 + Wintun TUN)"
)

// gameServerCIDRs — 跟 routing.go zaixProxyNets 同步, 给 wintun 路由表用
var gameServerCIDRs = []string{
	"122.96.96.0/24", "36.155.0.0/16", "59.83.207.0/24", "182.50.10.0/24",
	"180.109.171.0/24", "180.102.211.0/24", "117.89.177.0/24", "222.94.109.0/24",
	"43.135.105.0/24", "43.159.233.0/24", "129.226.102.0/24", "129.226.103.0/24",
	"129.226.107.0/24",
}

// ─────────── service runtime ───────────

func runAsServiceIfNeeded() bool {
	isService, err := svc.IsWindowsService()
	if err != nil || !isService {
		return false
	}
	if err := svc.Run(svcName, &gpService{}); err != nil {
		if elog, e := eventlog.Open(svcName); e == nil {
			elog.Error(1, fmt.Sprintf("svc.Run failed: %v", err))
			elog.Close()
		}
	}
	return true
}

type gpService struct{}

func (s *gpService) Execute(args []string, r <-chan svc.ChangeRequest, status chan<- svc.Status) (svcSpecificEC bool, exitCode uint32) {
	const cmdsAccepted = svc.AcceptStop | svc.AcceptShutdown
	status <- svc.Status{State: svc.StartPending}

	// flag.Parse() — service mode 下 os.Args 含 SCM 启动时 ImagePath 的参数
	if !flag.Parsed() {
		flag.Parse()
	}
	setupLogging()
	logInfo("=== Service start (SCM) ===")

	ctx, cancel := context.WithCancel(context.Background())
	errCh := make(chan error, 1)
	go func() {
		errCh <- runProxyMain(ctx)
	}()

	status <- svc.Status{State: svc.Running, Accepts: cmdsAccepted}

loop:
	for {
		select {
		case c := <-r:
			switch c.Cmd {
			case svc.Interrogate:
				status <- c.CurrentStatus
			case svc.Stop, svc.Shutdown:
				logInfo("=== Service stopping (SCM) ===")
				cancel()
				break loop
			}
		case err := <-errCh:
			if err != nil {
				logWarn("runProxyMain exited: %v", err)
			}
			cancel()
			break loop
		}
	}

	// 给 runProxyMain 时间退出
	select {
	case <-errCh:
	case <-time.After(5 * time.Second):
	}

	status <- svc.Status{State: svc.StopPending}
	return false, 0
}

// ─────────── service control (CLI subcommands) ───────────

func handleServiceCmd(cmd string) {
	switch cmd {
	case "install":
		if err := installService(); err != nil {
			fmt.Fprintf(os.Stderr, "install failed: %v\n", err)
			os.Exit(1)
		}
		fmt.Println("Service installed.")
		if err := startServiceCtrl(); err != nil {
			fmt.Fprintf(os.Stderr, "start failed: %v\n", err)
			os.Exit(1)
		}
		fmt.Println("Service started. Auto-starts on boot. (services.msc to manage)")
	case "uninstall":
		if err := uninstallService(); err != nil {
			fmt.Fprintf(os.Stderr, "uninstall failed: %v\n", err)
			os.Exit(1)
		}
		fmt.Println("Service uninstalled.")
	case "start":
		if err := startServiceCtrl(); err != nil {
			fmt.Fprintf(os.Stderr, "start failed: %v\n", err)
			os.Exit(1)
		}
		fmt.Println("Service started.")
	case "stop":
		if err := stopServiceCtrl(); err != nil {
			fmt.Fprintf(os.Stderr, "stop failed: %v\n", err)
			os.Exit(1)
		}
		fmt.Println("Service stopped.")
	case "status":
		st, err := queryServiceCtrl()
		if err != nil {
			fmt.Fprintf(os.Stderr, "status failed: %v\n", err)
			os.Exit(1)
		}
		fmt.Println(st)
	default:
		fmt.Fprintf(os.Stderr, "unknown subcommand: %s\n", cmd)
		os.Exit(1)
	}
}

func installService() error {
	exepath, err := os.Executable()
	if err != nil {
		return err
	}
	exepath, _ = filepath.Abs(exepath)
	logfile := filepath.Join(filepath.Dir(exepath), "proxy.log")

	m, err := mgr.Connect()
	if err != nil {
		return fmt.Errorf("mgr.Connect (need admin): %w", err)
	}
	defer m.Disconnect()

	if s, err := m.OpenService(svcName); err == nil {
		s.Close()
		return fmt.Errorf("service %q already installed; uninstall first", svcName)
	}

	cfg := mgr.Config{
		ServiceType:    0x10, // SERVICE_WIN32_OWN_PROCESS
		StartType:      mgr.StartAutomatic,
		ErrorControl:   mgr.ErrorNormal,
		DisplayName:    svcDisplay,
		Description:    svcDescription,
		ServiceStartName: "", // empty = LocalSystem
	}

	args := []string{
		"-host", "0.0.0.0",
		"-port", "9900",
		"-tun-mode",
		"-tun-name", "gp-tun",
		"-log-file", logfile,
	}

	s, err := m.CreateService(svcName, exepath, cfg, args...)
	if err != nil {
		return fmt.Errorf("CreateService: %w", err)
	}
	defer s.Close()

	if err := eventlog.InstallAsEventCreate(svcName, eventlog.Error|eventlog.Warning|eventlog.Info); err != nil {
		// 不致命
		fmt.Fprintf(os.Stderr, "(warn) eventlog install: %v\n", err)
	}
	return nil
}

func uninstallService() error {
	m, err := mgr.Connect()
	if err != nil {
		return fmt.Errorf("mgr.Connect (need admin): %w", err)
	}
	defer m.Disconnect()

	s, err := m.OpenService(svcName)
	if err != nil {
		return fmt.Errorf("service %q not installed", svcName)
	}
	defer s.Close()

	// 先 stop
	if st, _ := s.Query(); st.State != svc.Stopped {
		s.Control(svc.Stop)
		for i := 0; i < 30; i++ {
			st, _ = s.Query()
			if st.State == svc.Stopped {
				break
			}
			time.Sleep(time.Second)
		}
	}

	if err := s.Delete(); err != nil {
		return fmt.Errorf("Delete: %w", err)
	}
	eventlog.Remove(svcName)
	return nil
}

func startServiceCtrl() error {
	m, err := mgr.Connect()
	if err != nil {
		return fmt.Errorf("mgr.Connect (need admin): %w", err)
	}
	defer m.Disconnect()
	s, err := m.OpenService(svcName)
	if err != nil {
		return fmt.Errorf("service %q not installed", svcName)
	}
	defer s.Close()
	return s.Start()
}

func stopServiceCtrl() error {
	m, err := mgr.Connect()
	if err != nil {
		return fmt.Errorf("mgr.Connect (need admin): %w", err)
	}
	defer m.Disconnect()
	s, err := m.OpenService(svcName)
	if err != nil {
		return fmt.Errorf("service %q not installed", svcName)
	}
	defer s.Close()
	_, err = s.Control(svc.Stop)
	return err
}

func queryServiceCtrl() (string, error) {
	m, err := mgr.Connect()
	if err != nil {
		return "", err
	}
	defer m.Disconnect()
	s, err := m.OpenService(svcName)
	if err != nil {
		return fmt.Sprintf("service %q not installed", svcName), nil
	}
	defer s.Close()
	st, err := s.Query()
	if err != nil {
		return "", err
	}
	stateStr := map[svc.State]string{
		svc.Stopped:         "Stopped",
		svc.StartPending:    "StartPending",
		svc.StopPending:     "StopPending",
		svc.Running:         "Running",
		svc.ContinuePending: "ContinuePending",
		svc.PausePending:    "PausePending",
		svc.Paused:          "Paused",
	}[st.State]
	return fmt.Sprintf("Service %q: %s (PID=%d)", svcName, stateStr, st.ProcessId), nil
}

// ─────────── wintun route configuration (内嵌, 不再依赖外部 PS 脚本) ───────────

// configureWintunRoutes — startTunInbound 在 wintun adapter 创建后调.
// 等 Windows network stack 注册 adapter, 然后 netsh 配 IP + route add 加路由.
// 失败不致命 — log warn 后继续 (TUN inbound 可降级到只读包模式).
func configureWintunRoutes(adapterName string) {
	var ifIdx int
	for i := 0; i < 30; i++ {
		if iface, err := net.InterfaceByName(adapterName); err == nil && iface.Index > 0 {
			ifIdx = iface.Index
			break
		}
		time.Sleep(time.Second)
	}
	if ifIdx == 0 {
		logWarn("[CONFIG] adapter %q 30s 内未注册到 Windows network stack — 跳过路由配置", adapterName)
		return
	}
	logInfo("[CONFIG] adapter %q ifIdx=%d, 开始配 IP + 路由", adapterName, ifIdx)

	// 配 IP 26.26.26.1/30
	if err := runCmdQuiet("netsh", "interface", "ipv4", "set", "address",
		fmt.Sprintf("name=%d", ifIdx), "static", "26.26.26.1", "255.255.255.252"); err != nil {
		logWarn("[CONFIG] netsh set address: %v", err)
	}

	// 加路由 (route add <dest> mask <mask> 26.26.26.2 metric 1 if <ifIdx>)
	added := 0
	for _, cidr := range gameServerCIDRs {
		dest, mask, err := cidrToDestMask(cidr)
		if err != nil {
			logWarn("[CONFIG] bad cidr %s: %v", cidr, err)
			continue
		}
		if err := runCmdQuiet("route", "add", dest, "mask", mask, "26.26.26.2",
			"metric", "1", "if", strconv.Itoa(ifIdx)); err != nil {
			// 路由可能已存在 — 不致命
			continue
		}
		added++
	}
	logInfo("[CONFIG] 配置完成: %d/%d routes -> gp-tun (ifIdx=%d)", added, len(gameServerCIDRs), ifIdx)
}

func runCmdQuiet(name string, args ...string) error {
	cmd := exec.Command(name, args...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s %s: %w (output: %s)", name, strings.Join(args, " "), err,
			strings.TrimSpace(string(out)))
	}
	return nil
}

func cidrToDestMask(cidr string) (dest, mask string, err error) {
	_, ipnet, e := net.ParseCIDR(cidr)
	if e != nil {
		return "", "", e
	}
	dest = ipnet.IP.String()
	mask = net.IP(ipnet.Mask).String()
	return
}
