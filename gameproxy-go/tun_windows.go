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

	// 探测物理网卡 idx 给 dialerForRelay 用 (绑物理网卡避免 wintun routing loop)
	if idx := detectPhysicalIfIdx(); idx > 0 {
		SetPhysicalIfIdx(idx)
	} else {
		logWarn("tun-mode: 探测物理网卡 idx 失败, outbound 可能 routing loop")
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

	// 起 gVisor netstack + dispatch
	stk, err := startTunDispatch(srv, dev, mtu)
	if err != nil {
		dev.Close()
		return fmt.Errorf("startTunDispatch: %w", err)
	}
	_ = stk

	// 自己配 wintun IP + 游戏服路由表 (内嵌, 不依赖外部 PS 脚本)
	go configureWintunRoutes(actualName)

	logInfo("tun-mode: gVisor stack ready, 后台等 wintun 注册到 network stack 后配路由")

	// block forever (service mode 由 SCM 触发 ctx cancel; CLI 模式由 SIGINT)
	select {}
}
