"""Stage 1 helper — dismiss_known_popups 入口.

每个长 phase 主 loop 调一次:
    dismissed = await dismiss_known_popups(ctx, yolo_dets=...)
    if dismissed:
        continue   # 这一轮 wasted, 下一轮重新观察

调用流程 (位置无关 — 全靠 YOLO dialog bbox + OCR 关键词):
  第 1 层: YOLO close_x/dialog 触发 (复用 caller 的 yolo_dets, 0 额外开销)
          没疑似弹窗 → return None
  第 2 层: 对每个 YOLO dialog bbox, 内部 OCR; 按 KNOWN_POPUPS 顺序匹配
          anchor_keywords + co_occurrence, 第一个命中 → dismiss → return spec.name

致命弹窗: tracker.is_fatal(spec) True → 抛 PopupFatalEscalation, 上层 phase handler
catch 后走 FAIL + recovery 流程.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import numpy as np

from .popup_specs import (
    KNOWN_POPUPS, PopupSpec, DismissalTracker, PopupFatalEscalation,
)

logger = logging.getLogger(__name__)


async def dismiss_known_popups(
    ctx,
    *,
    yolo_dets: Optional[list] = None,
    pre_shot: Optional[np.ndarray] = None,
    exclude_names: Optional[set] = None,
    current_phase: str = "all",
) -> Optional[str]:
    """从 KNOWN_POPUPS 顺序匹配, 命中 dismiss 一个就返回 spec.name.

    Args:
        ctx: RunContext
        yolo_dets: caller 已跑过的 YOLO Detection list, 没传就内部跑一次
        pre_shot: caller 已截过的图, 没传就内部截
        exclude_names: 当前 phase 不希望被 dismiss 的 spec 名 (防误关自己 UI)
        current_phase: 当前 phase 名 (e.g. "P5"), 用于过滤 phases_active/excluded_phases

    Returns:
        None: 没弹窗, 业务流照常
        str: spec.name, 已 dismiss

    Raises:
        PopupFatalEscalation: 致命弹窗触发, 上层捕获走 recovery
    """
    runner = ctx.runner
    tracker = _get_or_create_tracker(runner)

    # ─── 第 1 层: YOLO 预过滤 ───
    shot = pre_shot
    if yolo_dets is None:
        if shot is None:
            shot = await runner.adb.screenshot()
        if shot is None:
            return None
        yolo_dets = await _yolo_detect_all(runner, shot)

    # 拿出所有 dialog bbox; 没 dialog 也没 close_x 就 0 开销退出
    dialog_dets = [d for d in (yolo_dets or [])
                   if getattr(d, "name", "") == "dialog"]
    has_close_x = any(getattr(d, "name", "") == "close_x"
                      for d in (yolo_dets or []))
    if not dialog_dets and not has_close_x:
        return None

    if shot is None:
        shot = await runner.adb.screenshot()
    if shot is None:
        return None

    ocr = getattr(runner, "ocr_dismisser", None)
    if ocr is None:
        logger.warning("[popup] runner 无 ocr_dismisser, 跳过弹窗清理")
        return None

    # ─── 第 2 层: 逐个 dialog bbox OCR + 跑 KNOWN_POPUPS 匹配 ───
    # 大 dialog 先, 因为弹窗叠加时大的更可能是当前焦点
    dialog_dets_sorted = sorted(
        dialog_dets,
        key=lambda d: (d.x2 - d.x1) * (d.y2 - d.y1),
        reverse=True,
    )

    # 兜底: 没 dialog 检测到, 但有 close_x → 全屏 OCR (close_x 周围)
    # 这种少见, 走这条路时性能稍差, 但保证 close_x 也能识别成弹窗
    if not dialog_dets_sorted:
        ocr_hits_full = await _ocr_full_screen(ocr, shot)
        spec_match = await _try_match_specs(
            spec_list=KNOWN_POPUPS,
            ocr_hits=ocr_hits_full,
            tracker=tracker,
            exclude_names=exclude_names,
            current_phase=current_phase,
        )
        if spec_match is not None:
            spec, dismiss_xy = spec_match
            return await _execute_dismiss_or_fatal(
                ctx, spec, dismiss_xy, shot, ocr_hits_full, tracker, current_phase)
        return None

    # 主路径: 对每个 dialog bbox 内部 OCR + 匹配
    for d_det in dialog_dets_sorted:
        ocr_hits = await _ocr_inside_bbox(ocr, shot, d_det)
        if not ocr_hits:
            continue
        spec_match = await _try_match_specs(
            spec_list=KNOWN_POPUPS,
            ocr_hits=ocr_hits,
            tracker=tracker,
            exclude_names=exclude_names,
            current_phase=current_phase,
        )
        if spec_match is None:
            continue
        spec, dismiss_xy = spec_match
        return await _execute_dismiss_or_fatal(
            ctx, spec, dismiss_xy, shot, ocr_hits, tracker, current_phase)

    return None


# ──────────────────────────── 私有 helpers ────────────────────────────

def _get_or_create_tracker(runner) -> DismissalTracker:
    tracker = getattr(runner, "_popup_tracker", None)
    if tracker is None:
        tracker = DismissalTracker()
        runner._popup_tracker = tracker
    return tracker


async def _yolo_detect_all(runner, shot) -> list:
    yolo = getattr(runner, "yolo_dismisser", None)
    if yolo is None or not yolo.is_available():
        return []
    try:
        return await asyncio.to_thread(yolo.detect, shot) or []
    except Exception as e:
        logger.debug(f"[popup] yolo detect err: {e}")
        return []


async def _ocr_inside_bbox(ocr, shot: np.ndarray, det) -> list:
    """在 YOLO det 的 bbox 内部跑 OCR, 转比例坐标喂 _ocr_roi."""
    h, w = shot.shape[:2]
    x1 = max(0, int(det.x1))
    y1 = max(0, int(det.y1))
    x2 = min(w, int(det.x2))
    y2 = min(h, int(det.y2))
    if x2 - x1 < 10 or y2 - y1 < 10:
        return []
    try:
        return await asyncio.to_thread(
            ocr._ocr_roi, shot, x1 / w, y1 / h, x2 / w, y2 / h) or []
    except Exception as e:
        logger.debug(f"[popup] OCR dialog bbox err: {e}")
        return []


async def _ocr_full_screen(ocr, shot: np.ndarray) -> list:
    """兜底: 全屏 OCR (只在没 dialog 但有 close_x 时用)."""
    try:
        return await asyncio.to_thread(
            ocr._ocr_roi, shot, 0.0, 0.0, 1.0, 1.0) or []
    except Exception as e:
        logger.debug(f"[popup] OCR full screen err: {e}")
        return []


def _spec_applicable(spec: PopupSpec, current_phase: str,
                     exclude_names: Optional[set]) -> bool:
    if exclude_names and spec.name in exclude_names:
        return False
    if "all" not in spec.phases_active and current_phase not in spec.phases_active:
        return False
    if current_phase in spec.excluded_phases:
        return False
    return True


def _check_anchor_and_cooccurrence(spec: PopupSpec, ocr_hits: list) -> bool:
    if not ocr_hits:
        return False
    texts = [(getattr(h, "text", "") or "") for h in ocr_hits]
    if not any(any(kw in t for kw in spec.anchor_keywords) for t in texts):
        return False
    for co in spec.co_occurrence:
        if not any(co in t for t in texts):
            return False
    return True


def _find_dismiss_target(spec: PopupSpec, ocr_hits: list):
    """OCR 命中里找跟 spec.dismiss_value 完全相等或包含的那条."""
    target = spec.dismiss_value
    if not target:
        return None
    for h in ocr_hits:
        t = (getattr(h, "text", "") or "").strip()
        if t == target:
            return (getattr(h, "cx", 0), getattr(h, "cy", 0))
    for h in ocr_hits:
        t = (getattr(h, "text", "") or "").strip()
        if target in t:
            return (getattr(h, "cx", 0), getattr(h, "cy", 0))
    return None


async def _try_match_specs(
    *,
    spec_list: list,
    ocr_hits: list,
    tracker: DismissalTracker,
    exclude_names: Optional[set],
    current_phase: str,
):
    """对一组 OCR hits, 按 spec_list 顺序找第一个匹配的. 返回 (spec, dismiss_xy) or None."""
    for spec in spec_list:
        if not _spec_applicable(spec, current_phase, exclude_names):
            continue
        if not tracker.can_dismiss(spec):
            continue
        if not _check_anchor_and_cooccurrence(spec, ocr_hits):
            continue
        dismiss_xy = _find_dismiss_target(spec, ocr_hits)
        if dismiss_xy is None:
            logger.warning(
                f"[popup] {spec.name} anchor 命中但找不到 dismiss '{spec.dismiss_value}'")
            continue
        return (spec, dismiss_xy)
    return None


async def _execute_dismiss_or_fatal(
    ctx, spec: PopupSpec, dismiss_xy, shot, ocr_hits,
    tracker: DismissalTracker, current_phase: str,
) -> str:
    """记录 + 致命 check + tap dismiss. 返回 spec.name."""
    tracker.record(spec)
    if tracker.is_fatal(spec):
        count = tracker.count_in_window(spec)
        logger.warning(
            f"[popup] {spec.name} FATAL: 触发 {count} 次 in {spec.fatal_window_s}s 窗口")
        await _record_fatal_decision(ctx, spec, count, shot, current_phase)
        raise PopupFatalEscalation(spec.name, count)

    logger.info(
        f"[popup] dismiss {spec.name} → tap '{spec.dismiss_value}' @ {dismiss_xy}")
    await _record_dismiss_decision(
        ctx, spec, dismiss_xy, shot, ocr_hits, current_phase)
    runner = ctx.runner
    await runner.adb.tap(int(dismiss_xy[0]), int(dismiss_xy[1]))
    await asyncio.sleep(0.3)  # 短等画面变化
    return spec.name


async def _record_dismiss_decision(ctx, spec: PopupSpec, dismiss_xy,
                                    shot, ocr_hits, current_phase: str) -> None:
    from .decision_log import get_recorder
    from .recorder_helpers import record_signal_tier
    from .adb_lite import phash as _phash

    recorder = get_recorder()
    phase_name = current_phase if current_phase != "all" else "popup"
    dec = await asyncio.to_thread(
        recorder.new_decision, ctx.instance_idx, phase_name, 8000)
    try:
        ph = _phash(shot)
        ph_str = f"0x{int(ph):016x}" if ph else ""
        dec.set_input(shot, ph_str, q=70)
    except Exception:
        pass

    ocr_summary = ",".join(
        (getattr(h, "text", "") or "")[:10] for h in (ocr_hits or [])[:5])[:80]
    record_signal_tier(
        dec, name=f"PopupClose·{spec.name}", hit=True, tier_idx=4,
        note=f"识别到 {spec.name}, OCR: '{ocr_summary}' → tap '{spec.dismiss_value}' @ {dismiss_xy}")
    try:
        dec.set_tap(int(dismiss_xy[0]), int(dismiss_xy[1]),
                    method=f"popup_{spec.name}", screenshot=shot)
    except Exception:
        pass
    await asyncio.to_thread(dec.finalize, "popup_dismissed", spec.name)


async def _record_fatal_decision(ctx, spec: PopupSpec, count: int,
                                  shot, current_phase: str) -> None:
    from .decision_log import get_recorder
    from .recorder_helpers import record_signal_tier
    from .adb_lite import phash as _phash

    recorder = get_recorder()
    phase_name = current_phase if current_phase != "all" else "popup"
    dec = await asyncio.to_thread(
        recorder.new_decision, ctx.instance_idx, phase_name, 8999)
    try:
        ph = _phash(shot)
        ph_str = f"0x{int(ph):016x}" if ph else ""
        dec.set_input(shot, ph_str, q=70)
    except Exception:
        pass
    record_signal_tier(
        dec, name=f"FATAL·{spec.name}", hit=False, tier_idx=4,
        note=f"FATAL: {spec.name} 在 {spec.fatal_window_s}s 窗口内触发 {count} 次 ≥ 阈值 {spec.fatal_threshold} → 上抛 PopupFatalEscalation")
    await asyncio.to_thread(dec.finalize, "popup_fatal", f"{spec.name} x{count}")
