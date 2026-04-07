"""
有限状态机 (FSM) 定义
基于 transitions 库，定义所有状态和转换规则
Captain / Member 角色有不同的转换路径
"""

import logging
import time
from typing import Callable, Optional

from transitions import Machine

from .models import State, Role

logger = logging.getLogger(__name__)

# 所有状态值列表
ALL_STATES = [s.value for s in State]

# --- 状态转换定义 ---

# 通用转换（Captain 和 Member 共享）
COMMON_TRANSITIONS = [
    # 启动流程
    {"trigger": "start", "source": State.IDLE.value, "dest": State.LAUNCHING.value},
    {"trigger": "game_ready", "source": State.LAUNCHING.value, "dest": State.LOGIN_CHECK.value},
    {"trigger": "login_ok", "source": State.LOGIN_CHECK.value, "dest": State.LOBBY.value},
    {"trigger": "enter_lobby", "source": State.LOBBY.value, "dest": State.DISMISS_POPUPS.value},

    # 弹窗清理完成后
    # Captain → SETUP, Member → 等待（由协调器指挥加入队伍）
    # 这两条由角色过滤，见下方 CAPTAIN_TRANSITIONS / MEMBER_TRANSITIONS

    # 等待真人 → 校验玩家
    {"trigger": "players_joined", "source": State.WAIT_PLAYERS.value, "dest": State.VERIFY_PLAYERS.value},
    {"trigger": "players_verified", "source": State.VERIFY_PLAYERS.value, "dest": State.READY_CHECK.value},

    # 准备完成 → 匹配
    {"trigger": "all_ready", "source": State.READY_CHECK.value, "dest": State.MATCHING.value},

    # 匹配结果
    {"trigger": "match_found", "source": State.MATCHING.value, "dest": State.VERIFY_OPPONENT.value},
    {"trigger": "opponent_correct", "source": State.VERIFY_OPPONENT.value, "dest": State.SUCCESS.value},
    {"trigger": "opponent_wrong", "source": State.VERIFY_OPPONENT.value, "dest": State.ABORT.value},

    # 中止 → 回到大厅重来
    {"trigger": "restart", "source": State.ABORT.value, "dest": State.LOBBY.value},
    {"trigger": "restart", "source": State.SUCCESS.value, "dest": State.IDLE.value},

    # 匹配超时
    {"trigger": "match_timeout", "source": State.MATCHING.value, "dest": State.ABORT.value},

    # 错误处理 — 从任何状态都可以进入错误状态
    {"trigger": "banned", "source": "*", "dest": State.ERROR_BANNED.value},
    {"trigger": "network_error", "source": "*", "dest": State.ERROR_NETWORK.value},
    {"trigger": "unknown_error", "source": "*", "dest": State.ERROR_UNKNOWN.value},

    # 从网络错误恢复
    {"trigger": "recovered", "source": State.ERROR_NETWORK.value, "dest": State.LOBBY.value},
    # 从未知错误恢复（由 LLM 决策后触发）
    {"trigger": "recovered", "source": State.ERROR_UNKNOWN.value, "dest": State.LOBBY.value},

    # 强制回到空闲（停止）
    {"trigger": "force_stop", "source": "*", "dest": State.IDLE.value},
]

# Captain 专用转换
CAPTAIN_TRANSITIONS = [
    {"trigger": "popups_cleared", "source": State.DISMISS_POPUPS.value, "dest": State.SETUP.value},
    {"trigger": "setup_done", "source": State.SETUP.value, "dest": State.TEAM_CREATE.value},
    {"trigger": "team_created", "source": State.TEAM_CREATE.value, "dest": State.WAIT_PLAYERS.value},
]

# Member 专用转换
MEMBER_TRANSITIONS = [
    {"trigger": "popups_cleared", "source": State.DISMISS_POPUPS.value, "dest": State.TEAM_JOIN.value},
    {"trigger": "team_joined", "source": State.TEAM_JOIN.value, "dest": State.WAIT_PLAYERS.value},
]


class GameStateMachine:
    """
    游戏自动化状态机
    封装 transitions.Machine，提供状态变化回调和超时检测
    """

    def __init__(self, instance_index: int, role: Role,
                 on_state_change: Optional[Callable] = None):
        """
        Args:
            instance_index: 实例编号
            role: 角色（Captain/Member）
            on_state_change: 状态变化回调 fn(old_state, new_state)
        """
        self.instance_index = instance_index
        self.role = role
        self._on_state_change = on_state_change
        self._state_enter_time = time.time()

        # 构造转换列表
        transitions = list(COMMON_TRANSITIONS)
        if role == Role.CAPTAIN:
            transitions.extend(CAPTAIN_TRANSITIONS)
        else:
            transitions.extend(MEMBER_TRANSITIONS)

        # 初始化 transitions Machine
        self.machine = Machine(
            model=self,
            states=ALL_STATES,
            transitions=transitions,
            initial=State.IDLE.value,
            auto_transitions=False,      # 禁用自动生成的 to_xxx 方法
            send_event=True,             # 回调收到 EventData
            before_state_change="on_before_change",
            after_state_change="on_after_change",
        )

    @property
    def current_state(self) -> State:
        return State(self.state)

    @property
    def state_duration(self) -> float:
        """当前状态已持续秒数"""
        return time.time() - self._state_enter_time

    def is_error_state(self) -> bool:
        return self.current_state in (
            State.ERROR_BANNED, State.ERROR_NETWORK, State.ERROR_UNKNOWN
        )

    def is_terminal_state(self) -> bool:
        return self.current_state in (State.SUCCESS, State.ERROR_BANNED)

    def can_trigger(self, trigger_name: str) -> bool:
        """检查是否可以触发指定事件"""
        return self.machine.get_triggers(self.state).__contains__(trigger_name)

    def on_before_change(self, event):
        """状态变化前回调"""
        pass

    def on_after_change(self, event):
        """状态变化后回调"""
        old_state = event.transition.source
        new_state = event.transition.dest
        self._state_enter_time = time.time()

        logger.info(
            f"[实例{self.instance_index}] 状态变化: {old_state} → {new_state} "
            f"(trigger={event.event.name})"
        )

        if self._on_state_change:
            self._on_state_change(old_state, new_state)

    def get_available_triggers(self) -> list[str]:
        """获取当前状态下可用的触发器"""
        return self.machine.get_triggers(self.state)
