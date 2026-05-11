"""Middleware base — Protocol 定义 + 返回类型.

设计模式: runner 主 loop 在 handle_frame 之前调用 middleware.before_round(ctx),
如果有 intercept (e.g. 检测到邀请弹窗已关闭), 当前 round 提前 RETRY 不进业务.

V2 不要做嵌套 middleware (一个就够), 简单流水线模式.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from ..ctx import RunContext


@dataclass
class BeforeRoundResult:
    """middleware.before_round 返回值.

    intercept=True: 表示本 round 已被 middleware 处理 (e.g. 关闭了邀请弹窗),
                    runner 应该 RETRY 不进业务 handle_frame.
    intercept=False: 业务正常继续.
    note: 简短描述 (落到 decision.note, 便于排查).
    """
    intercept: bool = False
    note: str = ""


@runtime_checkable
class Middleware(Protocol):
    """跨 phase 的公共逻辑接口.

    生命周期:
    - runner 启动时 enable_for() 决定是否对当前 phase 启用
    - 每 round 调 before_round(ctx, shot), 返 intercept=True 跳过业务
    - phase 退出时 after_phase(ctx) 清理状态 (可选)
    """
    name: str

    def enable_for(self, phase_name: str) -> bool:
        """这个 middleware 对当前 phase 启用吗?

        e.g. 邀请关闭对所有 phase 启用; crash 检测只对 P1-P5 启用 (P0 还没启游戏)
        """
        ...

    async def before_round(self, ctx: RunContext, shot) -> BeforeRoundResult:
        """每 round 在 handle_frame 之前调用.

        返回 intercept=True → 当前 round 跳过业务, runner RETRY.
        允许 middleware 内部用 ctx.yolo.detect / ctx.adb.tap 等做处理.
        """
        ...

    async def after_phase(self, ctx: RunContext) -> None:
        """phase 退出时调 (可选, 清理 middleware 内部状态)."""
        ...
