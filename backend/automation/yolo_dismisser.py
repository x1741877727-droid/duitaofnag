"""
YOLO 弹窗清理器 — 替代 OcrDismisser 的视觉识别版本

工作原理：
  1. 加载 %APPDATA%\\GameBot\\data\\yolo\\models\\latest.onnx
  2. 每轮截图 → YOLOv8 ONNX 推理 (~30ms CPU / 5ms GPU) 找出所有 close_x + action_btn bbox
  3. 优先点 close_x（最安全，永远是关弹窗）
  4. 没 close_x 则点 action_btn —— 但先在 bbox 内做 OCR，含"前往/参加/进入"等导航词的跳过
  5. 都没检测到 → 等下一轮（可能在加载/动画中）

Fallback: 模型文件不存在时退到 OcrDismisser，保持兼容。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from . import metrics

logger = logging.getLogger(__name__)


CLASSES = ["close_x", "action_btn"]
CONF_THRESHOLD = 0.30   # 检测下限：低于此值丢弃
TAP_CONF_CLOSE = 0.50   # 点击 close_x 下限
TAP_CONF_ACTION = 0.50  # 点击 action_btn 下限
NMS_IOU = 0.45

# 主操作按钮文字若含这些词，视为"跳出大厅"的导航按钮，不点
# 跟 popup_rules.json 共用关键词体系（之后可挪进去支持热加载）
NAV_WORDS = (
    "前往", "参加", "进入", "查看活动", "去看看", "立即前往", "前往观赛",
    "去活动", "我要参加", "查看",
)


@dataclass
class Detection:
    cls: int
    name: str
    conf: float
    cx: int
    cy: int
    x1: int
    y1: int
    x2: int
    y2: int


@dataclass
class DismissResult:
    success: bool
    popups_closed: int
    final_state: str
    rounds: int
    # v2-4 细粒度时间记录 (单位: ms, -1 = 未发生)
    t_first_popup_seen_ms: float = -1.0   # 第一次 YOLO 检到 close_x/action_btn
    t_first_tap_ms: float = -1.0          # 第一次 tap (从 dismiss_all 开始算)
    t_first_dismiss_ok_ms: float = -1.0   # 第一次成功关掉弹窗 (verify 通过)
    t_lobby_confirmed_ms: float = -1.0    # 大厅确认时间


def _model_path() -> Path:
    from .user_paths import user_yolo_dir
    return user_yolo_dir() / "models" / "latest.onnx"


class YoloDismisser:
    """YOLO 驱动的弹窗清理器。warmup 加载 ONNX，每轮 detect + tap"""

    _session = None
    _input_name: Optional[str] = None
    _input_shape = (640, 640)

    def __init__(self, max_rounds: int = 20):
        self.max_rounds = max_rounds
        self._ocr_for_cta = None  # 懒初始化, CTA 检测在 ROI 内 OCR 用

    def _get_ocr_for_cta(self):
        """懒初始化, 复用 OcrDismisser 的 _ocr_all (调 OcrPool)"""
        if self._ocr_for_cta is None:
            try:
                from .ocr_dismisser import OcrDismisser
                self._ocr_for_cta = OcrDismisser(max_rounds=1)
            except Exception:
                self._ocr_for_cta = False  # 标记失败, 不重试
        return self._ocr_for_cta if self._ocr_for_cta else None

    # ─────────── 模型加载 ───────────

    @classmethod
    def is_available(cls) -> bool:
        """有可用模型 → True；用来决定是否走 YOLO 路径"""
        if cls._session is not None:
            return True
        return cls._try_load()

    @classmethod
    def _try_load(cls) -> bool:
        path = _model_path()
        if not path.is_file():
            logger.info(f"[yolo] 模型文件不存在 ({path})，dismiss 走 OCR fallback")
            return False
        try:
            import onnxruntime as ort
        except ImportError:
            logger.warning("[yolo] onnxruntime 未安装，dismiss 走 OCR fallback")
            return False
        try:
            available = set(ort.get_available_providers())
            providers = []
            # 优先 CUDA（Windows GPU）→ DirectML → CPU
            for p in ("CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"):
                if p in available:
                    providers.append(p)
            if not providers:
                providers = ["CPUExecutionProvider"]
            cls._session = ort.InferenceSession(str(path), providers=providers)
            cls._input_name = cls._session.get_inputs()[0].name
            shape = cls._session.get_inputs()[0].shape
            cls._input_shape = (int(shape[2]), int(shape[3]))
            logger.info(
                f"[yolo] 加载 {path} providers={cls._session.get_providers()} "
                f"input={cls._input_shape}"
            )
            return True
        except Exception as e:
            logger.error(f"[yolo] 模型加载失败: {e}")
            return False

    @classmethod
    def warmup(cls) -> bool:
        """启动时调一次预热（先 load 一次防首次推理慢）"""
        if not cls._try_load():
            return False
        # 跑一帧热身
        try:
            dummy = np.zeros((720, 1280, 3), dtype=np.uint8)
            cls._infer(dummy)
            logger.info("[yolo] warmup 完成")
            return True
        except Exception as e:
            logger.warning(f"[yolo] warmup 推理失败: {e}")
            return False

    # ─────────── 推理 ───────────

    @classmethod
    def _preprocess(cls, frame: np.ndarray) -> tuple[np.ndarray, float, tuple[int, int]]:
        """letterbox resize 到 input_shape + normalize"""
        h, w = frame.shape[:2]
        th, tw = cls._input_shape
        scale = min(tw / w, th / h)
        new_w, new_h = int(w * scale), int(h * scale)
        resized = cv2.resize(frame, (new_w, new_h))
        canvas = np.full((th, tw, 3), 114, dtype=np.uint8)
        pad_x = (tw - new_w) // 2
        pad_y = (th - new_h) // 2
        canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
        canvas = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        tensor = canvas.transpose(2, 0, 1).astype(np.float32) / 255.0
        return np.expand_dims(tensor, 0), scale, (pad_x, pad_y)

    @classmethod
    def _postprocess(
        cls, output: np.ndarray, scale: float, pad: tuple[int, int],
        orig_h: int, orig_w: int,
    ) -> list[Detection]:
        """YOLOv8 ONNX 输出 (1, 4+nc, anchors) → NMS → Detections"""
        # output[0] shape: (4+nc, anchors)
        preds = output[0].T  # (anchors, 4+nc)
        boxes = preds[:, :4]
        scores = preds[:, 4:]
        max_scores = scores.max(axis=1)
        max_classes = scores.argmax(axis=1)
        mask = max_scores > CONF_THRESHOLD
        if not mask.any():
            return []
        boxes = boxes[mask]
        max_scores = max_scores[mask]
        max_classes = max_classes[mask]

        # cx,cy,w,h → x1,y1,x2,y2 (in input space)
        x1 = boxes[:, 0] - boxes[:, 2] / 2
        y1 = boxes[:, 1] - boxes[:, 3] / 2
        x2 = boxes[:, 0] + boxes[:, 2] / 2
        y2 = boxes[:, 1] + boxes[:, 3] / 2
        # un-letterbox：减去 pad，除 scale
        x1 = (x1 - pad[0]) / scale
        y1 = (y1 - pad[1]) / scale
        x2 = (x2 - pad[0]) / scale
        y2 = (y2 - pad[1]) / scale
        x1 = np.clip(x1, 0, orig_w - 1)
        y1 = np.clip(y1, 0, orig_h - 1)
        x2 = np.clip(x2, 0, orig_w - 1)
        y2 = np.clip(y2, 0, orig_h - 1)

        keep: list[Detection] = []
        for cid in range(len(CLASSES)):
            cls_mask = max_classes == cid
            if not cls_mask.any():
                continue
            xyxy = np.stack(
                [x1[cls_mask], y1[cls_mask], x2[cls_mask], y2[cls_mask]],
                axis=1,
            ).astype(np.float32)
            sc = max_scores[cls_mask].astype(np.float32)
            indices = cv2.dnn.NMSBoxes(
                bboxes=xyxy.tolist(), scores=sc.tolist(),
                score_threshold=CONF_THRESHOLD, nms_threshold=NMS_IOU,
            )
            if len(indices) == 0:
                continue
            for i in (indices.flatten() if hasattr(indices, "flatten") else indices):
                keep.append(Detection(
                    cls=cid,
                    name=CLASSES[cid],
                    conf=float(sc[i]),
                    cx=int((xyxy[i][0] + xyxy[i][2]) / 2),
                    cy=int((xyxy[i][1] + xyxy[i][3]) / 2),
                    x1=int(xyxy[i][0]),
                    y1=int(xyxy[i][1]),
                    x2=int(xyxy[i][2]),
                    y2=int(xyxy[i][3]),
                ))
        keep.sort(key=lambda d: d.conf, reverse=True)
        return keep

    @classmethod
    def _infer(cls, frame: np.ndarray) -> list[Detection]:
        if cls._session is None and not cls._try_load():
            return []
        h, w = frame.shape[:2]
        tensor, scale, pad = cls._preprocess(frame)
        outputs = cls._session.run(None, {cls._input_name: tensor})
        return cls._postprocess(outputs[0], scale, pad, h, w)

    @classmethod
    def detect(cls, frame: np.ndarray) -> list[Detection]:
        """对外暴露的检测方法。返回所有 conf > CONF_THRESHOLD 的 bbox"""
        return cls._infer(frame)

    # ─────────── 主循环 ───────────

    async def dismiss_all(self, device, matcher=None) -> DismissResult:
        """主驱动。每轮：截图 → 检测大厅 → YOLO 推理 → tap close_x 或 action_btn"""
        if not self._try_load():
            # 退到 OCR fallback
            from .ocr_dismisser import OcrDismisser
            logger.warning("[yolo] 模型不可用，退到 OcrDismisser")
            return await OcrDismisser(max_rounds=self.max_rounds).dismiss_all(device, matcher)

        # Decision Recorder（前端可视化每次决策）
        from .decision_log import get_recorder, TierRecord, YoloDetection
        from .adb_lite import phash as _phash
        rec = get_recorder()

        # 实例号（从 ContextVar 读，便于决策 ID）
        try:
            from ..runner_service import _current_instance
            inst_idx = _current_instance.get(-1)
        except Exception:
            inst_idx = -1

        popups_closed = 0
        lobby_confirm = 0
        LOBBY_CONFIRM_NEEDED = 2
        last_tap = (0, 0)
        same_target_count = 0
        empty_dets_streak = 0  # 连续多少轮 YOLO 没检到 close_x/action_btn

        # ── v2-4 Memory L1: 见过这个画面 → 直接 tap 历史成功坐标 ──
        # 跨实例共享 (同 PC), 用户教 / YOLO 成功 / CTA 成功 都自动入库
        memory = None
        try:
            from .memory_l1 import FrameMemory
            from .user_paths import user_data_dir
            memory_db = user_data_dir() / "memory" / "dismiss_popups.db"
            memory = FrameMemory(memory_db)
        except Exception as _e:
            logger.warning(f"[memory] 初始化失败 (非致命): {_e}")
        memory_target = "dismiss_popups"  # 主循环统一用这个 target_name

        # v2-4 细粒度时间记录: 从 dismiss_all 开始的累计 ms
        _phase_start_ts = time.perf_counter()
        _t_first_popup_seen_ms = -1.0
        _t_first_tap_ms = -1.0
        _t_first_dismiss_ok_ms = -1.0
        def _ms_since_start() -> float:
            return round((time.perf_counter() - _phase_start_ts) * 1000, 1)

        # v2 P2 四元信号融合判大厅 — 替代旧"连续 2 次模板命中"简化逻辑
        # 修半透明弹窗误判 bug: 模板命中 + close_x=0 + action_btn=0 +
        # 无遮罩 + phash 稳定, 全过才算大厅
        # stable_frames_required=2: 大厅有金币飞动 / 活动 banner 微动效, 严格 5 帧凑不齐
        from .lobby_check import LobbyQuadDetector
        quad_detector = LobbyQuadDetector(stable_frames_required=2)
        use_quad = True  # 灰度开关, 出问题改 False 退回旧逻辑

        for rnd in range(self.max_rounds):
            shot = await device.screenshot()
            if shot is None:
                await asyncio.sleep(0.3)
                continue

            # 开始一次决策记录（Decision context）
            decision = rec.new_decision(inst_idx, "dismiss_popups", rnd + 1)
            ph_before = ""
            try:
                ph_before = hex(_phash(shot))
            except Exception:
                pass
            decision.set_input(shot, ph_before)

            # 顺手采集训练数据（持续投喂未来训练用）
            try:
                from .screenshot_collector import collect as _c
                _c(shot, tag=f"yolo_R{rnd + 1:02d}")
            except Exception:
                pass

            # 大厅检测（保留模板路径，因为它在 lobby 上准确率比 YOLO 高）
            # 详细记录 Tier 0 模板：哪个模板命中、命中分数、命中位置（用红框标在画面上）
            from .decision_log import TierRecord as _TR
            tier_lobby = _TR(tier=0, name="模板·大厅检测", duration_ms=0.0)
            lobby_hit = None
            lobby_t0 = time.perf_counter()
            try:
                if matcher:
                    # 直接调底层 match_one 拿到模板名 + 位置 + 分数
                    for tname in ("lobby_start_btn", "lobby_start_game"):
                        h = matcher.match_one(shot, tname, threshold=0.75)
                        # 不论命中与否, 记录这个尝试
                        try:
                            from pathlib import Path as _P
                            tdir = _P(matcher.template_dir) if hasattr(matcher, 'template_dir') else None
                        except Exception:
                            tdir = None
                        decision.add_template_attempt(
                            tier_lobby, tname, tdir,
                            score=(h.confidence if h else 0.0),
                            threshold=0.75,
                            hit=(h is not None),
                            bbox=([h.cx - h.w//2, h.cy - h.h//2, h.cx + h.w//2, h.cy + h.h//2] if h else None),
                            scale=1.0,
                        )
                        if h and lobby_hit is None:
                            lobby_hit = (tname, h)
            except Exception as _e:
                tier_lobby.note = f"模板检测异常: {_e}"
            tier_lobby.duration_ms = round((time.perf_counter() - lobby_t0) * 1000, 2)

            if lobby_hit is not None:
                # 命中模板 → 在 input 图上画框, 让用户看见识别在哪
                tname, h = lobby_hit
                annot = shot.copy()
                x1 = max(0, h.cx - h.w // 2)
                y1 = max(0, h.cy - h.h // 2)
                x2 = h.cx + h.w // 2
                y2 = h.cy + h.h // 2
                cv2.rectangle(annot, (x1, y1), (x2, y2), (0, 255, 0), 3)
                cv2.putText(annot, f"模板{tname} {h.confidence:.2f}", (x1, max(20, y1 - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                try:
                    cv2.imwrite(str(decision.path / "lobby_annot.jpg"), annot,
                                [cv2.IMWRITE_JPEG_QUALITY, 70])
                    tier_lobby.note = f"命中 {tname}@({h.cx},{h.cy}) conf={h.confidence:.2f} → 见 lobby_annot.jpg"
                    tier_lobby.yolo_annot_image = "lobby_annot.jpg"
                except Exception:
                    pass
                tier_lobby.early_exit = True
                decision.add_tier(tier_lobby)
            else:
                # 模板未命中, 记录后继续走 YOLO (quad 检查也会用)
                decision.add_tier(tier_lobby)

            # YOLO 推理
            t0 = time.perf_counter()
            dets = self.detect(shot)
            dur_ms = (time.perf_counter() - t0) * 1000
            metrics.record("yolo_detect", dur_ms=round(dur_ms, 2), n=len(dets))

            # v2-4 漏检诊断: 每轮都打 dets 概览, 让"P2 前 18s 没动" 能精确定位
            # 是 'YOLO 真没看到东西' 还是 '看到了但 conf 低于阈值'
            if dets:
                _det_summary = ", ".join(
                    f"{d.name}({d.conf:.2f})@({d.cx},{d.cy})"
                    for d in dets[:5]
                )
                logger.info(
                    f"[Y{rnd + 1}] dets={len(dets)} infer={dur_ms:.0f}ms "
                    f"top: {_det_summary}"
                )
            else:
                logger.info(
                    f"[Y{rnd + 1}] dets=0 infer={dur_ms:.0f}ms"
                    f" (画面无 close_x/action_btn 检出)"
                )

            # ─── v2 P2 四元融合判大厅 (代替老的"连续 2 次模板命中") ───
            # 模板命中 + close_x=0 + action_btn=0 + 无遮罩 + phash 5 帧稳定
            if use_quad:
                quad_r = quad_detector.check(shot, matcher, dets)
                if quad_r.is_lobby:
                    logger.info(
                        f"[Y{rnd + 1}] 大厅 (四元融合) → 完成 · 关闭 {popups_closed}"
                    )
                    quad_tier = _TR(
                        tier=0,
                        name="四元融合·大厅判定",
                        duration_ms=0.0,
                        early_exit=True,
                        note=quad_r.note,
                    )
                    decision.add_tier(quad_tier)
                    decision.finalize(
                        outcome="lobby_confirmed_quad",
                        note=f"四元融合 OK · 关闭 {popups_closed} 个弹窗 · {quad_r.note}",
                    )
                    return DismissResult(True, popups_closed, "lobby", rnd + 1, t_first_popup_seen_ms=_t_first_popup_seen_ms, t_first_tap_ms=_t_first_tap_ms, t_first_dismiss_ok_ms=_t_first_dismiss_ok_ms, t_lobby_confirmed_ms=_ms_since_start())
                # 模板命中但 quad 不通过 → 仍清弹窗 (说明有遮罩或 close_x 在)
                if lobby_hit is not None:
                    logger.debug(
                        f"[Y{rnd + 1}] 模板命中但 quad 拒: {quad_r.note}"
                    )
            else:
                # 灰度回退: 旧"连续 2 次模板命中"路径
                if lobby_hit is not None:
                    lobby_confirm += 1
                    if lobby_confirm >= LOBBY_CONFIRM_NEEDED:
                        logger.info(f"[Y{rnd + 1}] 大厅确认 (legacy) → 完成 (关闭 {popups_closed})")
                        decision.finalize(
                            outcome="lobby_confirmed_legacy",
                            note=f"模板连续命中 {LOBBY_CONFIRM_NEEDED} 次 · 关闭 {popups_closed} 个弹窗",
                        )
                        return DismissResult(True, popups_closed, "lobby", rnd + 1, t_first_popup_seen_ms=_t_first_popup_seen_ms, t_first_tap_ms=_t_first_tap_ms, t_first_dismiss_ok_ms=_t_first_dismiss_ok_ms, t_lobby_confirmed_ms=_ms_since_start())
                    decision.finalize(
                        outcome=f"lobby_pending_{lobby_confirm}/{LOBBY_CONFIRM_NEEDED}",
                        note=f"模板命中, 等 {LOBBY_CONFIRM_NEEDED} 次 (legacy)",
                    )
                    await asyncio.sleep(0.3)
                    continue

            # 记录 YOLO Tier 到 Decision
            tier_yolo = TierRecord(tier=2, name="YOLO", duration_ms=round(dur_ms, 2))
            yolo_dets_log = [
                YoloDetection(cls=d.name, conf=round(d.conf, 3), bbox=[d.x1, d.y1, d.x2, d.y2])
                for d in dets
            ]
            decision.save_yolo_annot(tier_yolo, shot, yolo_dets_log)
            decision.add_tier(tier_yolo)

            close_xs = [d for d in dets if d.name == "close_x" and d.conf > TAP_CONF_CLOSE]
            actions = [d for d in dets if d.name == "action_btn" and d.conf > TAP_CONF_ACTION]

            # ── 时间埋点: 第一次见弹窗 ──
            if (close_xs or actions) and _t_first_popup_seen_ms < 0:
                _t_first_popup_seen_ms = _ms_since_start()
                logger.info(
                    f"[时间] dismiss_popups: 第一次见弹窗 +{_t_first_popup_seen_ms:.0f}ms"
                    f" (close_x={len(close_xs)}, action_btn={len(actions)})"
                )

            tap_xy: Optional[tuple[int, int, str]] = None
            target_class = ""
            target_conf = 0.0
            ocr_text = ""

            # ─── 优先级 0: Memory L1 (见过这画面就直接复用) ───
            if memory is not None and tap_xy is None:
                try:
                    mem_hit = memory.query(shot, target_name=memory_target, max_dist=5)
                except Exception as _e:
                    mem_hit = None
                    logger.debug(f"[memory] query err: {_e}")
                if mem_hit:
                    tap_xy = (mem_hit.cx, mem_hit.cy,
                              f"Memory(conf={mem_hit.confidence:.2f})")
                    target_class = "memory_hit"
                    target_conf = mem_hit.confidence
                    logger.info(
                        f"[Y{rnd + 1}] 🧠 Memory 命中 → tap "
                        f"({mem_hit.cx},{mem_hit.cy}) {mem_hit.note}"
                    )
                    mem_tier = _TR(
                        tier=1, name="Memory L1",
                        duration_ms=0.0, early_exit=True,
                        note=f"phash 复用: {mem_hit.note}",
                    )
                    decision.add_tier(mem_tier)

            # 优先级 1：close_x（最安全，纯关弹窗）
            if tap_xy is None and close_xs:
                tgt = close_xs[0]
                tap_xy = (tgt.cx, tgt.cy, f"close_x({tgt.conf:.2f})")
                target_class = "close_x"
                target_conf = tgt.conf

            # 优先级 1.5: 模板 close_x_* 兜底 (YOLO 漏检公告 / 活动 / 对话框 X)
            # 这些模板高准确率 (0.85+), 但 v2 主路径之前没用, GuardedADB 关后丢失了
            if tap_xy is None and matcher is not None:
                for tn in (
                    "close_x_announce", "close_x_dialog", "close_x_activity",
                    "close_x_gold", "close_x_signin", "close_x_newplay",
                    "close_x_return", "close_x_white_big",
                ):
                    h = matcher.match_one(shot, tn, threshold=0.80)
                    if h:
                        tap_xy = (h.cx, h.cy, f"模板 {tn}({h.confidence:.2f})")
                        target_class = "template_close_x"
                        target_conf = h.confidence
                        logger.info(
                            f"[Y{rnd + 1}] 模板兜底命中 {tn} @ ({h.cx},{h.cy}) "
                            f"conf={h.confidence:.2f}"
                        )
                        tmpl_tier = _TR(
                            tier=0, name=f"模板·{tn}",
                            duration_ms=0.0, early_exit=True,
                            note=f"YOLO 漏检, 模板兜底命中 {tn}",
                        )
                        decision.add_tier(tmpl_tier)
                        break

            # 优先级 2：action_btn — 但要 OCR 排除 nav 按钮
            elif tap_xy is None and actions:
                tgt = actions[0]
                roi_box = [max(0, tgt.x1), max(0, tgt.y1), tgt.x2, tgt.y2]
                roi = shot[roi_box[1]:roi_box[3], roi_box[0]:roi_box[2]]
                text, ocr_hits_log = self._ocr_bbox_with_hits(roi, roi_box)
                ocr_text = text
                # 记录 OCR Tier 到 Decision（带 ROI 框）
                from .decision_log import TierRecord as _TR
                tier_ocr = _TR(tier=3, name="OCR-bbox",
                               duration_ms=0.0,
                               note=f"在 action_btn bbox 内 OCR")
                decision.save_ocr_roi(tier_ocr, shot, roi=roi_box, hits=ocr_hits_log)
                decision.add_tier(tier_ocr)
                if any(nav in text for nav in NAV_WORDS):
                    logger.info(f"[Y{rnd + 1}] action_btn 含 nav 词 '{text[:30]}'，跳过")
                else:
                    tap_xy = (tgt.cx, tgt.cy, f"action_btn({tgt.conf:.2f},{text[:20]})")
                    target_class = "action_btn"
                    target_conf = tgt.conf

            if tap_xy is None:
                # 啥都没识别 → 可能加载中 / 干净大厅 / outside_lobby 强引导
                empty_dets_streak += 1

                # 兜底 A: 大厅模板命中 + 连续 3 轮无弹窗 → 大厅成功
                if empty_dets_streak >= 3 and lobby_hit is not None:
                    logger.info(
                        f"[Y{rnd + 1}] 大厅 (兜底: 连续 {empty_dets_streak} 轮无弹窗 + 模板命中) → 完成 · 关闭 {popups_closed}"
                    )
                    decision.finalize(
                        outcome="lobby_confirmed_empty",
                        note=f"连续 {empty_dets_streak} 轮 YOLO 无目标 + 模板命中 lobby_start_btn",
                    )
                    return DismissResult(True, popups_closed, "lobby", rnd + 1, t_first_popup_seen_ms=_t_first_popup_seen_ms, t_first_tap_ms=_t_first_tap_ms, t_first_dismiss_ok_ms=_t_first_dismiss_ok_ms, t_lobby_confirmed_ms=_ms_since_start())

                # 兜底 B0: outside_lobby + 卡 5 轮 → 检查是不是登录页 (自动登录失败兜底)
                # 优先模板 (5ms), 模板都没命中再退到 OCR (200ms)
                if lobby_hit is None and empty_dets_streak >= 5 and matcher is not None:
                    # 模板路径: lobby_login_btn (微信) / lobby_login_btn_qq (QQ)
                    for tn in ("lobby_login_btn", "lobby_login_btn_qq"):
                        h = matcher.match_one(shot, tn, threshold=0.80)
                        if h:
                            tap_xy = (h.cx, h.cy, f"登录模板 {tn}({h.confidence:.2f})")
                            target_class = "login_btn"
                            target_conf = h.confidence
                            logger.warning(
                                f"[Y{rnd + 1}] 登录页兜底 (模板 {tn}) → tap "
                                f"@ ({h.cx},{h.cy}) (自动登录失败)"
                            )
                            login_tier = _TR(
                                tier=0, name=f"登录模板·{tn}",
                                duration_ms=0.0, early_exit=True,
                                note=f"模板兜底登录: conf={h.confidence:.2f}",
                            )
                            decision.add_tier(login_tier)
                            break
                    # 模板都没命中, OCR 二级兜底 (兼容老/新版本游戏 UI 变化)
                    if tap_xy is None:
                        try:
                            ocr_inst = self._get_ocr_for_cta()
                            if ocr_inst is not None:
                                hits = ocr_inst._ocr_all(shot) or []
                                for h in hits:
                                    t = getattr(h, "text", "")
                                    if "微信登录" in t or "QQ登录" in t or "扫码登录" in t:
                                        cx = getattr(h, "cx", 0)
                                        cy = getattr(h, "cy", 0)
                                        if cx and cy:
                                            tap_xy = (cx, cy, f"登录OCR('{t[:8]}')")
                                            target_class = "login_btn"
                                            target_conf = 0.85
                                            ocr_text = t
                                            logger.warning(
                                                f"[Y{rnd + 1}] 登录页兜底 (OCR) → tap "
                                                f"'{t}' @ ({cx},{cy})"
                                            )
                                            login_tier = _TR(
                                                tier=3, name="登录OCR兜底",
                                                duration_ms=0.0, early_exit=True,
                                                note=f"OCR 找到登录按钮: {t}",
                                            )
                                            decision.add_tier(login_tier)
                                            break
                        except Exception as _e:
                            logger.debug(f"[login] err: {_e}")

                # 兜底 B: outside_lobby (lobby 模板未命中) + X 找不到 → 必须找 CTA 才能回大厅
                # 强引导活动 (砍价 / 立即领取) 设计上只有 CTA 出路, 必须点
                # popup_in_lobby (lobby 模板命中) 时不进, 等下一轮 (避免误参与活动)
                if tap_xy is None and lobby_hit is None:
                    try:
                        from .cta_detector import find_main_cta, NAV_BLACKLIST
                        ocr_inst = self._get_ocr_for_cta()
                        ocr_fn_local = (
                            (lambda roi: ocr_inst._ocr_all(roi)) if ocr_inst else None
                        )
                        cta = find_main_cta(
                            shot, ocr_fn=ocr_fn_local, nav_blacklist=NAV_BLACKLIST,
                        )
                    except Exception as _e:
                        logger.debug(f"[cta] err: {_e}")
                        cta = None
                    if cta:
                        tap_xy = (cta.cx, cta.cy,
                                  f"CTA('{cta.text[:8]}',sat={cta.saturation:.0f})")
                        target_class = "cta"
                        target_conf = min(0.99, max(0.6, cta.score / 100))
                        ocr_text = cta.text
                        logger.warning(
                            f"[Y{rnd + 1}] CTA 兜底 (outside_lobby) → tap '{cta.text}' "
                            f"@ ({cta.cx},{cta.cy}) sat={cta.saturation:.0f} area={cta.area}"
                        )
                        cta_tier = _TR(
                            tier=2, name="CTA 兜底",
                            duration_ms=0.0, early_exit=True,
                            note=f"text='{cta.text}' sat={cta.saturation:.0f} area={cta.area}",
                        )
                        decision.add_tier(cta_tier)
                        # fallthrough 到 tap 路径
                if tap_xy is None:
                    logger.debug(
                        f"[Y{rnd + 1}] 无目标 (dets={len(dets)}, 连续{empty_dets_streak}轮, "
                        f"mode={'outside_lobby' if lobby_hit is None else 'popup_in_lobby'})"
                    )
                    decision.finalize(outcome="no_target",
                                      note=f"YOLO 检 {len(dets)} 个目标都不达标 + CTA 兜底也没找到")
                    await asyncio.sleep(0.6)
                    continue
            else:
                empty_dets_streak = 0  # 重置: 这轮有目标

            # 防死循环：连续 3 次同一坐标 → 这个目标可能不可交互，跳过本轮
            if abs(tap_xy[0] - last_tap[0]) < 20 and abs(tap_xy[1] - last_tap[1]) < 20:
                same_target_count += 1
                if same_target_count >= 3:
                    logger.warning(f"[Y{rnd + 1}] 连续 3 次同点击({tap_xy[:2]}) 无效果，等待")
                    decision.finalize(outcome="loop_blocked", note=f"同坐标连点 3 次无效果")
                    await asyncio.sleep(1.5)
                    same_target_count = 0
                    continue
            else:
                same_target_count = 0
            last_tap = tap_xy[:2]

            logger.info(f"[Y{rnd + 1}] tap {tap_xy[2]} @ ({tap_xy[0]},{tap_xy[1]}) "
                        f"({dur_ms:.0f}ms, dets={len(dets)})")
            decision.set_tap(tap_xy[0], tap_xy[1], method="YOLO",
                             target_class=target_class, target_text=ocr_text,
                             target_conf=target_conf, screenshot=shot)
            # ── 时间埋点: 第一次 tap ──
            if _t_first_tap_ms < 0:
                _t_first_tap_ms = _ms_since_start()
                logger.info(f"[时间] dismiss_popups: 第一次 tap +{_t_first_tap_ms:.0f}ms")
            await device.tap(tap_xy[0], tap_xy[1])
            popups_closed += 1
            lobby_confirm = 0
            await asyncio.sleep(0.5)

            # tap 后验证：防线 1 phash 比对 + 防线 2 State Expectation
            outcome = "tapped"
            verify_note = ""
            try:
                shot_after = await device.screenshot()
                if shot_after is not None:
                    ph_after = hex(_phash(shot_after))
                    from .adb_lite import phash_distance as _phd
                    dist = _phd(int(ph_before, 16), int(ph_after, 16))
                    decision.set_verify(ph_before, ph_after, dist)

                    # ── v2 防线 2: State Expectation ──
                    try:
                        from .state_expectation import verify as _expect_verify
                        # label 优先用 OCR 文字命中关键字, 否则用 yolo class
                        expect_label = target_class
                        if target_class == "action_btn" and ocr_text:
                            for kw in ("收下", "确定", "确认", "同意", "前往", "参加", "进入"):
                                if kw in ocr_text:
                                    expect_label = kw
                                    break
                        # 跑下一轮 YOLO 拿 after detections (给 close_x 计数 verifier 用)
                        try:
                            yolo_after = self.detect(shot_after)
                        except Exception:
                            yolo_after = []
                        ctx = {
                            "yolo_before": dets,
                            "yolo_after": yolo_after,
                            "matcher": matcher,
                        }
                        exp_r = _expect_verify(expect_label, shot, shot_after, ctx)
                        verify_note = f"expect[{expect_label}]={'OK' if exp_r.matched else 'FAIL'} {exp_r.note}"
                        if not exp_r.matched:
                            outcome = "tap_expect_failed"
                            logger.warning(
                                f"[Y{rnd + 1}] State Expectation 失败 [{expect_label}]: "
                                f"{exp_r.note}"
                            )
                            # ── Memory L1 写入 (失败) ──
                            if memory is not None:
                                try:
                                    memory.remember(
                                        shot, target_name=memory_target,
                                        action_xy=(tap_xy[0], tap_xy[1]),
                                        success=False,
                                    )
                                except Exception:
                                    pass
                        else:
                            # 时间埋点: 第一次成功关闭弹窗 (verify 通过)
                            if _t_first_dismiss_ok_ms < 0:
                                _t_first_dismiss_ok_ms = _ms_since_start()
                                logger.info(
                                    f"[时间] dismiss_popups: 第一次成功关闭 "
                                    f"+{_t_first_dismiss_ok_ms:.0f}ms"
                                )
                            # ── Memory L1 写入 (成功) ──
                            # 任何成功 tap 都入库, 下次见同 phash 直接秒过
                            if memory is not None:
                                try:
                                    memory.remember(
                                        shot, target_name=memory_target,
                                        action_xy=(tap_xy[0], tap_xy[1]),
                                        success=True,
                                    )
                                    logger.info(
                                        f"[Y{rnd + 1}] 🧠 Memory 写入 "
                                        f"({tap_xy[0]},{tap_xy[1]}) method={target_class}"
                                    )
                                except Exception as _me:
                                    logger.debug(f"[memory] write err: {_me}")
                    except Exception as _ee:
                        logger.debug(f"[Y{rnd + 1}] expectation verify err: {_ee}")
            except Exception:
                pass

            decision.finalize(outcome=outcome,
                              note=f"{target_class} conf={target_conf:.2f} · {verify_note}")

        logger.warning(f"[yolo] {self.max_rounds} 轮 timeout (关闭 {popups_closed})")
        return DismissResult(False, popups_closed, "timeout", self.max_rounds, t_first_popup_seen_ms=_t_first_popup_seen_ms, t_first_tap_ms=_t_first_tap_ms, t_first_dismiss_ok_ms=_t_first_dismiss_ok_ms, t_lobby_confirmed_ms=-1.0)

    @classmethod
    def _ocr_bbox(cls, roi: np.ndarray) -> str:
        """对 bbox 内做 OCR，返回拼接文字。失败返空串。"""
        if roi is None or roi.size == 0:
            return ""
        try:
            from .ocr_dismisser import OcrDismisser
            inst = OcrDismisser()
            hits = inst._ocr_all(roi)
            return " ".join(h.text for h in hits)
        except Exception:
            return ""

    @classmethod
    def _ocr_bbox_with_hits(cls, roi: np.ndarray, roi_offset: list) -> tuple[str, list]:
        """对 bbox 内做 OCR，返回 (拼接文字, OcrHit 列表带全屏坐标)"""
        from .decision_log import OcrHit
        if roi is None or roi.size == 0:
            return "", []
        try:
            from .ocr_dismisser import OcrDismisser
            inst = OcrDismisser()
            hits = inst._ocr_all(roi)
            ox, oy = roi_offset[0], roi_offset[1]
            log_hits = []
            for h in hits:
                # h.bbox 是 ROI 内坐标，转全屏坐标
                bb = getattr(h, "bbox", None)
                if bb and len(bb) == 4:
                    full_bb = [bb[0] + ox, bb[1] + oy, bb[2] + ox, bb[3] + oy]
                else:
                    full_bb = [ox, oy, ox + roi.shape[1], oy + roi.shape[0]]
                log_hits.append(OcrHit(
                    text=h.text,
                    bbox=full_bb,
                    conf=getattr(h, "conf", 0.0),
                    cx=getattr(h, "cx", 0) + ox,
                    cy=getattr(h, "cy", 0) + oy,
                ))
            return " ".join(h.text for h in hits), log_hits
        except Exception:
            return "", []
