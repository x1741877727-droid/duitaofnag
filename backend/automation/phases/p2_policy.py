"""
v3 P2 Policy 层 — 从 Perception 选下一动作, 纯函数, 无副作用.

优先级 (排黑名单后):
  1. memory_hit (历史成功坐标, phash dist<5) → tap, expect popup_dismissed
  2. yolo close_x (conf>0.5) → tap close_x, expect popup_dismissed
  3. yolo action_btn (conf>0.6) → tap action_btn, expect popup_dismissed
  4. None → 让 P2SubFSM 进守门 (lobby quad / login timeout)

模板兜底已下线 (close_x_* / dismiss_btn) — 模板坐标错位/精度不稳, 改走 YOLO 主线 + Memory.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..phase_base import PhaseAction, RunContext
from .p2_perception import NAV_WORDS, Perception

logger = logging.getLogger(__name__)


# action_btn OCR 安全词 (这些词出现 → 这是真要点的按钮). 现 policy 简化为 YOLO conf 阈值,
# OCR 验证留给 P2SubFSM 守门后续阶段 (避免 policy 跑同步 OCR 阻塞).
SAFE_ACTION_WORDS = (
    "确定", "确认", "同意", "知道了", "好的", "继续",
    "收下", "领取", "立即领取", "立即砍价", "我要参与",
)

ACTION_BTN_CONF_THRESHOLD = 0.6   # YOLO action_btn 直接 tap 的最小置信度


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
                label="memory_hit",
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

    # ── 优先级 3: YOLO action_btn (conf > 0.6 直接 tap) ──
    # 这类按钮通常带文字 ("确定/同意/知道了"), 用 conf 阈值卡掉低质量误检.
    # 风险: 登录页 "QQ登录" 之类的按钮也是 action_btn — 由 P2SubFSM 守门 (login timeout) 兜底.
    action_btns = getattr(p, "yolo_action_btns", None) or []
    for det in sorted(action_btns, key=lambda d: -d.conf):
        if det.conf < ACTION_BTN_CONF_THRESHOLD:
            break
        if not ctx.is_blacklisted(det.cx, det.cy):
            return PhaseAction(
                kind="tap",
                x=det.cx, y=det.cy,
                seconds=0.5,                  # action 类弹窗按掉后画面切换稍慢
                label="action_btn",
                expectation="popup_dismissed",
                payload={"yolo_before": p.yolo_dets_raw, "conf": det.conf},
            )

    # ── 模板兜底已下线 ──
    # template close_x / template dismiss_btn 坐标错位严重 (实测 R30+ tap 离按钮真位 ~500px),
    # 暂时取消, 改靠 Memory + YOLO 主线 + 守门. 模板素材 / ROI 修好之后再考虑接回.

    return None
