"""
v3 P5 — 等待目标真人入队 (重构 v2: 模板匹配 anchor 找新位置).

业务上下文:
  P3a 队长建队 → P3b 队员加入 → P4 队长选地图 → 队伍 N 人, 等真人入队.
  业务前提: 机器先入队 (baseline), 玩家后加入 (新位置).

为什么放弃 OCR 找位置:
  - 昵称 OCR 漂移 (text 飘忽 / cx 微抖)
  - 玩家可隐藏性别 → 性别图标方案死路
  - 玩家可用纯符号 / emoji 昵称 → OCR 抓不到
  - 模仿 ID (日文相似字符) → text 接近 baseline 误判

为什么用模板匹配 slot 旁的展开/退出按钮:
  - 每个 slot 必有 (PUBG UI 强制), 不受昵称/性别/隐私设置影响
  - UI alpha 层在 3D 之上, 不被烟花/角色挡住
  - 形状/大小/颜色固定 (LDPlayer 9 NORM 960x540)
  - 单帧 confidence 0.7 阈值 + 多帧累积稳定

依赖 ROI (config/roi.yaml, 用户在 OCR 调试页拖):
  team_lobby_area         模板匹配搜索范围 (4 角色昵称区域大块)
  player_card_info_btn    小卡片 "信息" 按钮 (tap 进详细面板)
  player_card_id          详细面板 "编号" 10 位数字 (核心 OCR)
  player_card_close_btn   详细面板右上角 X 关闭 (永远不用 ADB back)
  player_card_kick_area   大区域盖小卡片底部+右侧弹出菜单 (OCR 找 "移出队伍")
  player_card_more_btn    小卡片底部 "更多" 按钮 (非好友先点)

依赖模板 (用户在 templates 调试页采集):
  team_slot_btn_collapse  队友 slot 旁 "展开" 按钮 (16x16 - 24x24 px)
  team_slot_btn_exit      自己 slot 旁 "退出" 按钮 (形状不同, 同尺寸)

ID 三向比对 (终极保险):
  - got_id == expected_id  → DONE ✓
  - got_id ≠ expected_id   → 视为捣乱者, 自动踢人, 继续等
  (不维护机器白名单, 因为机器账号会换; 业务前提保证 baseline 时全是机器,
   后续新位置都是真人)

出口:
  DONE  匹配 expected_id
  FAIL  240s 超时 / 入口非大厅 / 必需 ROI/模板 缺失
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional

import cv2
import numpy as np

from ..phase_base import PhaseHandler, PhaseResult, PhaseStep, RunContext

logger = logging.getLogger(__name__)


# ─────────── 配置常量 ───────────

# 模板名 (旧路径模板, 仅 fallback 保留 - 主路径已切到 YOLO)
SLOT_BTN_TEMPLATES = ["team_slot_btn_collapse", "team_slot_btn_exit"]

# YOLO 类别配置 (v3 双信号架构):
#   lobby:          slot 占用真相源 (score 0.85+ 极稳, 计数 / baseline / 新位置)
#   slot_nameplate: tap target 主信号 (准但偶漏, 配 fallback 保险)
#   collapse/exit:  tap target fallback 来源 (在 nameplate 漏识时推算位置)
LOBBY_YOLO_CLASS = "lobby"
NAMEPLATE_YOLO_CLASS = "slot_nameplate"
BTN_YOLO_CLASSES = {"team_slot_btn_collapse", "team_slot_btn_exit"}
LOBBY_CONF_THRESHOLD = 0.7       # lobby 普遍 0.85-0.95, 0.7 留余量
NAMEPLATE_CONF_THRESHOLD = 0.4   # nameplate 0.5-0.8, 0.4 容忍边缘 case
BTN_CONF_THRESHOLD = 0.3         # btn 仅作 fallback, 给低阈宽容

# ROI 名 (用户用 OCR 调试页拖 + 保存到 config/roi.yaml)
ROI_LOBBY_AREA = "team_lobby_area"
ROI_INFO_BTN = "player_card_info_btn"
ROI_CARD_ID = "player_card_id"
ROI_CLOSE_BTN = "player_card_close_btn"
ROI_KICK_AREA = "player_card_kick_area"
ROI_MORE_BTN = "player_card_more_btn"

# 自适应轮询: idle 模式省 CPU, active 模式 (有候选) 抢确认
POLL_INTERVAL_IDLE_S = 1.5       # 默认空闲间隔
POLL_INTERVAL_ACTIVE_S = 0.5     # 检测到新位置后切到这个抢确认
TIMEOUT_S = 240.0

# 帧差自适应等待 (替代固定 sleep): 截图后 phash 比对前帧, 连续 N 帧"画面稳定"立即继续
STABLE_PHASH_DIST = 4            # 汉明距离 < 此值 = "画面稳定"
STABLE_FRAMES = 2                # 要求连续 N 帧稳定
STABLE_POLL_MS = 100             # 每 100ms 检测一次
WAIT_TIMEOUT_CARD_MS = 800       # tap slot 后等卡片动画上限 (实测多 300-500ms 完成)
WAIT_TIMEOUT_PANEL_MS = 800      # tap info 后等详细面板上限
WAIT_TIMEOUT_CLOSE_MS = 500      # tap close 后等回退上限
WAIT_TIMEOUT_MENU_MS = 500       # tap more 后等菜单上限

EXPECTED_ID_LEN = 10
ID_PATTERN = re.compile(r"\d{10}")

# 模板匹配 (YOLO 不可用时的最终 fallback)
TEMPLATE_THRESHOLD = 0.6
NMS_DISTANCE_PX = 30
NEW_POSITION_MIN_DIST_PX = 50    # 新 lobby cx 距 baseline 最小距离, 才算"新"
BASELINE_SAMPLES = 2             # 优化: 3→2 帧 (lobby 稳, 单帧已够)
BASELINE_INTERVAL_S = 0.3        # 优化: 0.4→0.3s
CONFIRM_FRAMES = 2               # 连续 N 帧确认新位置 (防误触发)

# OCR
ENTRY_GATE_MAX_ATTEMPTS = 8
ENTRY_GATE_INTERVAL_S = 0.5
VERIFY_OCR_RETRY = 2             # tap 后 OCR 找不到 ID 的 retry 次数

# 模板匹配 / 截图归一化尺寸 (跟 ScreenMatcher 一致, LDPlayer 9 native)
NORM_W = 960
NORM_H = 540


class P5WaitPlayersHandler(PhaseHandler):
    name = "P5"
    name_cn = "等待玩家"
    description = (
        "等指定真人 (expected_id, 10 位数字) 入队. 模板匹配 slot 旁展开/退出按钮 "
        "找 baseline 之外的新位置, tap 弹卡片 → 进详细面板 → OCR 编号 → 三向比对. "
        "不匹配自动踢人继续等. 超时 240s → FAIL."
    )
    flow_steps = [
        "入口守门 (PopupCloser 清弹窗 + 大厅检测)",
        "校验 ctx.expected_id 是 10 位数字",
        "Baseline: 多帧模板匹配 slot 按钮位置, 取最多命中数作锚",
        "轮询: 每 1.5s 模板匹配, 找距 baseline > 50px 新位置",
        "连续 2 帧出现新位置 → 确认真人 → tap (cx, cy)",
        "进卡片: tap 信息按钮 → OCR 编号 ROI 提 10 位数字 → tap 关闭按钮",
        "got_id == expected → DONE",
        "got_id ≠ expected → 重新 tap → OCR 找 '移出队伍' → 找不到先点 '更多' → tap 移出 → 继续等",
        "超时 240s → FAIL",
    ]
    max_rounds = 1                 # handler 一次性跑完整轮询循环
    round_interval_s = 1.0

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        runner = ctx.runner
        if runner is None:
            return PhaseStep(PhaseResult.FAIL, note="ctx.runner 未注入")

        from ..recorder_helpers import record_signal_tier
        from ..screen_classifier import ScreenKind
        from ..popup_closer import PopupCloser
        decision = ctx.current_decision

        # ─── 入口守门 ───
        kind = await PopupCloser.wait_for_lobby_clearing_popups(
            ctx, max_attempts=ENTRY_GATE_MAX_ATTEMPTS, interval_s=ENTRY_GATE_INTERVAL_S)
        if kind != ScreenKind.LOBBY:
            record_signal_tier(decision, name="入口守门", hit=False, tier_idx=2,
                               note=f"P5 入口非 LOBBY (kind={kind.name})")
            return PhaseStep(PhaseResult.FAIL,
                             note=f"P5 入口守门失败 (kind={kind.name})",
                             outcome_hint=f"entry_not_lobby_{kind.value}")
        record_signal_tier(decision, name="入口守门", hit=True, tier_idx=2,
                           note="P5 入口确认 LOBBY")

        # ─── 校验 expected_id ───
        eid = (ctx.expected_id or "").strip()
        if not (len(eid) == EXPECTED_ID_LEN and eid.isdigit()):
            record_signal_tier(decision, name="参数校验", hit=False, tier_idx=2,
                               note=f"expected_id 无效: {eid!r}")
            return PhaseStep(PhaseResult.FAIL,
                             note=f"expected_id 必须是 {EXPECTED_ID_LEN} 位数字, 实际={eid!r}",
                             outcome_hint="invalid_expected_id")
        logger.info(f"[P5 inst{ctx.instance_idx}] 等待玩家 expected_id={eid} timeout={TIMEOUT_S}s")
        record_signal_tier(decision, name="参数校验", hit=True, tier_idx=2,
                           note=f"expected_id={eid}")

        # ─── 必需 ROI / 模板检查 (启动前防卡死) ───
        missing = self._check_required_resources(runner)
        if missing:
            record_signal_tier(decision, name="资源校验", hit=False, tier_idx=2,
                               note=f"必需资源缺失: {missing}")
            return PhaseStep(PhaseResult.FAIL,
                             note=f"P5 必需 ROI/模板缺失: {missing}, 配置后重启 backend",
                             outcome_hint="resource_missing")

        # ─── Baseline (多帧最大命中数) ───
        baseline, baseline_yolo_dets = await self._build_baseline_with_dets(ctx)
        if baseline is None or len(baseline) == 0:
            record_signal_tier(decision, name="baseline", hit=False, tier_idx=3,
                               note="baseline 模板匹配 0 命中")
            return PhaseStep(PhaseResult.FAIL,
                             note="baseline 模板 0 命中 (检查模板采集 + ROI 拖框)",
                             outcome_hint="baseline_empty")
        ctx.team_slot_baseline = list(baseline)  # 复用现有字段存位置 list
        logger.info(f"[P5 inst{ctx.instance_idx}] baseline {len(baseline)} 个 slot 位置: {baseline}")
        # 拿 baseline 那帧的截图给标注图用 (主决策的 input_image 已是当时帧)
        baseline_shot = ctx.current_shot
        record_signal_tier(decision, name="baseline", hit=True, tier_idx=3,
                           note=f"baseline {len(baseline)} 位置: {baseline}",
                           yolo_dets=baseline_yolo_dets,
                           screenshot=baseline_shot)

        # ─── Polling Loop (自适应间隔: idle / active) ───
        start_ts = time.perf_counter()
        confirm_streak: dict[tuple, int] = {}  # (cx, cy) → 连续命中帧数
        loop_round = 0
        poll_interval = POLL_INTERVAL_IDLE_S  # 默认空闲间隔, 有候选时切到 active

        # 取消令牌 (前端"停止测试" → POST /api/runner/cancel → CANCEL_FLAG["v"]=True)
        # P5 max_rounds=1 不在 _run_v3_phase 的 round loop 里检查, 所以要这里查.
        try:
            from ...api_runner_test import CANCEL_FLAG
        except Exception:
            CANCEL_FLAG = None

        def _cancelled() -> bool:
            return CANCEL_FLAG is not None and bool(CANCEL_FLAG.get("v"))

        # 实时决策: 每轮轮询都写一条独立的 decision (跟主决策并列, 用 phash 当 round_idx 区分),
        # 用户能在档案里实时看到每次检测到了什么 / 是否新位置 / 何时 tap.
        from ..decision_log import get_recorder
        from ..adb_lite import phash as _phash
        from ..popup_dismiss import dismiss_known_popups
        from ..popup_specs import PopupFatalEscalation
        recorder = get_recorder()

        while True:
            if _cancelled():
                record_signal_tier(decision, name="取消", hit=False, tier_idx=4,
                                   note="收到取消信号, 主动中止 P5")
                return PhaseStep(PhaseResult.FAIL,
                                 note="P5 被用户取消",
                                 outcome_hint="cancelled")
            elapsed = time.perf_counter() - start_ts
            if elapsed >= TIMEOUT_S:
                record_signal_tier(decision, name="超时", hit=False, tier_idx=4,
                                   note=f"{TIMEOUT_S}s 内未等到目标玩家")
                return PhaseStep(PhaseResult.FAIL,
                                 note=f"等待超时 {TIMEOUT_S}s",
                                 outcome_hint="wait_timeout")

            # 分段 sleep, 每 200ms 检查 cancel (避免在长睡眠里卡死)
            slept = 0.0
            while slept < poll_interval:
                if _cancelled():
                    break
                chunk = min(0.2, poll_interval - slept)
                await asyncio.sleep(chunk)
                slept += chunk
            if _cancelled():
                continue  # 顶层 while 会再判断 _cancelled() 然后退出
            loop_round += 1

            # 单次截图 + 单次 YOLO → 同时拿 lobby + nameplate + btn (省 2 次推理)
            shot = await runner.adb.screenshot()
            if shot is None:
                logger.warning(f"[P5 inst{ctx.instance_idx}] R{loop_round} 截图失败, 跳过")
                continue

            # ─── Stage 1 弹窗清理 (跨 phase 通用, 优先级最高) ───
            # YOLO 全类一次推理, 拿全部 dets 给弹窗清理复用 (省一次推理)
            yolo = getattr(runner, "yolo_dismisser", None)
            all_dets = []
            if yolo is not None and yolo.is_available():
                try:
                    all_dets = await asyncio.to_thread(yolo.detect, shot) or []
                except Exception as e:
                    logger.debug(f"[P5] YOLO detect 失败: {e}")
                    all_dets = []
            try:
                dismissed = await dismiss_known_popups(
                    ctx, yolo_dets=all_dets, pre_shot=shot,
                    current_phase="P5",
                )
                if dismissed:
                    logger.info(f"[P5 inst{ctx.instance_idx}] R{loop_round} 弹窗清理: {dismissed}, 跳过本轮 baseline")
                    continue
            except PopupFatalEscalation as e:
                # 致命弹窗 (network 60s/5次 或 account_squeezed 1次)
                # Stage 1: 直接 FAIL phase, outcome 标 popup_fatal_*
                # Stage 3 后: 接 recovery 入口 (退游戏 / 重启 / re-login)
                logger.warning(f"[P5 inst{ctx.instance_idx}] FATAL popup: {e}")
                return PhaseStep(PhaseResult.FAIL,
                                 note=f"P5 致命弹窗 {e.spec_name} (触发 {e.count} 次)",
                                 outcome_hint=f"popup_fatal_{e.spec_name}")

            # 弹窗清理用 all_dets, 业务 filter 走 _yolo_detect_all (按各类阈值过滤)
            yolo_result = await self._yolo_detect_all(ctx, shot)
            if yolo_result is None:
                current = await self._match_slot_buttons(ctx)
                np_dets, btn_dets = [], []
                lobby_dets = []
            else:
                lobby_dets, np_dets, btn_dets = yolo_result
                current = _dedup_positions(
                    [(d.cx, d.cy) for d in lobby_dets], NMS_DISTANCE_PX)

            if current is None:
                continue

            # ─── 写实时轮询决策 (1 帧 = 1 决策, 立即可见) ───
            # round_idx 用 1000+loop_round, 跟主决策 (round=1) 区分
            poll_dec = await asyncio.to_thread(
                recorder.new_decision, ctx.instance_idx, "P5", 1000 + loop_round)
            try:
                ph = _phash(shot)
                ph_str = f"0x{int(ph):016x}" if ph else ""
                poll_dec.set_input(shot, ph_str, q=70)
            except Exception:
                pass

            # 队员退队: current 数 < baseline → 更新 baseline 防误触发
            if len(current) < len(baseline):
                logger.info(f"[P5 inst{ctx.instance_idx}] R{loop_round} 检测到 slot 减少 "
                            f"{len(baseline)} → {len(current)}, 更新 baseline")
                record_signal_tier(poll_dec, name=f"R{loop_round} 退队", hit=False, tier_idx=3,
                                   note=f"slot 减少 {len(baseline)} → {len(current)}, 重建 baseline",
                                   yolo_dets=list(lobby_dets) + list(np_dets) + list(btn_dets),
                                   screenshot=shot)
                await asyncio.to_thread(poll_dec.finalize, "member_left",
                                        f"slot {len(baseline)}→{len(current)}")
                baseline = current
                ctx.team_slot_baseline = list(baseline)
                confirm_streak.clear()
                poll_interval = POLL_INTERVAL_IDLE_S  # 退队 → 回 idle 模式
                continue

            # 找新位置: 距 baseline 任何位置 > 阈值
            new_positions = [
                (cx, cy) for (cx, cy) in current
                if all(_dist((cx, cy), bp) > NEW_POSITION_MIN_DIST_PX for bp in baseline)
            ]
            if not new_positions:
                # 无变化 — 写一条 "no_change" 决策让用户能看到 phase 在跑
                confirm_streak.clear()
                poll_interval = POLL_INTERVAL_IDLE_S  # 没候选 → idle
                record_signal_tier(poll_dec, name=f"R{loop_round} 轮询", hit=False, tier_idx=3,
                                   note=f"slot {len(current)} 占用, 无新人 (elapsed {elapsed:.0f}s)",
                                   yolo_dets=list(lobby_dets) + list(np_dets) + list(btn_dets),
                                   screenshot=shot)
                await asyncio.to_thread(poll_dec.finalize, "no_change",
                                        f"R{loop_round} {len(current)} slot")
                continue

            # 有候选 → 切到 active 模式抢确认
            poll_interval = POLL_INTERVAL_ACTIVE_S

            # 多帧确认
            np_pos = new_positions[0]  # 取第一个 (通常只有一个)
            confirm_streak[np_pos] = confirm_streak.get(np_pos, 0) + 1
            if confirm_streak[np_pos] < CONFIRM_FRAMES:
                logger.debug(f"[P5 inst{ctx.instance_idx}] R{loop_round} 新位置 {np_pos} "
                             f"streak {confirm_streak[np_pos]}/{CONFIRM_FRAMES}")
                record_signal_tier(poll_dec, name=f"R{loop_round} 新位置(待确认)", hit=False, tier_idx=3,
                                   note=f"候选 {np_pos} streak {confirm_streak[np_pos]}/{CONFIRM_FRAMES}",
                                   yolo_dets=list(lobby_dets) + list(np_dets) + list(btn_dets),
                                   screenshot=shot)
                await asyncio.to_thread(poll_dec.finalize, "candidate_pending",
                                        f"streak {confirm_streak[np_pos]}/{CONFIRM_FRAMES}")
                continue

            # 候选确认 — 这条 poll_dec 记录确认事件 (后续 verify/kick 会再写自己的决策)
            record_signal_tier(poll_dec, name=f"R{loop_round} 新位置(确认)", hit=True, tier_idx=4,
                               note=f"候选 {np_pos} 连续 {CONFIRM_FRAMES} 帧, 进 verify",
                               yolo_dets=list(lobby_dets) + list(np_dets) + list(btn_dets),
                               screenshot=shot)
            await asyncio.to_thread(poll_dec.finalize, "candidate_confirmed",
                                    f"new pos {np_pos}")

            lobby_cx, lobby_cy = np_pos
            # 解析 tap target (nameplate 优先 → btn 反推 → lobby 几何兜底)
            tap_x, tap_y = self._resolve_tap_target(lobby_cx, lobby_cy, np_dets, btn_dets)
            logger.info(f"[P5 inst{ctx.instance_idx}] R{loop_round} 真人入队 lobby=({lobby_cx},{lobby_cy}) "
                        f"tap_target=({tap_x},{tap_y})")

            # ─── Verify (tap → 信息按钮 → OCR 编号 → 关闭) ───
            got_id = await self._verify_player_id(ctx, tap_x, tap_y,
                                                   loop_round=loop_round)
            if got_id is None:
                record_signal_tier(decision, name=f"Verify·R{loop_round}", hit=False, tier_idx=4,
                                   note=f"lobby=({lobby_cx},{lobby_cy}) OCR 找不到 10 位编号 → 视为干扰")
                # 把这个 lobby 位置加 baseline 防反复触发
                baseline = baseline + [(lobby_cx, lobby_cy)]
                ctx.team_slot_baseline = list(baseline)
                confirm_streak.clear()
                continue

            if got_id == eid:
                record_signal_tier(decision, name=f"Verify·R{loop_round}", hit=True, tier_idx=4,
                                   note=f"lobby=({lobby_cx},{lobby_cy}) ID={got_id} ✓ 匹配 expected={eid}")
                return PhaseStep(PhaseResult.DONE,
                                 note=f"目标玩家入队 ID={got_id} 位置=({lobby_cx},{lobby_cy}) 耗时 {elapsed:.1f}s",
                                 outcome_hint="match_target")

            # ─── Kick (got_id ≠ expected, 视为捣乱者) ───
            logger.warning(f"[P5 inst{ctx.instance_idx}] R{loop_round} lobby=({lobby_cx},{lobby_cy}) "
                           f"ID={got_id} ≠ expected={eid}, 踢出")
            ctx.kicked_ids.add(got_id)
            kicked = await self._kick_player(ctx, tap_x, tap_y,
                                              loop_round=loop_round)
            record_signal_tier(decision, name=f"Kick·R{loop_round}", hit=kicked, tier_idx=5,
                               note=f"lobby=({lobby_cx},{lobby_cy}) got_id={got_id} "
                                    f"expected={eid} kicked={kicked}")

            # 重建 baseline (踢成功后队伍少 1 人, baseline 变少)
            # 等队伍 UI 刷新, 用帧差 polling 替代固定 1s sleep
            await self._wait_for_screen_stable(runner, timeout_ms=1500)
            new_baseline = await self._match_slot_buttons(ctx) or list(baseline)
            baseline = new_baseline
            ctx.team_slot_baseline = list(baseline)
            confirm_streak.clear()
            poll_interval = POLL_INTERVAL_IDLE_S  # kick 后回 idle 等下一个真人

    # ─────────── helpers ───────────

    @staticmethod
    def _check_required_resources(runner) -> list[str]:
        """检查必需资源 (主路径只要 ROI + YOLO 模型, 不再依赖 slot_btn 模板)."""
        missing = []
        try:
            from ..roi_config import all_names as _all_roi
            avail_roi = set(_all_roi())
            for need in (ROI_LOBBY_AREA, ROI_INFO_BTN, ROI_CARD_ID, ROI_CLOSE_BTN):
                if need not in avail_roi:
                    missing.append(f"ROI:{need}")
        except Exception as e:
            missing.append(f"ROI 加载异常:{e}")

        # YOLO 模型必须可用 (主路径). 不再 fail on slot_btn 模板缺失.
        yolo = getattr(runner, "yolo_dismisser", None)
        if yolo is None or not yolo.is_available():
            missing.append("YOLO:模型未加载")
        return missing

    async def _build_baseline(self, ctx: RunContext) -> Optional[list[tuple[int, int]]]:
        """多帧采样建 baseline (lobby 框中心点 list). 取命中数最多那帧."""
        best: list[tuple[int, int]] = []
        for i in range(BASELINE_SAMPLES):
            current = await self._match_slot_buttons(ctx)
            if current and len(current) > len(best):
                best = current
            if i < BASELINE_SAMPLES - 1:
                await asyncio.sleep(BASELINE_INTERVAL_S)
        return best if best else None

    async def _build_baseline_with_dets(self, ctx: RunContext):
        """多帧采样 baseline, 同时保留命中那帧的 YOLO Detection list (供决策档案标注用).

        返回 (positions list, yolo_dets list).
        """
        runner = ctx.runner
        best_positions: list[tuple[int, int]] = []
        best_dets: list = []  # YOLO Detection list (lobby + nameplate + btn 三类合并, 给前端画框)
        for i in range(BASELINE_SAMPLES):
            shot = await runner.adb.screenshot()
            if shot is not None:
                yolo_result = await self._yolo_detect_all(ctx, shot)
                if yolo_result is not None:
                    lobby_dets, np_dets, btn_dets = yolo_result
                    positions = _dedup_positions(
                        [(d.cx, d.cy) for d in lobby_dets], NMS_DISTANCE_PX)
                    if len(positions) > len(best_positions):
                        best_positions = positions
                        best_dets = list(lobby_dets) + list(np_dets) + list(btn_dets)
                else:
                    # YOLO 不可用, fallback 模板
                    positions = await self._match_slot_buttons(ctx)
                    if positions and len(positions) > len(best_positions):
                        best_positions = positions
            if i < BASELINE_SAMPLES - 1:
                await asyncio.sleep(BASELINE_INTERVAL_S)
        return (best_positions if best_positions else None), best_dets

    @staticmethod
    async def _wait_for_screen_stable(runner, timeout_ms: int = 800,
                                       stable_frames: int = STABLE_FRAMES,
                                       poll_ms: int = STABLE_POLL_MS) -> bool:
        """帧差检测画面"稳定下来" (替代固定 sleep).

        每 poll_ms 截图 phash 比上一帧, 连续 stable_frames 帧距离 < STABLE_PHASH_DIST
        即视为画面稳定, 立即返回 True. timeout_ms 上限保底, 超时返回 False.

        典型用法: tap UI 元素后等动画完成, 实测可省 300-500ms (动画通常 300-500ms,
        固定 sleep 800ms 多余 300-500ms).
        """
        from ..adb_lite import phash, phash_distance
        elapsed_ms = 0
        last_h: Optional[int] = None
        consec_stable = 0
        while elapsed_ms < timeout_ms:
            shot = await runner.adb.screenshot()
            if shot is not None:
                h = phash(shot)
                if last_h is not None and phash_distance(h, last_h) < STABLE_PHASH_DIST:
                    consec_stable += 1
                    if consec_stable >= stable_frames:
                        return True
                else:
                    consec_stable = 0
                last_h = h
            await asyncio.sleep(poll_ms / 1000.0)
            elapsed_ms += poll_ms
        return False  # 超时降级为 caller 自行处理 (一般没事, 反正最坏退化到 sleep 等价)

    async def _yolo_detect_all(self, ctx: RunContext, shot):
        """单次 YOLO 推理 → 返回 (lobby_dets, nameplate_dets, btn_dets) 三类过滤后 list.

        各类已按对应 conf 阈值过滤 + ROI 内点过滤 (如 team_lobby_area 配了).
        模型不可用返回 None (caller 走模板兜底).
        """
        runner = ctx.runner
        yolo = getattr(runner, "yolo_dismisser", None)
        if yolo is None or not yolo.is_available():
            return None

        try:
            dets = await asyncio.to_thread(yolo.detect, shot)
        except Exception as e:
            logger.warning(f"[P5] YOLO detect 失败: {e}")
            return None

        # ROI 限制 (如配了 team_lobby_area, 只保留中心点在 ROI 内的)
        from ..roi_config import get as _roi_get, all_names as _all_roi
        bx1 = by1 = 0
        bx2 = shot.shape[1]
        by2 = shot.shape[0]
        if ROI_LOBBY_AREA in set(_all_roi()):
            try:
                x1, y1, x2, y2, _ = _roi_get(ROI_LOBBY_AREA)
                h, w = shot.shape[:2]
                bx1 = int(w * x1); by1 = int(h * y1)
                bx2 = int(w * x2); by2 = int(h * y2)
            except Exception:
                pass

        def _in_roi(d) -> bool:
            return bx1 <= d.cx <= bx2 and by1 <= d.cy <= by2

        lobby_dets = [d for d in dets
                      if d.name == LOBBY_YOLO_CLASS and d.conf >= LOBBY_CONF_THRESHOLD
                      and _in_roi(d)]
        np_dets = [d for d in dets
                   if d.name == NAMEPLATE_YOLO_CLASS and d.conf >= NAMEPLATE_CONF_THRESHOLD
                   and _in_roi(d)]
        btn_dets = [d for d in dets
                    if d.name in BTN_YOLO_CLASSES and d.conf >= BTN_CONF_THRESHOLD
                    and _in_roi(d)]
        return lobby_dets, np_dets, btn_dets

    @staticmethod
    def _resolve_tap_target(lobby_cx: int, lobby_cy: int,
                             nameplate_dets, btn_dets) -> tuple[int, int]:
        """给定一个 lobby slot 的中心点, 算 tap target (永远不返回 None, 双轨兜底):

        优先级:
          1. 找 cx 距 lobby < 50 的 nameplate → 用 nameplate (cx, cy) (UI 交互区中心)
          2. nameplate 漏识 → 找 cx 距 lobby < 50 的 btn → tap (btn.cx - 50, btn.cy)
             (nameplate 永远在 btn 左侧 ~50px, 反推过去落在昵称文字)
          3. 都没 → 用 lobby 几何兜底 (cx, cy + h*0.15) (大致落在 nameplate 区域)

        永远不让 nameplate 漏识阻塞业务流.
        """
        # 优先级 1: nameplate
        for d in nameplate_dets:
            if abs(d.cx - lobby_cx) < 50:
                return (d.cx, d.cy)

        # 优先级 2: btn 反推
        for d in btn_dets:
            if abs(d.cx - lobby_cx) < 50:
                return (d.cx - 50, d.cy)

        # 优先级 3: lobby 几何兜底 (cx 不变, cy 往下 15% 落在 nameplate 大致区)
        return (lobby_cx, lobby_cy)  # caller 会传 lobby_cy, 上层可加 offset

    async def _yolo_detect_slot_buttons(self, ctx: RunContext,
                                         shot) -> Optional[list[tuple[int, int]]]:
        """[兼容旧 API] 用 lobby 类作为 slot 占用真相源, 返回 lobby 中心点 list.

        v3 双信号: lobby 计数 (稳) → caller 用 cx 区分 baseline / 新位置.
        Tap target 由 _resolve_tap_target 单独算 (在 verify/kick 入口).
        """
        result = await self._yolo_detect_all(ctx, shot)
        if result is None:
            return None  # 模型不可用, fallback 模板

        lobby_dets, _, _ = result
        if not lobby_dets:
            # 模型可用但本帧没 lobby 命中. 仍 return [] 而不是 None — None 会触发模板 fallback,
            # 但模板 fallback 现在没意义 (主路径就是 lobby). 直接返回空 list 让上层判定"队伍空".
            logger.debug(f"[P5] YOLO 0 lobby 命中 (队伍空 / 不在大厅?)")
            return []

        positions = [(d.cx, d.cy) for d in lobby_dets]
        positions = _dedup_positions(positions, NMS_DISTANCE_PX)
        logger.info(f"[P5] YOLO 命中 {len(positions)} 个 lobby slot: {positions}")
        return positions

    async def _match_slot_buttons(self, ctx: RunContext) -> Optional[list[tuple[int, int]]]:
        """找 team_lobby_area 内所有 slot 按钮位置 (去重后).

        优先级 1: YOLO 推理 (训练后准确率最高)
        优先级 2: 模板匹配 (YOLO 未训练 / 不可用时兜底, threshold 0.6)
        """
        runner = ctx.runner
        shot = await runner.adb.screenshot()
        if shot is None:
            return None

        # ─── 优先 YOLO ───
        yolo_hits = await self._yolo_detect_slot_buttons(ctx, shot)
        if yolo_hits is not None:
            return yolo_hits

        # ─── 兜底: 模板匹配 ───
        from ..roi_config import get as _roi_get
        try:
            x1, y1, x2, y2, _ = _roi_get(ROI_LOBBY_AREA)
        except Exception as e:
            logger.error(f"[P5] ROI {ROI_LOBBY_AREA} 拿不到: {e}")
            return None

        # 归一化截图到 NORM
        if shot.shape[1] != NORM_W or shot.shape[0] != NORM_H:
            shot_norm = cv2.resize(shot, (NORM_W, NORM_H))
        else:
            shot_norm = shot

        # ROI 像素 bbox
        px1 = int(NORM_W * x1)
        py1 = int(NORM_H * y1)
        px2 = int(NORM_W * x2)
        py2 = int(NORM_H * y2)
        crop = shot_norm[py1:py2, px1:px2]
        if crop.size == 0:
            return None

        all_hits: list[tuple[int, int]] = []
        for tmpl_name in SLOT_BTN_TEMPLATES:
            hits = _match_all_in_crop(runner.matcher, crop, tmpl_name,
                                      threshold=TEMPLATE_THRESHOLD,
                                      nms_dist=NMS_DISTANCE_PX)
            # 转回全屏坐标
            for cx, cy in hits:
                all_hits.append((cx + px1, cy + py1))

        # 跨模板去重 (展开/退出按钮可能尺寸接近, 同一 slot 别两个模板都命中)
        return _dedup_positions(all_hits, NMS_DISTANCE_PX)

    async def _run_verify_step(self, ctx: RunContext, round_idx: int,
                                step_name: str, note: str, *,
                                tap_xy: Optional[tuple] = None,
                                ocr_roi_name: Optional[str] = None,
                                wait_timeout_ms: int = WAIT_TIMEOUT_CARD_MS,
                                outcome: Optional[str] = None,
                                hit: bool = True,
                                pre_shot=None) -> None:
        """通用子步骤执行器 — 把"截 pre-tap 图 → 写决策 → tap → 等画面稳定"4 步封装.

        标准用法 (verify/kick/未来其他需要分步决策的 phase):
            await self._run_verify_step(
                ctx, base_round+N, "Step·名字", "说明文字",
                tap_xy=(x, y),                # 给了就 tap, 没给就只写决策不动作
                ocr_roi_name="player_card_X", # 给了就在标注图画 ROI 黄框
                wait_timeout_ms=800,          # tap 后帧差等待上限
            )

        强类型保护 — 内部不用 `arr or fallback` (避免 numpy ndarray truthy 陷阱),
        所有 None check 显式 `is not None`.
        """
        runner = ctx.runner
        # 1) Pre-action 截图 (caller 没传就现拍)
        if pre_shot is None:
            pre_shot = await runner.adb.screenshot()
        # 2) ROI 比例坐标 (从 ROI 名字解析, 给标注图画黄框用)
        ocr_roi_pct = None
        if ocr_roi_name:
            try:
                from ..roi_config import get as _roi_get, all_names as _all_roi
                if ocr_roi_name in set(_all_roi()):
                    x1, y1, x2, y2, _ = _roi_get(ocr_roi_name)
                    ocr_roi_pct = [x1, y1, x2, y2]
            except Exception:
                pass
        # 3) Outcome 自动名 (给了 tap 就 _planned, 没给就 _checked)
        if outcome is None:
            slug = step_name.lower().replace("·", "_").replace(" ", "_")
            outcome = f"{slug}_planned" if tap_xy is not None else f"{slug}_checked"
        # 4) 写决策 (调既有 _record_step_decision, 它已 handle ndarray None check)
        await self._record_step_decision(
            ctx, round_idx, step_name, note, outcome=outcome,
            screenshot=pre_shot, hit=hit,
            ocr_roi_pct=ocr_roi_pct, tap_xy=tap_xy)
        # 5) 执行 tap + 等画面稳定 (如果给了 tap_xy)
        if tap_xy is not None:
            await runner.adb.tap(int(tap_xy[0]), int(tap_xy[1]))
            await self._wait_for_screen_stable(runner, timeout_ms=wait_timeout_ms)

    async def _record_step_decision(self, ctx: RunContext, round_idx: int,
                                     step_name: str, note: str, outcome: str,
                                     screenshot=None, hit: bool = True,
                                     ocr_roi_pct: Optional[list] = None,
                                     tap_xy: Optional[tuple] = None) -> None:
        """写一条独立 step 决策 (verify/kick 内部子步骤用, 让用户实时看到每步结果).

        ocr_roi_pct: 比例坐标 [x1,y1,x2,y2] (0-1), 给了就在 OCR ROI 标注图画 ROI 矩形
        tap_xy: (x, y) 像素坐标, 给了就在标注图上画红圈 + tap 标签 (前端"标注"面板显示)
        """
        from ..decision_log import get_recorder
        from ..adb_lite import phash as _phash
        from ..recorder_helpers import record_signal_tier
        recorder = get_recorder()
        dec = await asyncio.to_thread(
            recorder.new_decision, ctx.instance_idx, "P5", round_idx)
        if screenshot is not None:
            try:
                ph = _phash(screenshot)
                ph_str = f"0x{int(ph):016x}" if ph else ""
                dec.set_input(screenshot, ph_str, q=70)
            except Exception:
                pass
        record_signal_tier(dec, name=step_name, hit=hit, tier_idx=4, note=note)
        # 画 OCR ROI 矩形 (如果给了) → ocr_annot.jpg → 前端 "OCR ROI" 面板显示
        if ocr_roi_pct is not None and screenshot is not None and dec.tiers:
            try:
                h, w = screenshot.shape[:2]
                roi_pix = [int(w*ocr_roi_pct[0]), int(h*ocr_roi_pct[1]),
                           int(w*ocr_roi_pct[2]), int(h*ocr_roi_pct[3])]
                dec.save_ocr_roi(dec.tiers[-1], screenshot, roi=roi_pix, hits=[])
            except Exception as e:
                logger.debug(f"[P5] save_ocr_roi err: {e}")
        # 画 tap 圆圈 (如果给了 tap_xy) → tap_annot.jpg → 前端"标注"面板显示
        if tap_xy is not None and screenshot is not None:
            try:
                tx, ty = int(tap_xy[0]), int(tap_xy[1])
                dec.set_tap(tx, ty, method=step_name, screenshot=screenshot)
            except Exception as e:
                logger.debug(f"[P5] set_tap err: {e}")
        await asyncio.to_thread(dec.finalize, outcome, note[:80])

    async def _verify_player_id(self, ctx: RunContext, cx: int, cy: int,
                                 loop_round: int = 0) -> Optional[str]:
        """tap (cx, cy) → 小卡片 → OCR 找信息按钮 → tap 信息 → 详细面板 →
           OCR 编号 → 关面板. 返回 10 位 ID (str) 或 None.

        4 个 step 全用 `_run_verify_step` helper, 不再手写 "截图+决策+tap+等"
        4 步重复代码. 每个决策 input.jpg = **tap 之前画面** + 红圈/黄框.
        """
        runner = ctx.runner
        base_round = 2000 + loop_round * 10

        # ─── Step 1: tap slot ───
        await self._run_verify_step(
            ctx, base_round + 1, "VerifyStep1·tap_slot",
            f"R{loop_round} 准备 tap slot ({cx},{cy}) → 期望弹小卡片",
            tap_xy=(cx, cy), wait_timeout_ms=WAIT_TIMEOUT_CARD_MS)

        # ─── Step 2: OCR 找 "信息" 按钮 + tap ───
        info_xy = await self._find_info_button(ctx)
        if info_xy is None:
            await self._run_verify_step(
                ctx, base_round + 2, "VerifyStep2·tap_info",
                f"R{loop_round} OCR 在 ROI {ROI_INFO_BTN} 没找到 '信息' 文字 → 放弃",
                ocr_roi_name=ROI_INFO_BTN, hit=False,
                outcome="info_btn_not_found")
            return None
        await self._run_verify_step(
            ctx, base_round + 2, "VerifyStep2·tap_info",
            f"R{loop_round} OCR 找到 '信息' @ {info_xy}, 准备 tap",
            tap_xy=info_xy, ocr_roi_name=ROI_INFO_BTN,
            wait_timeout_ms=WAIT_TIMEOUT_PANEL_MS)

        # ─── Step 3: OCR ID (retry) ───
        ocr = runner.ocr_dismisser
        got_id: Optional[str] = None
        last_shot = None
        last_hits: list = []
        for attempt in range(VERIFY_OCR_RETRY + 1):
            shot = await runner.adb.screenshot()
            if shot is None:
                continue
            last_shot = shot
            try:
                hits = await asyncio.to_thread(ocr._ocr_roi_named, shot, ROI_CARD_ID)
                last_hits = hits
            except Exception as e:
                logger.warning(f"[P5] OCR {ROI_CARD_ID} 失败 (try {attempt}): {e}")
                continue
            id_str = _extract_player_id(hits)
            if id_str:
                got_id = id_str
                break
            if attempt < VERIFY_OCR_RETRY:
                await asyncio.sleep(0.3)

        # 写 OCR 决策 (无论成败) — 画面 = 详细面板, 黄框 = card_id ROI
        ocr_summary = ",".join(getattr(h, "text", "") for h in (last_hits or [])[:5])
        ocr_summary = ocr_summary[:80] if ocr_summary else "(无文字命中)"
        await self._run_verify_step(
            ctx, base_round + 3, "VerifyStep3·ocr_id",
            f"R{loop_round} OCR {ROI_CARD_ID} 命中: '{ocr_summary}' → got_id={got_id}",
            ocr_roi_name=ROI_CARD_ID, hit=bool(got_id),
            outcome="ocr_done" if got_id else "ocr_no_10digit",
            pre_shot=last_shot)

        # ─── Step 4: 关闭详细面板 ───
        close_xy = await self._roi_center_xy(ctx, ROI_CLOSE_BTN)
        if close_xy is None:
            close_xy = (20, 20)  # 兜底 tap 左上空白
        await self._run_verify_step(
            ctx, base_round + 4, "VerifyStep4·close",
            f"R{loop_round} 关闭详细面板 @ {close_xy}",
            tap_xy=close_xy, wait_timeout_ms=WAIT_TIMEOUT_CLOSE_MS)

        return got_id

    async def _kick_player(self, ctx: RunContext, cx: int, cy: int,
                            loop_round: int = 0) -> bool:
        """踢人. tap (cx, cy) 重新弹小卡片 → OCR 找 '移出队伍' → 找不到先 tap 更多 → tap 移出.
        返回是否成功执行 tap '移出队伍'.

        全部子步骤走 `_run_verify_step` helper, 跟 verify 同一个接口.
        """
        base_round = 3000 + loop_round * 10

        # ─── Step 1: tap slot 重新弹小卡片 ───
        await self._run_verify_step(
            ctx, base_round + 1, "KickStep1·tap_slot",
            f"R{loop_round} 准备重新 tap slot ({cx},{cy}) → 弹小卡片踢人",
            tap_xy=(cx, cy), wait_timeout_ms=WAIT_TIMEOUT_CARD_MS)

        # ─── Step 2: OCR 找 "移出队伍" 按钮 ───
        kick_xy = await self._find_kick_button(ctx)
        if kick_xy is None:
            # 非好友默认状态: 先 tap "更多" 展开菜单
            more_xy = await self._roi_center_xy(ctx, ROI_MORE_BTN)
            if more_xy is None:
                await self._run_verify_step(
                    ctx, base_round + 2, "KickStep2·find_kick",
                    f"R{loop_round} OCR 没找到 '移出队伍' + ROI {ROI_MORE_BTN} 也没配 → 放弃",
                    ocr_roi_name=ROI_KICK_AREA, hit=False,
                    outcome="kick_btn_unfindable")
                await self._close_card(ctx)
                return False
            await self._run_verify_step(
                ctx, base_round + 2, "KickStep2·tap_more",
                f"R{loop_round} 没直接看到'移出', 准备 tap 更多 ROI 中心 {more_xy}",
                tap_xy=more_xy, ocr_roi_name=ROI_KICK_AREA,
                wait_timeout_ms=WAIT_TIMEOUT_MENU_MS)
            kick_xy = await self._find_kick_button(ctx)
            if kick_xy is None:
                await self._run_verify_step(
                    ctx, base_round + 3, "KickStep3·find_kick_after_more",
                    f"R{loop_round} tap 更多后仍没找到 '移出队伍' → 放弃",
                    ocr_roi_name=ROI_KICK_AREA, hit=False,
                    outcome="kick_btn_unfindable_after_more")
                await self._close_card(ctx)
                return False

        # ─── Step 4: tap "移出队伍" ───
        await self._run_verify_step(
            ctx, base_round + 4, "KickStep4·tap_kick",
            f"R{loop_round} 准备 tap '移出队伍' @ {kick_xy}",
            tap_xy=kick_xy, ocr_roi_name=ROI_KICK_AREA,
            wait_timeout_ms=WAIT_TIMEOUT_CARD_MS)
        return True

    async def _find_info_button(self, ctx: RunContext) -> Optional[tuple[int, int]]:
        """在 player_card_info_btn ROI 内 OCR 找 '信息' 文字, 返回那个 hit 的 (cx, cy).

        正确做法: ROI 是搜索范围, OCR 找到"信息"文字 → 返回它的精确中心,
        不是直接 tap ROI 中心 (会偏到旁边的 赠送礼物/移出队伍/转让队长 上).
        """
        runner = ctx.runner
        from ..roi_config import all_names as _all_roi
        if ROI_INFO_BTN not in set(_all_roi()):
            logger.error(f"[P5] ROI {ROI_INFO_BTN} 未配置")
            return None
        shot = await runner.adb.screenshot()
        if shot is None:
            return None
        ocr = runner.ocr_dismisser
        try:
            hits = await asyncio.to_thread(ocr._ocr_roi_named, shot, ROI_INFO_BTN)
        except Exception as e:
            logger.warning(f"[P5] OCR {ROI_INFO_BTN} 失败: {e}")
            return None
        # 严格匹配 "信息" — 跟其他按钮 (赠送礼物/移出队伍/转让队长) 区分
        for h in hits:
            t = (getattr(h, "text", "") or "").strip()
            if t == "信息" or "信息" in t and "礼物" not in t:
                return (h.cx, h.cy)
        return None

    async def _find_kick_button(self, ctx: RunContext) -> Optional[tuple[int, int]]:
        """OCR player_card_kick_area 区域找含 '移出'+'队伍' 的 hit, 返回 (cx, cy)."""
        runner = ctx.runner
        from ..roi_config import all_names as _all_roi
        if ROI_KICK_AREA not in set(_all_roi()):
            logger.error(f"[P5] ROI {ROI_KICK_AREA} 未配置")
            return None
        shot = await runner.adb.screenshot()
        if shot is None:
            return None
        ocr = runner.ocr_dismisser
        try:
            hits = await asyncio.to_thread(ocr._ocr_roi_named, shot, ROI_KICK_AREA)
        except Exception as e:
            logger.warning(f"[P5] OCR {ROI_KICK_AREA} 失败: {e}")
            return None
        for h in hits:
            if "移出" in h.text and "队伍" in h.text:
                return (h.cx, h.cy)
        return None

    async def _close_card(self, ctx: RunContext) -> None:
        """关任何卡片. 优先 close_btn ROI, 兜底 tap 左上角. 帧差等关闭动画完成."""
        runner = ctx.runner
        close_xy = await self._roi_center_xy(ctx, ROI_CLOSE_BTN)
        if close_xy is not None:
            await runner.adb.tap(*close_xy)
        else:
            await runner.adb.tap(20, 20)
        await self._wait_for_screen_stable(runner, timeout_ms=WAIT_TIMEOUT_CLOSE_MS)

    @staticmethod
    async def _roi_center_xy(ctx: RunContext, roi_name: str) -> Optional[tuple[int, int]]:
        """拿 ROI 中心点的全屏像素坐标 (基于当前截图尺寸)."""
        from ..roi_config import get as _roi_get, all_names as _all_roi
        if roi_name not in set(_all_roi()):
            return None
        try:
            x1, y1, x2, y2, _ = _roi_get(roi_name)
        except Exception:
            return None
        runner = ctx.runner
        shot = ctx.current_shot
        if shot is None:
            shot = await runner.adb.screenshot()
        if shot is None:
            return None
        h, w = shot.shape[:2]
        cx = int((x1 + x2) / 2 * w)
        cy = int((y1 + y2) / 2 * h)
        return (cx, cy)


# ─────────── module-private helpers ───────────


def _dist(a: tuple[int, int], b: tuple[int, int]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _dedup_positions(positions: list[tuple[int, int]],
                     min_dist: float) -> list[tuple[int, int]]:
    """按距离去重 (NMS), 保留先出现的."""
    out: list[tuple[int, int]] = []
    for p in positions:
        if all(_dist(p, o) > min_dist for o in out):
            out.append(p)
    return out


def _match_all_in_crop(matcher, crop: np.ndarray, template_name: str,
                       threshold: float, nms_dist: int) -> list[tuple[int, int]]:
    """cv2.matchTemplate + NMS 找 crop 内所有命中位置, 返回 [(cx, cy)] 在 crop 局部坐标系.

    复用 ScreenMatcher 已加载的模板 (matcher._templates).
    """
    if template_name not in getattr(matcher, "_templates", {}):
        return []
    tdata = matcher._templates[template_name]
    tmpl_bgr = tdata.get("bgr")
    if tmpl_bgr is None:
        return []

    # 转 gray (跟 ScreenMatcher 一致, NORM_CCOEFF 灰度)
    if len(crop.shape) == 3:
        crop_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    else:
        crop_gray = crop
    if len(tmpl_bgr.shape) == 3:
        tmpl_gray = cv2.cvtColor(tmpl_bgr, cv2.COLOR_BGR2GRAY)
    else:
        tmpl_gray = tmpl_bgr

    th, tw = tmpl_gray.shape
    if crop_gray.shape[0] < th or crop_gray.shape[1] < tw:
        return []

    result = cv2.matchTemplate(crop_gray, tmpl_gray, cv2.TM_CCOEFF_NORMED)
    hits: list[tuple[int, int]] = []
    # 迭代取最大值 + NMS 抑制周围
    while True:
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val < threshold:
            break
        x, y = max_loc
        cx = x + tw // 2
        cy = y + th // 2
        hits.append((cx, cy))
        # 抑制周围 nms_dist 范围
        x1 = max(0, x - nms_dist)
        y1 = max(0, y - nms_dist)
        x2 = min(result.shape[1], x + tw + nms_dist)
        y2 = min(result.shape[0], y + th + nms_dist)
        result[y1:y2, x1:x2] = 0
        if len(hits) >= 10:  # 防异常 (一个模板最多 10 个命中)
            break
    return hits


def _extract_player_id(ocr_hits) -> Optional[str]:
    """从 OCR hits 提取连续 10 位数字. 优先单 hit 内匹配, 兜底拼接所有 text 再找."""
    for h in ocr_hits:
        m = ID_PATTERN.search(h.text)
        if m:
            return m.group(0)
    joined = "".join(h.text for h in ocr_hits)
    m = ID_PATTERN.search(joined)
    return m.group(0) if m else None
