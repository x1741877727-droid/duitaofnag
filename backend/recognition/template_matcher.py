"""
OpenCV 模板匹配器
支持截图归一化、多尺度匹配、灰度化，解决跨机器识别问题
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# 默认归一化分辨率
DEFAULT_RESOLUTION = (1280, 720)

# 多尺度搜索范围
SCALE_RANGE = [0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2]


@dataclass
class MatchResult:
    """模板匹配结果"""
    matched: bool           # 是否命中
    template_name: str      # 模板名称
    confidence: float       # 匹配置信度 (0~1)
    x: int = 0              # 匹配中心 x（归一化坐标下）
    y: int = 0              # 匹配中心 y（归一化坐标下）
    width: int = 0          # 匹配区域宽
    height: int = 0         # 匹配区域高
    scale: float = 1.0      # 匹配时的缩放倍率


@dataclass
class Template:
    """模板定义"""
    name: str                           # 模板名称（如 "btn_start_match"）
    category: str                       # 分类（如 "lobby", "match", "popup"）
    image: np.ndarray = field(repr=False)  # 模板图片（灰度）
    threshold: float = 0.85             # 匹配阈值
    # 可选：指定搜索区域 (x, y, w, h)，加速匹配
    roi: Optional[tuple[int, int, int, int]] = None


class TemplateMatcher:
    """
    模板匹配器
    - 截图归一化到固定分辨率
    - 多尺度搜索容忍缩放差异
    - 灰度化减少颜色偏差
    """

    def __init__(self, templates_dir: str, resolution: tuple[int, int] = DEFAULT_RESOLUTION):
        """
        Args:
            templates_dir: 模板图片目录
            resolution: 归一化分辨率 (width, height)
        """
        self.templates_dir = templates_dir
        self.resolution = resolution
        self.templates: dict[str, Template] = {}

    def load_templates(self):
        """
        从目录加载所有模板图片
        目录结构: templates/{category}/{name}.png
        阈值配置: templates/{category}/{name}.json（可选）
        """
        if not os.path.isdir(self.templates_dir):
            logger.warning(f"模板目录不存在: {self.templates_dir}")
            return

        count = 0
        for category in os.listdir(self.templates_dir):
            cat_dir = os.path.join(self.templates_dir, category)
            if not os.path.isdir(cat_dir):
                continue

            for filename in os.listdir(cat_dir):
                if not filename.lower().endswith((".png", ".jpg", ".jpeg")):
                    continue

                name = os.path.splitext(filename)[0]
                filepath = os.path.join(cat_dir, filename)
                img = cv2.imread(filepath, cv2.IMREAD_GRAYSCALE)
                if img is None:
                    logger.warning(f"无法读取模板: {filepath}")
                    continue

                # 归一化模板到目标分辨率比例
                img = self._normalize_template(img)

                # 尝试读取阈值配置
                threshold = self._load_threshold(cat_dir, name)

                # 尝试读取 ROI 配置
                roi = self._load_roi(cat_dir, name)

                key = f"{category}/{name}"
                self.templates[key] = Template(
                    name=key,
                    category=category,
                    image=img,
                    threshold=threshold,
                    roi=roi,
                )
                count += 1

        logger.info(f"加载了 {count} 个模板")

    def add_template(self, name: str, category: str, image: np.ndarray,
                     threshold: float = 0.85, roi: Optional[tuple] = None):
        """动态添加模板（用于模板采集工具）"""
        if len(image.shape) == 3:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        image = self._normalize_template(image)

        key = f"{category}/{name}"
        self.templates[key] = Template(
            name=key, category=category, image=image,
            threshold=threshold, roi=roi,
        )
        logger.info(f"添加模板: {key}")

    def normalize_screenshot(self, screenshot: np.ndarray) -> np.ndarray:
        """将截图归一化到固定分辨率"""
        h, w = screenshot.shape[:2]
        target_w, target_h = self.resolution

        if w == target_w and h == target_h:
            return screenshot

        return cv2.resize(screenshot, (target_w, target_h), interpolation=cv2.INTER_AREA)

    def match_one(self, screenshot: np.ndarray, template_key: str,
                  multi_scale: bool = True) -> MatchResult:
        """
        匹配单个模板
        Args:
            screenshot: 原始截图 (BGR)
            template_key: 模板键名，如 "lobby/btn_start"
            multi_scale: 是否启用多尺度匹配
        Returns:
            MatchResult
        """
        tmpl = self.templates.get(template_key)
        if tmpl is None:
            return MatchResult(matched=False, template_name=template_key, confidence=0)

        # 归一化 + 灰度化
        normalized = self.normalize_screenshot(screenshot)
        if len(normalized.shape) == 3:
            gray = cv2.cvtColor(normalized, cv2.COLOR_BGR2GRAY)
        else:
            gray = normalized

        # 如果有 ROI，裁剪搜索区域
        search_area = gray
        roi_offset_x, roi_offset_y = 0, 0
        if tmpl.roi:
            rx, ry, rw, rh = tmpl.roi
            search_area = gray[ry:ry+rh, rx:rx+rw]
            roi_offset_x, roi_offset_y = rx, ry

        if multi_scale:
            return self._match_multi_scale(search_area, tmpl, roi_offset_x, roi_offset_y)
        else:
            return self._match_single_scale(search_area, tmpl, 1.0, roi_offset_x, roi_offset_y)

    def match_any(self, screenshot: np.ndarray,
                  category: Optional[str] = None,
                  template_keys: Optional[list[str]] = None,
                  multi_scale: bool = False) -> Optional[MatchResult]:
        """
        匹配多个模板，返回第一个命中的结果
        Args:
            screenshot: 截图
            category: 按分类过滤
            template_keys: 指定模板列表
            multi_scale: 是否多尺度（对批量匹配建议关闭以提升速度）
        """
        keys = template_keys or []
        if not keys and category:
            keys = [k for k, t in self.templates.items() if t.category == category]
        elif not keys:
            keys = list(self.templates.keys())

        # 归一化 + 灰度化只做一次
        normalized = self.normalize_screenshot(screenshot)
        if len(normalized.shape) == 3:
            gray = cv2.cvtColor(normalized, cv2.COLOR_BGR2GRAY)
        else:
            gray = normalized

        for key in keys:
            tmpl = self.templates.get(key)
            if tmpl is None:
                continue

            search_area = gray
            roi_offset_x, roi_offset_y = 0, 0
            if tmpl.roi:
                rx, ry, rw, rh = tmpl.roi
                search_area = gray[ry:ry+rh, rx:rx+rw]
                roi_offset_x, roi_offset_y = rx, ry

            if multi_scale:
                result = self._match_multi_scale(search_area, tmpl, roi_offset_x, roi_offset_y)
            else:
                result = self._match_single_scale(search_area, tmpl, 1.0, roi_offset_x, roi_offset_y)

            if result.matched:
                return result

        return None

    def match_all(self, screenshot: np.ndarray,
                  category: Optional[str] = None) -> list[MatchResult]:
        """匹配所有模板，返回所有命中的结果"""
        keys = [k for k, t in self.templates.items()
                if category is None or t.category == category]

        normalized = self.normalize_screenshot(screenshot)
        if len(normalized.shape) == 3:
            gray = cv2.cvtColor(normalized, cv2.COLOR_BGR2GRAY)
        else:
            gray = normalized

        results = []
        for key in keys:
            tmpl = self.templates.get(key)
            if tmpl is None:
                continue

            search_area = gray
            roi_offset_x, roi_offset_y = 0, 0
            if tmpl.roi:
                rx, ry, rw, rh = tmpl.roi
                search_area = gray[ry:ry+rh, rx:rx+rw]
                roi_offset_x, roi_offset_y = rx, ry

            result = self._match_single_scale(search_area, tmpl, 1.0, roi_offset_x, roi_offset_y)
            if result.matched:
                results.append(result)

        return results

    def verify_template(self, screenshot: np.ndarray, template_key: str) -> dict:
        """
        验证模板在当前截图上的匹配效果（模板采集工具用）
        返回详细信息：各尺度下的置信度
        """
        tmpl = self.templates.get(template_key)
        if tmpl is None:
            return {"error": f"模板 {template_key} 不存在"}

        normalized = self.normalize_screenshot(screenshot)
        if len(normalized.shape) == 3:
            gray = cv2.cvtColor(normalized, cv2.COLOR_BGR2GRAY)
        else:
            gray = normalized

        results_by_scale = {}
        for scale in SCALE_RANGE:
            resized = self._resize_template(tmpl.image, scale)
            th, tw = resized.shape[:2]
            sh, sw = gray.shape[:2]
            if tw > sw or th > sh:
                continue

            res = cv2.matchTemplate(gray, resized, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            results_by_scale[f"{scale:.2f}x"] = {
                "confidence": round(float(max_val), 4),
                "location": (max_loc[0] + tw // 2, max_loc[1] + th // 2),
                "pass": max_val >= tmpl.threshold,
            }

        best_scale = max(results_by_scale.items(), key=lambda x: x[1]["confidence"])
        return {
            "template": template_key,
            "threshold": tmpl.threshold,
            "best_scale": best_scale[0],
            "best_confidence": best_scale[1]["confidence"],
            "overall_pass": best_scale[1]["pass"],
            "details": results_by_scale,
        }

    # --- 内部方法 ---

    def _match_multi_scale(self, search_area: np.ndarray, tmpl: Template,
                           roi_offset_x: int = 0, roi_offset_y: int = 0) -> MatchResult:
        """多尺度匹配"""
        best_val = 0.0
        best_loc = (0, 0)
        best_scale = 1.0
        best_size = (0, 0)

        for scale in SCALE_RANGE:
            resized = self._resize_template(tmpl.image, scale)
            th, tw = resized.shape[:2]
            sh, sw = search_area.shape[:2]

            if tw > sw or th > sh:
                continue

            res = cv2.matchTemplate(search_area, resized, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)

            if max_val > best_val:
                best_val = max_val
                best_loc = max_loc
                best_scale = scale
                best_size = (tw, th)

        matched = best_val >= tmpl.threshold
        cx = best_loc[0] + best_size[0] // 2 + roi_offset_x
        cy = best_loc[1] + best_size[1] // 2 + roi_offset_y

        if matched:
            logger.debug(f"模板 {tmpl.name} 命中: conf={best_val:.3f} scale={best_scale:.2f} pos=({cx},{cy})")

        return MatchResult(
            matched=matched,
            template_name=tmpl.name,
            confidence=float(best_val),
            x=cx, y=cy,
            width=best_size[0], height=best_size[1],
            scale=best_scale,
        )

    def _match_single_scale(self, search_area: np.ndarray, tmpl: Template,
                            scale: float, roi_offset_x: int = 0,
                            roi_offset_y: int = 0) -> MatchResult:
        """单尺度匹配（快速）"""
        template_img = tmpl.image if scale == 1.0 else self._resize_template(tmpl.image, scale)
        th, tw = template_img.shape[:2]
        sh, sw = search_area.shape[:2]

        if tw > sw or th > sh:
            return MatchResult(matched=False, template_name=tmpl.name, confidence=0)

        res = cv2.matchTemplate(search_area, template_img, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)

        matched = max_val >= tmpl.threshold
        cx = max_loc[0] + tw // 2 + roi_offset_x
        cy = max_loc[1] + th // 2 + roi_offset_y

        return MatchResult(
            matched=matched,
            template_name=tmpl.name,
            confidence=float(max_val),
            x=cx, y=cy,
            width=tw, height=th,
            scale=scale,
        )

    def _resize_template(self, template: np.ndarray, scale: float) -> np.ndarray:
        """缩放模板"""
        if scale == 1.0:
            return template
        h, w = template.shape[:2]
        new_w = max(1, int(w * scale))
        new_h = max(1, int(h * scale))
        return cv2.resize(template, (new_w, new_h), interpolation=cv2.INTER_AREA)

    def _normalize_template(self, template: np.ndarray) -> np.ndarray:
        """归一化模板（假设模板在标准分辨率下截取，不需要额外缩放）"""
        return template

    def _load_threshold(self, cat_dir: str, name: str) -> float:
        """从 JSON 配置加载阈值"""
        import json
        config_path = os.path.join(cat_dir, f"{name}.json")
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                data = json.load(f)
            return data.get("threshold", 0.85)
        return 0.85

    def _load_roi(self, cat_dir: str, name: str) -> Optional[tuple[int, int, int, int]]:
        """从 JSON 配置加载 ROI"""
        import json
        config_path = os.path.join(cat_dir, f"{name}.json")
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                data = json.load(f)
            roi = data.get("roi")
            if roi and len(roi) == 4:
                return tuple(roi)
        return None

    def save_template(self, name: str, category: str, image: np.ndarray,
                      threshold: float = 0.85, roi: Optional[tuple] = None):
        """保存模板到磁盘"""
        cat_dir = os.path.join(self.templates_dir, category)
        os.makedirs(cat_dir, exist_ok=True)

        # 保存图片
        filepath = os.path.join(cat_dir, f"{name}.png")
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image
        cv2.imwrite(filepath, gray)

        # 保存配置
        import json
        config = {"threshold": threshold}
        if roi:
            config["roi"] = list(roi)
        config_path = os.path.join(cat_dir, f"{name}.json")
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        # 注册到内存
        self.add_template(name, category, gray, threshold, roi)
        logger.info(f"模板已保存: {filepath}")
