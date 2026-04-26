"""自动截图收集 — 每次测试时把多样化截图存到 fixtures/yolo/raw_screenshots/

设计要点：
  - **跨进程 / 跨 instance** 的全局 dedup（pHash hamming 距离）
  - **磁盘上限**保护（满 5000 张就停止采）
  - **零开销路径**：不影响主测试性能，pHash 0.3ms

调用：
    from .screenshot_collector import collect

    # 在 single_runner 关键点调用（phase 切换 / 弹窗出现 / OCR 命中等）
    collect(screenshot, instance=1, tag="lobby_popup")

输出文件名：fixtures/yolo/raw_screenshots/<tag>_inst<N>_<unix_ts>.png

环境变量：
    GAMEBOT_YOLO_COLLECT_DISABLE=1     完全关闭
    GAMEBOT_YOLO_COLLECT_MAX=5000      最多保存 N 张
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ────────────── 配置 ──────────────
_DISABLED = bool(os.environ.get("GAMEBOT_YOLO_COLLECT_DISABLE"))
_MAX_FILES = int(os.environ.get("GAMEBOT_YOLO_COLLECT_MAX", "5000"))
_DEDUP_DISTANCE = 2  # pHash Hamming 距离 < 此值视为重复（2 = 更宽松，捕捉更多变种用于 YOLO 训练多样性）
_DEDUP_RING_SIZE = 2000  # 内存里保留多少近期 hash 做 dedup

# ────────────── 状态 ──────────────
_lock = threading.Lock()
_recent_hashes: deque = deque(maxlen=_DEDUP_RING_SIZE)
_dump_dir: Optional[Path] = None
_disk_count: Optional[int] = None
_warned_disk_full = False


def _ensure_dump_dir() -> Optional[Path]:
    """统一走 paths.user_yolo_raw_dir() — 持久存储 (%APPDATA%\\GameBot\\data\\yolo)，rebuild 不丢"""
    global _dump_dir
    if _dump_dir is not None:
        return _dump_dir
    try:
        from .user_paths import user_yolo_raw_dir
        _dump_dir = user_yolo_raw_dir()
        logger.info(f"YOLO 截图收集目录: {_dump_dir}")
        return _dump_dir
    except Exception as e:
        logger.warning(f"YOLO 截图收集：用户目录不可用 ({e})，禁用收集")
        return None


def _seed_dedup_from_disk() -> None:
    """启动时从已有截图重建 dedup 索引（避免重启重复采）"""
    global _disk_count
    target = _ensure_dump_dir()
    if not target:
        _disk_count = 0
        return
    from .adb_lite import phash
    files = list(target.glob("*.png")) + list(target.glob("*.jpg"))
    _disk_count = len(files)
    if files:
        # 只读最近 N 张算 hash 加进 ring（全读太慢）
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        sample = files[: _DEDUP_RING_SIZE]
        for p in sample:
            try:
                img = cv2.imread(str(p))
                if img is not None:
                    _recent_hashes.append(phash(img))
            except Exception:
                pass
        logger.info(f"YOLO dedup 已读 {len(sample)} 张历史指纹")


def collect(screenshot: np.ndarray, instance: Optional[int] = None, tag: str = "frame") -> bool:
    """采集一帧。返回 True=新帧已存, False=重复 / 满 / 禁用

    instance=None 时自动从 runner_service._current_instance ContextVar 读
    """
    global _disk_count, _warned_disk_full

    if _DISABLED or screenshot is None or screenshot.size == 0:
        return False

    if instance is None:
        try:
            from backend.runner_service import _current_instance
            instance = _current_instance.get(-1)
            if instance < 0:
                instance = 0
        except Exception:
            instance = 0

    if _disk_count is None:
        _seed_dedup_from_disk()
    if _disk_count is not None and _disk_count >= _MAX_FILES:
        if not _warned_disk_full:
            logger.warning(f"YOLO 截图已达上限 {_MAX_FILES}，停止收集")
            _warned_disk_full = True
        return False

    target = _ensure_dump_dir()
    if not target:
        return False

    from .adb_lite import phash, phash_distance
    fp = phash(screenshot)

    with _lock:
        # dedup
        for prev in _recent_hashes:
            if phash_distance(fp, prev) < _DEDUP_DISTANCE:
                return False
        _recent_hashes.append(fp)

        # 写盘
        # tag 清洗（不能含路径分隔符）
        safe_tag = tag.replace("/", "_").replace("\\", "_").replace(":", "_")[:32]
        name = f"{safe_tag}_inst{instance}_{int(time.time() * 1000)}.png"
        try:
            cv2.imwrite(str(target / name), screenshot)
            if _disk_count is not None:
                _disk_count += 1
            return True
        except Exception as e:
            logger.warning(f"YOLO 截图写盘失败: {e}")
            return False


def stats() -> dict:
    return {
        "enabled": not _DISABLED,
        "max_files": _MAX_FILES,
        "disk_count": _disk_count,
        "ring_size": len(_recent_hashes),
        "dump_dir": str(_dump_dir) if _dump_dir else None,
    }
