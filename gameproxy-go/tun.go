//go:build !windows

// TUN inbound 仅 Windows 支持. 非 Windows 平台提供 stub 让 main.go 的引用编得过.
// 真实实现见 tun_windows.go.

package main

import "errors"

type TunConfig struct {
	Name      string
	RouteFile string
}

func startTunInbound(s *Socks5Server, cfg TunConfig) error {
	return errors.New("TUN 模式仅 Windows 支持 (本 binary 非 Windows)")
}
