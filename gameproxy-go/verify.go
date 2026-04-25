package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"sync/atomic"
	"time"
)

// isVerifyHost 检测是否是代理自检用的虚拟域名
// 用户访问 http://gameproxy-verify/ 或 /verify-json/ 验证 VPN→proxy 链路通
// 端口接受 80/443/8080/8081/8085 等常见浏览器默认端口
func isVerifyHost(addr string, port int) string {
	// 虚拟域名检测与端口无关，只要是 verify 域名就走 verify 分支
	switch addr {
	case "gameproxy-verify":
		return "html"
	case "gameproxy-verify-json":
		return "json"
	}
	return ""
}

// handleVerifyPage 代理内回应自检页（不连上游）
func (s *Socks5Server) handleVerifyPage(conn net.Conn, kind string) {
	socks5Reply(conn, 0x00)
	// 读掉 HTTP 请求（随便丢）
	conn.SetReadDeadline(time.Now().Add(3 * time.Second))
	discard := make([]byte, 4096)
	io.ReadAtLeast(conn, discard, 1)
	conn.SetReadDeadline(time.Time{})

	var body []byte
	var ct string
	if kind == "json" {
		data, _ := json.Marshal(s.GetVerifyJSON())
		body = data
		ct = "application/json; charset=utf-8"
	} else {
		body = []byte(s.verifyHTML())
		ct = "text/html; charset=utf-8"
	}
	resp := fmt.Sprintf(
		"HTTP/1.1 200 OK\r\n"+
			"Content-Type: %s\r\n"+
			"Content-Length: %d\r\n"+
			"Access-Control-Allow-Origin: *\r\n"+
			"Connection: close\r\n"+
			"\r\n", ct, len(body))
	conn.Write([]byte(resp))
	conn.Write(body)
}

// verifyHTML 最小验证页
func (s *Socks5Server) verifyHTML() string {
	uptime := int(time.Since(s.startTime).Seconds())
	h := uptime / 3600
	m := (uptime % 3600) / 60
	sec := uptime % 60
	active := atomic.LoadInt64(&s.activeConns)
	total := atomic.LoadInt64(&s.totalConns)
	return fmt.Sprintf(`<!DOCTYPE html><html lang="zh-CN"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GameProxy OK</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:-apple-system,'Helvetica Neue',sans-serif}
body{background:#0A0E1A;color:#e0e0e0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}
.c{max-width:380px;width:100%%}
.hd{text-align:center;margin-bottom:28px}
.hd h1{font-size:26px;color:#00D4FF;letter-spacing:1px;font-weight:700}
.badge{display:inline-flex;align-items:center;gap:6px;background:#00FF8822;color:#00FF88;font-size:13px;font-weight:600;padding:6px 16px;border-radius:20px;margin-top:12px}
.badge .dot{width:8px;height:8px;background:#00FF88;border-radius:50%%;animation:pulse 2s infinite}
@keyframes pulse{0%%,100%%{opacity:1}50%%{opacity:.3}}
.card{background:#141929;border:1px solid #1F2640;border-radius:16px;padding:20px}
.row{display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid #1F2640;font-size:13px}
.row:last-child{border:none}
.label{color:#888}.val{color:#00D4FF;font-weight:500}
</style></head><body>
<div class="c">
  <div class="hd">
    <h1>GameProxy</h1>
    <div class="badge"><span class="dot"></span>代理链路正常</div>
  </div>
  <div class="card">
    <div class="row"><span class="label">运行时长</span><span class="val">%d时%d分%d秒</span></div>
    <div class="row"><span class="label">活跃连接</span><span class="val">%d</span></div>
    <div class="row"><span class="label">总连接数</span><span class="val">%d</span></div>
  </div>
</div></body></html>`, h, m, sec, active, total)
}
