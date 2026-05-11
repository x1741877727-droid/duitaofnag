"""邀请关闭 middleware — 好友/队伍/公会邀请弹窗自动关.

REVIEW_DAY3_WATCHDOG.md 推荐: V1 P5 内置, V2 提取到 middleware 跨所有 phase.

骨架版本 (Day 3): 接口完整, 业务逻辑 TODO 留接口.
用户原话 (2026-05-11): "有些检测我也不清楚整个业务怎么做的, 这种地方留着就行"

接入业务时填:
1. _detect_invite_dialog(ctx, shot) — 怎么检测邀请弹窗
   候选实现:
   - YOLO 检测 'invite_popup' class (需训模型)
   - OCR 关键词匹配 ("邀请你加入队伍" 等)
   - 模板匹配 (邀请框 close x 模板)
2. _dismiss_invite(ctx, dialog) — 怎么关掉
   候选实现:
   - tap close x 位置
   - tap "拒绝" 按钮
   - 返键
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from ..ctx import RunContext
from .base import BeforeRoundResult

logger = logging.getLogger(__name__)

# 节流: 同实例 0.8s 内不重复关 (防连发多次 tap 同位置)
_THROTTLE_S = 0.8


class _V1CtxProxy:
    """喂给 v1 dismiss_known_popups 的最小 ctx-like 对象.

    v1 popup_dismiss 读 ctx.runner.{adb, yolo_dismisser, ocr_dismisser, _popup_tracker}.
    我们只需把 v1 SingleInstanceRunner 包成 ctx.runner = v1_runner 形式.
    """
    def __init__(self, v1_runner, instance_idx: int):
        self.runner = v1_runner
        self.instance_idx = instance_idx


class InviteDismissMiddleware:
    """所有 phase 启用. 每 round 检测邀请弹窗, 自动关闭."""

    name = "invite_dismiss"

    def __init__(self):
        self._last_dismiss_ts: dict[int, float] = {}   # inst_idx → 上次关闭时间

    def enable_for(self, phase_name: str) -> bool:
        """所有 phase 启用 (P0 也启用 — 启动加速器期间可能弹邀请)."""
        return True

    async def before_round(self, ctx: RunContext, shot) -> BeforeRoundResult:
        """每 round 委托 v1 dismiss_known_popups (跑友邀请/网络/account_squeezed 全在 KNOWN_POPUPS)."""
        if shot is None or ctx.v1_runner is None:
            return BeforeRoundResult(intercept=False)

        # 节流 0.8s
        now = time.time()
        last = self._last_dismiss_ts.get(ctx.instance_idx, 0)
        if now - last < _THROTTLE_S:
            return BeforeRoundResult(intercept=False)

        # v1 dismiss_known_popups 读 ctx.runner. 用最小 proxy: 只需 .runner 指 v1.
        proxy = _V1CtxProxy(v1_runner=ctx.v1_runner, instance_idx=ctx.instance_idx)
        try:
            from backend.automation.popup_dismiss import dismiss_known_popups
            from backend.automation.popup_specs import PopupFatalEscalation
        except ImportError as e:
            logger.warning(f"[middleware/invite] v1 popup_dismiss 不可用: {e}")
            return BeforeRoundResult(intercept=False)

        try:
            dismissed = await dismiss_known_popups(
                proxy, pre_shot=shot, current_phase="middleware",
            )
        except PopupFatalEscalation as e:
            logger.warning(f"[middleware/invite] inst{ctx.instance_idx} FATAL: {e.spec_name}")
            # Day 4: fatal 也仅记 log, 不破 phase. Day 5 再接 recovery.
            return BeforeRoundResult(intercept=False, note=f"fatal_{e.spec_name}")
        except Exception as e:
            logger.debug(f"[middleware/invite] inst{ctx.instance_idx} dismiss err: {e}")
            return BeforeRoundResult(intercept=False)

        if dismissed:
            self._last_dismiss_ts[ctx.instance_idx] = now
            logger.info(f"[middleware/invite] inst{ctx.instance_idx} dismissed: {dismissed}")
            return BeforeRoundResult(intercept=True, note=f"dismissed:{dismissed}")
        return BeforeRoundResult(intercept=False)

    async def after_phase(self, ctx: RunContext) -> None:
        """phase 切换时不清状态 (邀请节流跨 phase 仍有效)."""
        pass

    # ─────────── 业务接入点 (TODO) ───────────

    async def _detect_invite_dialog(self, ctx: RunContext, shot) -> Optional[dict]:
        """检测邀请弹窗. 返 dict {x, y, type} 或 None.

        TODO (业务接入):
        - 候选 1: YOLO 检测 'invite_popup' / 'dialog' class
            dets = await ctx.yolo.detect(shot)
            invites = [d for d in dets if d.name == 'invite_popup' and d.conf > 0.6]
            if invites: return {'cx': invites[0].cx, 'cy': invites[0].cy, 'type': 'yolo'}
        - 候选 2: OCR 关键词
            roi = Roi(0.15, 0.30, 0.85, 0.50)
            hits = await ctx.ocr.recognize(shot, roi=roi)
            if any('邀请' in h.text or '加入' in h.text for h in hits):
                return {'cx': ..., 'cy': ..., 'type': 'ocr'}
        - 候选 3: 模板匹配
            hit = await ctx.matcher.match_one(shot, 'invite_close_x', threshold=0.7)
            if hit: return {'cx': hit.cx, 'cy': hit.cy, 'type': 'template'}
        """
        return None    # 业务未接入, 默认无邀请

    async def _dismiss_invite(self, ctx: RunContext, dialog: dict) -> bool:
        """关掉邀请弹窗. 成功 True, 失败 False.

        TODO (业务接入):
        - 优先 tap 拒绝/关闭按钮 (用户业务上不希望接受邀请)
        - 配合 dialog['type'] 不同走不同流程
            if dialog['type'] == 'yolo':
                # close_x bbox 在 dialog['cx'], dialog['cy']
                await ctx.adb.tap(dialog['cx'], dialog['cy'])
            elif dialog['type'] == 'ocr':
                # 找 "拒绝" 文字位置 tap
                ...
        """
        return False   # 业务未接入, 默认未关 (intercept=False 业务正常跑)
