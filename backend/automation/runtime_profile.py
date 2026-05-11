"""
Runtime Profile — 全局性能模式 (stable / balanced / speed)

设计意图:
  整个脚本里散落的"硬编码常数" (max_workers / round_interval / threshold / fps / ...)
  收敛到这一个文件, 由用户选的 mode 决定具体值.

  Wizard 应用时调 set_mode("speed") → 全局生效 → runner / decision_log / vision_daemon
  下次读取这些参数时拿到新值.

mode 的科学依据 (见 docs / agent 调查报告):
  - stable: 多缓冲, 闪退率最低, 适合弱机 / 长跑稳定性场景
  - balanced: 默认, 6 实例 E5-2673v3 实测最佳点
  - speed: 极致压榨, 速度优先, 弱机也能选 (闪退风险红色警告)

存储:
  - 不用 .env 不用 settings (避免跟 LDPlayer perf-optimized.json 撞)
  - %APPDATA%/GameBot/state/runtime-profile.json
  - 启动时 load, 用户改 mode 调 save_profile()

公开 API:
  get_profile() -> RuntimeProfile           (全局单例, 模块顶层访问安全)
  set_mode(mode_name) -> RuntimeProfile     (切 mode + 持久化)
  load_profile_from_disk() -> None          (启动时自动调一次)
  reload_dependents() -> None               (mode 改后通知 decision_log / vision_daemon 重建池)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Profile 数据
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

VALID_MODES = ("stable", "balanced", "speed")


@dataclass
class RuntimeProfile:
    """所有 mode-driven 性能参数. 一个 dict 化的 namespace."""
    mode: str = "balanced"

    # asyncio default ThreadPoolExecutor
    pool_max_workers: int = 64

    # decision_log IO pool (cv2.imwrite 落盘)
    dlog_pool_workers: int = 8

    # phase round_interval (秒) — single_runner sleep 前 resolve
    p0_round_interval: float = 0.5
    p1_round_interval: float = 0.2
    p2_round_interval: float = 0.5
    p3_round_interval: float = 1.0
    p4_round_interval: float = 1.0
    p5_round_interval: float = 1.0

    # vision daemon
    daemon_target_fps: int = 8
    daemon_motion_threshold: int = 4    # dHash hamming, 4=严格 / 6=平衡 / 8=松

    # OCR pool worker 数 (按 CPU 自适应, mode 决定除数)
    # stable: cpu_logical / 8 (节省 RAM)
    # balanced: cpu_logical / 4 (默认)
    # speed: cpu_logical / 3 (并行 OCR)
    ocr_pool_divisor: int = 4
    ocr_pool_min: int = 2
    ocr_pool_max: int = 8

    # P1 motion gate
    p1_motion_threshold: int = 6

    # 5-tier 短路 (speed 模式: 高置信命中即停, 不跑剩余 tier)
    five_tier_short_circuit: bool = False

    # 模板匹配 confidence 阈值
    tmpl_conf_default: float = 0.7

    # YOLO confidence 阈值
    yolo_conf: float = 0.25

    def as_dict(self) -> dict:
        return asdict(self)


# 三档预设 (来自 agent 调查 + 用户实测)
_PRESETS: dict[str, dict] = {
    "stable": {
        "mode": "stable",
        "pool_max_workers": 32,
        "dlog_pool_workers": 6,
        "p0_round_interval": 0.7,
        "p1_round_interval": 0.3,
        "p2_round_interval": 0.7,
        "p3_round_interval": 1.5,
        "p4_round_interval": 1.5,
        "p5_round_interval": 1.5,
        "daemon_target_fps": 4,
        "daemon_motion_threshold": 8,
        "p1_motion_threshold": 8,
        "five_tier_short_circuit": False,
        "tmpl_conf_default": 0.75,
        "yolo_conf": 0.30,
        "ocr_pool_divisor": 8,
        "ocr_pool_min": 1,
        "ocr_pool_max": 4,
    },
    "balanced": {
        "mode": "balanced",
        "pool_max_workers": 64,
        "dlog_pool_workers": 8,
        "p0_round_interval": 0.5,
        "p1_round_interval": 0.2,
        "p2_round_interval": 0.5,
        "p3_round_interval": 1.0,
        "p4_round_interval": 1.0,
        "p5_round_interval": 1.0,
        "daemon_target_fps": 8,
        "daemon_motion_threshold": 6,
        "p1_motion_threshold": 6,
        "five_tier_short_circuit": False,
        "tmpl_conf_default": 0.70,
        "yolo_conf": 0.25,
        "ocr_pool_divisor": 4,
        "ocr_pool_min": 2,
        "ocr_pool_max": 6,
    },
    "speed": {
        "mode": "speed",
        "pool_max_workers": 96,
        "dlog_pool_workers": 12,
        "p0_round_interval": 0.3,
        "p1_round_interval": 0.1,
        "p2_round_interval": 0.3,
        "p3_round_interval": 0.6,
        "p4_round_interval": 0.6,
        "p5_round_interval": 0.6,
        "daemon_target_fps": 12,
        "daemon_motion_threshold": 4,
        "p1_motion_threshold": 4,
        "five_tier_short_circuit": True,
        "tmpl_conf_default": 0.65,
        "yolo_conf": 0.20,
        "ocr_pool_divisor": 3,
        "ocr_pool_min": 3,
        "ocr_pool_max": 8,
    },
}


def preset(mode: str) -> RuntimeProfile:
    """从预设构造 profile, 不写盘. 给 wizard recommend 预览用."""
    if mode not in _PRESETS:
        raise ValueError(f"未知 mode: {mode}, 支持: {VALID_MODES}")
    return RuntimeProfile(**_PRESETS[mode])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 全局单例 + 持久化
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_lock = threading.Lock()
_current: Optional[RuntimeProfile] = None
_dependent_reloaders: list = []     # 注册回调, mode 改后通知 (decision_log / vision_daemon)


def _state_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", "")) / "GameBot"
    else:
        base = Path.home() / ".gamebot"
    p = base / "state"
    p.mkdir(parents=True, exist_ok=True)
    return p / "runtime-profile.json"


def get_profile() -> RuntimeProfile:
    """全局当前 profile. 第一次调用时 lazy load (兼容 import 顺序)."""
    global _current
    if _current is None:
        with _lock:
            if _current is None:
                _current = _load_or_default()
    return _current


def _load_or_default() -> RuntimeProfile:
    p = _state_path()
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            mode = data.get("mode", "balanced")
            base = preset(mode if mode in VALID_MODES else "balanced")
            # 用户自定义覆盖 (允许 wizard apply 后用户在 GUI 调单个参数)
            for k, v in data.items():
                if k != "mode" and hasattr(base, k):
                    setattr(base, k, v)
            logger.info(f"[runtime_profile] loaded mode={base.mode} from {p}")
            return base
        except Exception as e:
            logger.warning(f"[runtime_profile] load 失败, 用默认 balanced: {e}")
    return preset("balanced")


def load_profile_from_disk() -> RuntimeProfile:
    """显式调用, 强制重读. 启动时调一次."""
    global _current
    with _lock:
        _current = _load_or_default()
    return _current


def set_mode(mode: str, save: bool = True) -> RuntimeProfile:
    """切 mode + 持久化 + 通知依赖方 reload. 给 wizard 和设置页用."""
    if mode not in VALID_MODES:
        raise ValueError(f"未知 mode: {mode}")
    global _current
    with _lock:
        _current = preset(mode)
        if save:
            try:
                _state_path().write_text(
                    json.dumps(_current.as_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as e:
                logger.warning(f"[runtime_profile] save 失败: {e}")
    # 通知依赖方 (decision_log 池 / vision_daemon fps)
    for reload_fn in list(_dependent_reloaders):
        try:
            reload_fn()
        except Exception as e:
            logger.warning(f"[runtime_profile] dependent reload 失败: {e}")
    logger.info(f"[runtime_profile] mode → {mode}")
    return _current


def register_dependent(reload_fn) -> None:
    """注册 mode 改后的回调. decision_log / vision_daemon 启动时调."""
    _dependent_reloaders.append(reload_fn)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase round_interval resolver
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def resolve_ocr_workers() -> int:
    """按当前 mode + CPU 核数算 OCR pool worker 数.

    公式: clamp(cpu_logical // divisor, min, max)
    e.g. 20T CPU + speed mode (div=3, min=3, max=8) -> clamp(6, 3, 8) = 6
         12T CPU + balanced mode (div=4, min=2, max=6) -> clamp(3, 2, 6) = 3
    """
    p = get_profile()
    cpu_log = os.cpu_count() or 4
    raw = cpu_log // max(1, p.ocr_pool_divisor)
    return max(p.ocr_pool_min, min(p.ocr_pool_max, raw))


def resolve_round_interval(phase_name: str, fallback: float = 0.5) -> float:
    """根据 phase name 拿当前 mode 的 round_interval.

    phase_name 形如 "P0_accelerator" / "P1_launch" / "P2_dismiss" / "P3a_team_create" / ...
    取首字母 P0/P1/P2/P3/P4/P5 → 查 profile.
    """
    p = get_profile()
    n = phase_name.lower()
    if n.startswith("p0"):
        return p.p0_round_interval
    if n.startswith("p1"):
        return p.p1_round_interval
    if n.startswith("p2"):
        return p.p2_round_interval
    if n.startswith("p3"):
        return p.p3_round_interval
    if n.startswith("p4"):
        return p.p4_round_interval
    if n.startswith("p5"):
        return p.p5_round_interval
    return fallback
