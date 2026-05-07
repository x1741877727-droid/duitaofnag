//go:build windows

// Windows wintun TUN inbound — Step 2 脱 APK 改造.
//
// 设计 (按 plan apk-jolly-gem.md §2.2):
//   1. wintun.CreateAdapter(cfg.Name) 建虚拟网卡, 配 IP + 路由
//   2. 循环读 IP 包 (TUN 收到的所有 dst-LAN-到-外网流量)
//   3. 用 gVisor netstack 解析 IP/TCP/UDP, 抽出 (srcIP:srcPort, dstIP:dstPort, payload)
//   4. zaixRoute(dstAddr, dstPort) 决策 PROXY / DIRECT / REJECT (复用 routing.go)
//   5. PROXY 命中: 拼一个虚拟 net.Conn 调 Socks5Server.relay(...)
//      → 完全复用现有 Rule1 / Rule2 fingerprint / activity drop / acecrypto 改写, 一行不改
//   6. DIRECT: 透传到真实目标
//
// 当前 round (1/N): 只是 skeleton stub, 真实 wintun 调用待后续 round.
// 计划 round 切分:
//   round 2: 引入 golang.zx2c4.com/wireguard/tun 依赖 + adapter 创建 + 读包 loop
//   round 3: gVisor netstack 集成 + dispatch 调 relay
//   round 4: 路由白名单文件解析 + 完整 e2e 测试
//   round 5: backend ACCEL_MODE 切换 + 灰度部署 (Phase B 启动条件: Step 1 ≥ 24h 稳定)

package main

import (
	"fmt"
)

type TunConfig struct {
	Name      string
	RouteFile string
}

func startTunInbound(s *Socks5Server, cfg TunConfig) error {
	logInfo("tun-mode skeleton: name=%q route_file=%q (真实 wintun 实现待 round 2)", cfg.Name, cfg.RouteFile)
	return fmt.Errorf("TUN skeleton, wintun adapter 实现待补 (round 2)")
}
