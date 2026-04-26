r"""
弹窗规则热加载 — 持久化版

读取层级（后者覆盖前者）：
  1. 内置 DEFAULTS（永远存在的兜底）
  2. bundle 内 config/popup_rules.json（跟代码版本走的默认值）
  3. 用户目录 %APPDATA%\GameBot\config\popup_rules.json（用户编辑覆盖）

写入：永远写到用户目录，rebuild 不被覆盖。

设计目标：
  改 keyword / 加 close text **不用 rebuild GameBot.exe**，
  下一轮 dismiss_popups 立刻用新规则。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from .user_paths import bundle_config_path, user_config_dir

logger = logging.getLogger(__name__)


# 内置默认值（万一 JSON 全丢/解析失败时兜底）
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


def _bundle_path() -> Optional[Path]:
    return bundle_config_path("popup_rules.json")


def _user_path() -> Path:
    return user_config_dir() / "popup_rules.json"


def _read_json_safe(p: Path) -> dict:
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f"[rules] 读 {p} 失败: {e}")
        return {}


def _merge_rules(base: dict, override: dict) -> dict:
    """override 的同名 list 完全替换 base，未指定的保留 base"""
    out = dict(base)
    for k, v in override.items():
        if k.startswith("_"):
            continue
        if isinstance(v, list):
            out[k] = v
    return out


class RulesLoader:
    """三层 mtime 检测自动 reload"""

    _bundle_p: Optional[Path] = None
    _user_p: Optional[Path] = None
    _bundle_mtime: float = 0.0
    _user_mtime: float = 0.0
    _rules: dict = DEFAULTS.copy()
    _load_count: int = 0

    @classmethod
    def _resolve(cls) -> None:
        if cls._bundle_p is None:
            cls._bundle_p = _bundle_path()
        if cls._user_p is None:
            cls._user_p = _user_path()

    @classmethod
    def get(cls) -> dict:
        """返回当前规则（合并后）。每次调用都做 mtime 检查（os.stat ~30us × 2）"""
        cls._resolve()

        bundle_mtime = 0.0
        user_mtime = 0.0
        try:
            if cls._bundle_p and cls._bundle_p.is_file():
                bundle_mtime = cls._bundle_p.stat().st_mtime
        except OSError:
            pass
        try:
            if cls._user_p.is_file():
                user_mtime = cls._user_p.stat().st_mtime
        except OSError:
            pass

        if bundle_mtime <= cls._bundle_mtime and user_mtime <= cls._user_mtime:
            return cls._rules

        # 任意一边变了 → 重 merge
        rules = dict(DEFAULTS)
        if cls._bundle_p and cls._bundle_p.is_file():
            rules = _merge_rules(rules, _read_json_safe(cls._bundle_p))
        if cls._user_p.is_file():
            rules = _merge_rules(rules, _read_json_safe(cls._user_p))

        cls._rules = rules
        cls._bundle_mtime = bundle_mtime
        cls._user_mtime = user_mtime
        cls._load_count += 1
        logger.info(
            f"[rules] reload #{cls._load_count}: "
            f"bundle={cls._bundle_p} user={cls._user_p} "
            f"close={len(rules.get('close_text', []))} "
            f"confirm={len(rules.get('confirm_text', []))}"
        )
        return cls._rules

    @classmethod
    def path(cls) -> Optional[str]:
        """对外：返回写入路径（用户目录），写永远走这"""
        cls._resolve()
        return str(cls._user_p)

    @classmethod
    def user_path(cls) -> Path:
        cls._resolve()
        return cls._user_p

    @classmethod
    def write_user_override(cls, rules: dict) -> str:
        """覆盖写整个用户配置文件"""
        cls._resolve()
        p = cls._user_p
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(rules, f, ensure_ascii=False, indent=2)
        return str(p)
