"""
弹窗规则热加载

热加载机制：
  - 配置在 config/popup_rules.json
  - 每次 get_rules() 检查文件 mtime
  - mtime 变了就重新读 + 缓存
  - 失败/不存在 → 回退到 DEFAULTS（保证脚本不挂）

设计目标：
  改 keyword / 加 close text **不用 rebuild GameBot.exe**，
  下一轮 dismiss_popups 立刻用新规则。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Optional

logger = logging.getLogger(__name__)


# 内置默认值（万一 JSON 丢失或解析失败时兜底）
DEFAULTS: dict = {
    "lobby_keywords": ["开始游戏"],
    "loading_keywords": ["正在检查更新", "正在加载", "加载中"],
    "login_keywords": ["QQ授权登录", "微信登录", "登录中"],
    "left_game_keywords": ["CDN节点第", "六花官方通知"],
    "close_text": ["关闭", "×", "✕", "X"],
    "confirm_text": [
        "确定", "确认", "知道了", "我知道了", "同意", "暂不", "跳过", "不需要",
        "领取见面礼", "点击屏幕继续", "点击屏幕", "点击继续",
        "立即更新", "稍后更新", "已了解", "已阅读", "取消", "返回",
        "重新连接", "重试", "继续游戏", "我已满18周岁",
        "下次再说", "以后再说",
    ],
    "checkbox_text": [
        "今日内不再弹出", "今日不再弹出", "不再弹出", "不再提醒",
        "不再显示", "下次不再提醒",
    ],
}


def _resolve_config_path() -> Optional[str]:
    """frozen-aware：dev / PyInstaller 都能找到 config/popup_rules.json"""
    candidates = []
    # PyInstaller _MEIPASS（onefile 模式）
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "config", "popup_rules.json"))
    # frozen exe 旁边的 _internal/config/
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates.append(os.path.join(exe_dir, "_internal", "config", "popup_rules.json"))
        candidates.append(os.path.join(exe_dir, "config", "popup_rules.json"))
    # dev 模式：从本文件向上找 game-automation/config/
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(
        os.path.dirname(os.path.dirname(here)),
        "config", "popup_rules.json",
    ))
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


class RulesLoader:
    """热加载 popup_rules.json，按 mtime 自动 reload"""

    _path: Optional[str] = None
    _last_mtime: float = 0.0
    _rules: dict = DEFAULTS.copy()
    _load_count: int = 0

    @classmethod
    def get(cls) -> dict:
        """返回当前规则。每次调用都做轻量 mtime 检查（os.stat ~30us）"""
        if cls._path is None:
            cls._path = _resolve_config_path()
            if cls._path is None:
                if cls._load_count == 0:
                    logger.warning("[rules] popup_rules.json 不存在，用内置 DEFAULTS")
                    cls._load_count = 1
                return cls._rules

        try:
            mtime = os.path.getmtime(cls._path)
        except OSError:
            return cls._rules

        if mtime <= cls._last_mtime:
            return cls._rules

        # mtime changed → reload
        try:
            with open(cls._path, "r", encoding="utf-8") as f:
                new_rules = json.load(f)
        except Exception as e:
            logger.warning(f"[rules] 读 {cls._path} 失败: {e}, 沿用旧规则")
            return cls._rules

        # 合并 DEFAULTS（用户 JSON 缺字段时用 DEFAULTS 补）
        merged = DEFAULTS.copy()
        for k, v in new_rules.items():
            if k.startswith("_"):
                continue  # 跳过 _comment / _schema_version 等
            if isinstance(v, list):
                merged[k] = v

        cls._rules = merged
        cls._last_mtime = mtime
        cls._load_count += 1
        logger.info(
            f"[rules] reload #{cls._load_count} from {cls._path}: "
            f"close={len(merged['close_text'])} confirm={len(merged['confirm_text'])} "
            f"checkbox={len(merged['checkbox_text'])} loading={len(merged['loading_keywords'])} "
            f"left_game={len(merged['left_game_keywords'])}"
        )
        return cls._rules

    @classmethod
    def path(cls) -> Optional[str]:
        if cls._path is None:
            cls._path = _resolve_config_path()
        return cls._path
