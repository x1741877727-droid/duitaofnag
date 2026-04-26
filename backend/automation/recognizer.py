"""
v2 Tier 0-4 Early-exit 识别链调度器

任何 phase 想找一个 UI 目标 (e.g. "lobby_start_btn", "close_x") 都用这个。
99% 流量在 Tier 0/1 命中即返回, 后续 Tier 不跑.

用法:
    target = Target(
        name="lobby_start_btn",
        template_names=["lobby_start_btn"],
        template_threshold=0.85,
        use_memory=True,
    )
    hit = recognizer.find(frame, target)
    if hit:
        adb.tap(hit.cx, hit.cy)
        memory.remember(frame, target.name, (hit.cx, hit.cy), success=True)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class Tier(IntEnum):
    TEMPLATE = 0
    MEMORY = 1
    YOLO = 2
    OCR = 3
    VLM = 4


@dataclass
class Hit:
    """一次识别命中结果. 任何 Tier 命中都用这个统一类型回."""
    tier: Tier
    label: str
    confidence: float
    cx: int
    cy: int
    w: int = 0
    h: int = 0
    elapsed_ms: float = 0.0
    note: str = ""

    @property
    def bbox(self) -> Tuple[int, int, int, int]:
        return (self.cx - self.w // 2, self.cy - self.h // 2, self.w, self.h)


@dataclass
class Target:
    """要找的 UI 目标 — recognizer 按 5 层链顺序试."""
    name: str
    template_names: List[str] = field(default_factory=list)
    template_threshold: float = 0.85
    use_memory: bool = True
    memory_max_dist: int = 5
    yolo_classes: List[str] = field(default_factory=list)
    yolo_threshold: float = 0.7
    ocr_keywords: List[str] = field(default_factory=list)
    ocr_blacklist: List[str] = field(default_factory=list)
    use_vlm: bool = False


class Recognizer:
    """5 层 Early-exit 调度器.

    任何 Tier 没装的都跳过. 没装 = None / 不传.
    """

    def __init__(
        self,
        matcher=None,
        yolo_detect_fn: Optional[Callable] = None,
        ocr_fn: Optional[Callable] = None,
        memory=None,
        vlm_fn: Optional[Callable] = None,
    ):
        self._matcher = matcher
        self._yolo_detect = yolo_detect_fn
        self._ocr = ocr_fn
        self._memory = memory
        self._vlm = vlm_fn
        self._stats = {t.name: 0 for t in Tier}
        self._stats["MISS"] = 0

    def find(
        self,
        frame: np.ndarray,
        target: Target,
        record: Optional[Callable] = None,
    ) -> Optional[Hit]:
        """5 层 early-exit. 命中即返回, 没命中 None.

        record(tier: Tier, info: dict) 可选, 让 Decision Theater 拿到每层数据.
        """
        # ── Tier 0: 模板 ──
        if target.template_names and self._matcher is not None:
            t0 = time.perf_counter()
            hit = self._tier_template(frame, target)
            ms = (time.perf_counter() - t0) * 1000
            if record:
                record(Tier.TEMPLATE, {"hit": hit, "ms": ms})
            if hit:
                hit.elapsed_ms = ms
                self._stats["TEMPLATE"] += 1
                return hit

        # ── Tier 1: Memory phash 复读机 ──
        if target.use_memory and self._memory is not None:
            t0 = time.perf_counter()
            hit = self._tier_memory(frame, target)
            ms = (time.perf_counter() - t0) * 1000
            if record:
                record(Tier.MEMORY, {"hit": hit, "ms": ms})
            if hit:
                hit.elapsed_ms = ms
                self._stats["MEMORY"] += 1
                return hit

        # ── Tier 2: YOLO ──
        yolo_hits: List[Hit] = []
        if target.yolo_classes and self._yolo_detect is not None:
            t0 = time.perf_counter()
            yolo_hits = self._tier_yolo(frame, target)
            ms = (time.perf_counter() - t0) * 1000
            if record:
                record(Tier.YOLO, {"hits": yolo_hits, "ms": ms})
            if yolo_hits and not target.ocr_keywords:
                hit = yolo_hits[0]
                hit.elapsed_ms = ms
                self._stats["YOLO"] += 1
                return hit

        # ── Tier 3: OCR (优先 YOLO bbox 内) ──
        if target.ocr_keywords and self._ocr is not None:
            t0 = time.perf_counter()
            hit = self._tier_ocr(frame, target, yolo_hits)
            ms = (time.perf_counter() - t0) * 1000
            if record:
                record(Tier.OCR, {"hit": hit, "ms": ms})
            if hit:
                hit.elapsed_ms = ms
                self._stats["OCR"] += 1
                return hit

        # ── Tier 4: VLM 兜底 ──
        if target.use_vlm and self._vlm is not None:
            t0 = time.perf_counter()
            hit = self._vlm(frame, target)
            ms = (time.perf_counter() - t0) * 1000
            if record:
                record(Tier.VLM, {"hit": hit, "ms": ms})
            if hit:
                hit.elapsed_ms = ms
                self._stats["VLM"] += 1
                return hit

        self._stats["MISS"] += 1
        return None

    def _tier_template(self, frame: np.ndarray, target: Target) -> Optional[Hit]:
        try:
            match = self._matcher.find_any(
                frame, target.template_names, threshold=target.template_threshold
            )
        except Exception as e:
            logger.warning(f"[recognizer] template failed: {e}")
            return None
        if match is None:
            return None
        return Hit(
            tier=Tier.TEMPLATE,
            label=match.name,
            confidence=match.confidence,
            cx=match.cx,
            cy=match.cy,
            w=match.w,
            h=match.h,
        )

    def _tier_memory(self, frame: np.ndarray, target: Target) -> Optional[Hit]:
        try:
            return self._memory.query(frame, target.name, max_dist=target.memory_max_dist)
        except Exception as e:
            logger.warning(f"[recognizer] memory failed: {e}")
            return None

    def _tier_yolo(self, frame: np.ndarray, target: Target) -> List[Hit]:
        try:
            detections = self._yolo_detect(frame)
        except Exception as e:
            logger.warning(f"[recognizer] yolo failed: {e}")
            return []
        out: List[Hit] = []
        for d in detections:
            cls_name = getattr(d, "cls", None) or getattr(d, "name", None)
            if cls_name not in target.yolo_classes:
                continue
            conf = getattr(d, "conf", None)
            if conf is None:
                conf = getattr(d, "score", 0.0)
            if conf < target.yolo_threshold:
                continue
            bbox = getattr(d, "bbox", None)
            if bbox is None and hasattr(d, "x1"):
                bbox = [d.x1, d.y1, d.x2, d.y2]
            if not bbox or len(bbox) < 4:
                continue
            x1, y1, x2, y2 = bbox[:4]
            out.append(
                Hit(
                    tier=Tier.YOLO,
                    label=cls_name,
                    confidence=float(conf),
                    cx=(x1 + x2) // 2,
                    cy=(y1 + y2) // 2,
                    w=x2 - x1,
                    h=y2 - y1,
                )
            )
        out.sort(key=lambda h: -h.confidence)
        return out

    def _tier_ocr(
        self, frame: np.ndarray, target: Target, yolo_hits: List[Hit]
    ) -> Optional[Hit]:
        if yolo_hits:
            for yh in yolo_hits:
                roi = (yh.cx - yh.w // 2, yh.cy - yh.h // 2, yh.w, yh.h)
                hits = self._ocr_in_roi(frame, roi)
                hit = self._match_keywords(hits, target)
                if hit:
                    return hit
            return None
        hits = self._ocr_in_roi(frame, None)
        return self._match_keywords(hits, target)

    def _ocr_in_roi(self, frame: np.ndarray, roi):
        try:
            return self._ocr(frame, roi) or []
        except Exception as e:
            logger.warning(f"[recognizer] ocr failed: {e}")
            return []

    def _match_keywords(self, ocr_hits, target: Target) -> Optional[Hit]:
        for h in ocr_hits:
            text = getattr(h, "text", "") or ""
            if any(bw and bw in text for bw in target.ocr_blacklist):
                continue
            for kw in target.ocr_keywords:
                if kw and kw in text:
                    cx = getattr(h, "cx", None)
                    cy = getattr(h, "cy", None)
                    if cx is None or cy is None:
                        bbox = getattr(h, "bbox", None) or [0, 0, 0, 0]
                        if len(bbox) >= 4:
                            cx = (bbox[0] + bbox[2]) // 2
                            cy = (bbox[1] + bbox[3]) // 2
                        else:
                            cx = cy = 0
                    conf = getattr(h, "conf", 0.7) or 0.7
                    return Hit(
                        tier=Tier.OCR,
                        label=kw,
                        confidence=float(conf),
                        cx=int(cx),
                        cy=int(cy),
                    )
        return None

    def stats(self) -> dict:
        total = sum(self._stats.values())
        if total == 0:
            return {"counts": dict(self._stats), "pct": {}, "total": 0}
        pct = {k: round(v / total * 100, 1) for k, v in self._stats.items()}
        return {"counts": dict(self._stats), "pct": pct, "total": total}

    def reset_stats(self) -> None:
        for k in self._stats:
            self._stats[k] = 0


# ─────────────── Decision Theater 适配 ───────────────


def make_decision_recorder(decision) -> Optional[Callable]:
    """把 Recognizer 的 record callback 转成 Decision.add_tier(TierRecord) 写入.

    用法:
        decision = recorder.new_decision(instance=0, phase="dismiss_popups")
        decision.set_input(frame)
        hit = recognizer.find(frame, target, record=make_decision_recorder(decision))

    decision 为 None 或 _NullDecision 时返回 None, 不记录 (零开销).
    """
    if decision is None:
        return None
    # _NullDecision 的 add_tier 是 no-op (通过 __getattr__), 不需要单独检查

    _tier_names = {0: "Template", 1: "Memory", 2: "YOLO", 3: "OCR", 4: "VLM"}

    def _record(tier: Tier, info: dict) -> None:
        try:
            from .decision_log import TierRecord, YoloDetection
        except Exception:
            return
        rec = TierRecord(
            tier=int(tier),
            name=_tier_names.get(int(tier), str(tier)),
            duration_ms=round(float(info.get("ms", 0.0)), 2),
        )
        hit = info.get("hit")
        hits = info.get("hits") or []
        if hit is not None:
            rec.early_exit = True
            rec.note = (
                f"label={getattr(hit, 'label', '?')} "
                f"conf={getattr(hit, 'confidence', 0):.2f} "
                f"({getattr(hit, 'cx', 0)},{getattr(hit, 'cy', 0)})"
            )
        elif hits:
            rec.note = f"{len(hits)} detections"

        if int(tier) == int(Tier.YOLO) and hits:
            for h in hits:
                w = getattr(h, "w", 0)
                hh = getattr(h, "h", 0)
                cx = getattr(h, "cx", 0)
                cy = getattr(h, "cy", 0)
                rec.yolo_detections.append(
                    YoloDetection(
                        cls=getattr(h, "label", ""),
                        conf=float(getattr(h, "confidence", 0)),
                        bbox=[cx - w // 2, cy - hh // 2, cx + w // 2, cy + hh // 2],
                    )
                )

        try:
            decision.add_tier(rec)
        except Exception as e:
            logger.debug(f"[recognizer] decision.add_tier failed: {e}")

    return _record
