"""
配置管理模块
读写 config/accounts.json / config/settings.json，提供全局配置访问。
兼容旧结构：若根目录仍存在同名文件，也会优先回退读取。
"""

import json
import os
from dataclasses import dataclass, field
from typing import Optional

import sys as _sys


def _resolve_config_path(filename: str) -> str:
    """Resolve config files from config/ first, then fall back to the old root layout."""
    primary = os.path.join(BASE_DIR, "config", filename)
    legacy = os.path.join(BASE_DIR, filename)
    if os.path.exists(primary):
        return primary
    if os.path.exists(legacy):
        return legacy
    return primary


# 配置文件路径
# 打包后: %APPDATA%\GameBot（可写，与只读的程序目录分离）
# 开发模式: 项目根目录下的 config/
if getattr(_sys, 'frozen', False):
    BASE_DIR = os.path.join(os.environ.get('APPDATA', '.'), 'GameBot')
    os.makedirs(BASE_DIR, exist_ok=True)
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(os.path.join(BASE_DIR, "config"), exist_ok=True)
ACCOUNTS_PATH = _resolve_config_path("accounts.json")
SETTINGS_PATH = _resolve_config_path("settings.json")


@dataclass
class AccountConfig:
    """单个账号配置"""
    qq: str                    # QQ 号
    nickname: str              # 游戏昵称
    game_id: str               # 游戏内 ID（用于校验）
    group: str                 # "A" 或 "B"
    role: str                  # "captain" 或 "member"
    instance_index: int        # 雷电模拟器实例编号（0-5）


@dataclass
class Settings:
    """全局设置"""
    ldplayer_path: str = r"C:\leidian\LDPlayer9"  # 雷电模拟器安装路径
    adb_path: str = ""                             # ADB 路径，空则用 ldplayer 自带的
    llm_api_url: str = ""                          # Gemini 逆向 API 地址
    llm_api_key: str = ""                          # API Key
    game_package: str = "com.tencent.tmgp.pubgmhd" # 游戏包名（私服可能不同）
    game_activity: str = ""                        # 游戏启动 Activity
    game_mode: str = ""                            # 预设游戏模式
    game_map: str = ""                             # 预设地图
    match_timeout: int = 60                        # 匹配超时（秒）
    state_timeout: int = 30                        # 通用状态超时（秒）
    screenshot_interval: float = 1.0               # 截图间隔（秒）
    normalize_resolution: list = field(default_factory=lambda: [1280, 720])  # 归一化分辨率
    dev_mock: bool = False                         # 开发模式（macOS mock）
    mock_screenshots_dir: str = ""                 # mock 模式截图目录


class ConfigManager:
    """配置管理器"""

    def __init__(self):
        self.accounts: list[AccountConfig] = []
        self.settings = Settings()

    def load(self):
        """加载所有配置"""
        self._load_settings()
        self._load_accounts()

    def save_settings(self):
        """保存设置到文件"""
        data = {
            "ldplayer_path": self.settings.ldplayer_path,
            "adb_path": self.settings.adb_path,
            "llm_api_url": self.settings.llm_api_url,
            "llm_api_key": self.settings.llm_api_key,
            "game_package": self.settings.game_package,
            "game_activity": self.settings.game_activity,
            "game_mode": self.settings.game_mode,
            "game_map": self.settings.game_map,
            "match_timeout": self.settings.match_timeout,
            "state_timeout": self.settings.state_timeout,
            "screenshot_interval": self.settings.screenshot_interval,
            "normalize_resolution": self.settings.normalize_resolution,
            "dev_mock": self.settings.dev_mock,
            "mock_screenshots_dir": self.settings.mock_screenshots_dir,
        }
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def save_accounts(self):
        """保存账号配置到文件"""
        data = []
        for acc in self.accounts:
            data.append({
                "qq": acc.qq,
                "nickname": acc.nickname,
                "game_id": acc.game_id,
                "group": acc.group,
                "role": acc.role,
                "instance_index": acc.instance_index,
            })
        with open(ACCOUNTS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_group_accounts(self, group: str) -> list[AccountConfig]:
        """获取指定组的所有账号"""
        return [a for a in self.accounts if a.group == group]

    def get_captain(self, group: str) -> Optional[AccountConfig]:
        """获取指定组的队长"""
        for a in self.accounts:
            if a.group == group and a.role == "captain":
                return a
        return None

    def _load_settings(self):
        if not os.path.exists(SETTINGS_PATH):
            self.save_settings()  # 生成默认配置
            return
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key, value in data.items():
            if hasattr(self.settings, key):
                setattr(self.settings, key, value)

    def _load_accounts(self):
        if not os.path.exists(ACCOUNTS_PATH):
            self._create_default_accounts()
            return
        with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.accounts = [AccountConfig(**item) for item in data]

    def _create_default_accounts(self):
        """首次运行创建空账号列表，由用户在设置页面自行分配"""
        self.accounts = []
        self.save_accounts()


# 全局单例
config = ConfigManager()
