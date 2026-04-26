"""
用户数据持久目录 — rebuild 不会被覆盖

所有用户写入的东西都放这里：
  - 配置文件（popup_rules.json 用户改的版本）
  - YOLO 训练截图 + 标注
  - 训练好的模型 weights

Windows: %APPDATA%\\GameBot\\
其他系统: ~/.gamebot/

PyInstaller --clean 只清 dist/GameBot/ 下面的东西，
%APPDATA% 在用户目录，**永远不会被 rebuild 覆盖**。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional


def user_data_dir() -> Path:
    """返回持久数据根目录，自动创建"""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = Path(base) / "GameBot"
    else:
        d = Path(os.path.expanduser("~/.gamebot"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_config_dir() -> Path:
    d = user_data_dir() / "config"
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_yolo_dir() -> Path:
    """YOLO 训练数据根目录。子目录：
      raw_screenshots/   原始截图（自动采）
      labels/            YOLO 格式 .txt 标注
      models/            训练后的 onnx
    """
    d = user_data_dir() / "data" / "yolo"
    (d / "raw_screenshots").mkdir(parents=True, exist_ok=True)
    (d / "labels").mkdir(parents=True, exist_ok=True)
    (d / "models").mkdir(parents=True, exist_ok=True)
    return d


def first_run_copy(default_path: Optional[str], user_path: Path) -> Optional[Path]:
    """
    若 user_path 不存在但 default_path 存在 → 拷过去（首次运行 seed）
    返回最终可读取的路径（user 优先）
    """
    if user_path.exists():
        return user_path
    if default_path and os.path.isfile(default_path):
        try:
            user_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(default_path, user_path)
            return user_path
        except Exception:
            return Path(default_path)  # 拷不动就用只读默认
    return None
