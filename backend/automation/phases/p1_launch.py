"""
v3 P1 — 启动游戏并等待出现可交互 UI.

判定: 用 ScreenClassifier 单帧分类, ScreenKind ∈ {LOBBY, POPUP, LOGIN}
任一即 NEXT (UI 出来了, 后续 P2 / P3 处理). LOADING / UNKNOWN 等下一帧.

完全不跑 OCR (12 实例并发会爆 CPU 240s/分钟).
P1 不区分大厅 / 登录页 / 弹窗 — 那是 P2 的活.
"""

from __future__ import annotations

import logging
import os
import time as _time

import numpy as np

from ..phase_base import PhaseHandler, PhaseResult, PhaseStep, RunContext
from ..recorder_helpers import record_signal_tier

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Motion Gate (2026-05-10): 智能化 P1 — 画面没变就跳过 yolo classify
#
# 之前 P1 是轮询: 每 0.5s 跑 yolo + 模板, 不管画面变没变. 浪费 90% CPU,
# 而且公告渲染前的 loading 画面也在跑 detection (永远 LOADING/UNKNOWN).
#
# 现在: 每帧算 64-bit dHash, 跟上次比 hamming distance.
# - dist <= 4: 画面没变, 复用上次 ScreenKind, 5ms 跳过
# - dist > 4:  画面变化 (loading→popup, popup→lobby), 跑完整 classify
#
# 安全:
# - 纯 numpy, 不调 cv2 (上次 motion gate 用 cv2.absdiff 怀疑引发 native crash)
# - 不动 ldopengl ndarray view, 第一步就 .copy() 出来
# - 完整 try/except, 异常 fallback 走原 classify
# - env GAMEBOT_P1_MOTION_GATE=0 一键关
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_DHASH_GRID_H = 8       # 8 行 → 64 bit
_DHASH_GRID_W = 9       # 9 列, 横向相邻比较 → 8 列 bool


def _motion_thresh() -> int:
    """优先 runtime_profile.p1_motion_threshold, fallback 4."""
    try:
        from ..runtime_profile import get_profile
        return get_profile().p1_motion_threshold
    except Exception:
        return 4


def _fast_dhash(shot: np.ndarray) -> int:
    """64-bit dHash, 纯 numpy, 不调 cv2.
    shot: BGR ndarray (h, w, 3). 假设 ldopengl 出 (540, 960, 3).
    """
    # 先 ascontiguousarray + .copy() 防 view 悬挂
    a = np.ascontiguousarray(shot)
    h, w = a.shape[:2]
    # BGR → 灰度 (Rec. 601 weights, 跟 cv2.cvtColor 等价)
    gray = (a[..., 0].astype(np.float32) * 0.114 +
            a[..., 1].astype(np.float32) * 0.587 +
            a[..., 2].astype(np.float32) * 0.299)
    # 缩到 8x9 (block-mean pooling, 不用 cv2.resize)
    h_step = h // _DHASH_GRID_H
    w_step = w // _DHASH_GRID_W
    h_trim = h_step * _DHASH_GRID_H
    w_trim = w_step * _DHASH_GRID_W
    g = gray[:h_trim, :w_trim].reshape(_DHASH_GRID_H, h_step, _DHASH_GRID_W, w_step).mean(axis=(1, 3))
    # 横向相邻比较 → 8x8 bool → 64-bit int
    diff = (g[:, :-1] > g[:, 1:]).astype(np.uint8)
    bits = np.packbits(diff.flatten()).tobytes()
    return int.from_bytes(bits, 'big')


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count('1')


class P1LaunchHandler(PhaseHandler):
    """启动游戏 (am start) → 等任意可交互 UI 出现."""

    name = "P1"
    name_cn = "启动游戏"
    description = "am start 拉起游戏, 等任意可交互 UI 出现. 不区分大厅/弹窗/登录页, 都让 P2 处理."
    flow_steps = [
        "enter: am start 启动游戏包",
        "每帧 0.5s: 抓帧 + 跑识别 (2026-05-10 改: 1.5s→0.5s 减少公告出现到检测的延迟)",
        "ScreenClassifier 分类: LOBBY/POPUP/LOGIN 任一 → NEXT (UI 出来了)",
        "LOADING/UNKNOWN → 等下一帧",
        "180 轮 (~90s) 都没命中 → FAIL",
    ]
    max_rounds = 450              # 450 × 0.2s = 90s timeout
    round_interval_s = 0.2        # 0.5s → 0.2s: P1 用 daemon cache 后, 单 round < 5ms, 频率可以高

    async def enter(self, ctx: RunContext) -> None:
        await super().enter(ctx)
        # 第一次进入时启动游戏 (后续 RETRY 不再重启)
        if ctx.runner is not None:
            try:
                from ..single_runner import GAME_PACKAGE
                await ctx.device.start_app(GAME_PACKAGE)
                logger.info("[P1] am start 游戏")
            except Exception as e:
                logger.warning(f"[P1] start_app 异常: {e}")

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        shot = ctx.current_shot
        if shot is None:
            return PhaseStep(PhaseResult.RETRY)

        # 顺手采集训练数据
        try:
            from ..screenshot_collector import collect as _collect
            _collect(shot, tag="launch_game")
        except Exception:
            pass

        rnd = ctx.phase_round
        matcher = ctx.matcher
        decision = ctx.current_decision

        from ..screen_classifier import ScreenKind, classify_from_frame, _POPUP_CLASSES, _LOBBY_CLASS, POPUP_MIN_CONF, LOBBY_MIN_CONF

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Vision Daemon 接入 (2026-05-10): P1 不再自己跑 classify (1s/round)
        # 直接读 daemon cache 的 yolo dets, 见 popup/lobby 立刻退 P1
        # 跟用户"简单脚本"思路一致: daemon 持续看, 业务见到立刻反应
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if os.environ.get("GAMEBOT_VISION_DAEMON", "1") != "0":
            try:
                from ..vision_daemon import VisionDaemon
                slot = VisionDaemon.get().snapshot(ctx.instance_idx, max_age_ms=800)
                if slot is not None:
                    dets = slot.yolo_dets
                    has_popup = any(
                        getattr(d, "name", "") in _POPUP_CLASSES
                        and getattr(d, "conf", 0.0) >= POPUP_MIN_CONF
                        for d in dets
                    )
                    has_lobby = any(
                        getattr(d, "name", "") == _LOBBY_CLASS
                        and getattr(d, "conf", 0.0) >= LOBBY_MIN_CONF
                        for d in dets
                    )
                    if has_popup or has_lobby:
                        kind = ScreenKind.POPUP if has_popup else ScreenKind.LOBBY
                        ctx._p1_last_kind = kind
                        record_signal_tier(decision, name="daemon", hit=True, tier_idx=2,
                                           note=f"daemon kind={kind.name}", duration_ms=1.0)
                        return PhaseStep(
                            PhaseResult.NEXT,
                            note=f"R{rnd}: daemon kind={kind.name} → done",
                            outcome_hint=f"daemon_{kind.value}",
                        )
                    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    # cache 有但没 popup/lobby = 还在 LOADING.
                    # 关键: 业务侧不跑 yolo 兜底! daemon 是唯一 yolo 源.
                    # 否则 6 实例 P1 + daemon + 6 P2 = 13 路 yolo 抢 GPU lock,
                    # 卡 25-30s 在 P1_R1 → R2 之间 (实测).
                    # 一键回退: env GAMEBOT_P1_YOLO_FALLBACK=1
                    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    if os.environ.get("GAMEBOT_P1_YOLO_FALLBACK", "0") != "1":
                        if rnd % 20 == 0:
                            logger.info(f"[P1] R{rnd}: daemon 等待 popup/lobby (cache hit, dets={len(dets)})")
                        return PhaseStep(PhaseResult.RETRY, outcome_hint="daemon_no_target")
            except Exception as e:
                logger.debug(f"[P1] daemon snapshot err: {e}")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Motion Gate (2026-05-10): 画面没变就跳过 yolo classify
        # 之前 P1 18 round × 1s = 18s 全跑 yolo, 现在 loading 期间 5ms 跳过
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        _gate_enabled = os.environ.get("GAMEBOT_P1_MOTION_GATE", "1") == "1"
        if _gate_enabled:
            try:
                _t_gate = _time.perf_counter()
                cur_ph = _fast_dhash(shot)
                last_ph = getattr(ctx, '_p1_last_phash', None)
                last_kind = getattr(ctx, '_p1_last_kind', None)
                gate_ms = (_time.perf_counter() - _t_gate) * 1000

                if last_ph is not None and last_kind in (ScreenKind.LOADING, ScreenKind.UNKNOWN):
                    dist = _hamming(cur_ph, last_ph)
                    if dist <= _motion_thresh():
                        # 画面没变 + 上次也是 LOADING/UNKNOWN → 跳过 classify, 短 wait 后再来
                        record_signal_tier(decision, name="motion_gate", hit=False, tier_idx=0,
                                           note=f"画面静止 dist={dist}, 复用 {last_kind.name}",
                                           duration_ms=gate_ms)
                        if rnd % 10 == 0:
                            logger.info(f"[P1] R{rnd}: motion gate 跳过 (画面静止, kind={last_kind.name})")
                        return PhaseStep(PhaseResult.RETRY, outcome_hint=f"motion_static_{last_kind.value}")

                # 缓存 phash 给下次比 (无论是否变化, 都更新基线)
                ctx._p1_last_phash = cur_ph
            except Exception as e:
                logger.debug(f"[P1] motion gate err (fallback to classify): {e}")

        # P1.5 ScreenClassifier — 单帧分类 {LOBBY, POPUP, LOGIN, LOADING, UNKNOWN}.
        t0 = _time.perf_counter()
        kind = await classify_from_frame(shot, ctx.yolo, matcher)
        ms = (_time.perf_counter() - t0) * 1000

        # 缓存 kind 给 motion gate 下次复用
        ctx._p1_last_kind = kind

        if kind not in (ScreenKind.LOADING, ScreenKind.UNKNOWN):
            record_signal_tier(decision, name="classifier", hit=True, tier_idx=2,
                               note=f"ScreenKind={kind.name}",
                               duration_ms=ms)
            return PhaseStep(
                PhaseResult.NEXT,
                note=f"R{rnd}: 分类={kind.name} → done",
                outcome_hint=f"classify_{kind.value}",
            )

        record_signal_tier(decision, name="classifier", hit=False, tier_idx=2,
                           note=f"ScreenKind={kind.name} (loading/unknown, 等下一帧)",
                           duration_ms=ms)

        if rnd % 5 == 0:
            logger.info(f"[P1] R{rnd}: 等待中 (kind={kind.name})")
        return PhaseStep(PhaseResult.RETRY, outcome_hint=f"waiting_{kind.value}")
