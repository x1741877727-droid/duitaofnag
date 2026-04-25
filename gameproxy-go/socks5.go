package main

import (
	"context"
	"encoding/binary"
	"fmt"
	"io"
	"net"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

const (
	socks5Ver          = 0x05
	socks5AuthNone     = 0x00
	socks5AuthUserPass = 0x02
	socks5AuthReject   = 0xFF
	socks5CmdConnect   = 0x01
	socks5CmdUDPAssoc  = 0x03
	socks5AtypIPv4     = 0x01
	socks5AtypDomain   = 0x03
	socks5AtypIPv6     = 0x04
)

// bypass 域名白名单 — 纯资源/登录类
var bypassSuffixes = []string{
	".gtimg.cn", ".gtimg.com",
	".qpic.cn", ".idqqimg.com", ".qlogo.cn",
	".myqcloud.com", ".qcloudimg.com",
	".cdn-go.cn", ".gcloudsdk.com",
	"down.qq.com",
	".googleapis.com", ".gstatic.com", ".google.com",
	".wx.qq.com",
	"wtlogin.qq.com", "ptlogin2.qq.com",
	"ssl.ptlogin2.qq.com", "connect.qq.com",
	"captcha.qq.com",
}

// ServerConfig 服务器配置
type ServerConfig struct {
	Host          string
	Port          int
	Tokens        map[string]bool
	Capture       *PacketCapture
	ZaixStrict    bool
	BlockActivity bool
	Rule2Disabled            bool
	Rule1Disabled            bool
	Rule2MaxBody             int  // 0=全量；>0=只改 17500 帧 body_size < N 的帧
	Rule2Fingerprint         bool // true=443 S→C 用 8B fingerprint 精确匹配（推荐），false=旧版 patchWPEAdvanced 全量
	BlockActivityFingerprint bool // true=破坏 activity s2c 包（opcode 04/05 → 00），让 client 静默丢弃
	DropActivityFrames       bool // true=17500 S→C frame-level DROP activity channel (00 10 51/65 00)

	// R2 实验（2026-04-25）：活动包 = 17500 S→C 非 G6 大包；conn 存活 > N 秒后出现的多为活动
	// DropNonG617500AfterSec：conn age 阈值（秒），0=关闭此拦截
	// DropNonG617500Enforce： false(默认) = log-only 不真 drop；true = 实际 drop
	DropNonG617500AfterSec int
	DropNonG617500Enforce  bool
}

// Socks5Server SOCKS5 代理服务器
type Socks5Server struct {
	host          string
	port          int
	tokens        map[string]bool
	capture       *PacketCapture
	failCache     *FailCache
	zaixStrict    bool
	blockActivity bool
	rule2Disabled            bool
	rule1Disabled            bool
	rule2MaxBody             int
	rule2Fingerprint         bool
	blockActivityFingerprint bool
	dropActivityFrames       bool

	dropNonG617500AfterSec int
	dropNonG617500Enforce  bool

	activeConns   int64
	totalConns    int64
	maxConns      int64
	startTime     time.Time
	clientTracker *ClientTracker

	// UUID 全局映射：443 连接提取到的设备 UUID 按 clientIP 共享给 17500 连接
	uuidMu       sync.Mutex
	uuidByClient map[string]string

	listener net.Listener
}

func NewSocks5Server(cfg ServerConfig) *Socks5Server {
	return &Socks5Server{
		host:          cfg.Host,
		port:          cfg.Port,
		tokens:        cfg.Tokens,
		capture:       cfg.Capture,
		zaixStrict:    cfg.ZaixStrict,
		blockActivity: cfg.BlockActivity,
		rule2Disabled:            cfg.Rule2Disabled,
		rule1Disabled:            cfg.Rule1Disabled,
		rule2MaxBody:             cfg.Rule2MaxBody,
		rule2Fingerprint:         cfg.Rule2Fingerprint,
		blockActivityFingerprint: cfg.BlockActivityFingerprint,
		dropActivityFrames:       cfg.DropActivityFrames,
		dropNonG617500AfterSec:   cfg.DropNonG617500AfterSec,
		dropNonG617500Enforce:    cfg.DropNonG617500Enforce,
		uuidByClient:  make(map[string]string),
		failCache:     NewFailCache(),
		clientTracker: NewClientTracker(),
		maxConns:      500,
		startTime:     time.Now(),
	}
}

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

	go func() {
		<-ctx.Done()
		ln.Close()
	}()

	for {
		conn, err := ln.Accept()
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			continue
		}
		go s.handleClient(ctx, conn)
	}
}

func (s *Socks5Server) Shutdown() {
	if s.listener != nil {
		s.listener.Close()
	}
}

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

	if atomic.LoadInt64(&s.activeConns) > s.maxConns {
		logWarn("连接数超限 (%d), 拒绝", atomic.LoadInt64(&s.activeConns))
		return
	}

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
		conn.Write([]byte{socks5Ver, socks5AuthUserPass})
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
			conn.Write([]byte{0x01, 0x01})
			return
		}
		conn.Write([]byte{0x01, 0x00})
	} else {
		conn.Write([]byte{socks5Ver, socks5AuthNone})
	}

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

	if cmd == socks5CmdUDPAssoc {
		s.handleUDPAssociate(ctx, conn, dstAddr, dstPort)
		return
	}

	if cmd != socks5CmdConnect {
		socks5Reply(conn, 0x07)
		return
	}

	// 代理自检虚拟域名（不连上游，直接回 HTML/JSON）
	if kind := isVerifyHost(dstAddr, dstPort); kind != "" {
		s.handleVerifyPage(conn, kind)
		return
	}

	// zaix 路由：决策 PROXY / DIRECT / REJECT
	switch zaixRoute(dstAddr, dstPort) {
	case zaixReject:
		// anti/crashsight 等 ACE 检测域名：不论是否 strict 都拒
		logInfo("[ZAIX-REJECT] 阻断 ACE 域名: %s:%d", dstAddr, dstPort)
		socks5Reply(conn, 0x02)
		return
	case zaixDirect:
		if s.zaixStrict {
			logInfo("[ZAIX-DIRECT] 拒: %s:%d (不在白名单)", dstAddr, dstPort)
			socks5Reply(conn, 0x02)
			return
		}
		// 宽松模式：仍然接 connection, 走透传/relay
	}

	// 连接目标服务器
	failKey := fmt.Sprintf("%s:%d", dstAddr, dstPort)
	dialer := net.Dialer{
		Timeout:   5 * time.Second,
		KeepAlive: 30 * time.Second,
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

	if tc, ok := remote.(*net.TCPConn); ok {
		tc.SetNoDelay(true)
		tc.SetReadBuffer(65536)
		tc.SetWriteBuffer(65536)
	}
	if tc, ok := conn.(*net.TCPConn); ok {
		tc.SetNoDelay(true)
		tc.SetKeepAlive(true)
		tc.SetKeepAlivePeriod(30 * time.Second)
	}

	socks5Reply(conn, 0x00)
	conn.SetDeadline(time.Time{})

	isIP := net.ParseIP(dstAddr) != nil
	bypassDomain := !isIP && isBypassDomain(dstAddr)

	var connID string
	if s.capture != nil && s.capture.ShouldCapture(dstPort) {
		connID, _ = s.capture.NewConnection(dstAddr, dstPort, clientIP)
		logInfo("[CAPTURE] %s: %s:%d (client=%s)", connID, dstAddr, dstPort, clientIP)
	}

	if bypassDomain {
		logInfo("直连透传: %s:%d (bypass domain)", dstAddr, dstPort)
		s.relayPassthrough(conn, remote)
	} else {
		s.relay(conn, remote, dstAddr, dstPort, connID)
	}
}

func socks5Reply(conn net.Conn, status byte) error {
	_, err := conn.Write([]byte{socks5Ver, status, 0x00, socks5AtypIPv4,
		0, 0, 0, 0, 0, 0})
	return err
}

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
	return map[string]interface{}{
		"ok":                 true,
		"server":             "GameProxy-Go",
		"uptime_seconds":     uptime,
		"active_connections": atomic.LoadInt64(&s.activeConns),
		"total_connections":  atomic.LoadInt64(&s.totalConns),
	}
}
