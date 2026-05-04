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
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from . import metrics

logger = logging.getLogger(__name__)


def _load_classes() -> list[str]:
    """从 user_yolo_dir/classes.txt 读 CLASSES (跟训练时一致). 不存在 fallback 内置.
    模块加载时调一次, runtime 改 classes.txt 不会生效 (改了要重启 exe — 通常伴随
    训新模型, 重启是预期流程)."""
    try:
        from .user_paths import user_yolo_dir
        p = user_yolo_dir() / "classes.txt"
        if p.is_file():
            lines = [x.strip() for x in p.read_text(encoding="utf-8").splitlines()
                     if x.strip() and not x.strip().startswith("#")]
            if lines:
                logger.info(f"[yolo] classes from {p}: {lines}")
                return lines
    except Exception as e:
        logger.warning(f"[yolo] 读 classes.txt 失败, fallback: {e}")
    return ["close_x", "action_btn"]


CLASSES = _load_classes()
CONF_THRESHOLD = 0.30   # 检测下限：低于此值丢弃
TAP_CONF_CLOSE = 0.50   # 点击 close_x 下限
TAP_CONF_ACTION = 0.50  # 点击 action_btn 下限
NMS_IOU = 0.45

# 按钮文字分级词典 — 决定"多个 action_btn 选哪个点"
# 优先级: CLOSE > ACCEPT > NAV (NAV 永不点); 优先级匹配 (前面的关键词命中就停)
# 用户原话: "取消大于收下"

# P1 关闭/拒绝类 — 最安全, 永远不会跳出大厅
CLOSE_WORDS = (
    "关闭", "取消", "稍后", "暂不", "下次", "再想想", "再看看", "再说",
    "不再提醒", "不再显示", "跳过", "略过", "拒绝",
)

# P2 收下/确认类 — 关弹窗 (动作完成后弹窗消失, 不跳出大厅)
ACCEPT_WORDS = (
    "收下", "我收下了", "领取", "立即领取", "确定", "确认",
    "知道了", "我知道了", "好的", "已了解", "了解", "继续", "下一步",
    "立即砍价", "砍价", "完成",
)

# P3 导航/前往类 — 绝对不点 (会跳出大厅去别的界面)
NAV_WORDS = (
    "前往", "前往观赛", "立即前往", "参加", "我要参加", "进入",
    "查看", "查看活动", "去看看", "去活动",
)

# 错误/网络对话框关键词 — 用于识别"操作确认型"弹窗 (区分于普通"取消+确定")
# 这种弹窗"取消"=放弃恢复, "确定"=重试恢复, ACCEPT 必须反向优先
# 用户原话: "只有提示网络才这样 不要成对的取消+确定就反向"
ERROR_DIALOG_WORDS = (
    "无法连接", "连接失败", "连接断开", "网络", "断网", "断开",
    "出错", "错误", "失败", "重试", "超时", "异常", "服务器",
    "请检查", "无法访问",
)


def _classify_button_text(text: str) -> str:
    """按钮文字分类. 返回 'close' | 'accept' | 'nav' | 'unknown'.
    优先级 CLOSE > ACCEPT > NAV — 处理 "立即砍价" 这种含 "立即"(NAV) 也含 "砍价"(ACCEPT)
    的边界, 让 ACCEPT 优先于 NAV; "暂不开通" 含 "暂不"(CLOSE) 也含 "开通", CLOSE 优先."""
    if not text:
        return "unknown"
    if any(w in text for w in CLOSE_WORDS):
        return "close"
    if any(w in text for w in ACCEPT_WORDS):
        return "accept"
    if any(w in text for w in NAV_WORDS):
        return "nav"
    return "unknown"


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



def _model_path() -> Path:
    from .user_paths import user_yolo_dir
    return user_yolo_dir() / "models" / "latest.onnx"


class YoloDismisser:
    """YOLO 驱动的弹窗清理器.

    v2-9: 多 session 池模式 - 每实例独立 ONNX session 实现真并发推理.
    旧: 类级单 _session, 6 实例并发要排队 (实测推理时间 30→200ms 抖动)
    新: 每实例 self._session, 真并发, 关键路径 (队长 P3a) 不被队员拖
    用 intra_op_num_threads (默认 2) 防多 session 互抢 CPU 核.
    """

    # 类级共享 _session 作为兼容路径 (谁第一个调 cls.detect/is_available 谁初始化)
    # 实际生产: single_runner 每实例创建自己的 YoloDismisser, 用 instance 方法
    _shared_session = None
    _shared_input_name: Optional[str] = None
    _shared_input_shape = (640, 640)

    def __init__(self, max_rounds: int = 20, intra_threads: Optional[int] = None):
        self.max_rounds = max_rounds
        # 每实例独立 session (v2-9 真并发关键)
        self._session = None
        self._input_name: Optional[str] = None
        self._input_shape = (640, 640)
        self._load_failed = False
        # CPU intra-op 线程数: 默认 2 (6 实例 × 2 = 12 thread, 8 核接近饱和不打架)
        self._intra_threads = intra_threads or int(
            os.environ.get("GAMEBOT_YOLO_INTRA_THREADS", "2")
        )

    # ─────────── 模型加载 (v2-9: 实例级 session) ───────────

    def is_available(self) -> bool:
        """有可用模型 → True; 触发实例级 session 懒加载"""
        if self._session is not None:
            return True
        return self._try_load()

    def _try_load(self) -> bool:
        if self._session is not None:
            return True
        if self._load_failed:
            return False
        path = _model_path()
        if not path.is_file():
            logger.info(f"[yolo] 模型文件不存在 ({path}), dismiss 走 OCR fallback")
            self._load_failed = True
            return False
        try:
            import onnxruntime as ort
        except ImportError:
            logger.warning("[yolo] onnxruntime 未安装, dismiss 走 OCR fallback")
            self._load_failed = True
            return False
        try:
            available = set(ort.get_available_providers())
            providers = []
            for p in ("CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"):
                if p in available:
                    providers.append(p)
            if not providers:
                providers = ["CPUExecutionProvider"]
            # SessionOptions 限制 intra-op 线程数, 防多 session 互抢 CPU 核
            sess_options = ort.SessionOptions()
            sess_options.intra_op_num_threads = self._intra_threads
            sess_options.inter_op_num_threads = 1
            sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            self._session = ort.InferenceSession(
                str(path), sess_options=sess_options, providers=providers,
            )
            self._input_name = self._session.get_inputs()[0].name
            shape = self._session.get_inputs()[0].shape
            self._input_shape = (int(shape[2]), int(shape[3]))
            logger.info(
                f"[yolo] 加载 (instance) providers={self._session.get_providers()} "
                f"input={self._input_shape} intra_threads={self._intra_threads}"
            )
            return True
        except Exception as e:
            logger.error(f"[yolo] 模型加载失败: {e}")
            self._load_failed = True
            return False

    def warmup(self) -> bool:
        """启动时调一次预热 (避免首次推理慢)"""
        if not self._try_load():
            return False
        try:
            dummy = np.zeros((720, 1280, 3), dtype=np.uint8)
            self._infer(dummy)
            logger.info("[yolo] warmup 完成 (instance)")
            return True
        except Exception as e:
            logger.warning(f"[yolo] warmup 推理失败: {e}")
            return False

    # ─────────── 推理 ───────────

    @staticmethod
    def _preprocess(frame: np.ndarray, input_shape: tuple = (640, 640)) -> tuple[np.ndarray, float, tuple[int, int]]:
        """letterbox resize 到 input_shape + normalize"""
        h, w = frame.shape[:2]
        th, tw = input_shape
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

    def _infer(self, frame: np.ndarray) -> list[Detection]:
        if self._session is None and not self._try_load():
            return []
        h, w = frame.shape[:2]
        tensor, scale, pad = self._preprocess(frame, input_shape=self._input_shape)
        outputs = self._session.run(None, {self._input_name: tensor})
        return self._postprocess(outputs[0], scale, pad, h, w)

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """对外暴露的检测方法 (实例级 session, v2-9 真并发)"""
        return self._infer(frame)

    # ─── 兼容 classmethod (没 instance 时用, 共享一个 session) ───
    _shared_instance: Optional["YoloDismisser"] = None

    @classmethod
    def _shared(cls) -> "YoloDismisser":
        if cls._shared_instance is None:
            cls._shared_instance = YoloDismisser()
        return cls._shared_instance

    @classmethod
    def is_available_cls(cls) -> bool:
        """兼容旧代码 (popup_watchdog 等). 推荐用 instance.is_available()"""
        return cls._shared().is_available()

    @classmethod
    def detect_cls(cls, frame: np.ndarray) -> list[Detection]:
        """兼容旧代码. 推荐用 instance.detect(frame) 走独立 session"""
        return cls._shared().detect(frame)

