"""Stage 1 — 声明式弹窗清理配置 + 防连发 tracker.

设计参考 ALAS pattern (调研结论, 见 docs/POPUP_AND_RECOVERY_PLAN.md):
  1. 每种弹窗一个独立 PopupSpec, 不用通用 close_x 检测器 (防误伤自己 UI)
  2. anchor 用 OCR 关键词 (跨电脑稳, 比模板匹配可靠)
  3. co_occurrence 共现校验 (防半渲染帧误判)
  4. min_interval_s 防同一弹窗短时间内连发 (网络抖动场景)
  5. fatal_threshold 致命弹窗升级 (网络真崩 / 账号被挤 → 抛 PopupFatalEscalation)

调用入口: backend/automation/popup_dismiss.py:dismiss_known_popups()
跨 phase 共享 tracker, 实例级 (ctx.runner._popup_tracker).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal, Optional


@dataclass
class PopupSpec:
    """弹窗识别 + 处理配置.

    位置无关设计 (重要):
      所有弹窗类型由 OCR 关键词区分, **不再用静态 ROI**. 流程:
        1. YOLO 检测画面所有 dialog bbox (可能多个)
        2. 对每个 dialog bbox 内部 OCR
        3. 按 KNOWN_POPUPS 顺序匹配 anchor_keywords + co_occurrence
        4. 第一个命中 → tap dismiss_value 所在位置 (也来自 OCR)

      跨电脑 / 不同分辨率 / 弹窗位置变化 都不用改 spec, YOLO bbox 自适应.
    """
    name: str                                 # 唯一 ID, 写入决策 outcome / 日志
    anchor_keywords: list[str]                # OCR 任一命中即可识别为这种弹窗
    co_occurrence: list[str] = field(default_factory=list)  # 必须共现的其他文字 (防误判)
    dismiss_kind: Literal["ocr_tap"] = "ocr_tap"
    dismiss_value: str = ""                   # OCR 找这个文字, tap 它的位置
    phases_active: list[str] = field(default_factory=lambda: ["all"])
    excluded_phases: list[str] = field(default_factory=list)
    # 防连发 / 致命
    min_interval_s: float = 0.8               # 同一弹窗两次 dismiss 最小间隔
    fatal_threshold: int = 0                  # 滑窗内触发 N 次升级 fatal; 0=不触发
    fatal_window_s: float = 60.0              # 滑窗大小


KNOWN_POPUPS: list[PopupSpec] = [
    # ─── 1a: QQ 好友邀请 (来自好友 + 拒绝/接受 + 右上 X) ───
    PopupSpec(
        name="friend_invite_qq",
        anchor_keywords=["来自好友"],
        co_occurrence=["申请入队"],
        dismiss_value="拒绝",
        min_interval_s=0.8,
    ),
    # ─── 1b: 推荐组邀请 (推荐组 / 模拟器在线玩家 + 不了 2s/邀请组队) ───
    PopupSpec(
        name="friend_invite_recommend",
        anchor_keywords=["推荐组", "模拟器在线玩家"],
        co_occurrence=["邀请组队"],
        dismiss_value="不了",
        min_interval_s=0.8,
    ),
    # ─── 2: 网络异常 (中央"提示"对话框 + 取消/确定) ───
    PopupSpec(
        name="network_error",
        anchor_keywords=["无法连接", "检查你的网络"],
        co_occurrence=["提示"],
        dismiss_value="确定",
        min_interval_s=2.0,                   # 网络弹窗稍长间隔, 防疯狂连点
        fatal_threshold=5,                    # 60s 内 5 次 → 网络真崩
        fatal_window_s=60.0,
    ),
    # ─── 3: 账号被挤 (中央"提示"对话框 + 确定 → 退到登录页) ───
    PopupSpec(
        name="account_squeezed",
        anchor_keywords=["账号在别处登录"],
        co_occurrence=["提示"],
        dismiss_value="确定",
        min_interval_s=2.0,
        fatal_threshold=1,                    # 1 次即 fatal — 当前 session 必死
        fatal_window_s=60.0,
    ),
    # 待补 (用户后续采样): 7 周年 / 收藏等级 / 公告 / 闪退恢复 / 战令 / 签到
]


class DismissalTracker:
    """每个 instance 一个, 跟踪 spec 的 dismiss 历史 (cooldown + fatal 计数).

    挂在 runner._popup_tracker 上, 跨 phase 持续累计 (网络异常 P3 + P5 累计才能触发 fatal).
    """

    def __init__(self):
        self._last_dismissed: dict[str, float] = {}
        self._dismissal_history: dict[str, list[float]] = {}

    def can_dismiss(self, spec: PopupSpec) -> bool:
        """是否过了冷却期."""
        last = self._last_dismissed.get(spec.name, 0.0)
        return time.time() - last >= spec.min_interval_s

    def record(self, spec: PopupSpec) -> None:
        """记录一次 dismiss + 修剪窗口外历史."""
        now = time.time()
        self._last_dismissed[spec.name] = now
        hist = self._dismissal_history.setdefault(spec.name, [])
        hist.append(now)
        cutoff = now - spec.fatal_window_s
        self._dismissal_history[spec.name] = [t for t in hist if t >= cutoff]

    def is_fatal(self, spec: PopupSpec) -> bool:
        """滑窗内是否超过 fatal_threshold."""
        if spec.fatal_threshold <= 0:
            return False
        hist = self._dismissal_history.get(spec.name, [])
        return len(hist) >= spec.fatal_threshold

    def count_in_window(self, spec: PopupSpec) -> int:
        return len(self._dismissal_history.get(spec.name, []))


class PopupFatalEscalation(Exception):
    """致命弹窗触发, 上层 phase handler catch 后走恢复流程.

    Stage 1: 抛出后只打日志 + 写决策, phase return FAIL outcome="popup_fatal".
    Stage 3: phase 接 recovery 入口 (退游戏 / 重启 / re-login 等) 时再细化.
    """

    def __init__(self, spec_name: str, count: int):
        self.spec_name = spec_name
        self.count = count
        super().__init__(
            f"Popup {spec_name} fatal: triggered {count} times in window")
