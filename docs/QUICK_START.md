# 快速上手 — 给新对话的 AI 看

## 项目是什么

和平精英自动化匹配系统。6 个模拟器（3+3 两组）自动组队、匹配、同局检测、断网退出，循环运行。

## 环境

| 机器 | 用途 | 连接方式 |
|------|------|----------|
| macOS (本机) | 开发、构建 APK、AI 工作环境 | 直接操作 |
| Windows 192.168.0.102 | 运行模拟器 (LDPlayer)、游戏脚本 | Remote Agent :9100 |
| 服务器 38.22.234.228 | game_proxy 代理、封包抓取 | Remote Agent :9100 |

## 连接 Windows (192.168.0.102)

```bash
# 测试连接
curl -s -H "X-Auth: <密码>" http://192.168.0.102:9100

# 执行命令
curl -s -X POST http://192.168.0.102:9100/exec \
  -H "Content-Type: application/json" \
  -H "X-Auth: <密码>" \
  -d '{"cmd": "echo hello"}'

# ADB (雷电5)
D:\leidian\LDPlayer64\adb.exe -s emulator-5554 shell <命令>
```

密码每次启动 Remote Agent 会变，用户会提供。

## 连接服务器 (38.22.234.228)

```bash
# 测试连接
curl -s -H "X-Auth: <密码>" http://38.22.234.228:9100

# 执行命令（同上格式）
# game_proxy 日志
type C:\captures\session_XXX\proxy.log
```

## game_proxy 启动命令

```bash
# 在服务器上（关键：不加 --ca-cert --ca-key，否则会封号）
python C:/game_proxy/server/game_proxy.py --port 9900 --rules C:/game_proxy/server/rules.json --dry-run --capture-dir C:/captures/session_XXX --capture-ports 8085,8080,50000,20000 --rule-debug --log-file C:/captures/session_XXX/proxy.log

# 通过 Remote Agent 启动
powershell "Start-Process cmd -ArgumentList '/c python C:/game_proxy/server/game_proxy.py --port 9900 ...'"
```

## FightMaster APK 构建

```bash
cd /Users/Zhuanz/Vexa/game-automation/vpn-app
./gradlew assembleDebug
# 输出: app/build/outputs/apk/debug/app-debug.apk
```

当前 APK 只有 x86 native libs，适配 LDPlayer（x86 模拟器）。

## 关键目录

```
game-automation/
├── backend/       # Python 后端（状态机、协调器、识别）
├── web/           # React 前端（控制面板）
├── vpn-app/       # FightMaster Android VPN 加速器
├── proxy/         # game_proxy 代理服务端代码
├── data/          # 抓包数据（session_001/002/003）
├── tools/         # host_memscan.py（宿主机内存扫描）
├── fixtures/      # 模板图片（1280×720 标准）
├── tests/         # 测试脚本
└── docs/          # 技术文档
```

## FightMaster VPN 控制

通过 ADB 广播控制，不需要 UI 交互。backend 的 `single_runner.py` 已集成。

```bash
# 启动 VPN（默认代理 38.22.234.228:9900）
adb shell am broadcast -a com.fightmaster.vpn.START -n com.fightmaster.vpn/.CommandReceiver

# 启动 VPN（自定义代理地址）
adb shell am broadcast -a com.fightmaster.vpn.START -n com.fightmaster.vpn/.CommandReceiver --es proxy_host "1.2.3.4" --ei proxy_port 9900

# 停止 VPN
adb shell am broadcast -a com.fightmaster.vpn.STOP -n com.fightmaster.vpn/.CommandReceiver
```

注意：首次使用需在模拟器 UI 上手动授权 VPN 权限（之后就不需要了）。

## 关键技术决策（已验证）

| 决策 | 结论 |
|------|------|
| 加速器 | FightMaster VPN，ADB 广播控制，代理地址可配置 |
| 代理方式 | FightMaster VPN → game_proxy SOCKS5，**不加 MITM** |
| 封号原因 | MITM 导致 SSL 错误被 ACE 检测，去掉后不封号 |
| 同局检测 | game_proxy 检测 UDP ASSOCIATE 爆发 → 提取战斗服 IP 比对 |
| 玩家验证 | host_memscan.py 从宿主机 ReadProcessMemory 读 VM 内存 |
| 模板匹配 | 1280×720 灰度截图，多尺度匹配 |
| 弹窗关闭 | 遮罩亮度分析 + 模板匹配找 X 按钮 |

## 待实现：QQ Token 抓号上号

capture mode 代码已从 game_proxy 和 FightMaster 中移除。保留 `proxy/tls_mitm.py` 和 `proxy/token_capture.py` 供未来使用。

**验证发现的阻塞点：**
- MITM 白屏：WebView 不信任自签 CA（Android 7+ Network Security）
- Scheme URL：和平精英 AppID `1106467070`，scheme `tencent1106467070://`，MSDK v5.20+ 可能已屏蔽
- Token 格式：`access_token=xxx&openid=xxx&pay_token=xxx&expires_in=xxx`

**待探索方向：** Xposed hook、SharedPreferences 备份恢复、浏览器独立 OAuth

**详细计划：** `docs/superpowers/plans/2026-04-14-qq-token-capture.md`

## 已知问题

- 雷电5 是 32 位 Android，6 个模拟器同时跑游戏可能 OOM
- 雷电9 是 64 位但 FightMaster 的 libgojni.so 通过 houdini 翻译会崩溃
- 封包规则（rules.json）11 条全部不匹配当前游戏版本的封包
- QQ 扫码登录需要 VPN 路由里把 QQ 域名设为直连

## 相关记忆文件

Claude 的 memory 文件在 `/Users/Zhuanz/.claude/projects/-Users-Zhuanz-Vexa/memory/`，包含：
- `no_mitm_breakthrough.md` — 去掉 MITM 不封号的完整记录
- `fightmaster_breakthrough.md` — FightMaster 链路打通记录
- `memscan_findings.md` — 宿主机内存扫描方案
- `accelerator_architecture.md` — 加速器架构演进
