"""PhaseStep / PhaseResult / PhaseAction — phase 基础数据类.

设计:
- PhaseResult: NEXT/RETRY/FAIL/GAME_RESTART/DONE/WAIT 6 个枚举
- PhaseStep: phase.handle_frame() 返回值, 含 result + action + note + 时间戳辅助
- PhaseAction: tap 动作, runner 自己 dispatch 给 ctx.adb.tap()
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


class PhaseResult(Enum):
    """phase round 结果."""
    NEXT = "NEXT"             # 完成, 进下一 phase
    RETRY = "RETRY"           # 当前 phase 重试 (sleep round_interval 再来)
    FAIL = "FAIL"             # 失败, runner 停整个会话
    GAME_RESTART = "GAME_RESTART"   # 游戏崩了, 重启 PUBG 回 P1
    DONE = "DONE"             # 整个会话完成 (P5 NEXT 等价)
    WAIT = "WAIT"             # sleep N 秒 (handler 自己定 wait_seconds)


@dataclass
class PhaseAction:
    """phase 让 runner 做的动作 (主要是 tap)."""
    kind: str = ""             # 'tap' / 'noop'
    x: int = 0
    y: int = 0
    target: str = ""           # 'close_x' / 'action_btn' / 'memory_hit' 等
    conf: float = 0.0


@dataclass
class PhaseStep:
    """handle_frame() 返回值."""
    result: PhaseResult
    note: str = ""
    outcome_hint: str = ""     # 写到 decision.outcome 字段 (e.g. 'tapped'/'no_target')
    action: Optional[PhaseAction] = None
    wait_seconds: float = 0.0  # WAIT 时用


# 便利构造函数
def step_next(note: str = "", **kw) -> PhaseStep:
    return PhaseStep(PhaseResult.NEXT, note=note, **kw)


def step_retry(note: str = "", action: Optional[PhaseAction] = None,
               outcome_hint: str = "") -> PhaseStep:
    return PhaseStep(PhaseResult.RETRY, note=note, action=action,
                     outcome_hint=outcome_hint or ("tapped" if action else "no_target"))


def step_fail(note: str = "", outcome_hint: str = "fail") -> PhaseStep:
    return PhaseStep(PhaseResult.FAIL, note=note, outcome_hint=outcome_hint)


def step_done(note: str = "", outcome_hint: str = "done") -> PhaseStep:
    return PhaseStep(PhaseResult.DONE, note=note, outcome_hint=outcome_hint)


def step_wait(seconds: float, note: str = "") -> PhaseStep:
    return PhaseStep(PhaseResult.WAIT, note=note, wait_seconds=seconds)


def step_game_restart(note: str = "") -> PhaseStep:
    return PhaseStep(PhaseResult.GAME_RESTART, note=note,
                     outcome_hint="game_restart")
