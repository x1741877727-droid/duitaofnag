"""
state_expectation.py 单元测试.

跑法: python -X utf8 tests/test_state_expectation.py
"""

from __future__ import annotations

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import numpy as np

from backend.automation.state_expectation import (
    ExpectKind,
    ExpectationRegistry,
    verify,
)


# ─── stubs ───


class StubDet:
    def __init__(self, cls, conf=0.85):
        self.cls = cls
        self.conf = conf


class StubMatch:
    def __init__(self, name, conf):
        self.name = name
        self.confidence = conf


class StubMatcher:
    def __init__(self, table):
        self.table = table

    def match_one(self, frame, name, threshold=0.5):
        m = self.table.get(name)
        return m if m and m.confidence >= threshold else None


def make_frame(seed=0):
    rng = np.random.RandomState(seed)
    return rng.randint(0, 256, (720, 1280, 3), dtype=np.uint8)


# ─── 测试 ───


def test_close_x_dismissed_yolo_count_decreased():
    before = make_frame(1)
    after = make_frame(2)
    ctx = {
        "yolo_before": [StubDet("close_x"), StubDet("close_x")],
        "yolo_after": [StubDet("close_x")],  # 减 1
    }
    r = verify("close_x", before, after, ctx)
    assert r.matched, f"close_x 数减少应符合预期, got: {r.note}"
    assert r.kind == ExpectKind.POPUP_DISMISSED
    print("  ✓ close_x_dismissed_yolo_count_decreased")


def test_close_x_count_unchanged_but_phash_changed_ok():
    """tap close_x 后数量没减但画面大变 → 成功 (新弹窗替代旧弹窗, 不算失败).

    这是真实游戏跑出来后修的: 点 close_x → 新弹窗冒出来也有 close_x →
    count 不变. 旧 verifier 误判失败导致 timeout. 现 phash fallback 救场.
    """
    before = make_frame(1)
    after = make_frame(99)  # 完全不同的画面
    ctx = {
        "yolo_before": [StubDet("close_x")],
        "yolo_after": [StubDet("close_x")],  # 计数没减
    }
    r = verify("close_x", before, after, ctx)
    assert r.matched, "count 没减但画面变了应算成功"
    print("  ✓ close_x_count_unchanged_but_phash_changed_ok")


def test_close_x_count_unchanged_and_phash_unchanged_fail():
    """tap close_x 后数量没减且画面没变 → 失败 (真没响应)"""
    f = make_frame(5)
    ctx = {
        "yolo_before": [StubDet("close_x")],
        "yolo_after": [StubDet("close_x")],
    }
    r = verify("close_x", f, f, ctx)
    assert not r.matched, "count 没减且画面没变应判失败"
    print("  ✓ close_x_count_unchanged_and_phash_unchanged_fail")


def test_popup_next_phash_changed():
    """tap '收下' 后画面变了 → OK"""
    r = verify("收下", make_frame(1), make_frame(99), {})
    assert r.matched
    assert r.kind == ExpectKind.POPUP_NEXT
    print("  ✓ popup_next_phash_changed")


def test_popup_next_phash_unchanged():
    """tap '确定' 后画面没变 → 失败 (没响应)"""
    f = make_frame(5)
    r = verify("确定", f, f, {})
    assert not r.matched
    print("  ✓ popup_next_phash_unchanged")


def test_lobby_stayed_ok_when_lobby_btn_still_match():
    """tap '前往' 后 lobby_start_btn 仍命中 → OK (没跳出大厅)"""
    matcher = StubMatcher({"lobby_start_btn": StubMatch("lobby_start_btn", 0.95)})
    r = verify("前往", make_frame(1), make_frame(2), {"matcher": matcher})
    assert r.matched, "lobby 仍在应符合 LOBBY_STAYED 预期"
    assert r.kind == ExpectKind.LOBBY_STAYED
    print("  ✓ lobby_stayed_ok_when_lobby_btn_still_match")


def test_lobby_stayed_failed_when_jumped_out():
    """tap '前往' 后 lobby_start_btn 不命中 → 失败 (跳出大厅了, 危险)"""
    matcher = StubMatcher({})  # 啥都没匹中
    r = verify("前往", make_frame(1), make_frame(2), {"matcher": matcher})
    assert not r.matched
    assert "回 dismiss_popups" in r.note or "跳出" in r.note
    print("  ✓ lobby_stayed_failed_when_jumped_out")


def test_unknown_label_returns_matched_true():
    """没注册的 label → 视为无预期, 不卡流程"""
    r = verify("某个未注册的标签xyz", make_frame(1), make_frame(2), {})
    assert r.matched
    assert "no expectation" in r.note or "unknown" in r.note
    print("  ✓ unknown_label_returns_matched_true")


def test_default_registry_has_critical_labels():
    """关键 label 默认注册"""
    for label in ["close_x", "收下", "前往", "action_btn"]:
        assert ExpectationRegistry.get(label) is not None, f"{label} 应该默认注册"
    print("  ✓ default_registry_has_critical_labels")


def test_verifier_crash_returns_matched_true():
    """verifier 崩 → 不阻塞流程, 默认 matched=True"""

    def _crash(b, a, ctx):
        raise ValueError("intentional crash")

    from backend.automation.state_expectation import Expectation, ExpectKind

    ExpectationRegistry.register(
        "_test_crash",
        Expectation(
            kind=ExpectKind.POPUP_NEXT,
            description="test",
            verifier=_crash,
        ),
    )
    r = verify("_test_crash", make_frame(1), make_frame(2), {})
    assert r.matched
    assert "verifier error" in r.note
    print("  ✓ verifier_crash_returns_matched_true")


def main():
    tests = [
        test_close_x_dismissed_yolo_count_decreased,
        test_close_x_count_unchanged_but_phash_changed_ok,
        test_close_x_count_unchanged_and_phash_unchanged_fail,
        test_popup_next_phash_changed,
        test_popup_next_phash_unchanged,
        test_lobby_stayed_ok_when_lobby_btn_still_match,
        test_lobby_stayed_failed_when_jumped_out,
        test_unknown_label_returns_matched_true,
        test_default_registry_has_critical_labels,
        test_verifier_crash_returns_matched_true,
    ]
    print(f"\nRunning {len(tests)} tests for state_expectation\n" + "=" * 60)
    failed = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"  ✗ {t.__name__}: {e}")
        except Exception as e:
            failed.append((t.__name__, f"EX: {e}"))
            print(f"  ✗ {t.__name__} EX: {e}")
            import traceback; traceback.print_exc()
    print("=" * 60)
    if failed:
        print(f"\n{len(failed)}/{len(tests)} FAILED")
        sys.exit(1)
    print(f"\n{len(tests)}/{len(tests)} PASSED ✓")


if __name__ == "__main__":
    main()
