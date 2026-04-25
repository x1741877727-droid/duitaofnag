x c c# game-automation 稳定高速架构优化计划

## Summary

目标是把当前脚本从“截图轮询 + OCR 碰运气 + 点击后假设成功”，升级为适合 **多开、48H 连续运行、低误点、低延迟** 的自动化系统。

核心结论：

- 不能用 Windows 宿主机截图，因为窗口遮挡会导致画面不可信。
- 生产环境禁用 `minicap`，因为 UE4 渲染场景下截图传输不稳定。
- 最优截图方案是 **Android 内部录屏流转接**：`MediaProjection + VirtualDisplay + MediaCodec + adb forward socket`。
- OCR 必须并发，但要受控并发，避免 6 个模拟器同时抢 CPU 导致更慢、更不稳。
- 所有点击必须变成“识别-点击-验证”的事务，不允许旧帧 OCR 结果直接点击。

## Screenshot Architecture

主截图后端：Android CaptureService。

在现有 `vpn-app` 中新增录屏服务：

- `CapturePermissionActivity`：首次请求录屏授权。
- `CaptureService`：前台服务，持有 `MediaProjection` session。
- `CaptureSocketServer`：通过 `localabstract:fmcapture_<index>` 输出帧流。
- `CommandReceiver` 增加 `CAPTURE_START / CAPTURE_STOP / CAPTURE_STATUS` 广播命令。

截图链路：

- Android 端用 `MediaProjection.createVirtualDisplay()` 获取屏幕画面。
- 输出到 `MediaCodec` input surface，使用硬件 H.264 编码。
- Python 端通过 `adb forward tcp:<port> localabstract:fmcapture_<index>` 读取视频流。
- 后端解码后每个实例只保留最新帧。
- OCR 需要高清图时，再按需请求当前帧 JPEG，不持续传 JPEG。

截图后端优先级：

1. `CaptureService` 录屏流，生产主路径。
2. `adb screencap`，仅用于兜底和故障诊断。
3. `minicap`，只保留 debug 开关，不自动启用。
4. Windows 宿主机截图，不使用。

每帧必须携带：

- `frame_id`
- `timestamp`
- `width / height`
- `rotation`
- `backend`
- `phash`
- `capture_latency_ms`

健康检测：

- 帧是否持续更新。
- 是否黑屏。
- pHash 是否长期不变。
- socket 是否断开。
- 解码延迟是否过高。
- 某实例失败只恢复该实例，不影响其他实例。

## Recognition Architecture

新增 `RecognitionScheduler`，统一调度模板识别和 OCR。

OCR 并发规则：

- 每个实例最多 1 个活跃 OCR 任务。
- 全局默认 3-4 个 OCR worker。
- OCR 结果绑定 `frame_id`。
- 如果 OCR 返回时该帧已经过期，结果直接丢弃，不能点击。
- 同一帧同一 ROI 只跑一次 OCR，多关键词共享结果。
- 默认禁止全屏 OCR，全屏 OCR 只作为最后兜底。

OCR 结果结构：

- `text`
- `confidence`
- `box`
- `roi_id`
- `frame_id`
- `preprocess`
- `latency_ms`

模板识别优化：

- 每帧只做一次灰度、缩放、边缘预处理。
- 多个模板共享同一份预处理结果。
- 模板按 phase 分组：
  - 大厅
  - 弹窗
  - 地图面板
  - 组队面板
  - 队员加入
  - 准备状态
- 每个模板配置：
  - ROI
  - 阈值
  - 是否多尺度
  - 是否允许点击
  - 点击后期望状态

识别优先级：

1. 模板识别。
2. ROI OCR。
3. 状态化像素/遮罩检测。
4. 全屏 OCR 兜底。
5. 保存失败证据，不盲目乱点。

## Click Safety

所有点击统一改成事务：

```text
获取最新帧 -> 识别目标 -> 校验 phase -> 校验 ROI -> 校验置信度 -> 坐标映射 -> 点击 -> 等待新帧 -> 验证状态变化
```

规则：

- 旧帧识别结果不能点击。
- 小按钮禁止随机抖动。
- 大按钮最多 1-2px 抖动。
- 点击坐标必须从 capture 分辨率映射到设备真实分辨率。
- 点击后必须验证画面变化或目标消失。
- 连续两次点击无变化，停止当前策略，进入恢复逻辑。
- 每次点击都保存证据：识别方式、`frame_id`、ROI、置信度、坐标、点击前后截图。

## Popup Strategy

大厅清弹窗是重点重构对象。

清弹窗流程：

1. 判断是否在大厅。
2. 判断是否存在遮罩或弹窗面板。
3. 模板查找 X、关闭、确定、跳过。
4. ROI OCR 查找确认类按钮。
5. 点击。
6. 验证弹窗消失。
7. 若失败，保存证据并切换策略。

禁止行为：

- 不再默认盲点屏幕中央。
- 除非明确识别到“点击屏幕继续”，否则不点中央。
- 不允许 OCR 低置信度文字直接触发点击。
- 不允许 guard 在地图/组队等合法弹层阶段误关面板。

## Performance Strategy

目标不是处理每一帧，而是始终处理“最新且必要”的帧。

性能规则：

- Capture stream 可持续 15-30 FPS。
- 识别层按 phase 降频处理。
- 等待阶段低频。
- 弹窗、地图、组队阶段中高频。
- 点击后验证短时间高频。
- OCR 只按事件触发，不按 FPS 连续跑。
- 日志截图异步写盘。
- 成功路径抽样保存，失败路径全量保存。
- 前端截图接口只走缓存和缩略图，不影响主流程。

建议参数：

- 视频流默认 `960x540` 或 `1280x720`。
- `960x540` 码率 1.5-3 Mbps。
- `1280x720` 码率 3-5 Mbps。
- ROI OCR 目标 <300ms。
- 模板批量识别目标 <50ms。
- 全屏 OCR 只允许兜底，不作为常态路径。

## 48H Stability

新增 `ResourceGovernor` 和健康检查。

每个实例独立监控：

- Capture stream 是否更新。
- OCR 队列是否堵塞。
- ADB 是否在线。
- 当前 phase 是否超时。
- 模拟器是否卡死。
- 游戏是否闪退。
- VPN 是否断开。

恢复等级：

- 轻恢复：清空旧 OCR、重新取帧、重新识别。
- 中恢复：重启游戏、重连 VPN。
- 重恢复：重启该模拟器实例。
- 单实例恢复不能影响其他实例。

每小时生成健康摘要：

- 截图延迟。
- OCR 平均/尾延迟。
- 模板耗时。
- 点击次数。
- 点击失败次数。
- 自动恢复次数。
- 模拟器重启次数。

## Implementation Order

### 1. 观测先行

- 给截图、OCR、模板、点击、phase 增加结构化耗时日志。
- 先确认当前 1-2 秒延迟到底来自 OCR、截图、队列还是阻塞。

### 2. OCR 调度器

- 改造 `OcrDismisser` 为 async OCR。
- 增加全局 worker 池。
- 增加 `frame_id` 过期保护。
- 增加 ROI 缓存。

### 3. 清弹窗重构

- 优先重构大厅清弹窗。
- 全部点击改成事务式验证。
- 去掉危险盲点逻辑。

### 4. 地图和组队重构

- 地图选择、组队码、二维码、队员加入都改为模板优先 + ROI OCR。
- 找不到目标不能假成功。

### 5. CaptureService POC

- 先单实例实现 Android 录屏转接。
- 验证 UE4 画面、遮挡无影响、帧不旧。
- 再扩展到 6 实例。

### 6. 6 开压测

- 先 2H。
- 再 12H。
- 最后 48H。
- 每次失败都必须能从日志复盘。

## Acceptance Criteria

- 6 个实例同时运行不串画面、不串 ADB serial。
- OCR ROI 平均延迟 <300ms。
- OCR 不再常态 1-2 秒。
- 点击误触显著下降。
- 清弹窗失败时能自动恢复或保存完整证据。
- 单实例异常不拖垮其他实例。
- 48H 无全局卡死。
- 所有关键失败都能通过日志定位原因。

## Assumptions

- 允许修改并重新打包 `vpn-app`。
- 每个模拟器首次启动时可以处理一次录屏授权。
- 目标环境是 Windows + LDPlayer 多开。
- 优先级是稳定性、性能、速度同时兼顾，但任何速度优化都不能牺牲画面可信度。
- 官方依据：Android `MediaProjectionManager.createScreenCaptureIntent()` 用于请求屏幕捕获授权；`MediaProjection.createVirtualDisplay()` 用于捕获屏幕；`ImageReader.acquireLatestImage()` 适合实时处理，因为它会丢弃旧图并取最新图。
