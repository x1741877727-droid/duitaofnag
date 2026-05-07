//go:build !windows

// dialer_other.go — 非 Windows 平台默认 net.Dialer (没 wintun 路由 loop 问题)
package main

import (
	"net"
	"time"
)

func dialerForRelay(timeout time.Duration) *net.Dialer {
	return &net.Dialer{
		Timeout:   timeout,
		KeepAlive: 30 * time.Second,
	}
}

func SetPhysicalIfIdx(idx uint32) {
	// no-op
}

func detectPhysicalIfIdx() uint32 {
	return 0
}
