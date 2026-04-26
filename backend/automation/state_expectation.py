"""
v2 防线 2 — State Expectation

每个 action (tap close_x / tap "收下" / tap mode_card / tap "前往") 注册预期效果:
  tap close_x  → 弹窗消失 (YOLO close_x 数量减少)
  tap "收下"   → 弹窗消失 OR 切下一弹窗 (画面变化)
  tap "前往"   → 危险, tap 后必须仍在大厅 (lobby_start_btn 仍命中)
  tap mode_card → 进入 selected 态

tap 后取截图, 调 verifier(before, after, ctx). 不达预期 → 触发 on_fail.

跟防线 1 (phash 验证) 的区别:
  防线 1: 画面变了吗? (二值)
  防线 2: 画面按 *预期方向* 变了吗? (语义)

例: tap close_x 后画面变了 → 防线 1 OK, 但如果是切到了别的 *新* 弹窗 (close_x 数没减),
   防线 2 会判定 "不符合 popup_dismissed 预期" → on_fail (e.g. 重选目标)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


class ExpectKind(Enum):
    POPUP_DISMISSED = "popup_dismissed"
    POPUP_NEXT = "popup_next"
    MODE_SELECTED = "mode_selected"
    LOBBY_STAYED = "lobby_stayed"          # tap 后仍在大厅 (危险按钮专用)
    LOBBY_LEFT_OK = "lobby_left_ok"        # tap 后离开大厅 (例如 phase_team_create)


# verifier 签名: (frame_before, frame_after, ctx) -> bool (True = 符合预期)
VerifierFn = Callable[[np.ndarray, np.ndarray, Dict[str, Any]], bool]


@dataclass
class Expectation:
    kind: ExpectKind
    description: str
    verifier: VerifierFn
    on_fail_hint: str = ""    # 失败时人类可读建议 (e.g. "标该坐标无效, 重选目标")


@dataclass
class ExpectationResult:
    matched: bool
    kind: ExpectKind
    description: str
    note: str = ""


# ─── 内置 verifiers ───


def _verify_popup_dismissed(before, after, ctx) -> bool:
    """tap close_x 后 close_x 数量应该减少"""
    yb = ctx.get("yolo_before", []) or []
    ya = ctx.get("yolo_after", []) or []
    cx_b = sum(1 for d in yb if getattr(d, "cls", "") == "close_x")
    cx_a = sum(1 for d in ya if getattr(d, "cls", "") == "close_x")
    return cx_a < cx_b


def _verify_popup_next(before, after, ctx) -> bool:
    """tap 收下/确定 后画面变了 (弹窗消失或切下一弹窗)"""
    from .adb_lite import phash, phash_distance

    try:
        return phash_distance(phash(before), phash(after)) > 5
    except Exception:
        return False


def _verify_mode_selected(before, after, ctx) -> bool:
    """tap mode_card 后画面变了 (理想还要看 mode_card_selected YOLO 类, v2 后做)"""
    from .adb_lite import phash, phash_distance

    try:
        return phash_distance(phash(before), phash(after)) > 3
    except Exception:
        return False


def _verify_lobby_stayed(before, after, ctx) -> bool:
    """tap "前往" 后仍要在大厅 — lobby_start_btn 仍命中即 OK.

    没匹中 = 跳出大厅了 = 危险 → False.
    """
    matcher = ctx.get("matcher")
    if matcher is None:
        return True  # 没 matcher 没法判, 默认 OK
    try:
        m = matcher.match_one(after, "lobby_start_btn", threshold=0.80)
        if m is not None:
            return True
        m2 = matcher.match_one(after, "lobby_start_game", threshold=0.80)
        return m2 is not None
    except Exception:
        return True


def _verify_lobby_left_ok(before, after, ctx) -> bool:
    """phase_team_create 等场景: tap 后 *应该* 离开大厅. lobby_start_btn 不再命中即 OK."""
    matcher = ctx.get("matcher")
    if matcher is None:
        return True
    try:
        m = matcher.match_one(after, "lobby_start_btn", threshold=0.80)
        return m is None
    except Exception:
        return True


# ─── 全局注册表 ───


class ExpectationRegistry:
    """label → Expectation. label 通常是 yolo class 名 / OCR 关键字."""

    _table: Dict[str, Expectation] = {}

    @classmethod
    def register(cls, label: str, exp: Expectation) -> None:
        cls._table[label] = exp

    @classmethod
    def get(cls, label: str) -> Optional[Expectation]:
        return cls._table.get(label)

    @classmethod
    def known_labels(cls) -> List[str]:
        return list(cls._table.keys())

    @classmethod
    def clear(cls) -> None:
        cls._table.clear()


def _register_defaults() -> None:
    """启动时调一次. 注册 v1 默认预期."""
    ExpectationRegistry.register(
        "close_x",
        Expectation(
            kind=ExpectKind.POPUP_DISMISSED,
            description="点 X 关闭按钮, 弹窗应该消失",
            verifier=_verify_popup_dismissed,
            on_fail_hint="该 close_x 坐标可能误识, 标无效后重选",
        ),
    )
    for kw in ("收下", "确定", "确认", "同意", "好的", "知道了", "继续", "开始"):
        ExpectationRegistry.register(
            kw,
            Expectation(
                kind=ExpectKind.POPUP_NEXT,
                description=f"点 '{kw}' 弹窗应消失或切下一个",
                verifier=_verify_popup_next,
                on_fail_hint="画面没变, 该坐标可能不响应",
            ),
        )
    for danger in ("前往", "参加", "进入", "查看"):
        ExpectationRegistry.register(
            danger,
            Expectation(
                kind=ExpectKind.LOBBY_STAYED,
                description=f"'{danger}' 按钮不应跳出大厅 (导航词)",
                verifier=_verify_lobby_stayed,
                on_fail_hint="跳出大厅了, 立刻回 dismiss_popups 清回",
            ),
        )
    ExpectationRegistry.register(
        "action_btn",
        Expectation(
            kind=ExpectKind.POPUP_NEXT,
            description="action_btn 通用 (收下/确定/同意), 弹窗应变化",
            verifier=_verify_popup_next,
        ),
    )


_register_defaults()


# ─── 公开 verify API ───


def verify(
    label: str,
    before: np.ndarray,
    after: np.ndarray,
    ctx: Optional[Dict[str, Any]] = None,
) -> ExpectationResult:
    """检查 tap label 后画面是否符合预期.

    没注册的 label → matched=True, 视为"无预期 = 不卡"
    """
    exp = ExpectationRegistry.get(label)
    if exp is None:
        return ExpectationResult(
            matched=True,
            kind=ExpectKind.POPUP_NEXT,
            description="(no expectation registered)",
            note="unknown label, no check",
        )
    try:
        ok = exp.verifier(before, after, ctx or {})
    except Exception as e:
        logger.warning(f"[expect] verifier {label} crashed: {e}")
        return ExpectationResult(
            matched=True,  # crash 不当失败
            kind=exp.kind,
            description=exp.description,
            note=f"verifier error: {e}",
        )
    return ExpectationResult(
        matched=ok,
        kind=exp.kind,
        description=exp.description,
        note=("OK" if ok else exp.on_fail_hint or "expectation failed"),
    )
