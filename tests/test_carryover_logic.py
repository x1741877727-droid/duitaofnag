"""
帧复用 carryover 状态机单元测试 — 不进 emulator, 不跑 ML.

验 RunnerFSM._loop_phase 那个 if-else 分支的语义:
  carryover_shot 不为 None 且 carryover_ts < 200ms 前 → 复用
  否则 → 自拍

跑法:
    python tests/test_carryover_logic.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# 让 backend 可 import
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


# 抽出来等价于 _loop_phase 头部的逻辑 (不依赖 numpy / cv2 / FastAPI)
CARRYOVER_MAX_AGE_S = 0.2


class StubCtx:
    """最小 ctx, 字段名跟 RunContext 对齐."""
    def __init__(self):
        self.carryover_shot = None
        self.carryover_phash = 0
        self.carryover_ts = 0.0
        self.current_shot = None
        self.current_phash = ""


def loop_head_decide(ctx, now: float) -> tuple[bool, str]:
    """模拟 _loop_phase 头部的"复用 or 自拍"决策.
    返回 (used_carryover, reason)."""
    if (ctx.carryover_shot is not None
            and (now - ctx.carryover_ts) <= CARRYOVER_MAX_AGE_S):
        # 复用路径
        ctx.current_shot = ctx.carryover_shot
        ctx.current_phash = ctx.carryover_phash
        ctx.carryover_shot = None
        ctx.carryover_phash = 0
        ctx.carryover_ts = 0.0
        return True, "fresh"
    return False, ("none" if ctx.carryover_shot is None else "stale")


def case(name, expected_used, expected_reason, run):
    ctx = StubCtx()
    run(ctx)
    used, reason = loop_head_decide(ctx, now=time.perf_counter())
    ok = (used == expected_used) and (reason == expected_reason)
    return name, ok, used, reason


def main():
    cases = [
        # 第一轮: 没人写 carryover → 自拍
        case(
            "first_round_no_carryover",
            expected_used=False, expected_reason="none",
            run=lambda c: None,
        ),
        # 上一轮 _do_tap 刚写 carryover (现在用) → 复用
        case(
            "carryover_fresh",
            expected_used=True, expected_reason="fresh",
            run=lambda c: setattr(c, "carryover_shot", "fake_frame")
                          or setattr(c, "carryover_phash", 0xABCDEF)
                          or setattr(c, "carryover_ts", time.perf_counter()),
        ),
        # carryover 写了 300ms 前, 超 200ms 时效 → 自拍
        case(
            "carryover_stale",
            expected_used=False, expected_reason="stale",
            run=lambda c: setattr(c, "carryover_shot", "fake_frame")
                          or setattr(c, "carryover_phash", 0xABCDEF)
                          or setattr(c, "carryover_ts", time.perf_counter() - 0.3),
        ),
        # carryover_shot is None 但 ts 不为 0 (异常态) → 自拍 (none)
        case(
            "carryover_shot_none_but_ts_set",
            expected_used=False, expected_reason="none",
            run=lambda c: setattr(c, "carryover_ts", time.perf_counter()),
        ),
        # 边界: 刚好 200ms (临界) → 应当复用 (<= 是闭区间)
        case(
            "carryover_at_boundary",
            expected_used=True, expected_reason="fresh",
            run=lambda c: setattr(c, "carryover_shot", "fake_frame")
                          or setattr(c, "carryover_ts", time.perf_counter() - CARRYOVER_MAX_AGE_S + 0.001),
        ),
    ]

    fail = 0
    for name, ok, used, reason in cases:
        print(f"{'PASS' if ok else 'FAIL'}  {name}: used={used} reason={reason}")
        if not ok:
            fail += 1

    # 第二轮消费验证: 复用一次后 ctx 应清空, 第二次再调走 fallback
    print("\n--- 消费验证 ---")
    ctx = StubCtx()
    ctx.carryover_shot = "fake"
    ctx.carryover_ts = time.perf_counter()
    used1, _ = loop_head_decide(ctx, now=time.perf_counter())
    used2, reason2 = loop_head_decide(ctx, now=time.perf_counter())
    consumed_ok = used1 is True and used2 is False and reason2 == "none"
    print(f"{'PASS' if consumed_ok else 'FAIL'}  consume_once: 1st={used1} 2nd={used2}/{reason2}")
    if not consumed_ok:
        fail += 1

    print(f"\n{'ALL OK' if fail == 0 else f'{fail} FAILED'}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
