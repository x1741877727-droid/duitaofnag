"""
模板采集/验证工具
- 截图模拟器 → 用户框选区域 → 保存为模板
- 即时测试匹配
- 批量验证所有模板在当前环境下的识别率

这个模块提供 API 接口，实际 UI 交互在前端 TemplateTool.tsx 中完成
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from ..recognition.template_matcher import TemplateMatcher, MatchResult

logger = logging.getLogger(__name__)


@dataclass
class CaptureResult:
    """采集结果"""
    success: bool
    template_name: str = ""
    category: str = ""
    image_path: str = ""
    error: str = ""


@dataclass
class VerifyResult:
    """单模板验证结果"""
    template_name: str
    passed: bool
    best_confidence: float
    best_scale: str
    details: dict


class TemplateTool:
    """
    模板采集和验证工具
    提供给 Web UI 调用的后端接口
    """

    def __init__(self, matcher: TemplateMatcher):
        self.matcher = matcher

    def capture_template(self, screenshot: np.ndarray,
                         region: tuple[int, int, int, int],
                         name: str, category: str,
                         threshold: float = 0.85) -> CaptureResult:
        """
        从截图中裁剪区域保存为模板
        Args:
            screenshot: 完整截图 (BGR)
            region: 裁剪区域 (x, y, w, h) — 基于归一化分辨率坐标
            name: 模板名称
            category: 分类（如 "popup", "lobby", "match"）
            threshold: 匹配阈值
        Returns:
            CaptureResult
        """
        # 先归一化截图
        normalized = self.matcher.normalize_screenshot(screenshot)

        x, y, w, h = region
        # 边界检查
        img_h, img_w = normalized.shape[:2]
        x = max(0, min(x, img_w - 1))
        y = max(0, min(y, img_h - 1))
        w = min(w, img_w - x)
        h = min(h, img_h - y)

        if w < 5 or h < 5:
            return CaptureResult(success=False, error="区域太小（最小 5x5）")

        # 裁剪
        cropped = normalized[y:y+h, x:x+w]

        # 保存到磁盘 + 注册到 matcher
        try:
            self.matcher.save_template(
                name=name,
                category=category,
                image=cropped,
                threshold=threshold,
                roi=None,  # ROI 可以后续在 JSON 配置中手动添加
            )

            image_path = os.path.join(self.matcher.templates_dir, category, f"{name}.png")
            logger.info(f"模板采集成功: {category}/{name} ({w}x{h})")

            return CaptureResult(
                success=True,
                template_name=f"{category}/{name}",
                category=category,
                image_path=image_path,
            )
        except Exception as e:
            logger.error(f"模板保存失败: {e}")
            return CaptureResult(success=False, error=str(e))

    def test_match(self, screenshot: np.ndarray,
                   template_key: str,
                   multi_scale: bool = True) -> MatchResult:
        """
        即时测试：在当前截图上匹配指定模板
        """
        return self.matcher.match_one(screenshot, template_key, multi_scale=multi_scale)

    def verify_single(self, screenshot: np.ndarray, template_key: str) -> VerifyResult:
        """
        详细验证单个模板：各尺度下的置信度
        """
        detail = self.matcher.verify_template(screenshot, template_key)
        return VerifyResult(
            template_name=template_key,
            passed=detail.get("overall_pass", False),
            best_confidence=detail.get("best_confidence", 0),
            best_scale=detail.get("best_scale", "1.00x"),
            details=detail,
        )

    def verify_all(self, screenshot: np.ndarray,
                   category: Optional[str] = None) -> dict:
        """
        批量验证所有模板在当前截图上的识别效果
        返回总体统计 + 每个模板的详细结果
        """
        keys = [
            k for k, t in self.matcher.templates.items()
            if category is None or t.category == category
        ]

        if not keys:
            return {"total": 0, "passed": 0, "failed": 0, "results": []}

        results = []
        passed_count = 0

        for key in keys:
            vr = self.verify_single(screenshot, key)
            results.append({
                "name": vr.template_name,
                "passed": vr.passed,
                "confidence": round(vr.best_confidence, 4),
                "best_scale": vr.best_scale,
            })
            if vr.passed:
                passed_count += 1

        return {
            "total": len(keys),
            "passed": passed_count,
            "failed": len(keys) - passed_count,
            "pass_rate": round(passed_count / len(keys) * 100, 1) if keys else 0,
            "results": results,
        }

    def list_templates(self, category: Optional[str] = None) -> list[dict]:
        """列出所有已注册的模板"""
        templates = []
        for key, tmpl in self.matcher.templates.items():
            if category and tmpl.category != category:
                continue
            templates.append({
                "name": tmpl.name,
                "category": tmpl.category,
                "threshold": tmpl.threshold,
                "size": f"{tmpl.image.shape[1]}x{tmpl.image.shape[0]}",
                "has_roi": tmpl.roi is not None,
            })
        return templates

    def delete_template(self, template_key: str) -> bool:
        """删除模板（内存 + 磁盘）"""
        if template_key not in self.matcher.templates:
            return False

        tmpl = self.matcher.templates[template_key]

        # 删除磁盘文件
        parts = template_key.split("/")
        if len(parts) == 2:
            category, name = parts
            img_path = os.path.join(self.matcher.templates_dir, category, f"{name}.png")
            json_path = os.path.join(self.matcher.templates_dir, category, f"{name}.json")
            for path in [img_path, json_path]:
                if os.path.exists(path):
                    os.remove(path)

        # 从内存移除
        del self.matcher.templates[template_key]
        logger.info(f"模板已删除: {template_key}")
        return True

    def get_screenshot_preview(self, screenshot: np.ndarray,
                               template_key: Optional[str] = None) -> bytes:
        """
        生成截图预览（带匹配框标注），返回 JPEG bytes
        用于前端展示
        """
        # 归一化
        preview = self.matcher.normalize_screenshot(screenshot).copy()

        # 如果指定了模板，标注匹配位置
        if template_key:
            result = self.matcher.match_one(screenshot, template_key, multi_scale=True)
            if result.matched:
                x, y = result.x, result.y
                w, h = result.width, result.height
                top_left = (x - w // 2, y - h // 2)
                bottom_right = (x + w // 2, y + h // 2)
                cv2.rectangle(preview, top_left, bottom_right, (0, 255, 0), 2)
                label = f"{result.template_name} ({result.confidence:.2f})"
                cv2.putText(preview, label, (top_left[0], top_left[1] - 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

        _, buffer = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return buffer.tobytes()
