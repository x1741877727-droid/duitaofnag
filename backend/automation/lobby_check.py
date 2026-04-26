"""
v2 P2 dismiss_popups 四元信号融合判大厅 — 修当前最大 bug "半透明弹窗误判".

四个条件 *全部* 满足才算大厅, 任一不满足继续清弹窗:
  ① 模板 lobby_start_btn (or lobby_start_game) conf > 0.85
  ② YOLO 检测: close_x = 0 AND action_btn = 0 (没有任何弹窗结构)
  ③ 4 角无半透明遮罩 (overlay 检测)
  ④ 5 帧 phash 距离 < 3 (画面持续 1 秒稳定, 防过渡帧)

旧逻辑只用 ① + "连续 2 次命中" 兜底, 半透明弹窗下模板照常命中
导致误判 → 提前判大厅 → 跳过 dismiss_popups → 后续 phase 操作弹窗下 UI 出问题.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np

from .adb_lite import phash, phash_distance


@dataclass
class LobbyQuadResult:
    """四元融合判定结果. is_lobby 是最终结论, 其他字段供日志/前端可视化."""
    is_lobby: bool
    template_hit: bool
    template_conf: float
    template_name: str
    yolo_close_x_count: int
    yolo_action_btn_count: int
    has_overlay: bool
    phash_stable_frames: int
    phash_stable_required: int
    note: str = ""


class LobbyQuadDetector:
    """有状态: 维护 phash 滑动窗口判稳定. 每个实例一个 detector.

    用法:
        det = LobbyQuadDetector()
        for round in main_loop:
            shot = await screenshot()
            yolo_dets = yolo.detect(shot)
            r = det.check(shot, matcher, yolo_dets)
            if r.is_lobby:
                done()
            # 不是大厅 → 继续清弹窗
    """

    def __init__(
        self,
        stable_frames_required: int = 5,
        phash_dist_max: int = 3,
        template_threshold: float = 0.85,
        overlay_corner_dark: int = 50,
        overlay_center_diff: int = 40,
    ):
        self.stable_required = stable_frames_required
        self.phash_dist_max = phash_dist_max
        self.template_threshold = template_threshold
        self._overlay_corner_dark = overlay_corner_dark
        self._overlay_center_diff = overlay_center_diff
        self._phash_history: list[int] = []

    def reset(self) -> None:
        self._phash_history.clear()

    # ─── 四个独立信号 ───

    def _check_template(self, frame: np.ndarray, matcher,
                        template_names: list[str]) -> tuple[bool, float, str]:
        if matcher is None:
            return False, 0.0, ""
        best_conf = 0.0
        best_name = ""
        for tn in template_names:
            try:
                m = matcher.match_one(frame, tn, threshold=0.5)
            except Exception:
                m = None
            if m and m.confidence > best_conf:
                best_conf = m.confidence
                best_name = tn
        hit = best_conf >= self.template_threshold
        return hit, best_conf, best_name

    @staticmethod
    def _count_yolo(detections: list, target_cls: str) -> int:
        if not detections:
            return 0
        return sum(1 for d in detections if getattr(d, "cls", "") == target_cls)

    def _check_overlay(self, frame: np.ndarray) -> bool:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
        h, w = gray.shape
        if h < 120 or w < 120:
            return False
        corners = [
            gray[0:60, 0:60],
            gray[0:60, w - 60:w],
            gray[h - 60:h, 0:60],
            gray[h - 60:h, w - 60:w],
        ]
        avg_corner = float(np.mean([c.mean() for c in corners]))
        center = gray[h // 4:3 * h // 4, w // 4:3 * w // 4]
        avg_center = float(center.mean())
        return (
            avg_corner < self._overlay_corner_dark
            and avg_center > avg_corner + self._overlay_center_diff
        )

    def _check_phash_stable(self, frame: np.ndarray) -> tuple[int, bool]:
        """连续 N 帧 phash 距离 < phash_dist_max 算稳定. 跟上一帧比, 渐变也算."""
        try:
            cur = phash(frame)
        except Exception:
            return len(self._phash_history), False

        if not self._phash_history:
            self._phash_history.append(cur)
            return 1, False

        last = self._phash_history[-1]
        dist = phash_distance(cur, last)
        if dist <= self.phash_dist_max:
            self._phash_history.append(cur)
            # 控制窗口大小
            if len(self._phash_history) > self.stable_required + 5:
                self._phash_history.pop(0)
        else:
            self._phash_history = [cur]

        n = len(self._phash_history)
        return n, n >= self.stable_required

    # ─── 总检查 ───

    def check(
        self,
        frame: np.ndarray,
        matcher,
        yolo_detections: list,
        template_names: Optional[List[str]] = None,
    ) -> LobbyQuadResult:
        names = template_names or ["lobby_start_btn", "lobby_start_game"]
        t_hit, t_conf, t_name = self._check_template(frame, matcher, names)
        close_x = self._count_yolo(yolo_detections, "close_x")
        action_btn = self._count_yolo(yolo_detections, "action_btn")
        has_overlay = self._check_overlay(frame)
        stable_n, stable = self._check_phash_stable(frame)

        is_lobby = (
            t_hit
            and close_x == 0
            and action_btn == 0
            and not has_overlay
            and stable
        )

        if is_lobby:
            note = "all 4 signals OK"
        else:
            reasons = []
            if not t_hit:
                reasons.append(f"template={t_conf:.2f}<{self.template_threshold}")
            if close_x > 0:
                reasons.append(f"close_x={close_x}")
            if action_btn > 0:
                reasons.append(f"action_btn={action_btn}")
            if has_overlay:
                reasons.append("overlay")
            if not stable:
                reasons.append(f"unstable {stable_n}/{self.stable_required}")
            note = ", ".join(reasons)

        return LobbyQuadResult(
            is_lobby=is_lobby,
            template_hit=t_hit,
            template_conf=t_conf,
            template_name=t_name,
            yolo_close_x_count=close_x,
            yolo_action_btn_count=action_btn,
            has_overlay=has_overlay,
            phash_stable_frames=stable_n,
            phash_stable_required=self.stable_required,
            note=note,
        )
