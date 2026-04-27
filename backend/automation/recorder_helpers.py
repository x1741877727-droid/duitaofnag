"""
recorder_helpers — 把 Phase 产生的中间数据翻译成 decision_log 的 TierRecord.

当前主要给 P2 SubFSM 用: 把 Perception 8 字段拆成 5 层 Tier.
后续 P0/P1/P3a/P3b/P4 各自的 perception 也通过这里复用.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional

from .decision_log import (
    OcrHit, TemplateMatch, TierRecord, YoloDetection,
)

logger = logging.getLogger(__name__)


# Tier 编号 / 名称约定 (中文术语表)
TIER_TEMPLATE = (0, "模板")
TIER_MEMORY = (1, "记忆")
TIER_YOLO = (2, "YOLO")
TIER_OCR = (3, "文字")
TIER_VLM = (4, "视觉模型")


def _hit_bbox(hit: Any) -> Optional[list]:
    """从 MatchHit 取 bbox [x1,y1,x2,y2]. 若没有则用 cx/cy 推一个 64x64."""
    if hit is None:
        return None
    bbox = getattr(hit, "bbox", None)
    if bbox and len(bbox) == 4:
        return [int(v) for v in bbox]
    cx = int(getattr(hit, "cx", 0))
    cy = int(getattr(hit, "cy", 0))
    if cx or cy:
        return [cx - 32, cy - 32, cx + 32, cy + 32]
    return None


def _tier_template_from_perception(p: Any, started: float) -> TierRecord:
    """模板层: lobby_start_btn / lobby_login_btn / template_close_x_*"""
    t = TierRecord(tier=TIER_TEMPLATE[0], name=TIER_TEMPLATE[1])
    hit_any = False

    # 1) 大厅模板
    if getattr(p, "lobby_template_hit", None) is not None:
        h = p.lobby_template_hit
        score = float(getattr(h, "confidence", getattr(h, "score", 0.0)))
        t.templates.append(TemplateMatch(
            name=getattr(h, "name", "lobby_start_btn"),
            score=round(score, 3),
            threshold=0.75,
            hit=True,
            bbox=_hit_bbox(h),
            scale=float(getattr(h, "scale", 1.0)),
        ))
        hit_any = True

    # 2) 登录页
    if getattr(p, "login_template_hit", None) is not None:
        h = p.login_template_hit
        score = float(getattr(h, "confidence", getattr(h, "score", 0.0)))
        t.templates.append(TemplateMatch(
            name=getattr(h, "name", "lobby_login_btn"),
            score=round(score, 3),
            threshold=0.80,
            hit=True,
            bbox=_hit_bbox(h),
            scale=float(getattr(h, "scale", 1.0)),
        ))
        hit_any = True

    # 3) close_x_* 兜底
    tcx = getattr(p, "template_close_x", None)
    if tcx is not None:
        try:
            tn, h = tcx
            score = float(getattr(h, "confidence", getattr(h, "score", 0.0)))
            t.templates.append(TemplateMatch(
                name=tn,
                score=round(score, 3),
                threshold=0.80,
                hit=True,
                bbox=_hit_bbox(h),
                scale=float(getattr(h, "scale", 1.0)),
            ))
            hit_any = True
        except Exception:
            pass

    # 3.5) btn_confirm_* / btn_agree / btn_no_need 兜底 (无 X 弹窗)
    tdb = getattr(p, "template_dismiss_btn", None)
    if tdb is not None:
        try:
            tn, h = tdb
            score = float(getattr(h, "confidence", getattr(h, "score", 0.0)))
            t.templates.append(TemplateMatch(
                name=tn,
                score=round(score, 3),
                threshold=0.80,
                hit=True,
                bbox=_hit_bbox(h),
                scale=float(getattr(h, "scale", 1.0)),
            ))
            hit_any = True
        except Exception:
            pass

    quad = getattr(p, "quad_lobby_confirmed", False)
    quad_note = getattr(p, "quad_note", "")
    if quad:
        t.note = f"四元判大厅: ✓ ({quad_note})"
    elif hit_any:
        t.note = f"模板命中, 但四元未判大厅 ({quad_note})"
    else:
        t.note = f"模板无命中 ({quad_note})" if quad_note else "模板无命中"

    t.early_exit = quad
    t.duration_ms = round((time.perf_counter() - started) * 1000, 2)
    return t


def _tier_memory_from_perception(p: Any) -> TierRecord:
    t = TierRecord(tier=TIER_MEMORY[0], name=TIER_MEMORY[1])
    mem_hit = getattr(p, "memory_hit", None)
    phash = getattr(p, "phash_now", 0)
    t.memory_phash_query = f"0x{int(phash):016x}" if phash else ""
    if mem_hit is not None:
        cx = int(getattr(mem_hit, "cx", 0))
        cy = int(getattr(mem_hit, "cy", 0))
        note = getattr(mem_hit, "note", "")
        t.memory_hit = {
            "cx": cx, "cy": cy,
            "phash": str(getattr(mem_hit, "phash", "")),
            "note": str(note),
            "success": True,
        }
        t.note = f"记忆命中 @ ({cx},{cy})"
        t.early_exit = True
    else:
        t.note = "无记忆 (phash dist > 5)"
    return t


def _tier_yolo_from_perception(p: Any) -> TierRecord:
    t = TierRecord(tier=TIER_YOLO[0], name=TIER_YOLO[1])
    raw = list(getattr(p, "yolo_dets_raw", []) or [])
    for d in raw:
        try:
            x1 = int(getattr(d, "cx", 0)) - int(getattr(d, "w", 64) // 2 if hasattr(d, "w") else 32)
            y1 = int(getattr(d, "cy", 0)) - int(getattr(d, "h", 64) // 2 if hasattr(d, "h") else 32)
            bbox = getattr(d, "bbox", None)
            if bbox and len(bbox) == 4:
                bbox = [int(v) for v in bbox]
            else:
                bbox = [x1, y1, x1 + 64, y1 + 64]
            t.yolo_detections.append(YoloDetection(
                cls=str(getattr(d, "name", "")),
                conf=round(float(getattr(d, "conf", 0.0)), 3),
                bbox=bbox,
            ))
        except Exception:
            continue
    n_close = len(getattr(p, "yolo_close_xs", []) or [])
    n_act = len(getattr(p, "yolo_action_btns", []) or [])
    if raw:
        t.note = f"YOLO {len(raw)} dets (close_x×{n_close}, action_btn×{n_act})"
    else:
        t.note = "YOLO 无检出"
    return t


def _tier_ocr_placeholder() -> TierRecord:
    t = TierRecord(tier=TIER_OCR[0], name=TIER_OCR[1])
    t.note = "未触发 (P2 主流程不跑 OCR)"
    return t


def _tier_vlm_placeholder() -> TierRecord:
    t = TierRecord(tier=TIER_VLM[0], name=TIER_VLM[1])
    t.note = "未触发 (VLM 未部署)"
    return t


def record_perception(decision: Any, p: Any, *, started: Optional[float] = None) -> None:
    """把一份 Perception 拆成 5 层 Tier 添加到 decision.

    decision: decision_log.Decision (或 _NullDecision)
    p: p2_perception.Perception
    """
    if decision is None or p is None:
        return
    started = started if started is not None else time.perf_counter()
    try:
        decision.add_tier(_tier_template_from_perception(p, started))
        decision.add_tier(_tier_memory_from_perception(p))
        decision.add_tier(_tier_yolo_from_perception(p))
        decision.add_tier(_tier_ocr_placeholder())
        decision.add_tier(_tier_vlm_placeholder())
    except Exception as e:
        logger.debug(f"[recorder_helpers] record_perception err: {e}")


def record_signal_tier(decision: Any, *, name: str, hit: bool, note: str = "",
                       duration_ms: float = 0.0, tier_idx: int = 0) -> None:
    """通用单层 Tier 记录 (P0/P1/P3a/P3b/P4 用).

    name: '模板'/'YOLO'/'VPN检测'/'OCR' 等
    hit:  本层是否拍板 (early_exit)
    note: 一行说明
    """
    if decision is None:
        return
    try:
        t = TierRecord(tier=int(tier_idx), name=name)
        t.early_exit = bool(hit)
        t.note = note
        t.duration_ms = round(float(duration_ms), 2)
        decision.add_tier(t)
    except Exception as e:
        logger.debug(f"[recorder_helpers] record_signal_tier err: {e}")
