"""
v3 P2 SubFSM — 把 perception + policy + 守门规则 粘起来.

每帧的子状态机:
  PERCEIVE → CHECK_LOBBY ↘ EXIT_OK (大厅确认 2 帧)
                          ↘ CHECK_LOGIN ↘ EXIT_FAIL (登录 60s 超时 → game_restart)
                                         ↘ DECIDE → TAP (return WAIT, executor 实施)
                                                   ↘ NONE → empty_streak++ → 死屏判定

实际不显式 enumerate 子状态, 用顺序 if/return 表达 (单帧内一次性流转完).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from ..action_executor import ActionExecutor
from ..phase_base import PhaseAction, PhaseResult, PhaseStep, RunContext
from ..recorder_helpers import record_perception
from .p2_perception import Perception, perceive
from .p2_policy import decide

logger = logging.getLogger(__name__)


# 守门常量
LOBBY_CONFIRM_NEEDED = 2          # [legacy fallback] 连续 N 帧四元判大厅 → 确认
LOBBY_POST_THRESHOLD = 0.92       # 贝叶斯早退: 后验 ≥ 此值即视为大厅
LOBBY_FALSE_POS_RATE = 0.10       # P(quad fires | 实际不在大厅), 单帧 conf 太低时按这权重
LOGIN_TIMEOUT_SECONDS = 60.0      # 登录页停留超过此 → game_restart
EMPTY_STREAK_LIMIT = 25           # 连续 N 帧无目标 (软触发, 还要看 phash 是否卡住)
PHASH_STUCK_THRESHOLD = 5         # phash 距离 ≤ 此 视为同一帧 (画面没变)
PHASH_STUCK_LIMIT = 10            # 连续 N 轮 phash 卡住 → 才认定真死屏
                                  # 区分 "真死屏" (画面冻住) vs "loading" (画面在变但没弹窗)
                                  # 双条件: empty_streak ≥ 25 AND phash_stuck ≥ 10 才 game_restart


def _bayes_update(prior: float, p_frame: float) -> float:
    """单帧 Bayesian 更新: 后验 = (prior * p) / (prior * p + (1-prior) * (1-p)).
    p_frame 是这一帧"在大厅"的瞬时概率."""
    p_frame = max(0.01, min(0.99, p_frame))   # 截断防数值爆炸
    num = prior * p_frame
    return num / (num + (1.0 - prior) * (1.0 - p_frame))


class P2SubFSM:
    """P2 dismiss_popups 的子状态机. 每帧 step() 一次, 返回 PhaseStep."""

    async def step(self, ctx: RunContext) -> PhaseStep:
        # 1. 跑 perception
        p: Perception = await perceive(ctx)
        rnd = ctx.phase_round

        # 把 perception 8 字段写进 5 层 Tier (decision_log)
        record_perception(ctx.current_decision, p)

        # log dets 概览 (跟 v2 等价, 便于排查 YOLO 漏检)
        if p.yolo_dets_raw:
            tops = ", ".join(
                f"{d.name}({d.conf:.2f})@({d.cx},{d.cy})"
                for d in p.yolo_dets_raw[:3]
            )
            logger.info(f"[P2/R{rnd}] dets={len(p.yolo_dets_raw)} top: {tops}")
        else:
            logger.info(f"[P2/R{rnd}] dets=0 (画面无 close_x/action_btn)")

        # 2. 大厅守门 — 贝叶斯早退 (替代死板 2-frame confirm).
        #    单帧高 conf (>0.92 后验) 立即退, 慢机 / 边界帧自然多看一眼,
        #    比 quad-2-frame 平均快 ~600ms.
        if p.quad_lobby_confirmed:
            # 命中: 用 template_conf 作单帧 P(在大厅), 没值用 0.85 默认
            p_frame = p.quad_template_conf if p.quad_template_conf > 0.5 else 0.85
            ctx.lobby_posterior = _bayes_update(ctx.lobby_posterior, p_frame)
            ctx.lobby_confirm_count += 1   # legacy 计数仍维护, 兼容老 commit_pending 逻辑
            if ctx.lobby_posterior >= LOBBY_POST_THRESHOLD:
                n = ActionExecutor.commit_pending_memory(ctx)
                if n:
                    logger.info(f"[P2/R{rnd}] Memory commit {n} 条 (P2 success)")
                return PhaseStep(
                    PhaseResult.NEXT,
                    note=f"大厅确认 (贝叶斯 post={ctx.lobby_posterior:.3f}, 累计 {ctx.lobby_confirm_count} 帧), "
                         f"关闭 {ctx.popups_closed} 弹窗 · {p.quad_note}",
                    outcome_hint="lobby_confirmed_quad",
                )
            return PhaseStep(
                PhaseResult.WAIT,
                wait_seconds=0.1,
                note=f"大厅 pending post={ctx.lobby_posterior:.3f}/{LOBBY_POST_THRESHOLD} ({p.quad_note})",
                outcome_hint=f"lobby_pending_{ctx.lobby_posterior:.2f}",
            )
        else:
            # 不命中: 用低 P 值更新 — 不直接归零, 给瞬态过渡帧容错
            ctx.lobby_posterior = _bayes_update(ctx.lobby_posterior, LOBBY_FALSE_POS_RATE)
            ctx.lobby_confirm_count = 0

        # 3. 登录页守门 (60s 超时 → game_restart)
        if p.login_template_hit is not None:
            if ctx.login_first_seen_ts is None:
                ctx.login_first_seen_ts = time.time()
                logger.info(
                    f"[P2/R{rnd}] 见登录页 → 开始 {LOGIN_TIMEOUT_SECONDS:.0f}s 计时"
                )
            else:
                elapsed = time.time() - ctx.login_first_seen_ts
                if elapsed >= LOGIN_TIMEOUT_SECONDS:
                    return PhaseStep(
                        PhaseResult.GAME_RESTART,
                        note=f"自动登录 {elapsed:.0f}s 仍在登录页 → game_restart",
                        outcome_hint="login_timeout_fail",
                    )
        else:
            if ctx.login_first_seen_ts is not None:
                logger.info(f"[P2/R{rnd}] 离开登录页 (登录成功) → 重置计时器")
            ctx.login_first_seen_ts = None

        # 4. 决策 — 选下一动作
        action = decide(p, ctx)

        # 5. 没目标 → empty_streak++ + 检查 phash 卡住
        if action is None:
            ctx.empty_dets_streak += 1
            # 跟踪 phash 是否卡住 (区分死屏 vs loading)
            cur_ph = int(p.phash_now or 0)
            if cur_ph and ctx.last_phash_int:
                dist = bin(cur_ph ^ ctx.last_phash_int).count("1")
                if dist <= PHASH_STUCK_THRESHOLD:
                    ctx.phash_stuck_streak += 1
                else:
                    ctx.phash_stuck_streak = 0
            ctx.last_phash_int = cur_ph or ctx.last_phash_int

            # 大厅模板命中 + 持续 3 轮无目标 → 兜底判大厅成功
            if (ctx.empty_dets_streak >= 3
                    and p.lobby_template_hit is not None):
                n = ActionExecutor.commit_pending_memory(ctx)
                if n:
                    logger.info(f"[P2/R{rnd}] Memory commit {n} 条 (兜底大厅)")
                return PhaseStep(
                    PhaseResult.NEXT,
                    note=f"大厅 (兜底: 连续{ctx.empty_dets_streak}轮无目标 + 模板命中) "
                         f"· 关闭 {ctx.popups_closed} 弹窗",
                    outcome_hint="lobby_confirmed_legacy",
                )
            # 死屏判定: 双条件 — 长时间无目标 AND phash 长时间卡住
            #   单条件 empty_streak 太敏感 (loading 画面在变但没弹窗会误杀)
            if (ctx.empty_dets_streak > EMPTY_STREAK_LIMIT
                    and ctx.phash_stuck_streak >= PHASH_STUCK_LIMIT):
                return PhaseStep(
                    PhaseResult.GAME_RESTART,
                    note=f"死屏: 无目标 {ctx.empty_dets_streak} 轮 + phash 卡住 "
                         f"{ctx.phash_stuck_streak} 轮",
                    outcome_hint="dead_screen",
                )
            return PhaseStep(
                PhaseResult.RETRY,
                note=f"无目标 (streak={ctx.empty_dets_streak}, phash_stuck={ctx.phash_stuck_streak})",
                outcome_hint="no_target",
            )

        ctx.empty_dets_streak = 0
        ctx.phash_stuck_streak = 0   # tap 成功 → 重置卡住计数

        # 删了原 same_target 防死循环机制.
        # 根因: 它不区分"真死循环 (verify 失败连击)"和"弹窗排队 (同位置弹一个接一个)".
        # 真死循环已被 state_expectation 失败 → ActionExecutor 加黑名单挡住,
        # 这里多余, 反而误伤合法排队 (R14-R16 verify=True 但被加黑名单导致 R17 起 no_target).

        # 7. 真 tap — 返回 WAIT, executor 处理 verify + 缓冲 memory
        # wait_seconds=0: ActionExecutor 内部 wait_for_change 已经 adaptive 等过了,
        #   再 sleep 是浪费. 让下一 round 立即跑 (burst dismiss 模式 #3).
        ctx.popups_closed += 1
        return PhaseStep(
            PhaseResult.WAIT,
            action=action,
            wait_seconds=0.0,
            note=f"tap {action.label}({action.x},{action.y})",
            outcome_hint="tapped",
        )
