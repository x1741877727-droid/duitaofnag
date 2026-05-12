package main

import (
	"sync/atomic"
	"time"
)

// 全局实时计数器 — 给加速器 UI 用 (/api/tun/state).
//
// 设计:
//   - 全 atomic, 无锁开销
//   - 命中点 inc 一次, 永不 reset (重启 service 才归零)
//   - mode = tun / socks5 (由 -tun-mode flag + tunReady 决定)
type tunStats struct {
	TunTCPProxy    atomic.Int64 // [TUN] TCP PROXY (进 wintun, gameproxy 再 dial 真目标)
	TunTCPDirect   atomic.Int64 // [TUN] TCP DIRECT (透传不改写)
	TunTCPReject   atomic.Int64 // [TUN] TCP REJECT (ACE 域名 RST)
	TunTCPFastFail atomic.Int64 // [TUN] TCP FAST-FAIL (IPHealth 标 unhealthy 立即 RST, 不 dial)
	TunUDP         atomic.Int64 // [TUN] UDP datagram pump

	Rule1Hits  atomic.Int64 // [WPE-ADV C→S] 改包次数
	Rule2Hits  atomic.Int64 // [WPE-ADV S→C] 改包次数
	ZaixReject atomic.Int64 // SOCKS5 zaixReject + TUN zaixReject 合计

	TunReady atomic.Bool // wintun adapter 就绪 (startTunDispatch 成功后 = true)
}

var stats tunStats

// GetTunStats 返回当前实时状态 (mode + counters), 给 /api/tun/state 用
func (s *Socks5Server) GetTunStats() map[string]any {
	mode := "socks5"
	if *flagTunMode {
		if stats.TunReady.Load() {
			mode = "tun"
		} else {
			mode = "tun-pending" // -tun-mode=true 但 wintun 还没起
		}
	}
	return map[string]any{
		"ok":             true,
		"mode":           mode,
		"uptime_seconds": int(time.Since(s.startTime).Seconds()),
		"counters": map[string]int64{
			"tun_tcp_proxy":     stats.TunTCPProxy.Load(),
			"tun_tcp_direct":    stats.TunTCPDirect.Load(),
			"tun_tcp_reject":    stats.TunTCPReject.Load(),
			"tun_tcp_fast_fail": stats.TunTCPFastFail.Load(),
			"tun_udp":           stats.TunUDP.Load(),
			"rule1_hits":     stats.Rule1Hits.Load(),
			"rule2_hits":     stats.Rule2Hits.Load(),
			"zaix_reject":    stats.ZaixReject.Load(),
		},
	}
}
