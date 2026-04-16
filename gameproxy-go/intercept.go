package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"time"
)

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
	return ""
}

// handleIntercept 处理拦截请求
func (s *Socks5Server) handleIntercept(conn net.Conn, addr string, port int, interceptType string) {
	// SOCKS5 成功回复
	socks5Reply(conn, 0x00)

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
