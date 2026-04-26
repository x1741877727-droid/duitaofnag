"""
MultiRunnerService — 多实例并行运行服务
管理 N 个 SingleInstanceRunner，每个独立 asyncio Task。
是 API 层和 automation 层之间的桥梁。
"""

import asyncio
import contextvars
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

# 当前运行实例标识 — asyncio task 级别隔离，WSLogHandler 用来过滤
_current_instance: contextvars.ContextVar[int] = contextvars.ContextVar('_current_instance', default=-1)

import cv2
import numpy as np

from .automation.adb_lite import ADBController
from .automation.guarded_adb import GuardedADB
from .automation.screen_matcher import ScreenMatcher
from .automation.single_runner import SingleInstanceRunner, Phase
from .automation.watchdogs import WatchState, WatchdogManager
from .config import AccountConfig, Settings

logger = logging.getLogger(__name__)

# Phase → 中文标签
PHASE_LABELS = {
    "init": "初始化",
    "accelerator": "启动加速器",
    "launch_game": "启动游戏",
    "wait_login": "等待登录",
    "dismiss_popups": "清理弹窗",
    "lobby": "大厅就绪",
    "map_setup": "设置地图",
    "team_create": "创建队伍",
    "team_join": "加入队伍",
    "done": "完成",
    "error": "出错",
}


@dataclass
class InstanceStatus:
    """单个实例运行状态"""
    index: int
    group: str
    role: str
    nickname: str
    state: str = "init"
    error: str = ""
    state_duration: float = 0.0
    _phase_start: float = field(default_factory=time.time, repr=False)
    stage_times: dict = field(default_factory=dict)  # {"accelerator": 12.3, ...}

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "group": self.group,
            "role": self.role,
            "nickname": self.nickname,
            "state": self.state,
            "error": self.error,
            "state_duration": round(time.time() - self._phase_start, 1),
            "stage_times": self.stage_times,
        }


class WSLogHandler(logging.Handler):
    """拦截 automation 日志，转为 WebSocket 消息"""

    def __init__(self, instance_index: int, callback: Callable):
        super().__init__()
        self.instance_index = instance_index
        self._callback = callback

    def emit(self, record):
        # 只处理属于本实例的日志（通过 contextvars 判断当前 task 是哪个实例）
        ctx_instance = _current_instance.get(-1)
        if ctx_instance >= 0 and ctx_instance != self.instance_index:
            return
        try:
            self._callback({
                "type": "log",
                "data": {
                    "timestamp": record.created,
                    "instance": self.instance_index,
                    "level": record.levelname.lower(),
                    "message": record.getMessage(),
                    "state": "",
                }
            })
        except Exception:
            pass


class _PhaseError(Exception):
    """阶段执行失败（可重试）"""
    def __init__(self, phase: str, reason: str):
        self.phase = phase
        self.reason = reason
        super().__init__(f"{phase}: {reason}")


class _GameCrashError(Exception):
    """游戏闪退"""
    pass


class MultiRunnerService:
    """多实例并行运行服务"""

    def __init__(self):
        self._instances: dict[int, InstanceStatus] = {}
        self._runners: dict[int, SingleInstanceRunner] = {}
        self._tasks: dict[int, asyncio.Task] = {}
        self._log_handlers: dict[int, WSLogHandler] = {}
        self._running = False
        self._starting = False  # 防止重复启动（start_all 执行期间为 True）
        self._start_time: float = 0
        self._snapshot_task: Optional[asyncio.Task] = None
        self._ws_broadcast: Optional[Callable] = None
        self._team_schemes: dict[str, str] = {}  # group → game scheme URL
        self._team_events: dict[str, asyncio.Event] = {}  # group → Event (队员等待队长)
        self._session_dir: str = ""
        self._file_handler: Optional[logging.FileHandler] = None

    def set_broadcast(self, fn: Callable):
        """设置 WebSocket 广播函数（由 api.py 注入）"""
        self._ws_broadcast = fn

    def _broadcast(self, msg: dict):
        if self._ws_broadcast:
            self._ws_broadcast(msg)

    @property
    def running(self) -> bool:
        return self._running

    def _lower_emulator_priority(self):
        """降低雷电模拟器进程优先级到 Below Normal，减轻宿主机卡顿"""
        import platform
        if platform.system() != "Windows":
            return
        try:
            import subprocess as sp
            # LDPlayer 主进程名
            for proc_name in ("LdVBoxHeadless.exe", "dnplayer.exe"):
                sp.run(
                    ["wmic", "process", "where", f"name='{proc_name}'",
                     "CALL", "setpriority", "16384"],  # 16384 = Below Normal
                    capture_output=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
                )
            logger.info("[性能] 已降低模拟器进程优先级")
        except Exception as e:
            logger.debug(f"降低进程优先级失败（非致命）: {e}")

    async def start_all(self, settings: Settings, accounts: list[AccountConfig]):
        """启动所有配置的实例"""
        if self._running or self._starting:
            logger.warning("已经在运行中或正在启动")
            return
        self._starting = True

        self._running = True
        self._start_time = time.time()
        self._instances.clear()
        self._runners.clear()
        self._tasks.clear()

        # 创建会话日志目录
        from datetime import datetime
        session_name = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._session_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "logs", session_name
        )
        os.makedirs(self._session_dir, exist_ok=True)

        # 文件日志：所有 automation 日志写入 run.log
        self._file_handler = logging.FileHandler(
            os.path.join(self._session_dir, "run.log"),
            encoding="utf-8"
        )
        self._file_handler.setLevel(logging.DEBUG)

        class _InstanceFormatter(logging.Formatter):
            """日志格式带实例号（从 contextvars 读取）"""
            def format(self, record):
                idx = _current_instance.get(-1)
                record.inst = f"#{idx}" if idx >= 0 else "SYS"
                return super().format(record)

        self._file_handler.setFormatter(_InstanceFormatter(
            "%(asctime)s [%(inst)s] %(levelname)s: %(message)s",
            datefmt="%H:%M:%S"
        ))
        logging.getLogger("backend.automation").addHandler(self._file_handler)
        logger.info(f"会话日志: {self._session_dir}")

        # 通知 debug_server 当前 session 路径（让 /api/log/tail 能找到 run.log）
        try:
            from .debug_server import set_session_dir as _set_debug_session
            _set_debug_session(self._session_dir)
        except Exception:
            pass

        # 初始化 Decision Recorder（每次决策都记录到磁盘 + 前端可视化）
        try:
            from .automation.decision_log import get_recorder
            get_recorder().init(self._session_dir)
        except Exception as _e:
            logger.warning(f"Decision Recorder 初始化失败: {_e}")

        # 结构化性能指标（Task 0.1）
        from .automation import metrics
        metrics.configure(os.path.join(self._session_dir, "metrics.jsonl"))
        metrics.start_system_sampler(interval=2.0)  # 每 2s 记 CPU/内存/线程
        # 事件循环延迟监控：async task 每 0.5s 打点，OCR 卡 loop 时能看到
        asyncio.create_task(metrics.event_loop_lag_monitor(interval=0.5, threshold_ms=50))
        logger.info(f"metrics.jsonl → {self._session_dir}/metrics.jsonl (sys sampler + loop lag 已启动)")

        # 解析 ADB 路径
        adb_path = settings.adb_path
        if not adb_path:
            adb_path = os.path.join(settings.ldplayer_path, "adb.exe")

        # 降低模拟器进程优先级（减轻系统卡顿）
        self._lower_emulator_priority()

        # 预热 OCR（所有实例共享，只初始化一次）
        from .automation.ocr_dismisser import OcrDismisser
        OcrDismisser.warmup()

        # 加载模板（所有实例共享，只读）
        template_dir = self._resolve_template_dir()
        matcher = ScreenMatcher(template_dir)
        n = matcher.load_all()
        logger.info(f"已加载 {n} 个模板: {matcher.template_names}")

        # 获取 ADB 在线设备真实端口
        import subprocess
        adb_devices = {}
        try:
            result = subprocess.run(
                [adb_path, "devices"], capture_output=True, timeout=5
            )
            for line in result.stdout.decode("utf-8", errors="replace").splitlines():
                if line.strip().startswith("emulator-") and "device" in line:
                    serial = line.split()[0]
                    adb_devices[serial] = True
        except Exception:
            pass

        self._team_schemes.clear()
        self._team_events.clear()

        # 为每个账号创建实例
        for account in accounts:
            idx = account.instance_index
            # 优先用默认端口，如果不在线则扫描可用端口
            serial = f"emulator-{5554 + idx * 2}"
            if serial not in adb_devices:
                # 扫描可能的端口（LDPlayer 重启后端口可能变化）
                for port in range(5554, 5574, 2):
                    candidate = f"emulator-{port}"
                    if candidate in adb_devices and candidate not in [
                        f"emulator-{5554 + a.instance_index * 2}" for a in accounts if a.instance_index != idx
                    ]:
                        serial = candidate
                        logger.info(f"[实例{idx}] ADB 端口映射: {5554+idx*2} → {port}")
                        break

            raw_adb = ADBController(serial, adb_path)

            # 初始化 minicap 流式截图（失败则自动回退到 screencap）
            if raw_adb.setup_minicap():
                logger.info(f"[实例{idx}] minicap 流式截图就绪 ✓")
            else:
                logger.info(f"[实例{idx}] minicap 不可用，使用 screencap 回退")

            # 用 GuardedADB 包装：任何阶段截图时自动清除意外弹窗
            from .automation.ocr_dismisser import OcrDismisser
            dismisser = OcrDismisser(max_rounds=25)
            guarded_adb = GuardedADB(raw_adb, dismisser, matcher)

            # phase 变化回调
            def make_phase_cb(instance_idx):
                def on_phase(phase: Phase):
                    self._on_phase_change(instance_idx, phase)
                return on_phase

            instance_log_dir = os.path.join(self._session_dir, f"instance_{idx}")

            runner = SingleInstanceRunner(
                adb=guarded_adb,
                matcher=matcher,
                role=account.role,
                on_phase_change=make_phase_cb(idx),
                log_dir=instance_log_dir,
            )

            self._runners[idx] = runner
            self._instances[idx] = InstanceStatus(
                index=idx,
                group=account.group,
                role=account.role,
                nickname=account.nickname,
            )

            # 为每个实例安装日志拦截器
            handler = WSLogHandler(idx, self._broadcast)
            handler.setLevel(logging.INFO)
            logging.getLogger("backend.automation").addHandler(handler)
            self._log_handlers[idx] = handler

            # 创建独立 task
            self._tasks[idx] = asyncio.create_task(
                self._run_instance(idx, runner)
            )

        # 启动状态快照定期推送
        self._snapshot_task = asyncio.create_task(self._snapshot_loop())

        self._starting = False
        logger.info(f"已启动 {len(accounts)} 个实例")
        self._broadcast({"type": "log", "data": {
            "timestamp": time.time(), "instance": -1,
            "level": "info", "message": f"启动 {len(accounts)} 个实例",
            "state": "",
        }})

    async def start_mock(self, accounts: list[AccountConfig]):
        """Mock 模式：模拟所有阶段（不需要真实设备）"""
        if self._running:
            return

        self._running = True
        self._start_time = time.time()
        self._instances.clear()
        self._tasks.clear()

        import random

        for account in accounts:
            idx = account.instance_index
            self._instances[idx] = InstanceStatus(
                index=idx, group=account.group, role=account.role,
                nickname=account.nickname,
            )
            self._tasks[idx] = asyncio.create_task(self._run_mock_instance(idx))

        self._snapshot_task = asyncio.create_task(self._snapshot_loop())

        self._broadcast({"type": "log", "data": {
            "timestamp": time.time(), "instance": -1,
            "level": "info", "message": f"[Mock] 启动 {len(accounts)} 个实例",
            "state": "",
        }})

    async def _run_mock_instance(self, idx: int):
        """模拟单个实例的阶段流转"""
        _current_instance.set(idx)
        import random
        phases = [
            ("accelerator", "启动加速器", 2),
            ("launch_game", "启动游戏", 4),
            ("dismiss_popups", "清理弹窗", 5),
            ("lobby", "大厅就绪", 0),
        ]
        try:
            for phase_key, label, base_time in phases:
                self._instances[idx].state = phase_key
                self._instances[idx]._phase_start = time.time()
                self._broadcast_state_change(idx, "prev", phase_key)
                self._broadcast({"type": "log", "data": {
                    "timestamp": time.time(), "instance": idx,
                    "level": "info", "message": f"[Mock] {label}",
                    "state": phase_key,
                }})
                if base_time > 0:
                    await asyncio.sleep(base_time + random.uniform(0, 2))

            self._instances[idx].state = "lobby"
            self._instances[idx]._phase_start = time.time()
            self._broadcast({"type": "log", "data": {
                "timestamp": time.time(), "instance": idx,
                "level": "info", "message": "[Mock] 到达大厅 ✓",
                "state": "lobby",
            }})
        except asyncio.CancelledError:
            self._instances[idx].state = "init"

    # ── 恢复点定义 ──
    # 游戏闪退后从 launch_game 恢复（加速器已连不需要重做）
    # 阶段失败只重试当前阶段
    _RECOVERY_POINT = {
        "accelerator": "accelerator",     # 加速器失败 → 重试加速器
        "launch_game": "accelerator",     # 启动失败 → 检查加速器（可能掉了）
        "dismiss_popups": "accelerator",  # 弹窗卡死 → 从加速器开始（游戏状态不可信）
        "lobby": "accelerator",           # 大厅丢失 → 从加速器开始
        "team_create": "team_create",     # 组队失败 → 重试组队（还在大厅）
        "team_join": "team_join",         # 加入失败 → 重试加入
        "map_setup": "map_setup",         # 地图失败 → 重试地图
    }
    _MAX_PHASE_RETRIES = 3       # 同阶段最多重试次数
    _MAX_GAME_RESTARTS = 5       # 最多重启游戏次数
    _GAME_PACKAGE = "com.tencent.tmgp.pubgmhd"

    async def _run_instance(self, idx: int, runner: SingleInstanceRunner):
        """单个实例运行 — 带自动恢复的状态机

        容错规则：
        - 阶段失败 → 重试当前阶段（最多 3 次）
        - 连续失败 → 升级到重启游戏（跳过加速器）
        - 游戏闪退 → 检测到后从 launch_game 恢复
        - 队员等队长 → 事件驱动无限等（不再固定 60 秒）
        - 单实例崩溃不影响其他实例
        """
        _current_instance.set(idx)
        inst = self._instances[idx]
        group = inst.group
        phase_retries = 0       # 当前阶段重试计数
        game_restarts = 0       # 游戏重启计数
        current_phase = "accelerator"  # 当前要执行的阶段

        # ── v2 横切 Watchdog: per-instance 后台任务 ──
        # 只观察 + 写状态, 不主动打断 phase (打断逻辑留到第 4 刀做)
        wd_state = WatchState(instance_idx=idx)
        wd_mgr = WatchdogManager(wd_state)
        try:
            wd_mgr.start_vpn(runner._check_vpn_connected, interval_s=5.0)

            async def _pidof_game() -> int:
                try:
                    raw_adb = getattr(runner.adb, '_adb', runner.adb)
                    loop = asyncio.get_event_loop()
                    out = await loop.run_in_executor(
                        None, raw_adb._cmd, "shell",
                        f"pidof {self._GAME_PACKAGE}",
                    )
                    out = (out or "").strip()
                    return int(out) if out.isdigit() else -1
                except Exception:
                    return -1

            wd_mgr.start_process(_pidof_game, interval_s=5.0)

            # PopupWatchdog: phase 感知 (dismiss_popups skip / team_create+map_setup
            # system_only / 其他 all). YOLO 模型不存在时安静跳过.
            try:
                from .automation.yolo_dismisser import YoloDismisser
                if YoloDismisser.is_available():
                    async def _wd_screenshot():
                        try:
                            return await runner.adb.screenshot()
                        except Exception:
                            return None

                    def _wd_yolo_detect(frame):
                        try:
                            return YoloDismisser.detect(frame)
                        except Exception:
                            return []

                    async def _wd_popup_handler(detections):
                        # 简化策略: 只 tap 第一个高置信 close_x
                        # (避免跟主流程 dismiss_popups 抢, 主流程已有更复杂逻辑)
                        target = None
                        for d in detections:
                            if getattr(d, 'cls', '') == 'close_x' and getattr(d, 'conf', 0) > 0.7:
                                target = d
                                break
                        if target is None:
                            return
                        bbox = getattr(target, 'bbox', None)
                        if not bbox or len(bbox) < 4:
                            return
                        cx = (bbox[0] + bbox[2]) // 2
                        cy = (bbox[1] + bbox[3]) // 2
                        try:
                            raw_adb = getattr(runner.adb, '_adb', runner.adb)
                            loop = asyncio.get_event_loop()
                            await loop.run_in_executor(
                                None, raw_adb._cmd, "shell",
                                f"input tap {cx} {cy}",
                            )
                            logger.info(f"[实例{idx}] PopupWatchdog 自动 tap close_x@({cx},{cy})")
                        except Exception as e:
                            logger.debug(f"[实例{idx}] PopupWatchdog tap 失败: {e}")

                    wd_mgr.start_popup(
                        _wd_screenshot, _wd_yolo_detect,
                        _wd_popup_handler, interval_s=2.0,
                    )
                    logger.info(f"[实例{idx}] watchdogs 启动: vpn + process + popup")
                else:
                    logger.info(f"[实例{idx}] watchdogs 启动: vpn + process (popup 跳过, YOLO 模型不可用)")
            except Exception as e:
                logger.warning(f"[实例{idx}] popup watchdog 启动失败 (非致命): {e}")
        except Exception as e:
            logger.warning(f"[实例{idx}] watchdog 启动失败 (非致命): {e}")

        try:
            while True:
                try:
                    # 同步当前 phase 到 watchdog state (PopupWatchdog 用)
                    wd_state.current_phase = current_phase
                    if current_phase == "accelerator":
                        ok = await runner.phase_accelerator()
                        if ok:
                            current_phase = "launch_game"
                            phase_retries = 0
                        else:
                            raise _PhaseError("accelerator", "加速器连接失败")

                    if current_phase == "launch_game":
                        ok = await runner.phase_launch_game()
                        if ok:
                            current_phase = "dismiss_popups"
                            phase_retries = 0
                        else:
                            raise _PhaseError("launch_game", "启动游戏失败")

                    if current_phase == "dismiss_popups":
                        ok = await runner.phase_dismiss_popups()
                        if ok:
                            current_phase = "team" if runner.role else "done"
                            phase_retries = 0
                            runner.phase = Phase.LOBBY
                        else:
                            raise _PhaseError("dismiss_popups", "弹窗清理超时")

                    if current_phase == "team":
                        if runner.role == "captain":
                            current_phase = "team_create"
                        else:
                            current_phase = "team_join"

                    if current_phase == "team_create":
                        scheme = await runner.phase_team_create()
                        if scheme:
                            runner._team_code = scheme
                            self._team_schemes[group] = scheme
                            # 通知等待的队员
                            evt = self._team_events.get(group)
                            if evt:
                                evt.set()
                            logger.info(f"[实例{idx}] 队长已创建队伍")
                            current_phase = "map_setup"
                            phase_retries = 0
                        else:
                            raise _PhaseError("team_create", "创建队伍失败")

                    if current_phase == "team_join":
                        # 事件驱动等待队长（不再固定超时）
                        scheme = self._team_schemes.get(group, "")
                        if not scheme:
                            inst.state = "team_join"
                            inst.error = "等待队长创建队伍..."
                            self._broadcast({"type": "log", "data": {
                                "timestamp": time.time(), "instance": idx,
                                "level": "info", "message": "等待队长创建队伍...",
                                "state": "team_join",
                            }})
                            evt = self._team_events.get(group)
                            if evt is None:
                                evt = asyncio.Event()
                                self._team_events[group] = evt
                            # 无限等，但每 10 秒检查一次（队长可能重建了队伍）
                            while not self._team_schemes.get(group, ""):
                                try:
                                    await asyncio.wait_for(evt.wait(), timeout=10)
                                except asyncio.TimeoutError:
                                    pass  # 继续等
                            scheme = self._team_schemes[group]
                            inst.error = ""

                        ok = await runner.phase_team_join(scheme)
                        if ok:
                            current_phase = "done"
                            phase_retries = 0
                        else:
                            # 加入失败 → 清空 scheme 让队长知道需要重建
                            raise _PhaseError("team_join", "加入队伍失败")

                    if current_phase == "map_setup":
                        ok = await runner.phase_map_setup()
                        if ok:
                            current_phase = "done"
                            phase_retries = 0
                        else:
                            # 地图设置失败不致命，继续
                            logger.warning(f"[实例{idx}] 地图设置失败，跳过")
                            current_phase = "done"

                    if current_phase == "done":
                        inst.state = "done"
                        elapsed = round(time.time() - inst._phase_start, 1)
                        inst.stage_times[inst.state] = elapsed
                        logger.info(f"[实例{idx}] 全部阶段完成 ✓")
                        break  # 正常退出循环

                except _PhaseError as e:
                    phase_retries += 1
                    failed_phase = e.phase
                    logger.warning(f"[实例{idx}] {e.reason} (重试 {phase_retries}/{self._MAX_PHASE_RETRIES})")
                    inst.error = f"{e.reason} (重试 {phase_retries})"

                    # 失败时保存截图供排查
                    try:
                        raw_adb = getattr(runner.adb, '_adb', runner.adb)
                        err_shot = await raw_adb.screenshot()
                        runner.dbg.log_screenshot(err_shot, tag=f"error_{failed_phase}")
                        runner.dbg.log_fail(e.reason)
                    except Exception:
                        pass

                    if phase_retries >= self._MAX_PHASE_RETRIES:
                        # 升级：重启游戏
                        game_restarts += 1
                        phase_retries = 0
                        if game_restarts > self._MAX_GAME_RESTARTS:
                            inst.state = "error"
                            inst.error = f"重启游戏 {self._MAX_GAME_RESTARTS} 次仍失败，放弃"
                            logger.error(f"[实例{idx}] 超过最大重启次数，放弃")
                            break

                        logger.warning(f"[实例{idx}] 升级恢复: 重启游戏 ({game_restarts}/{self._MAX_GAME_RESTARTS})")
                        self._broadcast({"type": "log", "data": {
                            "timestamp": time.time(), "instance": idx,
                            "level": "warn",
                            "message": f"阶段 {failed_phase} 连续失败，重启游戏 ({game_restarts})",
                            "state": failed_phase,
                        }})
                        # 强制停止游戏
                        try:
                            raw_adb = getattr(runner.adb, '_adb', runner.adb)
                            await raw_adb.stop_app(self._GAME_PACKAGE)
                            await asyncio.sleep(2)
                        except Exception:
                            pass
                        # 从 accelerator 恢复（游戏闪退会连带关闭加速器）
                        current_phase = "accelerator"
                        # 如果是队长，重启后需要重新创建队伍
                        if runner.role == "captain" and failed_phase in ("team_create", "map_setup"):
                            self._team_schemes.pop(group, None)
                            evt = self._team_events.get(group)
                            if evt:
                                evt.clear()  # 重置事件，队员会继续等
                    else:
                        # 普通重试：回到恢复点
                        recovery = self._RECOVERY_POINT.get(failed_phase, failed_phase)
                        current_phase = recovery
                        await asyncio.sleep(2)  # 给系统一点喘息时间

                except _GameCrashError:
                    # 游戏闪退 → 从 accelerator 恢复（闪退会连带关闭加速器）
                    game_restarts += 1
                    phase_retries = 0
                    if game_restarts > self._MAX_GAME_RESTARTS:
                        inst.state = "error"
                        inst.error = "游戏反复闪退，放弃"
                        break
                    logger.warning(f"[实例{idx}] 游戏闪退，从加速器开始恢复 ({game_restarts}/{self._MAX_GAME_RESTARTS})")
                    self._broadcast({"type": "log", "data": {
                        "timestamp": time.time(), "instance": idx,
                        "level": "warn", "message": f"游戏闪退，从加速器开始恢复 ({game_restarts})",
                        "state": "accelerator",
                    }})
                    current_phase = "accelerator"
                    await asyncio.sleep(2)

        except asyncio.CancelledError:
            inst.state = "init"
            logger.info(f"[实例{idx}] 已取消")
        except Exception as e:
            inst.state = "error"
            inst.error = str(e)
            logger.error(f"[实例{idx}] 运行异常: {e}", exc_info=True)
        finally:
            # 停 watchdog (它的 task 是 daemon-like, 不会自己退)
            try:
                await wd_mgr.stop_all()
            except Exception:
                pass
            inst._phase_start = time.time()
            self._broadcast_state_change(idx, "running", inst.state)
            # 写入运行摘要
            try:
                import json as _json
                summary = {
                    "instance": idx,
                    "final_state": inst.state,
                    "error": inst.error,
                    "stage_times": inst.stage_times,
                    "game_restarts": game_restarts,
                    "watchdog_stats": {
                        "vpn_fail_count": wd_state.vpn_fail_count,
                        "vpn_last_check_ts": wd_state.vpn_last_check_ts,
                        "game_running_at_end": wd_state.game_running,
                        "suspected_stall_at_end": wd_state.suspected_stall,
                        "phash_unchanged_seconds": wd_state.phash_unchanged_seconds,
                    },
                }
                summary_path = os.path.join(runner.dbg._run_dir, "summary.json")
                with open(summary_path, "w", encoding="utf-8") as f:
                    _json.dump(summary, f, ensure_ascii=False, indent=2)
            except Exception:
                pass


    def _on_phase_change(self, idx: int, phase: Phase):
        """phase 变化回调，同时记录上一阶段耗时"""
        if idx not in self._instances:
            return
        inst = self._instances[idx]
        old_state = inst.state
        new_state = phase.value
        # 记录上一阶段耗时
        if old_state != "init":
            elapsed = round(time.time() - inst._phase_start, 1)
            inst.stage_times[old_state] = elapsed
        inst.state = new_state
        inst.error = ""
        inst._phase_start = time.time()
        self._broadcast_state_change(idx, old_state, new_state)

    def _broadcast_state_change(self, idx: int, old: str, new: str):
        self._broadcast({
            "type": "state_change",
            "data": {"instance": idx, "old": old, "new": new}
        })

    async def stop_all(self):
        """停止所有实例"""
        if not self._running:
            return

        logger.info("停止所有实例...")

        # 先标记停止，防止 stop 失败后卡在 running 状态
        self._running = False

        # 唤醒所有等待中的队员（让 Event.wait 能响应 cancel）
        for evt in self._team_events.values():
            evt.set()

        # 取消快照循环
        if self._snapshot_task:
            self._snapshot_task.cancel()
            self._snapshot_task = None

        # 取消所有运行 task
        for idx, task in self._tasks.items():
            if not task.done():
                task.cancel()

        # 等待所有 task 完成
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

        # 清理日志拦截器
        auto_logger = logging.getLogger("backend.automation")
        for handler in self._log_handlers.values():
            auto_logger.removeHandler(handler)
        self._log_handlers.clear()

        # 停止所有截图流（screenrecord / 兼容旧 minicap 字段）
        for runner in self._runners.values():
            adb = getattr(runner, 'adb', None)
            raw = getattr(adb, '_adb', adb) if adb else None
            stream = getattr(raw, '_stream', None) if raw else None
            if stream:
                try:
                    stream.stop()
                except Exception:
                    pass

        # 关闭会话文件日志
        if self._file_handler:
            logging.getLogger("backend.automation").removeHandler(self._file_handler)
            self._file_handler.close()
            self._file_handler = None

        self._tasks.clear()
        self._runners.clear()
        self._team_events.clear()

        self._broadcast({"type": "log", "data": {
            "timestamp": time.time(), "instance": -1,
            "level": "info", "message": "所有实例已停止",
            "state": "",
        }})

    def get_all_status(self) -> dict:
        """返回所有实例状态（兼容前端 snapshot 格式）"""
        instances = {}
        running_count = 0
        lobby_count = 0
        error_count = 0

        for idx, status in self._instances.items():
            instances[str(idx)] = status.to_dict()
            if status.state not in ("init", "done", "error", "lobby"):
                running_count += 1
            if status.state == "lobby" or status.state == "done":
                lobby_count += 1
            if status.state == "error":
                error_count += 1

        return {
            "instances": instances,
            "stats": {
                "total_attempts": 0,
                "success_count": lobby_count,
                "abort_count": 0,
                "error_count": error_count,
                "running_duration": round(time.time() - self._start_time, 1) if self._running else 0,
            },
            "running": self._running,
        }

    # 截图缓存：{instance_index: (timestamp, jpeg_bytes)}
    _screenshot_cache: dict[int, tuple[float, bytes]] = {}
    _SCREENSHOT_CACHE_TTL = 2.0  # 秒

    async def get_screenshot(self, instance_index: int,
                             adb_path: str = "",
                             max_width: int = 0) -> Optional[bytes]:
        """获取指定实例截图，返回 JPEG bytes

        2秒内重复请求返回缓存，避免多个前端请求同时触发 screencap。
        max_width > 0 时缩小图片（缩略图用，省带宽 + 渲染更快）。
        """
        cache_key = (instance_index, max_width)

        # 检查缓存
        cached = self._screenshot_cache.get(cache_key)
        if cached:
            ts, jpg = cached
            if time.time() - ts < self._SCREENSHOT_CACHE_TTL:
                return jpg

        raw_adb = None

        # 优先用正在运行的 runner（有 minicap 流）
        runner = self._runners.get(instance_index)
        if runner:
            adb = runner.adb
            raw_adb = getattr(adb, '_adb', adb)
        elif adb_path:
            serial = f"emulator-{5554 + instance_index * 2}"
            raw_adb = ADBController(serial, adb_path)

        if raw_adb is None:
            return None

        shot = await raw_adb.screenshot()
        if shot is None:
            return None

        # 缩小（缩略图场景：1280→320，数据量减少 16 倍）
        if max_width > 0 and shot.shape[1] > max_width:
            scale = max_width / shot.shape[1]
            new_h = int(shot.shape[0] * scale)
            shot = cv2.resize(shot, (max_width, new_h), interpolation=cv2.INTER_AREA)

        _, buf = cv2.imencode(".jpg", shot, [cv2.IMWRITE_JPEG_QUALITY, 50])
        jpg = buf.tobytes()

        self._screenshot_cache[cache_key] = (time.time(), jpg)
        return jpg

    async def _snapshot_loop(self):
        """每秒推送一次全量快照"""
        try:
            while self._running:
                snapshot = self.get_all_status()
                self._broadcast({"type": "snapshot", **snapshot})
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    @staticmethod
    def _resolve_template_dir() -> str:
        """查找模板目录（兼容 PyInstaller 打包后）"""
        import sys as _sys
        if getattr(_sys, 'frozen', False):
            root = os.path.dirname(_sys.executable)
        else:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates = [
            os.path.join(root, "fixtures", "templates"),
            os.path.join(root, "_internal", "fixtures", "templates"),
            os.path.join(root, "backend", "recognition", "templates"),
        ]
        for d in candidates:
            if os.path.isdir(d):
                return d
        return candidates[0]
