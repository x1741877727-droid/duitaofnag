# Game Automation

这是 `game-automation` 项目的主说明文档，给人和 AI 都能直接上手用。

如果这个 README 和旧文档、历史记忆、批处理脚本里的说明冲突，优先相信下面这些源码文件：

- `backend/api.py`
- `backend/main.py`
- `backend/config.py`
- `backend/runner_service.py`
- `agents/remote_agent.py`

## 这个项目到底是什么

它不是单一小工具，而是三条线并行的项目集合：

1. **主自动化系统**
   - 控制 LDPlayer 多开
   - 通过 ADB、模板匹配、OCR、状态机驱动游戏流程
   - 提供浏览器控制台和后端 API

2. **远程运维 / 远程调试系统**
   - 后端服务运行在 `8900`
   - Remote Agent 运行在 `9100`
   - 让远程 AI 或远程操作者直接查看机器、读文件、跑命令、开 Web 终端

3. **网络 / 逆向 / 加速实验系统**
   - game proxy / 封包捕获
   - Android VPN app
   - 内存扫描、hook、模板处理、分析脚本

所以以后接手这个仓库时，第一件事不是“看一堆文件”，而是先判断问题属于哪条线。

## 当前真实架构

### 1. 主运行链路

- 程序入口：`backend/main.py`
- 后端 API：`backend/api.py`
- 多实例运行服务：`backend/runner_service.py`（事件循环驱动，**当前主控**）
- 自动化逻辑：`backend/automation/`
- 前端控制台：`web/`
- 封包代理：`gameproxy-go/`（Go 实现，171.80.4.221:9900 线上跑这个）

> `backend/handlers/` + `backend/coordinator.py` + `backend/state_machine.py` 是早期 7-phase 架构，已被 `runner_service.py` 事件循环替代，仅作为**归档**保留，不参与生产路径。

如果你的目标是：

- 启动机器人
- 看实例状态
- 看截图
- 改设置
- 改主流程逻辑

那优先看这一条。

### 2. 当前远程调试链路

- Remote Agent：`agents/remote_agent.py`
- Windows 启动脚本：`deploy/windows/start-remote-agent.bat`
- 最小化启动/停止：`deploy/windows/agent-start.vbs`、`deploy/windows/agent-stop.vbs`

Remote Agent 现在是这个项目最重要的远程协作入口。它当前提供：

- `GET /`
- `GET /health`
- `GET /ui`
- `POST /exec`
- `GET /history`
- `GET /read`
- `GET /download`
- `WS /ws/exec`

它运行在 `9100`，会：

- 把 token 持久化到 `agents/.remote_agent_token.txt`
- 把日志写到 `agents/remote_agent.log`
- 尝试自动启动 `cloudflared`
- 在启动后给出局域网地址、Web UI 地址、公网地址

### 3. 主后端 HTTP / UI 链路

主后端运行在 `8900`，当前真实暴露的接口来自 `backend/api.py`：

- `GET /api/emulators`
- `POST /api/start`
- `POST /api/start/{instance_index}`
- `POST /api/stop`
- `GET /api/status`
- `GET /api/settings`
- `PUT /api/settings`
- `GET /api/accounts`
- `PUT /api/accounts`
- `GET /api/screenshot/{instance_index}`
- `GET /api/health`
- `WS /ws`

重要：旧文档里提到的这些接口，**现在不是当前公开接口**：

- `/api/diagnostic/*`
- `/api/debug/*`
- `/api/pause`
- `/api/resume`

也就是说，后面如果 AI 看到这些旧路径，不要直接当真，先回到 `backend/api.py` 复核。

## 推荐的“先看哪里”

以后任何 AI 接手时，建议按这个顺序判断：

1. 如果是主流程、实例状态、截图、启动停止问题，先看 `backend/api.py`
2. 如果是运行方式、开发模式、桌面模式、前端挂载问题，先看 `backend/main.py`
3. 如果是配置文件、路径、账号映射问题，先看 `backend/config.py`
4. 如果是多实例调度、日志目录、模板加载、mock 流程问题，先看 `backend/runner_service.py`
5. 如果是远程执行、远程读文件、Remote Agent 鉴权问题，先看 `agents/remote_agent.py`

## 目录地图

```text
game-automation/
├── agents/                # Remote Agent，以及它自己的 token / log
├── artifacts/windows/     # 收口后的 Windows 二进制产物
├── backend/               # 主 Python 后端：API、自动化、识别（含 handlers 归档）
├── config/                # settings.json / accounts.json
├── deploy/windows/        # Windows 部署、启动、更新脚本
├── docs/                  # 方案、计划、流程、会话交接文档
├── fixtures/              # 模板图、截图样本
├── gameproxy-go/          # Go 封包代理（线上 171.80.4.221:9900 主防封）
├── reversing/             # Ghidra 工程归档（libtersafe / libgcloud 逆向）
├── tests/                 # phase1-7 测试脚本（与 handlers 归档同生死）
├── tools/                 # host_*.py 逆向脚本、yolo 训练、模板裁剪
├── vpn-app/               # Android VPN 工程 FightMaster
├── web/                   # React 控制台 + 封包 dashboard
├── build.py               # Windows 打包入口
├── version.py             # 版本号
├── MASTER_PLAN.md         # 项目主路线规划
└── FM_DTW_AUTH_PLAN.md    # FightMaster 认证方案
```

## 各目录到底负责什么

### `backend/`

这是主项目核心，绝大多数“产品逻辑”都在这里。

- `automation/`：ADB 控制、截图、OCR、模板识别、弹窗清理、单实例运行器（**当前主链**）
- `runner_service.py`：多实例启动、会话目录、模板加载、mock 运行（**事件循环主控**）
- `recognition/`：OCR (`ocr_reader.py`) + 模板匹配 (`template_matcher.py`)
- `config.py`：配置读写和路径解析
- `handlers/` + `coordinator.py` + `state_machine.py` + `instance_agent.py`：**归档**（早期 7-phase 架构，已被 runner_service 替代）

补充：`backend/diagnostic.py` 仍然存在，也确实有日志/截图/snapshot 能力，但它的历史 HTTP 接口目前没有挂载到 `backend/api.py` 里。

### `web/`

React 19 + Vite 前端，负责控制台界面。现在能看出它包含：

- dashboard
- battle / accelerator / deploy / settings 视图
- log panel
- WebSocket 状态联动

如果问题是“界面显示不对”“前端开发模式”“状态推送”，优先看这里。

### `agents/`

这是运维辅助层，不是主自动化逻辑本体。

Remote Agent 基本上就是一个高权限远程入口，只要 token 对，就能：

- 执行命令
- 读文件
- 下载文件
- 打开远程 Web 终端

所以把它当成“运维控制面”理解更准确。

### `gameproxy-go/`

**当前生产防封代理**，Go 实现，部署在 `171.80.4.221:9900`。核心是 5 层精细化规则（详见 `gameproxy-go/WPE_ADV_RULES.md`）：

- `relay.go`：Rule1（设备指纹伪装）+ Rule2-Sized（G6 帧限定）+ Rule2-Fingerprint（443 8B 精准匹配）+ ActivityDrop / ActivityBreak
- `socks5.go`、`main.go`、`clients.go`：SOCKS5 入口、CLI flags、客户端路由
- `acecrypto/`：OICQ TEA-CBC（17500 G6 协议解密）
- `query.go` / `labels.go`：封包 Dashboard API（`/api/conns`、`/api/timeline`、`/api/frame`、`/api/labels`）
- `cmd/`：独立工具（如 ace_decrypt_verify roundtrip 验证）
- `*_test.go`：每条规则的单元测试

旧 Python 版 `proxy/` 和 `server/` 已删除，全部统一到 Go 实现。

### `vpn-app/`

这是 Android VPN 工程，应用名是 `FightMaster`。Manifest 已确认暴露了广播控制入口：

- `com.fightmaster.vpn.START`
- `com.fightmaster.vpn.STOP`
- `com.fightmaster.vpn.STATUS`

它属于“模拟器里网络路由”的一部分，不属于 Python 后端本体。

### `tools/`

实验工具层，包含：

- `host_*.py`：逆向研究脚本（host_memscan、host_ace_sniff、host_readmem、TLV 队伍信息解析等）
- `yolo_*.py`：YOLO 训练 / autolabel / export / verify
- `auto_configure.py`：跨硬件自动配置 OCR 后端
- `crop_templates.py`、`golden_runner.py`：模板裁剪、单实例 golden 测试

如果问题只是主自动化跑不起来，不建议先一头扎进这里。

### `tests/`

这里现在是唯一可信的独立测试脚本目录。之前根目录重复的测试文件已经收口到这里。

## 配置文件

### `config/settings.json`

当前重要字段包括：

- `ldplayer_path`
- `adb_path`
- `llm_api_url`
- `llm_api_key`
- `game_package`
- `game_activity`
- `game_mode`
- `game_map`
- `match_timeout`
- `state_timeout`
- `screenshot_interval`
- `normalize_resolution`
- `dev_mock`
- `mock_screenshots_dir`

### `config/accounts.json`

负责账号和实例映射，主要是：

- 分组
- 角色
- instance index

当前 `backend/config.py` 会优先读取 `config/` 下的配置，只在兼容场景下回退到旧根目录布局。

## 怎么启动

### macOS 本地开发

```bash
pip install -r backend/requirements.txt
cd web
npm install
npm run build
cd ..
python backend/main.py --dev --mock --port 8900
```

浏览器打开 `http://127.0.0.1:8900`

### 前端热更新开发

终端 1：

```bash
python backend/main.py --dev --mock --port 8900
```

终端 2：

```bash
cd web
npm run dev
```

浏览器打开 `http://127.0.0.1:5174`

### Windows 真机 / 真模拟器后端模式

```bash
pip install -r backend/requirements.txt
python backend/main.py --dev --port 8900
```

### Windows Remote Agent 模式

```bat
deploy\windows\start-remote-agent.bat
```

或者用：

- `deploy\windows\agent-start.vbs`
- `deploy\windows\agent-stop.vbs`

## 当前调试模型

现在这个项目有两种远程面：

### A. `8900` 后端面

适合做这些事：

- 看控制台 UI
- 看实例状态
- 看截图
- 启动/停止自动化
- 改 settings / accounts

### B. `9100` Remote Agent 面

适合做这些事：

- 远程执行命令
- 直接读日志和文件
- 下载文件
- 打开 Web 终端
- 远程检查 Windows 机器本身

如果是 AI 远程协作，通常先走 `9100` 更高效，因为它能先看到机器，再决定要不要调用 `8900`。

## 调试方式现在和以前哪里不一样

以前的思路更像：

- 开 `8900`
- 用 cloudflared 暴露 `8900`
- 通过旧的 `/api/diagnostic/*`、`/api/debug/*` 去做远程诊断

现在不应该再这样理解。

当前更准确的理解是：

- `8900` 是产品后端和控制台 API
- `9100` 是远程执行和运维入口
- 旧 diagnostic/debug 文档是历史遗留，不能直接等同于当前接口

## 现在最容易混淆的点

### 1. 文档和代码并不完全同步

仓库里仍有一些旧文档、旧会话交接说明、旧脚本提示语，里面会写到历史接口。遇到冲突时：

1. 先信源码
2. 再信 README / REMOTE_DEBUG
3. 最后再回头修旧文档

### 2. `backend/diagnostic.py` 还在

它不是假的，也不是废文件，但它现在不等于“后端已经公开提供这些诊断 HTTP 接口”。

### 3. 防封规则的权威 SOT 是 `gameproxy-go/WPE_ADV_RULES.md`

任何关于"线上跑哪些规则"的疑问，先看这份文档，再看 `gameproxy-go/relay.go` 实现。memory 里旧的"3 条核心规则"描述已过时，当前是 5 层精细化叠加。

### 4. 二进制不只一份

`artifacts/windows/` 是整理后的 Windows 产物路径，`gameproxy-go/gameproxy` 是 Linux Go 二进制（部署目标 `/opt/gameproxy/gameproxy`）。两者用途不同，不要混。

## 打包

### 构建前端

```bash
cd web
npm install
npm run build
```

### 打包 Windows 可执行程序

```bash
python build.py
```

常用参数：

- `python build.py --pyinstaller`
- `python build.py --check`
- `python build.py --installer`

## 给下一个 AI 的接手建议

以后新的 AI 来到这个项目，建议先做这几步：

1. 先判断任务属于 `backend`、`web`、`agents`、`gameproxy-go`、`vpn-app` 还是 `tools`
2. 如果任务跟远程调试有关，先读 `agents/remote_agent.py`
3. 如果任务跟公开后端接口有关，先读 `backend/api.py`
4. 如果任务跟配置位置或路径有关，先读 `backend/config.py`
5. 如果任务跟代理 / 抓包 / 防封有关，先读 `gameproxy-go/WPE_ADV_RULES.md` 再看 `gameproxy-go/relay.go`

## 相关文档

- `REMOTE_DEBUG.md`：当前远程调试方法
- `DEPLOYMENT.md`：Windows 部署流程
- `MASTER_PLAN.md`：项目主路线规划
- `FM_DTW_AUTH_PLAN.md`：FightMaster apk 认证方案
- `docs/QUICK_START.md`：历史会话交接和环境摘要
- `docs/STABLE_FAST_ARCHITECTURE_PLAN.md`：稳定快速架构方案
- `gameproxy-go/WPE_ADV_RULES.md`：**防封规则权威 SOT**
