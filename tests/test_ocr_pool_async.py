"""
_ocr_all_async 路由单测 — pool / cache / fallback 三条路径.

为什么必要:
  call sites 全跑 asyncio.to_thread(ocr._ocr_all, shot) → class-level
  _inference_lock 串行 6 实例. _ocr_all_async 改写后接 OcrPool 真并发,
  必须保证三条路径分支正确, 尤其修过的 bug:
    旧版 if hits_raw: 把"OCR 真没识别 (空 list)"误判为"pool 故障", 错误 fallback
    新版用 OcrPool.is_enabled() 状态判故障.

跑法:
    python tests/test_ocr_pool_async.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# Mac 没装 numpy/cv2 — 塞 stub 让顶层 import 过
class _StubArray:
    def __init__(self, size=100, shape=(540, 960, 3)):
        self.size = size
        self.shape = shape

if "numpy" not in sys.modules:
    np_stub = ModuleType("numpy")
    np_stub.ndarray = _StubArray
    np_stub.zeros = lambda *a, **kw: _StubArray()
    np_stub.uint8 = "uint8"
    sys.modules["numpy"] = np_stub
if "cv2" not in sys.modules:
    sys.modules["cv2"] = ModuleType("cv2")


async def main():
    from backend.automation import ocr_dismisser as od
    from backend.automation import ocr_pool as op

    fail = 0

    def make_dismisser():
        d = od.OcrDismisser.__new__(od.OcrDismisser)
        d.max_rounds = 1
        return d

    fake_shot = _StubArray()

    # ─── 替换 cache helper / pool / fallback ───
    state = SimpleNamespace()

    def reset_state():
        state.lookup_calls = 0
        state.store_calls = 0
        state.pool_calls = 0
        state.fallback_calls = 0
        state.lookup_return = (None, 12345, 1700.0)  # default: cache miss
        state.pool_return = []                        # default: pool 空结果
        state.pool_enabled_after = True               # 调 pool 后 is_enabled() 仍为 True
        state.pool_enabled_before = True              # 调用前

    def fake_lookup(shot):
        state.lookup_calls += 1
        return state.lookup_return

    def fake_store(fp, now, result):
        state.store_calls += 1

    async def fake_pool_async(shot):
        state.pool_calls += 1
        return state.pool_return

    def fake_is_enabled():
        # 第一次 (gate check) 用 before, 第二次 (pool 跑完后) 用 after
        if state.pool_calls == 0:
            return state.pool_enabled_before
        return state.pool_enabled_after

    # patch 整条路径
    import backend.automation.ocr_cache as oc
    oc.lookup_full = fake_lookup
    oc.store_full = fake_store
    op.OcrPool.ocr_async = classmethod(lambda cls, shot: fake_pool_async(shot))
    op.OcrPool.is_enabled = classmethod(lambda cls: fake_is_enabled())
    op.OcrPool._executor = object()  # 非 None 即可

    # fallback 路径走 _ocr_all (sync). mock 它.
    def fake_ocr_all(self, shot):
        state.fallback_calls += 1
        return [self.TextHit(text="fallback", cx=0, cy=0)]
    od.OcrDismisser._ocr_all = fake_ocr_all

    # ─── case 1: cache hit → 直接返回, 不进 pool / fallback ───
    reset_state()
    state.lookup_return = ([od.OcrDismisser.TextHit(text="cached", cx=10, cy=20)], None, None)
    d = make_dismisser()
    result = await d._ocr_all_async(fake_shot)
    ok = (
        len(result) == 1
        and result[0].text == "cached"
        and state.pool_calls == 0
        and state.fallback_calls == 0
        and state.store_calls == 0
    )
    print(f"{'PASS' if ok else 'FAIL'}  case1 cache_hit: pool={state.pool_calls} fb={state.fallback_calls} store={state.store_calls}")
    if not ok:
        fail += 1

    # ─── case 2: pool 启用 + cache miss + pool 返回非空 → store + 返回 ───
    reset_state()
    state.pool_return = [op.OcrHit(text="poolhit", cx=5, cy=6, score=0.9)]
    d = make_dismisser()
    result = await d._ocr_all_async(fake_shot)
    ok = (
        len(result) == 1
        and result[0].text == "poolhit"
        and state.pool_calls == 1
        and state.fallback_calls == 0
        and state.store_calls == 1
    )
    print(f"{'PASS' if ok else 'FAIL'}  case2 pool_hit: pool={state.pool_calls} fb={state.fallback_calls} store={state.store_calls}")
    if not ok:
        fail += 1

    # ─── case 3 (核心修复): pool 返回空 list → 视为合法结果, 不 fallback ───
    reset_state()
    state.pool_return = []  # OCR 真没识别到任何文字 (合法)
    d = make_dismisser()
    result = await d._ocr_all_async(fake_shot)
    ok = (
        result == []
        and state.pool_calls == 1
        and state.fallback_calls == 0  # 关键: 旧版 bug 这里会 == 1
        and state.store_calls == 1     # 空结果也 cache (避免下一帧再跑 OCR)
    )
    print(f"{'PASS' if ok else 'FAIL'}  case3 pool_empty_no_fallback: pool={state.pool_calls} fb={state.fallback_calls} store={state.store_calls}")
    if not ok:
        fail += 1

    # ─── case 4: pool 启用 → 调 ocr_async 时 pool 故障被 disable → fallback ───
    reset_state()
    state.pool_enabled_after = False  # ocr_async 跑完后 pool 自动关
    state.pool_return = []
    d = make_dismisser()
    result = await d._ocr_all_async(fake_shot)
    ok = (
        len(result) == 1
        and result[0].text == "fallback"
        and state.pool_calls == 1
        and state.fallback_calls == 1
        and state.store_calls == 0  # fallback 不 store (sync _ocr_all 自带 cache)
    )
    print(f"{'PASS' if ok else 'FAIL'}  case4 pool_crash_fallback: pool={state.pool_calls} fb={state.fallback_calls} store={state.store_calls}")
    if not ok:
        fail += 1

    # ─── case 5: pool 禁用 (启动失败 / 环境变量关闭) → 直接 fallback ───
    reset_state()
    state.pool_enabled_before = False
    d = make_dismisser()
    result = await d._ocr_all_async(fake_shot)
    ok = (
        len(result) == 1
        and result[0].text == "fallback"
        and state.pool_calls == 0  # pool 完全没被调
        and state.fallback_calls == 1
    )
    print(f"{'PASS' if ok else 'FAIL'}  case5 pool_disabled: pool={state.pool_calls} fb={state.fallback_calls}")
    if not ok:
        fail += 1

    # ─── case 6: 边界 — None / size 0 → 直接 [] ───
    reset_state()
    d = make_dismisser()
    result = await d._ocr_all_async(None)
    empty_shot = _StubArray(size=0)
    result2 = await d._ocr_all_async(empty_shot)
    ok = (
        result == []
        and result2 == []
        and state.lookup_calls == 0
        and state.pool_calls == 0
        and state.fallback_calls == 0
    )
    print(f"{'PASS' if ok else 'FAIL'}  case6 boundary: lookup={state.lookup_calls} pool={state.pool_calls}")
    if not ok:
        fail += 1

    print(f"\n{'ALL OK' if fail == 0 else f'{fail} FAILED'}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
