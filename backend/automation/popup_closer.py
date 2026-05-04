"""
PopupCloser — 弹窗清理的可复用入口.

设计:
  - P2 是"循环关弹窗直到大厅"的主消费者, 但它需要单独读 Perception
    (用 quad_lobby_confirmed / login_template_hit 守门), 直接用 perceive+decide
    更顺手, 不强制走这里.
  - P3a/P3b/P4 等非 P2 phase 中途撞上弹窗时 (e.g. 切组队码 tab 弹活动公告),
    只想知道"这一帧有弹窗吗 → 给我 tap 哪里", 用 find_target() 一行搞定.

API:
  PopupCloser.find_target(ctx) -> Optional[PhaseAction]
      None = 没目标可点, 上层自己决定怎么办 (重试 / 兜底 / 退出).
      不为 None: 调用方 await ActionExecutor.apply(ctx, action) 即可.

并不打算把 P2 的"5 源融合 + 4 道防线" 全套搬过来. 这里就是 perceive + decide
两步薄壳; 复杂度同 P2.
"""

from __future__ import annotations

import logging
from typing import Optional

from .phase_base import PhaseAction, RunContext

logger = logging.getLogger(__name__)


class PopupCloser:
    """弹窗清理薄壳. 复用 P2 的 perceive + decide.

    用法 (非 P2 phase 中途见到弹窗时):
        action = await PopupCloser.find_target(ctx)
        if action is not None:
            await ActionExecutor.apply(ctx, action)
            # 等动作生效, 再继续主流程
    """

    @staticmethod
    async def find_target(ctx: RunContext) -> Optional[PhaseAction]:
        """跑一帧 perceive + decide, 返回弹窗 tap 动作 / None.

        要求 ctx.current_shot 已经塞好 + matcher / yolo / memory / lobby_detector
        都连上 (跟 P2 同样的依赖).
        """
        # 延迟导入: popup_closer 是底层 util, 被 phases.* 引用; phases 会反向
        # 引用 popup_closer 时不能在模块顶部循环依赖.
        from .phases.p2_perception import perceive
        from .phases.p2_policy import decide

        try:
            p = await perceive(ctx)
        except Exception as e:
            logger.warning(f"[PopupCloser] perceive 失败: {e}")
            return None

        return decide(p, ctx)

    @staticmethod
    async def wait_for_lobby_clearing_popups(
        ctx: RunContext,
        max_attempts: int = 8,
        interval_s: float = 0.5,
    ) -> "ScreenKind":
        """P3a/P3b/P4 入口守门替代 wait_for_kind: 看到 POPUP 不直接 FAIL,
        用 PopupCloser 把它点掉再重 classify, 最多 max_attempts 次循环.

        替代场景: P2 退出后 UE4 延迟弹窗 (周年庆 / 活动公告) 才冒出来,
        P3a 撞上必需就地清, 不能甩回 P2 / 直接 FAIL.

        max_attempts × interval_s = 总等待预算. 默认 8×0.5s = 4s.

        返回最后一次 classify 的 ScreenKind. 调用方判 != LOBBY 决定 FAIL/RETRY.
        """
        import asyncio
        from .screen_classifier import ScreenKind, classify_from_frame
        from .action_executor import ActionExecutor

        last = ScreenKind.UNKNOWN
        for i in range(max_attempts):
            try:
                shot = await ctx.device.screenshot()
            except Exception:
                shot = None
            if shot is not None:
                ctx.current_shot = shot
                last = await classify_from_frame(shot, ctx.yolo, ctx.matcher)
                if last == ScreenKind.LOBBY:
                    return last
                if last == ScreenKind.POPUP:
                    # 内联清弹窗: PopupCloser → ActionExecutor 走完整 verify 链路
                    try:
                        action = await PopupCloser.find_target(ctx)
                    except Exception as e:
                        logger.debug(f"[PopupCloser/wait] find_target err: {e}")
                        action = None
                    if action is not None:
                        try:
                            await ActionExecutor.apply(ctx, action)
                        except Exception as e:
                            logger.debug(f"[PopupCloser/wait] apply err: {e}")
                        # tap 后下个 loop 直接重 classify, 不睡 (carryover_shot 已写)
                        continue
                    # 没找到 target — 当前帧 POPUP 但 closer 找不到点哪儿
                    logger.warning(
                        f"[PopupCloser/wait] kind=POPUP 但 PopupCloser 找不到 target, "
                        f"attempt {i+1}/{max_attempts}"
                    )
            if i < max_attempts - 1:
                await asyncio.sleep(interval_s)
        return last
