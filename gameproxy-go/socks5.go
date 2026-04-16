package main

import (
	"context"
	"encoding/binary"
	"fmt"
	"io"
	"net"
	"strings"
	"sync/atomic"
	"time"
)

const (
	socks5Ver         = 0x05
	socks5AuthNone    = 0x00
	socks5AuthUserPass = 0x02
	socks5AuthReject  = 0xFF
	socks5CmdConnect  = 0x01
	socks5CmdUDPAssoc = 0x03
	socks5AtypIPv4    = 0x01
	socks5AtypDomain  = 0x03
	socks5AtypIPv6    = 0x04
)

// MITM 绕过域名列表
var bypassSuffixes = []string{
	".qq.com",
	".tencent.com",
	".wechat.com",
	".weixin.qq.com",
	".gtimg.cn",
	".myqcloud.com",
	".qpic.cn",
	".idqqimg.com",
	".googleapis.com",
	".gstatic.com",
	".google.com",
	".cdn-go.cn",
	".gcloudsdk.com",
	".anticheatexpert.com",
	".crashsight.qq.com",
}

// ServerConfig 服务器配置
type ServerConfig struct {
	Host       string
	Port       int
	Tokens     map[string]bool
	RuleEngine *RuleEngine
	Capture    *PacketCapture
}

// Socks5Server SOCKS5 代理服务器
type Socks5Server struct {
	host       string
	port       int
	tokens     map[string]bool
	ruleEngine *RuleEngine
	capture    *PacketCapture
	failCache  *FailCache

	activeConns   int64
	totalConns    int64
	maxConns      int64
	startTime     time.Time
	clientTracker *ClientTracker

	listener net.Listener
}

// NewSocks5Server 创建服务器
func NewSocks5Server(cfg ServerConfig) *Socks5Server {
	return &Socks5Server{
		host:          cfg.Host,
		port:          cfg.Port,
		tokens:        cfg.Tokens,
		ruleEngine:    cfg.RuleEngine,
		capture:       cfg.Capture,
		failCache:     NewFailCache(),
		clientTracker: NewClientTracker(),
		maxConns:      500,
		startTime:     time.Now(),
	}
}

// ListenAndServe 监听并接受连接
func (s *Socks5Server) ListenAndServe(ctx context.Context) error {
	addr := fmt.Sprintf("%s:%d", s.host, s.port)
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return err
	}
	s.listener = ln

	logInfo("SOCKS5 代理启动: %s", addr)
	if s.tokens != nil {
		logInfo("鉴权: 已配置 %d 个 Token", len(s.tokens))
	} else {
		logInfo("鉴权: 无（开放访问）")
	}
	logInfo("规则: %d 条", len(s.ruleEngine.rules))

	go func() {
		<-ctx.Done()
		ln.Close()
	}()

	for {
		conn, err := ln.Accept()
		if err != nil {
			if ctx.Err() != nil {
				return nil // 正常关闭
			}
			continue
		}
		go s.handleClient(ctx, conn)
	}
}

// Shutdown 关闭服务器
func (s *Socks5Server) Shutdown() {
	if s.listener != nil {
		s.listener.Close()
	}
}

// handleClient 处理一个 SOCKS5 客户端连接
func (s *Socks5Server) handleClient(ctx context.Context, conn net.Conn) {
	atomic.AddInt64(&s.activeConns, 1)
	atomic.AddInt64(&s.totalConns, 1)
	clientIP := extractClientIP(conn)
	s.clientTracker.Connect(clientIP)
	defer func() {
		atomic.AddInt64(&s.activeConns, -1)
		s.clientTracker.Disconnect(clientIP)
		conn.Close()
	}()

	// 并发上限
	if atomic.LoadInt64(&s.activeConns) > s.maxConns {
		logWarn("连接数超限 (%d), 拒绝", atomic.LoadInt64(&s.activeConns))
		return
	}

	// ── 1. 握手：协商认证方式 ──
	conn.SetDeadline(time.Now().Add(10 * time.Second))

	header := make([]byte, 2)
	if _, err := io.ReadFull(conn, header); err != nil {
		return
	}
	if header[0] != socks5Ver {
		return
	}

	methods := make([]byte, header[1])
	if _, err := io.ReadFull(conn, methods); err != nil {
		return
	}

	if s.tokens != nil {
		// 要求用户名/密码认证
		conn.Write([]byte{socks5Ver, socks5AuthUserPass})

		// ── 2. 用户名/密码认证 ──
		authVer := make([]byte, 1)
		if _, err := io.ReadFull(conn, authVer); err != nil || authVer[0] != 0x01 {
			return
		}

		ulenBuf := make([]byte, 1)
		io.ReadFull(conn, ulenBuf)
		username := make([]byte, ulenBuf[0])
		io.ReadFull(conn, username)

		plenBuf := make([]byte, 1)
		io.ReadFull(conn, plenBuf)
		password := make([]byte, plenBuf[0])
		io.ReadFull(conn, password)

		token := string(password)
		if token == "" {
			token = string(username)
		}
		if !s.tokens[token] {
			logWarn("认证失败: token=%s...", token[:min(8, len(token))])
			conn.Write([]byte{0x01, 0x01}) // 失败
			return
		}
		conn.Write([]byte{0x01, 0x00}) // 成功
	} else {
		conn.Write([]byte{socks5Ver, socks5AuthNone})
	}

	// ── 3. 连接请求 ──
	req := make([]byte, 4)
	if _, err := io.ReadFull(conn, req); err != nil {
		return
	}

	cmd := req[1]
	atyp := req[3]
	var dstAddr string
	var dstPort int

	switch atyp {
	case socks5AtypIPv4:
		addrBuf := make([]byte, 4)
		io.ReadFull(conn, addrBuf)
		dstAddr = net.IP(addrBuf).String()
	case socks5AtypDomain:
		lenBuf := make([]byte, 1)
		io.ReadFull(conn, lenBuf)
		domainBuf := make([]byte, lenBuf[0])
		io.ReadFull(conn, domainBuf)
		dstAddr = string(domainBuf)
	case socks5AtypIPv6:
		addrBuf := make([]byte, 16)
		io.ReadFull(conn, addrBuf)
		dstAddr = net.IP(addrBuf).String()
	default:
		return
	}

	portBuf := make([]byte, 2)
	io.ReadFull(conn, portBuf)
	dstPort = int(binary.BigEndian.Uint16(portBuf))

	// ── 4. UDP ASSOCIATE ──
	if cmd == socks5CmdUDPAssoc {
		s.handleUDPAssociate(ctx, conn, dstAddr, dstPort)
		return
	}

	if cmd != socks5CmdConnect {
		socks5Reply(conn, 0x07) // 不支持的命令
		return
	}

	// ── 5. 拦截检查 ──
	interceptType := shouldIntercept(dstAddr, dstPort)
	if interceptType != "" {
		s.handleIntercept(conn, dstAddr, dstPort, interceptType)
		return
	}

	// ── 5.5. 灯塔上报黑洞（8081 端口）──
	// 拦截腾讯 Beacon SDK 上报（TYPE_COMPRESS/bea_key）
	// 假装连接成功，但吞掉所有数据，不转发给腾讯服务器
	if dstPort == 8081 {
		logInfo("[BLACKHOLE] 灯塔上报拦截: %s:%d", dstAddr, dstPort)
		socks5Reply(conn, 0x00)
		conn.SetDeadline(time.Time{})
		s.relayBlackhole(conn, dstAddr, dstPort)
		return
	}

	// ── 6. 连接目标服务器 ──
	failKey := fmt.Sprintf("%s:%d", dstAddr, dstPort)
	dialer := net.Dialer{
		Timeout:   5 * time.Second,
		KeepAlive: 30 * time.Second, // TCP keepalive 每 30 秒探测
	}
	remote, err := dialer.Dial("tcp", fmt.Sprintf("%s:%d", dstAddr, dstPort))
	if err != nil {
		fc := s.failCache.RecordFailure(failKey)
		if fc <= 3 {
			logInfo("连接失败: %s:%d - %v", dstAddr, dstPort, err)
		} else if fc == 4 {
			logWarn("连接失败: %s:%d 频繁失败，后续不再逐条打印", dstAddr, dstPort)
		}
		socks5Reply(conn, 0x05)
		return
	}
	s.failCache.Clear(failKey)

	// 设置 TCP 缓冲区（减少小包延迟）
	if tc, ok := remote.(*net.TCPConn); ok {
		tc.SetNoDelay(true)       // 禁用 Nagle，游戏包需要低延迟
		tc.SetReadBuffer(65536)   // 64KB 读缓冲
		tc.SetWriteBuffer(65536)  // 64KB 写缓冲
	}
	if tc, ok := conn.(*net.TCPConn); ok {
		tc.SetNoDelay(true)
		tc.SetKeepAlive(true)
		tc.SetKeepAlivePeriod(30 * time.Second)
	}

	// 返回连接成功
	socks5Reply(conn, 0x00)

	// 清除 deadline，进入转发模式
	conn.SetDeadline(time.Time{})

	// ── 7. 路由决策 ──
	isIP := net.ParseIP(dstAddr) != nil
	bypassDomain := !isIP && isBypassDomain(dstAddr)

	// 设置抓包
	var connID string
	if s.capture != nil && s.capture.ShouldCapture(dstPort) {
		connID, _ = s.capture.NewConnection(dstAddr, dstPort)
		logInfo("[CAPTURE] %s: %s:%d", connID, dstAddr, dstPort)
	}

	if isIP {
		logInfo("直连+规则: %s:%d (IP, 私有协议)", dstAddr, dstPort)
		s.relay(conn, remote, dstAddr, dstPort, connID)
	} else if bypassDomain {
		logInfo("直连透传: %s:%d (bypass domain)", dstAddr, dstPort)
		s.relayPassthrough(conn, remote)
	} else {
		s.relay(conn, remote, dstAddr, dstPort, connID)
	}
}

// socks5Reply 发送 SOCKS5 回复
func socks5Reply(conn net.Conn, status byte) error {
	_, err := conn.Write([]byte{socks5Ver, status, 0x00, socks5AtypIPv4,
		0, 0, 0, 0, 0, 0})
	return err
}

// isBypassDomain 检查是否为绕过域名
func isBypassDomain(addr string) bool {
	lower := strings.ToLower(addr)
	for _, suffix := range bypassSuffixes {
		if strings.HasSuffix(lower, suffix) {
			return true
		}
	}
	return false
}

// GetVerifyJSON 返回代理内部状态
func (s *Socks5Server) GetVerifyJSON() map[string]interface{} {
	uptime := int(time.Since(s.startTime).Seconds())
	stats := s.ruleEngine.Stats()
	return map[string]interface{}{
		"ok":                 true,
		"server":             "GameProxy-Go",
		"uptime_seconds":     uptime,
		"mitm_enabled":       false,
		"rules_loaded":       len(s.ruleEngine.rules),
		"rules_enabled":      countEnabled(s.ruleEngine.rules),
		"packets_total":      stats.TotalPackets,
		"packets_modified":   stats.ModifiedPackets,
		"rules_matched":      stats.MatchedRules,
		"active_connections": atomic.LoadInt64(&s.activeConns),
		"total_connections":  atomic.LoadInt64(&s.totalConns),
	}
}

func countEnabled(rules []PacketRule) int {
	n := 0
	for _, r := range rules {
		if r.Enabled {
			n++
		}
	}
	return n
}
