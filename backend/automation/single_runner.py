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

        # phase 中间步骤日志 (每个 phase 跑前清空, 跑完 P4Handler 等读出来塞 decision.note)
        self._stage_log: list[str] = []

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
            try:
                from .screenshot_collector import collect as _yolo_collect
                _yolo_collect(shot, tag="P0-vpn_connected")
            except Exception:
                pass
            return True

        # 先尝试广播启动（快速路径）
        await self._start_vpn()
        if await self._wait_vpn_connected(timeout=8):
            logger.info("[阶段0] FightMaster 广播启动成功 ✓")
            shot = await self.adb.screenshot()
            self.dbg.log_screenshot(shot, tag="vpn_connected")
            try:
                from .screenshot_collector import collect as _yolo_collect
                _yolo_collect(shot, tag="P0-vpn_connected")
            except Exception:
                pass
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
        try:
            from .screenshot_collector import collect as _yolo_collect
            _yolo_collect(shot, tag="P0-vpn_failed")
        except Exception:
            pass
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
        """队长设置地图和模式. 拆 5 条独立子 decision (每条有图+tier+tap), 像 P2 清弹窗一样档案能逐条点开看."""
        import time as _t
        from .decision_log import get_recorder, TierRecord, TemplateMatch, OcrHit as _OcrHit

        self._stage_log = []
        _t0 = _t.perf_counter()
        recorder = get_recorder()
        inst_idx = getattr(self._v3_ctx, "instance_idx", 0) if self._v3_ctx else 0

        def _slog(msg: str):
            logger.info(msg)
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
        for _retry in range(len(_P4_RETRY_SLEEPS) + 1):  # 4 次
            p4_1_attempts = _retry + 1
            # 模板 1: lobby_start_game (用户裁的)
            hit = self.matcher.match_one(shot, "lobby_start_game", threshold=0.7)
            tmpl_used = "lobby_start_game"
            # 模板 2: lobby_start_btn (旧)
            if hit is None:
                hit = self.matcher.match_one(shot, "lobby_start_btn", threshold=0.7)
                tmpl_used = "lobby_start_btn"
            if hit is not None:
                break
            # 模板都 miss → OCR 兜底
            ocr_hits = ocr._ocr_all(shot)
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

        if hit:
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
            list_hits = (ocr._ocr_roi_named(shot, "map_panel_list_center")
                         if has_list_roi else ocr._ocr_all(shot)) if shot is not None else []
            _slog(f"[P4-2 PERF] attempt {p4_2_attempts}: OCR list_center "
                  f"{(_tm.perf_counter()-_pt)*1000:.0f}ms ({len(list_hits)} 文字)")

            _pt = _tm.perf_counter()
            if has_left_roi:
                left_hits = ocr._ocr_roi_named(shot, "map_panel_left_tabs") if shot is not None else []
            else:
                full = ocr._ocr_all(shot) if shot is not None else []
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
                await asyncio.sleep(0.5)
                _slog(f"[阶段6] 切团竞 tap ({team_battle_hit.cx},{team_battle_hit.cy})")
                if d2: d2.finalize(outcome="mode_switched", note="切到团竞")
                # 重新拿 list_hits + shot
                shot_after = await self.adb.screenshot()
                if shot_after is not None:
                    shot = shot_after
                    list_hits = (ocr._ocr_roi_named(shot, "map_panel_list_center")
                                 if has_list_roi else ocr._ocr_all(shot))
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
            orange = int(((r_ch > 150) & (g_ch > 80) & (b_ch < 80)).sum())
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
                list_hits = (ocr._ocr_roi_named(shot, "map_panel_list_center")
                             if has_list_roi else ocr._ocr_all(shot)) if shot is not None else []
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
                f"({ratio*100:.1f}%) 阈值=10% (尝试 {p4_4_attempts} 次)"
            )
            if d4:
                tier_p4 = TierRecord(
                    tier=4, name="ROI颜色·fill_checkbox",
                    early_exit=True, note=tier_note, ocr_roi=bbox,
                )
                d4.add_tier(tier_p4)
                d4.save_ocr_roi(tier_p4, shot, roi=bbox, hits=[])
            if ratio > 0.10:
                if d4:
                    d4.set_tap(fill_state["tap_cx"], fill_state["tap_cy"],
                               method="ROI颜色", target_class="补位勾选框", screenshot=shot)
                await self.adb.tap(fill_state["tap_cx"], fill_state["tap_cy"])
                await asyncio.sleep(0.3)
                _slog(f"[阶段6] 补位已勾 ({ratio*100:.1f}%) → tap 取消")
                if d4: d4.finalize(outcome="fill_unchecked",
                                    note=f"补位已勾 → tap 取消 (橙色 {ratio*100:.1f}%)")
            else:
                _slog(f"[阶段6] 补位未勾 ({ratio*100:.1f}% < 10%)")
                if d4: d4.finalize(outcome="skipped",
                                    note=f"补位未勾 (橙色 {ratio*100:.1f}% < 10%)")
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
        for _retry in range(len(_P4_RETRY_SLEEPS) + 1):  # 4 次
            p4_5_attempts = _retry + 1
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
                confirm_hits_last = ocr._ocr_roi_named(shot, "map_panel_btn_confirm") if shot is not None else []
                _btn_roi_px_last = _roi_pixels("map_panel_btn_confirm", shot)
                ocr_tier_name = "OCR·btn_confirm"
                ocr_tier_note = f"模板没命中 → OCR ROI 找'确定' ({len(confirm_hits_last)} 文字)"
            else:
                confirm_hits_last = ocr._ocr_all(shot) if shot is not None else []
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

        if confirm_tmpl_fresh:
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
            logger.info(f"[P3a-1 PERF] attempt {attempt+1}: screenshot {(_tm.perf_counter()-_pt_shot)*1000:.0f}ms")
            if shot is None:
                await asyncio.sleep(0.5)
                continue
            self.dbg.log_screenshot(shot, f"attempt{attempt}")
            _pt_ocr = _tm.perf_counter()
            left_hits = ocr._ocr_roi_named(shot, "team_btn_left")
            logger.info(f"[P3a-1 PERF] attempt {attempt+1}: OCR team_btn_left {(_tm.perf_counter()-_pt_ocr)*1000:.0f}ms ({len(left_hits)} 文字)")
            last_left_hits = left_hits
            self.dbg.log_ocr(left_hits, "ROI=team_btn_left")
            # 主匹配: 文字含 "组" 或 "队" 都算 (用户要求放宽, OCR 经常把"组队" → "如WB"
            # 之类乱识别, 但只要有一个字命中就 tap, 因为 ROI 已限定在左侧栏组队按钮区域).
            # 排除"队友"避免错点找队友按钮 (两者位置不同).
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
        logger.info(f"[P3a-1 PERF] 总 (找组队按钮): {(_tm.perf_counter()-_p3a_t0)*1000:.0f}ms, 尝试 {attempt+1} 次")

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
            logger.info(f"[P3a-1 PERF] add_tier+save_ocr_roi: {(_tm.perf_counter()-_pt_save)*1000:.0f}ms")
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
        logger.info(f"[P3a-2 PERF] 等面板动画 sleep: {(_tm.perf_counter()-_pt_sleep)*1000:.0f}ms")
        _pt_shot = _tm.perf_counter()
        shot_d2 = await self.adb.screenshot()
        logger.info(f"[P3a-2 PERF] 入口 screenshot: {(_tm.perf_counter()-_pt_shot)*1000:.0f}ms")
        d2 = _make_d("2-tab", shot_in=shot_d2)
        _p3a2_t0 = _tm.perf_counter()
        tab_clicked = False
        last_bottom_hits: list = []
        for attempt in range(5):
            _pt_shot = _tm.perf_counter()
            shot = await self.adb.screenshot() if attempt > 0 else shot_d2
            logger.info(f"[P3a-2 PERF] attempt {attempt+1}: screenshot {(_tm.perf_counter()-_pt_shot)*1000:.0f}ms")
            if shot is None:
                await asyncio.sleep(0.3)
                continue

            # 直接 OCR 底部"组队码" tab. 之前的中部"使用组队码加入"弹窗检测删了
            # (实测从来不命中, 311K 像素的 OCR 每次浪费 ~2-3s).
            # 如果某天真撞到那个弹窗, 这 5 次 retry 都会找不到 tab → P3a-2 fail.
            _pt_ocr = _tm.perf_counter()
            bottom_hits = ocr._ocr_roi_named(shot, "team_code_tab_bottom")
            logger.info(f"[P3a-2 PERF] attempt {attempt+1}: OCR team_code_tab_bottom {(_tm.perf_counter()-_pt_ocr)*1000:.0f}ms ({len(bottom_hits)} 文字)")
            last_bottom_hits = bottom_hits
            for h in bottom_hits:
                if OcrDismisser.fuzzy_match(h.text, "组队码"):
                    if d2:
                        d2.set_tap(int(h.cx), int(h.cy), method="OCR",
                                   target_class="组队码 tab", target_text=h.text,
                                   screenshot=shot)
                    await self.adb.tap(h.cx, h.cy)
                    logger.info(f"[阶段4] 点击组队码tab ({h.cx},{h.cy})")
                    tab_clicked = True
                    break
            if tab_clicked:
                break
            await asyncio.sleep(0.3)
        logger.info(f"[P3a-2 PERF] 总 (loop): {(_tm.perf_counter()-_p3a2_t0)*1000:.0f}ms")

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
            logger.info(f"[P3a-2 PERF] add_tier+save_ocr_roi: {(_tm.perf_counter()-_pt_save)*1000:.0f}ms")
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
            logger.info(f"[P3a-3 PERF] attempt {attempt+1}: screenshot {(_tm.perf_counter()-_pt_shot)*1000:.0f}ms")
            if shot is None:
                await asyncio.sleep(0.3)
                continue
            _pt_ocr = _tm.perf_counter()
            left_hits = ocr._ocr_roi_named(shot, "qr_team_btn_left")
            logger.info(f"[P3a-3 PERF] attempt {attempt+1}: OCR qr_team_btn_left {(_tm.perf_counter()-_pt_ocr)*1000:.0f}ms ({len(left_hits)} 文字)")
            last_qr_hits = left_hits
            for h in left_hits:
                if OcrDismisser.fuzzy_match(h.text, "二维码"):
                    if d3:
                        d3.set_tap(int(h.cx), int(h.cy), method="OCR",
                                   target_class="二维码组队", target_text=h.text,
                                   screenshot=shot)
                    await self.adb.tap(h.cx, h.cy)
                    logger.info(f"[阶段4] 点击二维码组队 ({h.cx},{h.cy})")
                    qr_clicked = True
                    break
            if qr_clicked:
                break
            await asyncio.sleep(0.3)
        logger.info(f"[P3a-3 PERF] 总 (找二维码组队): {(_tm.perf_counter()-_p3a3_t0)*1000:.0f}ms")

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
            logger.info(f"[P3a-3 PERF] add_tier+save_ocr_roi: {(_tm.perf_counter()-_pt_save)*1000:.0f}ms")
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

            # ② YOLO 兜底找 close_x
            yolo_hit = None
            try:
                from .yolo_detector import detect_buttons, is_available as _yolo_avail
                if _yolo_avail():
                    dets = detect_buttons(shot, names=["close_x"], conf_thr=0.4)
                    if dets:
                        yolo_hit = dets[0]
            except Exception:
                yolo_hit = None
            if yolo_hit is not None:
                ycx, ycy = yolo_hit.center_px
                last_method = f"YOLO·close_x ({yolo_hit.score:.2f})"
                logger.info(f"[阶段4] 关闭(YOLO close_x) ({ycx},{ycy}) score={yolo_hit.score:.2f}")
                if d5:
                    d5.set_tap(int(ycx), int(ycy), method="YOLO",
                               target_class="close_x", target_conf=float(yolo_hit.score),
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
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        d2 = _make_d("2-verify")
        ocr = OcrDismisser()
        joined = False
        last_hits: list = []
        last_shot = None
        attempt_idx = 0
        for attempt in range(10):
            attempt_idx = attempt + 1
            await asyncio.sleep(1)
            shot = await raw_adb.screenshot()
            if shot is None:
                continue
            last_shot = shot
            hits = ocr._ocr_all(shot)
            last_hits = hits
            for h in hits:
                if "取消" in h.text and "准备" in h.text:
                    logger.info("[阶段5] 队员加入完成 ✓（检测到取消准备）")
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
                tier=3, name="OCR·全屏(取消准备)", early_exit=joined,
                note=f"轮询 {attempt_idx} 次, 加入={joined}",
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
