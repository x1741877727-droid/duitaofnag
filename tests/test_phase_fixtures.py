"""
Phase fixture 测试 — 不进 emulator, 不跑 ML, 只验纯函数 policy 逻辑.

Fixture 约定见 tests/phase_fixtures/README.md.

跑法:
    python -m pytest tests/test_phase_fixtures.py -v
    # 单个 fixture:
    python -m pytest tests/test_phase_fixtures.py -v -k "memory_hit"

加新 phase 测试: 在 _ADAPTERS 注册一个 (fixture_dir, run_fn) 对, 不需要改 harness 主体.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

# 让 backend 可 import
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


# ─────────────────── 通用工具 ───────────────────


def _to_obj(d):
    """JSON dict → SimpleNamespace (递归), list/None 原样返."""
    if isinstance(d, dict):
        return SimpleNamespace(**{k: _to_obj(v) for k, v in d.items()})
    if isinstance(d, list):
        return [_to_obj(x) for x in d]
    return d


def _load_fixture(p: Path) -> dict:
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _assert_action(actual, expected):
    """断言 PhaseAction (或 None) 跟 expected dict 一致.
    expected=None → actual 必须 None.
    expected dict → actual 必须 PhaseAction, 字段子集匹配 (没列的字段不验)."""
    if expected is None:
        assert actual is None, f"期望 None, 实际 {actual!r}"
        return
    assert actual is not None, f"期望 action, 实际 None"
    for k, v in expected.items():
        got = getattr(actual, k, None)
        assert got == v, f"字段 {k}: 期望 {v!r}, 实际 {got!r}"


# ─────────────────── adapter: p2_decide ───────────────────


def _run_p2_decide(fix: dict):
    """构造 Perception + RunContext, 跑 phases.p2_policy.decide()."""
    from backend.automation.phases.p2_perception import Perception
    from backend.automation.phases.p2_policy import decide
    from backend.automation.phase_base import RunContext

    p_raw = fix.get("perception", {})

    # 把 dict 字段转成 SimpleNamespace (Hit / Detection / MatchHit 都是属性访问)
    p_kwargs: dict[str, Any] = {}
    for k, v in p_raw.items():
        if v is None:
            p_kwargs[k] = None
        elif k in ("yolo_close_xs", "yolo_action_btns", "yolo_dets_raw"):
            p_kwargs[k] = [_to_obj(d) for d in v]
        elif k in ("template_close_x", "template_dismiss_btn"):
            # tuple (template_name, MatchHit) — MatchHit 是 SimpleNamespace
            tn, h = v
            p_kwargs[k] = (tn, _to_obj(h))
        else:
            p_kwargs[k] = _to_obj(v)

    p = Perception(**p_kwargs)

    # 最小 RunContext — decide 只用 is_blacklisted
    ctx_raw = fix.get("ctx", {})
    blacklist = [tuple(c) for c in ctx_raw.get("blacklist_coords", [])]

    ctx = RunContext(
        device=None, matcher=None, runner=None,
    )
    ctx.blacklist_coords = blacklist

    return decide(p, ctx)


# ─────────────────── adapter: screen_classify ───────────────────


def _run_screen_classify(fix: dict):
    """跑 screen_classifier.classify(yolo_dets, login_hit, brightness)."""
    from backend.automation.screen_classifier import classify

    inp = fix["input"]
    dets = [_to_obj(d) for d in inp.get("yolo_dets", [])]
    return classify(
        yolo_dets=dets,
        lobby_login_template_hit=bool(inp.get("lobby_login_template_hit", False)),
        frame_brightness=float(inp.get("frame_brightness", 128)),
    )


def _assert_screen_kind(actual, expected):
    """expected 是字符串 ('LOBBY' / 'POPUP' / ...), actual 是 ScreenKind enum."""
    assert actual is not None, "classify 不应该返 None"
    assert actual.name == expected, f"期望 {expected}, 实际 {actual.name}"


# ─────────────────── 注册表 ───────────────────


# fixture 目录 → (运行函数, 断言函数)
_ADAPTERS = {
    "p2_decide": (_run_p2_decide, _assert_action),
    "screen_classify": (_run_screen_classify, _assert_screen_kind),
}


# ─────────────────── pytest 入口 ───────────────────


def _collect_fixtures():
    """扫 phase_fixtures/<adapter>/*.json, 生成 pytest param 列表."""
    base = _PROJECT_ROOT / "tests" / "phase_fixtures"
    out = []
    for adapter_dir, _ in _ADAPTERS.items():
        d = base / adapter_dir
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            out.append(pytest.param(adapter_dir, f, id=f"{adapter_dir}/{f.stem}"))
    return out


@pytest.mark.parametrize("adapter,fixture_path", _collect_fixtures())
def test_phase_fixture(adapter: str, fixture_path: Path):
    fix = _load_fixture(fixture_path)
    run_fn, assert_fn = _ADAPTERS[adapter]
    actual = run_fn(fix)
    assert_fn(actual, fix.get("expected"))


# ─────────────────── 直接跑 (无 pytest) ───────────────────


if __name__ == "__main__":
    """python tests/test_phase_fixtures.py — 不依赖 pytest, 退化跑全集."""
    fail = 0
    for adapter, (run_fn, assert_fn) in _ADAPTERS.items():
        d = _PROJECT_ROOT / "tests" / "phase_fixtures" / adapter
        for f in sorted(d.glob("*.json")):
            fix = _load_fixture(f)
            try:
                actual = run_fn(fix)
                assert_fn(actual, fix.get("expected"))
                print(f"PASS  {adapter}/{f.stem}")
            except AssertionError as e:
                fail += 1
                print(f"FAIL  {adapter}/{f.stem}: {e}")
            except Exception as e:
                fail += 1
                print(f"ERROR {adapter}/{f.stem}: {type(e).__name__}: {e}")
    print(f"\n{'OK' if fail == 0 else f'{fail} FAILED'}")
    sys.exit(1 if fail else 0)
