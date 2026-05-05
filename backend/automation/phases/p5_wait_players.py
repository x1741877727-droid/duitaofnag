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

# 模板名 (用户用 templates 调试页采集 + 保存到 fixtures/templates/)
SLOT_BTN_TEMPLATES = ["team_slot_btn_collapse", "team_slot_btn_exit"]

# YOLO 类别名 (训练时类别名要跟这俩一致, 用 YoloLabeler 标注时选这俩 label).
# 跟模板名一致, 避免混淆.
SLOT_BTN_YOLO_CLASSES = {"team_slot_btn_collapse", "team_slot_btn_exit"}
YOLO_CONF_THRESHOLD = 0.5  # YOLO conf 阈值 (训练后实测调)

# ROI 名 (用户用 OCR 调试页拖 + 保存到 config/roi.yaml)
ROI_LOBBY_AREA = "team_lobby_area"
ROI_INFO_BTN = "player_card_info_btn"
ROI_CARD_ID = "player_card_id"
ROI_CLOSE_BTN = "player_card_close_btn"
ROI_KICK_AREA = "player_card_kick_area"
ROI_MORE_BTN = "player_card_more_btn"

POLL_INTERVAL_S = 1.5
TIMEOUT_S = 240.0
TAP_CARD_WAIT_MS = 800           # tap slot 后等小卡片动画
TAP_INFO_WAIT_MS = 800           # tap 信息按钮后等详细面板
TAP_CLOSE_WAIT_MS = 500          # tap 关闭按钮后等回退
TAP_MORE_WAIT_MS = 500           # tap 更多后等弹菜单
TAP_KICK_WAIT_MS = 800           # tap 移出队伍后等关闭

EXPECTED_ID_LEN = 10
ID_PATTERN = re.compile(r"\d{10}")

# 模板匹配 (YOLO 模型未训练时的 fallback)
TEMPLATE_THRESHOLD = 0.6         # 0.6 兼顾召回 + 误命中, 低于 ScreenMatcher 默认 0.75
NMS_DISTANCE_PX = 30             # 同一模板多命中去重距离
NEW_POSITION_MIN_DIST_PX = 50    # 新位置距 baseline 任何位置最小距离, 才算"新"
BASELINE_SAMPLES = 3             # baseline 取多帧
BASELINE_INTERVAL_S = 0.4
CONFIRM_FRAMES = 2               # 连续 N 帧出现新位置才触发

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
        baseline = await self._build_baseline(ctx)
        if baseline is None or len(baseline) == 0:
            record_signal_tier(decision, name="baseline", hit=False, tier_idx=3,
                               note="baseline 模板匹配 0 命中")
            return PhaseStep(PhaseResult.FAIL,
                             note="baseline 模板 0 命中 (检查模板采集 + ROI 拖框)",
                             outcome_hint="baseline_empty")
        ctx.team_slot_baseline = list(baseline)  # 复用现有字段存位置 list
        logger.info(f"[P5 inst{ctx.instance_idx}] baseline {len(baseline)} 个 slot 位置: {baseline}")
        record_signal_tier(decision, name="baseline", hit=True, tier_idx=3,
                           note=f"baseline {len(baseline)} 位置: {baseline}")

        # ─── Polling Loop ───
        start_ts = time.perf_counter()
        confirm_streak: dict[tuple, int] = {}  # (cx, cy) → 连续命中帧数
        loop_round = 0

        while True:
            elapsed = time.perf_counter() - start_ts
            if elapsed >= TIMEOUT_S:
                record_signal_tier(decision, name="超时", hit=False, tier_idx=4,
                                   note=f"{TIMEOUT_S}s 内未等到目标玩家")
                return PhaseStep(PhaseResult.FAIL,
                                 note=f"等待超时 {TIMEOUT_S}s",
                                 outcome_hint="wait_timeout")

            await asyncio.sleep(POLL_INTERVAL_S)
            loop_round += 1
            current = await self._match_slot_buttons(ctx)
            if current is None:
                logger.warning(f"[P5 inst{ctx.instance_idx}] R{loop_round} 截图失败, 跳过")
                continue

            # 队员退队: current 数 < baseline → 更新 baseline 防误触发
            if len(current) < len(baseline):
                logger.info(f"[P5 inst{ctx.instance_idx}] R{loop_round} 检测到 slot 减少 "
                            f"{len(baseline)} → {len(current)}, 更新 baseline")
                baseline = current
                ctx.team_slot_baseline = list(baseline)
                confirm_streak.clear()
                continue

            # 找新位置: 距 baseline 任何位置 > 阈值
            new_positions = [
                (cx, cy) for (cx, cy) in current
                if all(_dist((cx, cy), bp) > NEW_POSITION_MIN_DIST_PX for bp in baseline)
            ]
            if not new_positions:
                confirm_streak.clear()
                continue

            # 多帧确认
            np_pos = new_positions[0]  # 取第一个 (通常只有一个)
            confirm_streak[np_pos] = confirm_streak.get(np_pos, 0) + 1
            if confirm_streak[np_pos] < CONFIRM_FRAMES:
                logger.debug(f"[P5 inst{ctx.instance_idx}] R{loop_round} 新位置 {np_pos} "
                             f"streak {confirm_streak[np_pos]}/{CONFIRM_FRAMES}")
                continue

            cx, cy = np_pos
            logger.info(f"[P5 inst{ctx.instance_idx}] R{loop_round} 真人入队位置 ({cx},{cy})")

            # ─── Verify (tap → 信息按钮 → OCR 编号 → 关闭) ───
            got_id = await self._verify_player_id(ctx, cx, cy)
            if got_id is None:
                record_signal_tier(decision, name=f"Verify·R{loop_round}", hit=False, tier_idx=4,
                                   note=f"({cx},{cy}) OCR 找不到 10 位编号 → 视为干扰")
                # 把这个位置加 baseline 防反复触发
                baseline = baseline + [(cx, cy)]
                ctx.team_slot_baseline = list(baseline)
                confirm_streak.clear()
                continue

            if got_id == eid:
                record_signal_tier(decision, name=f"Verify·R{loop_round}", hit=True, tier_idx=4,
                                   note=f"({cx},{cy}) ID={got_id} ✓ 匹配 expected={eid}")
                return PhaseStep(PhaseResult.DONE,
                                 note=f"目标玩家入队 ID={got_id} 位置=({cx},{cy}) 耗时 {elapsed:.1f}s",
                                 outcome_hint="match_target")

            # ─── Kick (got_id ≠ expected, 视为捣乱者) ───
            logger.warning(f"[P5 inst{ctx.instance_idx}] R{loop_round} ({cx},{cy}) "
                           f"ID={got_id} ≠ expected={eid}, 踢出")
            ctx.kicked_ids.add(got_id)
            kicked = await self._kick_player(ctx, cx, cy)
            record_signal_tier(decision, name=f"Kick·R{loop_round}", hit=kicked, tier_idx=5,
                               note=f"({cx},{cy}) got_id={got_id} expected={eid} kicked={kicked}")

            # 重建 baseline (踢成功后队伍少 1 人, baseline 变少)
            await asyncio.sleep(1.0)
            new_baseline = await self._match_slot_buttons(ctx) or list(baseline)
            baseline = new_baseline
            ctx.team_slot_baseline = list(baseline)
            confirm_streak.clear()

    # ─────────── helpers ───────────

    @staticmethod
    def _check_required_resources(runner) -> list[str]:
        """检查 ROI / 模板都配齐了. 返回缺失列表."""
        missing = []
        try:
            from ..roi_config import all_names as _all_roi
            avail_roi = set(_all_roi())
            for need in (ROI_LOBBY_AREA, ROI_INFO_BTN, ROI_CARD_ID, ROI_CLOSE_BTN):
                if need not in avail_roi:
                    missing.append(f"ROI:{need}")
        except Exception as e:
            missing.append(f"ROI 加载异常:{e}")

        avail_tmpl = set(getattr(runner.matcher, "template_names", []) or [])
        for need in SLOT_BTN_TEMPLATES:
            if need not in avail_tmpl:
                missing.append(f"模板:{need}")
        return missing

    async def _build_baseline(self, ctx: RunContext) -> Optional[list[tuple[int, int]]]:
        """多帧采样, 取命中数最多的那帧位置. 容忍单帧漏识."""
        best: list[tuple[int, int]] = []
        for i in range(BASELINE_SAMPLES):
            current = await self._match_slot_buttons(ctx)
            if current and len(current) > len(best):
                best = current
            if i < BASELINE_SAMPLES - 1:
                await asyncio.sleep(BASELINE_INTERVAL_S)
        return best if best else None

    async def _yolo_detect_slot_buttons(self, ctx: RunContext,
                                         shot) -> Optional[list[tuple[int, int]]]:
        """YOLO 推理找 slot 按钮. 模型未训练 / 不含目标类别 → 返 None (调用方走模板兜底).

        过滤规则:
          - cls.name ∈ SLOT_BTN_YOLO_CLASSES
          - conf ≥ YOLO_CONF_THRESHOLD
          - 中心点在 team_lobby_area ROI 内 (如果 ROI 配了; 没配就不限)
        """
        runner = ctx.runner
        yolo = getattr(runner, "yolo_dismisser", None)
        if yolo is None or not yolo.is_available():
            return None  # 模型没加载, fallback 模板

        try:
            dets = await asyncio.to_thread(yolo.detect, shot)
        except Exception as e:
            logger.warning(f"[P5] YOLO detect 失败: {e}, fallback 模板")
            return None

        # 过滤目标类别 + conf
        slot_dets = [d for d in dets
                     if d.name in SLOT_BTN_YOLO_CLASSES and d.conf >= YOLO_CONF_THRESHOLD]
        if not slot_dets:
            # 模型加载了但本帧没识别到任何 slot_btn (可能模型还没学这俩类别) → 走模板兜底
            # 注意: 真的"无队员"也会 0 命中, 但 baseline 阶段 P3a/P3b 之后至少 1 个 slot
            # 总会被识别. 持续 0 命中说明 YOLO 模型没训这俩类别 → 应当 fallback.
            logger.debug(f"[P5] YOLO 0 slot_btn 命中 (模型可能没训这俩类别), fallback 模板")
            return None

        # 用 ROI 限制 (如果配了 team_lobby_area)
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

        positions: list[tuple[int, int]] = []
        for d in slot_dets:
            if bx1 <= d.cx <= bx2 and by1 <= d.cy <= by2:
                positions.append((d.cx, d.cy))

        # 去重 (NMS)
        positions = _dedup_positions(positions, NMS_DISTANCE_PX)
        logger.info(f"[P5] YOLO 命中 {len(positions)} 个 slot_btn 位置: {positions}")
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

    async def _verify_player_id(self, ctx: RunContext, cx: int, cy: int) -> Optional[str]:
        """tap (cx, cy) → 小卡片 → tap 信息按钮 → 详细面板 → OCR 编号 → 关面板.
        返回 10 位 ID (str) 或 None.
        """
        runner = ctx.runner
        # tap slot
        await runner.adb.tap(cx, cy)
        await asyncio.sleep(TAP_CARD_WAIT_MS / 1000.0)

        # tap "信息" 按钮
        info_xy = await self._roi_center_xy(ctx, ROI_INFO_BTN)
        if info_xy is None:
            return None
        await runner.adb.tap(*info_xy)
        await asyncio.sleep(TAP_INFO_WAIT_MS / 1000.0)

        # OCR 编号 (retry)
        ocr = runner.ocr_dismisser
        got_id: Optional[str] = None
        for attempt in range(VERIFY_OCR_RETRY + 1):
            shot = await runner.adb.screenshot()
            if shot is None:
                continue
            try:
                hits = await asyncio.to_thread(ocr._ocr_roi_named, shot, ROI_CARD_ID)
            except Exception as e:
                logger.warning(f"[P5] OCR {ROI_CARD_ID} 失败 (try {attempt}): {e}")
                continue
            id_str = _extract_player_id(hits)
            if id_str:
                got_id = id_str
                break
            if attempt < VERIFY_OCR_RETRY:
                await asyncio.sleep(0.3)

        # 关详细面板 (永远不用 back)
        close_xy = await self._roi_center_xy(ctx, ROI_CLOSE_BTN)
        if close_xy is not None:
            await runner.adb.tap(*close_xy)
            await asyncio.sleep(TAP_CLOSE_WAIT_MS / 1000.0)
        else:
            # 兜底: tap 屏幕左上角空白处
            await runner.adb.tap(20, 20)
            await asyncio.sleep(TAP_CLOSE_WAIT_MS / 1000.0)

        return got_id

    async def _kick_player(self, ctx: RunContext, cx: int, cy: int) -> bool:
        """踢人. tap (cx, cy) 重新弹小卡片 → OCR 找 '移出队伍' → 找不到先 tap 更多 → tap 移出.
        返回是否成功执行 tap '移出队伍'.
        """
        runner = ctx.runner
        # tap slot 再次弹小卡片
        await runner.adb.tap(cx, cy)
        await asyncio.sleep(TAP_CARD_WAIT_MS / 1000.0)

        kick_xy = await self._find_kick_button(ctx)
        if kick_xy is None:
            # 非好友默认状态: 先点 "更多"
            more_xy = await self._roi_center_xy(ctx, ROI_MORE_BTN)
            if more_xy is None:
                logger.warning(f"[P5] ROI {ROI_MORE_BTN} 没配, 也找不到 '移出队伍', 放弃 kick")
                await self._close_card(ctx)
                return False
            await runner.adb.tap(*more_xy)
            await asyncio.sleep(TAP_MORE_WAIT_MS / 1000.0)
            kick_xy = await self._find_kick_button(ctx)
            if kick_xy is None:
                logger.warning(f"[P5] tap '更多' 后仍没找到 '移出队伍', 放弃 kick")
                await self._close_card(ctx)
                return False

        await runner.adb.tap(*kick_xy)
        await asyncio.sleep(TAP_KICK_WAIT_MS / 1000.0)
        return True

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
        """关任何卡片. 优先 close_btn ROI, 兜底 tap 左上角."""
        runner = ctx.runner
        close_xy = await self._roi_center_xy(ctx, ROI_CLOSE_BTN)
        if close_xy is not None:
            await runner.adb.tap(*close_xy)
        else:
            await runner.adb.tap(20, 20)
        await asyncio.sleep(TAP_CLOSE_WAIT_MS / 1000.0)

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
