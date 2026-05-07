//go:build windows

// gVisor netstack dispatch (Round 3 lab 模式) — wintun → stack → forwarder → zaixRoute
//
// Round 3 范围:
//   - 创建 gVisor stack + channel.Endpoint + IPv4 + TCP/UDP forwarder
//   - wintun ↔ stack 双向 packet pump goroutines
//   - TCP/UDP forwarder callback 抽 dst IP/port + 调 zaixRoute() 路由决策
//   - 当前所有 forwarder 都 Complete(true) RST, 不接 socks5.relay (Round 4 才接)
//
// Round 4 接通点:
//   - tcpFw callback: req.CreateEndpoint → gonet.NewTCPConn → server.relay(...)
//   - 复用现有 Rule1/Rule2 fingerprint/activity drop/acecrypto 改写, 一行不改
//
// 仍 lab 模式: Windows 路由表不动, 任何真实 emulator 流量不会进 wintun.
// 测试方法 (Round 3): Windows 上手动 `route add` 把测试 IP 段指到 gp-tun, 看 log 出 [TUN] dispatch.

package main

import (
	"context"
	"fmt"

	"golang.zx2c4.com/wireguard/tun"

	"gvisor.dev/gvisor/pkg/buffer"
	"gvisor.dev/gvisor/pkg/tcpip"
	"gvisor.dev/gvisor/pkg/tcpip/header"
	"gvisor.dev/gvisor/pkg/tcpip/link/channel"
	"gvisor.dev/gvisor/pkg/tcpip/network/ipv4"
	"gvisor.dev/gvisor/pkg/tcpip/stack"
	"gvisor.dev/gvisor/pkg/tcpip/transport/tcp"
	"gvisor.dev/gvisor/pkg/tcpip/transport/udp"
)

const tunNICID tcpip.NICID = 1

// startTunDispatch 启 gVisor stack + 双向 pump + TCP/UDP forwarder.
// 返回 stack 让调用方做 graceful shutdown.
func startTunDispatch(server *Socks5Server, dev tun.Device, mtu uint32) (*stack.Stack, error) {
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

	// TCP forwarder
	tcpFw := tcp.NewForwarder(s, 0, 1024, func(req *tcp.ForwarderRequest) {
		id := req.ID()
		dstAddr := id.LocalAddress.String()
		dstPort := int(id.LocalPort)
		switch zaixRoute(dstAddr, dstPort) {
		case zaixReject:
			logInfo("[TUN] TCP REJECT %s:%d (ACE)", dstAddr, dstPort)
		case zaixDirect:
			logInfo("[TUN] TCP DIRECT %s:%d (R4 will dial-out)", dstAddr, dstPort)
		case zaixProxy:
			logInfo("[TUN] TCP PROXY %s:%d (R4 will dispatch socks5.relay)", dstAddr, dstPort)
		}
		req.Complete(true) // Round 3: 全部 RST, Round 4 才真接通
	})
	s.SetTransportProtocolHandler(tcp.ProtocolNumber, tcpFw.HandlePacket)

	// UDP forwarder
	udpFw := udp.NewForwarder(s, func(req *udp.ForwarderRequest) {
		id := req.ID()
		dstAddr := id.LocalAddress.String()
		dstPort := int(id.LocalPort)
		logInfo("[TUN] UDP %s:%d (R4 will dial-out direct)", dstAddr, dstPort)
		// Round 3: 不接 endpoint, 包丢弃
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
			// 拷贝出来 (wintun buf 会被复用, gVisor 拿走会异步处理)
			payload := make([]byte, sz)
			copy(payload, bufs[i][:sz])

			v := payload[0] >> 4
			if v != 4 {
				continue // Round 3 仅处理 IPv4
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
	ctx := context.Background()
	bufs := make([][]byte, 1)
	for {
		pkt := linkEP.ReadContext(ctx)
		if pkt == nil {
			return
		}
		// PacketBuffer 拼回完整 byte slice
		view := pkt.ToView()
		bufs[0] = view.AsSlice()
		if _, err := dev.Write(bufs, 0); err != nil {
			logWarn("[TUN] pump out: wintun.Write: %v", err)
		}
		view.Release()
		pkt.DecRef()
	}
}
