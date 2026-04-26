"""
CTA (Call-to-Action) 主按钮通用检测 — 不依赖 YOLO 训练数据.

设计观察 (来自实跑游戏):
  - 游戏 UI 设计法则: CTA 比背景显眼 (高饱和色 + 大圆角矩形 + 内含中文动词)
  - 装饰元素 (X / 图标 / 数字) 通常面积小或长宽比不对
  - 大厅左/右侧栏 + 顶部 + 底部 nav 都要排除

用途:
  当 YOLO 漏检 + 画面已离开大厅 (lobby_btn 模板不命中) 时,
  必须找到 CTA 才能回大厅 (砍价 / 立即领取 / 立即抽奖 等强引导活动).

  yolo_dismisser 主循环在 mode="outside_lobby" + 没找到 X 时调用.

用法:
    from .cta_detector import find_main_cta, NAV_BLACKLIST
    cta = find_main_cta(frame, ocr_fn=lambda roi: ocr.recognize(roi),
                        nav_blacklist=NAV_BLACKLIST)
    if cta:
        await adb.tap(cta.cx, cta.cy)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# CTA 不能含的危险词 (跳出大厅 / 强行参与跨场景活动 / 登录页按钮)
NAV_BLACKLIST: Tuple[str, ...] = (
    "前往", "参加", "进入", "查看活动", "去看看", "立即前往",
    "前往观赛", "去活动", "我要参加", "查看", "开启", "去看",
    # v2-5 实跑发现: 登录页 "微信登录/QQ登录" 被误识为 CTA, 加进黑名单
    "微信登录", "QQ登录", "扫码登录", "微信", "QQ登",
)


# CTA 必须含至少 1 个动词关键字才算 "主动按钮"
# 不在白名单的色块即使大且艳, 也不点 (保守策略, 避免误点装饰按钮)
# 用户教学时如果遇到新动词没覆盖, 可以加进来 (热加载留 v2-6)
CTA_VERB_WHITELIST: Tuple[str, ...] = (
    "立即",  # 立即领取 / 立即砍价 / 立即抽奖 / 立即参与
    "我要",  # 我要参与
    "砍价",  # 立即砍价 / 帮砍 / 我要砍价
    "领取",  # 立即领取 / 一键领取
    "分享",  # 立即分享
    "邀请",  # 邀请好友
    "收下",  # 收下奖励
    "确定", "确认", "同意", "好的", "知道了",
    "继续",  # 继续游戏
    "进入战场", "开始", "开战",
    "助力",  # 帮 X 助力
    "充值",  # 立即充值
    "加入",  # 加入队伍 / 立即加入
    "签到",
)


@dataclass
class CtaCandidate:
    """一个候选 CTA 按钮."""
    cx: int
    cy: int
    w: int
    h: int
    area: int
    saturation: float       # 平均饱和度 (0-255)
    rect_fill: float        # 轮廓占外接矩形比例 (>0.65 视为矩形)
    text: str = ""          # OCR 文字
    score: float = 0.0      # 综合评分 (饱和度 × 面积)


def _color_block_mask(
    frame: np.ndarray,
    sat_min: int = 80,
    val_min: int = 100,
) -> np.ndarray:
    """HSV 高饱和 + 高亮度 → 二值 mask.

    形态学 kernel 改小 (3,3) 避免相邻按钮粘连
    (实跑发现: 7x5 把"微信登录"+"QQ登录"两个相邻按钮粘成一个色块,
    boundingRect 横跨两按钮, tap 落在中间空隙).
    """
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]
    mask = ((s > sat_min) & (v > val_min)).astype(np.uint8) * 255
    # 形态学闭运算: 仅修复小毛刺, 不要把相邻按钮粘成一个
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)


def find_cta_candidates(
    frame: np.ndarray,
    edge_margin_x: int = 100,    # 排除左/右侧栏
    edge_margin_top: int = 50,   # 排除顶部状态栏
    edge_margin_bottom: int = 50, # 排除底部 nav
    min_area: int = 4000,        # 太小的不是按钮 (~70x60)
    max_area: int = 60000,       # 太大的可能是 banner (~400x150)
    min_aspect: float = 1.5,     # 横向矩形 (宽 > 高)
    max_aspect: float = 6.0,
    min_rect_fill: float = 0.60,
) -> List[CtaCandidate]:
    """找画面所有 CTA 候选, 按 (饱和度 × 面积) 排序. 不做 OCR (留给上层)."""
    if frame is None or frame.size == 0:
        return []
    h, w = frame.shape[:2]
    mask = _color_block_mask(frame)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out: List[CtaCandidate] = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area or area > max_area:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        if bh == 0:
            continue
        aspect = bw / bh
        if aspect < min_aspect or aspect > max_aspect:
            continue
        # 排除画面边缘 (导航栏 / 状态栏 / 侧栏)
        if x < edge_margin_x or x + bw > w - edge_margin_x:
            continue
        if y < edge_margin_top or y + bh > h - edge_margin_bottom:
            continue
        rect_fill = float(area) / float(bw * bh)
        if rect_fill < min_rect_fill:
            continue
        # 计算 ROI 平均饱和度 (验证不是灰色 banner)
        roi = frame[y:y + bh, x:x + bw]
        try:
            hsv_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            sat = float(hsv_roi[:, :, 1].mean())
        except Exception:
            sat = 0.0
        out.append(CtaCandidate(
            cx=x + bw // 2,
            cy=y + bh // 2,
            w=bw,
            h=bh,
            area=int(area),
            saturation=sat,
            rect_fill=rect_fill,
        ))
    # 排序: 饱和度 × 面积 (又大又艳优先)
    out.sort(key=lambda c: -(c.saturation * c.area))
    return out


def _extract_text(ocr_results) -> str:
    """ocr_fn 可能返回 list[OcrHit] 或 list[str] 或 [(text,...)] 元组"""
    parts = []
    for r in ocr_results or []:
        if isinstance(r, str):
            parts.append(r)
        elif hasattr(r, "text"):
            parts.append(str(r.text))
        elif isinstance(r, (list, tuple)) and r:
            parts.append(str(r[0]))
    return " ".join(p for p in parts if p)


def find_main_cta(
    frame: np.ndarray,
    ocr_fn: Optional[Callable] = None,
    nav_blacklist: Tuple[str, ...] = NAV_BLACKLIST,
    verb_whitelist: Tuple[str, ...] = CTA_VERB_WHITELIST,
    require_verb: bool = True,
    top_k: int = 5,
) -> Optional[CtaCandidate]:
    """找画面里"最显眼的可点 CTA 按钮".

    保守策略 (默认 require_verb=True):
      ① 候选必须 OCR 出文字 (无文字 = 装饰色块, 不点)
      ② 文字必须含 verb_whitelist 中的至少 1 个动词 (主动按钮特征)
      ③ 文字不含 nav_blacklist (跳出大厅 / 登录页等危险词)

    实跑教训:
      - 登录页 "微信登录"+"QQ登录" 两个按钮 都很显眼, 但都不是 CTA
      - 因此**白名单优先**, 不在白名单宁可不动也不要乱点

    ocr_fn(roi: ndarray) → list (含 .text 属性或字符串).
    require_verb=False + ocr_fn=None 时, 退回旧"取最显眼" 行为 (高风险).
    """
    candidates = find_cta_candidates(frame)
    if not candidates:
        return None

    # 没 OCR + 不强制动词 → 退回旧行为 (调用方知道在做啥)
    if ocr_fn is None:
        if require_verb:
            return None  # 没法验证文字, 保守不动
        cand = candidates[0]
        cand.score = cand.saturation * cand.area / 10000
        return cand

    # 有 OCR → 逐个验证
    for cand in candidates[:top_k]:
        try:
            x1 = max(0, cand.cx - cand.w // 2)
            y1 = max(0, cand.cy - cand.h // 2)
            x2 = cand.cx + cand.w // 2
            y2 = cand.cy + cand.h // 2
            roi = frame[y1:y2, x1:x2]
            if roi is None or roi.size == 0:
                continue
            text = _extract_text(ocr_fn(roi))
        except Exception as e:
            logger.debug(f"[cta] ocr err: {e}")
            continue
        cand.text = text
        if not text.strip():
            logger.debug(f"[cta] skip 无 OCR 文字 @ ({cand.cx},{cand.cy})")
            continue
        # 排除危险词
        if any(nw and nw in text for nw in nav_blacklist):
            logger.debug(f"[cta] skip nav '{text}' @ ({cand.cx},{cand.cy})")
            continue
        # 必须含动词关键字 (保守: 不在白名单不点)
        if require_verb:
            if not any(v and v in text for v in verb_whitelist):
                logger.debug(
                    f"[cta] skip 无动词 '{text}' @ ({cand.cx},{cand.cy}) "
                    f"(不在 verb_whitelist)"
                )
                continue
        cand.score = cand.saturation * cand.area / 10000
        return cand
    return None
