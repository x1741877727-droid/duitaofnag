"""P4 — 队长选模式 + 选地图 + 确认 (v2 真实现).

设计 (5 子步 + 状态机, 每 round 1 动作):
  open    → tap 模式名 (90, 92)            固定坐标
  mode    → OCR 找用户配置 game_mode tap   非固定 (用户选不同模式)
  map     → OCR 找用户配置 game_map tap    非固定
  fill    → ROI 颜色检测补位勾选           只读, 不 tap
  confirm → tap 确定 (885, 507)            固定坐标

Tap 前 yolo close_x 弹窗检查 (仅 open/confirm 两个固定 tap 步骤;
mode/map 主动 tap 用户选项 OCR 结果, 不需要拦弹窗 — 如果弹窗挡了 OCR 会找不到 keyword
重试时 yolo close_x 兜底).

坐标来源: 26000+ 历史 decision 日志统计 (>80% 一致).
v1 4.1 秒 → v2 目标 1.5-2.2 秒 (-50%).
"""
from __future__ import annotations

import logging
from typing import Optional

from ..ctx import RunContext
from ..perception.yolo import Roi
from ..phase_base import (
    PhaseStep, PhaseAction,
    step_next, step_retry, step_fail,
)

logger = logging.getLogger(__name__)

# ─────────── 固定坐标 (LDPlayer 960×540) ───────────
FIXED_TAPS = {
    "open":    (90, 92),     # 模式名 / "开始游戏"下方 60px
    "confirm": (885, 507),   # 右下"确定"
}

# ─────────── 验证关键词 ───────────
MODE_MENU_KEYWORDS = [
    "团队竞技", "团竞", "经典", "创意工坊", "军备", "迷你战争",
    "轮换", "特训",
]
MAP_LIST_KEYWORDS = [
    "海岛", "沙漠", "雨林", "雪地", "狙击", "大桥", "仓库", "码头",
    "图书", "维寒迪",
]
CONFIRM_KEYWORDS = ["确定"]

# game_map 容错: 配置 "狙击" → 匹配 ["狙击", "击团竞", "大桥"]
MAP_FALLBACK_MAP = {
    "狙击": ["狙击", "击团竞", "大桥"],
    "经典": ["经典", "经典团竞", "经典仓库", "仓库"],
    "军备": ["军备", "军备图书", "图书"],
    "海岛": ["海岛"],
    "沙漠": ["沙漠"],
    "雨林": ["雨林"],
    "雪地": ["雪地"],
}

# ─────────── 重试 / 守门 ───────────
TAP_VERIFY_RETRY = 3
SEARCH_RETRY = 3
POPUP_INTERCEPT_LIMIT = 5

# ─────────── ROI ───────────
MODE_OCR_ROI = (0.0, 0.10, 0.30, 0.90)     # 左侧栏
MAP_OCR_ROI = (0.25, 0.10, 0.85, 0.85)     # 中央列表
CONFIRM_OCR_ROI = (0.70, 0.70, 1.0, 1.0)   # 右下确定
FILL_CHECKBOX_ROI = (0.49, 0.85, 0.55, 0.91)
POPUP_CHECK_ROI = (0.50, 0.0, 1.0, 0.5)


class P4MapSetup:
    name = "P4"
    max_seconds = 30.0
    round_interval_s = 0.3

    async def enter(self, ctx: RunContext) -> None:
        ctx.reset_phase_state()
        # 读用户配置
        game_mode = "团队竞技"
        game_map = "狙击"
        try:
            from backend.config import config
            settings = config.settings
            if getattr(settings, "game_mode", None):
                game_mode = settings.game_mode
            if getattr(settings, "game_map", None):
                game_map = settings.game_map
        except Exception as e:
            logger.debug(f"P4 read config err: {e}")

        map_keywords = MAP_FALLBACK_MAP.get(game_map, [game_map])

        ctx._p4 = {
            "sub_step": "open",
            "tap_done": False,
            "tap_retry": 0,
            "search_retry": 0,
            "popup_intercept_count": 0,
            "game_mode": game_mode,
            "game_map": game_map,
            "map_keywords": map_keywords,
            "fill_state": None,
        }

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        shot = ctx.current_shot
        if shot is None:
            ctx.mark("yolo_start"); ctx.mark("yolo_done"); ctx.mark("decide")
            return step_retry(note="P4: no shot")

        st = ctx._p4
        sub = st["sub_step"]

        # ── 1. Tap 前弹窗检查: 只在 "open" 子步 (主菜单状态, panel 未打开) 跑.
        # mode/map/fill 在 panel 内, confirm 时 panel 还开着 — 这些状态 yolo
        # 看到的 close_x 多半是 panel 自带, 误识 popup 会把 panel 关掉.
        if sub == "open":
            ctx.mark("yolo_start")
            popup_tap = await self._find_popup(ctx, shot)
            ctx.mark("yolo_done")
            if popup_tap and st["popup_intercept_count"] < POPUP_INTERCEPT_LIMIT:
                x, y = popup_tap
                ctx.add_blacklist(x, y, ttl=3.0)
                st["popup_intercept_count"] += 1
                st["tap_done"] = False
                ctx.mark("decide")
                return step_retry(
                    note=f"P4[open]: popup intercept @({x},{y}) "
                         f"({st['popup_intercept_count']}/{POPUP_INTERCEPT_LIMIT})",
                    outcome_hint="popup_intercept",
                    action=PhaseAction(kind="tap", x=x, y=y, target="popup_close"),
                )
        else:
            ctx.mark("yolo_start"); ctx.mark("yolo_done")

        # ── 2. 分支 ──
        if sub == "open":
            # P4-1 改 OCR 找"开始游戏" → tap 下方 60px (跟 v1 一致).
            # 实测固定坐标 (90, 92) 历史 v1 数据只有 36% 一致, 现版本游戏 UI
            # 模式名 cy 漂移. OCR 找"开始游戏"文字 + cy+60 一直可靠.
            return await self._step_open_via_ocr(ctx, shot)
        if sub == "mode":
            # mode 优先找用户配置 game_mode, 找不到任意模式名兜底 (说明菜单出来了)
            return await self._step_search_and_tap(
                ctx, shot, "mode",
                keywords=[st["game_mode"]] + MODE_MENU_KEYWORDS,
                search_roi=MODE_OCR_ROI,
                target_text=st["game_mode"],
                verify_keywords=MAP_LIST_KEYWORDS,
                verify_roi=MAP_OCR_ROI,
                next_sub="map",
            )
        if sub == "map":
            return await self._step_search_and_tap(
                ctx, shot, "map",
                keywords=st["map_keywords"],
                search_roi=MAP_OCR_ROI,
                target_text=st["game_map"],
                verify_keywords=CONFIRM_KEYWORDS,
                verify_roi=CONFIRM_OCR_ROI,
                next_sub="fill",
            )
        if sub == "fill":
            return await self._step_fill_check(ctx, shot)
        if sub == "confirm":
            return await self._step_fixed_tap_with_verify(
                ctx, shot, "confirm",
                tap_coord=FIXED_TAPS["confirm"],
                verify_keywords=[],
                verify_roi=None,
                next_sub="done",
                verify_use_lobby=True,
            )
        if sub == "done":
            ctx.mark("decide")
            fill = st.get("fill_state")
            note = f"P4 done game_mode={st['game_mode']} game_map={st['game_map']}"
            if fill:
                note += f" fill_ratio={fill.get('ratio', 0):.2f}"
            return step_next(note=note, outcome_hint="map_setup_ok")

        ctx.mark("decide")
        return step_fail(note=f"P4 unknown sub_step={sub}", outcome_hint="bug")

    # ════════════════════════════════════════
    # 子步骤
    # ════════════════════════════════════════

    async def _step_open_via_ocr(
        self, ctx: RunContext, shot,
    ) -> PhaseStep:
        """P4-1 用 OCR 找"开始游戏" + tap 下方 60px (跟 v1 一致 single_runner:585).

        v1 实测: "开始游戏"文字位置稳定, 但下方模式名 (cx, cy+60) 才是真正打开
        地图菜单的按钮. 比固定坐标 (90, 92) 鲁棒多了.
        """
        st = ctx._p4

        if not st["tap_done"]:
            try:
                hits = await ctx.ocr.recognize(
                    shot, roi=Roi(0.0, 0.0, 0.30, 0.35),
                )
            except Exception as e:
                logger.debug(f"P4[open] OCR err: {e}")
                hits = []

            target = None
            for h in hits:
                if "开始游戏" in self._hit_text(h):
                    target = h
                    break

            if target is None:
                st["search_retry"] += 1
                if st["search_retry"] >= SEARCH_RETRY:
                    ctx.mark("decide")
                    return step_fail(
                        note="P4[open] OCR 找不到'开始游戏'",
                        outcome_hint="search_fail_open",
                    )
                ctx.mark("decide")
                return step_retry(
                    note=f"P4[open] OCR miss '开始游戏' "
                         f"({st['search_retry']}/{SEARCH_RETRY})",
                    outcome_hint="search_miss_open",
                )

            cx, cy = self._hit_center(target)
            tap_y = cy + 60   # v1: 模式名在'开始游戏'下方 60px
            st["tap_done"] = True
            ctx.mark("decide")
            return step_retry(
                note=f"P4[open] OCR hit '开始游戏'@({cx},{cy}) → "
                     f"tap mode_name@({cx},{tap_y})",
                outcome_hint="tap_open",
                action=PhaseAction(
                    kind="tap", x=cx, y=tap_y,
                    target="p4_open_mode_name", conf=0.9,
                ),
            )

        # verify (mode 菜单出来了 = 任一模式名可见)
        verified = await self._ocr_contains_any(
            ctx, shot, MODE_MENU_KEYWORDS, MODE_OCR_ROI,
        )
        ctx.mark("decide")
        if verified:
            st["sub_step"] = "mode"
            st["tap_done"] = False
            st["tap_retry"] = 0
            st["search_retry"] = 0
            st["popup_intercept_count"] = 0
            return step_retry(
                note="P4[open] verify OK → mode",
                outcome_hint="verify_ok_open",
            )

        st["tap_retry"] += 1
        if st["tap_retry"] >= TAP_VERIFY_RETRY:
            return step_fail(
                note="P4[open]: verify fail 3 次",
                outcome_hint="verify_fail_open",
            )
        st["tap_done"] = False
        return step_retry(
            note=f"P4[open] verify miss, retry "
                 f"({st['tap_retry']}/{TAP_VERIFY_RETRY})",
            outcome_hint="verify_miss_open",
        )

    async def _step_fixed_tap_with_verify(
        self, ctx: RunContext, shot, sub: str,
        tap_coord: tuple, verify_keywords: list, verify_roi: Optional[tuple],
        next_sub: str, verify_use_lobby: bool = False,
    ) -> PhaseStep:
        """open / confirm: 固定坐标 tap + verify."""
        st = ctx._p4

        if not st["tap_done"]:
            st["tap_done"] = True
            ctx.mark("decide")
            return step_retry(
                note=f"P4[{sub}]: fixed tap@({tap_coord[0]},{tap_coord[1]})",
                outcome_hint=f"tap_{sub}",
                action=PhaseAction(
                    kind="tap", x=tap_coord[0], y=tap_coord[1],
                    target=f"p4_{sub}_fixed", conf=1.0,
                ),
            )

        verified = False
        if verify_use_lobby:
            try:
                dets = await ctx.yolo.detect(shot, conf_thresh=0.40)
            except Exception:
                dets = []
            if any(d.name == "lobby" and d.conf >= 0.55 for d in dets):
                ctx.lobby_streak += 1
                if ctx.lobby_streak >= 2:
                    verified = True
            else:
                ctx.lobby_streak = 0
        else:
            verified = await self._ocr_contains_any(
                ctx, shot, verify_keywords, verify_roi,
            )

        ctx.mark("decide")
        if verified:
            st["sub_step"] = next_sub
            st["tap_done"] = False
            st["tap_retry"] = 0
            st["search_retry"] = 0
            st["popup_intercept_count"] = 0
            return step_retry(
                note=f"P4[{sub}] verify OK → {next_sub}",
                outcome_hint=f"verify_ok_{sub}",
            )

        st["tap_retry"] += 1
        if st["tap_retry"] >= TAP_VERIFY_RETRY:
            return step_fail(
                note=f"P4[{sub}]: verify fail 3 次",
                outcome_hint=f"verify_fail_{sub}",
            )
        st["tap_done"] = False
        return step_retry(
            note=f"P4[{sub}] verify miss, retry tap "
                 f"({st['tap_retry']}/{TAP_VERIFY_RETRY})",
            outcome_hint=f"verify_miss_{sub}",
        )

    async def _step_search_and_tap(
        self, ctx: RunContext, shot, sub: str,
        keywords: list, search_roi: tuple, target_text: str,
        verify_keywords: list, verify_roi: tuple, next_sub: str,
    ) -> PhaseStep:
        """mode / map: OCR 找 keyword 并 tap, 下 round verify."""
        st = ctx._p4

        if not st["tap_done"]:
            try:
                hits = await ctx.ocr.recognize(shot, roi=Roi(*search_roi))
            except Exception as e:
                logger.debug(f"P4[{sub}] OCR search err: {e}")
                hits = []

            target = None
            for h in hits:
                text = self._hit_text(h)
                if not text:
                    continue
                for kw in keywords:
                    if kw and kw in text:
                        target = h
                        break
                if target:
                    break

            if target is None:
                st["search_retry"] += 1
                if st["search_retry"] >= SEARCH_RETRY:
                    ctx.mark("decide")
                    return step_fail(
                        note=f"P4[{sub}]: 找不到 '{target_text}' "
                             f"({st['search_retry']} attempts)",
                        outcome_hint=f"search_fail_{sub}",
                    )
                ctx.mark("decide")
                return step_retry(
                    note=f"P4[{sub}]: OCR miss '{target_text}' "
                         f"({st['search_retry']}/{SEARCH_RETRY})",
                    outcome_hint=f"search_miss_{sub}",
                )

            cx, cy = self._hit_center(target)
            st["tap_done"] = True
            ctx.mark("decide")
            return step_retry(
                note=f"P4[{sub}]: OCR hit '{self._hit_text(target)[:20]}' "
                     f"tap@({cx},{cy})",
                outcome_hint=f"tap_{sub}",
                action=PhaseAction(
                    kind="tap", x=cx, y=cy,
                    target=f"p4_{sub}_ocr", conf=0.9,
                ),
            )

        # verify
        verified = await self._ocr_contains_any(
            ctx, shot, verify_keywords, verify_roi,
        )
        ctx.mark("decide")
        if verified:
            st["sub_step"] = next_sub
            st["tap_done"] = False
            st["tap_retry"] = 0
            st["search_retry"] = 0
            st["popup_intercept_count"] = 0
            return step_retry(
                note=f"P4[{sub}] verify OK → {next_sub}",
                outcome_hint=f"verify_ok_{sub}",
            )

        st["tap_retry"] += 1
        if st["tap_retry"] >= TAP_VERIFY_RETRY:
            return step_fail(
                note=f"P4[{sub}]: verify fail 3 次",
                outcome_hint=f"verify_fail_{sub}",
            )
        st["tap_done"] = False
        return step_retry(
            note=f"P4[{sub}] verify miss, retry tap "
                 f"({st['tap_retry']}/{TAP_VERIFY_RETRY})",
            outcome_hint=f"verify_miss_{sub}",
        )

    async def _step_fill_check(self, ctx: RunContext, shot) -> PhaseStep:
        """ROI 颜色检测补位勾选 (不 tap, 只记 state). 跟 v1 一致.

        黄色对勾 r>150 g>120 b<100, 占比 > 5% = 已勾选.
        """
        st = ctx._p4
        try:
            h_img, w_img = shot.shape[:2]
            x1, y1, x2, y2 = FILL_CHECKBOX_ROI
            px1, py1 = max(0, int(w_img * x1)), max(0, int(h_img * y1))
            px2, py2 = min(w_img, int(w_img * x2)), min(h_img, int(h_img * y2))
            region = shot[py1:py2, px1:px2]
            if region.size == 0:
                ratio = 0.0
            else:
                r_ch = region[:, :, 2]
                g_ch = region[:, :, 1]
                b_ch = region[:, :, 0]
                orange = int(((r_ch > 150) & (g_ch > 120) & (b_ch < 100)).sum())
                total = int(region.shape[0] * region.shape[1])
                ratio = orange / total if total > 0 else 0.0
            checked = ratio > 0.05
            st["fill_state"] = {
                "ratio": round(ratio, 4),
                "checked": checked,
            }
        except Exception as e:
            logger.debug(f"P4[fill] err: {e}")
            st["fill_state"] = {"ratio": 0.0, "checked": False, "err": str(e)}

        # 不阻塞业务, 直接进 confirm
        st["sub_step"] = "confirm"
        st["tap_done"] = False
        st["tap_retry"] = 0
        st["popup_intercept_count"] = 0
        ctx.mark("decide")
        return step_retry(
            note=f"P4[fill] checked={st['fill_state'].get('checked')} → confirm",
            outcome_hint="fill_done",
        )

    # ════════════════════════════════════════
    # 工具方法
    # ════════════════════════════════════════

    async def _find_popup(self, ctx: RunContext, shot) -> Optional[tuple]:
        try:
            dets = await ctx.yolo.detect(
                shot, roi=Roi(*POPUP_CHECK_ROI), conf_thresh=0.40,
            )
        except Exception:
            return None
        for d in dets:
            if d.name == "close_x" and d.conf >= 0.50:
                if not ctx.is_blacklisted(d.cx, d.cy):
                    return (d.cx, d.cy)
        return None

    async def _ocr_contains_any(
        self, ctx: RunContext, shot, keywords: list,
        roi: Optional[tuple],
    ) -> bool:
        if not keywords:
            return False
        try:
            kwargs = {}
            if roi is not None:
                kwargs["roi"] = Roi(*roi)
            hits = await ctx.ocr.recognize(shot, **kwargs)
        except Exception as e:
            logger.debug(f"P4 OCR verify err: {e}")
            return False
        for h in hits:
            text = self._hit_text(h)
            for kw in keywords:
                if kw and kw in text:
                    return True
        return False

    @staticmethod
    def _hit_text(h) -> str:
        if hasattr(h, "text"):
            return h.text or ""
        if isinstance(h, dict):
            return h.get("text", "") or ""
        return ""

    @staticmethod
    def _hit_center(h) -> tuple:
        if hasattr(h, "cx") and hasattr(h, "cy"):
            return (int(h.cx), int(h.cy))
        if hasattr(h, "bbox"):
            b = h.bbox
            return (int((b[0] + b[2]) / 2), int((b[1] + b[3]) / 2))
        if isinstance(h, dict):
            if "cx" in h and "cy" in h:
                return (int(h["cx"]), int(h["cy"]))
            if "bbox" in h:
                b = h["bbox"]
                return (int((b[0] + b[2]) / 2), int((b[1] + b[3]) / 2))
        return (0, 0)
