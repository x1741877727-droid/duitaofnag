"""
大厅判定 — 纯 YOLO 信号 + N 帧 streak 防闪现.

判定:
    candidate = yolo_lobby >= 1 AND close_x == 0 AND dialog == 0
    is_lobby  = streak >= lobby_streak_required (默认 2 帧, GAMEBOT_LOBBY_STREAK 可调)

设计取舍:
- 旧版有 template / brightness / overlay / phash 四路兜底, 已删:
    - template (lobby_start_btn): 游戏 UI 改版后 conf 永远 < 0.78, 形同失效
    - button_brightness / has_overlay: 模拟器画质波动大, 假阳/假阴都见过
    - phash_stable: 大厅永远在动 (背景浮动 NPC), 这条永远过不了
- 关弹窗瞬间 (~0.3-0.7s 动画) yolo 偶尔"看穿"半透明遮罩看到角色, streak=2 过滤.
- lobby_v1 模型未训该类时 _count_yolo 返 0, streak 永不增长 → 永远 is_lobby=False (安全降级).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass


@dataclass
class LobbyQuadResult:
    """大厅判定结果. 类名保留 Quad 兼容历史 import."""
    is_lobby: bool
    yolo_close_x_count: int
    yolo_action_btn_count: int
    yolo_dialog_count: int
    yolo_lobby_count: int
    lobby_streak: int
    lobby_streak_required: int
    note: str = ""


class LobbyQuadDetector:
    """有状态: 跨帧维护 streak. 每个 runner 实例一个 detector.

    用法:
        det = LobbyQuadDetector()
        for round in main_loop:
            shot = await screenshot()
            yolo_dets = yolo.detect(shot)
            r = det.check(shot, matcher, yolo_dets)
            if r.is_lobby:
                done()
    """

    def __init__(self):
        self.lobby_streak_required: int = int(os.environ.get("GAMEBOT_LOBBY_STREAK", "2"))
        self._lobby_streak: int = 0
        self._last_check_ts: float = 0.0
        # 跨长 gap 重置 (phase 切换间隔 >5s, 老 streak 不能跨用)
        self._streak_reset_gap_s: float = 5.0

    def reset(self) -> None:
        self._lobby_streak = 0
        self._last_check_ts = 0.0

    @staticmethod
    def _count_yolo(detections: list, target_cls: str) -> int:
        if not detections:
            return 0
        # Detection.cls 是 int class id, Detection.name 才是字符串.
        return sum(1 for d in detections if getattr(d, "name", "") == target_cls)

    def check(
        self,
        frame,
        matcher,
        yolo_detections: list,
        template_names=None,
    ) -> LobbyQuadResult:
        """frame / matcher / template_names 仅为兼容旧签名, 实际只用 yolo_detections."""
        del frame, matcher, template_names  # 未使用

        close_x = self._count_yolo(yolo_detections, "close_x")
        action_btn = self._count_yolo(yolo_detections, "action_btn")
        dialog = self._count_yolo(yolo_detections, "dialog")
        yolo_lobby = self._count_yolo(yolo_detections, "lobby")

        now = time.time()
        if now - self._last_check_ts > self._streak_reset_gap_s:
            self._lobby_streak = 0
        self._last_check_ts = now

        candidate = yolo_lobby >= 1 and close_x == 0 and dialog == 0
        if candidate:
            self._lobby_streak += 1
        else:
            self._lobby_streak = 0

        is_lobby = self._lobby_streak >= self.lobby_streak_required

        if is_lobby:
            note = f"OK (yolo_lobby={yolo_lobby} streak={self._lobby_streak}/{self.lobby_streak_required})"
        else:
            reasons = [f"streak={self._lobby_streak}/{self.lobby_streak_required}"]
            if yolo_lobby == 0:
                reasons.append("yolo_lobby=0")
            if close_x > 0:
                reasons.append(f"close_x={close_x}")
            if dialog > 0:
                reasons.append(f"dialog={dialog}")
            note = ", ".join(reasons)

        return LobbyQuadResult(
            is_lobby=is_lobby,
            yolo_close_x_count=close_x,
            yolo_action_btn_count=action_btn,
            yolo_dialog_count=dialog,
            yolo_lobby_count=yolo_lobby,
            lobby_streak=self._lobby_streak,
            lobby_streak_required=self.lobby_streak_required,
            note=note,
        )
