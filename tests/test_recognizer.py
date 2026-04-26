"""
recognizer + memory_l1 单元测试.

5 层 early-exit 调度器 + L1 Memory 复读机.

跑法:  python tests/test_recognizer.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import numpy as np

from backend.automation.recognizer import Hit, Recognizer, Target, Tier
from backend.automation.memory_l1 import FrameMemory


# ───────────── Stub 工具：模拟 matcher / yolo / ocr ─────────────


class StubMatchHit:
    """模拟 ScreenMatcher.MatchHit"""
    def __init__(self, name, conf, cx, cy, w=40, h=40):
        self.name = name
        self.confidence = conf
        self.cx = cx
        self.cy = cy
        self.w = w
        self.h = h


class StubMatcher:
    def __init__(self, hit: StubMatchHit | None = None):
        self.hit = hit
        self.calls = 0

    def find_any(self, frame, names, threshold=0.85):
        self.calls += 1
        if self.hit and self.hit.confidence >= threshold:
            return self.hit
        return None


class StubYoloDetection:
    """模拟 yolo_dismisser.Detection / yolo_detector.Detection"""
    def __init__(self, cls, conf, x1, y1, x2, y2):
        self.cls = cls
        self.conf = conf
        self.bbox = [x1, y1, x2, y2]


class StubOcrHit:
    def __init__(self, text, cx, cy, conf=0.9):
        self.text = text
        self.cx = cx
        self.cy = cy
        self.conf = conf
        self.bbox = [cx - 20, cy - 10, cx + 20, cy + 10]


def make_frame(seed=0, size=(720, 1280)):
    """随机但确定性的 frame, 用于 phash 测试"""
    rng = np.random.RandomState(seed)
    return rng.randint(0, 256, size + (3,), dtype=np.uint8)


# ────────────────── Recognizer 测试 ──────────────────


def test_tier0_template_hit_skips_others():
    """Tier 0 命中 → 不调 yolo / ocr"""
    matcher = StubMatcher(StubMatchHit("lobby_start_btn", 0.92, 1100, 600))
    yolo_calls = []
    ocr_calls = []

    rec = Recognizer(
        matcher=matcher,
        yolo_detect_fn=lambda f: yolo_calls.append(1) or [],
        ocr_fn=lambda f, roi: ocr_calls.append(1) or [],
    )
    target = Target(
        name="lobby_start_btn",
        template_names=["lobby_start_btn"],
        template_threshold=0.85,
        yolo_classes=["close_x"],
        ocr_keywords=["开始"],
    )
    hit = rec.find(make_frame(), target)

    assert hit is not None, "应命中"
    assert hit.tier == Tier.TEMPLATE
    assert hit.label == "lobby_start_btn"
    assert (hit.cx, hit.cy) == (1100, 600)
    assert matcher.calls == 1
    assert len(yolo_calls) == 0, "Tier 0 命中不应跑 YOLO"
    assert len(ocr_calls) == 0, "Tier 0 命中不应跑 OCR"
    print("  ✓ tier0_template_hit_skips_others")


def test_tier0_miss_falls_to_yolo():
    """Tier 0 没命中 → Tier 2 YOLO 接手"""
    matcher = StubMatcher(None)
    rec = Recognizer(
        matcher=matcher,
        yolo_detect_fn=lambda f: [
            StubYoloDetection("close_x", 0.85, 1200, 50, 1240, 90),
        ],
    )
    target = Target(
        name="close_x",
        template_names=["fake_template"],
        yolo_classes=["close_x"],
        yolo_threshold=0.7,
    )
    hit = rec.find(make_frame(), target)
    assert hit is not None
    assert hit.tier == Tier.YOLO
    assert hit.label == "close_x"
    assert hit.cx == 1220 and hit.cy == 70
    print("  ✓ tier0_miss_falls_to_yolo")


def test_tier1_memory_hit_skips_yolo_ocr():
    """Tier 1 Memory 命中 → 不调 YOLO / OCR"""
    db = Path(tempfile.mktemp(suffix=".db"))
    mem = FrameMemory(db)
    f = make_frame(seed=42)
    mem.remember(f, "lobby_start_btn", (1100, 600), (40, 40), success=True)

    yolo_calls = []
    rec = Recognizer(
        matcher=StubMatcher(None),
        yolo_detect_fn=lambda f: yolo_calls.append(1) or [],
        memory=mem,
    )
    target = Target(
        name="lobby_start_btn",
        template_names=["lobby_start_btn"],
        yolo_classes=["close_x"],
        use_memory=True,
    )
    hit = rec.find(f, target)
    assert hit is not None
    assert hit.tier == Tier.MEMORY
    assert (hit.cx, hit.cy) == (1100, 600)
    assert len(yolo_calls) == 0, "Memory 命中不应跑 YOLO"
    mem.close()
    db.unlink(missing_ok=True)
    print("  ✓ tier1_memory_hit_skips_yolo_ocr")


def test_tier3_ocr_inside_yolo_bbox():
    """YOLO 给位置, OCR 在 bbox 内匹关键字"""
    rec = Recognizer(
        matcher=StubMatcher(None),
        yolo_detect_fn=lambda f: [
            StubYoloDetection("action_btn", 0.85, 500, 400, 700, 460),
        ],
        ocr_fn=lambda f, roi: [
            StubOcrHit("收下奖励", cx=600, cy=430, conf=0.95),
        ],
    )
    target = Target(
        name="dismiss_action",
        yolo_classes=["action_btn"],
        ocr_keywords=["收下", "确定"],
        ocr_blacklist=["前往", "参加"],
    )
    hit = rec.find(make_frame(), target)
    assert hit is not None
    assert hit.tier == Tier.OCR
    assert hit.label == "收下"
    assert hit.cx == 600 and hit.cy == 430
    print("  ✓ tier3_ocr_inside_yolo_bbox")


def test_ocr_blacklist_filters_nav_words():
    """nav 词('前往') → 排除"""
    rec = Recognizer(
        matcher=StubMatcher(None),
        yolo_detect_fn=lambda f: [
            StubYoloDetection("action_btn", 0.85, 500, 400, 700, 460),
        ],
        ocr_fn=lambda f, roi: [
            StubOcrHit("前往活动", cx=600, cy=430),  # nav, 必须排
        ],
    )
    target = Target(
        name="dismiss_action",
        yolo_classes=["action_btn"],
        ocr_keywords=["收下", "前往"],  # 关键字含'前往'
        ocr_blacklist=["前往"],          # 但黑名单优先
    )
    hit = rec.find(make_frame(), target)
    assert hit is None, "黑名单匹中应跳过, 即使关键字也含此词"
    print("  ✓ ocr_blacklist_filters_nav_words")


def test_all_miss_returns_none():
    """全 miss → None + stats MISS+1"""
    rec = Recognizer(
        matcher=StubMatcher(None),
        yolo_detect_fn=lambda f: [],
    )
    target = Target(
        name="anything",
        template_names=["x"],
        yolo_classes=["close_x"],
    )
    hit = rec.find(make_frame(), target)
    assert hit is None
    s = rec.stats()
    assert s["counts"]["MISS"] == 1
    assert s["counts"]["TEMPLATE"] == 0
    print("  ✓ all_miss_returns_none")


def test_stats_distribution():
    """跑 3 轮: 1 次 Tier 0, 1 次 Tier 2, 1 次 MISS"""
    matcher = StubMatcher(StubMatchHit("x", 0.92, 100, 100))
    yolo_results = [
        [StubYoloDetection("close_x", 0.85, 0, 0, 40, 40)],
        [],
    ]
    yolo_idx = [0]

    def yolo_fn(f):
        i = yolo_idx[0]
        yolo_idx[0] += 1
        return yolo_results[i] if i < len(yolo_results) else []

    rec = Recognizer(matcher=matcher, yolo_detect_fn=yolo_fn)
    t1 = Target(name="t0", template_names=["x"], template_threshold=0.85)
    rec.find(make_frame(), t1)  # tier0 hit
    matcher.hit = None
    t2 = Target(name="t2", template_names=["x"], yolo_classes=["close_x"])
    rec.find(make_frame(), t2)  # tier2 hit
    rec.find(make_frame(), t2)  # all miss
    s = rec.stats()
    assert s["counts"]["TEMPLATE"] == 1
    assert s["counts"]["YOLO"] == 1
    assert s["counts"]["MISS"] == 1
    print("  ✓ stats_distribution")


def test_record_callback_per_tier():
    """record callback 每跑过一层都要调一次"""
    matcher = StubMatcher(None)
    rec = Recognizer(
        matcher=matcher,
        yolo_detect_fn=lambda f: [StubYoloDetection("close_x", 0.85, 0, 0, 40, 40)],
    )
    seen_tiers = []
    rec.find(
        make_frame(),
        Target(name="x", template_names=["x"], yolo_classes=["close_x"]),
        record=lambda tier, info: seen_tiers.append(tier),
    )
    # Tier 0 跑了 (template miss), Tier 2 跑了 (yolo hit)
    assert Tier.TEMPLATE in seen_tiers
    assert Tier.YOLO in seen_tiers
    print("  ✓ record_callback_per_tier")


# ────────────────── FrameMemory 测试 ──────────────────


def test_memory_empty_returns_none():
    db = Path(tempfile.mktemp(suffix=".db"))
    mem = FrameMemory(db)
    assert mem.query(make_frame(), "x") is None
    mem.close()
    db.unlink(missing_ok=True)
    print("  ✓ memory_empty_returns_none")


def test_memory_remember_then_query_same_frame():
    db = Path(tempfile.mktemp(suffix=".db"))
    mem = FrameMemory(db)
    f = make_frame(seed=1)
    mem.remember(f, "btn", (500, 300), (40, 40), success=True)
    hit = mem.query(f, "btn")
    assert hit is not None
    assert hit.tier == Tier.MEMORY
    assert (hit.cx, hit.cy) == (500, 300)
    mem.close()
    db.unlink(missing_ok=True)
    print("  ✓ memory_remember_then_query_same_frame")


def test_memory_different_target_no_hit():
    db = Path(tempfile.mktemp(suffix=".db"))
    mem = FrameMemory(db)
    f = make_frame(seed=2)
    mem.remember(f, "btn_a", (100, 100), success=True)
    assert mem.query(f, "btn_b") is None
    mem.close()
    db.unlink(missing_ok=True)
    print("  ✓ memory_different_target_no_hit")


def test_memory_disables_after_5_failures():
    """连续 5 次失败 + fail > succ → 该记忆失效"""
    db = Path(tempfile.mktemp(suffix=".db"))
    mem = FrameMemory(db)
    f = make_frame(seed=3)
    # 1 次成功 + 6 次失败 → fail > succ, fail >= 5
    mem.remember(f, "btn", (100, 100), success=True)
    for _ in range(6):
        mem.remember(f, "btn", (100, 100), success=False)
    assert mem.query(f, "btn") is None, "失败率高的记忆应被禁用"
    mem.close()
    db.unlink(missing_ok=True)
    print("  ✓ memory_disables_after_5_failures")


def test_memory_stats():
    db = Path(tempfile.mktemp(suffix=".db"))
    mem = FrameMemory(db)
    mem.remember(make_frame(seed=10), "btn", (100, 100), success=True)
    mem.remember(make_frame(seed=11), "btn", (200, 100), success=True)
    s = mem.stats()
    assert s["rows"] == 2
    assert s["succ"] >= 2
    mem.close()
    db.unlink(missing_ok=True)
    print("  ✓ memory_stats")


# ────────────────── Runner ──────────────────


def main():
    tests = [
        test_tier0_template_hit_skips_others,
        test_tier0_miss_falls_to_yolo,
        test_tier1_memory_hit_skips_yolo_ocr,
        test_tier3_ocr_inside_yolo_bbox,
        test_ocr_blacklist_filters_nav_words,
        test_all_miss_returns_none,
        test_stats_distribution,
        test_record_callback_per_tier,
        test_memory_empty_returns_none,
        test_memory_remember_then_query_same_frame,
        test_memory_different_target_no_hit,
        test_memory_disables_after_5_failures,
        test_memory_stats,
    ]
    print(f"\nRunning {len(tests)} tests for recognizer + memory_l1\n" + "=" * 60)
    failed = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"  ✗ {t.__name__}: {e}")
        except Exception as e:
            failed.append((t.__name__, f"EXCEPTION: {e}"))
            print(f"  ✗ {t.__name__} EXCEPTION: {e}")
            import traceback
            traceback.print_exc()
    print("=" * 60)
    if failed:
        print(f"\n{len(failed)}/{len(tests)} FAILED")
        sys.exit(1)
    print(f"\n{len(tests)}/{len(tests)} PASSED ✓")


if __name__ == "__main__":
    main()
