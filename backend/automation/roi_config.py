"""归一化 ROI 配置加载器 — Task 0.4

ROI 定义在 `config/roi.yaml`（归一化坐标 + scale）。
调用：
    from backend.automation import roi_config
    x1, y1, x2, y2, scale = roi_config.get("team_btn_left")

或通过 OcrDismisser._ocr_roi_named(shot, "team_btn_left") 一步到位。

热加载：开发时改 yaml 后调 `roi_config.reload()` 即生效，不必重启。
"""
from __future__ import annotations

import logging
import os
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# 与 backend/config.py 同步的项目根
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_YAML_PATH = os.path.join(_BASE_DIR, "config", "roi.yaml")

_cache: Optional[Dict[str, dict]] = None


def _load() -> Dict[str, dict]:
    global _cache
    if _cache is not None:
        return _cache
    if not os.path.exists(_YAML_PATH):
        logger.warning(f"roi.yaml 不存在：{_YAML_PATH}，所有 ROI 查询将失败")
        _cache = {}
        return _cache
    try:
        import yaml  # type: ignore
    except ImportError:
        raise RuntimeError(
            "PyYAML 未安装。pip install PyYAML（已加到 requirements.txt）"
        )
    with open(_YAML_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"roi.yaml 顶层必须是 dict，实际：{type(data).__name__}")
    _cache = data
    logger.info(f"roi.yaml 加载完成：{len(_cache)} 个 ROI ({_YAML_PATH})")
    return _cache


def get(name: str) -> Tuple[float, float, float, float, int]:
    """返回 (x1, y1, x2, y2, scale)。
    name 不存在 → KeyError；rect 字段格式错 → ValueError。
    """
    cfg = _load()
    if name not in cfg:
        raise KeyError(
            f"ROI '{name}' 未在 config/roi.yaml 定义。已有：{list(cfg.keys())}"
        )
    item = cfg[name]
    rect = item.get("rect")
    if not isinstance(rect, (list, tuple)) or len(rect) != 4:
        raise ValueError(
            f"ROI '{name}' 的 rect 必须是 4 元素列表 [x1,y1,x2,y2]，实际：{rect}"
        )
    scale = int(item.get("scale", 1))
    return (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]), scale)


def reload() -> Dict[str, dict]:
    """开发时改了 yaml 想热生效时调用"""
    global _cache
    _cache = None
    return _load()


def all_names() -> list:
    """列出所有 ROI 名（debug 用）"""
    return list(_load().keys())
