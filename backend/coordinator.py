"""
协调器
管理 2 组 × 3 实例的全局调度和同步
核心职责：二维码传递、匹配同步、校验同步、断网退出、异常处理
"""

import asyncio
import logging
import os
import tempfile
import time
from typing import Callable, Optional

from .adb.controller import ADBController
from .adb.ldplayer import LDPlayerManager
from .config import ConfigManager
from .instance_agent import InstanceAgent
from .models import (
    CoordinatorCommand, Group, LogEntry, MatchAttempt,
    Role, SessionStats, State, TeamInfo,
)
from .recognition.cache import LLMCache
from .recognition.llm_vision import LLMVision
from .recognition.ocr_reader import OCRReader
from .recognition.pipeline import RecognitionPipeline
from .recognition.template_matcher import TemplateMatcher

logger = logging.getLogger(__name__)


class Coordinator:
    """
    全局协调器
    - 管理 Group A (实例 0-2) 和 Group B (实例 3-5) 的生命周期
    - 二维码传递: Captain 截图→解码→分发给 Member
    - 匹配同步: 两组 Captain ±1s 内同时匹配
    - 校验同步: 等待双方校验结果→统一 SUCCESS/ABORT
    - 断网退出: 全部断网 + force-stop
    - 异常广播: 单实例异常→全局暂停/调整
    """

    def __init__(self, config: ConfigManager,
                 on_log: Optional[Callable] = None,
                 on_state_change: Optional[Callable] = None,
                 on_stats_update: Optional[Callable] = None):
        self.config = config
        self._on_log = on_log
        self._on_state_change = on_state_change
        self._on_stats_update = on_stats_update

        self.settings = config.settings
        self.mock = config.settings.dev_mock

        # 实例管理
        self.ldm: Optional[LDPlayerManager] = None
        self.agents: dict[int, InstanceAgent] = {}
        self.controllers: dict[int, ADBController] = {}
        self._agent_tasks: dict[int, asyncio.Task] = {}

        # 组队信息
        self.teams: dict[str, TeamInfo] = {}

        # 会话统计
        self.stats = SessionStats()

        # 运行控制
        self._running = False
        self._paused = False

        # 同步事件
        self._match_barrier: Optional[asyncio.Barrier] = None
        self._verify_results: dict[str, Optional[str]] = {}  # group -> "correct"/"wrong"/None

    # =====================
    # 生命周期
    # =====================

    async def initialize(self):
        """初始化：创建 LDPlayer 管理器、ADB 控制器、识别管道、Agent"""
        self.emit_log("初始化协调器...")

        # LDPlayer 管理器
        self.ldm = LDPlayerManager(
            ldplayer_path=self.settings.ldplayer_path,
            mock=self.mock,
        )

        # 识别组件（所有实例共享）
        templates_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "backend", "recognition", "templates",
        )
        matcher = TemplateMatcher(templates_dir, tuple(self.settings.normalize_resolution))
        matcher.load_templates()

        ocr = OCRReader(mock=self.mock)
        llm = LLMVision(
            api_url=self.settings.llm_api_url,
            api_key=self.settings.llm_api_key,
            mock=self.mock,
        )
        cache = LLMCache(ttl=1800)

        # 为每个账号创建 Agent
        for account in self.config.accounts:
            idx = account.instance_index

            # 确保模拟器实例运行
            await self.ldm.ensure_running(idx)

            # ADB 控制器
            serial = await self.ldm.get_adb_serial(idx)
            ctrl = ADBController(
                serial=serial,
                instance_index=idx,
                mock=self.mock,
                mock_screenshots_dir=self.settings.mock_screenshots_dir,
            )
            await ctrl.connect()
            self.controllers[idx] = ctrl

            # 识别管道（每实例独立管道，共享底层组件）
            pipeline = RecognitionPipeline(matcher, ocr, llm, cache)

            # Agent
            agent = InstanceAgent(
                account=account,
                settings=self.settings,
                ctrl=ctrl,
                pipeline=pipeline,
                on_log=self._forward_log,
                on_state_change=self._handle_agent_state_change,
            )
            self.agents[idx] = agent

        # 初始化组队信息
        for group_name in ("A", "B"):
            accounts = self.config.get_group_accounts(group_name)
            captain = self.config.get_captain(group_name)
            if captain:
                self.teams[group_name] = TeamInfo(
                    group=Group(group_name),
                    captain_index=captain.instance_index,
                    member_indices=[
                        a.instance_index for a in accounts if a.role != "captain"
                    ],
                )

        self.emit_log(f"初始化完成: {len(self.agents)} 个 Agent, {len(self.teams)} 个队伍")

    async def start(self):
        """启动所有 Agent 并开始协调循环"""
        self._running = True
        self.stats = SessionStats(start_time=time.time())
        self.emit_log("开始运行")

        # 启动所有 Agent
        for idx, agent in self.agents.items():
            task = asyncio.create_task(agent.start(), name=f"agent-{idx}")
            self._agent_tasks[idx] = task

        # 协调循环
        try:
            await self._coordination_loop()
        except asyncio.CancelledError:
            self.emit_log("协调器被取消")
        except Exception as e:
            self.emit_log(f"协调器异常: {e}", "error")
            raise
        finally:
            await self._cleanup()

    async def stop(self):
        """停止所有 Agent"""
        self._running = False
        self.emit_log("停止中...")

        for agent in self.agents.values():
            await agent.stop()

        # 等待所有 task 结束
        if self._agent_tasks:
            await asyncio.gather(*self._agent_tasks.values(), return_exceptions=True)

    def pause(self):
        self._paused = True
        for agent in self.agents.values():
            agent.pause()
        self.emit_log("已暂停")

    def resume(self):
        self._paused = False
        for agent in self.agents.values():
            agent.resume()
        self.emit_log("已恢复")

    # =====================
    # 协调循环
    # =====================

    async def _coordination_loop(self):
        """
        主协调循环
        监控所有 Agent 状态，在关键同步点做协调
        """
        while self._running:
            if self._paused:
                await asyncio.sleep(0.5)
                continue

            # 检查是否需要同步操作
            await self._check_qr_code_relay()
            await self._check_match_sync()
            await self._check_verify_sync()
            await self._check_errors()

            # 更新统计
            if self._on_stats_update:
                self._on_stats_update(self._get_stats_dict())

            await asyncio.sleep(0.5)

    # =====================
    # 二维码传递
    # =====================

    async def _check_qr_code_relay(self):
        """
        检查 Captain 是否已创建队伍
        如果 Captain 进入 WAIT_PLAYERS 且 Member 还在 TEAM_JOIN → 传递 QR URL
        """
        for group_name, team in self.teams.items():
            captain = self.agents.get(team.captain_index)
            if captain is None:
                continue

            # Captain 在 TEAM_CREATE 完成后会到 WAIT_PLAYERS
            # 但我们需要在 team_created 回调中拿到 QR URL
            # 这里简化：检查 Captain 是否已经到了 WAIT_PLAYERS
            if captain.state != State.WAIT_PLAYERS:
                continue

            # 检查是否已传递过 URL
            if team.qr_code_url:
                continue

            # 尝试获取 QR URL（从 Captain 最近的 handler result）
            # 实际上 QR URL 需要通过 Agent 回调传递
            # 这里用一个简化方案：如果 Captain 到了 WAIT_PLAYERS，
            # 说明 team_created handler 已完成，URL 在 handler result.data 中
            # TODO: 更优雅的回调机制
            # 暂时：Captain 截图→解码→分发
            if not team.qr_code_url:
                url = await self._extract_qr_from_captain(team.captain_index)
                if url:
                    team.qr_code_url = url
                    # 分发给所有 Member
                    for member_idx in team.member_indices:
                        member = self.agents.get(member_idx)
                        if member and member.state == State.TEAM_JOIN:
                            member.set_join_url(url)
                            member.send_command("join_team", {"url": url})
                            self.emit_log(f"[{group_name}组] 组队链接已发送给实例{member_idx}")

    async def _extract_qr_from_captain(self, captain_index: int) -> Optional[str]:
        """从 Captain 截图中提取 QR 码 URL"""
        ctrl = self.controllers.get(captain_index)
        if ctrl is None:
            return None

        img = await ctrl.screenshot()
        if img is None:
            return None

        try:
            from pyzbar.pyzbar import decode as decode_qr
            import cv2
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            decoded = decode_qr(gray)
            for obj in decoded:
                data = obj.data.decode("utf-8", errors="ignore")
                if data:
                    return data
        except ImportError:
            pass

        # mock 模式下返回模拟 URL
        if self.mock:
            return f"https://game.example.com/team/join?code=mock_{captain_index}"

        return None

    # =====================
    # 匹配同步
    # =====================

    async def _check_match_sync(self):
        """
        两组 Captain 都到 READY_CHECK 时，同步发起匹配
        """
        captains_ready = True
        for team in self.teams.values():
            captain = self.agents.get(team.captain_index)
            if captain is None or captain.state != State.READY_CHECK:
                captains_ready = False
                break

        if not captains_ready:
            return

        # 两组 Captain 都在 READY_CHECK → 同步匹配
        self.emit_log("两组队伍就绪，同步发起匹配！")

        # 同时向两个 Captain 发送 match_now 信号
        tasks = []
        for team in self.teams.values():
            captain = self.agents.get(team.captain_index)
            if captain:
                captain.send_command("match_now")
                tasks.append(team.captain_index)

        self.emit_log(f"匹配信号已发送给 Captain: {tasks}")

        # 记录匹配尝试
        self.stats.total_attempts += 1

        # 等待一小段时间避免重复触发
        await asyncio.sleep(2)

    # =====================
    # 校验同步
    # =====================

    async def _check_verify_sync(self):
        """
        两组都完成对手校验后，统一决定 SUCCESS 或 ABORT
        """
        all_at_verify = True
        results = {}

        for group_name, team in self.teams.items():
            captain = self.agents.get(team.captain_index)
            if captain is None:
                all_at_verify = False
                break

            if captain.state == State.SUCCESS:
                results[group_name] = "correct"
            elif captain.state == State.ABORT:
                results[group_name] = "wrong"
            elif captain.state == State.VERIFY_OPPONENT:
                all_at_verify = False  # 还在校验中
                break
            else:
                all_at_verify = False
                break

        if not all_at_verify or len(results) != len(self.teams):
            return

        # 两组都有结果了
        if all(r == "correct" for r in results.values()):
            # 全部匹配成功 → 断网退出
            self.emit_log("对手匹配成功！全部断网退出")
            await self._disconnect_all()

            attempt = MatchAttempt(
                attempt_number=self.stats.total_attempts,
                timestamp=time.time(),
                group_a_matched=True,
                group_b_matched=True,
                opponent_is_target=True,
            )
            self.stats.record_attempt(attempt)

        else:
            # 至少一组不匹配 → 全部中止重来
            wrong_groups = [g for g, r in results.items() if r == "wrong"]
            self.emit_log(f"对手不匹配 ({wrong_groups})，全部中止重来")
            await self._abort_and_restart()

            attempt = MatchAttempt(
                attempt_number=self.stats.total_attempts,
                timestamp=time.time(),
                opponent_is_target=False,
                abort_reason=f"对手不匹配: {wrong_groups}",
            )
            self.stats.record_attempt(attempt)

    # =====================
    # 断网退出 / 中止重来
    # =====================

    async def _disconnect_all(self):
        """全部实例断网退出"""
        tasks = []
        for idx, agent in self.agents.items():
            agent.send_command("disconnect")
            tasks.append(idx)
        self.emit_log(f"断网指令已发送: {tasks}")

    async def _abort_and_restart(self):
        """全部实例中止并准备重来"""
        # 先断网退出当前对局
        for agent in self.agents.values():
            agent.send_command("disconnect")

        await asyncio.sleep(2)

        # 恢复网络并重启
        for agent in self.agents.values():
            agent.send_command("restart")

        # 清理组队信息
        for team in self.teams.values():
            team.qr_code_url = ""
            team.team_ready = False

        self.emit_log("全部实例已重启，准备下一轮")

    # =====================
    # 异常处理
    # =====================

    async def _check_errors(self):
        """检查 Agent 异常状态"""
        for idx, agent in self.agents.items():
            if agent.state == State.ERROR_BANNED:
                self.emit_log(f"实例 {idx} 被禁赛！暂停全部", "error")
                self.pause()
                return

            if agent.state == State.ERROR_NETWORK:
                self.emit_log(f"实例 {idx} 网络错误，等待恢复", "warn")

            if agent.state == State.ERROR_UNKNOWN:
                self.emit_log(f"实例 {idx} 未知错误: {agent.info.error_msg}", "warn")

    def _handle_agent_state_change(self, instance_index: int, old: str, new: str):
        """Agent 状态变化回调"""
        if self._on_state_change:
            self._on_state_change(instance_index, old, new)

    # =====================
    # 清理
    # =====================

    async def _cleanup(self):
        """清理资源"""
        # 取消所有 Agent task
        for task in self._agent_tasks.values():
            if not task.done():
                task.cancel()

        if self._agent_tasks:
            await asyncio.gather(*self._agent_tasks.values(), return_exceptions=True)

        # 断开 ADB
        for ctrl in self.controllers.values():
            await ctrl.disconnect()

        self._running = False
        self.emit_log("清理完成")

    # =====================
    # 状态查询
    # =====================

    def get_all_states(self) -> dict[int, dict]:
        """获取所有实例的状态"""
        result = {}
        for idx, agent in self.agents.items():
            result[idx] = {
                "index": idx,
                "group": agent.info.group.value,
                "role": agent.info.role.value,
                "state": agent.state.value,
                "nickname": agent.info.nickname,
                "error": agent.info.error_msg,
                "state_duration": round(agent.info.state_duration(), 1),
            }
        return result

    def _get_stats_dict(self) -> dict:
        return {
            "total_attempts": self.stats.total_attempts,
            "success_count": self.stats.success_count,
            "abort_count": self.stats.abort_count,
            "error_count": self.stats.error_count,
            "running_duration": round(self.stats.running_duration, 0),
        }

    # =====================
    # 日志
    # =====================

    def _forward_log(self, entry: LogEntry):
        """转发 Agent 日志"""
        if self._on_log:
            self._on_log(entry)

    def emit_log(self, message: str, level: str = "info"):
        entry = LogEntry(
            timestamp=time.time(),
            instance_index=-1,  # 协调器级别
            level=level,
            message=message,
        )
        if self._on_log:
            self._on_log(entry)
        getattr(logger, level)(f"[协调器] {message}")
