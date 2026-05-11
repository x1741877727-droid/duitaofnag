"""Middleware — 跨 phase 公共逻辑 (邀请关闭 / 网络弹窗 / crash 检测).

runner 主 loop 每 round 在 handle_frame 前后调 middleware:
    for mw in middlewares:
        intercept = await mw.before_round(ctx)
        if intercept: return RETRY     # 中断 phase 流程, 下轮再来

设计来源: REVIEW_DAY3_WATCHDOG.md
"""
from .base import Middleware, BeforeRoundResult
from .invite_dismiss import InviteDismissMiddleware
from .crash_check import CrashCheckMiddleware
from .network import NetworkErrorMiddleware

__all__ = [
    "Middleware", "BeforeRoundResult",
    "InviteDismissMiddleware", "CrashCheckMiddleware", "NetworkErrorMiddleware",
]
