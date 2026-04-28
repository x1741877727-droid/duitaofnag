"""
Decision Recorder — 决策可观测性

每次识别+决策都记录到磁盘，前端能完整回放：
  - 输入截图（机器看见的全屏）
  - 各 Tier 的工作过程（模板试过哪些 / YOLO bbox / OCR 文字）
  - 模板的图本身（让用户看 close_x_announce 长啥样）
  - ROI 区域（如果只看部分屏幕，把那块框出来）
  - 最终决策（点哪 / 用哪个 Tier 给的结果）
  - 验证结果（phash 前后对比）

存盘位置：
  <session_dir>/decisions/<timestamp>_inst{N}_{phase}/
    decision.json    # 完整决策记录
    input.jpg        # 输入帧
    yolo_annot.jpg   # YOLO 标注帧
    tap_annot.jpg    # 点击位置标注帧
    tmpl_<name>.png  # 试过的模板图（拷贝）
    roi_<name>.jpg   # ROI 区域裁剪
    ...

前端通过 /api/decisions 查询。
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# ── 中文文本渲染 (cv2.putText 不支持中文, 用 PIL 兜底) ──────────────────────

_CN_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",          # 微软雅黑 (Windows 默认有)
    "C:/Windows/Fonts/simhei.ttf",        # 黑体
    "/System/Library/Fonts/PingFang.ttc",  # macOS
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",  # Linux
]


def _resolve_cn_font():
    """返回第一个存在的中文字体路径, 找不到返回 None"""
    for p in _CN_FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


_CN_FONT_PATH = _resolve_cn_font()
_CN_FONT_CACHE: dict = {}  # size → ImageFont 实例


def _has_non_ascii(s: str) -> bool:
    try:
        s.encode("ascii")
        return False
    except UnicodeEncodeError:
        return True


def _put_texts_cn_batch(img: np.ndarray,
                         items: list,
                         max_pil_items: int = 30) -> np.ndarray:
    """
    批量画文本到 BGR 图. 比逐条 _put_text_cn 快 10-20x.
    items: list of (text, org=(x,y), color=(B,G,R), font_size:int)
    全 ASCII 走 cv2.putText (in-place); 含中文转一次 PIL 一起画完再转回.
    多于 max_pil_items 的中文条目会被截掉, 避免画一坨遮原图.
    """
    if not items:
        return img

    ascii_items = []
    cn_items = []
    for it in items:
        text, org, color, sz = it
        if _has_non_ascii(text):
            cn_items.append(it)
        else:
            ascii_items.append(it)

    # ASCII 条目: cv2 直接画, 快, in-place
    for text, org, color, sz in ascii_items:
        scale = max(0.3, sz / 22.0)
        cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale,
                    color, max(1, int(scale * 1.5)))

    # 中文条目: 一次性 BGR→PIL→画→BGR (而不是每条转一遍)
    if cn_items and _CN_FONT_PATH:
        try:
            from PIL import Image, ImageDraw, ImageFont
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            pil = Image.fromarray(rgb)
            draw = ImageDraw.Draw(pil)
            for text, org, color, sz in cn_items[:max_pil_items]:
                font = _CN_FONT_CACHE.get(sz)
                if font is None:
                    font = ImageFont.truetype(_CN_FONT_PATH, sz)
                    _CN_FONT_CACHE[sz] = font
                x, y = int(org[0]), int(org[1]) - sz
                draw.text((x, y), text,
                          fill=(int(color[2]), int(color[1]), int(color[0])),
                          font=font)
            bgr = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
            np.copyto(img, bgr)
        except Exception:
            # PIL 出错 fallback: 替换非 ASCII 为 ?
            for text, org, color, sz in cn_items:
                safe = "".join(c if ord(c) < 128 else "?" for c in text)
                cv2.putText(img, safe, org, cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    elif cn_items:
        # 没字体, 全部降级
        for text, org, color, sz in cn_items:
            safe = "".join(c if ord(c) < 128 else "?" for c in text)
            cv2.putText(img, safe, org, cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    return img


def _put_text_cn(img: np.ndarray, text: str, org: tuple, color: tuple,
                 font_size: int = 16) -> np.ndarray:
    """
    在 BGR 图上画文本, 支持中文.
    text 全 ASCII 时走 cv2.putText (快); 含非 ASCII 走 PIL (慢但正确).
    org: (x, y) 左下角 (cv2 风格); color: (B, G, R).
    返回 img 本身 (in-place 修改, 跟 cv2.putText 一致).
    """
    try:
        text.encode("ascii")
        scale = font_size / 22.0
        cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, max(0.3, scale),
                    color, max(1, int(scale * 1.5)))
        return img
    except UnicodeEncodeError:
        pass

    if _CN_FONT_PATH is None:
        # 没字体, 把非 ASCII 字符替换成 ?
        safe = "".join(c if ord(c) < 128 else "?" for c in text)
        cv2.putText(img, safe, org, cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
        return img

    try:
        from PIL import Image, ImageDraw, ImageFont
        font = _CN_FONT_CACHE.get(font_size)
        if font is None:
            font = ImageFont.truetype(_CN_FONT_PATH, font_size)
            _CN_FONT_CACHE[font_size] = font
        # BGR → RGB → PIL → 画 → 回 BGR
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb)
        draw = ImageDraw.Draw(pil)
        # cv2 putText 的 org 是基线左下; PIL 的 xy 是左上. 把 y 上移 font_size
        x, y = int(org[0]), int(org[1]) - font_size
        draw.text((x, y), text, fill=(int(color[2]), int(color[1]), int(color[0])), font=font)
        bgr = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
        np.copyto(img, bgr)
        return img
    except Exception:
        safe = "".join(c if ord(c) < 128 else "?" for c in text)
        cv2.putText(img, safe, org, cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
        return img


# ─────────────── 数据结构 ───────────────


@dataclass
class TemplateMatch:
    """单次模板匹配尝试"""
    name: str                       # 模板名 (lobby_start_btn)
    template_image: str = ""        # 模板图相对路径（前端可拉）
    score: float = 0.0              # match score
    threshold: float = 0.0          # 当时阈值
    hit: bool = False               # 是否命中
    bbox: Optional[list] = None     # 命中时 [x1,y1,x2,y2]
    scale: float = 1.0              # 命中时的 scale


@dataclass
class YoloDetection:
    """单次 YOLO 检测目标"""
    cls: str                        # close_x / action_btn
    conf: float
    bbox: list                      # [x1,y1,x2,y2]


@dataclass
class OcrHit:
    """OCR 识别一条文字"""
    text: str
    bbox: list                      # [x1,y1,x2,y2]
    conf: float = 0.0
    cx: int = 0
    cy: int = 0


@dataclass
class TierRecord:
    """一个 Tier 的工作记录"""
    tier: int                       # 0/1/2/3/4
    name: str                       # 模板/Memory/YOLO/OCR/VLM
    duration_ms: float = 0.0
    early_exit: bool = False        # 是否在此 Tier 命中并退出
    note: str = ""

    # 模板：尝试列表
    templates: list = field(default_factory=list)   # list[TemplateMatch]

    # YOLO
    yolo_detections: list = field(default_factory=list)  # list[YoloDetection]
    yolo_annot_image: str = ""      # 画了 bbox 的标注图

    # OCR
    ocr_hits: list = field(default_factory=list)    # list[OcrHit]
    ocr_roi: Optional[list] = None  # 如果只 OCR 局部，[x1,y1,x2,y2]
    ocr_roi_image: str = ""         # ROI 区域的截图（带框）

    # Memory
    memory_phash_query: str = ""
    memory_hit: Optional[dict] = None    # {phash, action, success}


@dataclass
class TapRecord:
    x: int
    y: int
    method: str                     # 哪一 Tier 决定的
    target_class: str = ""          # close_x / action_btn / lobby_start_btn ...
    target_text: str = ""           # OCR 读出的文字（如有）
    target_conf: float = 0.0
    annot_image: str = ""           # 画了红圈的标注图


@dataclass
class VerifyRecord:
    phash_before: str = ""
    phash_after: str = ""
    distance: int = 0
    success: Optional[bool] = None  # True=画面变=点中, False=没变, None=未验证


# ─────────────── Recorder（单例 + per-decision context）───────────────


class _Recorder:
    """全局单例。runner_service start_all 时初始化 session dir"""

    def __init__(self):
        self._lock = threading.Lock()
        self._root: Optional[Path] = None
        self._enabled = False
        # 内存索引（最近 N 条），加速前端查询
        self._index: list[dict] = []
        self._max_index = 500

    def init(self, session_dir: str | Path):
        with self._lock:
            self._root = Path(session_dir) / "decisions"
            self._root.mkdir(parents=True, exist_ok=True)
            self._enabled = True
            # 切 session 时清内存索引, 避免上次决策出现在新 session 的 list_recent 里
            self._index = []
            logger.info(f"[decision] 记录目录: {self._root}")

    def register_live_listener(self, cb) -> None:
        """注册 finalize 回调 (cb(decision_dict)). 用于 LiveBroadcaster 推 WS."""
        with self._lock:
            if not hasattr(self, "_listeners"):
                self._listeners = []
            self._listeners.append(cb)

    def _notify_listeners(self, decision_dict: dict) -> None:
        """finalize 后异步通知 listener. 不阻塞主决策路径."""
        listeners = list(getattr(self, "_listeners", []) or [])
        for cb in listeners:
            try:
                cb(decision_dict)
            except Exception as e:
                logger.debug(f"[decision] listener err: {e}")

    def is_enabled(self) -> bool:
        return self._enabled and self._root is not None

    def root(self) -> Optional[Path]:
        return self._root

    def new_decision(self, instance: int, phase: str, round_idx: int = 0) -> "Decision":
        if not self.is_enabled():
            return _NullDecision()
        ts = time.strftime("%H%M%S") + f"_{int((time.time() % 1) * 1000):03d}"
        d_id = f"{ts}_inst{instance}_{phase}_R{round_idx}"
        d_path = self._root / d_id
        try:
            d_path.mkdir(parents=True, exist_ok=True)
        except Exception:
            return _NullDecision()
        return Decision(d_id, d_path, instance, phase, round_idx, self)

    def record_summary(self, summary: dict):
        """完结一次决策，加进索引"""
        with self._lock:
            self._index.append(summary)
            if len(self._index) > self._max_index:
                self._index = self._index[-self._max_index:]

    def list_recent(self, limit: int = 50, instance: Optional[int] = None) -> list[dict]:
        with self._lock:
            items = self._index[::-1]  # 倒序，最新在前
            if instance is not None:
                items = [x for x in items if x.get("instance") == instance]
            return items[:limit]

    # ─── 历史会话浏览（扫磁盘）───

    def _logs_root(self) -> Optional[Path]:
        """logs/ 根目录, 包含所有 session 子目录.
        优先用已 init 的 root, 否则按 runner_service.py 同算法发现 logs 目录.
        这样即使 runner 未启动, 也能浏览历史 session.
        """
        if self._root is not None:
            return self._root.parent.parent  # decisions → session → logs
        try:
            import sys
            candidates: list[Path] = []
            here = Path(__file__).resolve()
            # backend/automation/decision_log.py → backend → project_root
            candidates.append(here.parent.parent.parent / "logs")
            # PyInstaller bundle: _internal 目录下
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                candidates.append(Path(meipass) / "logs")
            if getattr(sys, "frozen", False):
                candidates.append(Path(sys.executable).parent / "logs")
                candidates.append(Path(sys.executable).parent / "_internal" / "logs")
            for c in candidates:
                try:
                    if c.is_dir():
                        return c
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def list_sessions(self) -> list[dict]:
        """列出所有有决策记录的 session, 按时间倒序"""
        try:
            root = self._logs_root()
            if root is None or not root.is_dir():
                return []
            current_parent = None
            try:
                if self._root is not None:
                    current_parent = self._root.parent.resolve()
            except Exception:
                current_parent = None
            out = []
            try:
                entries = list(root.iterdir())
            except Exception:
                return []
            # 按 mtime 倒序
            try:
                entries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            except Exception:
                pass
            for sess in entries:
                try:
                    if not sess.is_dir():
                        continue
                    d_dir = sess / "decisions"
                    if not d_dir.is_dir():
                        continue
                    try:
                        cnt = sum(1 for _ in d_dir.iterdir() if _.is_dir())
                    except Exception:
                        cnt = 0
                    if cnt == 0:
                        continue
                    is_cur = False
                    try:
                        is_cur = (current_parent is not None
                                  and sess.resolve() == current_parent)
                    except Exception:
                        pass
                    try:
                        mtime = sess.stat().st_mtime
                    except Exception:
                        mtime = 0
                    out.append({
                        "session": sess.name,
                        "decision_count": cnt,
                        "is_current": is_cur,
                        "mtime": mtime,
                    })
                except Exception:
                    continue
            return out
        except Exception as _e:
            logger.warning(f"[decision] list_sessions 异常: {_e}")
            return []

    def list_session_decisions(self, session_name: str, limit: int = 200,
                                offset: int = 0,
                                instance: Optional[int] = None) -> list[dict]:
        """扫指定 session 的所有决策, 按时间倒序"""
        root = self._logs_root()
        if root is None:
            return []
        sess_dir = root / session_name / "decisions"
        if not sess_dir.is_dir():
            return []
        # 当前 session 直接读内存索引
        if self._root is not None and sess_dir == self._root:
            return self.list_recent(limit=limit + offset, instance=instance)[offset:]
        # 历史 session 扫磁盘
        items = []
        try:
            dirs = sorted(sess_dir.iterdir(), reverse=True, key=lambda p: p.stat().st_mtime)
        except Exception:
            dirs = []
        for d in dirs:
            if not d.is_dir():
                continue
            json_p = d / "decision.json"
            if not json_p.is_file():
                continue
            try:
                data = json.loads(json_p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if instance is not None and data.get("instance") != instance:
                continue
            items.append({
                "id": data.get("id", d.name),
                "instance": data.get("instance"),
                "phase": data.get("phase"),
                "round": data.get("round"),
                "created": data.get("created"),
                "outcome": data.get("outcome"),
                "tap_method": (data.get("tap") or {}).get("method", "") if data.get("tap") else "",
                "tap_target": (data.get("tap") or {}).get("target_class", "") if data.get("tap") else "",
                "verify_success": (data.get("verify") or {}).get("success") if data.get("verify") else None,
                "tier_count": len(data.get("tiers") or []),
                "session": session_name,
            })
            if len(items) >= offset + limit:
                break
        return items[offset:offset + limit]

    def get_session_decision_data(self, session_name: str, decision_id: str) -> Optional[dict]:
        """读历史 session 的某条决策详情"""
        root = self._logs_root()
        if root is None:
            return None
        p = root / session_name / "decisions" / decision_id / "decision.json"
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def get_session_image_path(self, session_name: str, decision_id: str,
                               image_name: str) -> Optional[Path]:
        """读历史 session 决策图片的磁盘路径"""
        root = self._logs_root()
        if root is None:
            return None
        # 简单清洗（防止路径穿越）
        if "/" in image_name or "\\" in image_name or ".." in image_name:
            return None
        p = root / session_name / "decisions" / decision_id / image_name
        return p if p.is_file() else None


_recorder = _Recorder()


def get_recorder() -> _Recorder:
    return _recorder


# ─────────────── Decision 上下文 ───────────────


class _NullDecision:
    """禁用时返回，所有方法 no-op"""
    def __getattr__(self, name): return self._noop
    def _noop(self, *args, **kwargs): return self
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Decision:
    """单次决策的上下文"""

    def __init__(self, d_id: str, path: Path, instance: int, phase: str,
                 round_idx: int, recorder: _Recorder):
        self.id = d_id
        self.path = path
        self.instance = instance
        self.phase = phase
        self.round = round_idx
        self.created = time.time()
        self.recorder = recorder

        self.input_image = ""
        self.input_phash = ""
        self.input_w = 0
        self.input_h = 0

        self.tiers: list[TierRecord] = []
        self.tap: Optional[TapRecord] = None
        self.verify: Optional[VerifyRecord] = None
        self.outcome: str = ""          # "tap_succeeded" / "tap_failed" / "skipped" / "lobby"
        self.note: str = ""

    # ────── 输入 ──────

    def set_input(self, screenshot: np.ndarray, phash: str = "", q: int = 70):
        if screenshot is None:
            return self
        self.input_h, self.input_w = screenshot.shape[:2]
        self.input_phash = phash
        try:
            cv2.imwrite(str(self.path / "input.jpg"), screenshot,
                        [cv2.IMWRITE_JPEG_QUALITY, q])
            self.input_image = "input.jpg"
        except Exception as e:
            logger.warning(f"[decision] save input fail: {e}")
        return self

    # ────── Tier 结果 ──────

    def add_tier(self, tier: TierRecord):
        self.tiers.append(tier)
        return self

    # ────── 模板试探 ──────

    def add_template_attempt(self, tier: TierRecord, template_name: str,
                             template_dir: Path, score: float, threshold: float,
                             hit: bool, bbox: Optional[list] = None, scale: float = 1.0):
        """记录一次模板尝试。把模板图复制到决策目录"""
        tmpl_rel = ""
        try:
            src = template_dir / f"{template_name}.png"
            if src.exists():
                dst = self.path / f"tmpl_{template_name}.png"
                if not dst.exists():
                    shutil.copyfile(src, dst)
                tmpl_rel = dst.name
        except Exception:
            pass
        tier.templates.append(TemplateMatch(
            name=template_name,
            template_image=tmpl_rel,
            score=round(float(score), 3),
            threshold=round(float(threshold), 3),
            hit=hit,
            bbox=list(bbox) if bbox else None,
            scale=round(float(scale), 3),
        ))
        return self

    # ────── YOLO 标注图 ──────

    def save_yolo_annot(self, tier: TierRecord, screenshot: np.ndarray,
                        detections: list[YoloDetection], q: int = 70):
        """画 YOLO bbox 到截图副本"""
        if screenshot is None:
            return self
        annot = screenshot.copy()
        for det in detections:
            x1, y1, x2, y2 = [int(v) for v in det.bbox]
            color = (0, 0, 255) if det.cls == "close_x" else (0, 255, 255)
            cv2.rectangle(annot, (x1, y1), (x2, y2), color, 2)
            label = f"{det.cls} {det.conf:.2f}"
            cv2.putText(annot, label, (x1, max(20, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        try:
            cv2.imwrite(str(self.path / "yolo_annot.jpg"), annot,
                        [cv2.IMWRITE_JPEG_QUALITY, q])
            tier.yolo_annot_image = "yolo_annot.jpg"
            tier.yolo_detections = detections
        except Exception:
            pass
        return self

    # ────── OCR ROI 标注 ──────

    def save_ocr_roi(self, tier: TierRecord, screenshot: np.ndarray,
                     roi: Optional[list] = None, hits: Optional[list] = None, q: int = 70):
        """
        OCR 是局部的话，把 ROI 区域框出来 + 内部识别文字标在画面上
        roi: [x1,y1,x2,y2] 全屏坐标
        hits: list[OcrHit]
        """
        if screenshot is None:
            return self
        annot = screenshot.copy()
        # 先画所有 bbox, 再一次性批量画文本 (中文一次 BGR↔PIL 转换)
        text_items: list = []
        if roi:
            x1, y1, x2, y2 = [int(v) for v in roi]
            cv2.rectangle(annot, (x1, y1), (x2, y2), (255, 200, 0), 3)
            text_items.append(("OCR ROI", (x1, max(20, y1 - 6)),
                               (255, 200, 0), 18))
        if hits:
            # 限 Top 10, 避免画一坨遮原图; 完整列表在 JSON 里
            for h in hits[:10]:
                if not h.bbox or len(h.bbox) != 4:
                    continue
                hx1, hy1, hx2, hy2 = [int(v) for v in h.bbox]
                cv2.rectangle(annot, (hx1, hy1), (hx2, hy2), (0, 255, 0), 1)
                text_items.append((h.text[:14], (hx1, max(15, hy1 - 4)),
                                   (0, 255, 0), 14))
        _put_texts_cn_batch(annot, text_items)
        try:
            cv2.imwrite(str(self.path / "ocr_annot.jpg"), annot,
                        [cv2.IMWRITE_JPEG_QUALITY, q])
            tier.ocr_roi_image = "ocr_annot.jpg"
            tier.ocr_roi = list(roi) if roi else None
            tier.ocr_hits = hits or []
        except Exception:
            pass
        return self

    # ────── 点击 ──────

    def set_tap(self, x: int, y: int, method: str, target_class: str = "",
                target_text: str = "", target_conf: float = 0.0,
                screenshot: Optional[np.ndarray] = None):
        self.tap = TapRecord(int(x), int(y), method, target_class, target_text,
                              float(target_conf))
        if screenshot is not None:
            # 1) 独立 tap 图: 先叠所有 tier 的命中 bbox (模板/OCR), 再画 tap 圈
            annot = screenshot.copy()
            text_items: list = []
            for t in self.tiers:
                # 模板命中: 绿色框
                for tm in (t.templates or []):
                    if tm.hit and tm.bbox and len(tm.bbox) == 4:
                        x1, y1, x2, y2 = [int(v) for v in tm.bbox]
                        cv2.rectangle(annot, (x1, y1), (x2, y2), (0, 255, 0), 2)
                        text_items.append(
                            (f"TMPL {tm.name} {tm.score:.2f}",
                             (x1, max(20, y1 - 6)), (0, 255, 0), 14))
                # OCR 命中: 青色框 + 文字 (Top 10 防止挤一坨)
                for h in (t.ocr_hits or [])[:10]:
                    if h.bbox and len(h.bbox) == 4:
                        hx1, hy1, hx2, hy2 = [int(v) for v in h.bbox]
                        cv2.rectangle(annot, (hx1, hy1), (hx2, hy2), (255, 200, 0), 1)
                        if h.text:
                            text_items.append(
                                (h.text[:14], (hx1, max(15, hy1 - 4)),
                                 (255, 200, 0), 12))
            # 红色 tap 圈在最上面
            cv2.circle(annot, (int(x), int(y)), 36, (0, 0, 255), 3)
            cv2.circle(annot, (int(x), int(y)), 6, (0, 0, 255), -1)
            label = f"TAP {method} ({int(x)},{int(y)})"
            if target_text:
                label += f" '{target_text[:10]}'"
            text_items.append(
                (label, (int(x) + 40, int(y) - 12), (0, 0, 255), 18))
            # 一次性批量画所有文本
            _put_texts_cn_batch(annot, text_items)
            try:
                cv2.imwrite(str(self.path / "tap_annot.jpg"), annot,
                            [cv2.IMWRITE_JPEG_QUALITY, 70])
                self.tap.annot_image = "tap_annot.jpg"
            except Exception:
                pass

            # 2) 把 tap 圆点叠加到 yolo_annot 图上 (用户要求: 一图同时看 bbox + tap)
            yolo_annot_path = self.path / "yolo_annot.jpg"
            if yolo_annot_path.exists():
                try:
                    img = cv2.imread(str(yolo_annot_path))
                    if img is not None:
                        cv2.circle(img, (int(x), int(y)), 36, (0, 255, 255), 3)  # 黄色外圈
                        cv2.circle(img, (int(x), int(y)), 6, (0, 255, 255), -1)  # 黄色实心
                        cv2.putText(img, f"TAP ({int(x)},{int(y)})",
                                    (int(x) + 40, int(y) + 30),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                        cv2.imwrite(str(yolo_annot_path), img,
                                    [cv2.IMWRITE_JPEG_QUALITY, 70])
                except Exception:
                    pass
        return self

    # ────── 验证 ──────

    def set_verify(self, before: str, after: str, distance: int):
        self.verify = VerifyRecord(before, after, distance,
                                   success=(distance > 5))
        return self

    # ────── 收尾 ──────

    def finalize(self, outcome: str = "", note: str = ""):
        """写 decision.json + 进索引"""
        self.outcome = outcome
        if note:
            self.note = note
        data = {
            "id": self.id,
            "instance": self.instance,
            "phase": self.phase,
            "round": self.round,
            "created": self.created,
            "input_image": self.input_image,
            "input_phash": self.input_phash,
            "input_w": self.input_w,
            "input_h": self.input_h,
            "tiers": [_serialize_tier(t) for t in self.tiers],
            "tap": asdict(self.tap) if self.tap else None,
            "verify": asdict(self.verify) if self.verify else None,
            "outcome": self.outcome,
            "note": self.note,
        }
        try:
            with open(self.path / "decision.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[decision] save json fail: {e}")
        # 加索引
        summary = {
            "id": self.id,
            "instance": self.instance,
            "phase": self.phase,
            "round": self.round,
            "created": self.created,
            "outcome": self.outcome,
            "tap_method": self.tap.method if self.tap else "",
            "tap_target": self.tap.target_class if self.tap else "",
            "verify_success": self.verify.success if self.verify else None,
            "tier_count": len(self.tiers),
        }
        self.recorder.record_summary(summary)
        # 通知 listener (LiveBroadcaster 推 WS)
        try:
            self.recorder._notify_listeners({**summary, "tap_xy": [self.tap.x, self.tap.y] if self.tap else None})
        except Exception:
            pass
        return self


def _serialize_tier(t: TierRecord) -> dict:
    """把 dataclass list 里嵌套的 dataclass 也序列化"""
    return {
        "tier": t.tier,
        "name": t.name,
        "duration_ms": round(t.duration_ms, 2),
        "early_exit": t.early_exit,
        "note": t.note,
        "templates": [asdict(x) for x in t.templates] if t.templates else [],
        "yolo_detections": [asdict(x) for x in t.yolo_detections] if t.yolo_detections else [],
        "yolo_annot_image": t.yolo_annot_image,
        "ocr_hits": [asdict(x) for x in t.ocr_hits] if t.ocr_hits else [],
        "ocr_roi": t.ocr_roi,
        "ocr_roi_image": t.ocr_roi_image,
        "memory_phash_query": t.memory_phash_query,
        "memory_hit": t.memory_hit,
    }
