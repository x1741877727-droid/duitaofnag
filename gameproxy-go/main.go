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
	flagTokens       = flag.String("tokens", "", "Token 文件路径（每行一个 token）")
	flagLogFile     = flag.String("log-file", "", "日志输出到文件")
	flagCaptureDir   = flag.String("capture-dir", "", "封包抓取输出目录")
	flagCapturePorts = flag.String("capture-ports", "", "只抓这些端口（逗号分隔，为空抓所有）")
	flagZaixStrict   = flag.Bool("zaix-strict", true, "严格 zaix 路由（非白名单 SOCKS5 拒绝，省带宽）")
	flagBlockActivity = flag.Bool("block-activity", true, "拦截开场活动弹窗（17500 type=0x1340 早期大帧 drop）")
	flagRule2Disabled = flag.Bool("rule2-disabled", false, "临时关闭 Rule 2 改写（纯净抓包实验用，有封号风险）")
	flagRule1Disabled = flag.Bool("rule1-disabled", false, "临时关闭 Rule 1 改写（测登录冲突用）")
	flagRule2MaxBody = flag.Int("rule2-max-body", 0, "Rule 2 只改 17500 帧 body size < 此值的帧（0=全量；推荐 5000 只打心跳帧）")
	flagRule2Fingerprint = flag.Bool("rule2-fingerprint", true, "443 S→C Rule 2 用 8B fingerprint (3cd0ecaa88be0a92) 精确匹配 ACE 规则包；false=旧版全量")
	flagBlockActivityFingerprint = flag.Bool("block-activity-fingerprint", false, "破坏 activity s2c 包（opcode 04/05 → 00），让客户端解析失败")
	flagDropActivityFrames = flag.Bool("drop-activity-frames", false, "17500 S→C frame-level DROP activity 资源大包 (channel 00 10 51/65 00)")

	flagDropNonG617500AfterSec = flag.Int("drop-nonG6-17500-after-sec", 0, "17500 S→C 非 G6 包：conn age 阈值（秒），0=关闭")
	flagDropNonG617500Enforce  = flag.Bool("drop-nonG6-17500-enforce", false, "false=log-only（不拦，仅记日志）；true=实际 drop")

	// Step 2 脱 APK
	flagTunMode      = flag.Bool("tun-mode", false, "Windows 启用 wintun TUN inbound (脱 APK 模式)")
	flagTunName      = flag.String("tun-name", "gp-tun", "wintun 网卡名 (仅 Windows)")
	flagTunRouteFile = flag.String("tun-route-file", "", "TUN 路由白名单文件 (每行一个 CIDR / port / domain)")
)

// hasServiceSubcommand: 检测 args[1] 是不是 install/uninstall/start/stop/status
// 必须在 flag.Parse 之前调 (子命令不参与 flag 解析)
func hasServiceSubcommand() (string, bool) {
	if len(os.Args) < 2 {
		return "", false
	}
	cmd := strings.ToLower(os.Args[1])
	switch cmd {
	case "install", "uninstall", "start", "stop", "status":
		return cmd, true
	}
	return "", false
}

func main() {
	// 1. 子命令优先 (在 flag.Parse 之前 intercept)
	if cmd, ok := hasServiceSubcommand(); ok {
		handleServiceCmd(cmd)
		return
	}

	// 2. Windows service 模式检测 (SCM 启动 binary 时进这里)
	if runAsServiceIfNeeded() {
		return
	}

	// 3. 普通命令行模式
	flag.Parse()
	setupLogging()

	ctx, cancel := context.WithCancel(context.Background())
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sigCh
		fmt.Println()
		logInfo("服务停止 (signal)")
		cancel()
	}()

	if err := runProxyMain(ctx); err != nil {
		log.Fatalf("启动失败: %v", err)
	}
}

func setupLogging() {
	log.SetFlags(log.Ldate | log.Ltime | log.Lmicroseconds)
	if *flagLogFile != "" {
		f, err := os.OpenFile(*flagLogFile, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
		if err != nil {
			log.Fatalf("无法打开日志文件: %v", err)
		}
		log.SetOutput(f)
	}
}

// runProxyMain 是核心主循环, ctx done 时优雅退出.
// 普通 CLI 模式 + service 模式都调这个.
func runProxyMain(ctx context.Context) error {
	// Token 加载
	var tokens map[string]bool
	if *flagTokens != "" {
		data, err := os.ReadFile(*flagTokens)
		if err != nil {
			return fmt.Errorf("加载 Token 失败: %w", err)
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

	// 抓包初始化
	var capture *PacketCapture
	if *flagCaptureDir != "" {
		var ports map[int]bool
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
			return fmt.Errorf("初始化抓包失败: %w", err)
		}
	}

	server := NewSocks5Server(ServerConfig{
		Host:                     *flagHost,
		Port:                     *flagPort,
		Tokens:                   tokens,
		Capture:                  capture,
		ZaixStrict:               *flagZaixStrict,
		BlockActivity:            *flagBlockActivity,
		Rule2Disabled:            *flagRule2Disabled,
		Rule1Disabled:            *flagRule1Disabled,
		Rule2MaxBody:             *flagRule2MaxBody,
		Rule2Fingerprint:         *flagRule2Fingerprint,
		BlockActivityFingerprint: *flagBlockActivityFingerprint,
		DropActivityFrames:       *flagDropActivityFrames,
		DropNonG617500AfterSec:   *flagDropNonG617500AfterSec,
		DropNonG617500Enforce:    *flagDropNonG617500Enforce,
	})

	// 启动配置 log
	if *flagRule2Disabled {
		logInfo("⚠️  Rule 2 已关闭（纯净抓包模式）—— 注意封号风险")
	}
	if *flagRule2MaxBody > 0 {
		logInfo("Rule 2 精细化：只改 17500 帧 body_size < %d（放行大内容帧）", *flagRule2MaxBody)
	}
	if *flagRule2Fingerprint {
		logInfo("Rule 2 fingerprint：443 S→C 精确匹配 3c d0 ec aa 88 be 0a 92 (零误伤)")
	} else {
		logInfo("Rule 2 fingerprint：关闭，443 S→C 退回旧版 patchWPEAdvanced 全量模式")
	}
	if *flagBlockActivityFingerprint {
		logInfo("⚠️  拦活动开启：443 S→C 破坏 activity opcode 04/05 → 00 (商城/对战不受影响)")
	}
	if *flagDropActivityFrames {
		logInfo("⚠️  拦活动 v2 开启：17500 S→C frame-level DROP channel 00 10 51/65 00 (战斗 0x53 不受影响)")
	}
	if *flagDropNonG617500AfterSec > 0 {
		mode := "log-only"
		if *flagDropNonG617500Enforce {
			mode = "⚠️ ENFORCE (真 drop)"
		}
		logInfo("R2 实验：17500 S→C 非 G6 拦截 conn_age>%ds [%s]", *flagDropNonG617500AfterSec, mode)
	}
	if *flagRule1Disabled {
		logInfo("⚠️  Rule 1 已关闭（测登录冲突模式）—— 无防封保护")
	}
	if *flagZaixStrict {
		logInfo("zaix 严格路由：非白名单直接拒绝（省带宽）")
	} else {
		logInfo("zaix 宽松路由：全部接受（兼容模式，不省带宽）")
	}
	if *flagBlockActivity {
		logInfo("开场活动拦截：开启（17500 type=0x1340 seq<=0x40 size>3000 drop）")
	} else {
		logInfo("开场活动拦截：关闭")
	}

	go StartAPIServer(formatAPIAddr(*flagHost, *flagPort), server)

	// Step 2 脱 APK: TUN inbound
	if *flagTunMode {
		go func() {
			if err := startTunInbound(server, TunConfig{
				Name:      *flagTunName,
				RouteFile: *flagTunRouteFile,
			}); err != nil {
				logWarn("TUN inbound: %v (继续以 SOCKS5-only 模式运行)", err)
			}
		}()
	}

	// ListenAndServe block 直到 ctx cancel 或 server fail
	go func() {
		<-ctx.Done()
		logInfo("收到停止信号, 关闭 SOCKS5 server...")
		server.Shutdown()
		if capture != nil {
			logInfo("抓取封包数: %d, 连接数: %d", capture.PktCount(), capture.ConnCount())
		}
	}()

	if err := server.ListenAndServe(ctx); err != nil && ctx.Err() == nil {
		return fmt.Errorf("ListenAndServe: %w", err)
	}
	return nil
}

func logInfo(format string, args ...interface{}) {
	log.Printf("[INFO] game_proxy: "+format, args...)
}

func logWarn(format string, args ...interface{}) {
	log.Printf("[WARN] game_proxy: "+format, args...)
}

func logDebug(format string, args ...interface{}) {
	log.Printf("[DEBUG] game_proxy: "+format, args...)
}
