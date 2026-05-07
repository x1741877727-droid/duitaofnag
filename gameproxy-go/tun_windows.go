//go:build windows

// Windows wintun TUN inbound — Step 2 脱 APK 改造.
//
// 设计 (按 plan apk-jolly-gem.md §2.2):
//   1. wintun.CreateAdapter(cfg.Name) 建虚拟网卡
//   2. 循环读 IP 包
//   3. (Round 3) gVisor netstack 解析 IP/TCP/UDP, 抽出 dst IP/port + payload
//   4. (Round 3) zaixRoute(dstAddr, dstPort) 决策 PROXY / DIRECT / REJECT (复用 routing.go)
//   5. (Round 3) PROXY 命中 → 调 Socks5Server.relay(...) 复用 Rule1/Rule2/acecrypto 改写
//   6. (Round 3) DIRECT → 透传到目标
//
// 当前 round (2/N): lab 模式 — 创建 adapter + 读包 + 打 stats, 不转发.
//   - 路由表不改 → Windows 不会把流量送进 gp-tun
//   - 真实流量仍走 vpn-app → SOCKS5 → 现有逻辑
//   - 即时回滚: kill gameproxy.exe = wintun adapter 自动销毁
//
// 部署依赖: wintun.dll 必须放 binary 同目录 (官方下载 https://www.wintun.net/)
// 路线图:
//   round 3: gVisor netstack + dispatch 调 relay()
//   round 4: 路由白名单文件 + tun_test.go e2e
//   round 5: backend per-instance ACCEL_MODE + 前端 dropdown

package main

import (
	"context"
	"fmt"
	"sync/atomic"
	"time"

	"golang.zx2c4.com/wireguard/tun"
)

type TunConfig struct {
	Name      string
	RouteFile string
}

func startTunInbound(s *Socks5Server, cfg TunConfig) error {
	if cfg.Name == "" {
		cfg.Name = "gp-tun"
	}

	logInfo("tun-mode: 创建 wintun adapter %q (需 wintun.dll 在 binary 同目录) ...", cfg.Name)
	dev, err := tun.CreateTUN(cfg.Name, 1500)
	if err != nil {
		return fmt.Errorf("wintun.CreateTUN(%q): %w (检查 wintun.dll 是否在 binary 旁)", cfg.Name, err)
	}

	actualName, _ := dev.Name()
	mtu, _ := dev.MTU()
	logInfo("tun-mode: adapter ready name=%q mtu=%d (lab 模式 — 不转发流量, 路由表未动)", actualName, mtu)

	// graceful shutdown: 当 ctx 取消 (本 round 暂用 background, round 4 接 main ctx)
	ctx, cancel := context.WithCancel(context.Background())
	_ = cancel // round 4 接 main 的 SIGINT

	defer func() {
		logInfo("tun-mode: 销毁 adapter %q", actualName)
		dev.Close()
	}()

	// 计数器 (atomic 因 stats goroutine 并发读)
	var pktCount uint64
	var byteCount uint64

	// stats goroutine: 每 30s 打一次窗口统计
	go func() {
		ticker := time.NewTicker(30 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				p := atomic.SwapUint64(&pktCount, 0)
				b := atomic.SwapUint64(&byteCount, 0)
				if p > 0 {
					logInfo("tun-mode stats[30s]: %d packets, %d bytes", p, b)
				}
			}
		}
	}()

	// wireguard-go tun 包 batch read API: bufs 是 [][]byte, sizes 拿到每条 packet 实长
	const batchSize = 16
	bufs := make([][]byte, batchSize)
	for i := range bufs {
		bufs[i] = make([]byte, 1600) // mtu + headroom
	}
	sizes := make([]int, batchSize)

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}

		n, err := dev.Read(bufs, sizes, 0)
		if err != nil {
			return fmt.Errorf("tun.Read: %w", err)
		}
		for i := 0; i < n; i++ {
			sz := sizes[i]
			atomic.AddUint64(&pktCount, 1)
			atomic.AddUint64(&byteCount, uint64(sz))
			// Round 3 在这里把 bufs[i][:sz] 喂给 netstack dispatch
		}
	}
}
