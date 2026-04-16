package main

import (
	"bytes"
	"encoding/hex"
	"io"
	"net"
	"sync"
)

// banPktSignature 17500 主控长连接里的封号通知包特征
// 33 66 00 0a 00 0a 50 02 = 明文系统消息（实测：唯一会出现的非 4013 加密类型）
var banPktSignature = []byte{0x33, 0x66, 0x00, 0x0a, 0x00, 0x0a, 0x50, 0x02}

// isBanPacket 检查 s2c 数据是否为封号通知包
func isBanPacket(data []byte) bool {
	return len(data) >= 8 && bytes.Equal(data[:8], banPktSignature)
}

// relay 双向转发 + 规则引擎 + 可选抓包 + 流量统计
func (s *Socks5Server) relay(client, remote net.Conn,
	dstAddr string, dstPort int, connID string) {

	clientIP := extractClientIP(client)

	var wg sync.WaitGroup
	wg.Add(2)

	// 任一方向结束时关闭两端，让另一个 goroutine 的 Read 立刻返回 error
	closeOnce := sync.Once{}
	closeBoth := func() {
		closeOnce.Do(func() {
			client.Close()
			remote.Close()
		})
	}

	// 客户端 → 远程服务器（应用改写规则）
	go func() {
		defer wg.Done()
		defer closeBoth()

		buf := make([]byte, 65536)
		firstPacket := true
		applyRules := true
		seq := 0

		for {
			n, err := client.Read(buf)
			if n > 0 {
				data := buf[:n]

				// 第一个包检测 TLS ClientHello (0x16 0x03)
				if firstPacket {
					firstPacket = false
					if len(data) >= 3 && data[0] == 0x16 && data[1] == 0x03 {
						applyRules = false
						logInfo("TLS 检测: %s:%d 是 TLS 连接，跳过规则", dstAddr, dstPort)
					}
				}

				// 抓包：保存原始封包
				if s.capture != nil && connID != "" {
					s.capture.SavePacket(connID, copyBytes(data), "send", seq)
				}

				if applyRules {
					modified := s.ruleEngine.Process(data, "send")
					if s.capture != nil && connID != "" && !bytesEqual(data, modified) {
						s.capture.SavePacket(connID, copyBytes(modified), "send_mod", seq)
					}
					if nw, werr := remote.Write(modified); werr != nil {
						break
					} else {
						s.clientTracker.AddBytes(clientIP, 0, int64(nw))
					}
				} else {
					if nw, werr := remote.Write(data); werr != nil {
						break
					} else {
						s.clientTracker.AddBytes(clientIP, 0, int64(nw))
					}
				}
				seq++
			}
			if err != nil {
				break
			}
		}
	}()

	// 远程服务器 → 客户端（抓包 + 规则观察）
	go func() {
		defer wg.Done()
		defer closeBoth()

		buf := make([]byte, 65536)
		seq := 0

		for {
			n, err := remote.Read(buf)
			if n > 0 {
				data := buf[:n]

				if s.capture != nil && connID != "" {
					s.capture.SavePacket(connID, copyBytes(data), "recv", seq)
				}

				if s.ruleEngine.dryRun || s.ruleEngine.ruleDebug {
					s.ruleEngine.Process(data, "recv")
				}

				// 17500 主控长连接：丢弃 50 02 封号通知包，连接保持
				if dstPort == 17500 && isBanPacket(data) {
					logInfo("[BANPKT-DROP] 拦截封号通知 17500 s2c %d 字节: %s",
						len(data), hex.EncodeToString(data[:min(32, len(data))]))
					seq++
					continue
				}

				if nw, werr := client.Write(data); werr != nil {
					break
				} else {
					s.clientTracker.AddBytes(clientIP, int64(nw), 0)
				}
				seq++
			}
			if err != nil {
				break
			}
		}
	}()

	wg.Wait()
}

// relayPassthrough 纯透传，不过规则引擎
func (s *Socks5Server) relayPassthrough(client, remote net.Conn) {
	var wg sync.WaitGroup
	wg.Add(2)

	closeOnce := sync.Once{}
	closeBoth := func() {
		closeOnce.Do(func() {
			client.Close()
			remote.Close()
		})
	}

	go func() {
		defer wg.Done()
		defer closeBoth()
		io.Copy(remote, client)
	}()

	go func() {
		defer wg.Done()
		defer closeBoth()
		io.Copy(client, remote)
	}()

	wg.Wait()
}

// relayBlackhole 假装连接成功，吞掉所有 c2s 数据不转发
// 用于灯塔上报等"发出去就有害"的连接
func (s *Socks5Server) relayBlackhole(client net.Conn, dstAddr string, dstPort int) {
	defer client.Close()
	clientIP := extractClientIP(client)
	buf := make([]byte, 65536)
	total := 0
	for {
		n, err := client.Read(buf)
		if n > 0 {
			total += n
			s.clientTracker.AddBytes(clientIP, 0, int64(n))
			// 全部丢弃
		}
		if err != nil {
			break
		}
	}
	logInfo("[BLACKHOLE] 拦截完成: %s:%d 共吞掉 %d 字节", dstAddr, dstPort, total)
}

func copyBytes(data []byte) []byte {
	c := make([]byte, len(data))
	copy(c, data)
	return c
}

func hexHead(data []byte, n int) string {
	if len(data) < n {
		n = len(data)
	}
	return hex.EncodeToString(data[:n])
}
