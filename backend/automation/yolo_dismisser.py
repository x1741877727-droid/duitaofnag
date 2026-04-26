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

        popups_closed = 0
        lobby_confirm = 0
        LOBBY_CONFIRM_NEEDED = 2
        last_tap = (0, 0)
        same_target_count = 0

        for rnd in range(self.max_rounds):
            shot = await device.screenshot()
            if shot is None:
                await asyncio.sleep(0.3)
                continue

            # 顺手采集训练数据（持续投喂未来训练用）
            try:
                from .screenshot_collector import collect as _c
                _c(shot, tag=f"yolo_R{rnd + 1:02d}")
            except Exception:
                pass

            # 大厅检测（保留模板路径，因为它在 lobby 上准确率比 YOLO 高）
            if matcher and matcher.is_at_lobby(shot):
                # 还要确认没遮罩（弹窗刚消失，下一秒可能又冒新的）
                lobby_confirm += 1
                if lobby_confirm >= LOBBY_CONFIRM_NEEDED:
                    logger.info(f"[Y{rnd + 1}] 大厅确认 → 完成 (关闭 {popups_closed})")
                    return DismissResult(True, popups_closed, "lobby", rnd + 1)
                logger.debug(f"[Y{rnd + 1}] 大厅初判 ({lobby_confirm}/{LOBBY_CONFIRM_NEEDED})")
                await asyncio.sleep(0.3)
                continue

            # YOLO 推理
            t0 = time.perf_counter()
            dets = self.detect(shot)
            dur_ms = (time.perf_counter() - t0) * 1000
            metrics.record("yolo_detect", dur_ms=round(dur_ms, 2), n=len(dets))

            close_xs = [d for d in dets if d.name == "close_x" and d.conf > TAP_CONF_CLOSE]
            actions = [d for d in dets if d.name == "action_btn" and d.conf > TAP_CONF_ACTION]

            tap_xy: Optional[tuple[int, int, str]] = None

            # 优先级 1：close_x（最安全，纯关弹窗）
            if close_xs:
                tgt = close_xs[0]
                tap_xy = (tgt.cx, tgt.cy, f"close_x({tgt.conf:.2f})")

            # 优先级 2：action_btn — 但要 OCR 排除 nav 按钮
            elif actions:
                tgt = actions[0]
                roi = shot[max(0, tgt.y1):tgt.y2, max(0, tgt.x1):tgt.x2]
                text = self._ocr_bbox(roi)
                if any(nav in text for nav in NAV_WORDS):
                    logger.info(f"[Y{rnd + 1}] action_btn 含 nav 词 '{text[:30]}'，跳过")
                else:
                    tap_xy = (tgt.cx, tgt.cy, f"action_btn({tgt.conf:.2f},{text[:20]})")

            if tap_xy is None:
                # 啥都没识别 → 加载/动画中
                logger.debug(f"[Y{rnd + 1}] 无目标 (dets={len(dets)})，等待")
                await asyncio.sleep(0.6)
                continue

            # 防死循环：连续 3 次同一坐标 → 这个目标可能不可交互，跳过本轮
            if abs(tap_xy[0] - last_tap[0]) < 20 and abs(tap_xy[1] - last_tap[1]) < 20:
                same_target_count += 1
                if same_target_count >= 3:
                    logger.warning(f"[Y{rnd + 1}] 连续 3 次同点击({tap_xy[:2]}) 无效果，等待")
                    await asyncio.sleep(1.5)
                    same_target_count = 0
                    continue
            else:
                same_target_count = 0
            last_tap = tap_xy[:2]

            logger.info(f"[Y{rnd + 1}] tap {tap_xy[2]} @ ({tap_xy[0]},{tap_xy[1]}) "
                        f"({dur_ms:.0f}ms, dets={len(dets)})")
            await device.tap(tap_xy[0], tap_xy[1])
            popups_closed += 1
            lobby_confirm = 0
            await asyncio.sleep(0.5)

        logger.warning(f"[yolo] {self.max_rounds} 轮 timeout (关闭 {popups_closed})")
        return DismissResult(False, popups_closed, "timeout", self.max_rounds)

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
