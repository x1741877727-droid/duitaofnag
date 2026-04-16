package main

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// PacketCapture 完整封包抓取
type PacketCapture struct {
	captureDir   string
	capturePorts map[int]bool // nil = 抓所有端口

	mu          sync.Mutex
	connCounter int
	pktCounter  int
}

// NewPacketCapture 创建抓包实例并建目录
func NewPacketCapture(dir string, ports map[int]bool) (*PacketCapture, error) {
	if err := os.MkdirAll(dir, 0755); err != nil {
		return nil, err
	}
	var p map[int]bool
	if len(ports) > 0 {
		p = ports
	}
	logInfo("[CAPTURE] 启用 → %s, 端口=%v", dir, ports)
	return &PacketCapture{captureDir: dir, capturePorts: p}, nil
}

// ShouldCapture 检查端口是否需要抓包
func (c *PacketCapture) ShouldCapture(port int) bool {
	if c.capturePorts == nil {
		return true
	}
	return c.capturePorts[port]
}

// NewConnection 注册新连接，创建目录，写 meta.json
func (c *PacketCapture) NewConnection(dstAddr string, dstPort int) (string, error) {
	c.mu.Lock()
	c.connCounter++
	connID := fmt.Sprintf("conn_%06d", c.connCounter)
	c.mu.Unlock()

	connDir := filepath.Join(c.captureDir, connID)
	if err := os.MkdirAll(connDir, 0755); err != nil {
		return "", err
	}

	now := time.Now()
	meta := map[string]interface{}{
		"conn_id":   connID,
		"dst_addr":  dstAddr,
		"dst_port":  dstPort,
		"start_time": float64(now.UnixNano()) / 1e9,
		"start_iso":  now.Format("2006-01-02T15:04:05"),
	}
	data, _ := json.MarshalIndent(meta, "", "  ")
	os.WriteFile(filepath.Join(connDir, "meta.json"), data, 0644)

	return connID, nil
}

// SavePacket 保存一个封包为 .bin + .json
func (c *PacketCapture) SavePacket(connID string, data []byte, direction string, seq int) error {
	c.mu.Lock()
	c.pktCounter++
	pktNum := c.pktCounter
	c.mu.Unlock()

	prefix := "c2s"
	switch direction {
	case "recv":
		prefix = "s2c"
	case "send_mod":
		prefix = "c2s_mod"
	}

	base := fmt.Sprintf("%s_%06d", prefix, seq)
	connDir := filepath.Join(c.captureDir, connID)

	// 二进制数据
	if err := os.WriteFile(filepath.Join(connDir, base+".bin"), data, 0644); err != nil {
		return err
	}

	// 元数据
	headLen := 64
	if len(data) < headLen {
		headLen = len(data)
	}
	pktMeta := map[string]interface{}{
		"pkt_num":     pktNum,
		"direction":   direction,
		"sequence":    seq,
		"length":      len(data),
		"timestamp":   float64(time.Now().UnixNano()) / 1e9,
		"hex_head_64": hex.EncodeToString(data[:headLen]),
	}
	metaData, _ := json.MarshalIndent(pktMeta, "", "  ")
	return os.WriteFile(filepath.Join(connDir, base+".json"), metaData, 0644)
}

// ConnCount 连接计数
func (c *PacketCapture) ConnCount() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.connCounter
}

// PktCount 封包计数
func (c *PacketCapture) PktCount() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.pktCounter
}
