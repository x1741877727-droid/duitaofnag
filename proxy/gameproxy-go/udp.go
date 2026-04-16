package main

import (
	"context"
	"encoding/binary"
	"fmt"
	"net"
	"time"
)

// handleUDPAssociate 处理 SOCKS5 UDP ASSOCIATE 请求
func (s *Socks5Server) handleUDPAssociate(ctx context.Context, tcpConn net.Conn,
	dstAddr string, dstPort int) {

	// 创建 UDP socket
	udpConn, err := net.ListenPacket("udp", ":0")
	if err != nil {
		socks5Reply(tcpConn, 0x05)
		return
	}
	defer udpConn.Close()

	udpAddr := udpConn.LocalAddr().(*net.UDPAddr)
	logDebug("UDP ASSOCIATE: relay port=%d", udpAddr.Port)

	// 回复客户端：UDP relay 地址
	reply := make([]byte, 10)
	reply[0] = socks5Ver
	reply[1] = 0x00 // success
	reply[2] = 0x00
	reply[3] = socks5AtypIPv4
	// IP: 0.0.0.0
	binary.BigEndian.PutUint16(reply[8:], uint16(udpAddr.Port))
	tcpConn.Write(reply)

	// 监控 TCP 连接：关闭时终止 UDP relay
	go func() {
		buf := make([]byte, 1)
		tcpConn.Read(buf) // 阻塞直到 TCP 关闭
		udpConn.Close()
	}()

	// remoteMap: 远端地址 → 客户端地址
	type remoteInfo struct {
		clientAddr net.Addr
		atyp       byte
		dstAddr    string
		dstPort    int
	}
	remoteMap := make(map[string]*remoteInfo)

	buf := make([]byte, 65536)
	for {
		udpConn.SetReadDeadline(time.Now().Add(300 * time.Second))
		n, addr, err := udpConn.ReadFrom(buf)
		if err != nil {
			break
		}
		if n < 4 {
			continue
		}

		data := buf[:n]

		// SOCKS5 UDP: RSV(2) + FRAG(1) + ATYP(1) + DST.ADDR + DST.PORT + DATA
		frag := data[2]
		if frag != 0 {
			continue // 不支持分片
		}

		atyp := data[3]
		var targetAddr string
		var targetPort int
		var payload []byte

		switch atyp {
		case socks5AtypIPv4:
			if n < 10 {
				continue
			}
			targetAddr = net.IP(data[4:8]).String()
			targetPort = int(binary.BigEndian.Uint16(data[8:10]))
			payload = data[10:]
		case socks5AtypDomain:
			if n < 5 {
				continue
			}
			dlen := int(data[4])
			if n < 5+dlen+2 {
				continue
			}
			targetAddr = string(data[5 : 5+dlen])
			targetPort = int(binary.BigEndian.Uint16(data[5+dlen : 7+dlen]))
			payload = data[7+dlen:]
		default:
			continue
		}

		// 解析目标地址
		resolved, err := net.ResolveUDPAddr("udp", fmt.Sprintf("%s:%d", targetAddr, targetPort))
		if err != nil {
			continue
		}

		// 发送到目标
		_, err = udpConn.WriteTo(payload, resolved)
		if err != nil {
			continue
		}

		remoteMap[resolved.String()] = &remoteInfo{
			clientAddr: addr,
			atyp:       atyp,
			dstAddr:    targetAddr,
			dstPort:    targetPort,
		}

		// 接收回复（5 秒超时）
		udpConn.SetReadDeadline(time.Now().Add(5 * time.Second))
		rn, raddr, rerr := udpConn.ReadFrom(buf)
		if rerr != nil {
			continue
		}

		info, ok := remoteMap[raddr.String()]
		if !ok {
			continue
		}

		// 封装 SOCKS5 UDP 回复
		var replyHeader []byte
		if info.atyp == socks5AtypIPv4 {
			ip := net.ParseIP(info.dstAddr).To4()
			replyHeader = make([]byte, 10)
			replyHeader[3] = socks5AtypIPv4
			copy(replyHeader[4:8], ip)
			binary.BigEndian.PutUint16(replyHeader[8:10], uint16(info.dstPort))
		} else {
			nameBytes := []byte(info.dstAddr)
			replyHeader = make([]byte, 5+len(nameBytes)+2)
			replyHeader[3] = socks5AtypDomain
			replyHeader[4] = byte(len(nameBytes))
			copy(replyHeader[5:], nameBytes)
			binary.BigEndian.PutUint16(replyHeader[5+len(nameBytes):], uint16(info.dstPort))
		}

		replyData := append(replyHeader, buf[:rn]...)
		udpConn.WriteTo(replyData, info.clientAddr)
	}
}
