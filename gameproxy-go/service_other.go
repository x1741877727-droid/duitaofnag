//go:build !windows

// !windows stub — service 模式仅 Windows 支持
package main

import (
	"fmt"
	"os"
)

func runAsServiceIfNeeded() bool {
	return false
}

func handleServiceCmd(cmd string) {
	fmt.Fprintf(os.Stderr, "service subcommands (%s) only supported on Windows\n", cmd)
	os.Exit(1)
}
