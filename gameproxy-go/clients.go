package main

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"sync"
	"time"
)

// ClientInfo 单个客户端的连接状态
type ClientInfo struct {
	IP          string `json:"ip"`
	Connections int    `json:"connections"`
	LastActive  int64  `json:"last_active"`  // unix timestamp
	RxBytes     int64  `json:"rx_bytes"`
	TxBytes     int64  `json:"tx_bytes"`
}

// ClientTracker 跟踪所有已连接的客户端
type ClientTracker struct {
	mu      sync.Mutex
	clients map[string]*clientState // key = client IP
}

type clientState struct {
	connections int
	lastActive  time.Time
	rxBytes     int64
	txBytes     int64
}

// NewClientTracker 创建客户端跟踪器
func NewClientTracker() *ClientTracker {
	return &ClientTracker{clients: make(map[string]*clientState)}
}

// Connect 客户端建立新连接
func (ct *ClientTracker) Connect(clientIP string) {
	ct.mu.Lock()
	defer ct.mu.Unlock()
	s, ok := ct.clients[clientIP]
	if !ok {
		s = &clientState{}
		ct.clients[clientIP] = s
	}
	s.connections++
	s.lastActive = time.Now()
}

// Disconnect 客户端断开一个连接
func (ct *ClientTracker) Disconnect(clientIP string) {
	ct.mu.Lock()
	defer ct.mu.Unlock()
	s, ok := ct.clients[clientIP]
	if !ok {
		return
	}
	s.connections--
	if s.connections <= 0 {
		// 保留记录 30 秒，之后清理
		s.connections = 0
	}
}

// AddBytes 记录流量
func (ct *ClientTracker) AddBytes(clientIP string, rx, tx int64) {
	ct.mu.Lock()
	defer ct.mu.Unlock()
	s, ok := ct.clients[clientIP]
	if !ok {
		return
	}
	s.rxBytes += rx
	s.txBytes += tx
	s.lastActive = time.Now()
}

// GetAll 获取所有客户端状态
func (ct *ClientTracker) GetAll() []ClientInfo {
	ct.mu.Lock()
	defer ct.mu.Unlock()

	now := time.Now()
	var result []ClientInfo
	var toDelete []string

	for ip, s := range ct.clients {
		// 清理 60 秒无连接且无活动的客户端
		if s.connections <= 0 && now.Sub(s.lastActive) > 60*time.Second {
			toDelete = append(toDelete, ip)
			continue
		}
		result = append(result, ClientInfo{
			IP:          ip,
			Connections: s.connections,
			LastActive:  s.lastActive.Unix(),
			RxBytes:     s.rxBytes,
			TxBytes:     s.txBytes,
		})
	}

	for _, ip := range toDelete {
		delete(ct.clients, ip)
	}

	return result
}

// IsConnected 检查指定 IP 是否有活跃连接
func (ct *ClientTracker) IsConnected(clientIP string) bool {
	ct.mu.Lock()
	defer ct.mu.Unlock()
	s, ok := ct.clients[clientIP]
	if !ok {
		return false
	}
	return s.connections > 0
}

// beaconStore 存储远程 agent 上报的 URL（内存）
var beaconMu sync.Mutex
var beaconData = map[string]interface{}{}

// StartAPIServer 启动 HTTP API 服务
func StartAPIServer(addr string, server *Socks5Server) {
	mux := http.NewServeMux()

	// GET /api/clients — 所有已连接客户端
	mux.HandleFunc("/api/clients", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Access-Control-Allow-Origin", "*")

		clients := server.clientTracker.GetAll()
		resp := map[string]interface{}{
			"ok":      true,
			"clients": clients,
			"server":  "GameProxy-Go",
			"uptime":  int(time.Since(server.startTime).Seconds()),
		}
		json.NewEncoder(w).Encode(resp)
	})

	// GET /api/client?ip=x.x.x.x — 查询单个客户端
	mux.HandleFunc("/api/client", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Access-Control-Allow-Origin", "*")

		ip := r.URL.Query().Get("ip")
		if ip == "" {
			json.NewEncoder(w).Encode(map[string]interface{}{"ok": false, "error": "missing ip param"})
			return
		}

		connected := server.clientTracker.IsConnected(ip)
		resp := map[string]interface{}{
			"ok":        true,
			"ip":        ip,
			"connected": connected,
		}
		json.NewEncoder(w).Encode(resp)
	})

	// GET /api/status — 代理总状态
	mux.HandleFunc("/api/status", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Access-Control-Allow-Origin", "*")
		json.NewEncoder(w).Encode(server.GetVerifyJSON())
	})

	// POST /api/beacon — remote_agent 上报自身 URL
	// GET  /api/beacon — Claude 查询当前 remote_agent 地址
	mux.HandleFunc("/api/beacon", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Access-Control-Allow-Origin", "*")
		if r.Method == http.MethodPost {
			var body map[string]interface{}
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				w.WriteHeader(400)
				json.NewEncoder(w).Encode(map[string]interface{}{"ok": false, "error": "invalid json"})
				return
			}
			beaconMu.Lock()
			for k, v := range body {
				beaconData[k] = v
			}
			beaconData["updated_at"] = time.Now().Unix()
			beaconMu.Unlock()
			json.NewEncoder(w).Encode(map[string]interface{}{"ok": true})
		} else {
			beaconMu.Lock()
			snap := make(map[string]interface{}, len(beaconData))
			for k, v := range beaconData {
				snap[k] = v
			}
			beaconMu.Unlock()
			snap["ok"] = true
			json.NewEncoder(w).Encode(snap)
		}
	})

	// Packet viewer API (Phase A)
	getCaptureDir := func() string {
		if server.capture == nil {
			return ""
		}
		return server.capture.CaptureDir()
	}
	mux.HandleFunc("/api/conns", handleConns(getCaptureDir))
	mux.HandleFunc("/api/timeline", handleTimeline(getCaptureDir))
	mux.HandleFunc("/api/frame", handleFrame(getCaptureDir))
	mux.HandleFunc("/api/label", handleLabelPost(getCaptureDir))
	mux.HandleFunc("/api/labels", handleLabelsGet(getCaptureDir))

	// verify 页 — 复用 SOCKS5 isVerifyHost 的 HTML, 通过 HTTP API 也能 access
	// (TUN 模式没 vpn-app 拦虚拟域名了, 用这个 endpoint 替代 emulator 验证)
	mux.HandleFunc("/verify", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Header().Set("Cache-Control", "no-cache, no-store, must-revalidate")
		w.Header().Set("Access-Control-Allow-Origin", "*")
		fmt.Fprint(w, server.verifyHTML())
	})
	mux.HandleFunc("/api/verify", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.Header().Set("Access-Control-Allow-Origin", "*")
		_ = json.NewEncoder(w).Encode(server.GetVerifyJSON())
	})

	// GET /api/tun/ip_health — 当前所有跟踪 IP 的健康状态 (含 fast-fail 不通的)
	// 前端 / 用户能看到"哪些 PUBG IP 当前打不通", 知道是网络问题不是脚本问题.
	mux.HandleFunc("/api/tun/ip_health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Access-Control-Allow-Origin", "*")
		ips := []map[string]any{}
		if server.ipHealth != nil {
			ips = server.ipHealth.Snapshot()
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"ok":  true,
			"ips": ips,
		})
	})

	logInfo("HTTP API 启动: %s", addr)
	if err := http.ListenAndServe(addr, mux); err != nil {
		logWarn("HTTP API 启动失败: %v", err)
	}
}

// extractClientIP 从 net.Conn 中提取客户端 IP（不含端口）
func extractClientIP(conn net.Conn) string {
	addr := conn.RemoteAddr().String()
	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		return addr
	}
	return host
}

// formatAPIAddr 根据 proxy port 生成 API 端口（proxy port + 1）
func formatAPIAddr(host string, proxyPort int) string {
	return fmt.Sprintf("%s:%d", host, proxyPort+1)
}
