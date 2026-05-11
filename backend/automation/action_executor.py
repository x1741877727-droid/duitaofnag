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
import os
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

        # tap 后等动画启动 — 旧版 wait_for_change polling 删了 (100-400ms 浪费),
        # 改成: 短 sleep 启动动画 + 1 次 screencap 拿 shot_after.
        # 准确度由"推迟 verify 到下一轮 perceive 用 YOLO 判"接管, 见下面 popup_dismissed 分支.
        _t = _time.perf_counter()
        if act.seconds > 0:
            # 调用方指定 (P3a 切 tab 等长动画)
            await asyncio.sleep(act.seconds)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # popup_dismissed 走 deferred verify, 不需要立刻 shot_after (下一轮 perceive
        # 用 yolo dets 判定). 跳过 50ms sleep + 200-400ms shot_after screencap + phash.
        # 同时 tap 后调 daemon.invalidate, 强制 daemon 下一轮抓帧不被 motion gate 拦.
        # 一键回退: env GAMEBOT_TAP_FORCE_VERIFY=1
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        _skip_shot_after = (act.expectation == "popup_dismissed"
                            and os.environ.get("GAMEBOT_TAP_FORCE_VERIFY", "0") != "1")
        if _skip_shot_after:
            # 通知 daemon: tap 了, 下一帧请强制重抓
            try:
                from .vision_daemon import VisionDaemon
                VisionDaemon.get().invalidate(getattr(ctx, "instance_idx", -1))
            except Exception:
                pass
            ctx.pending_verify = {
                "kind": "popup_dismissed",
                "xy": (cx, cy),
                "label": act.label,
                "shot_before": shot_before,
                "phash_before": 0,  # deferred 用 yolo 判, 不再用 phash before
            }
            _perf["sleep"] = (_time.perf_counter() - _t) * 1000
            _total = (_time.perf_counter() - _t0) * 1000
            logger.info(
                f"[PERF/exec/inst{inst_idx}] tap_fast total={_total:.0f}ms "
                f"tap={_perf['tap']:.0f} (跳 sleep+shot_after+phash, daemon.invalidate)"
            )
            return True

        # 其他 expectation 仍走原 sleep + shot_after 路径
        if act.seconds <= 0:
            await asyncio.sleep(0.05)
        try:
            shot_after = await ctx.device.screenshot()
        except Exception as e:
            logger.debug(f"[executor] tap-after 截图失败: {e}")
            return True
        _perf["sleep"] = (_time.perf_counter() - _t) * 1000
        if shot_after is None:
            return True

        # 没要求 verify → 跳过验证, 直接缓冲 memory + 写 carryover
        if not act.expectation:
            if ctx.memory is not None and act.label and act.label != "memory_hit":
                ctx.pending_memory_writes.append((shot_before, (cx, cy), act.label))
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

        _perf["shot_after"] = 0.0  # 已经在上面 sleep 块取了, 不单独计时

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
        ctx.carryover_shot = shot_after
        ctx.carryover_phash = int(pb) if pb else 0
        ctx.carryover_ts = _time.perf_counter()

        # popup_dismissed: 推迟 verify 到下一轮 perceive 用 YOLO 判
        # (替代旧版同步 state_expectation.verify, 省 wait_for_change polling 100-300ms).
        # 下一轮 p2_perception.perceive 跑完后, _apply_pending_verify 检查
        # "上次 tap 的 (cx, cy) 处现在 YOLO 还检到 close_x 吗" → 决定 blacklist / memory.
        if act.expectation == "popup_dismissed":
            ctx.pending_verify = {
                "kind": "popup_dismissed",
                "xy": (cx, cy),
                "label": act.label,
                "shot_before": shot_before,
                "phash_before": int(pa) if pa else 0,
            }
            _total = (_time.perf_counter() - _t0) * 1000
            logger.info(
                f"[PERF/exec/inst{inst_idx}] tap_deferred_verify total={_total:.0f}ms "
                f"tap={_perf['tap']:.0f} sleep={_perf['sleep']:.0f} "
                f"phash={_perf['phash_set_verify']:.0f}"
            )
            return True

        # 其他 expectation (popup_next/mode_selected/lobby_*) — 跑同步 verify, 旧路径
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

        _total = (_time.perf_counter() - _t0) * 1000
        logger.info(
            f"[PERF/exec/inst{inst_idx}] tap_full total={_total:.0f}ms "
            f"tap={_perf['tap']:.0f} set_tap={_perf['set_tap']:.0f} "
            f"sleep={_perf['sleep']:.0f} shot_after={_perf['shot_after']:.0f} "
            f"phash_verify={_perf['phash_set_verify']:.0f} exp={_perf['exp_verify']:.0f}"
        )

        if exp_r.matched:
            if (ctx.memory is not None and act.label
                    and act.label != "memory_hit"):
                already = any(
                    m == act.label and abs(ax - cx) < 30 and abs(ay - cy) < 30
                    for (_f, (ax, ay), m) in ctx.pending_memory_writes
                )
                if not already:
                    ctx.pending_memory_writes.append(
                        (shot_before, (cx, cy), act.label)
                    )
        else:
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
    def apply_pending_verify(ctx: RunContext, perception) -> bool:
        """下一轮 perceive 跑完后调. 用 perception (有 yolo_dets_raw) 判定上次 tap 是否生效.

        替代旧版 _do_tap 中同步的 state_expectation.verify. 优势:
          - 用 perception 已有的 YOLO 输出, 无额外推理开销
          - 等下一轮才判 → 跳过 wait_for_change 100-300ms polling

        返回 True = 处理过 pending_verify, False = ctx.pending_verify 为空."""
        pv = ctx.pending_verify
        if pv is None:
            return False
        ctx.pending_verify = None  # 消费一次

        if pv.get("kind") != "popup_dismissed":
            logger.debug(f"[executor] 未知 pending_verify kind={pv.get('kind')}, 丢弃")
            return True

        cx, cy = pv["xy"]
        label = pv.get("label", "")
        shot_before = pv.get("shot_before")
        phash_before = int(pv.get("phash_before", 0))

        dets = getattr(perception, "yolo_dets_raw", []) or []

        # 信号 1: tap 点 (radius 30) 周围还有 close_x? — 主信号 (针对 close_x 类目标)
        close_x_still_there = any(
            getattr(d, "name", "") == "close_x"
            and getattr(d, "conf", 0.0) >= 0.5
            and abs(getattr(d, "cx", 0) - cx) < 30
            and abs(getattr(d, "cy", 0) - cy) < 30
            for d in dets
        )

        # 信号 2: 全屏 phash 距离 — 兜底 (针对"确定/同意"类弹窗, YOLO 看不见这类按钮)
        # template_dismiss_btn label 主要靠这条 — 系统内存过低 / 二次确认 类弹窗.
        phash_after = int(getattr(perception, "phash_now", 0) or 0)
        phash_distance = (
            bin(phash_before ^ phash_after).count("1")
            if (phash_before and phash_after) else 0
        )
        screen_changed = phash_distance >= 10  # 高阈值过滤微动画

        # 综合判定: close_x 信号 + phash 兜底
        # 历史 bug (P2 R26 案例): tap 关掉一个 popup, 游戏立刻在同位置弹下一个 popup,
        # 旧逻辑只看 "close_x 还在不在 tap 点" → 永远 True → 失败 → 黑名单累积 →
        # 半径 30 内所有 close_x 都被屏蔽 → R26 起 no_target 死循环.
        # 修法: close_x 还在 + phash 也没大变 → 才算失败. close_x 还在但画面大变 →
        # 弹窗换了一个, 算 success (黑名单不加, 让下一轮 perceive 继续点新弹窗).
        if label in ("close_x", "memory_hit", "template_close_x"):
            # close_x 类: 主信号 close_x_still_there, phash 兜底
            tap_failed = close_x_still_there and (not screen_changed)
            verdict_reason = (
                f"close_x_still_there={close_x_still_there} "
                f"screen_changed={screen_changed} (phash_dist={phash_distance})"
            )
        else:
            # template_dismiss_btn / 其他 — YOLO 不识别"确定"按钮, 主信号 phash
            # 二次保险: 如果 close_x 还在 tap 点, 直接判失败
            tap_failed = (close_x_still_there) or (not screen_changed)
            verdict_reason = (
                f"label={label} phash_dist={phash_distance} "
                f"changed={screen_changed} close_x_still={close_x_still_there}"
            )

        if not tap_failed:
            # tap 生效 → 缓冲 memory
            if ctx.memory is not None and label and label != "memory_hit":
                already = any(
                    m == label and abs(ax - cx) < 30 and abs(ay - cy) < 30
                    for (_f, (ax, ay), m) in ctx.pending_memory_writes
                )
                if not already:
                    ctx.pending_memory_writes.append((shot_before, (cx, cy), label))
                    logger.info(
                        f"[executor] 🧠 Memory 缓冲 ({cx},{cy}) label={label} "
                        f"(deferred verify ok, {verdict_reason})"
                    )
        else:
            # tap 失败 → 加黑名单 + memory.remember(fail)
            if not ctx.is_blacklisted(cx, cy):
                ctx.blacklist_coords.append((cx, cy))
                logger.warning(
                    f"[executor] Deferred verify 失败 [popup_dismissed] @ "
                    f"({cx},{cy}): {verdict_reason} → 加黑名单 "
                    f"(size={len(ctx.blacklist_coords)})"
                )
            if ctx.memory is not None and label:
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
