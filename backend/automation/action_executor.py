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
        """tap + 防线 1+2 验证. 失败的坐标加进 ctx.blacklist_coords.
        每个内部步骤打 PERF log, 让上游能定位耗时."""
        import time as _time
        _t0 = _time.perf_counter()
        _perf = {"tap": 0.0, "set_tap": 0.0, "sleep": 0.0,
                 "shot_after": 0.0, "phash_set_verify": 0.0, "exp_verify": 0.0}
        shot_before = ctx.current_shot
        cx, cy = act.x, act.y
        inst_idx = getattr(ctx, 'instance_idx', '?')

        # 黑名单防御 (Handler 应已过滤, 这里再兜一次)
        if ctx.is_blacklisted(cx, cy):
            logger.debug(f"[executor] tap 跳过黑名单 ({cx},{cy})")
            return True

        # 实际 tap
        _t = _time.perf_counter()
        try:
            await ctx.device.tap(cx, cy)
        except Exception as e:
            logger.warning(f"[executor] device.tap({cx},{cy}) 失败: {e}")
            return True
        _perf["tap"] = (_time.perf_counter() - _t) * 1000
        ctx.last_tap_xy = (cx, cy)

        # 写 decision.tap (含红圈标注图)
        decision = getattr(ctx, "current_decision", None)
        _t = _time.perf_counter()
        if decision is not None:
            try:
                decision.set_tap(
                    cx, cy,
                    method=act.label or "tap",
                    target_class=act.label or "",
                    target_text=str((act.payload or {}).get("template_name", ""))
                                 or str((act.payload or {}).get("memory_note", "")),
                    target_conf=float((act.payload or {}).get("conf", 0.0)),
                    screenshot=shot_before,
                )
            except Exception as e:
                logger.debug(f"[executor] set_tap err: {e}")
        _perf["set_tap"] = (_time.perf_counter() - _t) * 1000

        # tap 后用 wait_for_change 自适应等画面变化, 替代固定 sleep.
        # 优势: 快机 ~100ms 退出, 慢机给到 max_wait_ms 兜底, 不再死等.
        # 兼容: 若调用方显式给了 act.seconds (比如 P3a 切 tab 等动画), 仍用固定 sleep.
        _t = _time.perf_counter()
        shot_after = None
        if act.seconds > 0:
            # 调用方有意指定固定时长 (如 P3a 等面板动画)
            await asyncio.sleep(act.seconds)
        else:
            # 默认: 等 phash 变化 (检测到 ≥10 距离立即返, 最多 400ms)
            from .adb_lite import phash as _phash
            try:
                prev_ph = int(_phash(shot_before)) if shot_before is not None else 0
            except Exception:
                prev_ph = 0
            from . import wait_helpers as _wh
            grab = ctx.device.screenshot
            shot_after, _ph_after, elapsed, changed = await _wh.wait_for_change(
                grab, _phash,
                prev_phash=prev_ph,
                change_threshold=10,
                poll_ms=50,
                max_wait_ms=400,
            )
            logger.debug(
                f"[executor] wait_for_change inst{inst_idx}: "
                f"{elapsed:.0f}ms changed={changed}"
            )
        _perf["sleep"] = (_time.perf_counter() - _t) * 1000

        # 没要求 verify → 跳过验证 (但仍记录到 pending_memory)
        if not act.expectation:
            if ctx.memory is not None and act.label and act.label != "memory_hit":
                ctx.pending_memory_writes.append((shot_before, (cx, cy), act.label))
            # 帧复用: wait_for_change 路径已拿到 shot_after, 顺手写 carryover
            if shot_after is not None:
                ctx.carryover_shot = shot_after
                try:
                    from .adb_lite import phash as _phash
                    ctx.carryover_phash = int(_phash(shot_after))
                except Exception:
                    ctx.carryover_phash = 0
                ctx.carryover_ts = _time.perf_counter()
            _total = (_time.perf_counter() - _t0) * 1000
            logger.info(
                f"[PERF/exec/inst{inst_idx}] tap_no_verify total={_total:.0f}ms "
                f"tap={_perf['tap']:.0f} set_tap={_perf['set_tap']:.0f} sleep={_perf['sleep']:.0f}"
            )
            return True

        # 取 after 帧 (wait_for_change 已经返了 shot_after, 复用避免再截一次)
        _t = _time.perf_counter()
        if shot_after is None:
            try:
                shot_after = await ctx.device.screenshot()
            except Exception as e:
                logger.debug(f"[executor] verify 截图失败: {e}")
                return True
        _perf["shot_after"] = (_time.perf_counter() - _t) * 1000
        if shot_after is None:
            return True

        # 写 decision.verify (phash before/after + distance) + 顺便给下一轮 carryover
        _t = _time.perf_counter()
        pa = 0
        pb = 0
        try:
            from .adb_lite import phash as _phash
            pa = _phash(shot_before) if shot_before is not None else 0
            pb = _phash(shot_after) if shot_after is not None else 0
        except Exception as e:
            logger.debug(f"[executor] phash err: {e}")
        if decision is not None:
            try:
                dist = bin(int(pa) ^ int(pb)).count("1") if (pa and pb) else 0
                decision.set_verify(
                    before=f"0x{int(pa):016x}" if pa else "",
                    after=f"0x{int(pb):016x}" if pb else "",
                    distance=int(dist),
                )
            except Exception as e:
                logger.debug(f"[executor] set_verify err: {e}")
        _perf["phash_set_verify"] = (_time.perf_counter() - _t) * 1000

        # 帧复用: 把 tap 后的 shot_after + 已算的 phash 写到 ctx, 下一轮 _loop_phase
        # 在 200ms 时效内会直接拿来用, 省一次 screencap (~80ms).
        if shot_after is not None:
            ctx.carryover_shot = shot_after
            ctx.carryover_phash = int(pb) if pb else 0
            ctx.carryover_ts = _time.perf_counter()

        # 防线 1+2: state_expectation 综合判定 (内部含 phash + 自定义 verifier)
        # 包 to_thread: verify 内部可能跑模板匹配/OCR, 同步直接调会卡 main loop.
        _t = _time.perf_counter()
        try:
            from .state_expectation import verify as _verify
            verify_ctx = dict(act.payload or {})
            verify_ctx.setdefault("matcher", ctx.matcher)
            exp_r = await asyncio.to_thread(_verify, act.expectation, shot_before, shot_after, verify_ctx)
        except Exception as e:
            logger.debug(f"[executor] state_expectation.verify err: {e}")
            return True
        _perf["exp_verify"] = (_time.perf_counter() - _t) * 1000

        # PERF 总结
        _total = (_time.perf_counter() - _t0) * 1000
        logger.info(
            f"[PERF/exec/inst{inst_idx}] tap_full total={_total:.0f}ms "
            f"tap={_perf['tap']:.0f} set_tap={_perf['set_tap']:.0f} "
            f"sleep={_perf['sleep']:.0f} shot_after={_perf['shot_after']:.0f} "
            f"phash_verify={_perf['phash_set_verify']:.0f} exp={_perf['exp_verify']:.0f}"
        )

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
