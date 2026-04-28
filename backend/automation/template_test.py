"""
template_test — 模版 dryrun 匹配 (中控台模版库测试用).

不实际点击, 不污染主 runner 状态.
直接调 ScreenMatcher.match_one + 把结果画到帧上返回.
"""
from __future__ import annotations

import base64
import io
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class TestResult:
    template: str
    threshold: float
    hit: bool
    score: float
    cx: int = 0
    cy: int = 0
    w: int = 0
    h: int = 0
    bbox: Optional[list] = None
    scale: float = 1.0
    duration_ms: float = 0.0
    annotated_b64: str = ""        # data:image/jpeg;base64,...
    note: str = ""


def _decode_image_bytes(data: bytes) -> Optional[np.ndarray]:
    if not data:
        return None
    arr = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _encode_jpeg_b64(img: np.ndarray, q: int = 70) -> str:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    if not ok:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


def _draw_hit(img: np.ndarray, hit, color=(0, 200, 0)) -> np.ndarray:
    annot = img.copy()
    if hit is None:
        cv2.putText(annot, "MISS", (24, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 220), 4)
        return annot
    x1 = max(0, int(hit.cx - hit.w / 2))
    y1 = max(0, int(hit.cy - hit.h / 2))
    x2 = int(x1 + hit.w)
    y2 = int(y1 + hit.h)
    cv2.rectangle(annot, (x1, y1), (x2, y2), color, 3)
    cv2.circle(annot, (int(hit.cx), int(hit.cy)), 8, (0, 0, 220), -1)
    label = f"{hit.name}  {hit.confidence:.3f}"
    ty = max(28, y1 - 8)
    cv2.putText(annot, label, (x1, ty),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    return annot


def run_test(
    *,
    template_name: str,
    matcher,
    screenshot: np.ndarray,
    threshold: Optional[float] = None,
    use_edge: bool = False,
    annotate: bool = True,
    preprocessing: Optional[list] = None,
) -> TestResult:
    """跑一次 dryrun: matcher.match_one + 画框 + 返回 TestResult."""
    t0 = time.perf_counter()
    if matcher is None:
        return TestResult(template=template_name, threshold=threshold or 0,
                          hit=False, score=0.0, note="matcher 未初始化")
    if screenshot is None:
        return TestResult(template=template_name, threshold=threshold or 0,
                          hit=False, score=0.0, note="无截图")
    try:
        hit = matcher.match_one(
            screenshot, template_name, threshold=threshold,
            use_edge=use_edge, preprocessing=preprocessing,
        )
    except Exception as e:
        logger.warning(f"[template_test] match_one err: {e}")
        return TestResult(template=template_name, threshold=threshold or 0,
                          hit=False, score=0.0, note=f"匹配异常: {e}")

    dur_ms = round((time.perf_counter() - t0) * 1000, 2)
    annotated = _encode_jpeg_b64(_draw_hit(screenshot, hit)) if annotate else ""

    if hit is None:
        return TestResult(
            template=template_name,
            threshold=float(threshold or 0),
            hit=False, score=0.0,
            duration_ms=dur_ms,
            annotated_b64=annotated,
            note="未命中 (得分低于阈值或模板未加载)",
        )

    return TestResult(
        template=template_name,
        threshold=float(threshold or 0),
        hit=True,
        score=round(float(hit.confidence), 4),
        cx=int(hit.cx), cy=int(hit.cy),
        w=int(hit.w), h=int(hit.h),
        bbox=[
            int(hit.cx - hit.w / 2), int(hit.cy - hit.h / 2),
            int(hit.cx + hit.w / 2), int(hit.cy + hit.h / 2),
        ],
        duration_ms=dur_ms,
        annotated_b64=annotated,
        note="命中",
    )


def save_template_from_crop(
    *,
    source_image: np.ndarray,
    bbox: tuple[int, int, int, int],
    name: str,
    template_dir: Path,
) -> Optional[Path]:
    """从全屏截图 + bbox 裁出模版图保存到 fixtures/templates/{name}.png.

    bbox: (x1, y1, x2, y2) 全屏坐标. 自动 clip 到画面内.
    返回保存路径; 若已存在或失败返回 None.
    """
    if source_image is None or source_image.size == 0:
        return None
    h, w = source_image.shape[:2]
    x1, y1, x2, y2 = bbox
    x1 = max(0, min(int(x1), w - 1))
    y1 = max(0, min(int(y1), h - 1))
    x2 = max(x1 + 1, min(int(x2), w))
    y2 = max(y1 + 1, min(int(y2), h))
    crop = source_image[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    template_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c if (c.isalnum() or c in "_-") else "_" for c in name)[:64]
    if not safe_name:
        return None
    dst = template_dir / f"{safe_name}.png"
    if dst.exists():
        return None  # 调用方决定是否覆盖
    ok = cv2.imwrite(str(dst), crop)
    return dst if ok else None
