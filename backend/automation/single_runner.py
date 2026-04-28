"""
SingleInstanceRunner — 单实例自动化运行器
控制一个模拟器实例完成: 加速器 → 启动游戏 → 弹窗清理 → 大厅确认 → 组队 → 地图设置

可在Windows上直接运行:
  python -m backend.automation.single_runner --instance 0
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import cv2
import numpy as np

from .adb_lite import ADBController, phash, phash_distance
from .screen_matcher import MatchHit, ScreenMatcher
from .popup_dismisser import PopupDismisser
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
ACCELERATOR_PACKAGE = "com.fightmaster.vpn"


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
        self.popup_dismisser = PopupDismisser(matcher)
        self.ocr_dismisser = OcrDismisser(max_rounds=25)
        # YOLO 视觉识别（替代 OCR/模板/形状的层叠链）
        # 模型不存在时 dismiss_all 自动 fallback 到 OcrDismisser
        from .yolo_dismisser import YoloDismisser
        self.yolo_dismisser = YoloDismisser(max_rounds=25)
        self._team_code: str = ""  # 队长生成的口令码
        self._last_phash: int = 0  # 帧差跳过：上一帧的 pHash
        self.dbg = DebugLogger(enabled=bool(log_dir), save_dir=log_dir or "logs")

        # v3 资源 (lazy 初始化)
        self._v3_ctx = None  # backend.automation.phase_base.RunContext
        self._v3_memory = None
        self._v3_lobby_detector = None
        self._v3_recognizer = None

    def _build_v3_ctx(self):
        """构造 v3 RunContext (lazy, 缓存)."""
        if self._v3_ctx is not None:
            return self._v3_ctx
        from .phase_base import RunContext
        from .recognizer import Recognizer
        from .lobby_check import LobbyQuadDetector
        from .memory_l1 import FrameMemory
        from .user_paths import user_data_dir
        from .decision_log import get_recorder

        if self._v3_memory is None:
            try:
                self._v3_memory = FrameMemory(user_data_dir() / "memory" / "dismiss_popups.db")
            except Exception as _e:
                logger.warning(f"[v3] memory 初始化失败 (非致命): {_e}")
                self._v3_memory = None

        if self._v3_lobby_detector is None:
            self._v3_lobby_detector = LobbyQuadDetector(stable_frames_required=2)

        if self._v3_recognizer is None:
            self._v3_recognizer = Recognizer(
                matcher=self.matcher,
                yolo_detect_fn=(self.yolo_dismisser.detect
                                if self.yolo_dismisser.is_available() else None),
                memory=self._v3_memory,
            )

        self._v3_ctx = RunContext(
            device=self.adb,
            matcher=self.matcher,
            recognizer=self._v3_recognizer,
            runner=self,
            yolo=self.yolo_dismisser,
            memory=self._v3_memory,
            lobby_detector=self._v3_lobby_detector,
            decision_recorder=get_recorder(),
            instance_idx=-1,
            account=None,
            settings=None,
            role="leader" if self.role == "captain" else "follower",
        )
        return self._v3_ctx

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

        for rnd in range(handler.max_rounds):
            if _cancelled():
                logger.info(f"[{handler.name}] 收到取消信号 → 中止 (R{rnd})")
                await handler.exit(ctx, PhaseResult.FAIL)
                return False
            ctx.phase_round = rnd + 1
            try:
                shot = await self.adb.screenshot()
            except Exception as _e:
                shot = None
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
            decision = None
            try:
                decision = recorder.new_decision(
                    instance=ctx.instance_idx,
                    phase=handler.name,
                    round_idx=ctx.phase_round,
                )
                decision.set_input(shot, phash_str, q=70)
                ctx.current_decision = decision
            except Exception as e:
                logger.debug(f"[{handler.name}] new_decision err: {e}")

            handle_exc = None
            step = None
            try:
                step = await handler.handle_frame(ctx)
            except Exception as e:
                handle_exc = e
                logger.warning(f"[{handler.name}/R{rnd+1}] handle_frame 异常: {e}", exc_info=True)

            # 实施 action (set_tap/verify 在 ActionExecutor 里写 ctx.current_decision)
            if step is not None and step.action is not None:
                try:
                    await ActionExecutor.apply(ctx, step.action)
                except Exception as e:
                    logger.warning(f"[{handler.name}] ActionExecutor 异常: {e}")

            # finalize
            try:
                if decision is not None:
                    if step is not None:
                        outcome = step.outcome_hint or _result_to_outcome_str(step.result)
                        decision.finalize(outcome=outcome, note=step.note or "")
                    else:
                        decision.finalize(outcome="phase_exception", note=repr(handle_exc) if handle_exc else "")
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
            sleep_s = max(0.0, step.wait_seconds) if step.result == PhaseResult.WAIT else handler.round_interval_s
            if await _interruptible_sleep(sleep_s):
                logger.info(f"[{handler.name}] 取消信号 (sleep 期间) → 中止 (R{rnd})")
                await handler.exit(ctx, PhaseResult.FAIL)
                return False
        logger.warning(f"[{handler.name}] 超 max_rounds={handler.max_rounds} → FAIL")
        await handler.exit(ctx, PhaseResult.FAIL)
        return False


    def _frame_changed(self, shot: np.ndarray, threshold: int = 4) -> bool:
        """pHash 帧差检测：画面没变返回 False，跳过 OCR"""
        h = phash(shot)
        dist = phash_distance(h, self._last_phash)
        self._last_phash = h
        if dist < threshold:
            logger.debug(f"[帧差] 跳过 OCR (距离={dist})")
            return False
        return True

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
    # 阶段 0: 加速器
    # ================================================================

    @_timed_phase("accelerator")
    async def phase_accelerator(self) -> bool:
        """启动 FightMaster VPN 并确认连接

        两级策略：
        1. 广播 START（快速路径，~1s，适合 VPN 已初始化过的情况）
        2. UI 回退（拉起界面 + 点击连接，处理首次权限弹窗等）
        """
        self.phase = Phase.ACCELERATOR

        # 快速检查：VPN 是否已连接
        if await self._check_vpn_connected():
            logger.info("[阶段0] FightMaster 已连接 ✓ 跳过启动")
            shot = await self.adb.screenshot()
            self.dbg.log_screenshot(shot, tag="vpn_connected")
            return True

        # 先尝试广播启动（快速路径）
        await self._start_vpn()
        if await self._wait_vpn_connected(timeout=8):
            logger.info("[阶段0] FightMaster 广播启动成功 ✓")
            shot = await self.adb.screenshot()
            self.dbg.log_screenshot(shot, tag="vpn_connected")
            return True

        # 广播失败 → UI 回退（拉起界面点连接）
        logger.warning("[阶段0] 广播启动失败，切换到 UI 模式")
        for retry in range(3):
            if retry > 0:
                logger.info(f"[阶段0] UI 模式第{retry+1}次重试")
                await self._stop_vpn()
                await asyncio.sleep(2)

            await self._start_vpn_via_ui()

            if await self._wait_vpn_connected(timeout=10):
                logger.info("[阶段0] FightMaster UI 启动成功 ✓")
                shot = await self.adb.screenshot()
                self.dbg.log_screenshot(shot, tag="vpn_connected")
                return True

        logger.error("[阶段0] FightMaster 所有方式均失败")
        shot = await self.adb.screenshot()
        self.dbg.log_screenshot(shot, tag="vpn_failed")
        return False

    async def _check_vpn_connected(self) -> bool:
        """4 信号联合判定 VPN 真实连接状态（无 OCR，~200-400ms）

        全部必须通过：
        1. FightMaster 进程在跑（pgrep / ps）
        2. VpnService 在 dumpsys 里
        3. tun0 接口存在且 UP
        4. 默认路由经过 tun0（流量真的走加速器）

        修掉旧版的两个鸡肋：
          - 旧：只查 VpnService 不查进程 → 进程没了 dumpsys 还可能误报
          - 旧：要求 RX > 0 → 新建连接合法地 RX=0 时被误判失败
        """
        loop = asyncio.get_event_loop()
        raw_adb = getattr(self.adb, '_adb', self.adb)

        async def _shell(cmd: str) -> str:
            return await loop.run_in_executor(None, raw_adb._cmd, "shell", cmd)

        # 信号 1: FightMaster 进程在跑
        # pgrep 在某些 Android 版本可能 missing → ps + grep 兜底
        proc_out = await _shell(f"pgrep -f {ACCELERATOR_PACKAGE} 2>/dev/null")
        if not proc_out.strip():
            ps_out = await _shell(f"ps -A 2>/dev/null | grep {ACCELERATOR_PACKAGE}")
            if ACCELERATOR_PACKAGE not in ps_out:
                self.dbg.log_vpn(False, f"{ACCELERATOR_PACKAGE} 进程不存在")
                return False

        # 信号 2: VpnService 在 dumpsys 里
        svc_out = await _shell(f"dumpsys activity services {ACCELERATOR_PACKAGE}")
        if "FightMasterVpnService" not in svc_out:
            self.dbg.log_vpn(False, "VpnService 未运行")
            return False

        # 信号 3: tun0 UP（ip addr 优先，ifconfig 兜底）
        tun_out = await _shell("ip addr show tun0 2>/dev/null")
        tun_up = "tun0" in tun_out and ("state UP" in tun_out or "UP," in tun_out)
        if not tun_up:
            tun_out2 = await _shell("ifconfig tun0 2>/dev/null")
            if "UP" not in tun_out2:
                self.dbg.log_vpn(False, "tun0 不存在或未 UP")
                return False

        # 信号 4: 默认路由经过 tun0（确保流量真的走加速器）
        route_out = await _shell("ip route 2>/dev/null")
        # 默认路由形如 "default via 10.0.0.1 dev tun0" 或 "0.0.0.0/0 dev tun0"
        default_lines = [ln for ln in route_out.splitlines() if "default" in ln or "0.0.0.0/0" in ln]
        if default_lines and not any("tun0" in ln for ln in default_lines):
            self.dbg.log_vpn(False, f"默认路由不经过 tun0：{default_lines[0][:80]}")
            return False

        self.dbg.log_vpn(True, "4 信号全过 ✓")
        return True

    async def _wait_vpn_connected(self, timeout: int = 15) -> bool:
        """轮询等待 VPN 连接建立并验证通过"""
        for _ in range(timeout * 2):  # 每 0.5 秒检查一次
            await asyncio.sleep(0.5)
            if await self._check_vpn_connected():
                return True
        return False

    async def _start_vpn(self):
        """通过 ADB 广播启动 FightMaster VPN"""
        logger.info("[阶段0] 启动 FightMaster VPN")
        loop = asyncio.get_event_loop()
        raw_adb = getattr(self.adb, '_adb', self.adb)
        await loop.run_in_executor(
            None, raw_adb._cmd, "shell",
            "am broadcast -a com.fightmaster.vpn.START "
            "-n com.fightmaster.vpn/.CommandReceiver"
        )

    async def _stop_vpn(self):
        """通过 ADB 广播停止 FightMaster VPN"""
        loop = asyncio.get_event_loop()
        raw_adb = getattr(self.adb, '_adb', self.adb)
        await loop.run_in_executor(
            None, raw_adb._cmd, "shell",
            "am broadcast -a com.fightmaster.vpn.STOP "
            "-n com.fightmaster.vpn/.CommandReceiver"
        )

    async def _start_vpn_via_ui(self):
        """通过拉起 FightMaster UI + OCR 点击连接按钮启动 VPN

        广播 establish() 可能因未经 UI 初始化而失败，
        从界面点击则走完整流程（prepare → consent → establish）
        """
        loop = asyncio.get_event_loop()
        raw_adb = getattr(self.adb, '_adb', self.adb)

        # 1. 拉起 FightMaster 主界面
        logger.info("[阶段0] 拉起 FightMaster 界面")
        await loop.run_in_executor(
            None, raw_adb._cmd, "shell",
            f"am start -n {ACCELERATOR_PACKAGE}/.MainActivity"
        )
        await asyncio.sleep(2)

        # 2. 截图 + OCR
        shot = await raw_adb.screenshot()
        if shot is None:
            logger.error("[阶段0] UI 模式截图失败")
            return

        hits = self.ocr_dismisser._ocr_all(shot)
        all_text = " ".join(h.text for h in hits)
        logger.info(f"[阶段0] FightMaster UI OCR: {all_text[:100]}")

        # 如果已连接，直接返回
        if "已连接" in all_text:
            logger.info("[阶段0] FightMaster 界面显示已连接 ✓")
            await loop.run_in_executor(
                None, raw_adb._cmd, "shell", "input keyevent KEYCODE_HOME"
            )
            return

        # 3. OCR 找连接按钮（文字可能是 "连 接" "连接"）
        connect_hit = None
        for h in hits:
            clean = h.text.replace(" ", "")
            if clean in ("连接", "断开"):
                connect_hit = h
                break

        if connect_hit is None:
            logger.warning("[阶段0] OCR 未找到连接按钮")
            return

        if "断" in connect_hit.text:
            # 按钮显示"断开"说明已连接
            logger.info("[阶段0] 按钮显示断开，VPN 已连接 ✓")
            await loop.run_in_executor(
                None, raw_adb._cmd, "shell", "input keyevent KEYCODE_HOME"
            )
            return

        # 4. 点击连接
        logger.info(f"[阶段0] OCR 点击连接按钮 ({connect_hit.cx},{connect_hit.cy})")
        await raw_adb.tap(connect_hit.cx, connect_hit.cy)
        await asyncio.sleep(1.5)

        # 5. 处理可能的 VPN 权限弹窗（仅首次安装时出现）
        await self._dismiss_vpn_consent()

        # 6. 回到桌面
        await loop.run_in_executor(
            None, raw_adb._cmd, "shell", "input keyevent KEYCODE_HOME"
        )

    async def _dismiss_vpn_consent(self):
        """自动处理 Android VPN 权限弹窗（仅首次安装时出现）

        弹窗来自 com.android.vpndialogs，包含"确定"按钮。
        用 OCR 找到"确定"并点击，找不到则 ENTER 键兜底。
        """
        loop = asyncio.get_event_loop()
        raw_adb = getattr(self.adb, '_adb', self.adb)

        # 检查前台是否是 VPN 权限弹窗
        output = await loop.run_in_executor(
            None, raw_adb._cmd, "shell",
            "dumpsys activity activities | grep mResumedActivity"
        )
        if "vpndialogs" not in output.lower():
            return  # 无弹窗

        logger.info("[阶段0] 检测到 VPN 权限弹窗，OCR 定位确定按钮")

        shot = await raw_adb.screenshot()
        if shot is None:
            # 截图失败，用 ENTER 键兜底
            await loop.run_in_executor(
                None, raw_adb._cmd, "shell", "input keyevent KEYCODE_ENTER"
            )
            return

        hits = self.ocr_dismisser._ocr_all(shot)
        for h in hits:
            if "确定" in h.text or h.text.upper() == "OK":
                logger.info(f"[阶段0] 点击 VPN 授权确定 ({h.cx},{h.cy})")
                await raw_adb.tap(h.cx, h.cy)
                await asyncio.sleep(1)
                return

        # OCR 没找到，ENTER 键兜底
        logger.warning("[阶段0] OCR 未找到确定按钮，ENTER 键兜底")
        await loop.run_in_executor(
            None, raw_adb._cmd, "shell", "input keyevent KEYCODE_ENTER"
        )
        await asyncio.sleep(1)

    # ================================================================
    # 阶段 1: 启动游戏
    # ================================================================

    @_timed_phase("launch_game")
    async def phase_launch_game(self) -> bool:
        """启动游戏并等待到大厅或弹窗阶段"""
        self.phase = Phase.LAUNCH_GAME

        # ── 启动前二次校验 VPN ──
        if not await self._check_vpn_connected():
            logger.warning("[阶段1] VPN 连通性校验失败，等待恢复...")
            if not await self._wait_vpn_connected(timeout=10):
                logger.error("[阶段1] VPN 未连接，拒绝启动游戏（防封号）")
                return False
            logger.info("[阶段1] VPN 已恢复 ✓")

        logger.info("[阶段1] 启动游戏")

        await self.adb.start_app(GAME_PACKAGE)

        # v2-6: P1 完成判定多源 OR (零 OCR), 12 实例并发也不爆 CPU.
        # P1 只负责"脱离加载黑屏 + 出现可交互 UI", 后续大厅/弹窗/登录页让 P2 处理.
        #
        # 任一命中即 P1 done:
        #   ① YOLO 任何 dets > 0  (close_x / action_btn 出现 = 弹窗/按钮可见)
        #   ② 模板 lobby_start_btn / lobby_start_game (大厅)
        #   ③ 模板 close_x_* 任一 (公告 / 活动 / 对话框 弹窗)
        #
        # 老 OCR 路径完全废弃 (实测 12 实例并发跑全屏 OCR 240s/分钟 CPU).
        # 用 self.yolo_dismisser 实例 (v2-9 多 session 池, 真并发)
        yolo_avail = self.yolo_dismisser.is_available()
        # close_x_* 系列模板, 公告/活动/对话框/签到 等弹窗 X 高准确率
        close_x_template_names = [
            "close_x_announce", "close_x_dialog", "close_x_activity",
            "close_x_gold", "close_x_signin", "close_x_newplay",
            "close_x_return", "close_x_white_big",
        ]
        # v2-6 登录页模板 (微信登录 / QQ登录) — 看到登录页 = 加载完成 P1 done
        login_template_names = ["lobby_login_btn", "lobby_login_btn_qq"]

        for attempt in range(60):  # 60 × 1.5s = 90s
            await asyncio.sleep(1.5)
            shot = await self.adb.screenshot()
            if shot is None:
                continue

            # YOLO 训练数据采集 (公告/活动/周年弹窗) 保留
            try:
                from .screenshot_collector import collect as _yolo_collect
                _yolo_collect(shot, tag="launch_game")
            except Exception:
                pass

            # ① 模板检测大厅 (~5ms, 现有逻辑)
            if self.matcher and self.matcher.is_at_lobby(shot):
                logger.info(f"[阶段1] R{attempt+1}: 大厅模板命中 → done")
                self.dbg.log_screenshot(shot, tag="p1_done_lobby")
                return True

            # ② 模板检测 close_x 系列 + 登录页 (~20ms)
            if self.matcher:
                for tn in close_x_template_names + login_template_names:
                    h = self.matcher.match_one(shot, tn, threshold=0.80)
                    if h:
                        logger.info(
                            f"[阶段1] R{attempt+1}: 模板 {tn}({h.confidence:.2f}) 命中 → done"
                        )
                        self.dbg.log_screenshot(shot, tag=f"p1_done_template_{tn}")
                        return True

            # ③ YOLO 推理 (~30ms, 任何 dets > 0 即认为脱离加载黑屏)
            if yolo_avail:
                try:
                    dets = self.yolo_dismisser.detect(shot)
                    if dets:
                        names = ",".join(f"{d.name}({d.conf:.2f})" for d in dets[:3])
                        logger.info(
                            f"[阶段1] R{attempt+1}: YOLO 检到 {len(dets)} 个目标 [{names}] → done"
                        )
                        self.dbg.log_screenshot(shot, tag="p1_done_yolo")
                        return True
                except Exception as _e:
                    logger.debug(f"[阶段1] YOLO 推理失败: {_e}")

            # 都没命中, log 进度 (不跑 OCR, 不识别版权文字)
            if (attempt + 1) % 5 == 0:
                logger.info(f"[阶段1] R{attempt+1}: 等待中 (无 UI 元素出现)")

        logger.warning("[阶段1] 游戏加载超时 90s, 都没识别到任何可交互 UI")
        return False

    # ================================================================
    # 阶段 2: 登录检测
    # ================================================================

    @_timed_phase("wait_login")
    async def phase_wait_login(self, timeout: int = 20) -> bool:
        """等待自动登录完成"""
        self.phase = Phase.WAIT_LOGIN
        logger.info("[阶段2] 等待自动登录")

        start = time.time()
        while time.time() - start < timeout:
            shot = await self.adb.screenshot()
            if shot is None:
                await asyncio.sleep(1)
                continue

            # YOLO 训练数据采集：登录中也可能弹"上次未登录"对话框等
            try:
                from .screenshot_collector import collect as _yolo_collect
                _yolo_collect(shot, tag="wait_login")
            except Exception:
                pass

            if self.matcher.is_at_lobby(shot):
                logger.info("[阶段2] 登录成功，已在大厅 ✓")
                return True

            await asyncio.sleep(1)

        logger.warning("[阶段2] 自动登录超时，可能需要手动登录")
        return False

    # ================================================================
    # 阶段 3: 弹窗清理
    # ================================================================

    @_timed_phase("dismiss_popups")
    async def phase_dismiss_popups(self) -> bool:
        """清理所有弹窗直到大厅（OCR驱动）"""
        self.phase = Phase.DISMISS_POPUPS
        # 优先 YOLO; ONNX 不存在时 yolo_dismisser 内部 fallback 到 OCR
        use_yolo = self.yolo_dismisser.is_available()
        logger.info(f"[阶段3] 开始弹窗清理 ({'YOLO' if use_yolo else 'OCR fallback'})")

        # 关闭守卫：这个阶段自己完整处理弹窗，避免和守卫冲突
        if hasattr(self.adb, 'guard_enabled'):
            self.adb.guard_enabled = False

        shot = await self.adb.screenshot()
        self.dbg.log_screenshot(shot, tag="popups_before")

        if use_yolo:
            result = await self.yolo_dismisser.dismiss_all(self.adb, self.matcher)
        else:
            result = await self.ocr_dismisser.dismiss_all(self.adb, self.matcher)

        shot = await self.adb.screenshot()
        self.dbg.log_screenshot(shot, tag="popups_after")

        # 恢复守卫
        if hasattr(self.adb, 'guard_enabled'):
            self.adb.guard_enabled = True

        logger.info(f"[阶段3] 结果: {result.final_state}, 关闭{result.popups_closed}个弹窗, 共{result.rounds}轮")
        return result.success

    # ================================================================
    # 阶段 6: 地图设置 (队长)
    # ================================================================

    @_timed_phase("map_setup")
    async def phase_map_setup(self) -> bool:
        """队长设置地图和模式（OCR驱动，单次扫描提取所有目标）"""
        self.phase = Phase.MAP_SETUP
        logger.info(f"[阶段6] 地图设置: {self.target_mode} - {self.target_map}")

        # 禁用守卫（地图面板的关闭按钮会被守卫误判为弹窗）
        if hasattr(self.adb, 'guard_enabled'):
            self.adb.guard_enabled = False

        ocr = OcrDismisser()

        # 构建地图模糊关键词（OCR 常把"狙击"识别为"姐击"/"阻击"等）
        map_keywords = [self.target_map]
        if "狙击" in self.target_map:
            map_keywords.extend(["击团竞大桥", "击团竞"])
        elif "经典" in self.target_map:
            map_keywords.extend(["经典团竞仓库", "经典团竞"])
        elif "军备" in self.target_map:
            map_keywords.extend(["军备团竞图书", "军备团竞"])

        # ── 步骤1: 打开地图面板（模板优先，不用OCR）──
        shot = await self.adb.screenshot()
        if shot is None:
            return False

        hit = self.matcher.match_one(shot, "lobby_start_btn", threshold=0.7)
        if hit:
            await self.adb.tap(hit.cx, hit.cy + 60)
            logger.info(f"[阶段6] 模板定位，点击模式名 ({hit.cx},{hit.cy + 60})")
        else:
            # OCR 兜底找"开始游戏"
            hits = ocr._ocr_all(shot)
            for h in hits:
                if "开始游戏" in h.text:
                    await self.adb.tap(h.cx, h.cy + 60)
                    logger.info(f"[阶段6] OCR定位，点击模式名 ({h.cx},{h.cy + 60})")
                    break
            else:
                logger.error("[阶段6] 找不到'开始游戏'按钮")
                return False

        # ── 轮询等面板打开（等文字数量稳定）──
        hits = []
        prev_count = 0
        for _ in range(15):
            await asyncio.sleep(0.4)
            shot = await self.adb.screenshot()
            if shot is None:
                continue
            hits = ocr._ocr_all(shot)
            # 文字数量 > 30 且连续两次数量接近 = 面板已完全渲染
            if len(hits) > 30 and abs(len(hits) - prev_count) < 5:
                break
            prev_count = len(hits)

        if len(hits) < 20:
            logger.warning("[阶段6] 地图面板未打开")
            return False

        logger.info(f"[阶段6] 面板已打开 ({len(hits)}个文字)")
        self.dbg.log_screenshot(shot, tag="map_panel")
        self.dbg.log_ocr(hits, roi_desc="地图面板")
        h_img, w = shot.shape[:2]

        # ── 一次性提取所有目标 ──
        # 优先用 ROI 化路径 (yaml 里配了 map_panel_left_tabs / list_center / right_btns 才走);
        # ROI 没配 → 回退到全屏 OCR + 位置百分比筛选 (老路径).
        team_battle_hit = None
        map_hit = None
        fill_hit = None
        confirm_hit = None
        all_text = " ".join(h.text for h in hits)

        # 优先级 1: 补位 / 确定 用模板匹配 (用户原话: "勾选用颜色判断+模版定位置, 确定用模版替代")
        # 模板匹配比 OCR 准: 视觉特征强的按钮 (固定金色边框/图标), 像素级精准.
        # 模板没命中再回退 OCR ROI.
        from types import SimpleNamespace
        if self.matcher is not None:
            try:
                ft = self.matcher.match_one(shot, "btn_fill_p4", threshold=0.7)
                if ft:
                    fill_hit = SimpleNamespace(cx=ft.cx, cy=ft.cy, text="补位[模板]")
                    logger.info(f"[阶段6] 补位模板命中 ({ft.cx},{ft.cy}) conf={ft.confidence:.2f}")
            except Exception:
                pass
            try:
                ct = self.matcher.match_one(shot, "btn_confirm_map", threshold=0.7)
                if ct:
                    confirm_hit = SimpleNamespace(cx=ct.cx, cy=ct.cy, text="确定[模板]")
                    logger.info(f"[阶段6] 确定模板命中 ({ct.cx},{ct.cy}) conf={ct.confidence:.2f}")
            except Exception:
                pass

        # ROI 化路径 (左侧 tab + 中间地图列表用 OCR; 补位/确定模板没命中时也用 OCR 兜底)
        roi_used = False
        try:
            from .roi_config import all_names as _roi_all_names
            avail = set(_roi_all_names())
            required = {"map_panel_left_tabs", "map_panel_list_center"}
            if required <= avail:
                roi_used = True
                left_hits = ocr._ocr_roi_named(shot, "map_panel_left_tabs")
                center_hits = ocr._ocr_roi_named(shot, "map_panel_list_center")
                # 团竞 tab 在左侧
                for h in left_hits:
                    if "团队竞技" in h.text:
                        team_battle_hit = h
                        break
                # 地图在中间
                for h in center_hits:
                    if not map_hit:
                        for kw in map_keywords:
                            if kw in h.text:
                                map_hit = h
                                break
                # 补位/确定模板没命中 → OCR ROI 兜底
                if fill_hit is None and "map_panel_btn_fill" in avail:
                    for h in ocr._ocr_roi_named(shot, "map_panel_btn_fill"):
                        if "补位" in h.text:
                            fill_hit = h
                            logger.info(f"[阶段6] 补位 OCR 兜底命中 ({h.cx},{h.cy})")
                            break
                if confirm_hit is None and "map_panel_btn_confirm" in avail:
                    for h in ocr._ocr_roi_named(shot, "map_panel_btn_confirm"):
                        if "确定" in h.text:
                            confirm_hit = h
                            logger.info(f"[阶段6] 确定 OCR 兜底命中 ({h.cx},{h.cy})")
                            break
                logger.info(
                    f"[阶段6] ROI 化路径: 左={len(left_hits)} 中={len(center_hits)} "
                    f"team_battle={'Y' if team_battle_hit else 'N'} "
                    f"map={'Y' if map_hit else 'N'} "
                    f"fill={'Y' if fill_hit else 'N'} "
                    f"confirm={'Y' if confirm_hit else 'N'}"
                )
        except Exception as _e:
            logger.debug(f"[阶段6] ROI 路径失败 ({_e}), 回退全屏 OCR")
            roi_used = False

        # 全屏 OCR + 位置筛选 (回退路径, 跟之前完全一致)
        if not roi_used:
            for h in hits:
                if "团队竞技" in h.text and h.cx < w * 0.16:
                    team_battle_hit = h
                if "确定" in h.text and h.cx > w * 0.78:
                    confirm_hit = h
                if "补位" in h.text:
                    fill_hit = h
                if not map_hit:
                    for kw in map_keywords:
                        if kw in h.text:
                            map_hit = h
                            break

        # 判断是否已在团竞模式：多种特征词任一命中（模糊匹配）
        team_battle_keywords = ["团竞手册", "团竞详情", "军备团竞", "经典团竞",
                                "击团竞", "迷你战争", "轮换团竞", "突变团竞"]
        is_team_battle = (fill_hit is not None or
                          any(OcrDismisser.fuzzy_match(all_text, kw) for kw in team_battle_keywords) or
                          map_hit is not None)  # 目标地图能找到说明已在对应分类

        # ── 判断是否需要切换模式 ──
        if not is_team_battle:
            if team_battle_hit:
                logger.info(f"[阶段6] 切换到团队竞技 ({team_battle_hit.cx},{team_battle_hit.cy})")
                await self.adb.tap(team_battle_hit.cx, team_battle_hit.cy)
                await asyncio.sleep(0.5)
                # 重新 OCR
                shot = await self.adb.screenshot()
                if shot is not None:
                    hits = ocr._ocr_all(shot)
                    map_hit = None
                    fill_hit = None
                    confirm_hit = None
                    for h in hits:
                        if "确定" in h.text and h.cx > w * 0.78:
                            confirm_hit = h
                        if "补位" in h.text:
                            fill_hit = h
                        if not map_hit:
                            for kw in map_keywords:
                                if kw in h.text:
                                    map_hit = h
                                    break
            else:
                # 重试一次 OCR（可能面板还没完全渲染）
                logger.info("[阶段6] 未找到团队竞技，重试OCR...")
                await asyncio.sleep(0.5)
                shot = await self.adb.screenshot()
                if shot is not None:
                    hits = ocr._ocr_all(shot)
                    for h in hits:
                        if "团队竞技" in h.text:
                            logger.info(f"[阶段6] 重试找到团队竞技 ({h.cx},{h.cy})")
                            await self.adb.tap(h.cx, h.cy)
                            await asyncio.sleep(0.5)
                            break
                    else:
                        logger.warning("[阶段6] 未找到团队竞技入口")
                        self._restore_guard()
                        return False
        else:
            logger.info("[阶段6] 已在团队竞技，跳过切换")

        # ── 选择地图 ──
        if map_hit:
            logger.info(f"[阶段6] 选择地图 '{map_hit.text}' ({map_hit.cx},{map_hit.cy})")
            await self.adb.tap(map_hit.cx, map_hit.cy)
            await asyncio.sleep(0.3)
        else:
            logger.warning(f"[阶段6] 未找到目标地图 '{self.target_map}'")

        # ── 检查补位（像素检测勾选状态）──
        if fill_hit:
            shot = await self.adb.screenshot()
            if shot is not None:
                check_x = max(0, fill_hit.cx - 60)
                check_y = fill_hit.cy
                y1 = max(0, check_y - 5)
                y2 = min(shot.shape[0], check_y + 5)
                x1 = max(0, check_x - 5)
                x2 = min(shot.shape[1], check_x + 5)
                region = shot[y1:y2, x1:x2]
                if region.size > 0:
                    r_ch = region[:, :, 2]
                    g_ch = region[:, :, 1]
                    b_ch = region[:, :, 0]
                    orange_count = int(((r_ch > 150) & (g_ch > 80) & (b_ch < 80)).sum())
                    if orange_count > 5:
                        logger.info("[阶段6] 补位已开启 → 点击取消")
                        await self.adb.tap(fill_hit.cx, fill_hit.cy)
                        await asyncio.sleep(0.3)
                    else:
                        logger.info("[阶段6] 补位已关闭 → 跳过")

        # ── 点击确定 ──
        if confirm_hit:
            logger.info(f"[阶段6] 点击确定 ({confirm_hit.cx},{confirm_hit.cy})")
            await self.adb.tap(confirm_hit.cx, confirm_hit.cy)
        else:
            await self._ocr_tap(ocr, ["确定"], step="确定")

        # 恢复守卫
        if hasattr(self.adb, 'guard_enabled'):
            self.adb.guard_enabled = True

        logger.info("[阶段6] 地图设置完成 ✓")
        return True

    # ================================================================
    # 阶段 4: 组队 — 队长创建
    # ================================================================

    @_timed_phase("team_create")
    async def phase_team_create(self) -> Optional[str]:
        """队长创建队伍并获取 game scheme URL（通过二维码）

        流程：点组队 → 组队码tab → 二维码组队 → 截屏解码QR →
              curl获取game scheme → 关闭面板

        Returns:
            game scheme URL (如 "pubgmhd1106467070://?tmid:xxx,rlid:xxx,...")
            队员用 am start -d <url> 一条命令直接加入，不需要任何UI操作
        """
        self.phase = Phase.TEAM_CREATE
        logger.info("[阶段4] 队长创建队伍")

        # 禁用守卫
        if hasattr(self.adb, 'guard_enabled'):
            self.adb.guard_enabled = False

        shot = await self.adb.screenshot()
        self.dbg.log_screenshot(shot, tag="team_create_start")

        ocr = OcrDismisser()

        # ── 步骤1: 找"组队"入口并点击 ──
        # 左侧栏竖排小文字，全图 OCR 经常误识别 → 裁剪左侧 10% + 放大 3 倍
        self.dbg.log_step("阶段4", "步骤1", "找组队按钮")
        clicked = False
        for attempt in range(3):
            shot = await self.adb.screenshot()
            if shot is None:
                await asyncio.sleep(0.5)
                continue
            self.dbg.log_screenshot(shot, f"attempt{attempt}")
            # ROI: team_btn_left (config/roi.yaml)
            left_hits = ocr._ocr_roi_named(shot, "team_btn_left")
            self.dbg.log_ocr(left_hits, "ROI=team_btn_left")
            for h in left_hits:
                if OcrDismisser.fuzzy_match(h.text, "组队"):
                    self.dbg.log_match("组队", h, fuzzy=True)
                    logger.info(f"[阶段4] 点击组队 ({h.cx},{h.cy})")
                    await self.adb.tap(h.cx, h.cy)
                    self.dbg.log_action("tap", h.cx, h.cy)
                    clicked = True
                    break
            if clicked:
                break
            for h in left_hits:
                if OcrDismisser.fuzzy_match(h.text, "队友"):
                    tap_y = max(h.cy - 100, 50)
                    self.dbg.log_match("队友", h, fuzzy=True)
                    logger.info(f"[阶段4] 通过'找队友'定位组队 ({h.cx},{tap_y})")
                    await self.adb.tap(h.cx, tap_y)
                    self.dbg.log_action("tap", h.cx, tap_y, "通过队友定位")
                    clicked = True
                    break
            if clicked:
                break
            await asyncio.sleep(0.5)

        if not clicked:
            self.dbg.log_fail("未找到组队按钮", left_hits if 'left_hits' in dir() else [])
            logger.warning("[阶段4] 未找到组队按钮")
            self._restore_guard()
            return None

        # ── 步骤2: 等面板出现，点底部"组队码"tab ──
        # 只扫底部和中部 ROI，不做全屏 OCR
        tab_clicked = False
        await asyncio.sleep(0.8)  # 等面板动画
        for attempt in range(5):
            shot = await self.adb.screenshot()
            if shot is None:
                await asyncio.sleep(0.3)
                continue

            # 检查中部是否有"使用组队码加入"弹窗 — team_code_popup_mid
            mid_hits = ocr._ocr_roi_named(shot, "team_code_popup_mid")
            mid_text = " ".join(h.text for h in mid_hits)
            if any(OcrDismisser.fuzzy_match(mid_text, kw) for kw in ["加入队伍", "使用组队码"]):
                for h in mid_hits:
                    if OcrDismisser.fuzzy_match(h.text, "取消"):
                        logger.info(f"[阶段4] 弹窗出现，点击取消 ({h.cx},{h.cy})")
                        await self.adb.tap(h.cx, h.cy)
                        await asyncio.sleep(0.5)
                        break
                continue

            # 找底部"组队码"tab — team_code_tab_bottom
            bottom_hits = ocr._ocr_roi_named(shot, "team_code_tab_bottom")
            for h in bottom_hits:
                if OcrDismisser.fuzzy_match(h.text, "组队码"):
                    logger.info(f"[阶段4] 点击组队码tab ({h.cx},{h.cy})")
                    await self.adb.tap(h.cx, h.cy)
                    tab_clicked = True
                    break
            if tab_clicked:
                break
            await asyncio.sleep(0.3)

        # ── 步骤3: 在组队码面板找"二维码组队"并点击 ──
        # ROI: 左侧栏 (0~25% 宽度) + 放大
        await asyncio.sleep(0.5)
        qr_clicked = False
        for attempt in range(4):
            shot = await self.adb.screenshot()
            if shot is None:
                await asyncio.sleep(0.3)
                continue
            left_hits = ocr._ocr_roi_named(shot, "qr_team_btn_left")
            for h in left_hits:
                if OcrDismisser.fuzzy_match(h.text, "二维码"):
                    logger.info(f"[阶段4] 点击二维码组队 ({h.cx},{h.cy})")
                    await self.adb.tap(h.cx, h.cy)
                    qr_clicked = True
                    break
            if qr_clicked:
                break
            await asyncio.sleep(0.3)

        if not qr_clicked:
            logger.warning("[阶段4] 未找到二维码组队入口")
            self._restore_guard()
            return None

        # ── 步骤4: 截屏解码 QR 码 ──
        await asyncio.sleep(0.5)
        qr_url = ""
        for attempt in range(5):
            shot = await self.adb.screenshot()
            if shot is None:
                await asyncio.sleep(0.5)
                continue

            # OpenCV QR 解码（放大3倍+二值化提高识别率）
            # 裁剪区从 config/roi.yaml::qr_decode_crop 读
            from .roi_config import get as _roi_get
            _x1, _y1, _x2, _y2, _qr_scale = _roi_get("qr_decode_crop")
            h, w = shot.shape[:2]
            crop = shot[int(h * _y1):int(h * _y2), int(w * _x1):int(w * _x2)]
            big = cv2.resize(crop, (0, 0), fx=_qr_scale, fy=_qr_scale, interpolation=cv2.INTER_CUBIC)
            gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
            _, thresh = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY)

            detector = cv2.QRCodeDetector()
            data, _, _ = detector.detectAndDecode(thresh)
            if data:
                qr_url = data
                logger.info(f"[阶段4] QR码解码成功: {data[:60]}...")
                break
            logger.debug(f"[阶段4] QR码解码失败，重试 {attempt+1}/5")
            await asyncio.sleep(0.5)

        if not qr_url:
            logger.error("[阶段4] 无法解码QR码")
            self._restore_guard()
            return None

        # ── 步骤5: 请求URL获取 game scheme ──
        game_scheme = ""
        try:
            import urllib.request
            loop = asyncio.get_event_loop()

            def _fetch_scheme(url: str) -> str:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (Linux; Android 7.1.2)"
                })
                with urllib.request.urlopen(req, timeout=5) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                # 提取 game scheme: pubgmhd1106467070://...
                import re
                match = re.search(r'(pubgmhd\d+://[^"\']+)', html)
                return match.group(1) if match else ""

            # QR URL 可能是 http，需要跟随重定向到 https
            game_scheme = await loop.run_in_executor(None, _fetch_scheme, qr_url)
        except Exception as e:
            logger.error(f"[阶段4] 获取 game scheme 失败: {e}")

        if not game_scheme:
            logger.error("[阶段4] 未能提取 game scheme URL")
            self._restore_guard()
            return None

        logger.info(f"[阶段4] game scheme: {game_scheme}")

        # ── 步骤6: 关闭面板 ──
        for _ in range(4):
            shot = await self.adb.screenshot()
            if shot is None:
                break
            if self.matcher.match_one(shot, "lobby_start_btn", threshold=0.7):
                break
            close = self.matcher.find_dialog_close(shot)
            if close:
                logger.info(f"[阶段4] 关闭按钮 ({close.cx},{close.cy})")
                await self.adb.tap(close.cx, close.cy)
                await asyncio.sleep(0.3)
                continue
            h, w = shot.shape[:2]
            await self.adb.tap(w * 3 // 4, h // 2)
            await asyncio.sleep(0.3)

        logger.info("[阶段4] 已关闭组队面板")
        shot = await self.adb.screenshot()
        self.dbg.log_screenshot(shot, tag="team_create_done")
        self._restore_guard()
        return game_scheme

    def _restore_guard(self):
        if hasattr(self.adb, 'guard_enabled'):
            self.adb.guard_enabled = True

    async def _ocr_tap(self, ocr: OcrDismisser, keywords: list[str],
                        template_fallback: str = "", step: str = "",
                        retries: int = 3) -> bool:
        """先模板（快~20ms），再OCR（慢~200ms），找到即点"""
        for attempt in range(retries):
            shot = await self.adb.screenshot()
            if shot is None:
                await asyncio.sleep(0.5)
                continue

            # ── 快速路径: 模板匹配 (~20ms) ──
            if template_fallback:
                tmpl_hit = self.matcher.match_one(shot, template_fallback, threshold=0.65)
                if tmpl_hit:
                    logger.info(f"[{step}] 模板匹配 '{template_fallback}' → ({tmpl_hit.cx},{tmpl_hit.cy})")
                    await self.adb.tap(tmpl_hit.cx, tmpl_hit.cy)
                    return True

            # ── 慢速路径: OCR (~200ms) ──
            hits = ocr._ocr_all(shot)
            for kw in keywords:
                for hit in hits:
                    if kw in hit.text:
                        logger.info(f"[{step}] OCR匹配 '{hit.text}' → ({hit.cx},{hit.cy})")
                        await self.adb.tap(hit.cx, hit.cy)
                        return True

            if attempt < retries - 1:
                logger.debug(f"[{step}] 第{attempt+1}次未找到，重试...")
                await asyncio.sleep(0.8)

        logger.warning(f"[{step}] {retries}次尝试均未找到目标")
        return False

    # ================================================================
    # 阶段 5: 组队 — 队员加入
    # ================================================================

    @_timed_phase("team_join")
    async def phase_team_join(self, game_scheme_url: str) -> bool:
        """队员通过 game scheme URL 直接加入队伍

        一条 ADB 命令直接加入，不需要任何 UI 操作。
        多队完全并行，每台模拟器各自收到独立的 ADB 命令，零冲突。

        Args:
            game_scheme_url: 游戏内部 scheme URL
                如 "pubgmhd1106467070://?tmid:xxx,rlid:xxx,t:xxx,p:2"
        """
        self.phase = Phase.TEAM_JOIN
        logger.info(f"[阶段5] 队员加入队伍 (scheme: {game_scheme_url[:50]}...)")

        raw_adb = getattr(self.adb, '_adb', self.adb)
        loop = asyncio.get_event_loop()

        shot = await self.adb.screenshot()
        self.dbg.log_screenshot(shot, tag="team_join_start")

        # 一条命令直接加入
        output = await loop.run_in_executor(
            None, raw_adb._cmd, "shell",
            f"am start -a android.intent.action.VIEW -d '{game_scheme_url}'"
        )
        logger.info(f"[阶段5] am start 结果: {output.strip()}")

        # 验证是否成功加入：轮询检查左上角是否出现"取消准备"
        for attempt in range(10):
            await asyncio.sleep(1)
            shot = await raw_adb.screenshot()
            if shot is None:
                continue
            # "取消准备" 按钮出现 = 成功加入队伍
            ocr = OcrDismisser()
            hits = ocr._ocr_all(shot)
            for h in hits:
                if "取消" in h.text and "准备" in h.text:
                    logger.info("[阶段5] 队员加入完成 ✓（检测到取消准备）")
                    self.dbg.log_screenshot(shot, tag="team_join_done")
                    return True
            # 模板兜底
            if self.matcher.match_one(shot, "lobby_start_btn", threshold=0.7):
                # 还在大厅，没加入
                continue

        logger.warning("[阶段5] 队员加入超时，未检测到取消准备")
        return False

    # ================================================================
    # 主运行循环
    # ================================================================

    async def run_to_lobby(self) -> bool:
        """
        执行从启动到大厅的完整流程

        Returns:
            True = 成功到达大厅
        """
        logger.info(f"=== 单实例运行开始 (角色: {self.role}) ===")

        # 阶段0: 加速器
        if not await self.phase_accelerator():
            self.phase = Phase.ERROR
            return False

        # 阶段1: 启动游戏
        if not await self.phase_launch_game():
            self.phase = Phase.ERROR
            return False

        # 阶段2+3: 登录+弹窗（合并处理）
        # 游戏启动后，可能先看到登录页再到大厅，也可能直接到弹窗
        # 用弹窗清理循环统一处理
        if not await self.phase_dismiss_popups():
            # 如果弹窗清理超时，可能是登录失败
            logger.warning("弹窗清理失败，尝试检测登录状态...")
            self.phase = Phase.ERROR
            return False

        logger.info("=== 成功到达大厅 ===")
        self.phase = Phase.LOBBY
        return True

    async def run_full(self, game_scheme_url: str = "") -> bool:
        """
        完整流程: 启动到大厅 → 组队 → 地图设置

        Args:
            game_scheme_url: 队员需要传入队长的 game scheme URL
        """
        # 先到大厅
        if not await self.run_to_lobby():
            return False

        if self.role == "captain":
            # 队长: 创建队伍(QR码) → 地图设置
            scheme = await self.phase_team_create()
            if not scheme:
                logger.error("队长创建队伍失败")
                self.phase = Phase.ERROR
                return False
            self._team_code = scheme
            await self.phase_map_setup()
            self.phase = Phase.DONE
            return True
        else:
            # 队员: game scheme 一条命令直接加入
            if not game_scheme_url:
                logger.error("队员需要 game scheme URL")
                return False
            result = await self.phase_team_join(game_scheme_url)
            self.phase = Phase.DONE
            return result


# ====================================================================
# CLI 入口
# ====================================================================

async def main():
    import argparse
    import sys
    from pathlib import Path

    parser = argparse.ArgumentParser(description="单实例自动化运行")
    parser.add_argument("--instance", type=int, default=0, help="模拟器实例编号 (0-5)")
    parser.add_argument("--adb", default="adb", help="ADB路径")
    parser.add_argument("--role", default="captain", choices=["captain", "member"])
    parser.add_argument("--mode", default="团队竞技", help="目标模式")
    parser.add_argument("--map", default="狙击团竞", help="目标地图")
    parser.add_argument("--templates", default="", help="模板目录")
    parser.add_argument("--lobby-only", action="store_true", help="只运行到大厅")
    args = parser.parse_args()

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # ADB serial
    serial = f"emulator-{5554 + args.instance * 2}"
    logger.info(f"目标设备: {serial}")

    # 模板目录
    if args.templates:
        tmpl_dir = args.templates
    else:
        # 自动查找
        project_root = Path(__file__).parent.parent.parent
        tmpl_dir = str(project_root / "fixtures" / "templates")

    # 初始化
    adb = ADBController(serial, args.adb)
    matcher = ScreenMatcher(tmpl_dir)
    n = matcher.load_all()
    if n == 0:
        logger.error(f"未找到模板文件: {tmpl_dir}")
        sys.exit(1)
    logger.info(f"已加载模板: {matcher.template_names}")

    runner = SingleInstanceRunner(
        adb=adb,
        matcher=matcher,
        role=args.role,
        target_mode=args.mode,
        target_map=args.map,
    )

    if args.lobby_only:
        success = await runner.run_to_lobby()
    else:
        success = await runner.run_full()

    logger.info(f"运行结果: {'成功' if success else '失败'}, 最终阶段: {runner.phase}")
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
