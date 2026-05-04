"""
推迟 verify 单元测试 — 不进 emulator, 不跑 ML.

验 ActionExecutor.apply_pending_verify 的逻辑:
  - 没 pending_verify        → 啥也不做, 返 False
  - close_x 还在 tap 点 (radius 30) → blacklist + memory.remember(fail)
  - close_x 不在 / conf < 0.5  → pending_memory_writes 增条 (success)
  - close_x 在 50px 外           → 当作不在, success
  - 不同 yolo class             → 当作不在, success

跑法:
    python tests/test_deferred_verify.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

# 让 backend 可 import
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# Mac 上没装 numpy/cv2 — apply_pending_verify 是纯逻辑无 numpy/cv2 调用,
# 但 action_executor 顶层 import np/cv2. 这里塞 stub 让顶层 import 过.
for _name in ("numpy", "cv2"):
    if _name not in sys.modules:
        sys.modules[_name] = ModuleType(_name)


# ────── stubs ──────


class StubMemory:
    def __init__(self):
        self.calls = []

    def remember(self, frame, target_name, action_xy, success):
        self.calls.append({"frame": frame, "target_name": target_name,
                           "xy": action_xy, "success": success})


class StubCtx:
    """最小 ctx, 只放 apply_pending_verify 用到的字段."""
    def __init__(self, pv=None, has_memory=True):
        self.pending_verify = pv
        self.blacklist_coords = []
        self.pending_memory_writes = []
        self.memory = StubMemory() if has_memory else None

    def is_blacklisted(self, x, y, radius=30):
        return any(abs(x - bx) < radius and abs(y - by) < radius
                   for (bx, by) in self.blacklist_coords)


def fake_det(name, cx, cy, conf=0.9):
    """伪造 YOLO Detection (像 yolo_dismisser.Detection)."""
    return SimpleNamespace(name=name, cx=cx, cy=cy, conf=conf,
                           cls=0, x1=cx-10, y1=cy-10, x2=cx+10, y2=cy+10)


def fake_perception(yolo_dets):
    """伪造 Perception (只关心 yolo_dets_raw)."""
    return SimpleNamespace(yolo_dets_raw=yolo_dets)


# ────── 测试 cases ──────


def case(name, expected, run):
    ctx = run()
    return name, ctx, expected


def main():
    # 直接 import 实际的 apply_pending_verify
    from backend.automation.action_executor import ActionExecutor

    cases = []

    # case 1: 没 pending_verify → 啥也不做返 False
    def c1():
        ctx = StubCtx(pv=None)
        ret = ActionExecutor.apply_pending_verify(ctx, fake_perception([]))
        assert ret is False, f"期望 False, 实际 {ret}"
        assert ctx.blacklist_coords == []
        assert ctx.pending_memory_writes == []
        return "no_pending_verify"
    cases.append(("no_pending", c1))

    # case 2: close_x 还在原 tap 点 → blacklist + memory.remember(fail)
    def c2():
        pv = {"kind": "popup_dismissed", "xy": (480, 60),
              "label": "close_x", "shot_before": "fake_shot"}
        ctx = StubCtx(pv=pv)
        # YOLO 检到 close_x 在 (485, 65) — 距离 < 30, 算"还在"
        dets = [fake_det("close_x", 485, 65, conf=0.9)]
        ret = ActionExecutor.apply_pending_verify(ctx, fake_perception(dets))
        assert ret is True
        assert ctx.pending_verify is None  # 消费掉
        assert (480, 60) in ctx.blacklist_coords, f"应加黑名单, got {ctx.blacklist_coords}"
        assert ctx.pending_memory_writes == [], "不应进 pending_memory"
        # memory 失败计数
        assert len(ctx.memory.calls) == 1
        assert ctx.memory.calls[0]["success"] is False
        return "still_there_blacklist"
    cases.append(("still_there", c2))

    # case 3: close_x 没了 → pending_memory + 不动 blacklist
    def c3():
        pv = {"kind": "popup_dismissed", "xy": (480, 60),
              "label": "close_x", "shot_before": "fake_shot"}
        ctx = StubCtx(pv=pv)
        # YOLO 啥都没检到 (弹窗关掉了)
        ret = ActionExecutor.apply_pending_verify(ctx, fake_perception([]))
        assert ret is True
        assert ctx.blacklist_coords == [], "不应加黑名单"
        assert len(ctx.pending_memory_writes) == 1
        f, xy, label = ctx.pending_memory_writes[0]
        assert xy == (480, 60) and label == "close_x"
        return "popup_gone_buffer_memory"
    cases.append(("popup_gone", c3))

    # case 4: YOLO 检到 close_x 但在 50px 外 (跟 tap 点无关) → 当作不在
    def c4():
        pv = {"kind": "popup_dismissed", "xy": (480, 60),
              "label": "close_x", "shot_before": "fake_shot"}
        ctx = StubCtx(pv=pv)
        dets = [fake_det("close_x", 600, 200, conf=0.9)]  # 远离 tap 点
        ret = ActionExecutor.apply_pending_verify(ctx, fake_perception(dets))
        assert ret is True
        assert ctx.blacklist_coords == []
        assert len(ctx.pending_memory_writes) == 1
        return "close_x_far_away"
    cases.append(("far_away", c4))

    # case 5: close_x 在 tap 点但 conf < 0.5 → 当作不可信 → 算关掉了
    def c5():
        pv = {"kind": "popup_dismissed", "xy": (480, 60),
              "label": "close_x", "shot_before": "fake_shot"}
        ctx = StubCtx(pv=pv)
        dets = [fake_det("close_x", 485, 65, conf=0.3)]  # 低置信度
        ret = ActionExecutor.apply_pending_verify(ctx, fake_perception(dets))
        assert ret is True
        assert ctx.blacklist_coords == []
        assert len(ctx.pending_memory_writes) == 1
        return "low_conf_skip"
    cases.append(("low_conf", c5))

    # case 6: 检到的是 dialog 不是 close_x → 当作不在 (不同类)
    def c6():
        pv = {"kind": "popup_dismissed", "xy": (480, 60),
              "label": "close_x", "shot_before": "fake_shot"}
        ctx = StubCtx(pv=pv)
        dets = [fake_det("dialog", 485, 65, conf=0.9)]
        ret = ActionExecutor.apply_pending_verify(ctx, fake_perception(dets))
        assert ret is True
        assert ctx.blacklist_coords == []
        assert len(ctx.pending_memory_writes) == 1
        return "different_class"
    cases.append(("different_class", c6))

    # case 7: label="memory_hit" → 不进 pending_memory (避免自我强化)
    def c7():
        pv = {"kind": "popup_dismissed", "xy": (480, 60),
              "label": "memory_hit", "shot_before": "fake_shot"}
        ctx = StubCtx(pv=pv)
        ret = ActionExecutor.apply_pending_verify(ctx, fake_perception([]))
        assert ret is True
        assert ctx.pending_memory_writes == [], "memory_hit 不应自学"
        return "memory_hit_no_buffer"
    cases.append(("memory_hit_skip", c7))

    # 跑所有 case
    fail = 0
    for name, fn in cases:
        try:
            result = fn()
            print(f"PASS  {name}: {result}")
        except AssertionError as e:
            print(f"FAIL  {name}: {e}")
            fail += 1
        except Exception as e:
            print(f"ERROR {name}: {type(e).__name__}: {e}")
            fail += 1

    print(f"\n{'ALL OK' if fail == 0 else f'{fail} FAILED'}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
