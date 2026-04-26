r"""
统一管理"用户可写持久化目录" — rebuild 不会被覆盖。

为啥要这个：
  PyInstaller folder build 每次都 wipe 整个 dist/GameBot/ 重建，
  写到 _internal/config/、_internal/fixtures/ 的用户数据全没。

解决：
  Windows: %APPDATA%\GameBot\         (例: C:\Users\Administrator\AppData\Roaming\GameBot)
  其他   : ~/.gamebot/

目录结构：
  <user_dir>/
    config/popup_rules.json          用户编辑的规则覆盖（覆盖 bundle 默认值）
    data/yolo/raw_screenshots/       自动收集的 YOLO 训练截图
    data/yolo/labels/                标注 .txt（YOLO 格式）
    data/yolo/classes.txt            类别表
    models/                          训练好的 ONNX 模型
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


_CACHED_USER_DIR: "Path | None" = None


def user_data_dir() -> Path:
    """返回用户可写持久化目录（rebuild 不动）"""
    global _CACHED_USER_DIR
    if _CACHED_USER_DIR is not None:
        return _CACHED_USER_DIR
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = Path(base) / "GameBot"
    else:
        d = Path(os.path.expanduser("~/.gamebot"))
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning(f"创建用户目录失败 {d}: {e}")
    _CACHED_USER_DIR = d
    return d


def user_config_dir() -> Path:
    p = user_data_dir() / "config"
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_yolo_dir() -> Path:
    """YOLO 根目录（debug_server / yolo_dismisser 用）— rebuild 不动"""
    p = user_data_dir() / "data" / "yolo"
    p.mkdir(parents=True, exist_ok=True)
    return p


# 兼容别名
user_yolo_root = user_yolo_dir


def user_yolo_raw_dir() -> Path:
    p = user_yolo_dir() / "raw_screenshots"
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_yolo_labels_dir() -> Path:
    p = user_yolo_dir() / "labels"
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_yolo_classes_file() -> Path:
    """类别表（YOLO 格式：每行一个类名，行号 = class id）"""
    p = user_yolo_dir() / "classes.txt"
    if not p.exists():
        p.write_text("close_x\naction_btn\n", encoding="utf-8")
    return p


def user_models_dir() -> Path:
    p = user_data_dir() / "models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def bundle_config_path(filename: str) -> "Path | None":
    """返回 bundle 内只读默认配置路径（仅用于读取默认值）"""
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "config" / filename)
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "_internal" / "config" / filename)
        candidates.append(exe_dir / "config" / filename)
    here = Path(__file__).resolve().parent
    candidates.append(here.parent.parent / "config" / filename)
    for p in candidates:
        if p.is_file():
            return p
    return None
