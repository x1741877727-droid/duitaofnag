//go:build windows

// dialer_windows.go — wintun routing loop bypass
//
// 问题:
//   wintun TUN 模式下, 路由表把游戏服 IP CIDR 指向 gp-tun.
//   gameproxy 自己调 net.Dial("tcp", "<game IP>") 后,
//   host kernel 查路由表 -> 进 gp-tun -> 又回到 gameproxy -> 又 dial -> 死循环.
//
// 方案 (sing-box / clash 同款):
//   gameproxy 的 outbound socket 用 IP_UNICAST_IF 绑到物理网卡,
//   绕过路由表查表, 直接从物理网卡发出.
//   IP_UNICAST_IF 期望 network byte order (big-endian) interface index.
//
// SOCKS5-only 模式 (没 wintun): physicalIfIdx==0, dialer.Control return nil 不绑,
//   行为跟普通 net.Dialer 一致, 兼容.

package main

import (
	"net"
	"sync/atomic"
	"syscall"
	"time"
)

// IP_UNICAST_IF Windows IPPROTO_IP option (ws2tcpip.h, value 31)
const IP_UNICAST_IF = 31

var physicalIfIdx uint32 // 0 = 未配置, dialer 不绑

// SetPhysicalIfIdx 由 service / startTunInbound 在探测物理网卡后调用
func SetPhysicalIfIdx(idx uint32) {
	atomic.StoreUint32(&physicalIfIdx, idx)
	if idx > 0 {
		logInfo("[BIND] outbound socket -> physical iface idx=%d (avoid wintun loop)", idx)
	}
}

// dialerForRelay 返回 net.Dialer; 若 physicalIfIdx 已配, 绑物理网卡
func dialerForRelay(timeout time.Duration) *net.Dialer {
	return &net.Dialer{
		Timeout:   timeout,
		KeepAlive: 30 * time.Second,
		Control:   bindToPhysical,
	}
}

func bindToPhysical(network, address string, c syscall.RawConn) error {
	idx := atomic.LoadUint32(&physicalIfIdx)
	if idx == 0 {
		return nil
	}
	be := htonlU32(idx)
	var serr error
	err := c.Control(func(fd uintptr) {
		serr = syscall.SetsockoptInt(syscall.Handle(fd), syscall.IPPROTO_IP, IP_UNICAST_IF, int(int32(be)))
	})
	if err != nil {
		return err
	}
	return serr
}

// htonlU32 host -> network byte order (big endian) for u32
func htonlU32(u uint32) uint32 {
	return ((u & 0xFF) << 24) | ((u & 0xFF00) << 8) | ((u & 0xFF0000) >> 8) | ((u & 0xFF000000) >> 24)
}

// detectPhysicalIfIdx — 找物理网卡 (排除 loopback / gp-tun / DOWN)
// 取第一个有 non-loopback IPv4 的 UP interface.
func detectPhysicalIfIdx() uint32 {
	ifaces, err := net.Interfaces()
	if err != nil {
		return 0
	}
	for _, iface := range ifaces {
		if iface.Flags&net.FlagUp == 0 || iface.Flags&net.FlagLoopback != 0 {
			continue
		}
		if iface.Name == "gp-tun" {
			continue
		}
		addrs, _ := iface.Addrs()
		for _, a := range addrs {
			ipnet, ok := a.(*net.IPNet)
			if !ok {
				continue
			}
			ip := ipnet.IP.To4()
			if ip == nil || ip.IsLoopback() || ip.IsLinkLocalUnicast() {
				continue
			}
			// 找到第一个物理 IPv4 接口
			return uint32(iface.Index)
		}
	}
	return 0
}
