package main

import (
	"fmt"
	"net"
	"sync"
	"time"
)

// IPHealth 跟踪每个目标 IP 的 TCP dial 健康度.
//
// 问题背景:
//   当 PUBG 服务器 IP (43.135.105.51:17500 等) 因网络 / DC 切换 / ISP 故障
//   暂时不通时, gameproxy TUN dispatcher 每次 dial 都等满 5s timeout 才 RST,
//   模拟器里游戏 socket 卡 5s 才报错 → 业务体验差.
//
// 修法:
//   1. 每次 dial 结果记到 IPHealth (成功清失败计数, 失败累加)
//   2. 下次 dial 前 IsHealthy() 检查, 该 IP 30s 内失败 >=3 次 → 直接 RST 不 dial
//   3. 后台 prober 定期 TCP probe unhealthy IP, 通了立即 RecordSuccess 解除标记
//
// 体验: "卡 5s × 反复重试" → "卡 < 100ms 一次 + 自动恢复".
type IPHealth struct {
	mu      sync.RWMutex
	entries map[string]*ipHealthEntry
}

type ipHealthEntry struct {
	failCount    int       // 当前窗口内累计失败 (跨窗口 reset)
	lastFail     time.Time // 最近一次失败时间
	lastSuccess  time.Time // 最近一次成功
	totalFail    int64     // 永不 reset, 给统计
	totalSuccess int64
	lastPort     int // 最近 dial 用过的端口 (prober 探活复用)
}

const (
	// 30s 窗口内 >= 3 次失败 → 标 unhealthy
	unhealthyThreshold = 3
	unhealthyWindow    = 30 * time.Second
	// prober: 每 10s 对 unhealthy IP 探一次, 2s timeout
	proberInterval = 10 * time.Second
	proberTimeout  = 2 * time.Second
)

// NewIPHealth 构造
func NewIPHealth() *IPHealth {
	return &IPHealth{entries: make(map[string]*ipHealthEntry)}
}

// IsHealthy 返回 IP 是否可 dial. 无记录默认健康.
func (h *IPHealth) IsHealthy(ip string) bool {
	h.mu.RLock()
	defer h.mu.RUnlock()
	e, ok := h.entries[ip]
	if !ok {
		return true
	}
	if e.failCount >= unhealthyThreshold && time.Since(e.lastFail) < unhealthyWindow {
		return false
	}
	return true
}

// RecordFail 记一次 dial 失败. port 用于 prober 之后复用同端口探活.
func (h *IPHealth) RecordFail(ip string, port int) {
	h.mu.Lock()
	defer h.mu.Unlock()
	e := h.entries[ip]
	if e == nil {
		e = &ipHealthEntry{}
		h.entries[ip] = e
	}
	if time.Since(e.lastFail) >= unhealthyWindow {
		e.failCount = 0
	}
	e.failCount++
	e.lastFail = time.Now()
	e.totalFail++
	if port > 0 {
		e.lastPort = port
	}
}

// RecordSuccess dial 成功 → 立即清失败窗口
func (h *IPHealth) RecordSuccess(ip string, port int) {
	h.mu.Lock()
	defer h.mu.Unlock()
	e := h.entries[ip]
	if e == nil {
		e = &ipHealthEntry{}
		h.entries[ip] = e
	}
	e.failCount = 0
	e.lastSuccess = time.Now()
	e.totalSuccess++
	if port > 0 {
		e.lastPort = port
	}
}

// unhealthyIPs 返回当前 unhealthy 的 (ip, port) 给 prober 探活
func (h *IPHealth) unhealthyIPs() []ipProbeTarget {
	h.mu.RLock()
	defer h.mu.RUnlock()
	now := time.Now()
	out := []ipProbeTarget{}
	for ip, e := range h.entries {
		if e.failCount >= unhealthyThreshold && now.Sub(e.lastFail) < unhealthyWindow {
			p := e.lastPort
			if p == 0 {
				p = 17500 // PUBG 主端口兜底
			}
			out = append(out, ipProbeTarget{IP: ip, Port: p})
		}
	}
	return out
}

type ipProbeTarget struct {
	IP   string
	Port int
}

// Snapshot 给 /api/tun/ip_health endpoint
func (h *IPHealth) Snapshot() []map[string]any {
	h.mu.RLock()
	defer h.mu.RUnlock()
	now := time.Now()
	out := make([]map[string]any, 0, len(h.entries))
	for ip, e := range h.entries {
		healthy := true
		if e.failCount >= unhealthyThreshold && now.Sub(e.lastFail) < unhealthyWindow {
			healthy = false
		}
		var lastFailAgo, lastOkAgo int64 = -1, -1
		if !e.lastFail.IsZero() {
			lastFailAgo = int64(now.Sub(e.lastFail).Seconds())
		}
		if !e.lastSuccess.IsZero() {
			lastOkAgo = int64(now.Sub(e.lastSuccess).Seconds())
		}
		out = append(out, map[string]any{
			"ip":            ip,
			"healthy":       healthy,
			"recent_fails":  e.failCount,
			"total_fails":   e.totalFail,
			"total_success": e.totalSuccess,
			"last_fail_ago": lastFailAgo,
			"last_ok_ago":   lastOkAgo,
			"last_port":     e.lastPort,
		})
	}
	return out
}

// StartProber 启动后台探活 goroutine. 每 10s 对 unhealthy IP TCP probe (2s timeout).
// 探通 → RecordSuccess 解除 unhealthy, 业务 dial 立即恢复.
func (h *IPHealth) StartProber() {
	go func() {
		ticker := time.NewTicker(proberInterval)
		defer ticker.Stop()
		for range ticker.C {
			targets := h.unhealthyIPs()
			for _, t := range targets {
				addr := net.JoinHostPort(t.IP, fmt.Sprintf("%d", t.Port))
				d := net.Dialer{Timeout: proberTimeout}
				conn, err := d.Dial("tcp", addr)
				if err != nil {
					continue
				}
				conn.Close()
				h.RecordSuccess(t.IP, t.Port)
				logInfo("[ip-health] %s 恢复健康 (prober probe %d 通)", t.IP, t.Port)
			}
		}
	}()
}
