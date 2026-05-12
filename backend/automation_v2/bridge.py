"""bridge — Day 4 灰度桥接.

把 v1 SingleInstanceRunner 的 adb/yolo/ocr 包成 v2 Protocol 接口, 让 v2 phases (P0/P1/P2)
能在 v1 资源上跑, 不需要双倍 ONNX session. Day 5+ 真重构时砍掉.

v1 → v2 适配:
- v1 runner.adb (ADBController) → AdbTapProto (.tap / .screenshot, 直接 duck-type 兼容)
- v1 runner.yolo_dismisser.detect() (sync, v1 Detection w/ cls) → YoloProto (async, v2 Detection NamedTuple)
- v1 runner.ocr_dismisser → OcrProto (Day 4 不接, P0/P1/P2 不用 OCR)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class V1YoloAdapter:
    """包 v1 YoloDismisser.detect 成 v2 YoloProto.

    - 加 async (asyncio.to_thread)
    - ROI crop + 坐标 offset 还原
    - conf_thresh 内部过滤
    - v1 Detection (dataclass) → v2 Detection (NamedTuple, 砍 cls 字段)
    """

    def __init__(self, v1_yolo: Any):
        self._yolo = v1_yolo

    async def detect(self, shot, *, roi=None, conf_thresh: float = 0.20) -> list:
        from .perception.yolo import Detection as V2Det
        if shot is None:
            return []

        # ROI crop
        offset_x = offset_y = 0
        target = shot
        if roi is not None:
            h, w = shot.shape[:2]
            x1 = int(roi[0] * w); y1 = int(roi[1] * h)
            x2 = int(roi[2] * w); y2 = int(roi[3] * h)
            target = shot[y1:y2, x1:x2]
            offset_x, offset_y = x1, y1

        try:
            v1_dets = await asyncio.to_thread(self._yolo.detect, target)
        except Exception as e:
            logger.debug(f"[bridge/yolo] detect err: {e}")
            return []

        # v1 Detection → v2 Detection + offset 还原 + conf 过滤
        out = []
        for d in v1_dets or []:
            if getattr(d, "conf", 0) < conf_thresh:
                continue
            out.append(V2Det(
                name=d.name,
                conf=d.conf,
                cx=d.cx + offset_x, cy=d.cy + offset_y,
                x1=d.x1 + offset_x, y1=d.y1 + offset_y,
                x2=d.x2 + offset_x, y2=d.y2 + offset_y,
            ))
        return out

    async def warmup(self) -> None:
        """v1 yolo 由 runner_service 在 start_all 阶段已 warmup, 这里 no-op."""
        pass


class V1OcrAdapter:
    """包 v1 OcrDismisser 成 v2 OcrProto.

    - 加 async (v1 _ocr_all_async 已 async; _ocr_roi_named 是 sync, 包 to_thread)
    - ROI crop + 坐标 offset 还原 (跟 V1YoloAdapter 一致)
    - v1 TextHit (text/cx/cy) → 包 OcrHitAdapter, 暴露 .text/.cx/.cy/.bbox
    """

    class OcrHitAdapter:
        """OcrHit 兼容对象: 既有 .text/.cx/.cy 又有 .bbox/.conf, 适配 v2 phase."""
        __slots__ = ("text", "cx", "cy", "bbox", "conf")

        def __init__(self, text: str, cx: int, cy: int, conf: float = 0.9):
            self.text = text
            self.cx = cx
            self.cy = cy
            self.bbox = (cx - 5, cy - 5, cx + 5, cy + 5)  # v1 无 bbox, 估个小区域
            self.conf = conf

    def __init__(self, v1_ocr: Any):
        self._ocr = v1_ocr

    async def recognize(self, shot, *, roi=None, mode: str = "auto") -> list:
        if shot is None or getattr(shot, "size", 0) == 0:
            return []

        offset_x = offset_y = 0
        target = shot
        if roi is not None:
            h, w = shot.shape[:2]
            x1 = int(roi[0] * w); y1 = int(roi[1] * h)
            x2 = int(roi[2] * w); y2 = int(roi[3] * h)
            target = shot[y1:y2, x1:x2]
            offset_x, offset_y = x1, y1
            if target.size == 0:
                return []

        try:
            v1_hits = await self._ocr._ocr_all_async(target)
        except Exception as e:
            logger.debug(f"[bridge/ocr] recognize err: {e}")
            return []

        out = []
        for h in v1_hits or []:
            text = getattr(h, "text", "") or ""
            cx = int(getattr(h, "cx", 0)) + offset_x
            cy = int(getattr(h, "cy", 0)) + offset_y
            out.append(self.OcrHitAdapter(text=text, cx=cx, cy=cy))
        return out

    async def warmup(self) -> None:
        """v1 ocr_dismisser 在 single_runner init 时已 warmup."""
        pass


class V1AdbAdapter:
    """v1 ADBController 转发. 暴露 v2 phase 用的 tap/screenshot/start_app. ._adb 给底层访问."""

    def __init__(self, v1_adb):
        self._adb = v1_adb

    async def tap(self, x: int, y: int) -> None:
        return await self._adb.tap(x, y)

    async def screenshot(self):
        return await self._adb.screenshot()

    async def start_app(self, package: str, activity: str = ""):
        return await self._adb.start_app(package, activity)

    def __getattr__(self, name):
        """漏的方法转发到 v1 adb (e.g. shell / _cmd / pidof)."""
        return getattr(self._adb, name)


def build_v2_ctx(
    *,
    instance_idx: int,
    role: str,
    v1_runner: Any,
    session_dir,
):
    """装配 v2 RunContext, 资源全部桥接到 v1 runner.

    Args:
        instance_idx: 实例 idx
        role: captain / member
        v1_runner: v1 SingleInstanceRunner 实例 (拿 adb/yolo/ocr)
        session_dir: pathlib.Path, decision.jsonl 写这下

    Returns:
        (ctx, decision_log) — ctx 给 v2 SingleRunner, decision_log 给 runner_service 关闭
    """
    from .ctx import RunContext
    from .log.decision_simple import DecisionSimple
    from pathlib import Path

    # v2 decision 写 session_dir/inst_{N}_v2/decisions.jsonl, 跟 v1 detailed 不冲突
    log_dir = Path(session_dir) / f"inst_{instance_idx}_v2"
    decision_log = DecisionSimple(log_dir)

    yolo_v1 = getattr(v1_runner, "yolo_dismisser", None)
    yolo_adapter = V1YoloAdapter(yolo_v1) if yolo_v1 is not None else None

    # v2 P3a/P4 真重写后需要 OCR (找模式名/地图名/验证关键词).
    # v1 ocr_dismisser 是 ImageRunner, ImageRunner 模式下不创建会 None
    ocr_v1 = getattr(v1_runner, "ocr_dismisser", None)
    ocr_adapter = V1OcrAdapter(ocr_v1) if ocr_v1 is not None else None

    adb_adapter = V1AdbAdapter(v1_runner.adb)

    ctx = RunContext(
        yolo=yolo_adapter,
        ocr=ocr_adapter,
        matcher=getattr(v1_runner, "matcher", None),
        adb=adb_adapter,
        log=decision_log,
        instance_idx=instance_idx,
        role=role,
        runner_version="v2",
        v1_runner=v1_runner,
    )
    return ctx, decision_log


# Phase 名翻译: v2 (P0-P5) → v1 词汇 (前端 PHASE_LABELS 兼容).
# REVIEW_DAY4_SWITCH.md §4: 不翻译会让 inst.state 没 label.
V2_PHASE_TO_V1: dict[str, str] = {
    "P0":  "accelerator",
    "P1":  "launch_game",
    "P2":  "dismiss_popups",
    "P3a": "team_create",
    "P3b": "team_join",
    "P4":  "map_setup",
    "P5":  "done",
}
