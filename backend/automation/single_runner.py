"""
SingleInstanceRunner — 单实例自动化运行器
控制一个模拟器实例完成: 加速器 → 启动游戏 → 弹窗清理 → 大厅确认 → 组队 → 地图设置

可在Windows上直接运行:
  python -m backend.automation.single_runner --instance 0
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import cv2
import numpy as np

from .adb_lite import ADBController
from .screen_matcher import ScreenMatcher
from .ocr_dismisser import OcrDismisser
from .debug_logger import DebugLogger
from . import metrics

logger = logging.getLogger(__name__)


def _timed_phase(name: str):
    """phase 方法装饰器：记录进入/离开 + 结果 + 总耗时到 metrics.jsonl"""
    def wrap(fn):
        import functools
        @functools.wraps(fn)
        async def aw(self, *args, **kwargs):
            t0 = time.perf_counter()
            result = None
            ok = False
            try:
                result = await fn(self, *args, **kwargs)
                ok = bool(result) if not isinstance(result, str) else bool(result)
                return result
            finally:
                metrics.record(
                    "phase",
                    name=name,
                    result="ok" if ok else "fail",
                    dur_ms=round((time.perf_counter() - t0) * 1000, 2),
                )
        return aw
    return wrap


# v3 helper: PhaseResult → outcome string (用于 decision_log finalize)
def _result_to_outcome_str(r) -> str:
    from .phase_base import PhaseResult
    return {
        PhaseResult.NEXT: "phase_next", PhaseResult.RETRY: "retry",
        PhaseResult.WAIT: "wait", PhaseResult.FAIL: "phase_fail",
        PhaseResult.GAME_RESTART: "game_restart", PhaseResult.DONE: "phase_done",
    }.get(r, str(r))


# v3: PhaseHandler 内部要 game_restart 时, 抛这个让 runner_service 翻译成 _GameCrashError
class V3GameRestartRequested(Exception):
    """v3 PhaseHandler 返回 GAME_RESTART 时, _run_v3_phase 抛此异常."""
    def __init__(self, phase_name: str = ""):
        self.phase_name = phase_name
        super().__init__(f"v3 game_restart from {phase_name}")


class Phase(str, Enum):
    """运行阶段"""
    INIT = "init"
    ACCELERATOR = "accelerator"
    LAUNCH_GAME = "launch_game"
    WAIT_LOGIN = "wait_login"
    DISMISS_POPUPS = "dismiss_popups"
    LOBBY = "lobby"
    MAP_SETUP = "map_setup"
    TEAM_CREATE = "team_create"
    TEAM_JOIN = "team_join"
    DONE = "done"
    ERROR = "error"


# ====================================================================
# 游戏常量
# ====================================================================

GAME_PACKAGE = "com.tencent.tmgp.pubgmhd"


class SingleInstanceRunner:
    """
    单实例自动化运行器

    控制一个模拟器实例从启动加速器到进入大厅的完整流程。
    """

    def __init__(
        self,
        adb: ADBController,
        matcher: ScreenMatcher,
        role: str = "captain",  # "captain" | "member"
        target_mode: str = "团队竞技",
        target_map: str = "狙击团竞",
        on_phase_change=None,  # 回调: (Phase) -> None
        log_dir: str = "",     # 实例日志目录（空字符串=禁用调试日志）
    ):
        self.adb = adb
        self.matcher = matcher
        self.role = role
        self.target_mode = target_mode
        self.target_map = target_map
        self._phase = Phase.INIT
        self._on_phase_change = on_phase_change
        self.ocr_dismisser = OcrDismisser(max_rounds=25)
        # YOLO 视觉识别 (only used as detect()/is_available() provider for v3 perception).
        from .yolo_dismisser import YoloDismisser
        self.yolo_dismisser = YoloDismisser(max_rounds=25)
        self._team_code: str = ""  # 队长生成的口令码
        self.dbg = DebugLogger(enabled=bool(log_dir), save_dir=log_dir or "logs")

        # v3 资源 (lazy 初始化)
        self._v3_ctx = None  # backend.automation.phase_base.RunContext
        self._v3_memory = None
        self._v3_lobby_detector = None

        # phase 中间步骤日志 (每个 phase 跑前清空, 跑完 P4Handler 等读出来塞 decision.note)
        self._stage_log: list[str] = []

    def _build_v3_ctx(self):
        """构造 v3 RunContext (lazy, 缓存)."""
        if self._v3_ctx is not None:
            return self._v3_ctx
        from .phase_base import RunContext
        from .lobby_check import LobbyQuadDetector
        from .memory_l1 import FrameMemory
        from .user_paths import user_data_dir
        from .decision_log import get_recorder

        if self._v3_memory is None:
            try:
                # 必须走 get_shared_memory 而不是 new FrameMemory, 否则跨 runner 实例
                # 蓄水池 / LRU / BKTree 各自独立, 5-confirm 累积归零, 学不到记忆.
                from .memory_l1 import get_shared_memory
                self._v3_memory = get_shared_memory(user_data_dir() / "memory" / "dismiss_popups.db")
            except Exception as _e:
                logger.warning(f"[v3] memory 初始化失败 (非致命): {_e}")
                self._v3_memory = None

        if self._v3_lobby_detector is None:
            self._v3_lobby_detector = LobbyQuadDetector()

        self._v3_ctx = RunContext(
            device=self.adb,
            matcher=self.matcher,
            runner=self,
            yolo=self.yolo_dismisser,
            memory=self._v3_memory,
            lobby_detector=self._v3_lobby_detector,
            decision_recorder=get_recorder(),
            instance_idx=-1,
            account=None,
            settings=None,
            role=self.role,  # captain / member 直接传, 不再翻译为 leader/follower
        )
        return self._v3_ctx

    def _mem_query(self, shot, target_name: str, max_dist: int = 5):
        """统一 memory 查询入口 (P3a / P4 各 sub-decision 用).
        memory 没初始化 / 异常 → 返 None, 不阻塞流程."""
        if self._v3_memory is None or shot is None:
            return None
        try:
            return self._v3_memory.query(shot, target_name=target_name, max_dist=max_dist)
        except Exception as e:
            logger.debug(f"[mem_query {target_name}] err: {e}")
            return None

    def _mem_remember(self, shot, target_name: str, x: int, y: int, success: bool = True):
        """统一 memory 写入入口. 成功 tap 后调."""
        if self._v3_memory is None or shot is None:
            return
        try:
            self._v3_memory.remember(shot, target_name, (int(x), int(y)), success=success)
        except Exception as e:
            logger.debug(f"[mem_remember {target_name}] err: {e}")

    async def _try_memory_first(self, shot, target_name: str, decision, target_class_zh: str = "") -> bool:
        """memory 快速路径: 命中即 tap + 写 decision tier. 返回 True=命中.
        各 sub-decision 在 OCR/template 之前调一下, 命中则跳过 OCR 省 1-2s.

        注意: 召回不再自动 remember(success=True). 原因: entry 的存在本身已经过
        5 次蓄水池验证, 召回成功再 success++ 是无信息的自我反馈, 会让错记忆 (e.g.
        anchor 假命中) 越用越自信永远跌不下置信度阈值 → 错召回循环不能自愈.
        success_count 只由 OCR/template 路径独立确认时才涨.
        """
        from .decision_log import TierRecord
        mem = self._mem_query(shot, target_name)
        if mem is None:
            return False
        target_lbl = target_class_zh or target_name
        if decision is not None:
            try:
                decision.add_tier(TierRecord(
                    tier=1, name=f"Memory·{target_name}", early_exit=True,
                    note=f"Memory 命中 (省 OCR/模板): {mem.note}",
                ))
                decision.set_tap(int(mem.cx), int(mem.cy), method="记忆",
                                  target_class=f"{target_lbl}(memory)",
                                  target_conf=float(mem.confidence), screenshot=shot)
            except Exception:
                pass
        await self.adb.tap(mem.cx, mem.cy)
        return True

    async def _run_v3_phase(self, handler, instance_idx: int = -1) -> bool:
        """用 v3 PhaseHandler 跑一个 phase. 返回 True=NEXT/DONE, False=FAIL/超时.

        每帧 wrap recorder.new_decision/finalize, 跟 runner_fsm._loop_phase 一致,
        以便阶段测试也能写决策档案.
        """
        from .phase_base import PhaseResult
        from .action_executor import ActionExecutor
        from .decision_log import get_recorder
        recorder = get_recorder()
        ctx = self._build_v3_ctx()
        if instance_idx >= 0:
            ctx.instance_idx = instance_idx
        await handler.enter(ctx)
        # 取消令牌 (前端 /api/runner/cancel 可置位)
        try:
            from ..api_runner_test import CANCEL_FLAG
        except Exception:
            CANCEL_FLAG = None

        def _cancelled() -> bool:
            return CANCEL_FLAG is not None and bool(CANCEL_FLAG.get("v"))

        async def _interruptible_sleep(seconds: float) -> bool:
            """分段 sleep, 每 200ms 检查 cancel. 返回 True 表示被取消."""
            elapsed = 0.0
            while elapsed < seconds:
                if _cancelled():
                    return True
                chunk = min(0.2, seconds - elapsed)
                await asyncio.sleep(chunk)
                elapsed += chunk
            return False

        import time as _t_phase
        _phase_started_at = _t_phase.time()
        for rnd in range(handler.max_rounds):
            if _cancelled():
                logger.info(f"[{handler.name}] 收到取消信号 → 中止 (R{rnd})")
                await handler.exit(ctx, PhaseResult.FAIL)
                return False
            # max_seconds 守门 (优先级高于 max_rounds)
            if handler.max_seconds is not None:
                _elapsed = _t_phase.time() - _phase_started_at
                if _elapsed >= handler.max_seconds:
                    logger.warning(
                        f"[{handler.name}] 超 max_seconds={handler.max_seconds:.0f}s "
                        f"(实际 {_elapsed:.1f}s, R{rnd}) → FAIL"
                    )
                    await handler.exit(ctx, PhaseResult.FAIL)
                    return False
            ctx.phase_round = rnd + 1
            # PERF tracing: round 内每 stage 写到独立文件, 不依赖 backend.log
            _round_t0 = time.perf_counter()
            _round_perf = {}
            def _mark(name):
                _round_perf[name] = round((time.perf_counter() - _round_t0) * 1000, 1)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # screenshot 优先读 daemon cache (2026-05-10 真根因修复):
            # 之前每 round 都自己 adb.screenshot() 200-400ms, 而 daemon 后台 8fps
            # 抓的是同一个 ldopengl frame, 业务等于跑了 2 次 capture (业务 + daemon).
            # 改: 业务读 daemon cache (1ms), miss 才 fallback 自己截图.
            # 一键回退: env GAMEBOT_BIZ_USE_DAEMON_FRAME=0
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            shot = None
            inst_idx_for_daemon = getattr(ctx, 'instance_idx', None)
            if (os.environ.get("GAMEBOT_BIZ_USE_DAEMON_FRAME", "1") == "1"
                    and inst_idx_for_daemon is not None):
                try:
                    from .vision_daemon import VisionDaemon
                    slot = VisionDaemon.get().snapshot(inst_idx_for_daemon, max_age_ms=300)
                    if slot is not None and slot.frame is not None:
                        shot = slot.frame  # daemon 已 .copy()
                except Exception:
                    pass
            if shot is None:
                try:
                    shot = await self.adb.screenshot()
                except Exception as _e:
                    shot = None
            _mark("screenshot")
            ctx.current_shot = shot
            if shot is None:
                await asyncio.sleep(0.3)
                continue

            # 开决策记录
            phash_str = ""
            try:
                from .adb_lite import phash as _phash
                ph = _phash(shot)
                phash_str = f"0x{int(ph):016x}" if ph else ""
                ctx.current_phash = phash_str
            except Exception:
                pass
            _mark("phash")
            decision = None
            try:
                # 同步调 (mkdir < 1ms, 不该走 default executor 跟 cv2/yolo/ADB 抢 worker;
                # 实测之前 to_thread 排队让这步从 1ms 飙到 100-2500ms).
                decision = recorder.new_decision(
                    ctx.instance_idx,
                    handler.name,
                    ctx.phase_round,
                )
                decision.set_input(shot, phash_str, q=70)
                ctx.current_decision = decision
            except Exception as e:
                logger.debug(f"[{handler.name}] new_decision err: {e}")
            _mark("new_decision")

            handle_exc = None
            step = None
            try:
                step = await handler.handle_frame(ctx)
            except Exception as e:
                handle_exc = e
                logger.warning(f"[{handler.name}/R{rnd+1}] handle_frame 异常: {e}", exc_info=True)
            _mark("handle_frame")
            # 把 perceive 内部拆解写进 round_perf.log (定位 perceive 1500ms 慢点)
            _last_prc = getattr(ctx, "_last_perceive_perf", None)
            if _last_prc:
                for _k, _v in _last_prc.items():
                    _round_perf[f"prc_{_k}"] = round(_v, 1)
                ctx._last_perceive_perf = None

            if step is not None and step.action is not None:
                try:
                    await ActionExecutor.apply(ctx, step.action)
                except Exception as e:
                    logger.warning(f"[{handler.name}] ActionExecutor 异常: {e}")
            _mark("action_exec")

            # finalize — 序列化 dict + json.dump 已 thread-pool, 但 _serialize_tier
            # + record_summary + _notify_listeners 仍在调用方线程执行. 包 to_thread.
            try:
                if decision is not None:
                    # finalize 内部 cv2.imwrite 已用 dlog_pool 异步, 主体只是 dict serialize + json.dump,
                    # 同步执行 < 5ms, 不该走 default executor 跟业务 to_thread 抢 worker.
                    if step is not None:
                        outcome = step.outcome_hint or _result_to_outcome_str(step.result)
                        decision.finalize(outcome, step.note or "")
                    else:
                        decision.finalize("phase_exception",
                                          repr(handle_exc) if handle_exc else "")
            except Exception as _e:
                pass
            ctx.current_decision = None

            if handle_exc is not None:
                final = await handler.on_failure(ctx, handle_exc)
                await handler.exit(ctx, final)
                return False

            if step.note:
                logger.info(f"[{handler.name}/R{rnd+1}] {step.note}")
            if step.result in (PhaseResult.NEXT, PhaseResult.DONE):
                await handler.exit(ctx, step.result)
                return True
            if step.result == PhaseResult.FAIL:
                await handler.exit(ctx, step.result)
                return False
            if step.result == PhaseResult.GAME_RESTART:
                await handler.exit(ctx, step.result)
                raise V3GameRestartRequested(handler.name)
            # 分段 sleep + cancel check (用户点'停止'后最长延迟 200ms)
            # round_interval 优先读 runtime_profile (mode 切换生效), fallback 到 phase class attr
            if step.result == PhaseResult.WAIT:
                sleep_s = max(0.0, step.wait_seconds)
            else:
                try:
                    from .runtime_profile import resolve_round_interval
                    sleep_s = resolve_round_interval(handler.name, fallback=handler.round_interval_s)
                except Exception:
                    sleep_s = handler.round_interval_s
            _mark("finalize")
            # round perf 写文件 (轻量, 但 6 实例并发 file lock 竞争, 改异步写)
            try:
                _round_perf["sleep_s"] = round(sleep_s * 1000, 0)
                _round_perf["round_total_ms"] = round((time.perf_counter() - _round_t0) * 1000, 1)
                _perf_line = (
                    f"[ROUND/{handler.name}/inst{ctx.instance_idx}/R{rnd+1}] "
                    + " ".join(f"{k}={v}" for k, v in _round_perf.items())
                    + "\n"
                )
                _perf_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "round_perf.log"
                )
                # 用 to_thread 异步写, 不阻塞 round loop
                def _write_perf():
                    try:
                        with open(_perf_path, "a", encoding="utf-8") as _pf:
                            _pf.write(_perf_line)
                    except Exception:
                        pass
                asyncio.create_task(asyncio.to_thread(_write_perf))
            except Exception:
                pass
            if await _interruptible_sleep(sleep_s):
                logger.info(f"[{handler.name}] 取消信号 (sleep 期间) → 中止 (R{rnd})")
                await handler.exit(ctx, PhaseResult.FAIL)
                return False
        logger.warning(f"[{handler.name}] 超 max_rounds={handler.max_rounds} → FAIL")
        await handler.exit(ctx, PhaseResult.FAIL)
        return False


    @property
    def phase(self) -> Phase:
        return self._phase

    @phase.setter
    def phase(self, value: Phase):
        old = self._phase
        self._phase = value
        if self._on_phase_change and old != value:
            self._on_phase_change(value)

    # ================================================================
    # 阶段 1: 启动游戏
    # ================================================================

    @_timed_phase("map_setup")
    async def phase_map_setup(self) -> bool:
        """队长设置地图和模式. 拆 5 条独立子 decision (每条有图+tier+tap), 像 P2 清弹窗一样档案能逐条点开看."""
        import time as _t
        from .decision_log import get_recorder, TierRecord, TemplateMatch, OcrHit as _OcrHit

        self._stage_log = []
        _t0 = _t.perf_counter()
        recorder = get_recorder()
        inst_idx = getattr(self._v3_ctx, "instance_idx", 0) if self._v3_ctx else 0

        def _slog(msg: str):
            # P4 stage log: info 级太吵, 改 debug; 还是会进 self._stage_log 落决策档案 note
            logger.debug(msg)
            self._stage_log.append(msg)

        def _make_d(sub_name: str, shot_in=None):
            """创建子 decision + (可选) set_input + 顺带采样到 yolo raw screenshots.
            shot_in=None 时退化成只建 decision; 给了 shot_in 等价于 _make_d(name)+set_input+collect."""
            try:
                d = recorder.new_decision(
                    instance=inst_idx, phase=f"P4-{sub_name}", round_idx=1)
            except Exception as e:
                logger.debug(f"new_decision err: {e}")
                return None
            if d is not None and shot_in is not None:
                try:
                    d.set_input(shot_in, q=70)
                except Exception:
                    pass
                try:
                    from .screenshot_collector import collect as _yolo_collect
                    _yolo_collect(shot_in, instance=inst_idx, tag=f"P4-{sub_name}")
                except Exception:
                    pass
            return d

        self.phase = Phase.MAP_SETUP
        _slog(f"[阶段6] 地图设置: {self.target_mode} - {self.target_map}")

        # 禁用守卫
        if hasattr(self.adb, 'guard_enabled'):
            self.adb.guard_enabled = False

        ocr = OcrDismisser()

        # 地图关键词 (模糊匹配, OCR 常把"狙击"识别为"姐击"/"阻击")
        map_keywords = [self.target_map]
        if "狙击" in self.target_map:
            map_keywords.extend(["击团竞大桥", "击团竞"])
        elif "经典" in self.target_map:
            map_keywords.extend(["经典团竞仓库", "经典团竞"])
        elif "军备" in self.target_map:
            map_keywords.extend(["军备团竞图书", "军备团竞"])

        try:
            from .roi_config import all_names as _all_roi, get as _roi_get
            avail_rois = set(_all_roi())
        except Exception:
            avail_rois = set()
        has_list_roi = "map_panel_list_center" in avail_rois
        has_left_roi = "map_panel_left_tabs" in avail_rois
        has_fill_roi = "map_panel_fill_checkbox" in avail_rois

        def _roi_pixels(name: str, img):
            """把归一化 ROI 转成像素 bbox [x1,y1,x2,y2]; 失败返回 None"""
            if img is None:
                return None
            try:
                rx1, ry1, rx2, ry2, _ = _roi_get(name)
                ph, pw = img.shape[:2]
                return [max(0, int(pw * rx1)), max(0, int(ph * ry1)),
                        min(pw, int(pw * rx2)), min(ph, int(ph * ry2))]
            except Exception:
                return None

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 子 decision 1: P4-1-open  打开地图面板 (模板找开始游戏 → tap 模式名)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        shot = await self.adb.screenshot()
        if shot is None:
            self._restore_guard()
            return False
        d1 = _make_d("1-open", shot_in=shot)

        # P4-1 retry: 4 次尝试, 每次跑 模板 lobby_start_game → lobby_start_btn → OCR 兜底,
        # 全 miss 后 sleep [0.25/0.4/1.0] 再来一次. 最坏 1.65s 延迟但抗 cold OCR/动画.
        _P4_RETRY_SLEEPS = [0.25, 0.4, 1.0]
        hit = None
        tmpl_used = ""
        ocr_hits = []
        found = None
        p4_1_attempts = 0
        memory_hit_p4_1 = False
        for _retry in range(len(_P4_RETRY_SLEEPS) + 1):  # 4 次
            p4_1_attempts = _retry + 1
            # ① Memory 快速路径 (每次 retry 先查一次, 命中即 break)
            if await self._try_memory_first(shot, "P4-1-open", d1, "开始游戏"):
                _slog(f"[阶段6] P4-1 Memory 命中 (第 {p4_1_attempts} 次)")
                memory_hit_p4_1 = True
                hit = None; found = None
                break
            # 模板 1: lobby_start_game (用户裁的)
            hit = self.matcher.match_one(shot, "lobby_start_game", threshold=0.7)
            tmpl_used = "lobby_start_game"
            # 模板 2: lobby_start_btn (旧)
            if hit is None:
                hit = self.matcher.match_one(shot, "lobby_start_btn", threshold=0.7)
                tmpl_used = "lobby_start_btn"
            if hit is not None:
                break
            # 模板都 miss → OCR 兜底 (包 to_thread 防卡事件循环)
            ocr_hits = await ocr._ocr_all_async(shot)
            found = next((h for h in ocr_hits if "开始游戏" in h.text), None)
            if found is not None:
                tmpl_used = "OCR·开始游戏"
                break
            # 全 miss, retry
            if _retry < len(_P4_RETRY_SLEEPS):
                _sleep_s = _P4_RETRY_SLEEPS[_retry]
                _slog(f"[P4-1] attempt {p4_1_attempts} 模板+OCR 全 miss → sleep {_sleep_s}s 重试")
                await asyncio.sleep(_sleep_s)
                shot_r = await self.adb.screenshot()
                if shot_r is not None:
                    shot = shot_r

        if memory_hit_p4_1:
            # Memory 命中分支: tap 已在 _try_memory_first 内做了, 这里只 finalize
            if d1: d1.finalize(outcome="opened_panel", note=f"Memory 命中(第 {p4_1_attempts} 次)")
        elif hit:
            if d1:
                d1.add_tier(TierRecord(
                    tier=0, name=f"模板·{tmpl_used}", early_exit=True,
                    note=f"模板命中 conf={hit.confidence:.2f}, cx={hit.cx} cy={hit.cy}, 尝试 {p4_1_attempts} 次",
                    templates=[TemplateMatch(
                        name=tmpl_used, score=float(hit.confidence),
                        hit=True, bbox=[int(hit.cx - hit.w/2), int(hit.cy - hit.h/2),
                                        int(hit.cx + hit.w/2), int(hit.cy + hit.h/2)],
                    )],
                ))
                d1.set_tap(int(hit.cx), int(hit.cy + 60), method="模板",
                           target_class="开始游戏(下方模式名)",
                           target_conf=float(hit.confidence), screenshot=shot)
            await self.adb.tap(hit.cx, hit.cy + 60)
            self._mem_remember(shot, "P4-1-open", hit.cx, hit.cy + 60, success=True)
            _slog(f"[阶段6] 模板定位'开始游戏' conf={hit.confidence:.2f} → tap ({hit.cx},{hit.cy+60})")
            if d1: d1.finalize(outcome="opened_panel", note=f"模板命中(第 {p4_1_attempts} 次) → 已 tap")
        elif found is not None:
            if d1:
                _hits = [_OcrHit(text=h.text, bbox=[h.cx-20, h.cy-10, h.cx+20, h.cy+10],
                                  cx=h.cx, cy=h.cy) for h in ocr_hits[:30]]
                tier_p1 = TierRecord(
                    tier=3, name="OCR·全屏",
                    note=f"模板没命中 → OCR 找'开始游戏' (识别 {len(ocr_hits)} 文字, 尝试 {p4_1_attempts} 次)",
                    ocr_hits=_hits,
                )
                d1.add_tier(tier_p1)
                _h, _w = shot.shape[:2]
                d1.save_ocr_roi(tier_p1, shot, roi=[0, 0, _w, _h], hits=_hits)
                d1.set_tap(int(found.cx), int(found.cy + 60), method="OCR",
                           target_class="开始游戏", target_text=found.text, screenshot=shot)
            await self.adb.tap(found.cx, found.cy + 60)
            self._mem_remember(shot, "P4-1-open", found.cx, found.cy + 60, success=True)
            _slog(f"[阶段6] OCR 兜底找到'开始游戏' → tap ({found.cx},{found.cy+60})")
            if d1: d1.finalize(outcome="opened_panel", note=f"OCR 兜底命中(第 {p4_1_attempts} 次)")
        else:
            _slog(f"[阶段6] {p4_1_attempts} 次都没找到'开始游戏'按钮")
            if d1:
                # 给档案画上最后一帧的 OCR 结果, 方便 debug
                _hits = [_OcrHit(text=h.text, bbox=[h.cx-20, h.cy-10, h.cx+20, h.cy+10],
                                  cx=h.cx, cy=h.cy) for h in ocr_hits[:30]]
                tier_p1 = TierRecord(
                    tier=3, name="OCR·全屏",
                    note=f"{p4_1_attempts} 次全 miss (识别 {len(ocr_hits)} 文字)",
                    ocr_hits=_hits,
                )
                d1.add_tier(tier_p1)
                _h, _w = shot.shape[:2]
                d1.save_ocr_roi(tier_p1, shot, roi=[0, 0, _w, _h], hits=_hits)
                d1.finalize(outcome="failed", note=f"模板+OCR {p4_1_attempts} 次全 miss")
            self._restore_guard()
            return False

        # 等面板动画 (打开按钮 → 面板淡入). 用 0.3s 而不是 0.8s, 即使拍到过渡帧 P4-2 内部 OCR 也会 retry
        await asyncio.sleep(0.3)
        shot = await self.adb.screenshot()
        if shot is None:
            self._restore_guard()
            return False
        h_img, w_img = shot.shape[:2]
        # P4-2 才真正去 OCR list_center, 这里只占位
        list_hits: list = []

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 子 decision 2: P4-2-mode  切团竞模式 (OCR left_tabs 找团竞 tab)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        d2 = _make_d("2-mode", shot_in=shot)

        import time as _tm

        # P4-2 加 retry: 4 次尝试, 失败后递增 sleep [0.25, 0.4, 1.0]s.
        # 最坏总 wait 1.65s 但能救回 cold OCR 第一帧空 / 面板动画过渡的情况.
        team_battle_keywords = ["团竞手册", "团竞详情", "军备团竞", "经典团竞",
                                "击团竞", "迷你战争", "轮换团竞", "突变团竞"]
        _RETRY_SLEEPS = [0.25, 0.4, 1.0]   # 3 个间隔 → 4 次尝试
        list_hits = []
        left_hits = []
        team_battle_hit = None
        is_team_battle = False
        p4_2_attempts = 0
        for _retry in range(len(_RETRY_SLEEPS) + 1):  # 4 次
            p4_2_attempts = _retry + 1
            _pt = _tm.perf_counter()
            # 关键: ocr._ocr_roi_named 是同步 + 4-6s, 不 to_thread 会卡死整个事件循环,
            # 导致跨 instance 的并发任务无法推进 (两个 leader P4 看着像串行).
            if shot is not None:
                if has_list_roi:
                    list_hits = await asyncio.to_thread(ocr._ocr_roi_named, shot, "map_panel_list_center")
                else:
                    list_hits = await ocr._ocr_all_async(shot)
            else:
                list_hits = []
            _slog(f"[P4-2 PERF] attempt {p4_2_attempts}: OCR list_center "
                  f"{(_tm.perf_counter()-_pt)*1000:.0f}ms ({len(list_hits)} 文字)")

            _pt = _tm.perf_counter()
            if has_left_roi:
                left_hits = (await asyncio.to_thread(ocr._ocr_roi_named, shot, "map_panel_left_tabs")) if shot is not None else []
            else:
                full = (await ocr._ocr_all_async(shot)) if shot is not None else []
                left_hits = [h for h in full if h.cx < w_img * 0.16]
            _slog(f"[P4-2 PERF] attempt {p4_2_attempts}: OCR left_tabs "
                  f"{(_tm.perf_counter()-_pt)*1000:.0f}ms ({len(left_hits)} 文字)")
            team_battle_hit = next((h for h in left_hits if "团队竞技" in h.text), None)

            all_text = " ".join(h.text for h in list_hits)
            is_team_battle = (
                any(OcrDismisser.fuzzy_match(all_text, kw) for kw in team_battle_keywords)
                or any(kw in h.text for h in list_hits for kw in map_keywords)
            )

            # 成功条件: 已在团竞 (list_hits 含关键词) OR 找到了团竞 tab 可点
            if is_team_battle or team_battle_hit:
                break
            # 还需要 retry: 用 _RETRY_SLEEPS[_retry] 间隔, 重新截屏
            if _retry < len(_RETRY_SLEEPS):
                _sleep_s = _RETRY_SLEEPS[_retry]
                _slog(f"[P4-2] attempt {p4_2_attempts} miss → sleep {_sleep_s}s 重试")
                await asyncio.sleep(_sleep_s)
                shot_r = await self.adb.screenshot()
                if shot_r is not None:
                    shot = shot_r

        if d2:
            _pt = _tm.perf_counter()
            _left_hits_d = [_OcrHit(text=h.text, bbox=[h.cx-20, h.cy-10, h.cx+20, h.cy+10],
                                    cx=h.cx, cy=h.cy) for h in left_hits[:15]]
            tier_p2 = TierRecord(
                tier=3, name="OCR·left_tabs", early_exit=is_team_battle,
                note=f"找团竞 tab. 已在团竞={is_team_battle}. 列表 {len(list_hits)} 文字含目标关键词={any(kw in h.text for h in list_hits for kw in map_keywords)}",
                ocr_hits=_left_hits_d,
            )
            d2.add_tier(tier_p2)
            d2.save_ocr_roi(tier_p2, shot,
                            roi=_roi_pixels("map_panel_left_tabs", shot) if has_left_roi
                                else [0, 0, int(w_img * 0.16), shot.shape[0] if shot is not None else 0],
                            hits=_left_hits_d)
            _slog(f"[P4-2 PERF] add_tier+save_ocr_roi: {(_tm.perf_counter()-_pt)*1000:.0f}ms")

        if not is_team_battle:
            if team_battle_hit:
                if d2:
                    d2.set_tap(int(team_battle_hit.cx), int(team_battle_hit.cy),
                               method="OCR", target_class="团队竞技tab",
                               target_text=team_battle_hit.text, screenshot=shot)
                await self.adb.tap(team_battle_hit.cx, team_battle_hit.cy)
                self._mem_remember(shot, "P4-2-mode", team_battle_hit.cx, team_battle_hit.cy, success=True)
                await asyncio.sleep(0.5)
                _slog(f"[阶段6] 切团竞 tap ({team_battle_hit.cx},{team_battle_hit.cy})")
                if d2: d2.finalize(outcome="mode_switched", note="切到团竞")
                # 重新拿 list_hits + shot
                shot_after = await self.adb.screenshot()
                if shot_after is not None:
                    shot = shot_after
                    list_hits = (await asyncio.to_thread(ocr._ocr_roi_named, shot, "map_panel_list_center")
                                 if has_list_roi else await ocr._ocr_all_async(shot))
            else:
                _slog("[阶段6] 不在团竞 + 找不到团竞 tab → 失败")
                if d2: d2.finalize(outcome="failed", note="找不到团竞 tab")
                self._restore_guard()
                return False
        else:
            _slog("[阶段6] 已在团竞, 跳过切换")
            _pt = _tm.perf_counter()
            if d2: d2.finalize(outcome="skipped", note=f"已在团竞 ({len(list_hits)} 文字含关键词)")
            _slog(f"[P4-2 PERF] finalize: {(_tm.perf_counter()-_pt)*1000:.0f}ms")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 并发: 选地图 (CPU) + 补位 ROI 颜色 (像素) + 确定模板 (CPU)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        def _find_map_in_hits():
            for h in list_hits:
                for kw in map_keywords:
                    if kw in h.text:
                        return h
            return None

        async def _check_fill_checkbox():
            if not has_fill_roi or shot is None:
                return None
            rx1, ry1, rx2, ry2, _ = _roi_get("map_panel_fill_checkbox")
            ph, pw = shot.shape[:2]
            px1 = max(0, int(pw * rx1)); py1 = max(0, int(ph * ry1))
            px2 = min(pw, int(pw * rx2)); py2 = min(ph, int(ph * ry2))
            region = shot[py1:py2, px1:px2]
            if region.size == 0:
                return None
            r_ch, g_ch, b_ch = region[:,:,2], region[:,:,1], region[:,:,0]
            # 含黄色对勾 (r>150 & g>120 & b<100) — 之前 g>80 & b<80 太苛刻, 黄色 tick 漏了
            orange = int(((r_ch > 150) & (g_ch > 120) & (b_ch < 100)).sum())
            total = int(region.shape[0] * region.shape[1])
            ratio = orange / total if total > 0 else 0.0
            return {
                "ratio": ratio, "orange": orange, "total": total,
                "rgb": (int(r_ch.mean()), int(g_ch.mean()), int(b_ch.mean())),
                "bbox": [px1, py1, px2, py2],
                "tap_cx": (px1 + px2) // 2, "tap_cy": (py1 + py2) // 2,
            }

        def _find_confirm_template():
            if self.matcher is None or shot is None:
                return None
            try:
                # 优先 queding (专门裁的"确定"模板), 兜底 btn_1 (通用按钮模板)
                h = self.matcher.match_one(shot, "queding", threshold=0.7)
                if h is None:
                    h = self.matcher.match_one(shot, "btn_1", threshold=0.7)
                return h
            except Exception:
                return None

        _pt = _tm.perf_counter()
        map_hit, fill_state, confirm_tmpl = await asyncio.gather(
            asyncio.to_thread(_find_map_in_hits),
            _check_fill_checkbox(),
            asyncio.to_thread(_find_confirm_template),
        )
        _slog(f"[P4-2→P4-3 PERF] gather(map+fill+confirm): {(_tm.perf_counter()-_pt)*1000:.0f}ms")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 子 decision 3: P4-3-map  选地图. 加 retry: gather 第一次没找到地图就重新 OCR list_center
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        d3 = _make_d("3-map", shot_in=shot)

        p4_3_attempts = 1  # gather 那次算第 1 次
        if map_hit is None:
            for _retry in range(len(_P4_RETRY_SLEEPS)):
                _sleep_s = _P4_RETRY_SLEEPS[_retry]
                _slog(f"[P4-3] map_hit miss → sleep {_sleep_s}s 重新 OCR list_center")
                await asyncio.sleep(_sleep_s)
                shot_r = await self.adb.screenshot()
                if shot_r is not None:
                    shot = shot_r
                _pt = _tm.perf_counter()
                if shot is not None:
                    if has_list_roi:
                        list_hits = await asyncio.to_thread(ocr._ocr_roi_named, shot, "map_panel_list_center")
                    else:
                        list_hits = await ocr._ocr_all_async(shot)
                else:
                    list_hits = []
                p4_3_attempts += 1
                _slog(f"[P4-3 PERF] retry {_retry+1}: OCR list_center "
                      f"{(_tm.perf_counter()-_pt)*1000:.0f}ms ({len(list_hits)} 文字)")
                map_hit = _find_map_in_hits()
                if map_hit:
                    break

        _list_roi_px = (_roi_pixels("map_panel_list_center", shot) if has_list_roi
                         else [0, 0, w_img, (shot.shape[0] if shot is not None else 0)])
        if map_hit:
            if d3:
                _list_hits_d = [_OcrHit(text=map_hit.text,
                                         bbox=[map_hit.cx-30, map_hit.cy-15,
                                               map_hit.cx+30, map_hit.cy+15],
                                         cx=map_hit.cx, cy=map_hit.cy)]
                tier_p3 = TierRecord(
                    tier=3, name="OCR·list_center", early_exit=True,
                    note=f"找到 '{map_hit.text}' (匹配 {map_keywords}, 尝试 {p4_3_attempts} 次)",
                    ocr_hits=_list_hits_d,
                )
                d3.add_tier(tier_p3)
                d3.save_ocr_roi(tier_p3, shot, roi=_list_roi_px, hits=_list_hits_d)
                d3.set_tap(int(map_hit.cx), int(map_hit.cy), method="OCR",
                           target_class="地图", target_text=map_hit.text, screenshot=shot)
            await self.adb.tap(map_hit.cx, map_hit.cy)
            await asyncio.sleep(0.3)
            _slog(f"[阶段6] 选地图 '{map_hit.text}' tap ({map_hit.cx},{map_hit.cy})")
            if d3: d3.finalize(outcome="map_selected", note=f"选 '{map_hit.text}'")
        else:
            if d3:
                _list_hits_d = [_OcrHit(text=h.text, bbox=[h.cx-20, h.cy-10, h.cx+20, h.cy+10],
                                         cx=h.cx, cy=h.cy) for h in list_hits[:30]]
                tier_p3 = TierRecord(
                    tier=3, name="OCR·list_center",
                    note=f"未找到 '{self.target_map}' (关键词 {map_keywords}, {p4_3_attempts} 次全 miss)",
                    ocr_hits=_list_hits_d,
                )
                d3.add_tier(tier_p3)
                d3.save_ocr_roi(tier_p3, shot, roi=_list_roi_px, hits=_list_hits_d)
                d3.finalize(outcome="map_not_found", note=f"找不到 '{self.target_map}'")
            _slog(f"[阶段6] 找不到地图 '{self.target_map}'")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 子 decision 4: P4-4-fill  补位检测 (ROI 颜色)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        d4 = _make_d("4-fill", shot_in=shot)

        # P4-3 可能 retry 过, shot 已变, 用新 shot 重新算 fill_state.
        # 同时加 retry: ROI 已配置但 fill_state 仍 None (截屏问题) → sleep + 重试.
        p4_4_attempts = 1
        if has_fill_roi:
            fill_state = await _check_fill_checkbox()
            for _retry in range(len(_P4_RETRY_SLEEPS)):
                if fill_state is not None:
                    break
                _sleep_s = _P4_RETRY_SLEEPS[_retry]
                _slog(f"[P4-4] fill_state 为空 → sleep {_sleep_s}s 重新截屏")
                await asyncio.sleep(_sleep_s)
                shot_r = await self.adb.screenshot()
                if shot_r is not None:
                    shot = shot_r
                fill_state = await _check_fill_checkbox()
                p4_4_attempts += 1

        if fill_state is not None:
            ratio = fill_state["ratio"]
            rgb = fill_state["rgb"]
            bbox = fill_state["bbox"]
            tier_note = (
                f"ROI={tuple(bbox)} 平均RGB={rgb} "
                f"橙色={fill_state['orange']}/{fill_state['total']} "
                f"({ratio*100:.1f}%) 阈值=5% (尝试 {p4_4_attempts} 次)"
            )
            if d4:
                tier_p4 = TierRecord(
                    tier=4, name="ROI颜色·fill_checkbox",
                    early_exit=True, note=tier_note, ocr_roi=bbox,
                )
                d4.add_tier(tier_p4)
                d4.save_ocr_roi(tier_p4, shot, roi=bbox, hits=[])
            if ratio > 0.05:
                if d4:
                    d4.set_tap(fill_state["tap_cx"], fill_state["tap_cy"],
                               method="ROI颜色", target_class="补位勾选框", screenshot=shot)
                await self.adb.tap(fill_state["tap_cx"], fill_state["tap_cy"])
                await asyncio.sleep(0.3)
                _slog(f"[阶段6] 补位已勾 ({ratio*100:.1f}%) → tap 取消")
                if d4: d4.finalize(outcome="fill_unchecked",
                                    note=f"补位已勾 → tap 取消 (橙色 {ratio*100:.1f}%)")
            else:
                _slog(f"[阶段6] 补位未勾 ({ratio*100:.1f}% < 5%)")
                if d4: d4.finalize(outcome="skipped",
                                    note=f"补位未勾 (橙色 {ratio*100:.1f}% < 5%)")
        else:
            if d4:
                d4.add_tier(TierRecord(
                    tier=4, name="ROI颜色·fill_checkbox",
                    note="map_panel_fill_checkbox ROI 未配置",
                ))
                d4.finalize(outcome="skipped", note="ROI 未配置, 跳过")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 子 decision 5: P4-5-confirm  点确定 (btn_1 模板)
        # 注意: 重新截图 + 重新找模板. 之前的 confirm_tmpl 是在 map/fill tap 之前抓的,
        # 面板可能已重排, 直接用旧坐标会点空. 代价 +30~50ms 截屏, 但保证正确.
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        shot_d5 = await self.adb.screenshot()
        if shot_d5 is None:
            shot_d5 = shot  # 截屏失败 fallback 用旧 shot
        d5 = _make_d("5-confirm", shot_in=shot_d5)

        # P4-5 retry: queding → btn_1 (带区域约束) → OCR ROI/全屏 全 miss 才进 retry,
        # sleep [0.25/0.4/1.0] 重新截屏后再试. 任何一级命中即 break.
        confirm_tmpl_fresh = None
        confirm_tmpl_used = "queding"
        confirm_hit_ocr = None
        ocr_tier_name = ""
        ocr_tier_note = ""
        confirm_hits_last: list = []
        _btn_roi_px_last = None
        p4_5_attempts = 0
        memory_hit_p4_5 = False
        for _retry in range(len(_P4_RETRY_SLEEPS) + 1):  # 4 次
            p4_5_attempts = _retry + 1
            # ① Memory 快速路径
            if shot_d5 is not None and await self._try_memory_first(shot_d5, "P4-5-confirm", d5, "确定按钮"):
                _slog(f"[阶段6] P4-5 Memory 命中 (第 {p4_5_attempts} 次)")
                memory_hit_p4_5 = True
                confirm_tmpl_fresh = None; confirm_hit_ocr = None
                break
            confirm_tmpl_fresh = None
            confirm_tmpl_used = "queding"
            try:
                if shot_d5 is not None:
                    confirm_tmpl_fresh = self.matcher.match_one(shot_d5, "queding", threshold=0.7)
                    if confirm_tmpl_fresh is None:
                        confirm_tmpl_fresh = self.matcher.match_one(shot_d5, "btn_1", threshold=0.7)
                        confirm_tmpl_used = "btn_1"
            except Exception:
                confirm_tmpl_fresh = None

            # 区域约束: btn_1 通用模板必须卡在确定按钮区域 (cx/w > 0.80, cy/h ∈ [0.80, 0.98])
            if confirm_tmpl_fresh and shot_d5 is not None and confirm_tmpl_used == "btn_1":
                _h_d5, _w_d5 = shot_d5.shape[:2]
                rel_x = confirm_tmpl_fresh.cx / max(1, _w_d5)
                rel_y = confirm_tmpl_fresh.cy / max(1, _h_d5)
                if not (rel_x > 0.80 and 0.80 < rel_y < 0.98):
                    _slog(f"[阶段6] btn_1 命中位置 ({confirm_tmpl_fresh.cx},{confirm_tmpl_fresh.cy}) "
                          f"rel=({rel_x:.2f},{rel_y:.2f}) 不在确定按钮区域 → 拒绝")
                    confirm_tmpl_fresh = None

            if confirm_tmpl_fresh:
                break  # 模板命中, 不需要 OCR

            # OCR 兜底
            shot = shot_d5
            has_confirm_roi = "map_panel_btn_confirm" in avail_rois
            if has_confirm_roi:
                confirm_hits_last = (await asyncio.to_thread(ocr._ocr_roi_named, shot, "map_panel_btn_confirm")) if shot is not None else []
                _btn_roi_px_last = _roi_pixels("map_panel_btn_confirm", shot)
                ocr_tier_name = "OCR·btn_confirm"
                ocr_tier_note = f"模板没命中 → OCR ROI 找'确定' ({len(confirm_hits_last)} 文字)"
            else:
                confirm_hits_last = (await ocr._ocr_all_async(shot)) if shot is not None else []
                _btn_roi_px_last = None
                ocr_tier_name = "OCR·全屏(右侧)"
                ocr_tier_note = f"模板没命中 → OCR 全屏找'确定' (右侧 cx>{int(w_img*0.78)})"
            confirm_hit_ocr = next((h for h in confirm_hits_last if "确定" in h.text
                                    and (has_confirm_roi or h.cx > w_img * 0.78)), None)
            if confirm_hit_ocr:
                break
            # 全 miss → retry
            if _retry < len(_P4_RETRY_SLEEPS):
                _sleep_s = _P4_RETRY_SLEEPS[_retry]
                _slog(f"[P4-5] attempt {p4_5_attempts} 模板+OCR 全 miss → sleep {_sleep_s}s 重试")
                await asyncio.sleep(_sleep_s)
                shot_r = await self.adb.screenshot()
                if shot_r is not None:
                    shot_d5 = shot_r

        if memory_hit_p4_5:
            # tap 已在 _try_memory_first 内做了
            if d5: d5.finalize(outcome="confirmed", note=f"Memory 命中(第 {p4_5_attempts} 次)")
        elif confirm_tmpl_fresh:
            if d5:
                d5.add_tier(TierRecord(
                    tier=0, name=f"模板·{confirm_tmpl_used}", early_exit=True,
                    note=f"确定按钮模板命中 conf={confirm_tmpl_fresh.confidence:.2f}, 尝试 {p4_5_attempts} 次",
                    templates=[TemplateMatch(
                        name=confirm_tmpl_used, score=float(confirm_tmpl_fresh.confidence),
                        hit=True,
                        bbox=[int(confirm_tmpl_fresh.cx - confirm_tmpl_fresh.w/2),
                              int(confirm_tmpl_fresh.cy - confirm_tmpl_fresh.h/2),
                              int(confirm_tmpl_fresh.cx + confirm_tmpl_fresh.w/2),
                              int(confirm_tmpl_fresh.cy + confirm_tmpl_fresh.h/2)],
                    )],
                ))
                d5.set_tap(int(confirm_tmpl_fresh.cx), int(confirm_tmpl_fresh.cy),
                           method="模板", target_class="确定",
                           target_conf=float(confirm_tmpl_fresh.confidence), screenshot=shot_d5)
            await self.adb.tap(confirm_tmpl_fresh.cx, confirm_tmpl_fresh.cy)
            self._mem_remember(shot_d5, "P4-5-confirm", confirm_tmpl_fresh.cx, confirm_tmpl_fresh.cy, success=True)
            _slog(f"[阶段6] 确定 {confirm_tmpl_used} 模板命中(尝试 {p4_5_attempts} 次) → tap ({confirm_tmpl_fresh.cx},{confirm_tmpl_fresh.cy}) conf={confirm_tmpl_fresh.confidence:.2f}")
            if d5: d5.finalize(outcome="confirmed", note=f"模板命中 conf={confirm_tmpl_fresh.confidence:.2f} (尝试 {p4_5_attempts} 次)")
        elif confirm_hit_ocr:
            shot = shot_d5
            if d5:
                _full_d = [_OcrHit(text=h.text, bbox=[h.cx-20, h.cy-10, h.cx+20, h.cy+10],
                                    cx=h.cx, cy=h.cy) for h in confirm_hits_last[:30]]
                tier_p5o = TierRecord(tier=3, name=ocr_tier_name,
                                       note=ocr_tier_note + f" (尝试 {p4_5_attempts} 次)",
                                       ocr_hits=_full_d)
                d5.add_tier(tier_p5o)
                _h5, _w5 = (shot.shape[:2] if shot is not None else (0, 0))
                _roi_use = _btn_roi_px_last if _btn_roi_px_last else [0, 0, _w5, _h5]
                d5.save_ocr_roi(tier_p5o, shot, roi=_roi_use, hits=_full_d)
                d5.set_tap(int(confirm_hit_ocr.cx), int(confirm_hit_ocr.cy),
                           method="OCR", target_class="确定",
                           target_text=confirm_hit_ocr.text, screenshot=shot)
            await self.adb.tap(confirm_hit_ocr.cx, confirm_hit_ocr.cy)
            self._mem_remember(shot, "P4-5-confirm", confirm_hit_ocr.cx, confirm_hit_ocr.cy, success=True)
            _slog(f"[阶段6] OCR 兜底命中确定 (尝试 {p4_5_attempts} 次) → tap ({confirm_hit_ocr.cx},{confirm_hit_ocr.cy})")
            if d5: d5.finalize(outcome="confirmed", note=f"OCR 兜底命中 (尝试 {p4_5_attempts} 次)")
        else:
            _slog(f"[阶段6] {p4_5_attempts} 次都没找到确定按钮")
            if d5:
                # 把最后一次 OCR 的 hits 写进去, 方便档案 debug
                _full_d = [_OcrHit(text=h.text, bbox=[h.cx-20, h.cy-10, h.cx+20, h.cy+10],
                                    cx=h.cx, cy=h.cy) for h in confirm_hits_last[:30]]
                tier_p5o = TierRecord(tier=3, name=ocr_tier_name or "OCR·全屏",
                                       note=f"{p4_5_attempts} 次模板+OCR 全 miss",
                                       ocr_hits=_full_d)
                d5.add_tier(tier_p5o)
                _h5, _w5 = (shot_d5.shape[:2] if shot_d5 is not None else (0, 0))
                _roi_use = _btn_roi_px_last if _btn_roi_px_last else [0, 0, _w5, _h5]
                d5.save_ocr_roi(tier_p5o, shot_d5, roi=_roi_use, hits=_full_d)
                d5.finalize(outcome="failed", note=f"模板+OCR {p4_5_attempts} 次全 miss")

        # 恢复守卫 + 总耗时
        if hasattr(self.adb, 'guard_enabled'):
            self.adb.guard_enabled = True
        _t1 = _t.perf_counter()
        _slog(f"[阶段6] 完成 ✓ 总耗时 {(_t1 - _t0)*1000:.0f}ms")
        return True

    # ================================================================
    # 阶段 4: 组队 — 队长创建
    # ================================================================

    @_timed_phase("team_create")
    async def phase_team_create(self) -> Optional[str]:
        """队长创建队伍并获取 game scheme URL（通过二维码）

        拆 5 条独立 sub-decision (跟 P4 一样, 档案能逐条点开看图):
          P3a-1-open    找"组队"按钮 + tap
          P3a-2-tab     切"组队码" tab (含中部弹窗处理)
          P3a-3-qr      点"二维码组队"
          P3a-4-decode  截屏 + QR 解码 → fetch scheme URL
          P3a-5-close   关闭面板

        Returns:
            game scheme URL (如 "pubgmhd1106467070://?tmid:xxx,rlid:xxx,...")
        """
        from .decision_log import get_recorder, TierRecord, OcrHit as _OcrHit
        recorder = get_recorder()
        inst_idx = getattr(self._v3_ctx, "instance_idx", 0) if self._v3_ctx else 0

        def _make_d(sub_name: str, shot_in=None):
            """创建子 decision + (可选) set_input + 顺带采样到 yolo raw screenshots."""
            try:
                d = recorder.new_decision(
                    instance=inst_idx, phase=f"P3a-{sub_name}", round_idx=1)
            except Exception as e:
                logger.debug(f"new_decision err: {e}")
                return None
            if d is not None and shot_in is not None:
                try:
                    d.set_input(shot_in, q=70)
                except Exception:
                    pass
                try:
                    from .screenshot_collector import collect as _yolo_collect
                    _yolo_collect(shot_in, instance=inst_idx, tag=f"P3a-{sub_name}")
                except Exception:
                    pass
            return d

        def _roi_pixels(name: str, img):
            if img is None:
                return None
            try:
                from .roi_config import get as _rg
                rx1, ry1, rx2, ry2, _ = _rg(name)
                ph, pw = img.shape[:2]
                return [max(0, int(pw * rx1)), max(0, int(ph * ry1)),
                        min(pw, int(pw * rx2)), min(ph, int(ph * ry2))]
            except Exception:
                return None

        self.phase = Phase.TEAM_CREATE
        logger.info("[阶段4] 队长创建队伍")

        if hasattr(self.adb, 'guard_enabled'):
            self.adb.guard_enabled = False

        shot = await self.adb.screenshot()
        self.dbg.log_screenshot(shot, tag="team_create_start")
        ocr = OcrDismisser()

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 子 decision 1: P3a-1-open  找"组队"入口并点击
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        import time as _tm
        _p3a_t0 = _tm.perf_counter()
        d1 = _make_d("1-open", shot_in=shot)
        clicked = False
        last_left_hits: list = []
        for attempt in range(3):
            _pt_shot = _tm.perf_counter()
            shot = await self.adb.screenshot()
            logger.debug(f"[P3a-1 PERF] attempt {attempt+1}: screenshot {(_tm.perf_counter()-_pt_shot)*1000:.0f}ms")
            if shot is None:
                await asyncio.sleep(0.5)
                continue
            # ① Memory 快速路径 (命中省 OCR ~1.3s)
            if await self._try_memory_first(shot, "P3a-1-open", d1, "组队按钮"):
                logger.info("[阶段4] Memory 命中, 跳过 OCR")
                clicked = True
                break
            self.dbg.log_screenshot(shot, f"attempt{attempt}")
            _pt_ocr = _tm.perf_counter()
            left_hits = await asyncio.to_thread(ocr._ocr_roi_named, shot, "team_btn_left")
            logger.debug(f"[P3a-1 PERF] attempt {attempt+1}: OCR team_btn_left {(_tm.perf_counter()-_pt_ocr)*1000:.0f}ms ({len(left_hits)} 文字)")
            last_left_hits = left_hits
            self.dbg.log_ocr(left_hits, "ROI=team_btn_left")
            # ② OCR 主匹配: 文字含 "组" 或 "队" 都算
            for h in left_hits:
                if "队友" in h.text:
                    continue
                if ("组" in h.text) or ("队" in h.text):
                    self.dbg.log_match("组队(单字命中)", h, fuzzy=True)
                    if d1:
                        d1.set_tap(int(h.cx), int(h.cy), method="OCR",
                                   target_class="组队按钮", target_text=h.text,
                                   screenshot=shot)
                    await self.adb.tap(h.cx, h.cy)
                    # 成功 tap → 给 memory 累计 (5 次同位置 + std<15px 才落库)
                    self._mem_remember(shot, "P3a-1-open", h.cx, h.cy, success=True)
                    logger.info(f"[阶段4] 点击组队 (text='{h.text}', cx={h.cx}, cy={h.cy})")
                    clicked = True
                    break
            if clicked:
                break
            # 兜底: "队友"按钮在组队按钮下方约 100px, 反推上去
            for h in left_hits:
                if OcrDismisser.fuzzy_match(h.text, "队友"):
                    tap_y = max(h.cy - 100, 50)
                    if d1:
                        d1.set_tap(int(h.cx), int(tap_y), method="OCR",
                                   target_class="组队按钮(经队友定位)",
                                   target_text=h.text, screenshot=shot)
                    await self.adb.tap(h.cx, tap_y)
                    logger.info(f"[阶段4] 通过'找队友'定位组队 ({h.cx},{tap_y})")
                    clicked = True
                    break
            if clicked:
                break
            await asyncio.sleep(0.5)
        logger.debug(f"[P3a-1 PERF] 总 (找组队按钮): {(_tm.perf_counter()-_p3a_t0)*1000:.0f}ms, 尝试 {attempt+1} 次")

        if d1:
            _hits_d = [_OcrHit(text=h.text, bbox=[h.cx-20, h.cy-10, h.cx+20, h.cy+10],
                                cx=h.cx, cy=h.cy) for h in last_left_hits[:15]]
            tier1 = TierRecord(
                tier=3, name="OCR·team_btn_left", early_exit=clicked,
                note=f"找'组队'按钮 (尝试 {attempt+1} 次), 命中={clicked}, "
                     f"识别 {len(last_left_hits)} 文字",
                ocr_hits=_hits_d,
            )
            _pt_save = _tm.perf_counter()
            d1.add_tier(tier1)
            d1.save_ocr_roi(tier1, shot, roi=_roi_pixels("team_btn_left", shot),
                            hits=_hits_d)
            logger.debug(f"[P3a-1 PERF] add_tier+save_ocr_roi: {(_tm.perf_counter()-_pt_save)*1000:.0f}ms")
            d1.finalize(outcome="opened" if clicked else "failed",
                        note="点开组队界面" if clicked else "找不到组队按钮")
        if not clicked:
            logger.warning("[阶段4] 未找到组队按钮")
            self._restore_guard()
            return None

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 子 decision 2: P3a-2-tab  切"组队码" tab (中部弹窗处理在此 tier 里)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        _pt_sleep = _tm.perf_counter()
        await asyncio.sleep(0.3)  # 等面板动画 (从 0.8 改 0.3, 拍过渡帧也有 retry 兜底)
        logger.debug(f"[P3a-2 PERF] 等面板动画 sleep: {(_tm.perf_counter()-_pt_sleep)*1000:.0f}ms")
        _pt_shot = _tm.perf_counter()
        shot_d2 = await self.adb.screenshot()
        logger.debug(f"[P3a-2 PERF] 入口 screenshot: {(_tm.perf_counter()-_pt_shot)*1000:.0f}ms")
        d2 = _make_d("2-tab", shot_in=shot_d2)
        _p3a2_t0 = _tm.perf_counter()
        tab_clicked = False
        last_bottom_hits: list = []
        for attempt in range(5):
            _pt_shot = _tm.perf_counter()
            shot = await self.adb.screenshot() if attempt > 0 else shot_d2
            logger.debug(f"[P3a-2 PERF] attempt {attempt+1}: screenshot {(_tm.perf_counter()-_pt_shot)*1000:.0f}ms")
            if shot is None:
                await asyncio.sleep(0.3)
                continue

            # ① Memory 快速路径
            if await self._try_memory_first(shot, "P3a-2-tab", d2, "组队码tab"):
                logger.info("[阶段4] P3a-2 Memory 命中, 跳过 OCR")
                tab_clicked = True
                break

            # ② OCR 底部"组队码" tab
            _pt_ocr = _tm.perf_counter()
            bottom_hits = await asyncio.to_thread(ocr._ocr_roi_named, shot, "team_code_tab_bottom")
            logger.debug(f"[P3a-2 PERF] attempt {attempt+1}: OCR team_code_tab_bottom {(_tm.perf_counter()-_pt_ocr)*1000:.0f}ms ({len(bottom_hits)} 文字)")
            last_bottom_hits = bottom_hits
            for h in bottom_hits:
                if OcrDismisser.fuzzy_match(h.text, "组队码"):
                    if d2:
                        d2.set_tap(int(h.cx), int(h.cy), method="OCR",
                                   target_class="组队码 tab", target_text=h.text,
                                   screenshot=shot)
                    await self.adb.tap(h.cx, h.cy)
                    self._mem_remember(shot, "P3a-2-tab", h.cx, h.cy, success=True)
                    logger.info(f"[阶段4] 点击组队码tab ({h.cx},{h.cy})")
                    tab_clicked = True
                    break
            if tab_clicked:
                break
            await asyncio.sleep(0.3)
        logger.debug(f"[P3a-2 PERF] 总 (loop): {(_tm.perf_counter()-_p3a2_t0)*1000:.0f}ms")

        if d2:
            _hits_d = [_OcrHit(text=h.text, bbox=[h.cx-20, h.cy-10, h.cx+20, h.cy+10],
                                cx=h.cx, cy=h.cy) for h in last_bottom_hits[:15]]
            note = f"切组队码 tab, 命中={tab_clicked}"
            tier2 = TierRecord(
                tier=3, name="OCR·team_code_tab_bottom", early_exit=tab_clicked,
                note=note, ocr_hits=_hits_d,
            )
            _pt_save = _tm.perf_counter()
            d2.add_tier(tier2)
            d2.save_ocr_roi(tier2, shot,
                            roi=_roi_pixels("team_code_tab_bottom", shot),
                            hits=_hits_d)
            logger.debug(f"[P3a-2 PERF] add_tier+save_ocr_roi: {(_tm.perf_counter()-_pt_save)*1000:.0f}ms")
            d2.finalize(outcome="tab_switched" if tab_clicked else "failed",
                        note=note)
        if not tab_clicked:
            logger.warning("[阶段4] 未切到组队码 tab")
            self._restore_guard()
            return None

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 子 decision 3: P3a-3-qr  点"二维码组队"
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        await asyncio.sleep(0.3)  # 等 tab 切换 (从 0.5 改 0.3, retry 兜底)
        shot_d3 = await self.adb.screenshot()
        d3 = _make_d("3-qr", shot_in=shot_d3)
        qr_clicked = False
        last_qr_hits: list = []
        _p3a3_t0 = _tm.perf_counter()
        for attempt in range(4):
            _pt_shot = _tm.perf_counter()
            shot = await self.adb.screenshot() if attempt > 0 else shot_d3
            logger.debug(f"[P3a-3 PERF] attempt {attempt+1}: screenshot {(_tm.perf_counter()-_pt_shot)*1000:.0f}ms")
            if shot is None:
                await asyncio.sleep(0.3)
                continue
            # ① Memory 快速路径
            if await self._try_memory_first(shot, "P3a-3-qr", d3, "二维码组队"):
                logger.info("[阶段4] P3a-3 Memory 命中, 跳过 OCR")
                qr_clicked = True
                break
            # ② OCR
            _pt_ocr = _tm.perf_counter()
            left_hits = await asyncio.to_thread(ocr._ocr_roi_named, shot, "qr_team_btn_left")
            logger.debug(f"[P3a-3 PERF] attempt {attempt+1}: OCR qr_team_btn_left {(_tm.perf_counter()-_pt_ocr)*1000:.0f}ms ({len(left_hits)} 文字)")
            last_qr_hits = left_hits
            for h in left_hits:
                if OcrDismisser.fuzzy_match(h.text, "二维码"):
                    if d3:
                        d3.set_tap(int(h.cx), int(h.cy), method="OCR",
                                   target_class="二维码组队", target_text=h.text,
                                   screenshot=shot)
                    await self.adb.tap(h.cx, h.cy)
                    self._mem_remember(shot, "P3a-3-qr", h.cx, h.cy, success=True)
                    logger.info(f"[阶段4] 点击二维码组队 ({h.cx},{h.cy})")
                    qr_clicked = True
                    break
            if qr_clicked:
                break
            await asyncio.sleep(0.3)
        logger.debug(f"[P3a-3 PERF] 总 (找二维码组队): {(_tm.perf_counter()-_p3a3_t0)*1000:.0f}ms")

        if d3:
            _hits_d = [_OcrHit(text=h.text, bbox=[h.cx-20, h.cy-10, h.cx+20, h.cy+10],
                                cx=h.cx, cy=h.cy) for h in last_qr_hits[:15]]
            tier3 = TierRecord(
                tier=3, name="OCR·qr_team_btn_left", early_exit=qr_clicked,
                note=f"找'二维码组队', 命中={qr_clicked}",
                ocr_hits=_hits_d,
            )
            _pt_save = _tm.perf_counter()
            d3.add_tier(tier3)
            d3.save_ocr_roi(tier3, shot,
                            roi=_roi_pixels("qr_team_btn_left", shot),
                            hits=_hits_d)
            logger.debug(f"[P3a-3 PERF] add_tier+save_ocr_roi: {(_tm.perf_counter()-_pt_save)*1000:.0f}ms")
            d3.finalize(outcome="qr_opened" if qr_clicked else "failed",
                        note=tier3.note)
        if not qr_clicked:
            logger.warning("[阶段4] 未找到二维码组队入口")
            self._restore_guard()
            return None

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 子 decision 4: P3a-4-decode  截屏 + QR 解码 + fetch scheme
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        await asyncio.sleep(0.3)  # 等 QR 显示 (从 0.5 改 0.3, decode loop 已有 retry)
        shot_d4 = await self.adb.screenshot()
        d4 = _make_d("4-decode", shot_in=shot_d4)
        qr_url = ""
        decode_attempts = 0
        decode_winner = ""  # 哪种策略最后赢了 (打 PERF)
        # 试 pyzbar 是否能 import (装了用, 没装走 cv2)
        try:
            from pyzbar import pyzbar as _pyzbar
            _has_pyzbar = True
        except Exception:
            _pyzbar = None
            _has_pyzbar = False

        def _try_decode(crop_bgr: np.ndarray) -> tuple[str, str]:
            """对一张已裁剪的 crop 跑 5 种策略, 返回 (data, winner_name).
            winner_name 空字符串 = 全失败."""
            if crop_bgr is None or crop_bgr.size == 0:
                return "", ""
            big = cv2.resize(crop_bgr, (0, 0), fx=_qr_scale, fy=_qr_scale,
                              interpolation=cv2.INTER_CUBIC)
            gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)

            # ① pyzbar 直接读灰度
            if _has_pyzbar:
                try:
                    res = _pyzbar.decode(gray)
                    if res:
                        d = res[0].data.decode("utf-8", errors="ignore")
                        if d:
                            return d, "pyzbar·gray"
                except Exception:
                    pass

            # ② pyzbar + OTSU 自适应阈值 (整图自动定阈)
            if _has_pyzbar:
                try:
                    _, otsu = cv2.threshold(gray, 0, 255,
                                             cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                    res = _pyzbar.decode(otsu)
                    if res:
                        d = res[0].data.decode("utf-8", errors="ignore")
                        if d:
                            return d, "pyzbar·otsu"
                except Exception:
                    pass

            # ③ pyzbar + 局部自适应阈值 (抗光照不均)
            if _has_pyzbar:
                try:
                    adapt = cv2.adaptiveThreshold(
                        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                        cv2.THRESH_BINARY, 21, 5)
                    res = _pyzbar.decode(adapt)
                    if res:
                        d = res[0].data.decode("utf-8", errors="ignore")
                        if d:
                            return d, "pyzbar·adaptive"
                except Exception:
                    pass

            # ④ cv2 内置 QRCodeDetector + OTSU
            try:
                _, otsu = cv2.threshold(gray, 0, 255,
                                         cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                d, _, _ = cv2.QRCodeDetector().detectAndDecode(otsu)
                if d:
                    return d, "cv2·otsu"
            except Exception:
                pass

            # ⑤ cv2 内置 QRCodeDetector + 硬阈值 128 (老路径, 兜底)
            try:
                _, hard = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY)
                d, _, _ = cv2.QRCodeDetector().detectAndDecode(hard)
                if d:
                    return d, "cv2·hard128"
            except Exception:
                pass

            return "", ""

        from .roi_config import get as _roi_get
        _x1, _y1, _x2, _y2, _qr_scale = _roi_get("qr_decode_crop")

        for attempt in range(5):
            decode_attempts = attempt + 1
            shot = await self.adb.screenshot() if attempt > 0 else shot_d4
            if shot is None:
                await asyncio.sleep(0.5)
                continue
            h_img, w_img = shot.shape[:2]
            crop = shot[int(h_img * _y1):int(h_img * _y2),
                        int(w_img * _x1):int(w_img * _x2)]
            _qr_t0 = _tm.perf_counter()
            data, winner = _try_decode(crop)
            _qr_dur = (_tm.perf_counter() - _qr_t0) * 1000
            if data:
                qr_url = data
                decode_winner = winner
                logger.info(f"[阶段4] QR 解码成功 by {winner} ({_qr_dur:.0f}ms): {data[:60]}...")
                break
            logger.info(f"[阶段4] QR 解码失败 attempt {attempt+1}/5 (5 种策略全 miss, {_qr_dur:.0f}ms)")
            await asyncio.sleep(0.5)

        # fetch game scheme via HTTP
        game_scheme = ""
        if qr_url:
            try:
                import urllib.request
                loop = asyncio.get_event_loop()
                def _fetch_scheme(url: str) -> str:
                    req = urllib.request.Request(url, headers={
                        "User-Agent": "Mozilla/5.0 (Linux; Android 7.1.2)"
                    })
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        html = resp.read().decode("utf-8", errors="ignore")
                    import re
                    match = re.search(r'(pubgmhd\d+://[^"\']+)', html)
                    return match.group(1) if match else ""
                game_scheme = await loop.run_in_executor(None, _fetch_scheme, qr_url)
            except Exception as e:
                logger.error(f"[阶段4] 获取 game scheme 失败: {e}")

        if d4:
            note = (f"QR解码尝试 {decode_attempts} 次, "
                    f"赢家策略={decode_winner or '无'}, "
                    f"qr_url={'有' if qr_url else '无'}, "
                    f"game_scheme={'有' if game_scheme else '无'}")
            tier4 = TierRecord(
                tier=4, name="QR·decode+fetch", early_exit=bool(game_scheme),
                note=note,
                ocr_roi=_roi_pixels("qr_decode_crop", shot),
            )
            d4.add_tier(tier4)
            # QR 区域可视化 (无 OCR hits, 只画 ROI)
            d4.save_ocr_roi(tier4, shot,
                            roi=_roi_pixels("qr_decode_crop", shot),
                            hits=[])
            d4.finalize(outcome="scheme_ok" if game_scheme else "decode_fail",
                        note=note + (f"\nscheme={game_scheme[:80]}" if game_scheme else ""))

        if not game_scheme:
            logger.error("[阶段4] 无法解码QR码 / 获取 scheme")
            self._restore_guard()
            return None
        logger.info(f"[阶段4] game scheme: {game_scheme}")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 子 decision 5: P3a-5-close  关闭面板
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        shot_d5 = await self.adb.screenshot()
        d5 = _make_d("5-close", shot_in=shot_d5)
        last_close_hit = None
        last_method = ""
        closed = False
        for _ in range(4):
            shot = await self.adb.screenshot()
            if shot is None:
                break
            # 已回大厅 → 完成
            if self.matcher.match_one(shot, "lobby_start_btn", threshold=0.7) \
               or self.matcher.match_one(shot, "lobby_start_game", threshold=0.7):
                closed = True
                break

            # ① 优先用专门裁的 zuduima_close 模板 (组队码面板的 X 按钮)
            zd_hit = None
            try:
                zd_hit = self.matcher.match_one(shot, "zuduima_close", threshold=0.7)
            except Exception:
                zd_hit = None
            if zd_hit:
                last_close_hit = zd_hit
                last_method = "模板·zuduima_close"
                logger.info(f"[阶段4] 关闭(zuduima_close) ({zd_hit.cx},{zd_hit.cy}) conf={zd_hit.confidence:.2f}")
                if d5:
                    d5.set_tap(int(zd_hit.cx), int(zd_hit.cy), method="模板",
                               target_class="组队码面板X", target_conf=float(zd_hit.confidence),
                               screenshot=shot)
                await self.adb.tap(zd_hit.cx, zd_hit.cy)
                await asyncio.sleep(0.3)
                continue

            # ② YOLO 兜底找 close_x — 复用 runner.yolo_dismisser (per-instance session,
            # 同主流程模型, 替代老 yolo_detector 模块)
            yolo_hit = None
            try:
                if self.yolo_dismisser.is_available():
                    dets = self.yolo_dismisser.detect(shot)
                    close_xs = [d for d in dets if d.name == "close_x" and d.conf >= 0.4]
                    if close_xs:
                        yolo_hit = close_xs[0]
            except Exception:
                yolo_hit = None
            if yolo_hit is not None:
                ycx, ycy = yolo_hit.cx, yolo_hit.cy
                last_method = f"YOLO·close_x ({yolo_hit.conf:.2f})"
                logger.info(f"[阶段4] 关闭(YOLO close_x) ({ycx},{ycy}) score={yolo_hit.conf:.2f}")
                if d5:
                    d5.set_tap(int(ycx), int(ycy), method="YOLO",
                               target_class="close_x", target_conf=float(yolo_hit.conf),
                               screenshot=shot)
                await self.adb.tap(ycx, ycy)
                await asyncio.sleep(0.3)
                continue

            # ③ 通用 close_x 模板组 (find_dialog_close 内含多种 close_x_* 模板)
            close = self.matcher.find_dialog_close(shot)
            if close:
                last_close_hit = close
                last_method = "模板·close_x(any)"
                logger.info(f"[阶段4] 关闭(通用 close_x) ({close.cx},{close.cy})")
                if d5:
                    d5.set_tap(int(close.cx), int(close.cy), method="模板",
                               target_class="关闭按钮", target_conf=float(close.confidence),
                               screenshot=shot)
                await self.adb.tap(close.cx, close.cy)
                await asyncio.sleep(0.3)
                continue

            # ④ 全部 miss → 兜底点面板外空白
            h_img, w_img = shot.shape[:2]
            tap_x, tap_y = w_img * 3 // 4, h_img // 2
            last_method = "外部空白(兜底)"
            if d5 and last_close_hit is None:
                d5.set_tap(int(tap_x), int(tap_y), method="空白",
                           target_class="面板外空白(兜底)", screenshot=shot)
            await self.adb.tap(tap_x, tap_y)
            await asyncio.sleep(0.3)

        if d5:
            tier5 = TierRecord(
                tier=0 if last_method.startswith("模板") else 4,
                name=last_method or "关闭面板",
                early_exit=closed,
                note=f"关面板, 已回大厅={closed}, 方法={last_method or '未触发'}",
            )
            d5.add_tier(tier5)
            d5.finalize(outcome="closed" if closed else "maybe_closed",
                        note=f"已回大厅={closed}")

        logger.info("[阶段4] 已关闭组队面板")
        shot = await self.adb.screenshot()
        self.dbg.log_screenshot(shot, tag="team_create_done")
        self._restore_guard()
        return game_scheme

    def _restore_guard(self):
        if hasattr(self.adb, 'guard_enabled'):
            self.adb.guard_enabled = True

    # ================================================================
    # 阶段 5: 组队 — 队员加入
    # ================================================================

    @_timed_phase("team_join")
    async def phase_team_join(self, game_scheme_url: str) -> bool:
        """队员通过 game scheme URL 直接加入队伍

        拆 2 条 sub-decision (跟 P4/P3a 一样, 档案能逐条看图):
          P3b-1-launch  am start scheme://
          P3b-2-verify  轮询 OCR "取消准备" 验证已加入

        Args:
            game_scheme_url: 游戏内部 scheme URL
                如 "pubgmhd1106467070://?tmid:xxx,rlid:xxx,t:xxx,p:2"
        """
        from .decision_log import get_recorder, TierRecord, OcrHit as _OcrHit
        recorder = get_recorder()
        inst_idx = getattr(self._v3_ctx, "instance_idx", 0) if self._v3_ctx else 0

        def _make_d(sub_name: str, shot_in=None):
            """创建子 decision + (可选) set_input + 顺带采样到 yolo raw screenshots."""
            try:
                d = recorder.new_decision(
                    instance=inst_idx, phase=f"P3b-{sub_name}", round_idx=1)
            except Exception as e:
                logger.debug(f"new_decision err: {e}")
                return None
            if d is not None and shot_in is not None:
                try:
                    d.set_input(shot_in, q=70)
                except Exception:
                    pass
                try:
                    from .screenshot_collector import collect as _yolo_collect
                    _yolo_collect(shot_in, instance=inst_idx, tag=f"P3b-{sub_name}")
                except Exception:
                    pass
            return d

        self.phase = Phase.TEAM_JOIN
        logger.info(f"[阶段5] 队员加入队伍 (scheme: {game_scheme_url[:50]}...)")

        raw_adb = getattr(self.adb, '_adb', self.adb)
        loop = asyncio.get_event_loop()

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 子 decision 1: P3b-1-launch  am start scheme
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        shot = await self.adb.screenshot()
        self.dbg.log_screenshot(shot, tag="team_join_start")
        d1 = _make_d("1-launch", shot_in=shot)

        output = await loop.run_in_executor(
            None, raw_adb._cmd, "shell",
            f"am start -a android.intent.action.VIEW -d '{game_scheme_url}'"
        )
        out_short = (output or "").strip()
        logger.info(f"[阶段5] am start 结果: {out_short[:200]}")

        # am start 失败信号: stderr 含 "Error", 没有 "Starting" / "Activity"
        launch_ok = ("Error" not in out_short) and bool(out_short)
        if d1:
            tier1 = TierRecord(
                tier=4, name="ADB·am start",
                early_exit=launch_ok,
                note=f"scheme={game_scheme_url[:80]}\nam_start_output={out_short[:200]}",
            )
            d1.add_tier(tier1)
            d1.finalize(
                outcome="launched" if launch_ok else "launch_failed",
                note=f"am start {'OK' if launch_ok else '失败'}: {out_short[:120]}",
            )

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 子 decision 2: P3b-2-verify  轮询验证 "取消准备"
        # 改动: sleep 1.0 → 0.5 (上限耗时不变, 检测延迟砍半);
        # OCR 优先用 ROI team_ready_btn (用户在 OCR Tuner 配), 没配兜底走全屏
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        d2 = _make_d("2-verify")
        ocr = OcrDismisser()
        joined = False
        last_hits: list = []
        last_shot = None
        attempt_idx = 0
        # ROI 'team_ready_btn' 是队员屏幕"取消准备"按钮区域 (左上角小块)
        # 优先用它 → OCR 区域小, ~200ms vs 全屏 2s
        try:
            from .roi_config import all_names as _all_roi
            has_ready_roi = "team_ready_btn" in set(_all_roi())
        except Exception:
            has_ready_roi = False
        for attempt in range(20):  # 20 × 0.5s = 10s 上限, 跟原来 10×1s 一致
            attempt_idx = attempt + 1
            await asyncio.sleep(0.5)
            shot = await raw_adb.screenshot()
            if shot is None:
                continue
            last_shot = shot
            if has_ready_roi:
                hits = await asyncio.to_thread(ocr._ocr_roi_named, shot, "team_ready_btn")
            else:
                hits = await ocr._ocr_all_async(shot)
            last_hits = hits
            for h in hits:
                if "取消" in h.text and "准备" in h.text:
                    logger.info(f"[阶段5] 队员加入完成 ✓（{attempt_idx}×0.5s 检测到取消准备, ROI={'team_ready_btn' if has_ready_roi else 'fullscreen'}）")
                    self.dbg.log_screenshot(shot, tag="team_join_done")
                    joined = True
                    break
            if joined:
                break
            # 还在大厅 → 继续等
            if self.matcher.match_one(shot, "lobby_start_btn", threshold=0.7) \
               or self.matcher.match_one(shot, "lobby_start_game", threshold=0.7):
                continue

        if d2:
            if last_shot is not None:
                d2.set_input(last_shot, q=70)
                # 顺带采样到 yolo raw screenshots
                try:
                    from .screenshot_collector import collect as _yolo_collect
                    _yolo_collect(last_shot, instance=inst_idx, tag="P3b-2-verify")
                except Exception:
                    pass
            _hits_d = [_OcrHit(text=h.text, bbox=[h.cx-20, h.cy-10, h.cx+20, h.cy+10],
                                cx=h.cx, cy=h.cy) for h in last_hits[:15]]
            tier2 = TierRecord(
                tier=3,
                name=f"OCR·{'team_ready_btn ROI' if has_ready_roi else '全屏'}(取消准备)",
                early_exit=joined,
                note=f"轮询 {attempt_idx} × 0.5s, 加入={joined}, ROI={'team_ready_btn' if has_ready_roi else 'fullscreen'}",
                ocr_hits=_hits_d,
            )
            d2.add_tier(tier2)
            if last_shot is not None:
                _h, _w = last_shot.shape[:2]
                d2.save_ocr_roi(tier2, last_shot, roi=[0, 0, _w, _h], hits=_hits_d)
            d2.finalize(outcome="joined" if joined else "timeout",
                        note=f"轮询 {attempt_idx} 次后{'确认加入' if joined else '超时'}")

        if not joined:
            logger.warning("[阶段5] 队员加入超时，未检测到取消准备")
        return joined

