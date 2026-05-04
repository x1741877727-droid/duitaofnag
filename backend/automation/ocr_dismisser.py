"""
智能弹窗清理器 — 状态机驱动

核心思路：
  1. 每轮先判断当前状态（大厅/有弹窗/加载中/已退出游戏）
  2. 弹窗检测不靠模板穷举，而是检测"遮罩层"（弹窗共同特征）
  3. 找关闭目标分三级：模板X → OCR文字 → 区域扫描
  4. 每次操作后验证是否生效

弹窗共同特征：
  - 半透明暗色遮罩覆盖游戏画面
  - 中央或偏上有高亮面板
  - 关闭按钮在右上角或底部
"""

import asyncio
import logging
import threading
from dataclasses import dataclass
from enum import Enum

import cv2
import numpy as np

from . import metrics
from .ocr_cache import cached as _ocr_cached, cached_full as _ocr_cached_full
from .ocr_pool import OcrPool

logger = logging.getLogger(__name__)



# OCR 关键词热加载（修改 config/popup_rules.json 即可，无需 rebuild）
# 这些常量保留为"启动 fallback"，每次实际调用都从 RulesLoader.get() 拿最新值
from .rules_loader import RulesLoader, DEFAULTS as _RULES_DEFAULTS

LOBBY_KEYWORDS = _RULES_DEFAULTS["lobby_keywords"]
LOADING_KEYWORDS = _RULES_DEFAULTS["loading_keywords"]
LOGIN_KEYWORDS = _RULES_DEFAULTS["login_keywords"]
LEFT_GAME_KEYWORDS = _RULES_DEFAULTS["left_game_keywords"]
CLOSE_TEXT = _RULES_DEFAULTS["close_text"]
CONFIRM_TEXT = _RULES_DEFAULTS["confirm_text"]
CHECKBOX_TEXT = _RULES_DEFAULTS["checkbox_text"]


class OcrDismisser:
    """OCR 工具类: 全屏 / ROI OCR + fuzzy_match. 名字保留是因为 v3 perception 仍按
    runner.ocr_dismisser 调用 _ocr_all / _ocr_roi_named。dismiss 状态机已删."""

    # 类级别共享 OCR 实例（所有 OcrDismisser 实例共用，只初始化一次）
    _shared_ocr = None
    # OpenVINO 后端单 InferRequest 不允许多线程并发调用 → 序列化主进程的 ocr() 调用.
    # 真正并发由 OcrPool (3 个独立 worker 进程) 提供, 主进程这把锁只是防 "busy" 崩.
    _inference_lock = threading.Lock()

    def __init__(self, max_rounds: int = 20):
        self.max_rounds = max_rounds

    @classmethod
    def warmup(cls):
        """预热 OCR 引擎（启动时调用一次）

        后端选择优先级：
          1. config/runtime.json 里 ocr_backend.ocr_params (auto_configure 写的)
          2. 默认走 OpenVINO (Intel CPU/iGPU/dGPU 全兼容; thread-safe; 无 DML 那套
             pybind11 utf-8 解码 bug; 虚拟显卡环境也能落 CPU 跑)
          3. 装不到 OpenVINO 才退到 onnxruntime CPU (RapidOCR 默认)

        DirectML 路径已弃用 — 中文 Windows 上 onnxruntime C++ 异常字符串走 ANSI
        codepage (GBK), pybind11 用 utf-8 strict 解必炸; 虚拟显卡上 op 支持也不
        全, 实测 6 实例并发不可靠.

        速度参考 (RapidOCR PP-OCRv4_mobile, 960×540 单帧):
          CPU (基准)     ~1500ms
          OpenVINO CPU   ~30-80ms (Intel CPU 编译器优化)
          OpenVINO iGPU  ~30-50ms
          DirectML       50-250ms 但偶发 utf-8 崩 → 弃
        """
        if cls._shared_ocr is not None:
            return

        # 注意: yolo_dismisser 还是用 onnxruntime, 这个 patch 对 yolo 仍有保护意义.
        # OCR 这边走 OpenVINO 后已经不踩这条路.
        try:
            from . import _onnxruntime_patch
            _onnxruntime_patch.apply()
        except Exception as e:
            logger.debug(f"[ocr] onnxruntime patch 跳过: {e}")

        logger.info("预热 RapidOCR ...")
        from rapidocr import RapidOCR

        params = cls._load_ocr_params_from_config()
        if not params:
            # 默认走 OpenVINO; OpenVINO 装了就用, 没装 RapidOCR 自己会 raise,
            # 我们 except 后退回 onnxruntime CPU.
            try:
                import openvino  # noqa: F401  探测可用性
                params = {
                    "Det.engine_type": "openvino",
                    "Cls.engine_type": "openvino",
                    "Rec.engine_type": "openvino",
                }
                logger.info("RapidOCR: 默认 OpenVINO 后端 (CPU/iGPU/dGPU 自适应, thread-safe)")
            except ImportError:
                logger.info(
                    "RapidOCR: OpenVINO 未装 → 退到 onnxruntime CPU. "
                    "建议: pip install openvino"
                )

        # RapidOCR 3.x 要求 engine_type / lang_type / model_type 必须是对应 Enum 实例,
        # 不接受字符串. JSON 只能存字符串, 这里统一转一遍 (string→enum).
        params = cls._params_strings_to_enums(params)

        try:
            cls._shared_ocr = RapidOCR(params=params) if params else RapidOCR()
        except Exception as e:
            # OpenVINO 启动失败 (例如 model 不兼容), 退到默认 onnxruntime CPU
            logger.warning(f"RapidOCR 启自定义 params 失败, 退默认: {e}")
            cls._shared_ocr = RapidOCR()
        logger.info("RapidOCR 预热完成")

        # 启动多进程 OCR 池（让多实例 asyncio 真正并发）
        # workers 数：环境变量 GAMEBOT_OCR_WORKERS 优先，否则按 CPU 核数算
        try:
            OcrPool.init(ocr_params=params)
            logger.info(f"OCR Pool: {OcrPool.stats()}")
        except Exception as e:
            logger.warning(f"OcrPool 初始化失败（fallback 主进程 OCR）: {e}")

    @staticmethod
    def _load_ocr_params_from_config() -> dict:
        """从 config/runtime.json 读 ocr_backend.ocr_params。失败返回空 dict。"""
        import json as _json
        import os as _os
        import sys as _sys
        # frozen-aware：与 roi_config 同款多路径
        candidates = []
        meipass = getattr(_sys, "_MEIPASS", None)
        if meipass:
            candidates.append(_os.path.join(meipass, "config", "runtime.json"))
        if getattr(_sys, "frozen", False):
            candidates.append(_os.path.join(
                _os.path.dirname(_os.path.abspath(_sys.executable)),
                "config", "runtime.json",
            ))
        candidates.append(_os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
            "config", "runtime.json",
        ))
        for p in candidates:
            if _os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        cfg = _json.load(f)
                    params = cfg.get("ocr_backend", {}).get("ocr_params", {})
                    if params:
                        logger.info(f"RapidOCR: 从 {p} 读到 ocr_params={params}")
                    return params or {}
                except Exception as e:
                    logger.warning(f"读 {p} 失败：{e}")
        return {}

    @staticmethod
    def _params_strings_to_enums(params: dict) -> dict:
        """RapidOCR 3.x 严格要求 *.engine_type / *.lang_type / *.model_type 是 Enum.
        但 runtime.json 只能存字符串. 这里把已知字段的字符串转成对应 Enum 实例.
        未知字段 / 已经是 Enum 的值不动."""
        if not params:
            return params
        try:
            from rapidocr import EngineType, LangDet, LangRec, ModelType, OCRVersion
        except ImportError:
            # rapidocr 老版本没这些 Enum, 直接返回 (params 是字符串也能 work)
            return params
        # 字段名后缀 → Enum 类的映射
        suffix_to_enum = {
            "engine_type": EngineType,
            "lang_type": (LangDet, LangRec),     # det/rec 用不同 lang enum, 按 prefix 选
            "model_type": ModelType,
            "ocr_version": OCRVersion,
        }
        out = {}
        for k, v in params.items():
            if not isinstance(v, str):
                out[k] = v
                continue
            converted = False
            for suffix, enum_target in suffix_to_enum.items():
                if not k.endswith("." + suffix):
                    continue
                if isinstance(enum_target, tuple):
                    # lang_type: Det/Cls.* → LangDet, Rec.* → LangRec
                    LangDetCls, LangRecCls = enum_target
                    enum_cls = LangRecCls if k.startswith("Rec.") else LangDetCls
                else:
                    enum_cls = enum_target
                # 用 v.lower() 找 enum 成员; OPENVINO / openvino 都接受
                target = v.upper() if hasattr(enum_cls, v.upper()) else v
                try:
                    out[k] = enum_cls[target]
                    converted = True
                    break
                except KeyError:
                    # 可能 enum 用 value 不是 name (如 LangDet.CH = "ch")
                    for member in enum_cls:
                        if str(member.value).lower() == v.lower():
                            out[k] = member
                            converted = True
                            break
                    break
            if not converted:
                out[k] = v
        return out

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # OCR引擎
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _get_ocr(self):
        if OcrDismisser._shared_ocr is None:
            OcrDismisser.warmup()
        return OcrDismisser._shared_ocr

    @dataclass
    class TextHit:
        text: str
        cx: int
        cy: int

    @_ocr_cached_full
    def _ocr_all(self, screenshot: np.ndarray) -> list:
        """OCR全屏，返回 [TextHit, ...]

        @_ocr_cached_full：全帧 16×16 指纹缓存，loading/popup 静止画面 80%+ 命中（0ms 返回）。

        异常容错: ONNX (尤其 DML 在中文 Windows) 偶发 RuntimeError /
        UnicodeDecodeError, 这里转空 hits, 保证 worker 不死 — 上游会按"没找到"重试.
        """
        with metrics.timed("ocr_full") as tags:
            h, w = screenshot.shape[:2]
            tags["w"], tags["h"] = w, h
            ocr = self._get_ocr()
            try:
                with OcrDismisser._inference_lock:
                    result = ocr(screenshot)
            except (RuntimeError, UnicodeDecodeError) as e:
                logger.warning(f"[ocr] _ocr_all 推理异常 (返回空): {e}")
                tags["n_texts"] = 0
                tags["err"] = type(e).__name__
                return []
            hits = []
            if result and result.boxes is not None:
                for box, text, conf in zip(result.boxes, result.txts, result.scores):
                    xs = [p[0] for p in box]
                    ys = [p[1] for p in box]
                    cx = int(sum(xs) / 4)
                    cy = int(sum(ys) / 4)
                    hits.append(self.TextHit(text=text, cx=cx, cy=cy))
            tags["n_texts"] = len(hits)
            return hits

    async def _ocr_all_async(self, screenshot: np.ndarray) -> list:
        """全屏 OCR 的异步版本 — 路由到多进程 pool 让 N 实例真正并发。

        Pool 启用 → 跑 worker 进程，主进程不卡（最理想）
        Pool 失败 → 自动降级到默认 ThreadPool（asyncio loop 仍非阻塞）
        """
        if OcrPool.is_enabled() and OcrPool._executor is not None:
            with metrics.timed("ocr_full_pool") as tags:
                hits_raw = await OcrPool.ocr_async(screenshot)
                if hits_raw:  # pool 正常返回
                    tags["n_texts"] = len(hits_raw)
                    return [
                        self.TextHit(text=h.text, cx=h.cx, cy=h.cy)
                        for h in hits_raw
                    ]
                # pool 返回空（已自动禁用） → fall through 到下面 ThreadPool
        # fallback：默认 ThreadPool 跑同步 _ocr_all（不阻塞 loop）
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._ocr_all, screenshot)

    def _ocr_roi_named(self, screenshot: np.ndarray, name: str) -> list:
        """按 config/roi.yaml 的命名 ROI 裁剪 + (可选预处理) + OCR.
        用法:
            ocr._ocr_roi_named(shot, "team_btn_left")
        预处理在 yaml.preprocessing 里 (用户在 OCR 调试页保存的). 没配就走原路径.
        """
        from .roi_config import get as _get, get_preprocessing
        x1, y1, x2, y2, scale = _get(name)
        prep = get_preprocessing(name)
        if not prep:
            # 无预处理 — 走原路径 (含 cache)
            return self._ocr_roi(screenshot, x1, y1, x2, y2, scale=scale)
        # 有预处理 — 走非 cache 路径 (cache key 不含 preprocessing, 直接重新跑)
        return self._ocr_roi_with_preproc(screenshot, x1, y1, x2, y2, scale, prep)

    def _ocr_roi_with_preproc(self, screenshot: np.ndarray, x1: float, y1: float,
                              x2: float, y2: float, scale: int, methods: list) -> list:
        """带预处理的 ROI OCR (不走 cache, 因为 cache key 不含预处理).
        实现跟 _ocr_roi 一致, 多了 preprocessing 那一步."""
        from .image_preproc import apply_preprocessing
        with metrics.timed("ocr_roi_preproc",
                           roi=f"{x1:.2f},{y1:.2f},{x2:.2f},{y2:.2f}",
                           scale=scale, n_prep=len(methods)) as tags:
            h, w = screenshot.shape[:2]
            px1, py1 = int(w * x1), int(h * y1)
            px2, py2 = int(w * x2), int(h * y2)
            crop = screenshot[py1:py2, px1:px2]
            if crop.size == 0:
                tags["n_texts"] = 0
                return []
            if scale > 1:
                crop = cv2.resize(crop, (0, 0), fx=scale, fy=scale,
                                  interpolation=cv2.INTER_CUBIC)
            crop = apply_preprocessing(crop, methods)
            ocr = self._get_ocr()
            try:
                with OcrDismisser._inference_lock:
                    result = ocr(crop)
            except (RuntimeError, UnicodeDecodeError) as e:
                logger.warning(f"[ocr] _ocr_roi_with_preproc 推理异常 (返回空): {e}")
                tags["n_texts"] = 0
                tags["err"] = type(e).__name__
                return []
            hits = []
            if result and result.boxes is not None:
                for box, text, _conf in zip(result.boxes, result.txts, result.scores):
                    xs = [p[0] for p in box]
                    ys = [p[1] for p in box]
                    cx = int(sum(xs) / 4 / scale) + px1
                    cy = int(sum(ys) / 4 / scale) + py1
                    hits.append(self.TextHit(text=text, cx=cx, cy=cy))
            tags["n_texts"] = len(hits)
            return hits

    @_ocr_cached
    def _ocr_roi(self, screenshot: np.ndarray, x1: float, y1: float,
                 x2: float, y2: float, scale: int = 2) -> list:
        """裁剪 ROI 区域 + 放大后 OCR，提高小文字准确率

        坐标为比例 (0.0~1.0)，自动转换为像素。
        放大 scale 倍后识别，坐标映射回原图。

        用法（裸坐标）：
          _ocr_roi(shot, 0, 0, 0.1, 1.0)  # 左侧栏 (0~10% 宽度)
        命名 ROI（推荐，从 config/roi.yaml 读）：
          _ocr_roi_named(shot, "team_btn_left")

        缓存：被 @cached 装饰，TTL 内同一帧同一 ROI 结果直接命中（见 ocr_cache.py）。
        """
        with metrics.timed("ocr_roi", roi=f"{x1:.2f},{y1:.2f},{x2:.2f},{y2:.2f}", scale=scale) as tags:
            h, w = screenshot.shape[:2]
            px1, py1 = int(w * x1), int(h * y1)
            px2, py2 = int(w * x2), int(h * y2)
            crop = screenshot[py1:py2, px1:px2]

            if crop.size == 0:
                tags["n_texts"] = 0
                return []

            # 放大提高小文字识别率
            if scale > 1:
                crop = cv2.resize(crop, (0, 0), fx=scale, fy=scale,
                                  interpolation=cv2.INTER_CUBIC)

            ocr = self._get_ocr()
            try:
                with OcrDismisser._inference_lock:
                    result = ocr(crop)
            except (RuntimeError, UnicodeDecodeError) as e:
                logger.warning(f"[ocr] _ocr_roi 推理异常 (返回空): {e}")
                tags["n_texts"] = 0
                tags["err"] = type(e).__name__
                return []
            hits = []
            if result and result.boxes is not None:
                for box, text, conf in zip(result.boxes, result.txts, result.scores):
                    xs = [p[0] for p in box]
                    ys = [p[1] for p in box]
                    # 映射回原图坐标
                    cx = int(sum(xs) / 4 / scale) + px1
                    cy = int(sum(ys) / 4 / scale) + py1
                    hits.append(self.TextHit(text=text, cx=cx, cy=cy))
            tags["n_texts"] = len(hits)
            return hits

    @staticmethod
    def fuzzy_match(text: str, keyword: str, max_distance: int = 1) -> bool:
        """模糊匹配：编辑距离 <= max_distance 视为匹配

        解决 OCR 常见误识别：
          "组队" → "如WB", "确定" → "确宝", "关闭" → "关内"
        """
        # 先精确匹配（快速路径）
        if keyword in text:
            return True

        # 滑动窗口模糊匹配
        klen = len(keyword)
        for i in range(max(0, len(text) - klen - max_distance),
                       min(len(text), len(text) - klen + max_distance + 1)):
            window = text[i:i + klen]
            if len(window) != klen:
                continue
            dist = sum(1 for a, b in zip(window, keyword) if a != b)
            if dist <= max_distance:
                return True
        return False
