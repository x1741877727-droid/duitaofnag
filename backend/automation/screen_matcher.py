"""
ScreenMatcher — 基于真实截图模板的屏幕匹配引擎
精简版：只做模板匹配 + OCR关键词查找，不依赖LLM
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from . import metrics

logger = logging.getLogger(__name__)

# 归一化分辨率（所有截图统一缩放到这个尺寸再匹配）
NORM_W, NORM_H = 1280, 720

# 多尺度搜索（容忍轻微缩放差异）
SCALES = [1.0, 0.95, 1.05, 0.9, 1.1]


@dataclass
class MatchHit:
    """单次匹配命中"""
    name: str          # 模板名
    confidence: float  # 置信度
    cx: int            # 中心x（归一化坐标）
    cy: int            # 中心y（归一化坐标）
    w: int             # 匹配宽
    h: int             # 匹配高


class ScreenMatcher:
    """
    屏幕模板匹配器

    用法:
        matcher = ScreenMatcher("fixtures/templates")
        matcher.load_all()
        hits = matcher.find(screenshot, ["close_x_*", "btn_*"])
    """

    def __init__(self, template_dir: str, default_threshold: float = 0.80):
        self.template_dir = Path(template_dir)
        self.default_threshold = default_threshold
        # name -> (gray_image, threshold)
        self._templates: dict[str, tuple[np.ndarray, float]] = {}

    def _normalize_template(self, gray: np.ndarray, source_w: int = 0, source_h: int = 0) -> np.ndarray:
        """归一化模板到与 NORM_W x NORM_H 匹配的比例

        如果模板是在非标准分辨率下截取的，按比例缩放到标准分辨率。
        """
        if source_w <= 0 or source_h <= 0:
            return gray  # 无源分辨率信息，不缩放

        if source_w != NORM_W or source_h != NORM_H:
            scale_x = NORM_W / source_w
            scale_y = NORM_H / source_h
            scale = (scale_x + scale_y) / 2  # 取平均避免变形
            new_w = max(1, int(gray.shape[1] * scale))
            new_h = max(1, int(gray.shape[0] * scale))
            gray = cv2.resize(gray, (new_w, new_h))
        return gray

    def load_all(self) -> int:
        """加载所有模板图片，返回加载数量

        模板自动归一化：如果模板旁边有 _meta.txt 记录源分辨率，
        会按比例缩放到 1280x720。没有 meta 文件则假设已是标准分辨率。
        """
        count = 0
        for f in self.template_dir.glob("*.png"):
            name = f.stem
            img = cv2.imread(str(f), cv2.IMREAD_COLOR)
            if img is None:
                logger.warning(f"无法读取模板: {f}")
                continue
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            # 检查是否有 meta 文件记录源分辨率
            meta_path = self.template_dir / f"{name}_meta.txt"
            if meta_path.exists():
                try:
                    meta = meta_path.read_text().strip().split("x")
                    gray = self._normalize_template(gray, int(meta[0]), int(meta[1]))
                except Exception:
                    pass

            self._templates[name] = (gray, self.default_threshold)
            count += 1
        logger.info(f"已加载 {count} 个模板")
        return count

    def load_one(self, name: str, threshold: float | None = None) -> bool:
        """加载单个模板"""
        path = self.template_dir / f"{name}.png"
        if not path.exists():
            return False
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            return False
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        self._templates[name] = (gray, threshold or self.default_threshold)
        return True

    @property
    def template_names(self) -> list[str]:
        return list(self._templates.keys())

    # ----------------------------------------------------------------
    # 核心匹配
    # ----------------------------------------------------------------

    def _normalize(self, screenshot: np.ndarray) -> np.ndarray:
        """归一化截图到 1280x720 灰度"""
        h, w = screenshot.shape[:2]
        if len(screenshot.shape) == 3:
            gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
        else:
            gray = screenshot
        if w != NORM_W or h != NORM_H:
            gray = cv2.resize(gray, (NORM_W, NORM_H))
        return gray

    def match_one(
        self,
        screenshot: np.ndarray,
        template_name: str,
        threshold: float | None = None,
        multi_scale: bool = True,
        use_edge: bool = False,
    ) -> Optional[MatchHit]:
        """匹配单个模板，返回命中或None

        Args:
            use_edge: 使用 Canny 边缘检测匹配，对光照/对比度变化更鲁棒
        """
        if template_name not in self._templates:
            return None

        _m_t0 = __import__('time').perf_counter()
        tmpl_gray, default_th = self._templates[template_name]
        th = threshold if threshold is not None else default_th
        screen_gray = self._normalize(screenshot)

        # 边缘检测模式：Canny 提取轮廓后匹配
        if use_edge:
            screen_gray = cv2.Canny(screen_gray, 50, 150)
            tmpl_gray = cv2.Canny(tmpl_gray, 50, 150)

        scales = SCALES if multi_scale else [1.0]
        best_val = 0.0
        best_loc = None
        best_scale = 1.0
        best_tw, best_th_px = tmpl_gray.shape[1], tmpl_gray.shape[0]

        for scale in scales:
            if scale == 1.0:
                t = tmpl_gray
            else:
                new_w = max(1, int(tmpl_gray.shape[1] * scale))
                new_h = max(1, int(tmpl_gray.shape[0] * scale))
                t = cv2.resize(tmpl_gray, (new_w, new_h))

            # 模板不能比截图大
            if t.shape[0] > screen_gray.shape[0] or t.shape[1] > screen_gray.shape[1]:
                continue

            result = cv2.matchTemplate(screen_gray, t, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            if max_val > best_val:
                best_val = max_val
                best_loc = max_loc
                best_scale = scale
                best_tw, best_th_px = t.shape[1], t.shape[0]

            # 早退：1.0x 已达标就不试其他尺度
            if scale == 1.0 and best_val >= th:
                break

        _dur_ms = round((__import__('time').perf_counter() - _m_t0) * 1000, 2)
        if best_val >= th and best_loc is not None:
            cx = best_loc[0] + best_tw // 2
            cy = best_loc[1] + best_th_px // 2
            metrics.record("template_match", dur_ms=_dur_ms, tpl=template_name,
                           score=round(best_val, 3), hit=True,
                           scale=best_scale, edge=use_edge)
            return MatchHit(
                name=template_name,
                confidence=best_val,
                cx=cx, cy=cy,
                w=best_tw, h=best_th_px,
            )
        metrics.record("template_match", dur_ms=_dur_ms, tpl=template_name,
                       score=round(best_val, 3), hit=False, edge=use_edge)
        return None

    def find_any(
        self,
        screenshot: np.ndarray,
        names: list[str] | None = None,
        threshold: float | None = None,
    ) -> Optional[MatchHit]:
        """在模板列表中找第一个匹配的，返回置信度最高的命中"""
        if names is None:
            names = list(self._templates.keys())

        best: Optional[MatchHit] = None
        for name in names:
            hit = self.match_one(screenshot, name, threshold)
            if hit and (best is None or hit.confidence > best.confidence):
                best = hit
        return best

    def find_all(
        self,
        screenshot: np.ndarray,
        names: list[str] | None = None,
        threshold: float | None = None,
    ) -> list[MatchHit]:
        """返回所有匹配的模板"""
        if names is None:
            names = list(self._templates.keys())

        hits = []
        for name in names:
            hit = self.match_one(screenshot, name, threshold)
            if hit:
                hits.append(hit)
        return hits

    def find_by_prefix(
        self,
        screenshot: np.ndarray,
        prefix: str,
        threshold: float | None = None,
    ) -> Optional[MatchHit]:
        """按前缀匹配模板名，返回最佳命中"""
        names = [n for n in self._templates if n.startswith(prefix)]
        return self.find_any(screenshot, names, threshold)

    # ----------------------------------------------------------------
    # 便捷方法
    # ----------------------------------------------------------------

    def is_at_lobby(self, screenshot: np.ndarray) -> bool:
        """检测是否在大厅（匹配"开始游戏"按钮即可，不做反向排除）

        弹窗遮挡由 _has_overlay() 单独判断，这里只负责快速检测大厅标志。
        阈值降低到 0.75 提高命中率，避免因截图微小变化导致漏检。
        """
        hit = self.find_any(screenshot, [
            "lobby_start_btn", "lobby_start_game"
        ], threshold=0.75)
        return hit is not None

    def find_close_button(self, screenshot: np.ndarray) -> Optional[MatchHit]:
        """查找任何X关闭按钮"""
        names = [n for n in self._templates if n.startswith("close_x_")]
        return self.find_any(screenshot, names, threshold=0.70)

    def find_action_button(self, screenshot: np.ndarray) -> Optional[MatchHit]:
        """查找任何操作按钮（确定/同意/加入等）"""
        names = [n for n in self._templates if n.startswith("btn_")]
        return self.find_any(screenshot, names, threshold=0.75)

    def is_accelerator_connected(self, screenshot: np.ndarray) -> Optional[bool]:
        """检测加速器状态: True=已连接, False=未连接, None=不在加速器界面"""
        # 两个按钮形状相似（都是圆形），需要同时匹配取更高置信度的
        pause = self.match_one(screenshot, "accelerator_pause", threshold=0.90)
        play = self.match_one(screenshot, "accelerator_play", threshold=0.90)
        if pause and play:
            # 两个都匹配了，取置信度高的
            return pause.confidence > play.confidence
        if pause:
            return True
        if play:
            return False
        return None

    def find_dialog_close(self, screenshot: np.ndarray) -> Optional[MatchHit]:
        """查找对话框类的X关闭按钮（包括活动弹窗和对话框面板）"""
        names = [n for n in self._templates if n.startswith("close_x_")]
        return self.find_any(screenshot, names, threshold=0.70)

    def find_button(self, screenshot: np.ndarray, name: str, threshold: float = 0.75) -> Optional[MatchHit]:
        """按名称查找特定按钮，返回匹配命中（含坐标）"""
        return self.match_one(screenshot, name, threshold=threshold)
