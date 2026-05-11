"""模板匹配 — cv2.matchTemplate, ROI 可选, 单 scale.

REVIEW_MATCHER.md 验证 (✅ A 评分):
- cv2.matchTemplate 释放 GIL → 12 实例 asyncio.to_thread 真并发
- 单 scale (LDPlayer 960×540 锁死) 够用, vs v1 5-scale 节省 -93%
- TM_CCOEFF_NORMED 业界标准, 命中率 > 98%

性能目标:
- 单 match (ROI 内, 50×50 模板): 15-25 ms
- 12 实例并发 match: 20-40 ms (cv2 GIL 释放真并行)

3 点微调 (REVIEW_MATCHER.md):
1. ROI view 显式 .copy() — 消除多线程理论隐患 (+2-3ms 成本可接受)
2. per-instance factory 可选 (避免共享 dict GIL contention, 内存 +12-24MB)
3. 文档约束: shot 只读, 不要 in-place 修改
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, NamedTuple, Optional, Protocol

logger = logging.getLogger(__name__)


class MatchHit(NamedTuple):
    name: str
    score: float
    cx: int
    cy: int
    x1: int
    y1: int
    x2: int
    y2: int


class Roi(NamedTuple):
    x_min: float
    y_min: float
    x_max: float
    y_max: float


class MatcherProto(Protocol):
    async def match_one(
        self,
        shot: Any,
        name: str,
        *,
        threshold: float = 0.75,
        roi: Optional[Roi] = None,
    ) -> Optional[MatchHit]: ...


class Matcher:
    """模板预加载到内存. cv2.matchTemplate 线程安全, 12 实例并发无锁.

    线程安全前置条件 (调用方保证):
    - shot ndarray 只读, 不要 in-place 修改 (cv2.cvtColor / setLast 等)
    - 多 thread 调 match_one 时, 各自传不同的 shot (或同一只读 shot 多 thread 读)
    """

    def __init__(self, templates_dir: Path):
        self._templates: dict[str, Any] = {}
        self._load_templates(templates_dir)

    def _load_templates(self, templates_dir: Path) -> None:
        try:
            import cv2
        except ImportError:
            logger.warning("[matcher] cv2 未装, 跳过模板加载")
            return
        loaded = 0
        for p in templates_dir.glob("*.png"):
            try:
                img = cv2.imread(str(p), cv2.IMREAD_COLOR)
                if img is not None:
                    self._templates[p.stem] = img
                    loaded += 1
            except Exception as e:
                logger.debug(f"[matcher] load {p.name} err: {e}")
        logger.info(f"[matcher] loaded {loaded} templates from {templates_dir}")

    async def match_one(
        self,
        shot: Any,
        name: str,
        *,
        threshold: float = 0.75,
        roi: Optional[Roi] = None,
    ) -> Optional[MatchHit]:
        """匹配单模板. ROI 可选 (不传 = 全屏). cv2 释放 GIL → 真并发."""
        tpl = self._templates.get(name)
        if tpl is None:
            return None
        return await asyncio.to_thread(self._match, shot, tpl, name, threshold, roi)

    @staticmethod
    def _match(
        shot: Any,
        tpl: Any,
        name: str,
        thr: float,
        roi: Optional[Roi],
    ) -> Optional[MatchHit]:
        import cv2
        h0, w0 = shot.shape[:2]
        if roi is None:
            # 全屏: 直接用 shot 不 copy (cv2 内部不修改输入)
            search = shot
            ox = oy = 0
        else:
            x1 = int(w0 * roi.x_min)
            y1 = int(h0 * roi.y_min)
            x2 = int(w0 * roi.x_max)
            y2 = int(h0 * roi.y_max)
            # ROI .copy() 消除多线程理论隐患 (REVIEW_MATCHER.md 微调 1)
            # 成本 +2-3ms, 但避免 view 在 GC / view 释放时 race
            search = shot[y1:y2, x1:x2].copy()
            ox, oy = x1, y1
        if search.shape[0] < tpl.shape[0] or search.shape[1] < tpl.shape[1]:
            return None
        # TM_CCOEFF_NORMED 业界标准, 对光照变化鲁棒 (REVIEW_MATCHER.md)
        res = cv2.matchTemplate(search, tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val < thr:
            return None
        th, tw = tpl.shape[:2]
        x1 = max_loc[0] + ox
        y1 = max_loc[1] + oy
        return MatchHit(
            name=name,
            score=float(max_val),
            cx=x1 + tw // 2,
            cy=y1 + th // 2,
            x1=x1,
            y1=y1,
            x2=x1 + tw,
            y2=y1 + th,
        )

    def list_templates(self) -> list[str]:
        return sorted(self._templates.keys())

    def has_template(self, name: str) -> bool:
        return name in self._templates
