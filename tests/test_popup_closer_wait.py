"""
PopupCloser.wait_for_lobby_clearing_popups 状态机单测.

验证逻辑:
  - 第 1 帧 LOBBY → 立即返回, 不调 closer
  - 第 1 帧 POPUP → 调 closer.find_target → executor.apply → 重 classify
    第 2 帧 LOBBY → 返回
  - 多次 POPUP 后才 LOBBY → 多次 close 直到 LOBBY
  - max_attempts 用完仍 POPUP → 返回 last_kind=POPUP (调用方 FAIL)
  - LOADING / UNKNOWN → sleep + 重试 (不调 closer)

跑法:
    python tests/test_popup_closer_wait.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# Mac 没装 numpy/cv2 — 塞 stub 让 popup_closer / screen_classifier 顶层 import 过
for _name in ("numpy", "cv2"):
    if _name not in sys.modules:
        sys.modules[_name] = ModuleType(_name)


async def main():
    from backend.automation.popup_closer import PopupCloser
    from backend.automation.screen_classifier import ScreenKind

    # ─── stubs ───
    class StubDevice:
        def __init__(self):
            self.calls = 0
        async def screenshot(self):
            self.calls += 1
            return f"frame{self.calls}"
        async def tap(self, x, y):
            return None

    class StubCtx:
        def __init__(self):
            self.device = StubDevice()
            self.yolo = None
            self.matcher = None
            self.memory = None
            self.lobby_detector = None
            self.current_shot = None
            self.current_decision = None
            self.blacklist_coords = []
            self.pending_memory_writes = []
            self.pending_verify = None
            self.carryover_shot = None
            self.carryover_phash = 0
            self.carryover_ts = 0.0
            self.last_tap_xy = (0, 0)
            self.empty_dets_streak = 0
            self.lobby_confirm_count = 0
            self.popups_closed = 0
            self.no_target_started_ts = 0.0
            self.phash_stuck_started_ts = 0.0
            self.phase_started_at = 0.0
            self.phase_round = 0
            self.last_phash_int = 0
            self.lobby_posterior = 0.5
            self.login_first_seen_ts = None
        def is_blacklisted(self, x, y, radius=30):
            return False

    # ─── monkey-patch screen_classifier.classify_from_frame ───
    from backend.automation import screen_classifier as sc

    def make_classify(seq):
        """seq 是 ScreenKind 列表, 每次 call 返回下一个 (耗尽返最后一个)."""
        idx = [0]
        async def fake(frame, yolo, matcher):
            v = seq[min(idx[0], len(seq) - 1)]
            idx[0] += 1
            return v
        return fake

    # ─── monkey-patch PopupCloser.find_target + ActionExecutor.apply ───
    from backend.automation import popup_closer as pc_mod
    from backend.automation import action_executor as ae_mod

    fake_action = SimpleNamespace(
        kind="tap", x=480, y=60, seconds=0.4,
        label="close_x", expectation="popup_dismissed", payload={},
    )

    apply_calls = []
    async def fake_apply(ctx, act):
        apply_calls.append(act)
        return True
    ae_mod.ActionExecutor.apply = staticmethod(fake_apply)

    fail = 0

    async def case(name, classify_seq, find_target_returns, max_attempts, interval_s,
                   expected_kind, expected_apply_calls):
        nonlocal fail
        sc.classify_from_frame = make_classify(classify_seq)
        idx = [0]
        async def fake_find(ctx):
            v = find_target_returns[min(idx[0], len(find_target_returns) - 1)]
            idx[0] += 1
            return v
        pc_mod.PopupCloser.find_target = staticmethod(fake_find)
        apply_calls.clear()
        ctx = StubCtx()
        result = await PopupCloser.wait_for_lobby_clearing_popups(
            ctx, max_attempts=max_attempts, interval_s=interval_s)
        ok = (result == expected_kind) and (len(apply_calls) == expected_apply_calls)
        status = "PASS" if ok else "FAIL"
        print(f"{status}  {name}: kind={result.name} apply_calls={len(apply_calls)} "
              f"(expected kind={expected_kind.name} apply={expected_apply_calls})")
        if not ok:
            fail += 1

    # case 1: 第 1 帧 LOBBY → 立刻返回, 0 close
    await case(
        "lobby_immediate",
        classify_seq=[ScreenKind.LOBBY],
        find_target_returns=[],
        max_attempts=8, interval_s=0,
        expected_kind=ScreenKind.LOBBY, expected_apply_calls=0,
    )

    # case 2: POPUP → close → LOBBY
    await case(
        "popup_then_lobby",
        classify_seq=[ScreenKind.POPUP, ScreenKind.LOBBY],
        find_target_returns=[fake_action],
        max_attempts=8, interval_s=0,
        expected_kind=ScreenKind.LOBBY, expected_apply_calls=1,
    )

    # case 3: POPUP × 3 → close × 3 → LOBBY
    await case(
        "popup_3x_then_lobby",
        classify_seq=[ScreenKind.POPUP, ScreenKind.POPUP, ScreenKind.POPUP, ScreenKind.LOBBY],
        find_target_returns=[fake_action, fake_action, fake_action],
        max_attempts=8, interval_s=0,
        expected_kind=ScreenKind.LOBBY, expected_apply_calls=3,
    )

    # case 4: max_attempts 用完仍 POPUP → 返回 POPUP
    await case(
        "popup_exhaust",
        classify_seq=[ScreenKind.POPUP] * 10,
        find_target_returns=[fake_action] * 10,
        max_attempts=3, interval_s=0,
        expected_kind=ScreenKind.POPUP, expected_apply_calls=3,
    )

    # case 5: LOADING → sleep → LOADING → LOBBY (不调 closer)
    await case(
        "loading_then_lobby",
        classify_seq=[ScreenKind.LOADING, ScreenKind.LOADING, ScreenKind.LOBBY],
        find_target_returns=[],
        max_attempts=8, interval_s=0,
        expected_kind=ScreenKind.LOBBY, expected_apply_calls=0,
    )

    # case 6: POPUP 但 closer 找不到 target → 不 apply, 等 → 重试
    await case(
        "popup_no_target",
        classify_seq=[ScreenKind.POPUP, ScreenKind.POPUP, ScreenKind.LOBBY],
        find_target_returns=[None, None],
        max_attempts=8, interval_s=0,
        expected_kind=ScreenKind.LOBBY, expected_apply_calls=0,
    )

    # case 7: UNKNOWN → max_attempts 用完返回 UNKNOWN (不调 closer)
    await case(
        "unknown_exhaust",
        classify_seq=[ScreenKind.UNKNOWN] * 10,
        find_target_returns=[],
        max_attempts=3, interval_s=0,
        expected_kind=ScreenKind.UNKNOWN, expected_apply_calls=0,
    )

    print(f"\n{'ALL OK' if fail == 0 else f'{fail} FAILED'}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
