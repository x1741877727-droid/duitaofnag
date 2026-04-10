"""
MultiRunnerService — 多实例并行运行服务
管理 N 个 SingleInstanceRunner，每个独立 asyncio Task。
是 API 层和 automation 层之间的桥梁。
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

import cv2
import numpy as np

from .automation.adb_lite import ADBController
from .automation.guarded_adb import GuardedADB
from .automation.screen_matcher import ScreenMatcher
from .automation.single_runner import SingleInstanceRunner, Phase
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

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "group": self.group,
            "role": self.role,
            "nickname": self.nickname,
            "state": self.state,
            "error": self.error,
            "state_duration": round(time.time() - self._phase_start, 1),
        }


class WSLogHandler(logging.Handler):
    """拦截 automation 日志，转为 WebSocket 消息"""

    def __init__(self, instance_index: int, callback: Callable):
        super().__init__()
        self.instance_index = instance_index
        self._callback = callback

    def emit(self, record):
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


class MultiRunnerService:
    """多实例并行运行服务"""

    def __init__(self):
        self._instances: dict[int, InstanceStatus] = {}
        self._runners: dict[int, SingleInstanceRunner] = {}
        self._tasks: dict[int, asyncio.Task] = {}
        self._log_handlers: dict[int, WSLogHandler] = {}
        self._running = False
        self._start_time: float = 0
        self._snapshot_task: Optional[asyncio.Task] = None
        self._ws_broadcast: Optional[Callable] = None

    def set_broadcast(self, fn: Callable):
        """设置 WebSocket 广播函数（由 api.py 注入）"""
        self._ws_broadcast = fn

    def _broadcast(self, msg: dict):
        if self._ws_broadcast:
            self._ws_broadcast(msg)

    @property
    def running(self) -> bool:
        return self._running

    async def start_all(self, settings: Settings, accounts: list[AccountConfig]):
        """启动所有配置的实例"""
        if self._running:
            logger.warning("已经在运行中")
            return

        self._running = True
        self._start_time = time.time()
        self._instances.clear()
        self._runners.clear()
        self._tasks.clear()

        # 解析 ADB 路径
        adb_path = settings.adb_path
        if not adb_path:
            adb_path = os.path.join(settings.ldplayer_path, "adb.exe")

        # 加载模板（所有实例共享，只读）
        template_dir = self._resolve_template_dir()
        matcher = ScreenMatcher(template_dir)
        n = matcher.load_all()
        logger.info(f"已加载 {n} 个模板: {matcher.template_names}")

        # 为每个账号创建实例
        for account in accounts:
            idx = account.instance_index
            serial = f"emulator-{5554 + idx * 2}"

            raw_adb = ADBController(serial, adb_path)

            # 用 GuardedADB 包装：任何阶段截图时自动清除意外弹窗
            from .automation.ocr_dismisser import OcrDismisser
            dismisser = OcrDismisser(max_rounds=25)
            guarded_adb = GuardedADB(raw_adb, dismisser, matcher)

            # phase 变化回调
            def make_phase_cb(instance_idx):
                def on_phase(phase: Phase):
                    self._on_phase_change(instance_idx, phase)
                return on_phase

            runner = SingleInstanceRunner(
                adb=guarded_adb,
                matcher=matcher,
                role=account.role,
                on_phase_change=make_phase_cb(idx),
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

        logger.info(f"已启动 {len(accounts)} 个实例")
        self._broadcast({"type": "log", "data": {
            "timestamp": time.time(), "instance": -1,
            "level": "info", "message": f"启动 {len(accounts)} 个实例",
            "state": "",
        }})

    async def _run_instance(self, idx: int, runner: SingleInstanceRunner):
        """单个实例运行（在独立 task 中）"""
        try:
            ok = await runner.run_to_lobby()
            if ok:
                self._instances[idx].state = "lobby"
            else:
                self._instances[idx].state = "error"
                self._instances[idx].error = "未能到达大厅"
        except asyncio.CancelledError:
            self._instances[idx].state = "init"
            logger.info(f"[实例{idx}] 已取消")
        except Exception as e:
            self._instances[idx].state = "error"
            self._instances[idx].error = str(e)
            logger.error(f"[实例{idx}] 运行异常: {e}")
        finally:
            self._instances[idx]._phase_start = time.time()
            self._broadcast_state_change(idx, "running", self._instances[idx].state)

    def _on_phase_change(self, idx: int, phase: Phase):
        """phase 变化回调"""
        if idx not in self._instances:
            return
        old_state = self._instances[idx].state
        new_state = phase.value
        self._instances[idx].state = new_state
        self._instances[idx].error = ""
        self._instances[idx]._phase_start = time.time()
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

        self._running = False
        self._tasks.clear()
        self._runners.clear()

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

    async def get_screenshot(self, instance_index: int) -> Optional[bytes]:
        """获取指定实例截图（纯观察，不触发守卫），返回 JPEG bytes"""
        runner = self._runners.get(instance_index)
        if not runner:
            return None

        # 用底层 ADB 截图，避免触发 GuardedADB 的弹窗清除
        adb = runner.adb
        raw_adb = getattr(adb, '_adb', adb)  # 如果是 GuardedADB 取底层
        shot = await raw_adb.screenshot()
        if shot is None:
            return None

        _, buf = cv2.imencode(".jpg", shot, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes()

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
        """查找模板目录"""
        # 从项目根目录查找
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates = [
            os.path.join(root, "fixtures", "templates"),
            os.path.join(root, "backend", "recognition", "templates"),
        ]
        for d in candidates:
            if os.path.isdir(d):
                return d
        return candidates[0]  # 返回第一个作为默认值
