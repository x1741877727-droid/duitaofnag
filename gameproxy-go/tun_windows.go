//go:build windows

// Windows wintun TUN inbound — Step 2 脱 APK 改造.
//
// 设计 (按 plan apk-jolly-gem.md §2.2):
//   1. wintun.CreateAdapter(cfg.Name) 建虚拟网卡
//   2. (round 2) 循环读 IP 包
//   3. (round 3) gVisor netstack 解析 IP/TCP/UDP, 抽出 dst IP/port
//   4. (round 3) zaixRoute(dstAddr, dstPort) 决策 PROXY / DIRECT / REJECT (复用 routing.go)
//   5. (round 4) PROXY 命中 → 调 Socks5Server.relay(...) 复用 Rule1/Rule2/acecrypto 改写
//   6. (round 4) DIRECT → 透传到目标
//
// 当前 round (3/N): lab 模式 — adapter + stack + dispatch 全栈接通,
// 但 forwarder 全部 RST, 不接 socks5.relay (round 4 才接); 路由表不动 → 真实流量不会进来.
//
// 即时回滚: kill gameproxy.exe = wintun adapter 自动销毁; 不带 -tun-mode 启动 = SOCKS5-only.
// 部署依赖: wintun.dll 必须放 binary 同目录 (https://www.wintun.net/, ~256KB)

package main

import (
	"fmt"

	"golang.zx2c4.com/wireguard/tun"
)

type TunConfig struct {
	Name      string
	RouteFile string
}

func startTunInbound(srv *Socks5Server, cfg TunConfig) error {
	if cfg.Name == "" {
		cfg.Name = "gp-tun"
	}

	logInfo("tun-mode: 创建 wintun adapter %q (需 wintun.dll 在 binary 同目录) ...", cfg.Name)
	dev, err := tun.CreateTUN(cfg.Name, 1500)
	if err != nil {
		return fmt.Errorf("wintun.CreateTUN(%q): %w (检查 wintun.dll 是否在 binary 旁)", cfg.Name, err)
	}

	actualName, _ := dev.Name()
	mtuInt, _ := dev.MTU()
	mtu := uint32(mtuInt)
	logInfo("tun-mode: adapter ready name=%q mtu=%d", actualName, mtu)

	// round 3: 起 gVisor netstack + dispatch (lab 模式, forwarder 全部 RST)
	stk, err := startTunDispatch(srv, dev, mtu)
	if err != nil {
		dev.Close()
		return fmt.Errorf("startTunDispatch: %w", err)
	}
	_ = stk
	logInfo("tun-mode: gVisor stack ready (lab — 路由表未动, 真实流量不会进来)")

	// graceful shutdown 占位 (round 4 接 main ctx)
	select {} // block forever — round 4 接 main 的 SIGINT
}
