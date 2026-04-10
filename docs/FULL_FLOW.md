# 完整业务流程与系统架构文档

> 本文档记录整个自动化流程的每一步、每种异常、每个需要识别的画面，以及 48 小时稳定运行的架构设计。
> 截图状态用 ✅ 已有 / ⏳ 待截图 / ❌ 只能遇到才能截 标注。
> **最后更新**: 2026-04-10，基于实机测试验证 + 架构设计

---

## 零、系统架构总览

### 目标

两组各 5 人（3 机器人 + 2 真人）同时匹配同一对局，匹配成功后机器人断网退出，真人留在对局中。**循环运行 48 小时以上不中断**。

### 四层架构

```
┌─────────────────────────────────────────────────────────────────┐
│  Layer 0: Supervisor（看门狗）                                    │
│  - 监控 Coordinator 进程存活                                      │
│  - 崩溃自动重启（带指数退避）                                       │
│  - 内存/CPU 监控 → 超阈值强制重启                                   │
│  - 日志轮转（100MB/文件，保留最近5个）                               │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│  Layer 1: Coordinator（协调器 / 大脑）                             │
│  - 管理 2 组 × 3 实例 = 6 个 InstanceAgent                       │
│  - 口令码传递: Captain → Coordinator → Members                    │
│  - 匹配同步: 两组 Captain ±500ms 内同时点击开始                     │
│  - 校验同步: 等待双方验证对手 → 统一 SUCCESS / ABORT                │
│  - 断网退出: 全部断网 → 等回大厅 → 恢复网络                         │
│  - 异常升级: 单实例异常 → 组暂停 → 全局暂停                         │
│  - 心跳监控: 每 5s 检查每个 Agent 是否存活                          │
│  - 状态持久化: SQLite 记录会话状态，支持崩溃恢复                     │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│  Layer 2: InstanceAgent（实例代理 / 执行者）× 6                    │
│  - 状态机驱动: 每个阶段 = 一个 Phase                               │
│  - 阶段超时 + 重试 + 指数退避                                      │
│  - 弹窗中断处理: 任何阶段都可能被弹窗打断                            │
│  - ADB 健康检查: 截图失败 N 次 → 重连 ADB                          │
│  - 卡死检测: 连续截图 hash 相同 > 30s → 强制重启游戏                 │
│  - 内存保护: 每 100 轮强制 GC                                      │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│  Layer 3: Recognition（识别层 / 眼睛）                             │
│  - 模板匹配 ScreenMatcher (~20ms) → 快速路径                      │
│  - RapidOCR 中文识别 (~200ms) → 慢速路径                          │
│  - 遮罩检测 _has_overlay() (~5ms) → 弹窗存在性判断                  │
│  - 形状检测 _find_x_shape() (~10ms) → 兜底 X 按钮                 │
│  - 【不使用】EasyOCR (2-5s 太慢) / PaddleOCR (API不兼容)           │
│  - 【不使用】颜色检测 (误点率高，曾导致退出游戏)                      │
│  - 【不使用】LLM Vision (延迟太高，不适合实时循环)                    │
└─────────────────────────────────────────────────────────────────┘
```

### 单次循环生命周期

```
启动加速器 → 启动游戏 → 弹窗清理 → 到达大厅
  → 地图设置(队长) → 创建队伍(队长) / 加入队伍(队员)
  → 推送口令给真人 → 等待满员(5/5) → 校验玩家身份
  → 全员准备 → 两组同时匹配 → 校验对手
  → 匹配成功 → 全部断网 → 等回大厅 → 恢复网络
  → [循环: 重新创建队伍 → 等待真人回来 → ...]
```

### 关键技术决策（实测验证）

| 决策 | 选择 | 原因 |
|------|------|------|
| OCR 引擎 | RapidOCR | ~200ms/帧，中文准确率高。PaddleOCR 3.4 API 不兼容(cls/show_log参数被移除)，EasyOCR 2-5s 太慢 |
| 弹窗检测 | 遮罩亮度分析 | 四角暗(<50) + 中央亮(差值>40) = 有弹窗。比模板穷举更通用 |
| X 按钮查找 | 模板优先 → OCR → 形状检测 | 模板 20ms 最快；OCR 能找文字按钮；形状检测(Canny+轮廓)是兜底 |
| 退出游戏判定 | "CDN节点第" / "六花官方通知" | "六花加速器"不能用——游戏内底部也显示"六花加速器[已连接]" |
| 截图归一化 | 1280×720 灰度 | 模板匹配标准分辨率，多尺度 [0.9, 0.95, 1.0, 1.05, 1.1] |
| 点击抖动 | ±3px 随机偏移 | 模拟真人操作，避免反作弊检测 |
| 返回键策略 | 仅在加速器页面使用 | 游戏内按返回键会弹"退出游戏"对话框 |

---

## 一、环境概述

- 雷电模拟器 LDPlayer 9，6 个实例多开（当前 3 个运行中）
- 分 2 组：A 组（实例 0/1/2）、B 组（实例 3/4/5）
- 每组 3 个机器人账号：1 个队长 + 2 个队员
- 每组还会有 **2 个真人玩家** 手动加入组队（共 5 人一队）
- 目标：两组同时匹配，匹配到同一对局后断网退出，循环

### 环境实测信息

| 项目 | 值 |
|------|-----|
| LDPlayer 9 路径 | `D:\leidian\LDPlayer9\` |
| ADB | `D:\leidian\LDPlayer9\adb.exe` |
| ldconsole | `D:\leidian\LDPlayer9\ldconsole.exe` |
| ADB 设备 | emulator-5554(实例0), emulator-5556(实例1), emulator-5558(实例2) |
| 游戏包名 | `com.tencent.tmgp.pubgmhd` |
| 加速器包名 | `com.tencent.lhjsqxfb`（六花加速器，已确认） |
| 分辨率 | 1280×720（模板匹配归一化分辨率） |
| 远程调试 | 直连 LAN `192.168.0.102:9100`（不用 Cloudflare 隧道，会 524 超时） |

### 依赖

```
opencv-python>=4.8.0    # 图像处理、模板匹配
numpy>=1.24.0           # 数组操作
rapidocr                # 中文 OCR（替代 PaddleOCR/EasyOCR）
```

> **注意**: requirements.txt 中仍写着 paddlepaddle/paddleocr，需更新为 rapidocr。

---

## 二、主流程（正常路径）

### 阶段 0：启动加速器（前置步骤）

| 步骤 | 操作 | 识别目标 | 截图 |
|------|------|---------|------|
| 0.1 | `monkey -p com.tencent.lhjsqxfb` 启动加速器 | 加速器页面出现 | ✅ liuhuaguanbi-2.png |
| 0.2 | 检测加速器状态 | ▶ 播放按钮 = 未连接 / ⏸ 暂停按钮 = 已连接 | ✅ |
| 0.3 | 未连接 → 点击 ▶ → 等待连接 | 底部绿色"六花加速器已连接" | ✅ liuhuakaiqi-3.png |
| 0.4 | 按 Home 键回桌面 | — | — |

**识别策略**: 纯模板匹配。`accelerator_play.png` / `accelerator_pause.png`。

**实测发现的问题与解决**:
- **加速器公告弹窗**: 有时启动后弹出公告，挡住 play 按钮。模板匹配 play 按钮穿透弹窗导致反复点击无效。
- **解决方案**: 连续点击 play 3 次仍未连接 → 按 `KEYCODE_BACK` 清除弹窗 → 重置计数继续。
- **超时**: 15 次尝试 × 3s = 最多 45s。

```python
# 伪代码
play_click_count = 0
for attempt in range(15):
    status = matcher.is_accelerator_connected(screenshot)
    if status == True: return SUCCESS  # 已连接
    if status == False:
        play_click_count += 1
        if play_click_count >= 3:
            key_event(KEYCODE_BACK)  # 清弹窗
            play_click_count = 0
        else:
            tap(play_button)
    if status == None:
        key_event(KEYCODE_BACK)  # 不在主界面
    sleep(3)
```

### 阶段 1：启动游戏

| 步骤 | 操作 | 识别目标 | 截图 |
|------|------|---------|------|
| 1.1 | `monkey -p com.tencent.tmgp.pubgmhd` 启动游戏 | — | — |
| 1.2 | 等待 8s 初始加载 | — | — |
| 1.3 | 循环 OCR 检测加载状态（最多 90s） | 关键词触发 | — |
| 1.4 | 检测到"开始游戏"/"公告"/"活动"等 → 加载完成 | — | — |

**实测关键发现**:
- **不能用模板匹配判断加载完成**: `close_x_signin` 等模板在加载画面上会误匹配（0.85+ 的假阳性），导致还在加载就以为有弹窗。
- **正确方案: OCR 检测关键词**: 使用 `ocr_dismisser.ocr_screen()` 扫描全屏文字，出现以下任一关键词说明加载完毕：
  - `"开始游戏"` — 已到大厅
  - `"公告"` / `"更新公告"` — 公告弹窗已弹出
  - `"活动"` / `"立即前往"` — 活动弹窗已弹出
- **超时**: 45 次 × 2s = 90s

### 阶段 2：登录检测

| 步骤 | 操作 | 识别目标 | 截图 |
|------|------|---------|------|
| 2.1 | 正常：自动登录，直接跳过 | 检测到大厅 = 登录成功 | ✅ dating-8.png |
| 2.2 | 异常：登录页停留 >15s | "微信登录"/"QQ登录" 按钮 | ✅ denglu-6.png |

**说明**：
- 一般自动登录，不需要输入账号密码
- 登录页背景会变，不能依赖背景识别，用按钮文字做锚点
- 自动登录失败需要人工介入（扫码登录）

**实测**: 阶段 2 和阶段 3 在代码中合并处理。弹窗清理的状态机会自动识别 LOGIN 状态并等待。

### 阶段 3：关闭弹窗（最复杂的环节）⚠️

进入大厅后，通常会**连续弹出 6~7 个弹窗**（最多观测到 7 个），必须逐个关闭。
弹窗类型和样式**每个版本更新/每个赛季都会变**。

#### 已知弹窗类型清单（基于实测截图）

**类型A: 有 X 按钮的（右上角或右侧）**

| 编号 | 弹窗内容 | X 位置 | 截图 |
|------|---------|-------|------|
| A1 | 开局领奖励（经典模式/地铁逢生/绿洲启元/团队竞技） | 右上角（小号X） | ✅ youxihuodong-7.png |
| A2 | 回归8天签到 | 右上角 | ✅ youxihuodong-7.1.png |
| A3 | 新春共创赛（活动推广） | 右上角 | ✅ youxihuodong-7.1.1.png |
| A4 | 回归任务/奖励面板 | 右上角 | ✅ youxihuodong-7.2.png |
| A5 | 新玩法速览（轮播） | 右上角 | ✅ youxihuodong-7.2.1.png |
| A6 | 老六日活动/资讯 | 右侧 | ✅ youxihuodong-7.2.3.png |

**类型B: "点击屏幕继续"（赛季结算连续链）**

| 编号 | 弹窗内容 | 关闭方式 | 截图 |
|------|---------|---------|------|
| B1 | SS37段位结算 | 点击屏幕任意位置 | ✅ youxihuodong-7.1.2.png |
| B2 | SS38起始段位 | 点击屏幕任意位置 | ✅ youxihuodong-7.1.3.png |
| B3 | SS38段位奖励（两人跳舞） | 点击屏幕任意位置 | ✅ youxihuodong-7.1.4.png |
| B4 | 解锁赛季手册 | 点击继续（底部文字） | ✅ youxihuodong-7.1.5.png |

> B1-B4 是**连续链**，一般只有新赛季/长时间未登录才会出现。
> **实测**: "SS38段位奖励 点击屏幕继续"无 X 按钮也无关闭文字，必须通过 OCR 识别"点击屏幕"/"点击继续"并点击屏幕中央(640,400)。

**类型C: 有特定按钮的**

| 编号 | 弹窗内容 | 按钮文字 | 截图 |
|------|---------|---------|------|
| C1 | 欢迎回归/见面礼 | "领取见面礼" | ✅ jianmianli.png |
| C2 | 个人资料展示提示 | "确定" | ✅ yinsi.png |
| C3 | 游戏许可/隐私协议 | "同意" | ✅ yinsi2.png |
| C4 | 实名注册确认 | "确定" | ✅ (实测遇到) |

**类型D: 有"不再弹出"选项的**

| 编号 | 弹窗内容 | 复选框文字 | 截图 |
|------|---------|---------|------|
| D1 | 找队友组队 | "今日内不再弹出" | ✅ zhoaduiyou.png |
| D2 | 某些活动推广 | "不再提醒" / "不再弹出" | ✅ youxihuodong-7.1.1.png |

**类型E: 纯文字关闭的（无 X 按钮）**

| 编号 | 弹窗内容 | 关闭文字 | 截图 |
|------|---------|---------|------|
| E1 | 摸金杯活动 | "关闭" (底部小字) | ✅ (实测遇到) |

#### 弹窗清理状态机（实测验证版）

**核心思路**（OcrDismisser 状态机）：

```
每轮先判断当前状态:
  LOBBY    → 成功退出
  LEFT_GAME → 失败（加速器界面）
  LOGIN    → 等待自动登录
  LOADING  → 等待加载
  POPUP    → 找关闭目标并点击
  UNKNOWN  → 找关闭目标或点击中央
```

**状态检测优先级**:
1. 模板匹配大厅 `lobby_start_btn` / `lobby_start_game` (20ms) → 再检查是否有遮罩
2. 遮罩检测 `_has_overlay()` (5ms) → 四角暗 + 中央亮 = POPUP
3. OCR 全屏扫描 (200ms) → 关键词匹配确定状态

**关闭目标查找三级策略**:

```
级别1: 模板匹配 X 按钮 (~20ms)
  → close_x_announce, close_x_activity, close_x_white_big,
    close_x_dialog, close_x_gold, close_x_newplay, close_x_return,
    close_x_signin (共 8 种 X 样式)

级别2: OCR 文字识别 (~200ms)
  → 优先勾选: "今日内不再弹出", "不再提醒", "不再弹出"
  → 关闭类: "关闭", "×"
  → 确认类: "确定", "确认", "知道了", "同意", "暂不", "跳过", "不需要"
  → 屏幕类: "点击屏幕继续", "点击屏幕", "点击继续" → 点(640,400)

级别3: 形状检测 (~10ms)
  → 右上角 1/3 区域 Canny 边缘检测 + 轮廓分析
  → 找 20×20 ~ 50×50 的方形轮廓，宽高比 0.5~2.0
  → 中心比周围暗 > 20 → 判定为 X 按钮
```

**双速循环**:
- 模板 X 命中 → 快速路径: 点击后只等 **0.5s**
- OCR 路径 → 正常路径: 点击后等 **0.8s**
- 什么都没找到 → 卡住路径: stuck_count >= 2 时点击屏幕中央

**复选框特殊处理**:
- 点击"今日内不再弹出"后，**立即再截图找 X 按钮**并点击
- 避免无限循环（之前的 bug：只点复选框不关弹窗）

**实测结果**: 一次完整弹窗清理 7 个弹窗用了 12 轮，约 15 秒。

```
R1:  close_x_announce 模板命中 → 0.5s
R2:  OCR "确定"(实名注册) → 0.8s
R3:  loading 等待 → 2s
R4:  loading 等待 → 2s
R5:  close_x_white_big 模板命中 → 0.5s
R6:  close_x_white_big 模板命中 → 0.5s
R7:  close_x_activity 模板命中 → 0.5s
R8:  close_x_activity 模板命中 → 0.5s
R9:  loading 等待 → 2s
R10: OCR "关闭"(摸金杯) → 0.8s
R11: loading 等待 → 2s
R12: OCR 检测到"开始游戏" → LOBBY ✓
```

### 阶段 4：组队 — 队长操作

| 步骤 | 操作 | 识别目标 | 截图 |
|------|------|---------|------|
| 4.1 | 点击左侧"组队"文字 (35, 385) | 好友/组队面板打开 | ✅ zuduima-9.0.png |
| 4.2 | 模板匹配点击"组队码" tab | `btn_team_code_tab` | ✅ zudui-9.png |
| 4.3 | 模板匹配点击"分享组队口令码" | `btn_share_team_code` | ✅ zudui-9.png |
| 4.4 | 口令码自动复制到剪贴板 | — | — |
| 4.5 | 读取剪贴板获取口令码 | — | — |
| 4.6 | Coordinator 分发口令码给队员 | — | — |

**口令码格式示例**:
```
【微信和QQ】上线！和平精英开黑就等你了！（整段复制后打开邀请组队列表或在跨平台组队功能手动粘贴加入队伍）G0EV8ECDhttps://agp.qq.com/lq/lq.htm?1.cGdtOjEwNA==&tk=G0EV8ECD ZH1264 ¥4915
```

**剪贴板读取方案（待解决）**:
- 模拟器剪贴板 ≠ Windows 剪贴板，`adb shell am broadcast -a clipper.get` 需要安装 clipper 服务
- 备选方案: 雷电模拟器共享剪贴板功能（需确认）
- 备选方案: Windows 端 PowerShell 读剪贴板 `Get-Clipboard`

### 阶段 5：组队 — 队员加入

| 步骤 | 操作 | 识别目标 | 截图 |
|------|------|---------|------|
| 5.0 | Coordinator 写口令码到队员剪贴板 | — | — |
| 5.1a | **快捷路径**: 剪贴板有口令码 → 自动弹出提示 → 点击"加入" | `btn_join` 模板 | ✅ zuduima-9.1.png |
| 5.1b | **手动路径**: 点组队 → 组队码 → 粘贴 → 加入队伍 | 多步模板匹配 | ✅ |
| 5.2 | 验证已加入队伍 | 回到大厅，左下角有组队人数 | ⏳ |

### 阶段 6：赛前设置（队长）

| 步骤 | 操作 | 识别目标 | 截图 |
|------|------|---------|------|
| 6.0 | 关闭组队面板回到大厅 | 大厅主界面 | ✅ |
| 6.1 | 点击地图区域 (100, 105) | 地图选择面板打开 | ✅ ditu-10.png |
| 6.2 | 模板匹配"团队竞技" | `btn_team_battle_entry` | ✅ moshixuanze-11.png |
| 6.3 | 模板匹配"狙击团竞" | `card_sniper_team` | ✅ dituxuanze-12.png |
| 6.4 | 模板匹配"确定" | `btn_confirm_map` | ✅ |
| 6.5 | 回到大厅 | — | — |

**地图选择界面结构**（moshixuanze-11.png）:
- **左侧**: 模式分类：和平精英 / 绿洲启元 / 经典模式 / 创意工坊 / 限时模式 / 团队竞技 / 地铁逢生
- **右侧**: 地图网格 2×3：军备团竞 / 经典团竞 / 迷你战争 / 狙击团竞 / 轮换团竞 / 突变团竞
- **右上角**: 愿意补位开关（必须关闭）
- **底部**: 确定按钮

### 阶段 7：推送口令码给真人

| 步骤 | 操作 | 说明 |
|------|------|------|
| 7.1 | 把口令码推送给真人玩家 | 推送方式待定（微信/QQ/网页/API） |

### 阶段 8：等待真人加入组队

| 步骤 | 操作 | 识别目标 |
|------|------|---------|
| 8.1 | 循环检测组队人数 | 队伍满员(5人) |

**检测方式**:
- OCR 读队伍人数文字（如 "4/5" "5/5"）
- 或检测"空位"占位图标
- 每 3s 检测一次，超时 300s 告警

### 阶段 9：校验玩家身份

| 步骤 | 操作 | 识别目标 |
|------|------|---------|
| 9.1 | 点击队员头像 → 打开主页 | 主页界面 |
| 9.2 | OCR 读取游戏编号 | 编号文字 |
| 9.3 | 比对是否为预设真人 ID | — |
| 9.4 | 不匹配 → 踢出 | — |

### 阶段 10：准备检查

| 步骤 | 操作 | 识别目标 |
|------|------|---------|
| 10.1 | 检查所有队员是否"准备" | 准备状态标识 |
| 10.2 | 未准备 > 30s → 提醒真人 | — |
| 10.3 | 全员准备完成 | — |

### 阶段 11：同时匹配（核心同步点）

| 步骤 | 操作 | 说明 |
|------|------|------|
| 11.1 | Coordinator 确认两组 Captain 都准备完成 | — |
| 11.2 | 发送同步信号 | — |
| 11.3 | 两组 Captain **同时**点击"开始游戏" | 时间差 < 500ms |
| 11.4 | 进入匹配等待（计时 1 2 3 4 5 6...） | — |

**同步机制**:
```python
await asyncio.gather(
    group_a_captain.tap_start_button(),
    group_b_captain.tap_start_button(),
)
```

### 阶段 12：匹配结果判定

#### 12a. 两组匹配到同一对局 ✅

1. 两组几乎同时进入加载（时间差 < 3s）
2. OCR 读对手第一个名字 = 另一组某成员名字
3. 确认匹配成功 → **全部 6 实例断网**
4. 断网: `adb shell svc wifi disable && svc data disable`
5. 游戏自动退出回大厅

#### 12b. 只有一组匹配到 ❌

1. A 组进入加载，B 组还在匹配
2. B 组立即取消匹配
3. A 组断网退出
4. 等两组都回大厅 → 重来

#### 12c. 都匹配到但不是同一局 ❌

1. OCR 对手名字不匹配
2. 全部断网退出 → 回大厅 → 重来

### 阶段 13：断网退出后恢复

| 步骤 | 操作 | 超时 |
|------|------|------|
| 13.1 | 等待游戏回到大厅 | 15s |
| 13.2 | 恢复网络: `svc wifi enable && svc data enable` | — |
| 13.3 | 如果 15s 未回大厅 → force-stop → 重启游戏 | — |

### 阶段 14：循环

1. 队长重新创建队伍 → 生成新口令码
2. 队员加入
3. 等待真人完成上一局后回来
4. 回到阶段 8

---

## 三、异常情况清单与恢复策略

### 恢复策略总表

| 异常 | 检测方式 | 恢复策略 | 最大重试 | 升级 |
|------|---------|---------|---------|------|
| E1 游戏崩溃/闪退 | 安卓桌面检测 / 进程不存在 | 重启游戏(从加速器开始) | 3 | 重启模拟器 |
| E2 画面卡死 | 连续截图 hash 相同 > 30s | force-stop → 重启游戏 | 3 | 重启模拟器 |
| E3 被挤下线 | OCR "账号在其他设备登录" | 告警 → 暂停该账号 | 0 | 人工介入 |
| E4 封号 | OCR "账号已被封禁" | 永久停止该账号 | 0 | 人工换号 |
| E5 真人没准备 | 准备状态检测 > 30s | 推送提醒 | — | 超时踢出 |
| E6 真人掉线 | "掉线"标识 | 等重连 60s | 1 | 踢出重来 |
| E7 匹配超时 | 计时 > 60s | 双方取消 → 重新匹配 | ∞ | — |
| E8 一组匹配太慢 | B 组暂停晚了也匹配到 | 全部断网退出 → 重来 | — | — |
| E9 陌生人混入 | 玩家 ID 不在预设列表 | 踢出 → 等正确人加入 | — | — |
| E10 突发弹窗 | 遮罩检测 + OCR | 立即关闭 → 返回原流程 | 5 | 强制 tap 中央 |
| E11 网络异常提示 | OCR "网络异常"/"连接超时" | 点击重连 → 等待 | 3 | 重启游戏 |
| E12 服务器繁忙 | OCR "服务器繁忙" | 等 30s → 重试 | 5 | 换时段 |
| E13 禁赛 | OCR "禁赛 XX 分钟" | 记录时长 → 等待 | 0 | 告警换号 |
| E14 真人游戏中 | 无法加入组队 | 循环等待 | — | — |
| E15 断网后没回大厅 | 15s 无大厅检测 | 恢复网络 → force-stop → 重启 | 2 | — |
| **E16 ADB 连接断开** | 截图连续失败 5 次 | `adb reconnect` | 3 | 重启 ADB server |
| **E17 模拟器进程崩溃** | 检测不到模拟器窗口/进程 | ldconsole 重启实例 | 2 | 人工介入 |
| **E18 OCR 引擎异常** | 识别返回空 / 异常 | 重新初始化 OCR | 2 | 降级为纯模板 |
| **E19 内存溢出** | RSS > 阈值 | 强制 GC + 清理缓存 | — | 重启进程 |

> E16-E19 是 48 小时运行新增的必要检测项。

### E10 突发弹窗 — 核心中断机制（详细说明）

**这是最关键的 interrupt 机制。** 任何阶段执行任何步骤时，都要同时监听"有没有弹窗"。

实现方式：每次截图后，先过一遍弹窗检测，有弹窗先关掉再继续原流程。

```python
async def safe_action(self, action_fn):
    """带弹窗守卫的动作执行"""
    shot = await self.adb.screenshot()
    # 先检查弹窗
    if self.ocr_dismisser._has_overlay(shot):
        await self.ocr_dismisser.dismiss_all(self.adb, self.matcher)
        shot = await self.adb.screenshot()  # 重新截图
    # 再执行原动作
    return await action_fn(shot)
```

---

## 四、48 小时稳定运行设计

### 4.1 看门狗（Supervisor）

```python
class Supervisor:
    """进程级看门狗"""

    def run(self):
        while True:
            process = start_coordinator()
            exit_code = process.wait()

            if exit_code == 0:
                break  # 正常退出

            restart_count += 1
            backoff = min(60, 2 ** restart_count)  # 指数退避，最多60s
            log(f"Coordinator 崩溃 (code={exit_code}), {backoff}s 后重启")
            sleep(backoff)

            if restart_count > 10:
                alert("Coordinator 连续崩溃 10 次，需要人工介入")
                break
```

**实现选择**: Windows 上用 Python 父进程 + `subprocess.Popen`，或 NSSM 注册为 Windows 服务。

### 4.2 心跳与健康检查

```python
class InstanceAgent:
    async def heartbeat_loop(self):
        """每 5s 向 Coordinator 发送心跳"""
        while self.running:
            self.last_heartbeat = time.time()
            # 顺便做健康检查
            if not await self._check_adb_alive():
                await self._reconnect_adb()
            if self._same_screenshot_count > 6:  # 30s 无变化
                await self._handle_stuck()
            await asyncio.sleep(5)

class Coordinator:
    async def check_agent_health(self):
        """检查所有 Agent 心跳"""
        for agent in self.agents.values():
            if time.time() - agent.last_heartbeat > 15:
                log(f"Agent {agent.index} 心跳超时，尝试恢复")
                await self._recover_agent(agent)
```

### 4.3 ADB 连接保活

```python
async def _check_adb_alive(self) -> bool:
    """检测 ADB 连接是否存活"""
    try:
        shot = await asyncio.wait_for(self.adb.screenshot(), timeout=5)
        return shot is not None
    except asyncio.TimeoutError:
        return False

async def _reconnect_adb(self):
    """重连 ADB"""
    for attempt in range(3):
        subprocess.run([self.adb_path, "reconnect", self.serial])
        await asyncio.sleep(2)
        if await self._check_adb_alive():
            return True
    # 最后手段：重启 ADB server
    subprocess.run([self.adb_path, "kill-server"])
    await asyncio.sleep(1)
    subprocess.run([self.adb_path, "start-server"])
    await asyncio.sleep(3)
    return await self._check_adb_alive()
```

### 4.4 卡死检测

```python
import hashlib

class StuckDetector:
    def __init__(self, threshold_seconds=30):
        self.threshold = threshold_seconds
        self._last_hash = None
        self._same_since = None

    def check(self, screenshot: np.ndarray) -> bool:
        """返回 True = 卡死了"""
        # 缩小到 64x64 计算 hash，忽略微小变化
        small = cv2.resize(screenshot, (64, 64))
        h = hashlib.md5(small.tobytes()).hexdigest()

        if h == self._last_hash:
            if self._same_since is None:
                self._same_since = time.time()
            elif time.time() - self._same_since > self.threshold:
                return True
        else:
            self._last_hash = h
            self._same_since = None
        return False
```

### 4.5 内存保护

```python
import gc
import psutil

class MemoryGuard:
    MAX_RSS_MB = 2048  # 单进程最大 2GB

    def check_and_clean(self):
        rss = psutil.Process().memory_info().rss / 1024 / 1024
        if rss > self.MAX_RSS_MB * 0.8:
            gc.collect()
            logger.warning(f"内存警告: {rss:.0f}MB, 已触发 GC")
        if rss > self.MAX_RSS_MB:
            logger.critical(f"内存超限: {rss:.0f}MB, 需要重启")
            raise MemoryError("RSS exceeded limit")
```

### 4.6 状态持久化

```python
# SQLite 记录关键状态，支持崩溃后恢复
CREATE TABLE session_state (
    instance_index  INTEGER PRIMARY KEY,
    current_phase   TEXT,
    team_code       TEXT,
    retry_count     INTEGER DEFAULT 0,
    last_success_at TEXT,
    error_msg       TEXT,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE match_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_number  INTEGER,
    group_a_matched BOOLEAN,
    group_b_matched BOOLEAN,
    opponent_match  BOOLEAN,
    result          TEXT,  -- "success" | "mismatch" | "timeout" | "abort"
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE error_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_index  INTEGER,
    error_type      TEXT,
    error_msg       TEXT,
    recovery_action TEXT,
    resolved        BOOLEAN DEFAULT FALSE,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
```

### 4.7 日志轮转

```python
from logging.handlers import RotatingFileHandler

handler = RotatingFileHandler(
    "game_automation.log",
    maxBytes=100 * 1024 * 1024,  # 100MB
    backupCount=5,
    encoding="utf-8",
)
```

### 4.8 重试与退避策略

```python
class RetryPolicy:
    """统一重试策略"""

    def __init__(self, max_retries=3, base_delay=2, max_delay=60):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay

    async def execute(self, fn, *args):
        for attempt in range(self.max_retries + 1):
            try:
                return await fn(*args)
            except Exception as e:
                if attempt == self.max_retries:
                    raise
                delay = min(self.max_delay, self.base_delay * (2 ** attempt))
                logger.warning(f"重试 {attempt+1}/{self.max_retries}: {e}, {delay}s 后重试")
                await asyncio.sleep(delay)
```

---

## 五、识别画面总清单

### 正常流程画面

| 编号 | 画面 | 用途 | 状态 | 模板文件 |
|------|------|------|------|---------|
| S01 | 安卓桌面 | 模拟器就绪 | ✅ | moniqizhuyemian-1.png |
| S02 | 游戏 Logo/闪屏 | 游戏启动中 | ✅ | hepingjiazai-4.png |
| S03 | 公告弹窗 | X关闭 | ✅ | close_x_announce |
| S04 | 大厅（无弹窗） | 核心锚点 | ✅ | lobby_start_btn / lobby_start_game |
| S05-S14 | 各类活动弹窗 | 弹窗关闭 | ✅ | close_x_activity / close_x_white_big 等 |
| S15 | 见面礼 | 领取 | ✅ | jianmianli.png |
| S16-S17 | 隐私设置/协议 | 确定/同意 | ✅ | yinsi.png / yinsi2.png |
| S18 | 内存过低提醒 | 确定 | ✅ | neicundi-5.1.png |
| S19 | 登录页面 | 登录检测 | ✅ | denglu-6.png |
| S20-S21 | 加速器未连接/已连接 | 状态检测 | ✅ | accelerator_play / accelerator_pause |
| S22-S24 | 组队面板/组队码/加入提示 | 组队流程 | ✅ | btn_team_code_tab 等 |
| S25-S27 | 地图选择/选中/补位开关 | 地图设置 | ✅ | btn_team_battle_entry 等 |
| S28 | 大厅（组队后） | 地图区域 | ✅ | ditu-10.png |
| S29 | 找队友弹窗 | 随时中断 | ✅ | zhoaduiyou.png |
| S30 | 匹配中画面 | 匹配等待 | ⏳ |  |
| S31 | 加载地图画面 | 匹配成功判定 | ⏳ |  |
| S32 | 对手队伍信息 | 对手校验 | ⏳ |  |
| S33 | 满员组队界面(5人) | 人数检测 | ⏳ |  |
| S34 | 玩家主页(游戏编号) | OCR读ID | ⏳ |  |

### 异常画面

| 编号 | 画面 | 用途 | 状态 |
|------|------|------|------|
| E01 | 登录页面(自动登录失败) | 登录异常 | ❌ |
| E02 | 游戏崩溃后安卓桌面 | 崩溃检测 | ❌ |
| E03 | "账号在其他设备登录" | 被挤检测 | ❌ |
| E04 | "账号已被封禁" | 封号检测 | ❌ |
| E05 | "网络异常"/"连接超时" | 网络异常 | ❌ |
| E06 | "服务器繁忙" | 服务器问题 | ❌ |
| E07 | "禁赛 XX 分钟" | 禁赛检测 | ❌ |
| E08 | 队员"掉线"标识 | 掉线检测 | ❌ |
| E09 | 未知弹窗 | OCR兜底 | ❌ |
| E10 | 卡死/黑屏 | 异常检测 | ❌ |

---

## 六、模板文件清单（32 个）

| 类别 | 文件名 | 用途 |
|------|--------|------|
| 加速器 | accelerator_pause.png | 已连接状态 |
| 加速器 | accelerator_play.png | 未连接状态 |
| X按钮 | close_x_activity.png | 活动弹窗X |
| X按钮 | close_x_announce.png | 公告X |
| X按钮 | close_x_dialog.png | 对话框X |
| X按钮 | close_x_gold.png | 摸金杯X |
| X按钮 | close_x_newplay.png | 新玩法X |
| X按钮 | close_x_return.png | 返回X |
| X按钮 | close_x_signin.png | 签到X |
| X按钮 | close_x_white_big.png | 大白色X |
| 大厅 | lobby_start_btn.png | 开始游戏按钮 |
| 大厅 | lobby_start_game.png | 开始游戏文字 |
| 大厅 | lobby_bottom_bar.png | 底部栏 |
| 按钮 | btn_agree.png | 同意 |
| 按钮 | btn_claim_gift.png | 领取 |
| 按钮 | btn_confirm.png | 确定 |
| 按钮 | btn_confirm_map.png | 地图确定 |
| 按钮 | btn_confirm_privacy.png | 隐私确定 |
| 按钮 | btn_invite_tab.png | 邀请tab |
| 按钮 | btn_join.png | 加入 |
| 按钮 | btn_join_team.png | 加入队伍 |
| 按钮 | btn_no_need.png | 暂不需要 |
| 按钮 | btn_paste_code.png | 粘贴口令 |
| 按钮 | btn_share_team_code.png | 分享组队口令码 |
| 按钮 | btn_team_battle_entry.png | 团队竞技入口 |
| 按钮 | btn_team_code_tab.png | 组队码tab |
| 卡片 | card_classic_team.png | 经典团竞 |
| 卡片 | card_sniper_team.png | 狙击团竞 |
| 其他 | map_checked.png | 地图已选中 |
| 其他 | mode_team_battle.png | 团队竞技模式 |
| 其他 | tab_team.png | 组队tab |
| 其他 | text_click_continue.png | 点击屏幕继续 |

---

## 七、OCR 关键词配置

```python
# 状态判定关键词
LOBBY_KEYWORDS = ["开始游戏"]
LOADING_KEYWORDS = ["正在检查更新", "正在加载", "加载中"]
LOGIN_KEYWORDS = ["QQ授权登录", "微信登录", "登录中"]
LEFT_GAME_KEYWORDS = ["CDN节点第", "六花官方通知"]
# ⚠ 不能用"六花加速器"——游戏内底部也显示"六花加速器[已连接]"

# 弹窗关闭关键词（优先级从高到低）
CHECKBOX_TEXT = ["今日内不再弹出", "今日不再弹出", "不再弹出", "不再提醒"]
CLOSE_TEXT = ["关闭", "×"]
CONFIRM_TEXT = ["确定", "确认", "知道了", "我知道了", "同意",
                "暂不", "跳过", "不需要",
                "点击屏幕继续", "点击屏幕", "点击继续"]
# ⚠ "点击屏幕"类关键词命中时，点击屏幕中央(640,400)而不是文字位置

# 异常检测关键词
BAN_KEYWORDS = ["账号已被封禁", "封禁", "禁赛"]
NETWORK_ERROR_KEYWORDS = ["网络异常", "连接超时", "网络不稳定"]
KICKED_KEYWORDS = ["账号在其他设备登录", "被挤下线"]
SERVER_BUSY_KEYWORDS = ["服务器繁忙", "暂时无法开启"]
```

---

## 八、时间参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 加速器启动超时 | 45s | 15次 × 3s |
| 游戏加载超时 | 90s | 45次 × 2s |
| 弹窗清理最大轮数 | 25轮 | 覆盖7+个弹窗 |
| 模板命中后等待 | 0.5s | 快速路径 |
| OCR命中后等待 | 0.8s | 正常路径 |
| 大厅确认次数 | 1次 | (模板或OCR确认) |
| 截图间隔(正常) | 1s | — |
| 截图间隔(匹配中) | 0.5s | 变化快 |
| 组队人数检测间隔 | 3s | — |
| 匹配超时 | 60s | 超时取消重来 |
| 加载超时 | 30s | — |
| 卡死判定阈值 | 30s | 截图hash不变 |
| 崩溃检测间隔 | 5s | 心跳周期 |
| 两组同步时间差 | 500ms | asyncio.gather |
| 匹配结果判定时差 | 3s | 两组进入加载的时差 |
| 等待真人超时 | 300s | 5分钟 |
| 玩家准备超时 | 60s | — |
| 断网后回大厅超时 | 15s | 超时则force-stop |
| ADB心跳间隔 | 5s | — |
| ADB重连最大重试 | 3次 | 之后重启adb-server |
| 日志文件最大大小 | 100MB | 保留5个备份 |
| 内存上限 | 2GB | 超限重启 |

---

## 九、数据探索结果

### 游戏数据目录结构
```
/data/data/com.tencent.tmgp.pubgmhd/  (需要 adb root)
├── shared_prefs/
│   ├── MSDKPopupCommon.xml    ← 仅含 install_id，无弹窗控制
│   ├── itop.xml               ← 登录信息（Base64加密）
│   └── ... (36个xml文件)
├── files/
│   ├── popup/html/            ← MSDK弹窗HTML模板（加密）
│   └── jwt_token.txt          ← JWT token
└── ...

/sdcard/Android/data/com.tencent.tmgp.pubgmhd/files/UE4Game/
└── ShadowTrackerExtra/Saved/
    ├── Config/Android/        ← 仅音画质量设置
    └── SaveGames/
        ├── sg_newnotice.sav   ← 116KB, GVAS二进制，弹窗状态
        └── sg_act*.sav        ← 活动存档
```

### 结论：修改文件拦截弹窗 — ❌ 不可行
- GVAS 二进制格式（UE4 序列化），无法简单修改
- **最佳策略**: 在脚本中勾选"今日不再弹出"，从 UI 层面减少弹窗

---

## 十、待解决问题

### 必须解决（影响 48h 稳定运行）

| # | 问题 | 方案 | 优先级 |
|---|------|------|-------|
| 1 | 剪贴板读取：模拟器剪贴板 ≠ Windows 剪贴板 | 雷电共享剪贴板 or Windows PowerShell Get-Clipboard | P0 |
| 2 | 看门狗实现 | NSSM Windows 服务 或 Python supervisor | P0 |
| 3 | 两套架构统一 | 以 single_runner 为基础重构 instance_agent | P0 |
| 4 | ADB 连接保活 | 心跳 + 自动重连 | P0 |
| 5 | 卡死检测 + 自动恢复 | 截图 hash + force-stop | P0 |

### 需要实测确认

| # | 问题 | 状态 |
|---|------|------|
| 1 | 断网后游戏是直接回大厅还是有"网络断开"弹窗？ | ⏳ |
| 2 | 真人从上一局退出后多久能重新加入组队？ | ⏳ |
| 3 | 禁赛时长能从提示框 OCR 读取吗？ | ⏳ |
| 4 | 组队时队长能直接踢人吗？ | ⏳ |
| 5 | 匹配成功后加载页对手名字显示多久？ | ⏳ |
| 6 | ADB intent `am start -a VIEW -d URL` 能触发组队加入吗？ | ⏳ |
| 7 | 六个实例同时 ADB 截图的并发性能？ | ⏳ |

### 已解决

| # | 问题 | 结果 |
|---|------|------|
| 1 | 弹窗能否通过修改文件拦截 | ❌ 不可行，GVAS 二进制 |
| 2 | OCR 引擎选型 | RapidOCR ~200ms |
| 3 | 弹窗检测方案 | 遮罩亮度分析 + OCR 状态机 |
| 4 | 模板匹配误匹配问题 | 加载期不用模板判断弹窗，改用 OCR |
| 5 | "六花加速器"误判退出游戏 | 改用"CDN节点第"/"六花官方通知" |
| 6 | 复选框无限循环 | 点复选框后立即找 X 关闭 |
| 7 | 颜色检测误点 | 移除颜色检测，用形状检测替代 |
| 8 | 远程连接稳定性 | 直连 LAN 替代 Cloudflare 隧道 |

---

## 十一、代码文件对照

| 文件 | 用途 | 状态 |
|------|------|------|
| `backend/automation/single_runner.py` | 单实例运行器（阶段0-6） | ✅ 实测通过(到大厅) |
| `backend/automation/ocr_dismisser.py` | OCR状态机弹窗清理 | ✅ 实测通过(7弹窗/12轮) |
| `backend/automation/screen_matcher.py` | 模板匹配引擎 | ✅ 实测通过 |
| `backend/automation/popup_dismisser.py` | 旧版纯模板弹窗清理 | ⚠️ 保留备用 |
| `backend/coordinator.py` | 多实例协调器 | ⏳ 未实测，需重构 |
| `backend/instance_agent.py` | FSM实例代理 | ⏳ 未实测，需对齐 |
| `backend/state_machine.py` | 状态机定义 | ⏳ 需要与实际流程对齐 |
| `backend/handlers/*.py` | 各阶段处理器 | ⏳ 需要与 single_runner 合并 |
| `backend/api.py` | REST/WebSocket API | ⏳ 可用 |
| `backend/diagnostic.py` | 诊断数据收集 | ⏳ 可用 |
| `run_test.py` | Windows 测试入口 | ✅ 实测通过 |
