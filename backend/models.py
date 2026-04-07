"""
数据模型
定义状态机状态、事件、实例信息、匹配结果等核心数据结构
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class State(str, Enum):
    """实例状态"""
    IDLE = "idle"                       # 空闲
    LAUNCHING = "launching"             # 启动模拟器 + 游戏
    LOGIN_CHECK = "login_check"         # 检查自动登录
    LOBBY = "lobby"                     # 游戏大厅
    DISMISS_POPUPS = "dismiss_popups"   # 关闭弹窗
    SETUP = "setup"                     # 赛前设置（模式/地图/自动补位）
    TEAM_CREATE = "team_create"         # 队长创建队伍 + 生成二维码
    TEAM_JOIN = "team_join"             # 队员加入队伍
    WAIT_PLAYERS = "wait_players"       # 等待真人玩家加入
    VERIFY_PLAYERS = "verify_players"   # 校验玩家 ID
    READY_CHECK = "ready_check"         # 检查所有人准备状态
    MATCHING = "matching"               # 匹配中
    VERIFY_OPPONENT = "verify_opponent" # 校验对手
    SUCCESS = "success"                 # 匹配到目标对手
    ABORT = "abort"                     # 未匹配到，准备重来
    ERROR_BANNED = "error_banned"       # 被禁赛
    ERROR_NETWORK = "error_network"     # 网络错误
    ERROR_UNKNOWN = "error_unknown"     # 未知错误


class Role(str, Enum):
    """实例角色"""
    CAPTAIN = "captain"
    MEMBER = "member"


class Group(str, Enum):
    """分组"""
    A = "A"
    B = "B"


@dataclass
class InstanceInfo:
    """单个模拟器实例的运行时信息"""
    index: int                          # 模拟器实例编号（0-5）
    group: Group                        # 所属分组
    role: Role                          # 角色
    state: State = State.IDLE           # 当前状态
    qq: str = ""                        # QQ 号
    nickname: str = ""                  # 游戏昵称
    game_id: str = ""                   # 游戏内 ID
    adb_serial: str = ""               # ADB 连接地址

    # 运行时状态
    error_msg: str = ""                 # 错误信息
    retry_count: int = 0                # 当前轮次重试次数
    last_screenshot_time: float = 0     # 最后截图时间
    state_enter_time: float = 0         # 进入当前状态的时间

    def state_duration(self) -> float:
        """当前状态已持续秒数"""
        if self.state_enter_time == 0:
            return 0
        return time.time() - self.state_enter_time


@dataclass
class TeamInfo:
    """组队信息"""
    group: Group
    captain_index: int                  # 队长实例编号
    member_indices: list[int]           # 队员实例编号列表
    qr_code_url: str = ""              # 组队二维码链接
    team_ready: bool = False            # 全队是否就绪
    real_player_ids: list[str] = field(default_factory=list)  # 预设真人玩家 ID 列表


@dataclass
class MatchAttempt:
    """单次匹配尝试记录"""
    attempt_number: int                 # 第几次尝试
    timestamp: float = 0               # 匹配发起时间
    group_a_matched: bool = False       # A 组是否匹配到对局
    group_b_matched: bool = False       # B 组是否匹配到对局
    opponent_is_target: bool = False    # 对手是否为目标队伍
    abort_reason: str = ""              # 中止原因
    duration: float = 0                 # 本次匹配耗时（秒）


@dataclass
class SessionStats:
    """会话统计"""
    start_time: float = 0              # 开始时间
    total_attempts: int = 0            # 总匹配次数
    success_count: int = 0             # 成功次数
    abort_count: int = 0               # 中止次数
    error_count: int = 0               # 错误次数
    attempts: list[MatchAttempt] = field(default_factory=list)

    @property
    def running_duration(self) -> float:
        """运行时长（秒）"""
        if self.start_time == 0:
            return 0
        return time.time() - self.start_time

    def record_attempt(self, attempt: MatchAttempt):
        self.total_attempts += 1
        self.attempts.append(attempt)
        if attempt.opponent_is_target:
            self.success_count += 1
        elif attempt.abort_reason:
            self.abort_count += 1


@dataclass
class CoordinatorCommand:
    """协调器发给实例的指令"""
    action: str                         # 指令动作
    target_indices: list[int] = field(default_factory=list)  # 目标实例
    data: dict = field(default_factory=dict)  # 附加数据

    # 常用 action 值
    # "start"         — 启动流程
    # "pause"         — 暂停
    # "resume"        — 恢复
    # "stop"          — 停止
    # "match_now"     — 立即匹配（同步信号）
    # "abort"         — 中止当前匹配
    # "join_team"     — 加入队伍（data 含 url）
    # "disconnect"    — 断网退出


@dataclass
class LogEntry:
    """日志条目（推送给前端）"""
    timestamp: float
    instance_index: int                 # -1 表示协调器级别
    level: str                          # "info", "warn", "error"
    message: str
    state: str = ""                     # 当前状态

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "instance": self.instance_index,
            "level": self.level,
            "message": self.message,
            "state": self.state,
        }
