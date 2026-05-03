"""
v2 P2 dismiss_popups 四元信号融合判大厅 — 修当前最大 bug "半透明弹窗误判".

四个条件 *全部* 满足才算大厅, 任一不满足继续清弹窗:
  ① 模板 lobby_start_btn (or lobby_start_game) conf > 0.78
     (实测大厅干净时 score ~ 0.80-0.82, 0.85 太严会一直不过 quad)
  ② YOLO 检测: close_x = 0 AND action_btn = 0 (没有任何弹窗结构)
  ③ 4 角无半透明遮罩 (overlay 检测)
  ④ 5 帧 phash 距离 < 3 (画面持续 1 秒稳定, 防过渡帧)

旧逻辑只用 ① + "连续 2 次命中" 兜底, 半透明弹窗下模板照常命中
导致误判 → 提前判大厅 → 跳过 dismiss_popups → 后续 phase 操作弹窗下 UI 出问题.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np

from .adb_lite import phash, phash_distance


@dataclass
class LobbyQuadResult:
    """大厅判定结果. 类名保留 Quad 兼容历史 import, 实际用的信号已重构.
    新逻辑: 模板找到按钮 ∧ 按钮亮 ∧ 没X ∧ 没弹窗 ∧ 4角不黑.
    删了: action_btn=0 (直播 banner 占用) + phash 5 帧不动 (大厅永远在动)."""
    is_lobby: bool
    template_hit: bool
    template_conf: float
    template_name: str
    yolo_close_x_count: int
    yolo_action_btn_count: int
    has_overlay: bool
    phash_stable_frames: int        # 留字段, 不参与判定 (前端可视化兼容)
    phash_stable_required: int
    yolo_dialog_count: int = 0
    button_brightness: float = 0.0  # 按钮 ROI HSV V 均值. 干净大厅 200+, 遮罩下 <80
    yolo_lobby_count: int = 0       # lobby_v1 模型: 检出"角色站立大厅" 数量 (recall 100% on val)
    lobby_streak: int = 0           # 当前连续命中 lobby (yolo + 无弹窗) 的帧数
    lobby_streak_required: int = 2  # 需连续 N 帧才判 lobby (防关弹窗瞬间 yolo 闪现误判)
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
        template_threshold: float = 0.78,
        overlay_corner_dark: int = 50,
        overlay_center_diff: int = 40,
        button_brightness_min: float = 120.0,   # 干净大厅按钮 V>200, 遮罩下 V<80, 120 居中
    ):
        self.stable_required = stable_frames_required
        self.phash_dist_max = phash_dist_max
        self.template_threshold = template_threshold
        self.button_brightness_min = button_brightness_min
        self._overlay_corner_dark = overlay_corner_dark
        self._overlay_center_diff = overlay_center_diff
        self._phash_history: list[int] = []
        # YOLO lobby streak 状态: 关弹窗瞬间 yolo 可能"看穿"动画看到角色, 连续 N 帧才信
        # GAMEBOT_LOBBY_STREAK env 可调; 默认 2 (~0.5-1s)
        self.lobby_streak_required: int = int(os.environ.get("GAMEBOT_LOBBY_STREAK", "2"))
        self._lobby_streak: int = 0
        self._last_check_ts: float = 0.0
        # 跨长 gap 重置 (phase 切换间隔大, 老 streak 不能跨用)
        self._streak_reset_gap_s: float = 5.0

    def reset(self) -> None:
        self._phash_history.clear()
        self._lobby_streak = 0
        self._last_check_ts = 0.0

    # ─── 四个独立信号 ───

    def _check_template(self, frame: np.ndarray, matcher,
                        template_names: list[str]) -> tuple[bool, float, str, object]:
        """返回 (hit, conf, name, best_match_obj). best_match_obj 用来后续算按钮亮度."""
        if matcher is None:
            return False, 0.0, "", None
        best_conf = 0.0
        best_name = ""
        best_match = None
        for tn in template_names:
            try:
                m = matcher.match_one(frame, tn, threshold=0.5)
            except Exception:
                m = None
            if m and m.confidence > best_conf:
                best_conf = m.confidence
                best_name = tn
                best_match = m
        hit = best_conf >= self.template_threshold
        return hit, best_conf, best_name, best_match

    @staticmethod
    def _check_button_brightness(frame: np.ndarray, match) -> float:
        """算按钮 ROI 的 HSV 亮度 (V 通道) 均值. 干净大厅金黄按钮 V~200+, 弹窗遮罩下 V<80.
        阈值 120 (在 _check 里) 把两种情况干净分开. match 为 None / 越界 → 返 0."""
        if match is None:
            return 0.0
        try:
            cx, cy, w, h = int(match.cx), int(match.cy), int(match.w), int(match.h)
            x1 = max(0, cx - w // 2)
            y1 = max(0, cy - h // 2)
            x2 = min(frame.shape[1], cx + w // 2)
            y2 = min(frame.shape[0], cy + h // 2)
            if x2 <= x1 or y2 <= y1:
                return 0.0
            roi = frame[y1:y2, x1:x2]
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            return float(hsv[:, :, 2].mean())
        except Exception:
            return 0.0

    @staticmethod
    def _count_yolo(detections: list, target_cls: str) -> int:
        if not detections:
            return 0
        # 预存 bug 修复: Detection.cls 是 int class id, Detection.name 才是字符串
        # 旧代码 getattr(d, "cls", "") == "close_x" 永远 False,
        # 整条 yolo 信号失效. 改用 .name 比对.
        return sum(1 for d in detections if getattr(d, "name", "") == target_cls)

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
        t_hit, t_conf, t_name, t_match = self._check_template(frame, matcher, names)
        # 按钮亮度: 模板没找到位置就跳过这条信号 (brightness=0, 反正 t_hit=False 已挡)
        brightness = self._check_button_brightness(frame, t_match)
        close_x = self._count_yolo(yolo_detections, "close_x")
        action_btn = self._count_yolo(yolo_detections, "action_btn")
        # dialog 类是新训弹窗本体. 模型未训时 _count_yolo 返 0, 整条信号失效不影响判定 — 安全降级.
        dialog = self._count_yolo(yolo_detections, "dialog")
        # lobby 类: lobby_v1 模型 (recall 100% on val + inst-1 外部样本 conf 0.91).
        # 模型未训含 lobby 类时 _count_yolo 返 0, streak 永不增长 → 永远 is_lobby=False — 安全降级.
        yolo_lobby = self._count_yolo(yolo_detections, "lobby")
        has_overlay = self._check_overlay(frame)
        # phash stable 只为日志保留, 不参与判定 (大厅永远在动, 这条永远过不了)
        stable_n, _ = self._check_phash_stable(frame)

        # ─── 主判定 (改 v3): YOLO lobby + 排除信号 + N 帧稳定 ───
        # 旧版 (template 主信号) 因游戏 UI 改版后模板分数永远 < 0.78 而失效.
        # 新版以 YOLO lobby_v1 (训 100% recall) 为主: 连续 N 帧 (默认 2) 都
        # "看到角色 + 没 X + 没 dialog + 没 overlay" 才判大厅.
        # 关弹窗瞬间 (~0.3-0.7s 动画) yolo 偶尔"看穿"半透明弹窗看到角色,
        # 单帧就信会跳到 P3a 找不到组队按钮; 2 帧门槛过滤掉这类闪现.
        now = time.time()
        if now - self._last_check_ts > self._streak_reset_gap_s:
            # 跨长 gap = 不同 phase 的同一 detector 重用, 旧 streak 失效
            self._lobby_streak = 0
        self._last_check_ts = now

        # 候选: 这一帧符合"安静大厅"特征
        candidate = (
            yolo_lobby >= 1
            and close_x == 0
            and dialog == 0
            and not has_overlay
        )
        if candidate:
            self._lobby_streak += 1
        else:
            self._lobby_streak = 0

        is_lobby = self._lobby_streak >= self.lobby_streak_required

        # template + brightness 只用于 note 显示, 不再参与判定
        if is_lobby:
            note = (f"OK (yolo_lobby={yolo_lobby} streak={self._lobby_streak}/"
                    f"{self.lobby_streak_required}, template={t_conf:.2f} br={brightness:.0f})")
        else:
            reasons = [f"streak={self._lobby_streak}/{self.lobby_streak_required}"]
            if yolo_lobby == 0:
                reasons.append("yolo_lobby=0")
            if close_x > 0:
                reasons.append(f"close_x={close_x}")
            if dialog > 0:
                reasons.append(f"dialog={dialog}")
            if has_overlay:
                reasons.append("overlay")
            note = ", ".join(reasons)

        return LobbyQuadResult(
            is_lobby=is_lobby,
            template_hit=t_hit,
            template_conf=t_conf,
            template_name=t_name,
            yolo_close_x_count=close_x,
            yolo_action_btn_count=action_btn,
            yolo_dialog_count=dialog,
            yolo_lobby_count=yolo_lobby,
            lobby_streak=self._lobby_streak,
            lobby_streak_required=self.lobby_streak_required,
            has_overlay=has_overlay,
            phash_stable_frames=stable_n,
            phash_stable_required=self.stable_required,
            button_brightness=brightness,
            note=note,
        )
