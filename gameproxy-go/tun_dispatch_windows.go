//go:build windows

// gVisor netstack dispatch (Round 4: TCP 真接通 socks5.relay) — wintun → stack → forwarder → relay
//
// Round 4 行为:
//   - TCP zaixReject  → RST (拒 ACE 域名)
//   - TCP zaixDirect  → CreateEndpoint + net.Dial(真目标) + srv.relayPassthrough() 透传不改写
//   - TCP zaixProxy   → CreateEndpoint + net.Dial(真目标) + srv.relay() 复用 Rule1/Rule2 改写
//   - UDP             → 暂仍 log only, Round 4.5 接通
//
// 关键: TCP 命中 zaixProxy 后调 srv.relay(gonetConn, remote, dst, port, "") —
// 完全复用现有 Rule1 / Rule2 fingerprint / activity drop / acecrypto 改写, 一行不改.
//
// 仍 lab 模式: Windows 路由表不动, 真实 emulator 流量不会进 wintun.
// Phase B 部署门闸: Step 1 ≥ 24h 稳 + 路由白名单完备 (Round 4.5).

package main

import (
	"fmt"
	"net"
	"strconv"
	"time"

	"golang.zx2c4.com/wireguard/tun"

	"gvisor.dev/gvisor/pkg/buffer"
	"gvisor.dev/gvisor/pkg/tcpip"
	"gvisor.dev/gvisor/pkg/tcpip/adapters/gonet"
	"gvisor.dev/gvisor/pkg/tcpip/header"
	"gvisor.dev/gvisor/pkg/tcpip/link/channel"
	"gvisor.dev/gvisor/pkg/tcpip/network/ipv4"
	"gvisor.dev/gvisor/pkg/tcpip/stack"
	"gvisor.dev/gvisor/pkg/tcpip/transport/tcp"
	"gvisor.dev/gvisor/pkg/tcpip/transport/udp"
	"gvisor.dev/gvisor/pkg/waiter"
)

const tunNICID tcpip.NICID = 1

// startTunDispatch 启 gVisor stack + 双向 pump + TCP/UDP forwarder.
// 返回 stack 让调用方做 graceful shutdown.
func startTunDispatch(srv *Socks5Server, dev tun.Device, mtu uint32) (*stack.Stack, error) {
	s := stack.New(stack.Options{
		NetworkProtocols: []stack.NetworkProtocolFactory{
			ipv4.NewProtocol,
		},
		TransportProtocols: []stack.TransportProtocolFactory{
			tcp.NewProtocol,
			udp.NewProtocol,
		},
		HandleLocal: false,
	})

	linkEP := channel.New(512, mtu, "")
	if tcpipErr := s.CreateNIC(tunNICID, linkEP); tcpipErr != nil {
		return nil, fmt.Errorf("CreateNIC: %s", tcpipErr)
	}

	// transparent intercept: 接受任意 dst IP
	s.SetSpoofing(tunNICID, true)
	s.SetPromiscuousMode(tunNICID, true)

	// 默认路由 catch-all
	s.SetRouteTable([]tcpip.Route{
		{Destination: header.IPv4EmptySubnet, NIC: tunNICID},
	})

	// TCP forwarder (Round 4: 真接通 srv.relay / relayPassthrough)
	tcpFw := tcp.NewForwarder(s, 0, 2048, func(req *tcp.ForwarderRequest) {
		id := req.ID()
		dstAddr := id.LocalAddress.String()
		dstPort := int(id.LocalPort)

		decision := zaixRoute(dstAddr, dstPort)
		if decision == zaixReject {
			logInfo("[TUN] TCP REJECT %s:%d (ACE)", dstAddr, dstPort)
			req.Complete(true) // RST
			return
		}

		// 先 dial 真目标 — 失败就 RST 不浪费 endpoint 创建
		target := net.JoinHostPort(dstAddr, strconv.Itoa(dstPort))
		remote, dialErr := net.DialTimeout("tcp", target, 5*time.Second)
		if dialErr != nil {
			logInfo("[TUN] TCP %s dial 失败: %v", target, dialErr)
			req.Complete(true)
			return
		}

		// gVisor endpoint: 接管 emulator 端的 TCP 半连接
		var wq waiter.Queue
		ep, tcpipErr := req.CreateEndpoint(&wq)
		if tcpipErr != nil {
			logWarn("[TUN] TCP CreateEndpoint %s:%d: %s", dstAddr, dstPort, tcpipErr)
			remote.Close()
			req.Complete(true)
			return
		}
		req.Complete(false)

		client := gonet.NewTCPConn(&wq, ep)

		// TCP keepalive + nodelay 跟 socks5 走 relay 同样配置
		if tc, ok := remote.(*net.TCPConn); ok {
			tc.SetNoDelay(true)
			tc.SetReadBuffer(65536)
			tc.SetWriteBuffer(65536)
		}

		switch decision {
		case zaixDirect:
			logInfo("[TUN] TCP DIRECT %s:%d", dstAddr, dstPort)
			go srv.relayPassthrough(client, remote)
		case zaixProxy:
			logInfo("[TUN] TCP PROXY %s:%d", dstAddr, dstPort)
			go srv.relay(client, remote, dstAddr, dstPort, "")
		}
	})
	s.SetTransportProtocolHandler(tcp.ProtocolNumber, tcpFw.HandlePacket)

	// UDP forwarder (Round 4.5 接通; 当前 log + drop)
	udpFw := udp.NewForwarder(s, func(req *udp.ForwarderRequest) {
		id := req.ID()
		logInfo("[TUN] UDP %s:%d -> drop (Round 4.5 will dispatch)", id.LocalAddress.String(), int(id.LocalPort))
	})
	s.SetTransportProtocolHandler(udp.ProtocolNumber, udpFw.HandlePacket)

	go pumpWintunToStack(dev, linkEP)
	go pumpStackToWintun(dev, linkEP)

	return s, nil
}

// pumpWintunToStack: 从 wintun 读 IP 包 → InjectInbound 给 stack
func pumpWintunToStack(dev tun.Device, linkEP *channel.Endpoint) {
	const batch = 16
	bufs := make([][]byte, batch)
	for i := range bufs {
		bufs[i] = make([]byte, 1600)
	}
	sizes := make([]int, batch)

	for {
		n, err := dev.Read(bufs, sizes, 0)
		if err != nil {
			logWarn("[TUN] pump in: wintun.Read: %v", err)
			return
		}
		for i := 0; i < n; i++ {
			sz := sizes[i]
			if sz < header.IPv4MinimumSize {
				continue
			}
			// 拷贝出来 (wintun buf 复用, gVisor 异步处理)
			payload := make([]byte, sz)
			copy(payload, bufs[i][:sz])

			v := payload[0] >> 4
			if v != 4 {
				continue // Round 4 仅 IPv4
			}
			pkt := stack.NewPacketBuffer(stack.PacketBufferOptions{
				Payload: buffer.MakeWithData(payload),
			})
			linkEP.InjectInbound(ipv4.ProtocolNumber, pkt)
			pkt.DecRef()
		}
	}
}

// pumpStackToWintun: stack outbound packet → wintun.Write
func pumpStackToWintun(dev tun.Device, linkEP *channel.Endpoint) {
	bufs := make([][]byte, 1)
	for {
		pkt := linkEP.ReadContext(nil)
		if pkt == nil {
			return
		}
		view := pkt.ToView()
		bufs[0] = view.AsSlice()
		if _, err := dev.Write(bufs, 0); err != nil {
			logWarn("[TUN] pump out: wintun.Write: %v", err)
		}
		view.Release()
		pkt.DecRef()
	}
}
