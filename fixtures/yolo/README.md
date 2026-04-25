# YOLOv8 UI 元素检测 — 训练 / 推理 工作流

替代 OCR 检测**稳定 UI 元素**（按钮 / 图标）。变化频繁的内容（公告 / 模式 / 地图名）继续走 OCR + cache。

## 架构概览

```
┌─────────── 一次性 / 按需 ────────────┐  ┌──────── 每次推理 ────────┐
│                                       │  │                          │
│  raw_screenshots/  ←  你采集的截图      │  │  ocr_dismisser           │
│         │                              │  │       │ 想找按钮          │
│         ▼                              │  │       ▼                  │
│  yolo_autolabel.py                     │  │  yolo_detector.detect    │
│   ├─ OCR 伪标签                        │  │   (3-50ms 一帧)          │
│   ├─ 模板伪标签                        │  │       │                  │
│   └─ 写 YOLO 格式 dataset/             │  │       ├─ 命中 → tap      │
│         │                              │  │       └─ miss → OCR fallback
│         ▼  人工抽查 review.html        │  │                          │
│  yolo_train.py (ultralytics)           │  └──────────────────────────┘
│         │                              │
│         ▼                              │
│  yolo_export.py → ui_yolo.onnx         │
│                                        │
└────────────────────────────────────────┘
```

## 目录结构

```
fixtures/yolo/
  README.md                      ← 本文件
  raw_screenshots/               ← 你扔进来的原始截图（PNG/JPG）
  dataset/                       ← autolabel 生成
    train/{images, labels}/
    val/{images, labels}/
    data.yaml
  runs/                          ← 训练产出（ultralytics 默认目录）
    train/weights/{best.pt, last.pt}

backend/automation/models/
  ui_yolo.onnx                   ← export 后落地，runtime lazy-load 这个
```

## Step 1：采集原始截图

需要 **覆盖各种状态**的截图：
- 大厅（不同时间，不同活动 banner 在背景里）
- 加速器各种状态（连接前 / 连接中 / 已连接）
- 组队面板各步骤（点开 / 切到组队码 tab / QR 入口 / QR 显示）
- 弹窗各种类（公告 / 隐私同意 / 二次确认）
- **每个稳定按钮至少 100 张不同背景**

来源：
- `D:\game-automation\duitaofnag\logs\<session>\instance_*\screenshots\` （脚本采集的失败 / 成功帧）
- 手动 `adb shell screencap -p`
- 模拟器自带截图工具

把 PNG/JPG 都丢到 `fixtures/yolo/raw_screenshots/`。**不要分子目录**。

## Step 2：自动标注 + 人工抽查

```bash
python tools/yolo_autolabel.py --review
```

会做：
1. 跑现有 OCR + 模板对每张图找已知按钮
2. 输出伪标签到 `fixtures/yolo/dataset/train/labels/*.txt`（YOLO 格式）
3. 80% 训练集 / 20% 验证集自动切分
4. `--review` 生成 `dataset/review.html`，浏览器打开抽查框对不对

人工 review：
- 漏标的（YOLO 类有但 OCR/模板没找到）→ 手动加一行到 `.txt`
- 框对了但位置/尺寸偏 → 手动改坐标
- 类标错 → 改 class_id

YOLO 标签格式（每行一个 box）：
```
class_id  cx  cy  w  h
```
所有数字归一化到 `[0, 1]`。

## Step 3：训练

```bash
# 默认 80 epochs，YOLOv8n（最小最快）
python tools/yolo_train.py

# 详细控制
python tools/yolo_train.py --epochs 120 --batch 32 --device 0 --imgsz 640
```

依赖：`pip install ultralytics`

输出：`fixtures/yolo/runs/train/weights/best.pt`

时间预估：
- 1000 张训练集 + RTX 5070 Ti = ~25 分钟
- 1000 张训练集 + 集成显卡 = ~3 小时（强烈不建议；用云 GPU 或 Colab）

## Step 4：导出 ONNX

```bash
python tools/yolo_export.py
```

写到 `backend/automation/models/ui_yolo.onnx`。

## Step 5：自动启用

`yolo_detector.py` 会在第一次调 `detect_buttons()` 时 lazy-load 这个 ONNX。
**模型文件存在 = YOLO 启用；不存在 = 自动 fallback 全部走 OCR**，不报错。

集成入 `OcrDismisser` 的方式（示例，**需要后续手工接**）：

```python
from .yolo_detector import detect_buttons, is_available

class OcrDismisser:
    async def find_button(self, frame, button_name):
        if is_available():
            dets = detect_buttons(frame, names=[button_name])
            if dets and dets[0].score > 0.6:
                return dets[0].center_px
        # fallback OCR
        hits = self._ocr_roi_named(frame, ROI_FOR_BUTTON[button_name])
        for h in hits:
            if self.fuzzy_match(h.text, KEYWORDS_FOR_BUTTON[button_name]):
                return (h.cx, h.cy)
        return None
```

## 何时重训

- 游戏更新后，**先**跑 golden_runner 看 OCR 命中率有没有掉
- YOLO 模型对 UI 视觉变化敏感，**重大改版要重训**：
  - 新加的按钮 → 加到 `config/yolo_classes.yaml`，补 100-200 张样本，重训
  - 现有按钮换图标 → 补 50-100 张新样本，再训
  - 字体 / 配色微调 → 通常老模型还能用，先观察 score 分布

## 跨平台 ONNX provider

模型导出是平台无关的 ONNX，运行时根据已装 wheel 自动选：

| 平台 | 推荐 wheel | 速度 |
|---|---|---|
| Win + 任意 GPU | `pip install onnxruntime-directml` | YOLOv8n 5-15ms |
| Linux + NV | `pip install onnxruntime-gpu` | YOLOv8n 3-8ms |
| Mac (Apple Silicon) | `pip install onnxruntime` (CoreML 自带) | YOLOv8n 8-20ms |
| 任何机器 | `pip install onnxruntime` | YOLOv8n CPU 30-80ms |

**默认 CPU 也比 OCR 快 30-50 倍**。给所有用户都受益。
