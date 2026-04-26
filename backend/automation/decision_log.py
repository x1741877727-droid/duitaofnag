"""
Decision Recorder — 决策可观测性

每次识别+决策都记录到磁盘，前端能完整回放：
  - 输入截图（机器看见的全屏）
  - 各 Tier 的工作过程（模板试过哪些 / YOLO bbox / OCR 文字）
  - 模板的图本身（让用户看 close_x_announce 长啥样）
  - ROI 区域（如果只看部分屏幕，把那块框出来）
  - 最终决策（点哪 / 用哪个 Tier 给的结果）
  - 验证结果（phash 前后对比）

存盘位置：
  <session_dir>/decisions/<timestamp>_inst{N}_{phase}/
    decision.json    # 完整决策记录
    input.jpg        # 输入帧
    yolo_annot.jpg   # YOLO 标注帧
    tap_annot.jpg    # 点击位置标注帧
    tmpl_<name>.png  # 试过的模板图（拷贝）
    roi_<name>.jpg   # ROI 区域裁剪
    ...

前端通过 /api/decisions 查询。
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# ─────────────── 数据结构 ───────────────


@dataclass
class TemplateMatch:
    """单次模板匹配尝试"""
    name: str                       # 模板名 (lobby_start_btn)
    template_image: str = ""        # 模板图相对路径（前端可拉）
    score: float = 0.0              # match score
    threshold: float = 0.0          # 当时阈值
    hit: bool = False               # 是否命中
    bbox: Optional[list] = None     # 命中时 [x1,y1,x2,y2]
    scale: float = 1.0              # 命中时的 scale


@dataclass
class YoloDetection:
    """单次 YOLO 检测目标"""
    cls: str                        # close_x / action_btn
    conf: float
    bbox: list                      # [x1,y1,x2,y2]


@dataclass
class OcrHit:
    """OCR 识别一条文字"""
    text: str
    bbox: list                      # [x1,y1,x2,y2]
    conf: float = 0.0
    cx: int = 0
    cy: int = 0


@dataclass
class TierRecord:
    """一个 Tier 的工作记录"""
    tier: int                       # 0/1/2/3/4
    name: str                       # 模板/Memory/YOLO/OCR/VLM
    duration_ms: float = 0.0
    early_exit: bool = False        # 是否在此 Tier 命中并退出
    note: str = ""

    # 模板：尝试列表
    templates: list = field(default_factory=list)   # list[TemplateMatch]

    # YOLO
    yolo_detections: list = field(default_factory=list)  # list[YoloDetection]
    yolo_annot_image: str = ""      # 画了 bbox 的标注图

    # OCR
    ocr_hits: list = field(default_factory=list)    # list[OcrHit]
    ocr_roi: Optional[list] = None  # 如果只 OCR 局部，[x1,y1,x2,y2]
    ocr_roi_image: str = ""         # ROI 区域的截图（带框）

    # Memory
    memory_phash_query: str = ""
    memory_hit: Optional[dict] = None    # {phash, action, success}


@dataclass
class TapRecord:
    x: int
    y: int
    method: str                     # 哪一 Tier 决定的
    target_class: str = ""          # close_x / action_btn / lobby_start_btn ...
    target_text: str = ""           # OCR 读出的文字（如有）
    target_conf: float = 0.0
    annot_image: str = ""           # 画了红圈的标注图


@dataclass
class VerifyRecord:
    phash_before: str = ""
    phash_after: str = ""
    distance: int = 0
    success: Optional[bool] = None  # True=画面变=点中, False=没变, None=未验证


# ─────────────── Recorder（单例 + per-decision context）───────────────


class _Recorder:
    """全局单例。runner_service start_all 时初始化 session dir"""

    def __init__(self):
        self._lock = threading.Lock()
        self._root: Optional[Path] = None
        self._enabled = False
        # 内存索引（最近 N 条），加速前端查询
        self._index: list[dict] = []
        self._max_index = 500

    def init(self, session_dir: str | Path):
        with self._lock:
            self._root = Path(session_dir) / "decisions"
            self._root.mkdir(parents=True, exist_ok=True)
            self._enabled = True
            logger.info(f"[decision] 记录目录: {self._root}")

    def is_enabled(self) -> bool:
        return self._enabled and self._root is not None

    def root(self) -> Optional[Path]:
        return self._root

    def new_decision(self, instance: int, phase: str, round_idx: int = 0) -> "Decision":
        if not self.is_enabled():
            return _NullDecision()
        ts = time.strftime("%H%M%S") + f"_{int((time.time() % 1) * 1000):03d}"
        d_id = f"{ts}_inst{instance}_{phase}_R{round_idx}"
        d_path = self._root / d_id
        try:
            d_path.mkdir(parents=True, exist_ok=True)
        except Exception:
            return _NullDecision()
        return Decision(d_id, d_path, instance, phase, round_idx, self)

    def record_summary(self, summary: dict):
        """完结一次决策，加进索引"""
        with self._lock:
            self._index.append(summary)
            if len(self._index) > self._max_index:
                self._index = self._index[-self._max_index:]

    def list_recent(self, limit: int = 50, instance: Optional[int] = None) -> list[dict]:
        with self._lock:
            items = self._index[::-1]  # 倒序，最新在前
            if instance is not None:
                items = [x for x in items if x.get("instance") == instance]
            return items[:limit]


_recorder = _Recorder()


def get_recorder() -> _Recorder:
    return _recorder


# ─────────────── Decision 上下文 ───────────────


class _NullDecision:
    """禁用时返回，所有方法 no-op"""
    def __getattr__(self, name): return self._noop
    def _noop(self, *args, **kwargs): return self
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Decision:
    """单次决策的上下文"""

    def __init__(self, d_id: str, path: Path, instance: int, phase: str,
                 round_idx: int, recorder: _Recorder):
        self.id = d_id
        self.path = path
        self.instance = instance
        self.phase = phase
        self.round = round_idx
        self.created = time.time()
        self.recorder = recorder

        self.input_image = ""
        self.input_phash = ""
        self.input_w = 0
        self.input_h = 0

        self.tiers: list[TierRecord] = []
        self.tap: Optional[TapRecord] = None
        self.verify: Optional[VerifyRecord] = None
        self.outcome: str = ""          # "tap_succeeded" / "tap_failed" / "skipped" / "lobby"
        self.note: str = ""

    # ────── 输入 ──────

    def set_input(self, screenshot: np.ndarray, phash: str = "", q: int = 70):
        if screenshot is None:
            return self
        self.input_h, self.input_w = screenshot.shape[:2]
        self.input_phash = phash
        try:
            cv2.imwrite(str(self.path / "input.jpg"), screenshot,
                        [cv2.IMWRITE_JPEG_QUALITY, q])
            self.input_image = "input.jpg"
        except Exception as e:
            logger.warning(f"[decision] save input fail: {e}")
        return self

    # ────── Tier 结果 ──────

    def add_tier(self, tier: TierRecord):
        self.tiers.append(tier)
        return self

    # ────── 模板试探 ──────

    def add_template_attempt(self, tier: TierRecord, template_name: str,
                             template_dir: Path, score: float, threshold: float,
                             hit: bool, bbox: Optional[list] = None, scale: float = 1.0):
        """记录一次模板尝试。把模板图复制到决策目录"""
        tmpl_rel = ""
        try:
            src = template_dir / f"{template_name}.png"
            if src.exists():
                dst = self.path / f"tmpl_{template_name}.png"
                if not dst.exists():
                    shutil.copyfile(src, dst)
                tmpl_rel = dst.name
        except Exception:
            pass
        tier.templates.append(TemplateMatch(
            name=template_name,
            template_image=tmpl_rel,
            score=round(float(score), 3),
            threshold=round(float(threshold), 3),
            hit=hit,
            bbox=list(bbox) if bbox else None,
            scale=round(float(scale), 3),
        ))
        return self

    # ────── YOLO 标注图 ──────

    def save_yolo_annot(self, tier: TierRecord, screenshot: np.ndarray,
                        detections: list[YoloDetection], q: int = 70):
        """画 YOLO bbox 到截图副本"""
        if screenshot is None:
            return self
        annot = screenshot.copy()
        for det in detections:
            x1, y1, x2, y2 = [int(v) for v in det.bbox]
            color = (0, 0, 255) if det.cls == "close_x" else (0, 255, 255)
            cv2.rectangle(annot, (x1, y1), (x2, y2), color, 2)
            label = f"{det.cls} {det.conf:.2f}"
            cv2.putText(annot, label, (x1, max(20, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        try:
            cv2.imwrite(str(self.path / "yolo_annot.jpg"), annot,
                        [cv2.IMWRITE_JPEG_QUALITY, q])
            tier.yolo_annot_image = "yolo_annot.jpg"
            tier.yolo_detections = detections
        except Exception:
            pass
        return self

    # ────── OCR ROI 标注 ──────

    def save_ocr_roi(self, tier: TierRecord, screenshot: np.ndarray,
                     roi: Optional[list] = None, hits: Optional[list] = None, q: int = 70):
        """
        OCR 是局部的话，把 ROI 区域框出来 + 内部识别文字标在画面上
        roi: [x1,y1,x2,y2] 全屏坐标
        hits: list[OcrHit]
        """
        if screenshot is None:
            return self
        annot = screenshot.copy()
        if roi:
            x1, y1, x2, y2 = [int(v) for v in roi]
            cv2.rectangle(annot, (x1, y1), (x2, y2), (255, 200, 0), 3)
            cv2.putText(annot, "OCR ROI", (x1, max(20, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 200, 0), 2)
        if hits:
            for h in hits:
                if not h.bbox or len(h.bbox) != 4:
                    continue
                hx1, hy1, hx2, hy2 = [int(v) for v in h.bbox]
                cv2.rectangle(annot, (hx1, hy1), (hx2, hy2), (0, 255, 0), 1)
                cv2.putText(annot, h.text[:14], (hx1, max(15, hy1 - 4)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        try:
            cv2.imwrite(str(self.path / "ocr_annot.jpg"), annot,
                        [cv2.IMWRITE_JPEG_QUALITY, q])
            tier.ocr_roi_image = "ocr_annot.jpg"
            tier.ocr_roi = list(roi) if roi else None
            tier.ocr_hits = hits or []
        except Exception:
            pass
        return self

    # ────── 点击 ──────

    def set_tap(self, x: int, y: int, method: str, target_class: str = "",
                target_text: str = "", target_conf: float = 0.0,
                screenshot: Optional[np.ndarray] = None):
        self.tap = TapRecord(int(x), int(y), method, target_class, target_text,
                              float(target_conf))
        if screenshot is not None:
            annot = screenshot.copy()
            cv2.circle(annot, (int(x), int(y)), 36, (0, 0, 255), 3)
            cv2.circle(annot, (int(x), int(y)), 6, (0, 0, 255), -1)
            label = f"TAP {method}"
            if target_text:
                label += f" '{target_text[:10]}'"
            cv2.putText(annot, label, (int(x) + 40, int(y) - 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            try:
                cv2.imwrite(str(self.path / "tap_annot.jpg"), annot,
                            [cv2.IMWRITE_JPEG_QUALITY, 70])
                self.tap.annot_image = "tap_annot.jpg"
            except Exception:
                pass
        return self

    # ────── 验证 ──────

    def set_verify(self, before: str, after: str, distance: int):
        self.verify = VerifyRecord(before, after, distance,
                                   success=(distance > 5))
        return self

    # ────── 收尾 ──────

    def finalize(self, outcome: str = "", note: str = ""):
        """写 decision.json + 进索引"""
        self.outcome = outcome
        if note:
            self.note = note
        data = {
            "id": self.id,
            "instance": self.instance,
            "phase": self.phase,
            "round": self.round,
            "created": self.created,
            "input_image": self.input_image,
            "input_phash": self.input_phash,
            "input_w": self.input_w,
            "input_h": self.input_h,
            "tiers": [_serialize_tier(t) for t in self.tiers],
            "tap": asdict(self.tap) if self.tap else None,
            "verify": asdict(self.verify) if self.verify else None,
            "outcome": self.outcome,
            "note": self.note,
        }
        try:
            with open(self.path / "decision.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[decision] save json fail: {e}")
        # 加索引
        self.recorder.record_summary({
            "id": self.id,
            "instance": self.instance,
            "phase": self.phase,
            "round": self.round,
            "created": self.created,
            "outcome": self.outcome,
            "tap_method": self.tap.method if self.tap else "",
            "tap_target": self.tap.target_class if self.tap else "",
            "verify_success": self.verify.success if self.verify else None,
            "tier_count": len(self.tiers),
        })
        return self


def _serialize_tier(t: TierRecord) -> dict:
    """把 dataclass list 里嵌套的 dataclass 也序列化"""
    return {
        "tier": t.tier,
        "name": t.name,
        "duration_ms": round(t.duration_ms, 2),
        "early_exit": t.early_exit,
        "note": t.note,
        "templates": [asdict(x) for x in t.templates] if t.templates else [],
        "yolo_detections": [asdict(x) for x in t.yolo_detections] if t.yolo_detections else [],
        "yolo_annot_image": t.yolo_annot_image,
        "ocr_hits": [asdict(x) for x in t.ocr_hits] if t.ocr_hits else [],
        "ocr_roi": t.ocr_roi,
        "ocr_roi_image": t.ocr_roi_image,
        "memory_phash_query": t.memory_phash_query,
        "memory_hit": t.memory_hit,
    }
