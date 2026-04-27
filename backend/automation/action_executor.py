"""
v3 Action Executor — 把 PhaseAction (纯数据) 翻译成实际 IO + 4 道防线.

负责:
  1. tap (x, y) + sleep
  2. 防线 1 phash 验证 (画面是否变化)
  3. 防线 2 state_expectation 验证 (画面是否朝预期方向变化)
  4. 失败 → ctx.blacklist_coords.append() (本 P2 不再 tap)
  5. 成功 → ctx.pending_memory_writes.append() (P2 success 时 commit)
  6. wait — 单纯 sleep

设计:
  Handler.handle_frame 返回 PhaseStep + PhaseAction (纯逻辑).
  ActionExecutor.apply 集中处理所有 IO + 验证, Handler 不直接调 device.tap.
  这样 4 道防线集中在一处, 不会被 handler 漏掉.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import numpy as np

from .phase_base import PhaseAction, RunContext

logger = logging.getLogger(__name__)


class ActionExecutor:
    """把 PhaseAction 翻译成实际 IO. 全部静态方法, 无状态."""

    @staticmethod
    async def apply(ctx: RunContext, act: PhaseAction) -> bool:
        """实施 act. 返回 True = 行为完成 (无论 verify 成功/失败).

        tap 后会做防线 1+2 验证, 验证失败把坐标加进会话黑名单.
        verify 成功 → 缓冲到 pending_memory_writes (P2 success 时 commit).
        """
        if act.kind == "noop":
            return True

        if act.kind == "wait":
            if act.seconds > 0:
                await asyncio.sleep(act.seconds)
            return True

        if act.kind == "tap":
            return await ActionExecutor._do_tap(ctx, act)

        logger.warning(f"[executor] 未知 action.kind={act.kind!r}, noop")
        return True

    @staticmethod
    async def _do_tap(ctx: RunContext, act: PhaseAction) -> bool:
        """tap + 防线 1+2 验证. 失败的坐标加进 ctx.blacklist_coords."""
        shot_before = ctx.current_shot
        cx, cy = act.x, act.y

        # 黑名单防御 (Handler 应已过滤, 这里再兜一次)
        if ctx.is_blacklisted(cx, cy):
            logger.debug(f"[executor] tap 跳过黑名单 ({cx},{cy})")
            return True

        # 实际 tap
        try:
            await ctx.device.tap(cx, cy)
        except Exception as e:
            logger.warning(f"[executor] device.tap({cx},{cy}) 失败: {e}")
            return True
        ctx.last_tap_xy = (cx, cy)

        # tap 后等待 (默认 0.4s, 让画面有时间响应)
        await asyncio.sleep(act.seconds if act.seconds > 0 else 0.4)

        # 没要求 verify → 跳过验证 (但仍记录到 pending_memory)
        if not act.expectation:
            if ctx.memory is not None and act.label and act.label != "memory_hit":
                ctx.pending_memory_writes.append((shot_before, (cx, cy), act.label))
            return True

        # 取 after 帧
        try:
            shot_after = await ctx.device.screenshot()
        except Exception as e:
            logger.debug(f"[executor] verify 截图失败: {e}")
            return True
        if shot_after is None:
            return True

        # 防线 1+2: state_expectation 综合判定 (内部含 phash + 自定义 verifier)
        try:
            from .state_expectation import verify as _verify
            verify_ctx = dict(act.payload or {})
            verify_ctx.setdefault("matcher", ctx.matcher)
            exp_r = _verify(act.expectation, shot_before, shot_after, verify_ctx)
        except Exception as e:
            logger.debug(f"[executor] state_expectation.verify err: {e}")
            return True

        if exp_r.matched:
            # 成功 → 缓冲到 pending memory (P2 success 时 commit, 避免错坐标污染)
            if (ctx.memory is not None and act.label
                    and act.label != "memory_hit"):
                # 去重: 已 buffer 同 method 同坐标 (距离<30) → 跳过
                already = any(
                    m == act.label and abs(ax - cx) < 30 and abs(ay - cy) < 30
                    for (_f, (ax, ay), m) in ctx.pending_memory_writes
                )
                if not already:
                    ctx.pending_memory_writes.append(
                        (shot_before, (cx, cy), act.label)
                    )
                    logger.info(
                        f"[executor] 🧠 Memory 缓冲 ({cx},{cy}) label={act.label} "
                        f"(buffer={len(ctx.pending_memory_writes)})"
                    )
        else:
            # 失败 → 加会话黑名单 + Memory 衰减 (失败计数++)
            if not ctx.is_blacklisted(cx, cy):
                ctx.blacklist_coords.append((cx, cy))
                logger.warning(
                    f"[executor] State Expectation 失败 [{act.expectation}] @ "
                    f"({cx},{cy}): {exp_r.note} → 加黑名单 "
                    f"(size={len(ctx.blacklist_coords)})"
                )
            if ctx.memory is not None and act.label:
                try:
                    ctx.memory.remember(
                        shot_before, target_name="dismiss_popups",
                        action_xy=(cx, cy), success=False,
                    )
                except Exception:
                    pass

        return True

    @staticmethod
    def commit_pending_memory(ctx: RunContext) -> int:
        """P2 success 时回放 pending_memory_writes 全部 commit. 返回 commit 条数."""
        if ctx.memory is None or not ctx.pending_memory_writes:
            return 0
        n = 0
        for (frame, axy, label) in ctx.pending_memory_writes:
            try:
                ctx.memory.remember(
                    frame, target_name="dismiss_popups",
                    action_xy=axy, success=True,
                )
                n += 1
            except Exception as e:
                logger.debug(f"[executor] commit_memory err: {e}")
        ctx.pending_memory_writes.clear()
        return n
