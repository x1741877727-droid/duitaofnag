package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"strings"
	"time"
)

// HIT 上报 IP 段 — 六花抓包对比发现这些 IP 只出现在"我们模式"不出现在"六花模式"
// 特征：随机高位端口（41762/51762/63861）+ 小流量单向上报 = 典型 Tencent HIT/Beacon 上报
var hitBlockRanges []*net.IPNet

func init() {
	cidrs := []string{
		"222.189.172.0/24", // 江苏电信 — 2026-04-18 pcap 确认主要 HIT 上报目的地
		"27.155.112.0/24",  // 福建电信 — 同批次上报
	}
	for _, s := range cidrs {
		_, n, err := net.ParseCIDR(s)
		if err == nil {
			hitBlockRanges = append(hitBlockRanges, n)
		}
	}
}

func isHITBlockIP(addr string) bool {
	ip := net.ParseIP(addr)
	if ip == nil {
		return false
	}
	for _, r := range hitBlockRanges {
		if r.Contains(ip) {
			return true
		}
	}
	return false
}

// shouldIntercept 检查是否需要拦截
func shouldIntercept(addr string, port int) string {
	if addr == "gameproxy-verify" {
		return "verify"
	}
	if addr == "gameproxy-verify-json" {
		return "verify_json"
	}
	if addr == "m.baidu.com" && (port == 80 || port == 443) {
		return "brand_page"
	}
	// ── HIT 安全系统上报拦截：假 accept + blackhole（不能 REJECT 否则禁网）──
	if isHITBlockIP(addr) {
		return "hit_block"
	}
	// ── 六花式防封：灯塔 8081 返回假 HTTP 200 ──
	if port == 8081 {
		return "fake_beacon"
	}
	// ── 六花式防封：ACE 反作弊域名直接拒绝 ──
	lower := strings.ToLower(addr)
	if strings.Contains(lower, "anticheatexpert") || strings.Contains(lower, "crashsight") {
		return "ace_block"
	}
	return ""
}

// handleIntercept 处理拦截请求
func (s *Socks5Server) handleIntercept(conn net.Conn, addr string, port int, interceptType string) {
	// ACE 域名：SOCKS5 直接拒绝 (host unreachable)
	if interceptType == "ace_block" {
		socks5Reply(conn, 0x04) // host unreachable
		logInfo("[ACE-BLOCK] 拒绝 ACE 域名连接: %s:%d", addr, port)
		return
	}

	// SOCKS5 成功回复
	socks5Reply(conn, 0x00)

	// HIT 上报 IP：假 accept + 吞包（不能 REJECT/RST，否则游戏判定网络异常会禁网）
	if interceptType == "hit_block" {
		conn.SetReadDeadline(time.Now().Add(3 * time.Second))
		buf := make([]byte, 4096)
		total := 0
		for {
			n, err := conn.Read(buf)
			if n > 0 {
				total += n
			}
			if err != nil {
				break
			}
		}
		conn.SetReadDeadline(time.Time{})
		logInfo("[HIT-BLOCK] 吞包: %s:%d recv=%dB", addr, port, total)
		return
	}

	// 灯塔 8081：模仿六花，返回假 HTTP 200
	if interceptType == "fake_beacon" {
		conn.SetReadDeadline(time.Now().Add(3 * time.Second))
		discard := make([]byte, 4096)
		n, _ := io.ReadAtLeast(conn, discard, 1)
		conn.SetReadDeadline(time.Time{})
		fakeResp := "HTTP/1.1 200 OK\r\n" +
			"Content-Type: text/plain\r\n" +
			"Content-Length: 2\r\n" +
			"Connection: close\r\n" +
			"\r\n" +
			"OK"
		conn.Write([]byte(fakeResp))
		logInfo("[FAKE-BEACON] 假响应 8081 上报: %s:%d recv=%dB", addr, port, n)
		return
	}

	// 读取 HTTP 请求（丢弃）
	conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	discard := make([]byte, 4096)
	io.ReadAtLeast(conn, discard, 1)
	conn.SetReadDeadline(time.Time{})

	var body []byte
	var ct string

	if interceptType == "verify_json" {
		data, _ := json.Marshal(s.GetVerifyJSON())
		body = data
		ct = "application/json; charset=utf-8"
	} else {
		body = []byte(s.brandPageHTML())
		ct = "text/html; charset=utf-8"
	}

	response := fmt.Sprintf(
		"HTTP/1.1 200 OK\r\n"+
			"Content-Type: %s\r\n"+
			"Content-Length: %d\r\n"+
			"Access-Control-Allow-Origin: *\r\n"+
			"Connection: close\r\n"+
			"\r\n", ct, len(body))

	conn.Write([]byte(response))
	conn.Write(body)

	logInfo("拦截页面返回: %s → %s:%d", interceptType, addr, port)
}

// brandPageHTML 品牌验证页面 HTML
func (s *Socks5Server) brandPageHTML() string {
	uptime := int(time.Since(s.startTime).Seconds())
	h := uptime / 3600
	m := (uptime % 3600) / 60
	sec := uptime % 60

	stats := s.ruleEngine.Stats()
	rulesN := len(s.ruleEngine.rules)
	modifiedN := stats.ModifiedPackets

	matched := stats.MatchedRules
	matchedStr := "—"
	if len(matched) > 0 {
		parts := make([]string, 0, len(matched))
		for k, v := range matched {
			parts = append(parts, fmt.Sprintf("%s×%d", k, v))
		}
		matchedStr = ""
		for i, p := range parts {
			if i > 0 {
				matchedStr += ", "
			}
			matchedStr += p
		}
	}

	modClass := "warn"
	if modifiedN > 0 {
		modClass = "green"
	}

	return fmt.Sprintf(`<!DOCTYPE html><html lang="zh-CN"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FightMaster</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:-apple-system,'Helvetica Neue',sans-serif}
body{background:#0A0E1A;color:#e0e0e0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}
.c{max-width:380px;width:100%%}
.hd{text-align:center;margin-bottom:32px}
.hd .icon{font-size:56px;margin-bottom:8px}
.hd h1{font-size:26px;color:#00D4FF;letter-spacing:1px;font-weight:700}
.hd p{color:#888;font-size:13px;margin-top:4px}
.badge{display:inline-flex;align-items:center;gap:6px;background:#00FF8822;color:#00FF88;font-size:13px;font-weight:600;padding:6px 16px;border-radius:20px;margin-top:16px}
.badge .dot{width:8px;height:8px;background:#00FF88;border-radius:50%%;animation:pulse 2s infinite}
@keyframes pulse{0%%,100%%{opacity:1}50%%{opacity:.3}}
.card{background:#141929;border:1px solid #1F2640;border-radius:16px;padding:20px;margin-bottom:16px}
.row{display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid #1F2640;font-size:13px}
.row:last-child{border:none}
.label{color:#888}.val{color:#00D4FF;font-weight:500}
.val.green{color:#00FF88}.val.warn{color:#FFAA00}
.ft{text-align:center;color:#333;font-size:11px;margin-top:24px}
</style></head><body>
<div class="c">
  <div class="hd">
    <div class="icon">⚡</div>
    <h1>FightMaster</h1>
    <p>游戏加速引擎</p>
    <div class="badge"><span class="dot"></span>代理服务运行中</div>
  </div>
  <div class="card">
    <div class="row"><span class="label">运行时长</span><span class="val">%d时%d分%d秒</span></div>
    <div class="row"><span class="label">引擎</span><span class="val green">Go</span></div>
    <div class="row"><span class="label">封包规则</span><span class="val">%d 条</span></div>
    <div class="row"><span class="label">总封包</span><span class="val">%d</span></div>
    <div class="row"><span class="label">已改写</span><span class="val %s">%d</span></div>
    <div class="row"><span class="label">规则命中</span><span class="val">%s</span></div>
  </div>
  <div class="ft">Powered by FightMaster Engine v2.0 (Go)</div>
</div></body></html>`,
		h, m, sec,
		rulesN,
		stats.TotalPackets,
		modClass, modifiedN,
		matchedStr)
}
