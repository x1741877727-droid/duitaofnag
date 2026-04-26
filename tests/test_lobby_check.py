"""
lobby_check.LobbyQuadDetector 单元测试.

测重点 — 四元融合的"任一不满足就不算大厅", 修半透明弹窗误判 bug.

跑法: python -X utf8 tests/test_lobby_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import numpy as np

from backend.automation.lobby_check import LobbyQuadDetector


# ─── stubs ───


class StubMatch:
    def __init__(self, name, conf, cx=600, cy=400, w=40, h=40):
        self.name = name
        self.confidence = conf
        self.cx = cx
        self.cy = cy
        self.w = w
        self.h = h


class StubMatcher:
    """key=(template_name) → MatchHit or None"""
    def __init__(self, table=None):
        self.table = table or {}

    def match_one(self, frame, name, threshold=0.5):
        m = self.table.get(name)
        if m is None:
            return None
        if m.confidence >= threshold:
            return m
        return None


class StubDet:
    def __init__(self, cls, conf=0.85):
        self.cls = cls
        self.conf = conf


def make_lobby_frame():
    """模拟"干净大厅"帧 — 全亮, 无 4 角阴影"""
    return np.full((720, 1280, 3), 200, dtype=np.uint8)


def make_overlay_frame():
    """模拟"半透明弹窗遮罩"帧 — 4 角暗 (灰度 30), 中央亮 (180)"""
    f = np.full((720, 1280, 3), 30, dtype=np.uint8)  # 默认暗
    # 中央亮
    f[180:540, 320:960] = 200
    return f


# ─── 测试 ───


def test_clean_lobby_after_5_stable_frames():
    """干净大厅 + 模板命中 + 无 overlay + 5 帧稳定 → is_lobby=True"""
    det = LobbyQuadDetector(stable_frames_required=3)
    matcher = StubMatcher({"lobby_start_btn": StubMatch("lobby_start_btn", 0.95)})
    frame = make_lobby_frame()
    yolo_dets = []  # close_x=0 action_btn=0

    # 1, 2 帧不稳定
    r1 = det.check(frame, matcher, yolo_dets)
    assert not r1.is_lobby, f"1 帧不稳定不应算大厅: {r1.note}"
    r2 = det.check(frame, matcher, yolo_dets)
    assert not r2.is_lobby

    # 3 帧后 stable_frames_required=3 满足
    r3 = det.check(frame, matcher, yolo_dets)
    assert r3.is_lobby, f"3 帧稳定 + 全过应算大厅: {r3.note}"
    assert r3.template_hit
    assert r3.template_conf == 0.95
    assert r3.yolo_close_x_count == 0
    assert not r3.has_overlay
    assert r3.phash_stable_frames >= 3
    print("  ✓ clean_lobby_after_5_stable_frames")


def test_template_low_conf_not_lobby():
    """模板分数低于 0.85 → 不算大厅"""
    det = LobbyQuadDetector(stable_frames_required=2)
    matcher = StubMatcher({"lobby_start_btn": StubMatch("lobby_start_btn", 0.70)})
    f = make_lobby_frame()
    for _ in range(5):
        r = det.check(f, matcher, [])
    assert not r.is_lobby
    assert "template=0.70" in r.note
    print("  ✓ template_low_conf_not_lobby")


def test_close_x_present_not_lobby():
    """YOLO 检到 close_x → 即使其他信号 OK 也不算大厅 (这是修 bug 的核心)"""
    det = LobbyQuadDetector(stable_frames_required=2)
    matcher = StubMatcher({"lobby_start_btn": StubMatch("lobby_start_btn", 0.95)})
    f = make_lobby_frame()
    dets = [StubDet("close_x")]
    for _ in range(5):
        r = det.check(f, matcher, dets)
    assert not r.is_lobby, "有 close_x 不应算大厅"
    assert r.yolo_close_x_count == 1
    assert "close_x=1" in r.note
    print("  ✓ close_x_present_not_lobby")


def test_action_btn_present_not_lobby():
    """YOLO 检到 action_btn → 不算大厅"""
    det = LobbyQuadDetector(stable_frames_required=2)
    matcher = StubMatcher({"lobby_start_btn": StubMatch("lobby_start_btn", 0.95)})
    f = make_lobby_frame()
    dets = [StubDet("action_btn", conf=0.8)]
    for _ in range(5):
        r = det.check(f, matcher, dets)
    assert not r.is_lobby
    assert r.yolo_action_btn_count == 1
    print("  ✓ action_btn_present_not_lobby")


def test_overlay_detected_not_lobby():
    """半透明弹窗遮罩 (4 角暗 + 中央亮) → 不算大厅 (修这个 bug 的另一面)"""
    det = LobbyQuadDetector(stable_frames_required=2)
    matcher = StubMatcher({"lobby_start_btn": StubMatch("lobby_start_btn", 0.95)})
    f = make_overlay_frame()
    for _ in range(5):
        r = det.check(f, matcher, [])
    assert not r.is_lobby, "有遮罩不应算大厅"
    assert r.has_overlay
    assert "overlay" in r.note
    print("  ✓ overlay_detected_not_lobby")


def test_unstable_phash_not_lobby():
    """画面不断变化 → phash 不稳定 → 不算大厅 (过渡帧防误判)"""
    det = LobbyQuadDetector(stable_frames_required=3)
    matcher = StubMatcher({"lobby_start_btn": StubMatch("lobby_start_btn", 0.95)})
    # 每帧用不同 seed
    for i in range(5):
        rng = np.random.RandomState(i * 100)
        f = rng.randint(0, 256, (720, 1280, 3), dtype=np.uint8)
        r = det.check(f, matcher, [])
    assert not r.is_lobby, "phash 不稳定不应算大厅"
    print("  ✓ unstable_phash_not_lobby")


def test_reset_clears_phash_history():
    det = LobbyQuadDetector(stable_frames_required=2)
    matcher = StubMatcher({"lobby_start_btn": StubMatch("lobby_start_btn", 0.95)})
    f = make_lobby_frame()
    det.check(f, matcher, [])
    det.check(f, matcher, [])
    assert det.check(f, matcher, []).is_lobby
    det.reset()
    # reset 后第一帧不应立即算大厅
    r = det.check(f, matcher, [])
    assert not r.is_lobby
    print("  ✓ reset_clears_phash_history")


def main():
    tests = [
        test_clean_lobby_after_5_stable_frames,
        test_template_low_conf_not_lobby,
        test_close_x_present_not_lobby,
        test_action_btn_present_not_lobby,
        test_overlay_detected_not_lobby,
        test_unstable_phash_not_lobby,
        test_reset_clears_phash_history,
    ]
    print(f"\nRunning {len(tests)} tests for lobby_check\n" + "=" * 60)
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
