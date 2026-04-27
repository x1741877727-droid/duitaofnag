"""
v3 P2 Policy 层 — 从 Perception 选下一动作, 纯函数, 无副作用.

输入:
  Perception (一帧多源识别) + RunContext (黑名单 / pending_memory)

输出:
  Optional[PhaseAction] — None = 没合适目标 (上层进 lobby 守门 / login 守门)

优先级 (排黑名单后):
  1. memory_hit (历史成功坐标, phash dist<5) → tap, expect popup_dismissed
  2. yolo close_x (conf>0.5) → tap close_x, expect popup_dismissed
  3. yolo action_btn (OCR 文字 ∈ 安全词) → tap action_btn, expect 对应词
  4. template close_x_* (YOLO 漏检兜底) → tap, expect popup_dismissed
  5. None → 让 P2SubFSM 进守门 (lobby quad / login timeout)

NOTE: CTA 兜底 (HSV+OCR 找"立即砍价"等) 暂时不放在主 policy 里, 风险高 (登录页误识).
     如要加 CTA, 应在 P2SubFSM 检测到 "outside_lobby + 持续无目标" 时单独开门.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..phase_base import PhaseAction, RunContext
from .p2_perception import NAV_WORDS, Perception

logger = logging.getLogger(__name__)


# action_btn OCR 安全词 (这些词出现 → 这是真要点的按钮)
SAFE_ACTION_WORDS = (
    "确定", "确认", "同意", "知道了", "好的", "继续",
    "收下", "领取", "立即领取", "立即砍价", "我要参与",
)


def decide(p: Perception, ctx: RunContext) -> Optional[PhaseAction]:
    """从 Perception 选下一动作. None = 没合适目标 (上层守门接管)."""

    # ── 优先级 1: Memory L1 命中 (历史成功坐标) ──
    if p.memory_hit is not None:
        cx, cy = p.memory_hit.cx, p.memory_hit.cy
        if not ctx.is_blacklisted(cx, cy):
            note = getattr(p.memory_hit, "note", "")
            return PhaseAction(
                kind="tap",
                x=cx, y=cy,
                seconds=0.4,
                label="memory_hit",          # 不再缓冲 memory (避免自我强化)
                expectation="popup_dismissed",
                payload={
                    "yolo_before": p.yolo_dets_raw,
                    "memory_note": note,
                },
            )

    # ── 优先级 2: YOLO close_x (按 conf 降序选不在黑名单的) ──
    for det in sorted(p.yolo_close_xs, key=lambda d: -d.conf):
        if not ctx.is_blacklisted(det.cx, det.cy):
            return PhaseAction(
                kind="tap",
                x=det.cx, y=det.cy,
                seconds=0.4,
                label="close_x",
                expectation="popup_dismissed",
                payload={"yolo_before": p.yolo_dets_raw},
            )

    # ── 优先级 3: YOLO action_btn (OCR 安全词) ──
    # 这部分需要在 ROI 内做 OCR 验证. policy 是纯函数, 不该跑 OCR.
    # 简化: action_btn conf > 0.6 直接 tap, 用 expect 'popup_dismissed' 验证.
    # 真要 OCR 验证, 应在 perceive 时做好 + 把结果放进 Perception.
    # TODO v3.1: 在 perceive 阶段对 action_btn bbox 做 OCR, 放进 Perception
    # 当前先跳过 action_btn (避免误点), 让模板 + CTA 兜底接管.

    # ── 优先级 4: 模板 close_x_* 兜底 ──
    if p.template_close_x is not None:
        tn, h = p.template_close_x
        if not ctx.is_blacklisted(h.cx, h.cy):
            return PhaseAction(
                kind="tap",
                x=h.cx, y=h.cy,
                seconds=0.4,
                label="template_close_x",
                expectation="popup_dismissed",
                payload={
                    "yolo_before": p.yolo_dets_raw,
                    "template_name": tn,
                },
            )

    # ── 优先级 5: 模板 btn_confirm_* / btn_agree / btn_no_need 兜底 ──
    # (没 X 但有"确定/同意/不需要"按钮的弹窗, 比如"系统内存过低提醒")
    if p.template_dismiss_btn is not None:
        tn, h = p.template_dismiss_btn
        if not ctx.is_blacklisted(h.cx, h.cy):
            return PhaseAction(
                kind="tap",
                x=h.cx, y=h.cy,
                seconds=0.5,                  # 多等 100ms (这类弹窗按掉后画面切换稍慢)
                label="template_dismiss_btn",
                expectation="popup_dismissed",
                payload={
                    "yolo_before": p.yolo_dets_raw,
                    "template_name": tn,
                },
            )

    return None
