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


class ScreenState(str, Enum):
    LOBBY = "lobby"           # 大厅，无弹窗
    POPUP = "popup"           # 有弹窗遮挡
    LOADING = "loading"       # 游戏加载中
    LOGIN = "login"           # 登录页
    LEFT_GAME = "left_game"   # 退出了游戏
    UNKNOWN = "unknown"


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


def _live_rules() -> dict:
    """每次调用拿最新规则（自动 mtime 检测 reload）"""
    return RulesLoader.get()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 禁点 ROI（永远不该 tap 的区域，用相对坐标防分辨率漂移）
# 这些位置对应 PUBG/和平精英 的固定 native UI：
#   - 右侧栏：公告/静音/帮助/修复/注销/上报日志（一直可见）
#   - 顶部防沉迷广播条
#   - 右下"16+ 适龄提示"
# 模板/OCR/形状任何一路命中这里都会被 _is_never_tap 拒绝
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_NEVER_TAP_RECTS = [
    # 右侧栏（公告/静音/帮助/修复/注销/上报日志）— y 从 0.18 起，**留出顶部右上角**
    # 给弹窗 X（小马宝莉/抽奖等弹窗 X 在 y∈[0.05, 0.17] 范围）。旧 (0.91, 0, 1, 0.95)
    # 把弹窗 X 也圈进去导致点不到。
    (0.91, 0.18, 1.0, 0.95),
    # 顶部防沉迷广播（中央长条文字，不到右边角）— x 限 0.85 留右上角给弹窗 X
    (0.0, 0.0, 0.85, 0.05),
    # 右下"16+ 适龄"标
    (0.94, 0.86, 1.0, 1.0),
]


def _is_never_tap(x: int, y: int, screen_w: int, screen_h: int) -> bool:
    """判断 (x,y) 是否落在禁点 ROI"""
    if screen_w <= 0 or screen_h <= 0:
        return False
    rx, ry = x / screen_w, y / screen_h
    for x1, y1, x2, y2 in _NEVER_TAP_RECTS:
        if x1 <= rx <= x2 and y1 <= ry <= y2:
            return True
    return False


def _find_dialog_rect(screenshot: np.ndarray) -> "tuple[int, int, int, int] | None":
    """
    检测中央"亮色 dialog"矩形（公告/活动/确认弹窗的共同结构）。
    前提：背景被半透明蒙层压暗（_has_overlay True 时调用最稳）。

    返回 (x, y, w, h)，找不到 → None。

    工作原理：
      1. 二值化 gray > 170（dialog 主体白底/淡色）
      2. 形态学闭运算合并文字间隙
      3. 找最大的"中央"contour（不贴边、不全屏）
    """
    h, w = screenshot.shape[:2]
    gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)

    _, bright = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_area = 0
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        # 过滤：dialog 必须够大（占屏 30%+ 任一边）、不超 97%（保留 1px 余量给非全屏判断）
        # 旧上限 0.92 漏掉了"幸运开启抽奖"等近全屏活动弹窗 (~96%)
        if cw < w * 0.30 or ch < h * 0.30:
            continue
        if cw > w * 0.97 or ch > h * 0.97:
            continue
        cx = x + cw // 2
        # 横向居中（左右各 15% 余量）
        if not (w * 0.15 < cx < w * 0.85):
            continue
        area = cw * ch
        if area > best_area:
            best_area = area
            best = (x, y, cw, ch)
    return best


@dataclass
class DismissResult:
    success: bool
    popups_closed: int
    final_state: str
    rounds: int


class OcrDismisser:
    """状态机驱动的弹窗清理器"""

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

        # 注意: yolo_detector 还是用 onnxruntime, 这个 patch 对 yolo 仍有保护意义.
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

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 状态检测
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _has_overlay(self, screenshot: np.ndarray) -> bool:
        """
        检测是否有弹窗遮罩层。
        原理：弹窗会在画面上叠加半透明黑色遮罩，
        导致屏幕边缘区域（遮罩可见处）整体变暗。
        """
        gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        # 采样四个角落区域（遮罩最明显的地方）
        corners = [
            gray[0:60, 0:60],           # 左上
            gray[0:60, w-60:w],         # 右上
            gray[h-60:h, 0:60],         # 左下
            gray[h-60:h, w-60:w],       # 右下
        ]
        avg_corner_brightness = np.mean([c.mean() for c in corners])

        # 采样中央区域（弹窗面板通常更亮）
        center = gray[h//4:3*h//4, w//4:3*w//4]
        avg_center_brightness = center.mean()

        # 如果四角很暗（<50）且中央明显更亮 → 有遮罩
        has_dark_corners = avg_corner_brightness < 50
        center_brighter = avg_center_brightness > avg_corner_brightness + 40

        return has_dark_corners and center_brighter

    def detect_state(self, screenshot: np.ndarray, matcher=None, ocr_hits=None) -> ScreenState:
        """
        判断当前屏幕状态。
        先用快速方法（模板/亮度），不够再用OCR。
        """
        # 快速检查：模板匹配大厅
        if matcher:
            lobby_hit = matcher.find_any(screenshot, ["lobby_start_btn", "lobby_start_game"], threshold=0.85)
            if lobby_hit:
                # 有"开始游戏" — 检查是否有弹窗遮挡
                if self._has_overlay(screenshot):
                    return ScreenState.POPUP
                return ScreenState.LOBBY

        # 检查遮罩（不需要OCR，纯像素分析）
        if self._has_overlay(screenshot):
            return ScreenState.POPUP

        # 需要OCR来判断
        if ocr_hits is None:
            ocr_hits = self._ocr_all(screenshot)
        all_text = " ".join(h.text for h in ocr_hits)

        rules = _live_rules()  # 热加载：每次拿最新规则
        if any(kw in all_text for kw in rules["lobby_keywords"]):
            return ScreenState.LOBBY
        if any(kw in all_text for kw in rules["left_game_keywords"]):
            return ScreenState.LEFT_GAME
        if any(kw in all_text for kw in rules["login_keywords"]):
            return ScreenState.LOGIN
        if any(kw in all_text for kw in rules["loading_keywords"]):
            return ScreenState.LOADING

        return ScreenState.UNKNOWN

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 关闭目标查找
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _find_close_target(self, screenshot: np.ndarray, matcher=None) -> tuple[int, int, str] | None:
        """
        找弹窗的关闭目标，返回 (x, y, 方法描述) 或 None
        优先级：模板X → OCR关闭文字 → OCR确认文字 → 几何形状
        所有命中都过 _is_never_tap 过滤（右侧栏/广播条/角龄标永远不点）
        """
        sh, sw = screenshot.shape[:2]

        # 级别1: 模板匹配找X按钮 (~20ms) — threshold 0.72（旧 0.80 太严，UI 微变易失配）
        if matcher:
            x_hit = matcher.find_close_button(screenshot)
            if x_hit and x_hit.confidence > 0.72:
                if not _is_never_tap(x_hit.cx, x_hit.cy, sw, sh):
                    return (x_hit.cx, x_hit.cy, f"模板:{x_hit.name}({x_hit.confidence:.2f})")
                logger.debug(f"[dismiss] 模板 {x_hit.name} 命中禁点 ROI ({x_hit.cx},{x_hit.cy})，跳过")

        # 级别2: OCR找关闭类文字 (~200ms)
        hits = self._ocr_all(screenshot)
        rules = _live_rules()  # 热加载：每次拿最新规则

        # 注：原"勾选 今日内不再弹出"路径已禁用 — 实测多数活动弹窗的 checkbox
        # 不可交互（点了 frame 不变），bot 反复重试到 timeout。用户明确要求"不用管
        # 这个，直接关弹窗"。如果哪天确实需要勾选行为，从 confirm_text 加关键字
        # 或重新打开下面这段。
        # for h in hits:
        #     if _is_never_tap(h.cx, h.cy, sw, sh):
        #         continue
        #     for kw in rules["checkbox_text"]:
        #         if kw in h.text:
        #             return (h.cx, h.cy, f"勾选:{h.text}")

        # 找关闭按钮
        for h in hits:
            if _is_never_tap(h.cx, h.cy, sw, sh):
                continue
            for kw in rules["close_text"]:
                if kw in h.text:
                    return (h.cx, h.cy, f"关闭:{h.text}")

        # 找确认按钮
        for h in hits:
            if _is_never_tap(h.cx, h.cy, sw, sh):
                continue
            for kw in rules["confirm_text"]:
                if kw in h.text:
                    # "点击屏幕"类 → 点击屏幕中央而不是文字位置
                    if "屏幕" in kw or "继续" in kw:
                        return (sw // 2, sh // 2, f"点击屏幕:{h.text}")
                    return (h.cx, h.cy, f"确认:{h.text}")

        # 级别3: 形状检测 — 触发条件：有遮罩 OR 检测到 dialog rect
        # 旧逻辑只看 _has_overlay，对全屏活动弹窗（4 角不暗）失效；
        # 新逻辑加上 dialog 检测，覆盖近全屏弹窗。
        if self._has_overlay(screenshot) or _find_dialog_rect(screenshot) is not None:
            pos = self._find_x_shape(screenshot)
            if pos and not _is_never_tap(pos[0], pos[1], sw, sh):
                return (pos[0], pos[1], "形状检测X")

        return None

    def _find_x_shape(self, screenshot: np.ndarray) -> tuple[int, int] | None:
        """
        找关闭按钮（X 形状或圆形 ⊗）。
        策略：
          1. 优先检测中央 dialog rect → 在 dialog 右上角内 100x80 的小 ROI 搜 X
             这是抗 UI 变化的关键：不管公告/活动/确认弹窗，结构都一样
          2. dialog 找到但 X 没扫到 → 兜底点 dialog 右上角内偏 (rect.right-30, rect.top+30)
          3. 没检测到 dialog → 退回旧逻辑（右半屏上 2/3）但加禁点 ROI 过滤
        """
        h, w = screenshot.shape[:2]

        # 优先：dialog rect 引导的精准搜
        dialog = _find_dialog_rect(screenshot)
        if dialog is not None:
            dx, dy, dw, dh = dialog
            sub_x = max(0, dx + dw - 100)
            sub_y = max(0, dy)
            sub_w = min(w - sub_x, 100)
            sub_h = min(80, dh // 4)
            if sub_w > 20 and sub_h > 20:
                pos = self._scan_x_in_roi(
                    screenshot[sub_y:sub_y + sub_h, sub_x:sub_x + sub_w],
                    sub_x, sub_y,
                )
                if pos and not _is_never_tap(pos[0], pos[1], w, h):
                    logger.info(f"[shape] dialog 内 X: ({pos[0]},{pos[1]}) "
                                f"dialog=({dx},{dy},{dw}x{dh})")
                    return pos
            # X 没扫到 → 兜底点 dialog 右上角
            fb_x, fb_y = dx + dw - 30, dy + 30
            if not _is_never_tap(fb_x, fb_y, w, h):
                logger.info(f"[shape] dialog 兜底点右上角 ({fb_x},{fb_y}) "
                            f"dialog=({dx},{dy},{dw}x{dh})")
                return (fb_x, fb_y)

        # 兜底 1：右上角固定小 ROI（专治"全屏弹窗 + dialog 检测失败 + overlay 失败"）
        # ROI: x∈[0.78w, 0.92w], y∈[0, 0.18h]，刚好是游戏弹窗 X 关闭按钮的标准位置
        # 不依赖 dialog rect 也不依赖 _has_overlay → 永远跑一遍这个小区域
        # 风险有限：禁点 ROI 已排除 x>0.91 的右侧栏，剩下的角落区域基本只能是弹窗 X
        rx0, rx1 = int(w * 0.78), int(w * 0.92)
        ry0, ry1 = 0, int(h * 0.18)
        if rx1 > rx0 and ry1 > ry0:
            roi = screenshot[ry0:ry1, rx0:rx1]
            pos = self._scan_x_in_roi(roi, rx0, ry0)
            if pos and not _is_never_tap(pos[0], pos[1], w, h):
                logger.info(f"[shape] 右上角 X: ({pos[0]},{pos[1]})")
                return pos

        # 兜底 2：旧逻辑（右半屏上 2/3）
        roi = screenshot[0:h * 2 // 3, w // 3:]
        pos = self._scan_x_in_roi(roi, w // 3, 0)
        if pos and not _is_never_tap(pos[0], pos[1], w, h):
            return pos
        return None

    def _scan_x_in_roi(
        self, roi: np.ndarray, ox: int, oy: int,
    ) -> tuple[int, int] | None:
        """
        在给定 ROI 里扫 X / 圆 ⊗，返回全屏坐标 (x, y) 或 None。
        ox, oy: ROI 在原图中的左上角偏移
        """
        if roi is None or roi.size == 0:
            return None
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

        # ── 方法1: 边缘轮廓找方形X ──
        edges = cv2.Canny(gray, 100, 200)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            area = cv2.contourArea(c)
            x, y, cw, ch = cv2.boundingRect(c)
            if not (200 < area < 3000):
                continue
            if not (15 < cw < 60 and 15 < ch < 60):
                continue
            if not (0.5 < cw / max(ch, 1) < 2.0):
                continue
            cx_local = x + cw // 2
            cy_local = y + ch // 2
            if cy_local >= gray.shape[0] or cx_local >= gray.shape[1]:
                continue
            center_val = gray[cy_local, cx_local]
            surround = gray[max(0, y):y + ch, max(0, x):x + cw].mean()
            if center_val < surround - 20:
                return (ox + cx_local, oy + cy_local)

        # ── 方法2: 霍夫圆找圆形关闭按钮 ──
        blurred = cv2.GaussianBlur(gray, (9, 9), 2)
        circles = cv2.HoughCircles(
            blurred, cv2.HOUGH_GRADIENT, dp=1.2,
            minDist=30, param1=100, param2=40,
            minRadius=12, maxRadius=35,
        )
        if circles is not None:
            for circle in circles[0]:
                cx, cy, r = int(circle[0]), int(circle[1]), int(circle[2])
                region = gray[max(0, cy - r):cy + r, max(0, cx - r):cx + r]
                if region.size == 0:
                    continue
                inner_mean = region.mean()
                oy1, oy2 = max(0, cy - r * 2), min(gray.shape[0], cy + r * 2)
                ox1, ox2 = max(0, cx - r * 2), min(gray.shape[1], cx + r * 2)
                outer_mean = gray[oy1:oy2, ox1:ox2].mean()
                if inner_mean < outer_mean - 30:
                    return (ox + cx, oy + cy)

        return None

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 主循环
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def dismiss_all(self, device, matcher=None) -> DismissResult:
        """
        状态机驱动的弹窗清理。
        速度优化：模板命中时快速连点(0.5s/轮)，只有需要OCR时才慢(2s/轮)
        大厅确认：连续3次检测到大厅且无遮罩才算真正到达（弹窗可能1-2秒后冒出来）
        """
        popups_closed = 0
        stuck_count = 0
        lobby_confirm = 0
        LOBBY_CONFIRM_NEEDED = 2

        for rnd in range(self.max_rounds):
            shot = await device.screenshot()
            if shot is None:
                await asyncio.sleep(0.3)
                continue

            # YOLO 训练数据采集：每轮开头采一次（pHash dedup 自动去重）
            # 这是关键：公告/活动这种模板快速命中的弹窗，OCR 路径不会跑到，
            # 之前没采集，所以训练集缺了这类样本。在循环顶部采就能覆盖。
            try:
                from .screenshot_collector import collect as _yolo_collect
                _yolo_collect(shot, tag=f"dismiss_R{rnd+1:02d}")
            except Exception:
                pass

            # ━━ 快速路径: 模板匹配找X (~20ms) ━━
            # threshold 0.72（旧 0.80 太严，公告/活动 UI 微变就失配）
            # 加 _is_never_tap 过滤，防止模板误匹到右侧栏
            if matcher:
                x_hit = matcher.find_close_button(shot)
                if x_hit and x_hit.confidence > 0.72:
                    sh, sw = shot.shape[:2]
                    if _is_never_tap(x_hit.cx, x_hit.cy, sw, sh):
                        logger.debug(f"[R{rnd+1}] 模板 {x_hit.name} 命中禁点 ROI "
                                     f"({x_hit.cx},{x_hit.cy})，跳过")
                    else:
                        logger.info(f"[R{rnd+1}] 快速关闭: {x_hit.name}({x_hit.confidence:.2f}) "
                                    f"@ ({x_hit.cx},{x_hit.cy})")
                        await device.tap(x_hit.cx, x_hit.cy)
                        popups_closed += 1
                        stuck_count = 0
                        lobby_confirm = 0
                        await asyncio.sleep(0.5)
                        continue

            # ━━ 快速路径: 模板匹配大厅 + 无遮罩 (~30ms) ━━
            if matcher and matcher.is_at_lobby(shot) and not self._has_overlay(shot):
                lobby_confirm += 1
                if lobby_confirm >= LOBBY_CONFIRM_NEEDED:
                    logger.info(f"[R{rnd+1}] ✓ 大厅确认{lobby_confirm}次，完成！关闭{popups_closed}个弹窗")
                    return DismissResult(True, popups_closed, "lobby", rnd + 1)
                logger.info(f"[R{rnd+1}] 大厅检测 ({lobby_confirm}/{LOBBY_CONFIRM_NEEDED})")
                await asyncio.sleep(0.3)  # 短等，快速二次确认
                continue

            # ━━ 慢速路径: 需要OCR分析 ━━
            from .adb_lite import phash, phash_distance

            # 1) 稳定性检测：动画期间画面剧烈变化时不 OCR（OCR 结果会半成品 + 浪费 worker）
            #    再截一帧 150ms 后比对。差异大 → 在动画中，等下一轮再判
            await asyncio.sleep(0.15)
            shot2 = await device.screenshot()
            if shot2 is not None:
                ANIM_DIFF_THRESHOLD = 8
                diff = phash_distance(phash(shot), phash(shot2))
                if diff > ANIM_DIFF_THRESHOLD:
                    logger.debug(f"[R{rnd+1}] 帧动画中 (diff={diff} > {ANIM_DIFF_THRESHOLD})，等稳定")
                    await asyncio.sleep(0.4)
                    continue
                shot = shot2  # 用最新稳定帧

            # 2) 重复帧跳过：跟上一轮已 OCR 过的帧太相似 → 复用决策
            h = phash(shot)
            if not hasattr(self, '_last_ph'):
                self._last_ph = 0
            if phash_distance(h, self._last_ph) < 4:
                logger.debug(f"[R{rnd+1}] 帧差跳过 OCR (跟上轮相似)")
                await asyncio.sleep(0.5)
                continue
            self._last_ph = h

            # OCR 走 async + pool（多实例真正并发，不阻塞 asyncio loop）
            ocr_hits = await self._ocr_all_async(shot)
            state = self.detect_state(shot, matcher, ocr_hits=ocr_hits)
            logger.info(f"[R{rnd+1}] 状态: {state.value}")

            # 顺手为 YOLO 训练采集这帧（pHash dedup 保证不重复）
            # tag 格式 phase__OCRclaim：dismiss_popups__detect_lobby —— phase 准，OCR 端待 verify 验证
            try:
                from .screenshot_collector import collect as _yolo_collect
                _yolo_collect(shot, tag=f"dismiss_popups__detect_{state.value}")
            except Exception:
                pass

            if state == ScreenState.LOBBY:
                lobby_confirm += 1
                if lobby_confirm >= LOBBY_CONFIRM_NEEDED:
                    logger.info(f"[R{rnd+1}] ✓ 大厅确认{lobby_confirm}次(OCR)，完成！")
                    return DismissResult(True, popups_closed, "lobby", rnd + 1)
                logger.info(f"[R{rnd+1}] 大厅检测OCR ({lobby_confirm}/{LOBBY_CONFIRM_NEEDED})")
                await asyncio.sleep(0.4)
                continue

            if state == ScreenState.LEFT_GAME:
                logger.warning(f"[R{rnd+1}] ✗ 已退出游戏")
                return DismissResult(False, popups_closed, "left_game", rnd + 1)

            if state == ScreenState.LOGIN:
                logger.info(f"[R{rnd+1}] 登录页，等待...")
                await asyncio.sleep(3)
                continue

            if state == ScreenState.LOADING:
                logger.info(f"[R{rnd+1}] 加载中...")
                await asyncio.sleep(2)
                continue

            # 非大厅状态 → 重置大厅计数
            lobby_confirm = 0

            # ━━ POPUP/UNKNOWN: OCR找关闭目标 ━━
            target = self._find_close_target(shot, matcher)
            if target:
                x, y, method = target
                logger.info(f"[R{rnd+1}] 点击: {method} @ ({x},{y})")
                await device.tap(x, y)
                popups_closed += 1
                stuck_count = 0

                # 勾选复选框后紧接找X
                if "勾选" in method:
                    await asyncio.sleep(0.5)
                    shot2 = await device.screenshot()
                    if shot2 is not None:
                        target2 = self._find_close_target(shot2, matcher)
                        if target2 and "勾选" not in target2[2]:
                            logger.info(f"[R{rnd+1}] 勾选后点: {target2[2]} @ ({target2[0]},{target2[1]})")
                            await device.tap(target2[0], target2[1])

                await asyncio.sleep(0.5)

                # 点击效果验证：动画期间按钮 visible 但未激活 → 点击无效，重试一次
                #   tap 后等 600ms（含上面 0.5s + 0.1 截图），跟点击前对比
                #   pHash 距离 < 3 = 几乎没变 = 点击没生效（按钮未激活）
                verify_shot = await device.screenshot()
                if verify_shot is not None:
                    diff_after = phash_distance(phash(shot), phash(verify_shot))
                    if diff_after < 3:
                        logger.info(f"[R{rnd+1}] 点击无效果（diff={diff_after}，按钮可能未激活），重试")
                        await asyncio.sleep(0.6)  # 等动画再多一点
                        await device.tap(x, y)
                        await asyncio.sleep(0.4)
                continue

            # ━━ 什么都没找到 ━━
            stuck_count += 1
            logger.info(f"[R{rnd+1}] 未找到目标 (stuck={stuck_count})")
            if stuck_count >= 2:
                sh, sw = shot.shape[:2]
                await device.tap(sw // 2, sh // 2)  # 点屏幕中央
            if stuck_count >= 4 and matcher and matcher.is_at_lobby(shot):
                return DismissResult(True, popups_closed, "lobby_forced", rnd + 1)

            await asyncio.sleep(0.6)

        logger.warning(f"弹窗清理超时 ({self.max_rounds}轮)")
        return DismissResult(False, popups_closed, "timeout", self.max_rounds)

    # ── 兼容旧接口 ──

    def ocr_screen(self, screenshot: np.ndarray):
        """供phase_launch_game使用"""
        return self._ocr_all(screenshot)
