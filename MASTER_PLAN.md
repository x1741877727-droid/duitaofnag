# game-automation 主计划（整合版）

> **定位**：整合现有 `STABLE_FAST_ARCHITECTURE_PLAN.md`、`OPTIMIZED_ARCHITECTURE_PLAN.md`、`FM_DTW_AUTH_PLAN.md`、`WPE_ADV_RULES.md`、`match_detection_plan.md` 五份散落文档，加入对现状代码的 gap 分析，给出唯一一份可执行的终版路线图。
>
> **目标**：6 开（后续 80 开）自动化多开的稳定性、速度、性能**三维同时拉到可实现上限**（~95% 理论天花板）。
>
> **最后更新**：2026-04-20

---

## 目录

1. [现状速览](#一现状速览)
2. [距离理论上限的差距](#二距离理论上限的差距)
3. [缺什么数据 / 资源](#三缺什么数据--资源)
4. [架构改造路线](#四架构改造路线)
5. [完整实施计划（6 大阶段 · 26 个任务）](#五完整实施计划6-大阶段--26-个任务)
6. [验收标准](#六验收标准)
7. [风险与对策](#七风险与对策)

---

## 一、现状速览

### 1.1 工作正常的部分

| 模块 | 文件 | 状态 |
|---|---|---|
| Runner 事件循环 + 分级恢复（3 tier） | [runner_service.py:378-573](backend/runner_service.py) | ✅ 已用 |
| 7 个 phase（accelerator/launch/wait_login/dismiss_popups/map/team_create/team_join） | [single_runner.py:106,343,399,427,455,639,886](backend/automation/single_runner.py) | ✅ 已用 |
| minicap 流式截图（30ms） + screencap 兜底 | [adb_lite.py:62-263](backend/automation/adb_lite.py) | ✅ 已用 |
| RapidOCR 同步识别 | [ocr_dismisser.py:84-89](backend/automation/ocr_dismisser.py) | ✅ 已用 |
| 多尺度模板匹配 | [screen_matcher.py:128-193](backend/automation/screen_matcher.py) | ✅ 已用 |
| 分辨率归一化到 1280×720 | [screen_matcher.py:18](backend/automation/screen_matcher.py) | ✅ 已用 |
| 弹窗守卫（可开关） | [guarded_adb.py:48-51](backend/automation/guarded_adb.py) | ✅ 已用 |
| 失败截图 + 会话日志 | [debug_logger.py:52-64](backend/automation/debug_logger.py) | ✅ 已用 |
| WebSocket 前端实时流 | [runner_service.py:71-96](backend/runner_service.py) | ✅ 已用 |
| 32 张模板 | [fixtures/templates/](fixtures/templates/) | ✅ 已用 |
| gameproxy WPE Advanced 规则 | [gameproxy-go/relay.go patchWPEAdvanced](gameproxy-go/relay.go) | ✅ 已用 |
| gameproxy zaix 路由（宽松模式已部署） | [gameproxy-go/routing.go](gameproxy-go/routing.go) | ✅ 服务器已部署 |

### 1.2 代码已写但**从未接入**（死代码，要么整合要么删）

| 模块 | 文件 | 问题 |
|---|---|---|
| transitions 状态机 | [backend/state_machine.py](backend/state_machine.py) | runner_service 没用，仍是事件循环 |
| handlers 架构 | [backend/handlers/*](backend/handlers/) | runner 不调用 |
| LLM 视觉识别（Gemini） | [backend/recognition/llm_vision.py](backend/recognition/llm_vision.py) | 未启用 |
| host_memscan 内存扫描 | [tools/host_memscan.py](tools/host_memscan.py) | runner 里无引用 |
| TLS MITM 证书劫持 | [backend/proxy/*](backend/proxy/) | 未接入 |
| gameproxy HTTP API（9901） | [gameproxy-go/clients.go](gameproxy-go/clients.go) | Python 端不调用 |

### 1.3 完全未实现

| 能力 | 影响 |
|---|---|
| 进局 phase（`phase_enter_game`） | 队伍建完就停了，进不了对战 |
| 同局检测（两队是否匹配到同一场） | 80 开核心价值无法验证 |
| 游戏闪退自动检测 | 要人工发现 |
| 模拟器卡死自动检测 | 同上 |
| 三信号融合（视觉 + 内存 + 网络） | 所有决策只靠视觉，慢且不稳 |
| frame_id / 过期丢弃机制 | 旧帧 OCR 结果能点击，乱点风险 |
| ROI 外部配置文件 | 要改 ROI 得改代码 |
| 点击后验证状态变化 | 点完就走，不知成功没 |
| 结构化性能日志 + 健康度指标 | 出问题只能翻文本日志 |
| MediaProjection 截图（UE4 稳定方案） | minicap 在 UE4 下间歇崩 |
| WPE 规则本地加密下发 | 规则硬编码，泄露 = 核心 IP 裸奔 |
| DTW 商户授权对接 | 80 台没法统一管理 |
| 二进制加壳（garble / VMProtect） | 反编译几分钟就看完代码 |

---

## 二、距离理论上限的差距

### 2.1 三维度现状评估

| 维度 | 当前 | 若只做「稳定高速架构优化计划」 | 加三信号融合 | 理论上限 |
|---|---|---|---|---|
| 稳定性 | 50% | 85% | **95%** | 98% |
| 速度 | 50% | 75%（OCR 300ms 下限） | **95%**（内存 2ms / 网络 50ms 替代大部分 OCR） | 98% |
| 性能（多开） | 50% | 85% | **90%** | 95% |

### 2.2 差距的根因 = 只用了 1/3 信号通道

| 信号通道 | 延迟 | 准确率 | 当前使用 |
|---|---|---|---|
| 🎥 视觉（OCR/模板） | 200-300ms | 95% | **全系统都走这条** |
| 💾 内存（host_memscan） | 2-3ms | 100% | 已存在，孤立 |
| 🌐 网络（gameproxy 流量） | 10-50ms | 100% | 已存在，孤立 |

**洞察**：把"队伍成员验证"、"进局检测"、"匹配成功判定"、"同局检测"等**硬信号决策**下沉到内存/网络层，视觉只保留"看屏幕才能得到的信息"（弹窗存在性、按钮坐标）。

---

## 三、缺什么数据 / 资源

下一轮开发会被**数据**卡住，而不是代码。以下清单必须并行准备：

### 3.1 模板资源（视觉识别）

**现状**：32 张 PNG in `fixtures/templates/`
**缺**：进局阶段 + 结算阶段的模板 ~30-50 张

| 类别 | 已有 | 需要补 |
|---|---|---|
| 大厅 | ✅ lobby_start_btn / lobby_start_game | — |
| 加速器 | ✅ accelerator_play / accelerator_pause | — |
| 弹窗关闭 | ✅ close_x_* 多变体 + btn_confirm/agree | 🟡 补 5-10 种新弹窗 |
| 组队 | ✅ btn_join_team / btn_share_team_code | 🟡 补 队员准备状态图标 |
| 地图卡 | ✅ card_classic_team / card_sniper_team | 🟡 补所有目标模式卡（~10 张） |
| **进局阶段** | ❌ | 🔴 加载页 / 跳伞准备 / 出生岛 / 场景切换 (~10 张) |
| **结算阶段** | ❌ | 🔴 对局结束 / 战报 / 返回大厅按钮 (~5 张) |
| **异常状态** | ❌ | 🔴 网络断开弹窗 / 挤号 / 服务器维护 (~5 张) |

**采集方式**：手动截屏 + 裁剪，每张 5 分钟，**总工期 4-6 小时**。

### 3.2 ROI 配置（识别范围）

**现状**：ROI 硬编码在代码里，scattered 在 ocr_dismisser / popup_dismisser / single_runner
**缺**：统一的 YAML 配置

```yaml
# config/roi.yaml（要新建）
phases:
  lobby:
    popup_detect:      [0.15, 0.20, 0.70, 0.60]  # 弹窗遮罩检测区
    start_button:      [0.85, 0.80, 0.12, 0.12]  # 右下开始按钮
    team_code_input:   [0.40, 0.50, 0.20, 0.05]
  
  map_setup:
    game_mode_tab:     [0.05, 0.15, 0.10, 0.50]
    map_grid:          [0.30, 0.15, 0.45, 0.70]
    confirm_btn:       [0.75, 0.85, 0.15, 0.08]
  
  in_match:
    hp_bar:            [0.05, 0.92, 0.15, 0.05]
    player_count:      [0.85, 0.02, 0.12, 0.05]
    match_time:        [0.45, 0.02, 0.10, 0.05]
    # ...
```

**收集方式**：一次性在代码里 grep 所有 `x/y/w/h` 数字 + 手标 ~20 个新 ROI，**0.5-1 天**。

### 3.3 内存 offset / 结构（memscan 信号源）

**现状**：`host_memscan.py` 只有队伍名字搜索（关键词 `team_info_notify` 等）
**缺**：结构化偏移表

要新补的内存字段：
- `game_phase` 的位置（LOBBY / MATCHING / LOADING / IN_MATCH / SETTLEMENT）
- `match_id` 或 `battle_server_addr`
- `player_alive_count`（判活）
- `team_ready_mask`（4 人里几个点了准备）
- `kill_count` / `settlement_rank`

**收集方式**：
1. 跑 host_memscan 扫关键词找**值的位置**
2. 进不同状态对比扫，找差异地址
3. 建立 YAML offset map

```yaml
# config/mem_offsets.yaml（要新建）
ldplayer9:
  game_phase:
    search_key: "pubgm.phase."   # 搜索锚定字符串
    offset_after_key: 42          # 关键字后第几字节是 phase code
    values:
      1: lobby
      2: matching
      3: loading
      4: in_match
      5: settlement
  battle_server:
    search_key: "netconn.addr:"
    type: ipv4_port
  # ...
```

**估计工期**：**2-3 天**（含抓取 + 对比 + 验证）

### 3.4 网络协议签名（gameproxy 事件信号）

**现状**：gameproxy 只做改包，没提取事件
**缺**：协议签名表

要识别的事件：
- **matchmaking_start**：客户端发起匹配请求的包 type（猜测 0x01XX 之类）
- **match_found**：服务端返回"匹配成功"的包
- **battle_connect**：新连接建立到 `:5692`（这个已知）
- **match_end**：对局结束
- **kick_notice**：被踢通知（挤号）

**收集方式**：
1. 跑游戏完整流程，`gameproxy-go -capture-dir` 抓全程
2. Python 脚本对比不同阶段的包特征（已有工具 `/tmp/find_systematic_diff.py` 可复用）
3. 建立 YAML 签名表

```yaml
# gameproxy-go/config/events.yaml（要新建）
events:
  match_found:
    pattern: "01 00 00 00 36 03"   # magic + type 0x0336
    direction: s2c
    ports: [443]
    min_len: 54
  
  matchmaking_start:
    pattern: "01 00 00 00 ?? ??"
    direction: c2s
    # ...
```

**工期**：**2-3 天**。

### 3.5 黄金回归测试集

**现状**：无
**缺**：标注好的帧数据集做回归测试

```
fixtures/golden_set/
├── lobby/
│   ├── clean.png            # 大厅无弹窗
│   ├── with_popup_01.png    # 有摸金杯弹窗
│   └── labels.json          # 每张图的真实 ROI + phase
├── popup/
│   └── ...
└── map_setup/
    └── ...
```

目标：
- 每 phase 覆盖 20+ 张标注帧
- 修改 OCR/模板后跑一遍 → 看 precision/recall 有没有回退
- **每次上线前必跑**

**工期**：**1 天**（一次性，含工具写 + 标 100 张）

### 3.6 数据需求汇总

| 资源 | 数量 | 工期 | 优先级 |
|---|---|---|---|
| 模板 PNG | +30-50 张 | 0.5-1 天 | 🔴 P0 |
| ROI YAML | 1 份（含 20+ ROI） | 0.5-1 天 | 🔴 P0 |
| 内存 offset YAML | 1 份（含 5-10 字段） | 2-3 天 | 🟡 P1 |
| 网络事件 YAML | 1 份（含 5-8 事件） | 2-3 天 | 🟡 P1 |
| 黄金测试集 | ~100 帧标注 | 1 天 | 🟢 P2 |

**数据总工期：6-9 天**，可与代码并行做。

---

## 四、架构改造路线

### 4.1 终态架构总览

```
┌─────────────────────────────────────────────────────────┐
│                     Windows PC                           │
│                                                          │
│  ┌───────────────────────┐   ┌─────────────────────┐    │
│  │ LDPlayer (Android VM) │   │ gameproxy.exe       │    │
│  │  ┌──────────────────┐ │   │  ├─ SOCKS5 :9900   │    │
│  │  │ PUBG Mobile      │ │◄──┤  ├─ WPE 改包       │    │
│  │  │ FightMaster VPN  │─┼───┤  ├─ zaix 路由      │    │
│  │  │ CaptureService   │ │   │  ├─ 事件 WS :9901  │    │
│  │  │ (MediaProjection)│ │   │  └─ 本地日志       │    │
│  │  └────┬─────────────┘ │   └─────────▲───────────┘    │
│  │       │ H.264 stream  │             │                │
│  │       │ adb forward   │             │ HTTP/WS        │
│  └───────┼───────────────┘             │                │
│          ▼                             │                │
│  ┌──────────────────────────────────────┴───────────┐   │
│  │ Python Runner（backend/）                          │   │
│  │                                                   │   │
│  │  ┌────────────┐ ┌──────────────┐ ┌────────────┐ │   │
│  │  │ FSM        │ │SignalFusion  │ │EventBus    │ │   │
│  │  │ (phases)   │◄┤  ├─ vision   │ │WebSocket   │ │   │
│  │  │            │ │  ├─ memory   │ │→ 前端      │ │   │
│  │  └────┬───────┘ │  └─ network  │ └────────────┘ │   │
│  │       │         └──────────────┘                 │   │
│  │       │         ▲        ▲                        │   │
│  │       │    ┌────┘        └────┐                  │   │
│  │       │    │                  │                   │   │
│  │       ▼    │                  │                   │   │
│  │  ┌──────────────┐ ┌──────────────┐                │   │
│  │  │ OCR调度器    │ │ host_memscan │                │   │
│  │  │ (async pool) │ │ (ReadProcMem)│                │   │
│  │  └──────────────┘ └──────────────┘                │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
                             │
                             │ HTTPS (激活 / 规则下发)
                             ▼
              ┌──────────────────────────┐
              │    DTW 后端（远程）        │
              │  /api/fm/activate         │
              │  /api/fm/refresh          │
              │  商户管理 + 审计          │
              └──────────────────────────┘
```

### 4.2 关键架构决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 控制流 | **True FSM（用 transitions 库）** 替换事件循环 | state_machine.py 已存在，只是没启用；恢复路径更可控 |
| 主截图后端 | CaptureService (MediaProjection + MediaCodec + adb forward) | UE4 下 minicap 不稳，scrcpy 同款方案 |
| OCR 执行 | Async worker pool（4 workers） + frame_id 过期保护 | 当前是同步阻塞 |
| 信号融合 | SignalFusion 组件 = vision + memory + network 三路投票 | 任何硬信号 hit 即决策，视觉 fallback |
| 事件总线 | gameproxy WS 推 + Python asyncio event bus | 取代"轮询等屏幕变化" |
| 配置外部化 | 所有 ROI / 内存 offset / 事件签名 → YAML | 游戏更新只改 yaml，不改代码 |
| 部署模式 | **本地 gameproxy.exe per PC** | 带宽从 5TB 省到 ~0；延迟 50ms→5ms |
| IP 保护 | DTW 授权 + AES 规则下发 + garble 混淆 | 防商户泄漏规则 |

### 4.3 要废弃的代码

- [backend/state_machine.py](backend/state_machine.py) 要么启用要么删；**推荐启用**（直接用 transitions）
- [backend/handlers/](backend/handlers/) 废弃；合进 FSM phase 里
- [backend/proxy/](backend/proxy/) 废弃（TLS MITM 已证伪不封号）
- [backend/recognition/llm_vision.py](backend/recognition/llm_vision.py) 降级为"兜底识别"，不作为主路径（慢且贵）

---

## 五、完整实施计划（6 大阶段 · 26 个任务）

> **原则**：数据和代码并行；每阶段结束都能产出可跑东西，不是憋大招。

### 🟢 阶段 0：观测先行 + 基础设施（3-5 天）

**目的**：所有后续优化都要能"量化对比"，否则不知道是否真的变好了。

| # | 任务 | 文件 | 工期 |
|---|---|---|---|
| 0.1 | 结构化性能日志 —— 在 screenshot / OCR / template_match / click / phase 加 `time.perf_counter()` 耗时记录，输出到 `logs/YYYYMMDD/metrics.jsonl` | 新建 `backend/automation/metrics.py` | 0.5 天 |
| 0.2 | 黄金回归测试集工具 —— 把当前日志里的好帧扒出来标注，写 `tools/golden_runner.py` | `fixtures/golden_set/`, `tools/golden_runner.py` | 1 天 |
| 0.3 | 健康度仪表盘 API —— `/api/health` 暴露：截图延迟 P50/P99、OCR 延迟、各 phase 平均耗时 | `backend/api.py` | 0.5 天 |
| 0.4 | ROI YAML 提取 —— grep 现有代码里的 (x,y,w,h) 数字，整合到 `config/roi.yaml` | `config/roi.yaml` | 0.5 天 |

**交付物**：能看到"当前 OCR 实测 1.8s"这种具体数据；有回归测试基线。

---

### 🟡 阶段 1：控制流升级（FSM）+ 进局 phase（5-7 天）

**目的**：把事件循环替换成真状态机；补完缺失的 phase。

| # | 任务 | 文件 | 工期 |
|---|---|---|---|
| 1.1 | 启用 transitions 状态机 —— 把 [runner_service.py:391-600](backend/runner_service.py) 改用 [state_machine.py](backend/state_machine.py)，保留恢复三级 | `runner_service.py` + `state_machine.py` | 1.5 天 |
| 1.2 | 新增 `phase_enter_game` —— 点匹配 → 等加载 → 进局 | `single_runner.py` | 1 天 |
| 1.3 | 新增 `phase_in_match` + `phase_settlement` —— 对局中基础交互 + 结算返回大厅 | `single_runner.py` | 2 天 |
| 1.4 | Phase 转移钩子 —— 每次 transition 自动发 WebSocket event 到前端，显示当前 phase 时间线 | `runner_service.py` | 0.5 天 |
| 1.5 | phase 超时统一配置 —— `config/phase_timeouts.yaml` | `config/phase_timeouts.yaml` | 0.5 天 |

**交付物**：完整闭环（大厅→匹配→进局→对局→结算→回大厅），6 开连续跑 1 小时不手动干预。

---

### 🟡 阶段 2：OCR / 截图 / 点击事务化（7-10 天）

**目的**：把最大的速度/稳定瓶颈解了。

| # | 任务 | 文件 | 工期 |
|---|---|---|---|
| 2.1 | `frame_id` + 截图带元数据 —— 每帧带 `frame_id, timestamp, phash, width, height` | `adb_lite.py` | 0.5 天 |
| 2.2 | Async OCR 调度器 —— worker pool（默认 4），结果绑 frame_id，过期丢弃 | 新建 `backend/recognition/ocr_scheduler.py` | 2 天 |
| 2.3 | 模板 phase 分组 —— 按 phase 只加载需要的模板，减少每帧扫描量 | `screen_matcher.py` + `config/templates_by_phase.yaml` | 1 天 |
| 2.4 | 点击事务框架 —— `ClickTx(target, pre_check, post_check)` 封装，闭环验证 | 新建 `backend/automation/click_tx.py` | 1.5 天 |
| 2.5 | 坐标映射显式化 —— 点击时自动从"归一化 1280×720"映射到设备真实分辨率 | `adb_lite.py` | 0.5 天 |
| 2.6 | ROI 动态加载 —— 从 `config/roi.yaml` 读，不再硬编码 | 各 phase 代码 | 1 天 |
| 2.7 | 弹窗清理重构 —— 用 ClickTx + ROI YAML + phase 感知，去掉盲点中央 | `ocr_dismisser.py` | 1.5 天 |
| 2.8 | 跑黄金集回归 —— 每天 CI 跑一次，看指标 | `tools/golden_runner.py` | 0.5 天 |

**交付物**：OCR 平均 ≤300ms，点击误触率 ≤1%，清弹窗成功率 ≥95%。

---

### 🔴 阶段 3：CaptureService（MediaProjection）截图升级（5-10 天）

**目的**：UE4 场景下截图终于可靠；为同屏刷新率留空间。

| # | 任务 | 文件 | 工期 |
|---|---|---|---|
| 3.1 | vpn-app 里加 CapturePermissionActivity + CaptureService（前台服务） | `vpn-app/app/src/main/java/.../capture/` | 1.5 天 |
| 3.2 | MediaCodec 输出 H.264 stream 到 `localabstract:fmcapture_<index>` socket | 同上 | 1 天 |
| 3.3 | Python 侧 MediaCodec 解码器 —— 用 av（PyAV）或 ffmpeg | 新建 `backend/automation/capture_stream.py` | 2 天 |
| 3.4 | MediaProjection 授权自动化 —— UIAutomator dump + 定位"立即开始" | `tools/grant_capture_permission.py` | 1-2 天 |
| 3.5 | 截图后端切换开关 —— `settings.capture_backend = media_projection / minicap / screencap` | `config.py` | 0.5 天 |
| 3.6 | 6 实例并发压测 | 压测脚本 | 1-2 天 |

**交付物**：UE4 场景连续 2 小时不丢帧，6 开并发截图总 CPU < 30%。

---

### 🔴 阶段 4：三信号融合（memscan + gameproxy 事件）（6-8 天）

**目的**：让大部分决策脱离视觉，速度 × 10、准确率 → 100%。

| # | 任务 | 文件 | 工期 |
|---|---|---|---|
| 4.1 | host_memscan 扩展 —— 加 `get_game_phase / get_match_state / get_team_ready_count / get_battle_server` | `tools/host_memscan.py` | 1-2 天 |
| 4.2 | 内存 offset YAML + 运维工具 —— 抓取 + 对比 + 生成 `config/mem_offsets.yaml` | 新建 `tools/memoffset_finder.py` | 2 天 |
| 4.3 | gameproxy 事件总线 —— 识别 match_found / battle_connect / match_end 等，WS 推 | `gameproxy-go/events.go` | 2 天 |
| 4.4 | Python SignalFusion 组件 —— 融合 vision/memory/network 三路，硬信号优先 | 新建 `backend/automation/signal_fusion.py` | 1.5 天 |
| 4.5 | 进局 / 同局检测用 SignalFusion —— `phase_enter_game` + 同局判定走 memory+network | `single_runner.py` | 1 天 |
| 4.6 | 队伍状态用 memscan —— 替换 OCR 读队员名字 / 点准备检测 | `single_runner.py`（相关 phase） | 1 天 |

**交付物**：匹配成功判定延迟从 2s+ 降到 50ms；同局检测误判率 ≤1%。

---

### 🟢 阶段 5：48h 稳定性 + 恢复 + 健康监控（5-7 天）

**目的**：单实例异常不拖垮其他实例；24h/48h 跑通。

| # | 任务 | 文件 | 工期 |
|---|---|---|---|
| 5.1 | ResourceGovernor —— 每分钟检查 CPU/内存/磁盘，超阈值降频或重启实例 | 新建 `backend/automation/resource_governor.py` | 1.5 天 |
| 5.2 | 闪退自动检测 —— 监听 logcat `FATAL EXCEPTION` + 进程 PID 消失 | 新建 `backend/automation/crash_detector.py` | 1 天 |
| 5.3 | 模拟器卡死检测 —— phase 超时 + 截图 pHash 连续 60s 不变 | `runner_service.py` | 1 天 |
| 5.4 | 分级恢复扩展 —— 加"重启模拟器"级（现在只到"重启游戏"） | `runner_service.py` | 1 天 |
| 5.5 | 健康摘要小时报 —— 每小时日志汇总：截图延迟、OCR 延迟、恢复次数 | `backend/automation/metrics.py` | 0.5 天 |
| 5.6 | 压测 —— 6 开 2h → 12h → 24h → 48h | 运维 | 1-2 天 |

**交付物**：6 开连续 48h 无人工干预，成功率 ≥95%。

---

### 🟣 阶段 6：本地化 + DTW 授权 + 加壳（最后做）

**目的**：省服务器带宽；IP 保护；可以"发"给商户了。

> **前提**：阶段 0-5 都走通了，再做这个阶段。详见 [FM_DTW_AUTH_PLAN.md](FM_DTW_AUTH_PLAN.md) 里的完整方案。

| # | 任务 | 文件 | 工期 |
|---|---|---|---|
| 6.1 | gameproxy Windows 交叉编译 + 本机跑通 —— LDPlayer → 127.0.0.1:9900 | `gameproxy-go/` | 0.5 天 |
| 6.2 | DTW 后端新增 `internal/fm/` —— migration + activate/refresh/revoke API | `DTW/backend/internal/fm/` | 1.5 天 |
| 6.3 | DTW 商户后台 FM 管理页（生成 api_key / 设备列表 / 吊销） | `DTW/web/src/dashboard/pages/FMAccessPage.tsx` | 0.5 天 |
| 6.4 | gameproxy 端规则动态加载 —— 启动时拉加密 blob，内存解密 | `gameproxy-go/auth.go`, `rules_dynamic.go` | 1 天 |
| 6.5 | HW 指纹 + DPAPI 本地加密保存 api_key | `gameproxy-go/config_client.go` | 0.5 天 |
| 6.6 | 规则硬编码替换 —— `patchWPEAdvanced` 从 `activeRules` 读 | `gameproxy-go/relay.go` | 0.5 天 |
| 6.7 | garble + UPX 打包脚本 | `gameproxy-go/scripts/build_protected.sh` | 0.5 天 |
| 6.8 | NSIS Windows 安装包（auto-start 服务） | `gameproxy-go/installer/` | 0.5 天 |
| 6.9 | 灰度 1-2 商户 1 周 → 全量 80 台 | 运维 | 1 周 |

**交付物**：80 台设备完全本地跑；服务器带宽 0；商户从 DTW 后台管理。

---

## 五.B 工期汇总

| 阶段 | 代码工期 | 数据工期 | 关键前置 |
|---|---|---|---|
| 阶段 0 | 3-5 天 | — | 无 |
| 阶段 1 | 5-7 天 | — | 阶段 0 完成 |
| 阶段 2 | 7-10 天 | ROI YAML（0.5-1 天） | 阶段 0 |
| 阶段 3 | 5-10 天 | — | 阶段 2（要 frame_id） |
| 阶段 4 | 6-8 天 | 内存 offset（2-3 天）+ 网络事件签名（2-3 天） | 阶段 1（要 FSM） |
| 阶段 5 | 5-7 天 | — | 阶段 1-4 |
| 阶段 6 | 5-7 天 | — | 阶段 5 跑通 48h |

**总工期（单人全职）**：
- 最快：**~35 天**（5 周）
- 现实：**~50 天**（7-8 周）含 debug 和返工
- 模板 + ROI + 内存 offset + 网络签名 数据采集可以并行，**不占主路径工期**

---

## 六、验收标准

### 阶段性指标

| 阶段 | 指标 | 目标值 |
|---|---|---|
| 0 完成 | metrics.jsonl 日志覆盖率 | 100%（每个 phase 转移都有） |
| 1 完成 | 完整闭环（大厅→进局→结算→回大厅） | 6 开连续 1 小时成功率 ≥80% |
| 2 完成 | OCR 平均延迟 | ≤300ms |
| 2 完成 | 点击误触率 | ≤1% |
| 3 完成 | MediaProjection 6 开 CPU | 总占用 ≤30% |
| 3 完成 | UE4 场景帧稳定性 | 2 小时 0 丢帧 |
| 4 完成 | 匹配成功判定延迟 | ≤50ms（从现在 2s+） |
| 4 完成 | 同局检测误判率 | ≤1% |
| 5 完成 | 6 开连续 48h 无人工干预 | 完成率 ≥95% |
| 6 完成 | 服务器带宽 | 降到 ≤100MB/月（只剩 DTW auth） |
| 6 完成 | 反编译难度 | 业余 RE 工具扒不出规则字节 |

### 三维综合指标

| 维度 | 验收标准 |
|---|---|
| 稳定性 | 48h 单实例崩溃 ≤1 次；所有崩溃能从日志 10 秒内定位原因 |
| 速度 | phase 平均延迟：大厅 2s、弹窗清理 5s、匹配确认 <1s、进局检测 <500ms |
| 性能 | 单 PC 6 开 CPU 峰值 <70%，内存 <4GB；80 台月服务器带宽 <100MB |

---

## 七、风险与对策

| 风险 | 概率 | 影响 | 对策 |
|---|---|---|---|
| MediaProjection 自动授权在 LDPlayer 失效 | 中 | 阶段 3 卡住 | 回退到 minicap，只在 UE4 场景切换 |
| 6 开 H.264 解码 CPU 过载 | 中 | 性能不达标 | 降分辨率到 960×540，或 GPU 加速（QSV） |
| 内存 offset 游戏更新失效 | 高 | 阶段 4 部分失效 | offset YAML 外部化，每次游戏更新跑 `memoffset_finder.py` 重扫 |
| gameproxy 事件签名不稳定 | 中 | 网络信号失效 | Fallback 到 `/proc/net/tcp` + proxy 日志 |
| ACE 升级检测内存读 | 低 | memscan 失效 | memory 里已验证宿主机 ReadProcessMemory 不触发；若真失效，走加固方案（Rootkit / Hypervisor，不推荐） |
| DTW 后端宕机导致 80 台全挂 | 低 | 业务中断 | session 12h 缓存 + 多机备份 |
| 规则被商户破解 | 中 | 规则外泄 | garble + 规则热更新机制（发现外泄立即换 pattern） |

---

## 附录：相关文档链接

- 细分方案
  - [WPE_ADV_RULES.md](gameproxy-go/WPE_ADV_RULES.md) — 两条 WPE 规则 + 更新流程
  - [FM_DTW_AUTH_PLAN.md](FM_DTW_AUTH_PLAN.md) — DTW 授权集成详细方案
  - [match_detection_plan.md](docs/match_detection_plan.md) — 同局检测原方案
- 历史方案（已被本计划替代）
  - [docs/STABLE_FAST_ARCHITECTURE_PLAN.md](docs/STABLE_FAST_ARCHITECTURE_PLAN.md)
  - [docs/OPTIMIZED_ARCHITECTURE_PLAN.md](docs/OPTIMIZED_ARCHITECTURE_PLAN.md)
  - [docs/FULL_FLOW.md](docs/FULL_FLOW.md)
- Memory 相关
  - `memory/防封认知修正_2026_04_20.md`
  - `memory/wpe_adv_breakthrough_2026_04_20.md`
  - `memory/memscan_findings.md`

---

**状态**：规划完成，阶段 0 未开始
**下一步**：开始阶段 0 的任务 0.1（性能日志）—— 1 天能见到数据
