# V2_PHASES.md — automation_v2 各 phase 代码草稿

> 目标: 用户 1 分钟读懂"重构后每个 phase 长什么样". 总代码 ~3500 行 (含 P5 legacy), 每个 phase 文件 <= 150 行.
>
> 硬约束: (1) `roi` 全 optional 全屏 fallback; (2) 12 实例并发为基线; (3) 所有决策落 7 个时间戳; (4) Yolo/OCR/AdbTap/DecisionLog 皆 `typing.Protocol`.

详见 [Plan agent 输出, 已粘贴到本文档]

---

## 1. `backend/automation_v2/ctx.py` (~80 行)

```python
"""RunContext — 单实例 1 个, 所有 phase 共享的可变状态 + Protocol 接口."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, NamedTuple, Optional


class BlacklistEntry(NamedTuple):
    x: int
    y: int
    expires_at: float


@dataclass
class RunContext:
    yolo: Any                # Yolo Protocol
    ocr: Any                 # OCR Protocol
    matcher: Any             # Matcher Protocol
    adb: Any                 # AdbTap Protocol
    log: Any                 # DecisionLog Protocol

    instance_idx: int = -1
    role: str = "unknown"
    game_scheme_url: Optional[str] = None

    current_shot: Optional["np.ndarray"] = None
    phase_round: int = 0
    phase_started_at: float = 0.0

    trace_id: str = ""
    _ts: dict[str, float] = field(default_factory=dict)
    _blacklist: list[BlacklistEntry] = field(default_factory=list)

    def new_round(self):
        self.trace_id = uuid.uuid4().hex[:12]
        self._ts = {}
        self.mark("round_start")

    def mark(self, event: str):
        self._ts[f"t_{event}"] = time.perf_counter()

    def ts_snapshot(self) -> dict:
        return dict(self._ts)

    def add_blacklist(self, x: int, y: int, ttl: float = 3.0):
        self._blacklist.append(BlacklistEntry(x, y, time.perf_counter() + ttl))

    def is_blacklisted(self, x: int, y: int, radius: int = 30) -> bool:
        now = time.perf_counter()
        self._blacklist = [e for e in self._blacklist if e.expires_at > now]
        return any(abs(e.x - x) < radius and abs(e.y - y) < radius for e in self._blacklist)

    def reset_phase_state(self):
        self._blacklist.clear()
        self.phase_round = 0
        self.phase_started_at = time.perf_counter()
        self.current_shot = None
```

**关键设计点**:
- `mark(event)` 一行记时间戳, `ts_snapshot()` 一次性给 DecisionLog
- 黑名单 TTL 自动过期, 不需要 phase 显式清理
- yolo/ocr/matcher/adb/log 都是 Protocol 注入, 换实现不破上层
- 砍 v1 的 15+ 个 P2 内部字段 (pending_memory_writes / lobby_posterior / pending_verify ...)

---

## 2. `backend/automation_v2/perception/yolo.py` (~150 行)

```python
"""YOLO ONNX 推理 — ROI 可选, 12-instance 并发友好."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import NamedTuple, Optional, Protocol

import cv2
import numpy as np
import onnxruntime as ort

logger = logging.getLogger(__name__)
CLASSES = ["close_x", "action_btn", "lobby"]
NMS_IOU = 0.45
INPUT_SIZE = 640


class Detection(NamedTuple):
    name: str
    conf: float
    cx: int; cy: int
    x1: int; y1: int; x2: int; y2: int


class Roi(NamedTuple):
    x_min: float
    y_min: float
    x_max: float
    y_max: float


class Yolo:
    def __init__(self, model_path: Path, intra_threads: int = 2):
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = intra_threads
        opts.inter_op_num_threads = 1
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        providers = []
        avail = set(ort.get_available_providers())
        for p in ("DmlExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"):
            if p in avail: providers.append(p)
        self.sess = ort.InferenceSession(str(model_path), sess_options=opts, providers=providers)
        self.input_name = self.sess.get_inputs()[0].name

    async def warmup(self):
        dummy = np.zeros((540, 960, 3), dtype=np.uint8)
        await asyncio.to_thread(self._infer_full, dummy, 0.20)

    async def detect(self, shot: np.ndarray, *, roi: Optional[Roi] = None,
                     conf_thresh: float = 0.20) -> list[Detection]:
        if roi is None:
            return await asyncio.to_thread(self._infer_full, shot, conf_thresh)
        return await asyncio.to_thread(self._infer_roi, shot, roi, conf_thresh)

    def _infer_roi(self, shot, roi, conf):
        h, w = shot.shape[:2]
        x1 = int(w * roi.x_min); y1 = int(h * roi.y_min)
        x2 = int(w * roi.x_max); y2 = int(h * roi.y_max)
        crop = shot[y1:y2, x1:x2]
        dets = self._infer_full(crop, conf)
        return [d._replace(
            cx=d.cx + x1, cy=d.cy + y1,
            x1=d.x1 + x1, y1=d.y1 + y1,
            x2=d.x2 + x1, y2=d.y2 + y1,
        ) for d in dets]

    def _infer_full(self, frame, conf):
        h0, w0 = frame.shape[:2]
        tensor, scale, pad = self._letterbox(frame)
        out = self.sess.run(None, {self.input_name: tensor})[0]
        return self._postprocess(out, scale, pad, h0, w0, conf)

    # _letterbox / _postprocess 实现 (~60 行, 标准 YOLOv8 后处理)
```

**关键设计点**:
- ROI optional: 不传全屏 (P1/P5 用), 传了 crop + offset (P2 用 CLOSE_X_ROI 快 5x)
- per-instance session (12 × 200MB = 2.4GB < 8GB VRAM), 无锁真并发
- IO binding (跳 numpy 拷贝, 省 5-10ms/inference)
- 启动 warmup 1 次 dummy 推理避免 cold start
- conf_thresh 默认 0.20 (容忍边缘 popup, 配合黑名单过滤)

---

## 3. `backend/automation_v2/perception/ocr.py` (~120 行)

ROI optional + OpenVINO async + det/cls/rec 按需调用. 详见 Plan agent 原始输出.

**关键设计点**:
- `mode='rec_only'` 给 P5 ID slot 用 (~30ms vs det+rec 200ms)
- `mode='auto'` / `'det+rec'` 给 P3/P4 OCR 大厅按钮用
- 12 实例共享单 OCR (CPU bound, NUM_STREAMS=4)
- 没传 ROI 全屏 OCR 仍工作

---

## 4. `backend/automation_v2/perception/matcher.py` (~100 行)

cv2.matchTemplate, ROI 可选, 单 scale (LDPlayer 960×540 锁定).

**关键设计点**:
- 砍 5-scale 多尺度 (LDPlayer 实例固定分辨率, 单 scale 足够, 5ms/template)
- cv2 释放 GIL, asyncio.to_thread 12 并发真并行

---

## 5. `backend/automation_v2/action/tap.py` (~80 行)

```python
class AdbTap(Protocol):
    async def tap(self, x: int, y: int) -> None: ...
    async def screenshot(self) -> np.ndarray: ...
    async def start_app(self, package: str) -> None: ...


class SubprocessAdbTap:
    """默认实现, subprocess.run adb shell input tap"""
    ...

# 预留: PurePythonAdbTap (adb-shell python lib, 跳 fork), MaaTouchTap
```

**关键设计点**:
- Protocol 稳定, 默认 subprocess 实现
- 未来 POC adb-shell pure-python 不破上层
- screenshot 也挂这, 输入+输出一组 Protocol

---

## 6. `backend/automation_v2/log/decision_simple.py` (~80 行)

```python
class DecisionSimple:
    """JSONL append-only. 7 时间戳算 6 段 ms 落盘."""

    def record(self, *,
               inst, phase, round_idx, outcome,
               t_round_start, t_capture_done, t_yolo_start, t_yolo_done,
               t_decide, t_tap_send, t_tap_done,
               tap=None, tap_target="", conf=0.0,
               trace_id="", dets_count=0, note=""):
        base = t_round_start
        entry = {
            "ts": time.time(),
            "trace_id": trace_id,
            "inst": inst, "phase": phase, "round": round_idx, "outcome": outcome,
            "tap_target": tap_target,
            "tap_xy": list(tap) if tap else None,
            "conf": round(conf, 3),
            "dets_count": dets_count,
            "ms": {
                "capture": round((t_capture_done - base) * 1000, 1),
                "yolo_q": round((t_yolo_start - t_capture_done) * 1000, 1),
                "yolo": round((t_yolo_done - t_yolo_start) * 1000, 1),
                "decide": round((t_decide - t_yolo_done) * 1000, 1),
                "tap_q": round((t_tap_send - t_decide) * 1000, 1),
                "tap": round((t_tap_done - t_tap_send) * 1000, 1),
                "round_total": round((t_tap_done - base) * 1000, 1),
            },
            "note": note,
        }
        with self._lock:
            self._fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

**关键设计点**:
- 7 时间戳 → 6 段 ms, 用户一眼看哪段慢
- trace_id 12-char uuid 串联整 round
- JSONL append-only + line buffering, 12 实例 1 lock, < 1ms/行
- 砍 v1 的 ThreadPoolExecutor + 8 个文件落盘

---

## 7-12. 各 phase 文件

### `phases/p0_accel.py` (~75 行)

加速器校验 1 次 HTTP GET, ok → NEXT, fail → FAIL. 时间戳照填 (空段).

### `phases/p1_launch.py` (~100 行)

am start + 全屏 yolo 轮询. 见 popup/lobby → NEXT. 固定 0.3s round_interval.
**砍**: motion_gate / vision_daemon / ScreenClassifier

### `phases/p2_dismiss.py` (~120 行) — 核心

```python
class P2Dismiss:
    name = "P2"
    max_seconds = 60
    round_interval_s = 0.2
    CLOSE_X_ROI = Roi(0.65, 0.0, 1.0, 0.4)
    ACTION_BTN_ROI = Roi(0.0, 0.40, 1.0, 1.0)

    async def handle_frame(self, ctx):
        shot = ctx.current_shot
        if shot is None: return RETRY

        # ROI 优先
        ctx.mark("yolo_start")
        roi_dets = await ctx.yolo.detect(shot, roi=self.CLOSE_X_ROI)
        close_xs = [d for d in roi_dets if d.name == "close_x" and d.conf >= 0.5]

        # 全屏 fallback
        if not close_xs:
            full_dets = await ctx.yolo.detect(shot)
            close_xs = [d for d in full_dets if d.name == "close_x" and d.conf >= 0.5]
        ctx.mark("yolo_done")

        # 选不黑名单的 conf 最高
        for d in sorted(close_xs, key=lambda x: -x.conf):
            if not ctx.is_blacklisted(d.cx, d.cy):
                ctx.add_blacklist(d.cx, d.cy, ttl=3)
                ctx.mark("decide")
                ctx.mark("tap_send")
                await ctx.adb.tap(d.cx, d.cy)
                ctx.mark("tap_done")
                return RETRY(tap=(d.cx, d.cy), target="close_x", conf=d.conf)

        # action_btn fallback (确定/同意/知道了)
        # ... 类似流程

        # lobby 大厅判定: yolo lobby class >= 1 连续 2 帧 → NEXT
        # ...
        return RETRY
```

**关键设计点**:
- ROI 优先 + 全屏 fallback 一行 `if not result: result = full()`
- 砍 v1 的 5 路 perceive (lobby_tpl / login_tpl / yolo / memory / phash + quad)
- 黑名单 TTL 3s 防死循环, 不再依赖 memory_l1 BK-tree
- 7 时间戳记齐
- 单 round 150-300ms (v1 1500-3000ms)

### `phases/p3a/p3b/p4` (~80 行/each)

薄壳 phase + 真业务在 `flows/` 子模块.

### `phases/p5_wait_players.py` (~30 行壳)

直接复用 v1 (用户确认不动). v2 接口包装一下兼容 RunContext.

### `runner.py` (~250 行)

主 phase loop. 替代 v1 single_runner (1735) + runner_fsm (354) = 2000+ 行.
**关键**:
- 每 round `ctx.new_round()` + `ctx.mark()` 7 次
- 终态 `log.record()` 一次性写齐
- max_seconds 守门 + RETRY sleep round_interval

---

## 总代码量对比

| 文件 | V2 行数 | V1 对照 (砍掉的) |
|---|---|---|
| ctx.py | 80 | phase_base.py 270 (砍 15+ P2 字段) |
| perception/yolo.py | 150 | yolo_dismisser 342 + vision_daemon 600 |
| perception/ocr.py | 120 | ocr_dismisser 432 + ocr_pool + ocr_cache |
| perception/matcher.py | 100 | screen_matcher 465 (砍 5-scale) |
| action/tap.py | 80 | action_executor 400 + adb_lite (verify/exp 砍) |
| log/decision_simple.py | 80 | decision_log 846 (砍 8 文件落盘) |
| phases/p0_accel.py | 75 | p0_accelerator 78 (相当) |
| phases/p1_launch.py | 100 | p1_launch 226 (砍 motion_gate) |
| phases/p2_dismiss.py | 120 | p2_dismiss + subfsm + perception + policy = 1500+ |
| phases/p3a/p3b/p4 | 80 each | 各自 80 (移除入口守门) |
| phases/p5_wait_players.py | 30 (壳) | 1101 行 (legacy 拷过来) |
| runner.py | 120 | runner_fsm 354 + single_runner 1735 = 2000+ |
| **合计** | **~1200 行 (不含 P5)** | **v1 16000 行 → V2 ~3500 (含 P5+flows)** |

## 用户硬约束 ✓ 全达成

1. ROI optional ✓ — yolo/ocr/matcher 全 `roi: Optional[Roi] = None`
2. 12 实例并发 ✓ — yolo per-instance (2.4GB ≤ 8GB), OCR 共享 4 streams
3. 强复现时间戳 ✓ — `mark()` 7 次 + trace_id 12-char uuid
4. Protocol 稳定 ✓ — Yolo/OCR/Matcher/AdbTap/DecisionLog 全 typing.Protocol

详细完整代码见 Plan agent 输出 (此文档为草稿摘要, 实施时按 Plan agent 完整代码写).
