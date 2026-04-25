package main

import (
	"net"
	"strings"
)

// zaix 路由表（源：gitee.com/ez9673/udp/raw/master/zaix，2026-04-20 快照）
// PROXY = 走 proxy 应用 WPE 规则；非 PROXY = FINAL,DIRECT = 在我们这不接
//
// 节省带宽的核心：FightMaster 把所有流量塞给我们，我们只接纳白名单里的，
// 其他走 SOCKS5 reject → FightMaster 会自己处理/fallback（需要 FM 侧配合）

// 域名关键字 → PROXY（走 proxy 应用 rule）
// 空：我们的验证页用 gameproxy-verify 替代 zaix 原版的 baidu
var zaixProxyKeywords = []string{}

// 域名关键字 → REJECT（SOCKS5 立即拒绝，阻断 ACE 上报）
// 这是我们比 zaix 原版更严格的地方：原 zaix 是 anti → PROXY 透传，
// 我们直接拒 = 设备连不上 ACE 检测服务端 = 省带宽 + 等效 ACE-BLOCK
var zaixRejectKeywords = []string{"anti", "crashsight"}

// 端口白名单
var zaixProxyPorts = map[int]bool{
	10012: true,
	17500: true,
}

// IP-CIDR 白名单
var zaixProxyNets []*net.IPNet

// isZaixProxyIP 判断 IP 是否在 HIT IP 白名单里（zaix IP-CIDR 路由命中）
// 用于决定是否在该目标上应用 Rule 1（避免在非 HIT 的 443 登录流量上误伤）
func isZaixProxyIP(host string) bool {
	ip := net.ParseIP(host)
	if ip == nil {
		return false
	}
	for _, n := range zaixProxyNets {
		if n.Contains(ip) {
			return true
		}
	}
	return false
}

func init() {
	cidrs := []string{
		"122.96.96.217/32", "59.83.207.176/32", "122.96.96.179/32",
		"122.96.96.211/32", "122.96.96.251/32", "122.96.96.206/32",
		"182.50.10.74/32", "180.109.171.23/32", "180.102.211.93/32",
		"36.155.251.15/32", "36.155.186.200/32", "180.102.211.42/32",
		"36.155.249.82/32", "117.89.177.167/32", "180.102.211.116/32",
		"36.155.202.119/32", "36.155.202.73/32", "36.155.202.52/32",
		"36.155.228.118/32", "180.102.211.18/32", "222.94.109.22/32",
		"43.159.233.119/32", "129.226.103.18/32", "129.226.103.85/32",
		"43.159.233.192/32", "129.226.102.136/32", "43.135.105.28/32",
		"43.159.233.137/32", "43.159.233.204/32", "43.159.233.114/32",
		"129.226.102.235/32", "192.168.0.120/32",
	}
	for _, s := range cidrs {
		_, n, err := net.ParseCIDR(s)
		if err == nil {
			zaixProxyNets = append(zaixProxyNets, n)
		}
	}
}

// zaixDecision 决策结果
type zaixDecision int

const (
	zaixProxy  zaixDecision = iota // 接手代理 + 应用 WPE rule
	zaixDirect                     // SOCKS5 拒绝（让设备直连/失败）
	zaixReject                     // SOCKS5 拒绝（anti/crashsight，阻断）
)

// zaixRoute 按 zaix 规则（+ 我们的 reject 扩展）决策
// 匹配顺序：REJECT 关键字 → PROXY 端口 → PROXY 关键字 → PROXY IP-CIDR → FINAL,DIRECT
func zaixRoute(host string, port int) zaixDecision {
	lower := strings.ToLower(host)
	// REJECT 关键字（最高优先，防漏）
	for _, kw := range zaixRejectKeywords {
		if strings.Contains(lower, kw) {
			return zaixReject
		}
	}
	// PORT PROXY
	if zaixProxyPorts[port] {
		return zaixProxy
	}
	// DOMAIN-KEYWORD PROXY（当前为空）
	for _, kw := range zaixProxyKeywords {
		if strings.Contains(lower, kw) {
			return zaixProxy
		}
	}
	// IP-CIDR PROXY
	if ip := net.ParseIP(host); ip != nil {
		for _, n := range zaixProxyNets {
			if n.Contains(ip) {
				return zaixProxy
			}
		}
	}
	// FINAL,DIRECT
	return zaixDirect
}
