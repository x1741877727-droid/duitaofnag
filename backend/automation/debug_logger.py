"""
调试日志系统 — 记录每步操作的截图、OCR结果、匹配过程

用法:
    from .debug_logger import DebugLogger
    dbg = DebugLogger(enabled=True, save_dir="logs")
    dbg.log_step("阶段4", "步骤1", "找组队按钮")
    dbg.log_screenshot(shot)
    dbg.log_ocr(hits, roi_desc="左侧栏 ROI(0,0.4,0.1,0.8) scale=3")
    dbg.log_match("组队", hit, fuzzy=True)
    dbg.log_action("tap", 30, 398)
    dbg.log_template("lobby_start_btn", confidence=0.85, threshold=0.7, hit=(640,360))
    dbg.log_fail("未找到组队按钮", candidates=hits)
"""

import logging
import os
import time
from datetime import datetime
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class DebugLogger:
    """自动化调试日志记录器"""

    def __init__(self, enabled: bool = True, save_dir: str = "logs"):
        self.enabled = enabled
        self._run_dir = save_dir
        self._step_start = 0.0
        self._current_phase = ""
        self._current_step = ""
        self._screenshot_count = 0

        if enabled:
            os.makedirs(self._run_dir, exist_ok=True)
            logger.info(f"[调试日志] 保存目录: {self._run_dir}")

    def log_step(self, phase: str, step: str, desc: str):
        """记录步骤开始"""
        if not self.enabled:
            return
        self._current_phase = phase
        self._current_step = step
        self._step_start = time.time()
        logger.info(f"[{phase}] [{step}] {desc}")

    def log_screenshot(self, shot: Optional[np.ndarray], tag: str = "") -> str:
        """保存截图，返回文件路径"""
        if not self.enabled or shot is None:
            return ""
        self._screenshot_count += 1
        ts = datetime.now().strftime("%H%M%S")
        name = f"{ts}_{self._current_phase}_{self._current_step}"
        if tag:
            name += f"_{tag}"
        name += f"_{self._screenshot_count}.jpg"
        path = os.path.join(self._run_dir, name)
        cv2.imwrite(path, shot, [cv2.IMWRITE_JPEG_QUALITY, 50])
        return path

    def log_ocr(self, hits: list, roi_desc: str = "全图"):
        """记录 OCR 结果"""
        if not self.enabled:
            return
        texts = [(h.text, h.cx, h.cy) for h in hits]
        logger.info(f"  OCR [{roi_desc}] {len(hits)}个结果: {texts}")

    def log_ocr_annotated(self, shot: np.ndarray, hits: list,
                          tag: str = "", roi_desc: str = "全图") -> str:
        """保存标注截图：在原图上画出每个 OCR 命中的位置和文字

        用于后续分析 ROI 区域。每个命中画一个圆点 + 文字标签。
        """
        if not self.enabled or shot is None or not hits:
            return ""
        annotated = shot.copy()
        for h in hits:
            cv2.circle(annotated, (h.cx, h.cy), 6, (0, 255, 0), -1)
            cv2.putText(annotated, h.text, (h.cx + 8, h.cy - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
        self._screenshot_count += 1
        ts = datetime.now().strftime("%H%M%S")
        name = f"{ts}_ocr_{roi_desc}"
        if tag:
            name += f"_{tag}"
        name += f"_{self._screenshot_count}.jpg"
        path = os.path.join(self._run_dir, name)
        cv2.imwrite(path, annotated, [cv2.IMWRITE_JPEG_QUALITY, 60])
        self.log_ocr(hits, roi_desc)
        return path

    def log_match(self, keyword: str, hit, fuzzy: bool = False):
        """记录关键词匹配成功"""
        if not self.enabled:
            return
        fuzzy_tag = " (模糊)" if fuzzy else ""
        if hit:
            logger.info(f"  匹配{fuzzy_tag}: '{keyword}' → '{hit.text}' ({hit.cx},{hit.cy})")
        else:
            logger.info(f"  匹配{fuzzy_tag}: '{keyword}' → 未找到")

    def log_template(self, name: str, confidence: float, threshold: float,
                     hit_pos: Optional[tuple] = None):
        """记录模板匹配结果"""
        if not self.enabled:
            return
        if hit_pos:
            logger.info(f"  模板: '{name}' conf={confidence:.3f} >= th={threshold:.2f} → ({hit_pos[0]},{hit_pos[1]})")
        else:
            logger.info(f"  模板: '{name}' conf={confidence:.3f} < th={threshold:.2f} → 未命中")

    def log_action(self, action: str, x: int = 0, y: int = 0, detail: str = ""):
        """记录执行的动作"""
        if not self.enabled:
            return
        elapsed = round((time.time() - self._step_start) * 1000)
        msg = f"  动作: {action}"
        if x or y:
            msg += f"({x},{y})"
        if detail:
            msg += f" {detail}"
        msg += f" [{elapsed}ms]"
        logger.info(msg)

    def log_fail(self, reason: str, candidates: list = None):
        """记录失败，附带所有候选项供排查"""
        if not self.enabled:
            return
        logger.warning(f"  失败: {reason}")
        if candidates:
            texts = [(h.text, h.cx, h.cy) for h in candidates[:15]]
            logger.warning(f"  候选项: {texts}")

    def log_vpn(self, connected: bool, detail: str = ""):
        """记录 VPN 检测结果"""
        if not self.enabled:
            return
        status = "已连接" if connected else "未连接"
        logger.info(f"  VPN: {status} {detail}")
