"""
实例 Agent
将状态机、Handler、ADB 控制器和识别管道串联
驱动单个模拟器实例走完整个业务流程
"""

import asyncio
import logging
import time
from typing import Callable, Optional

from .adb.controller import ADBController
from .config import AccountConfig, Settings
from .handlers.base import HandlerResult
from .handlers.login_handler import LoginHandler
from .handlers.match_handler import MatchingHandler, ReadyCheckHandler, WaitPlayersHandler
from .handlers.popup_handler import PopupHandler
from .handlers.setup_handler import SetupHandler
from .handlers.team_handler import TeamCreateHandler, TeamJoinHandler
from .handlers.verify_handler import VerifyOpponentHandler, VerifyPlayersHandler
from .models import Group, InstanceInfo, LogEntry, Role, State
from .recognition.pipeline import RecognitionPipeline
from .state_machine import GameStateMachine

logger = logging.getLogger(__name__)


class InstanceAgent:
    """
    单实例 Agent
    - 持有 ADB 控制器、识别管道、状态机
    - 每个状态对应一个 Handler
    - 协调器通过 send_command 向 Agent 发送指令
    """

    def __init__(self, account: AccountConfig, settings: Settings,
                 ctrl: ADBController, pipeline: RecognitionPipeline,
                 on_log: Optional[Callable] = None,
                 on_state_change: Optional[Callable] = None):
        """
        Args:
            account: 账号配置
            settings: 全局设置
            ctrl: ADB 控制器
            pipeline: 识别管道
            on_log: 日志回调 fn(LogEntry)
            on_state_change: 状态变化回调 fn(instance_index, old, new)
        """
        self.account = account
        self.settings = settings
        self.ctrl = ctrl
        self.pipeline = pipeline
        self._on_log = on_log
        self._on_state_change = on_state_change

        # 实例信息
        self.info = InstanceInfo(
            index=account.instance_index,
            group=Group(account.group),
            role=Role(account.role),
            qq=account.qq,
            nickname=account.nickname,
            game_id=account.game_id,
        )

        # 状态机
        self.fsm = GameStateMachine(
            instance_index=account.instance_index,
            role=Role(account.role),
            on_state_change=self._handle_state_change,
        )

        # 协调器指令队列
        self._command_queue: asyncio.Queue = asyncio.Queue()

        # 运行控制
        self._running = False
        self._paused = False

        # 协调器传递的数据
        self._join_url: str = ""                     # Member: 组队链接
        self._target_opponent_ids: list[str] = []    # 对手 ID 列表
        self._expected_player_ids: list[str] = []    # 真人玩家 ID 列表

    @property
    def state(self) -> State:
        return State(self.fsm.state)

    @property
    def index(self) -> int:
        return self.info.index

    # --- 外部接口 ---

    async def start(self):
        """启动 Agent 主循环"""
        self._running = True
        self.emit_log("Agent 启动")
        try:
            await self._run_loop()
        except asyncio.CancelledError:
            self.emit_log("Agent 被取消")
        except Exception as e:
            self.emit_log(f"Agent 异常: {e}", "error")
            raise
        finally:
            self._running = False
            self.emit_log("Agent 停止")

    async def stop(self):
        """停止 Agent"""
        self._running = False
        if self.fsm.can_trigger("force_stop"):
            self.fsm.force_stop()

    def pause(self):
        self._paused = True
        self.emit_log("Agent 暂停")

    def resume(self):
        self._paused = False
        self.emit_log("Agent 恢复")

    def send_command(self, action: str, data: Optional[dict] = None):
        """协调器发送指令"""
        self._command_queue.put_nowait({"action": action, "data": data or {}})

    def set_join_url(self, url: str):
        """设置组队链接（协调器调用）"""
        self._join_url = url

    def set_target_opponents(self, ids: list[str]):
        """设置目标对手 ID"""
        self._target_opponent_ids = ids

    def set_expected_players(self, ids: list[str]):
        """设置预期真人玩家 ID"""
        self._expected_player_ids = ids

    # --- 主循环 ---

    async def _run_loop(self):
        """Agent 主循环：根据当前状态执行对应 Handler"""
        # 触发启动
        self.fsm.start()

        while self._running:
            # 暂停检查
            while self._paused and self._running:
                await asyncio.sleep(0.5)

            if not self._running:
                break

            # 处理协调器指令
            await self._process_commands()

            # 终止状态检查
            if self.fsm.is_terminal_state():
                self.emit_log(f"到达终止状态: {self.state.value}")
                break

            # 执行当前状态的 Handler
            handler = self._get_handler()
            if handler is None:
                # 没有 Handler 的状态（如 IDLE, LOBBY），等待外部触发
                await asyncio.sleep(0.5)
                continue

            try:
                result = await handler.execute()
                await self._apply_result(result)
            except Exception as e:
                self.emit_log(f"Handler 异常: {e}", "error")
                if self.fsm.can_trigger("unknown_error"):
                    self.fsm.unknown_error()

    def _get_handler(self):
        """根据当前状态返回对应的 Handler 实例"""
        state = self.state
        common_kwargs = {
            "ctrl": self.ctrl,
            "pipeline": self.pipeline,
            "instance_index": self.index,
            "timeout": self.settings.state_timeout,
            "poll_interval": self.settings.screenshot_interval,
        }

        handlers = {
            State.LAUNCHING: lambda: self._make_launch_handler(),
            State.LOGIN_CHECK: lambda: LoginHandler(**common_kwargs),
            State.DISMISS_POPUPS: lambda: PopupHandler(**common_kwargs, max_no_popup_count=3),
            State.SETUP: lambda: SetupHandler(
                **common_kwargs,
                target_mode=self.settings.game_mode,
                target_map=self.settings.game_map,
            ),
            State.TEAM_CREATE: lambda: TeamCreateHandler(**common_kwargs),
            State.TEAM_JOIN: lambda: TeamJoinHandler(**common_kwargs, join_url=self._join_url),
            State.WAIT_PLAYERS: lambda: WaitPlayersHandler(**common_kwargs, required_player_count=2),
            State.VERIFY_PLAYERS: lambda: VerifyPlayersHandler(
                **common_kwargs, expected_player_ids=self._expected_player_ids
            ),
            State.READY_CHECK: lambda: ReadyCheckHandler(**common_kwargs),
            State.MATCHING: lambda: MatchingHandler(
                **common_kwargs, match_timeout=self.settings.match_timeout
            ),
            State.VERIFY_OPPONENT: lambda: VerifyOpponentHandler(
                **common_kwargs, target_opponent_ids=self._target_opponent_ids
            ),
        }

        factory = handlers.get(state)
        return factory() if factory else None

    def _make_launch_handler(self):
        """
        LAUNCHING 状态：启动游戏并等待加载
        不单独做 Handler 文件，逻辑简单内联
        """
        from .handlers.base import BaseHandler, HandlerResult

        agent = self

        class LaunchHandler(BaseHandler):
            async def execute(self) -> HandlerResult:
                self.log("启动游戏...")
                # 启动应用
                package = agent.settings.game_package
                activity = agent.settings.game_activity
                await self.ctrl.start_app(package, activity)

                # 等待游戏主界面加载
                async def check_game_ready(img):
                    result = await self.pipeline.detect_state(img)
                    if result.success:
                        state = result.data.get("state", "")
                        if state in ("login", "lobby"):
                            return True
                    return None

                ready = await self.wait_and_poll(check_game_ready, timeout=60)
                if ready:
                    self.log("游戏已启动")
                    return HandlerResult(trigger="game_ready")
                else:
                    self.log("游戏启动超时", "error")
                    return HandlerResult(trigger="unknown_error", error="游戏启动超时")

        return LaunchHandler(
            ctrl=self.ctrl, pipeline=self.pipeline,
            instance_index=self.index, timeout=60,
            poll_interval=self.settings.screenshot_interval,
        )

    async def _apply_result(self, result: HandlerResult):
        """将 Handler 结果应用到状态机"""
        trigger = result.trigger

        if not self.fsm.can_trigger(trigger):
            self.emit_log(
                f"无效触发: {trigger} (当前状态: {self.state.value}, "
                f"可用: {self.fsm.get_available_triggers()})", "warn"
            )
            return

        # 触发状态转换
        trigger_fn = getattr(self.fsm, trigger)
        trigger_fn()

        # 更新实例信息
        self.info.state = self.state
        self.info.state_enter_time = time.time()

        if result.error:
            self.info.error_msg = result.error

        # 特殊处理：TEAM_CREATE 完成后把 QR URL 存起来
        if trigger == "team_created" and "qr_url" in result.data:
            # 协调器会通过回调获取这个信息
            pass

    async def _process_commands(self):
        """处理协调器指令"""
        while not self._command_queue.empty():
            try:
                cmd = self._command_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            action = cmd["action"]
            data = cmd.get("data", {})

            if action == "stop":
                await self.stop()
            elif action == "pause":
                self.pause()
            elif action == "resume":
                self.resume()
            elif action == "join_team":
                self._join_url = data.get("url", "")
            elif action == "set_opponents":
                self._target_opponent_ids = data.get("ids", [])
            elif action == "disconnect":
                self.emit_log("执行断网退出")
                await self.ctrl.disconnect_network()
                await self.ctrl.force_stop_app(self.settings.game_package)
            elif action == "restart":
                # 恢复网络，重启游戏
                await self.ctrl.restore_network()
                if self.fsm.can_trigger("restart"):
                    self.fsm.restart()
            elif action == "match_now":
                # 同步匹配信号：如果在 READY_CHECK 状态，直接推进到 MATCHING
                if self.fsm.can_trigger("all_ready"):
                    self.fsm.all_ready()

    # --- 日志 ---

    def _handle_state_change(self, old_state: str, new_state: str):
        """状态变化回调"""
        self.info.state = State(new_state)
        self.info.state_enter_time = time.time()

        if self._on_state_change:
            self._on_state_change(self.index, old_state, new_state)

    def emit_log(self, message: str, level: str = "info"):
        """发送日志"""
        entry = LogEntry(
            timestamp=time.time(),
            instance_index=self.index,
            level=level,
            message=message,
            state=self.state.value,
        )
        if self._on_log:
            self._on_log(entry)
        getattr(logger, level)(f"[实例{self.index}] {message}")
