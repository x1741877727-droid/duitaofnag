"""
cta_detector.py 单元测试.

跑法: python -X utf8 tests/test_cta_detector.py
"""

from __future__ import annotations

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import cv2
import numpy as np

from backend.automation.cta_detector import (
    CtaCandidate,
    NAV_BLACKLIST,
    find_cta_candidates,
    find_main_cta,
)


def make_dark_frame(h=720, w=1280):
    """暗色背景 720p"""
    return np.full((h, w, 3), 30, dtype=np.uint8)


def draw_button(frame, cx, cy, bw=240, bh=80, color_bgr=(180, 60, 200)):
    """画一个 BGR 圆角矩形按钮 (高饱和度)"""
    x1, y1 = cx - bw // 2, cy - bh // 2
    x2, y2 = cx + bw // 2, cy + bh // 2
    cv2.rectangle(frame, (x1, y1), (x2, y2), color_bgr, -1)
    return (x1, y1, x2, y2)


# ─── 测试 ───


def test_finds_single_cta_button():
    """画面只有一个粉色 CTA 按钮 → 检到"""
    f = make_dark_frame()
    draw_button(f, cx=640, cy=560, bw=240, bh=80, color_bgr=(180, 60, 200))  # 粉紫
    cands = find_cta_candidates(f)
    assert len(cands) >= 1, f"应找到至少 1 个候选, got {len(cands)}"
    top = cands[0]
    assert abs(top.cx - 640) < 30 and abs(top.cy - 560) < 30
    assert top.saturation > 80
    print("  ✓ finds_single_cta_button")


def test_skips_grey_banner():
    """灰色 banner (低饱和) → 不算 CTA"""
    f = make_dark_frame()
    draw_button(f, cx=640, cy=560, color_bgr=(120, 120, 120))  # 纯灰
    cands = find_cta_candidates(f)
    # 灰色饱和度低, 应被 sat_min 过滤
    assert len(cands) == 0, f"灰色不该算 CTA, got {len(cands)}"
    print("  ✓ skips_grey_banner")


def test_skips_edge_buttons():
    """画面边缘 (左右栏) 的按钮 → 排除"""
    f = make_dark_frame()
    draw_button(f, cx=50, cy=300, color_bgr=(200, 60, 180))    # 左侧栏
    draw_button(f, cx=1230, cy=300, color_bgr=(200, 60, 180))  # 右侧栏
    cands = find_cta_candidates(f)
    assert len(cands) == 0, f"边缘按钮应排除, got {len(cands)}"
    print("  ✓ skips_edge_buttons")


def test_skips_too_small_or_too_large():
    """太小 (图标) / 太大 (banner) 都排除"""
    f = make_dark_frame()
    draw_button(f, cx=640, cy=300, bw=60, bh=30, color_bgr=(200, 60, 180))    # 太小
    draw_button(f, cx=640, cy=560, bw=600, bh=200, color_bgr=(200, 60, 180))  # 太大
    cands = find_cta_candidates(f)
    assert len(cands) == 0, f"太大太小都该排, got {len(cands)}"
    print("  ✓ skips_too_small_or_too_large")


def test_picks_most_salient_when_multiple():
    """多个候选 → 取饱和度 × 面积最大的"""
    f = make_dark_frame()
    # 小一点低饱和按钮
    draw_button(f, cx=400, cy=560, bw=200, bh=70, color_bgr=(120, 100, 130))
    # 大且高饱和
    draw_button(f, cx=800, cy=560, bw=280, bh=80, color_bgr=(180, 60, 220))
    cands = find_cta_candidates(f)
    assert len(cands) >= 1
    # 排序后第一个应该是饱和度高的那个
    assert abs(cands[0].cx - 800) < 30
    print("  ✓ picks_most_salient_when_multiple")


def test_nav_blacklist_excludes():
    """OCR 识别到 nav 词 → 跳过这个候选"""
    f = make_dark_frame()
    draw_button(f, cx=640, cy=560, color_bgr=(200, 60, 180))

    def fake_ocr(roi):
        return [type("OcrHit", (), {"text": "前往活动"})()]  # 含 nav 词

    cta = find_main_cta(f, ocr_fn=fake_ocr, nav_blacklist=NAV_BLACKLIST)
    assert cta is None, "nav 词应排除"
    print("  ✓ nav_blacklist_excludes")


def test_returns_safe_cta_with_ocr():
    """OCR 识别到非 nav 词 → 返回该候选"""
    f = make_dark_frame()
    draw_button(f, cx=640, cy=560, color_bgr=(200, 60, 180))

    def fake_ocr(roi):
        return [type("OcrHit", (), {"text": "立即砍价"})()]

    cta = find_main_cta(f, ocr_fn=fake_ocr, nav_blacklist=NAV_BLACKLIST)
    assert cta is not None
    assert "立即砍价" in cta.text
    assert abs(cta.cx - 640) < 30
    print("  ✓ returns_safe_cta_with_ocr")


def test_ocr_string_format():
    """ocr_fn 返回 list[str] 也能工作 (兼容性)"""
    f = make_dark_frame()
    draw_button(f, cx=640, cy=560, color_bgr=(200, 60, 180))

    def fake_ocr(roi):
        return ["立即领取"]

    cta = find_main_cta(f, ocr_fn=fake_ocr, nav_blacklist=NAV_BLACKLIST)
    assert cta is not None and "立即领取" in cta.text
    print("  ✓ ocr_string_format")


def test_no_cta_in_empty_frame():
    """空画面 → None"""
    f = make_dark_frame()
    cta = find_main_cta(f, ocr_fn=lambda roi: [])
    assert cta is None
    print("  ✓ no_cta_in_empty_frame")


def test_no_ocr_default_returns_none_safe():
    """默认 (require_verb=True) + 没 OCR → 保守不动"""
    f = make_dark_frame()
    draw_button(f, cx=640, cy=560, color_bgr=(200, 60, 180))
    cta = find_main_cta(f, ocr_fn=None)
    assert cta is None, "默认保守, 不该乱点"
    print("  ✓ no_ocr_default_returns_none_safe")


def test_no_ocr_force_returns_first_blob():
    """显式 require_verb=False → 退回旧"取最显眼"行为 (调用方知道在做啥)"""
    f = make_dark_frame()
    draw_button(f, cx=640, cy=560, color_bgr=(200, 60, 180))
    cta = find_main_cta(f, ocr_fn=None, require_verb=False)
    assert cta is not None
    print("  ✓ no_ocr_force_returns_first_blob")


def test_verb_whitelist_required():
    """OCR 文字不含 verb_whitelist → 不点 (修登录页 '微信登录' 误识)"""
    f = make_dark_frame()
    draw_button(f, cx=640, cy=560, color_bgr=(60, 200, 60))  # 绿色 (微信)

    def fake_ocr(roi):
        return [type("OcrHit", (), {"text": "微信登录"})()]

    cta = find_main_cta(f, ocr_fn=fake_ocr)  # 默认 require_verb=True
    assert cta is None, "微信登录 不在动词白名单, 不该点"
    print("  ✓ verb_whitelist_required")


def test_verb_whitelist_pass():
    """OCR 含动词关键字 → 通过"""
    f = make_dark_frame()
    draw_button(f, cx=640, cy=560, color_bgr=(200, 60, 200))

    for text in ("立即砍价", "立即领取", "我要参与", "收下奖励", "确定", "邀请好友"):
        def _ocr(roi, t=text):
            return [type("OcrHit", (), {"text": t})()]
        cta = find_main_cta(f, ocr_fn=_ocr)
        assert cta is not None, f"含动词 '{text}' 应通过"
    print("  ✓ verb_whitelist_pass")


def test_ocr_no_text_skipped():
    """ROI 内 OCR 出空文字 → 跳过 (该色块可能不是按钮)"""
    f = make_dark_frame()
    draw_button(f, cx=400, cy=560, color_bgr=(180, 60, 200))   # 候选 1
    draw_button(f, cx=800, cy=560, color_bgr=(200, 60, 180))   # 候选 2

    calls = []
    def fake_ocr(roi):
        calls.append(1)
        if len(calls) == 1:
            return []  # 第一个空文字
        return [type("OcrHit", (), {"text": "立即砍价"})()]  # 第二个有

    cta = find_main_cta(f, ocr_fn=fake_ocr, nav_blacklist=NAV_BLACKLIST)
    assert cta is not None
    # 应跳过第一个 (空文字), 返回第二个
    assert "立即砍价" in cta.text
    print("  ✓ ocr_no_text_skipped")


def main():
    tests = [
        test_finds_single_cta_button,
        test_skips_grey_banner,
        test_skips_edge_buttons,
        test_skips_too_small_or_too_large,
        test_picks_most_salient_when_multiple,
        test_nav_blacklist_excludes,
        test_returns_safe_cta_with_ocr,
        test_ocr_string_format,
        test_no_cta_in_empty_frame,
        test_no_ocr_default_returns_none_safe,
        test_no_ocr_force_returns_first_blob,
        test_verb_whitelist_required,
        test_verb_whitelist_pass,
        test_ocr_no_text_skipped,
    ]
    print(f"\nRunning {len(tests)} tests for cta_detector\n" + "=" * 60)
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
