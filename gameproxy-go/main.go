package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
)

var (
	flagHost         = flag.String("host", "0.0.0.0", "监听地址")
	flagPort         = flag.Int("port", 9900, "监听端口")
	flagRules        = flag.String("rules", "", "规则 JSON 文件路径")
	flagTokens       = flag.String("tokens", "", "Token 文件路径（每行一个 token）")
	flagConvertFP    = flag.String("convert-fp", "", "转换 WPE .fp 文件为 JSON 并退出")
	flagDryRun       = flag.Bool("dry-run", false, "只记录规则匹配，不实际修改封包")
	flagLogFile      = flag.String("log-file", "", "日志输出到文件")
	flagCACert       = flag.String("ca-cert", "", "CA 证书路径（启用 TLS MITM）")
	flagCAKey        = flag.String("ca-key", "", "CA 私钥路径（启用 TLS MITM）")
	flagCaptureDir   = flag.String("capture-dir", "", "封包抓取输出目录（启用完整抓包）")
	flagCapturePorts = flag.String("capture-ports", "8085,8080,50000,20000", "只抓这些端口（逗号分隔）")
	flagRuleDebug    = flag.Bool("rule-debug", false, "规则不命中时记录失败原因")
)

func main() {
	flag.Parse()

	// 日志设置
	log.SetFlags(log.Ldate | log.Ltime | log.Lmicroseconds)
	if *flagLogFile != "" {
		f, err := os.OpenFile(*flagLogFile, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
		if err != nil {
			log.Fatalf("无法打开日志文件: %v", err)
		}
		defer f.Close()
		log.SetOutput(f)
	}

	// --convert-fp 模式
	if *flagConvertFP != "" {
		if err := RunFPConvert(*flagConvertFP); err != nil {
			log.Fatalf("转换失败: %v", err)
		}
		return
	}

	// 加载规则
	ruleEngine := NewRuleEngine(*flagDryRun, *flagRuleDebug)
	if *flagRules != "" {
		if err := ruleEngine.LoadFromJSON(*flagRules); err != nil {
			log.Fatalf("加载规则失败: %v", err)
		}
	}
	if *flagDryRun {
		logInfo("*** DRY-RUN 模式：只记录规则匹配，不修改封包 ***")
	}
	if *flagRuleDebug {
		logInfo("*** RULE-DEBUG 模式：记录规则诊断信息 ***")
	}

	// 加载 Token
	var tokens map[string]bool
	if *flagTokens != "" {
		data, err := os.ReadFile(*flagTokens)
		if err != nil {
			log.Fatalf("加载 Token 失败: %v", err)
		}
		tokens = make(map[string]bool)
		for _, line := range strings.Split(string(data), "\n") {
			t := strings.TrimSpace(line)
			if t != "" {
				tokens[t] = true
			}
		}
		logInfo("加载 %d 个 Token", len(tokens))
	}

	// 初始化封包抓取
	var capture *PacketCapture
	if *flagCaptureDir != "" {
		var ports map[int]bool
		// "all" = 抓所有端口（传 nil 给 PacketCapture）
		if *flagCapturePorts != "" && strings.ToLower(*flagCapturePorts) != "all" {
			ports = make(map[int]bool)
			for _, p := range strings.Split(*flagCapturePorts, ",") {
				pv, err := strconv.Atoi(strings.TrimSpace(p))
				if err == nil {
					ports[pv] = true
				}
			}
		}
		var err error
		capture, err = NewPacketCapture(*flagCaptureDir, ports)
		if err != nil {
			log.Fatalf("初始化抓包失败: %v", err)
		}
	}

	// 创建服务器
	server := NewSocks5Server(ServerConfig{
		Host:       *flagHost,
		Port:       *flagPort,
		Tokens:     tokens,
		RuleEngine: ruleEngine,
		Capture:    capture,
	})

	// 启动 HTTP API（端口 = proxy port + 1）
	go StartAPIServer(formatAPIAddr(*flagHost, *flagPort), server)

	// 信号处理
	ctx, cancel := context.WithCancel(context.Background())
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sigCh
		fmt.Println()
		logInfo("服务停止")
		cancel()
		server.Shutdown()

		stats := ruleEngine.Stats()
		logInfo("=== 最终统计 ===")
		logInfo("总封包: %d", stats.TotalPackets)
		logInfo("已改写: %d", stats.ModifiedPackets)
		logInfo("规则命中: %v", stats.MatchedRules)
		if capture != nil {
			logInfo("抓取封包数: %d", capture.PktCount())
			logInfo("抓取连接数: %d", capture.ConnCount())
		}
		os.Exit(0)
	}()

	// 启动
	if err := server.ListenAndServe(ctx); err != nil && ctx.Err() == nil {
		log.Fatalf("启动失败: %v", err)
	}
}

// 日志辅助函数
func logInfo(format string, args ...interface{}) {
	log.Printf("[INFO] game_proxy: "+format, args...)
}

func logWarn(format string, args ...interface{}) {
	log.Printf("[WARN] game_proxy: "+format, args...)
}

func logDebug(format string, args ...interface{}) {
	if *flagRuleDebug {
		log.Printf("[DEBUG] game_proxy: "+format, args...)
	}
}
