#!/usr/bin/env python3
"""YOLO 截图自动核验 — Windows 端跑

目标：把 raw_screenshots/ 里的图按"tag 是否真匹配画面内容"自动分桶，
让 Mac 端用户只需复审 verified/ + suspicious/，跳过 skip/。

分桶逻辑：
  verified/   — phase 名 + OCR 关键字交叉验证通过
  suspicious/ — phase 名匹配但关键字弱命中 / 或 OCR 内容跨 phase 模糊
  skip/       — 黑屏 / 加载条 / 0 文字（YOLO 训练价值低）

用法：
    python tools/yolo_verify.py
    python tools/yolo_verify.py --raw fixtures/yolo/raw_screenshots --out fixtures/yolo/verified_pool
    python tools/yolo_verify.py --copy   # 复制而非移动（保留原 raw 不动）
    python tools/yolo_verify.py --limit 100  # 只处理最近 100 张
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import cv2
import numpy as np


# ════════════════════════════════════════
# Phase → 关键字白名单（OCR 命中即认为该 phase tag 准确）
# 关键字尽量覆盖游戏 UI 实际用词
# ════════════════════════════════════════

PHASE_KEYWORDS: Dict[str, List[str]] = {
    "accelerator": [
        "加速", "VPN", "FightMaster", "网络", "节点", "已连接", "已断开",
        "Connected", "Disconnected", "代理", "选择服务器", "ping",
    ],
    "launch_game": [
        "和平精英", "PUBG", "GAME FOR PEACE", "启动中", "正在启动",
        "腾讯游戏", "腾讯", "公告", "更新", "下载",
    ],
    "wait_login": [
        "微信登录", "QQ登录", "登录", "微信", "进入游戏", "用户协议",
        "GAME FOR PEACE",
    ],
    "dismiss_popups": [
        # 弹窗常见元素：标题 / 关闭按钮 / 公告
        "公告", "关闭", "确定", "取消", "我知道了", "暂不参与",
        "活动", "通行证", "签到", "礼包", "战令",
    ],
    "team_create": [
        "组队", "找队友", "邀请", "二维码", "复制", "队伍号", "邀请好友",
        "创建队伍", "队伍设置",
    ],
    "team_join": [
        "加入队伍", "加入", "队友", "队伍",
    ],
    "map_setup": [
        "经典", "竞技", "娱乐", "训练", "模式", "地图", "开始", "匹配",
        "海岛", "雪地", "沙漠", "丛林", "选择模式",
    ],
    "init": [],
    "unknown": [],
}

# 跨 phase 排他规则（命中这些 = 强烈不属于该 phase）
PHASE_NEGATIVES: Dict[str, List[str]] = {
    "dismiss_popups": ["微信登录", "QQ登录"],   # 弹窗页不会有登录按钮
    "team_create": ["微信登录", "QQ登录", "公告"],
    "map_setup": ["微信登录", "QQ登录"],
    "wait_login": ["组队", "找队友", "二维码"],
}


# ════════════════════════════════════════
# OCR 单例（worker 级别 reuse）
# ════════════════════════════════════════

_ocr = None


def _get_ocr():
    global _ocr
    if _ocr is not None:
        return _ocr
    try:
        from rapidocr import RapidOCR
    except ImportError:
        print("[!] rapidocr 未安装。pip install rapidocr-onnxruntime", file=sys.stderr)
        sys.exit(2)
    # 优先 DML（Windows GPU 加速），失败回退 CPU
    try:
        _ocr = RapidOCR(params={"EngineConfig.onnxruntime.use_dml": True})
        # warmup
        _ocr(np.zeros((100, 100, 3), dtype=np.uint8))
        print("[ocr] RapidOCR (DirectML) 就绪")
    except Exception as e:
        print(f"[ocr] DML 失败 ({e})，回退 CPU")
        _ocr = RapidOCR()
        _ocr(np.zeros((100, 100, 3), dtype=np.uint8))
    return _ocr


def _ocr_text(img: np.ndarray) -> List[str]:
    """跑 OCR 返回所有文字 list（原顺序）"""
    ocr = _get_ocr()
    try:
        result = ocr(img)
    except Exception as e:
        print(f"[ocr] 调用失败: {e}", file=sys.stderr)
        return []
    if result is None or result.txts is None:
        return []
    return [str(t) for t in result.txts if t]


# ════════════════════════════════════════
# 分类核心
# ════════════════════════════════════════

@dataclass
class Verdict:
    bucket: str          # verified / suspicious / skip
    reason: str
    phase_tag: str
    sub_tag: str
    ocr_count: int
    matched_keywords: List[str] = field(default_factory=list)
    negative_keywords: List[str] = field(default_factory=list)


def parse_filename(name: str) -> Tuple[str, str]:
    """从 `<phase>[__<sub>]_inst<N>_<ts>.png` 提取 (phase, sub_tag)

    例：dismiss_popups__detect_lobby_inst1_1234567890.png → (dismiss_popups, detect_lobby)
    例：team_create_inst0_1234567890.png → (team_create, "")
    """
    stem = Path(name).stem
    # 去掉尾部 _inst{N}_{ts}
    parts = stem.rsplit("_inst", 1)
    if len(parts) != 2:
        return ("unknown", "")
    tag_part = parts[0]
    if "__" in tag_part:
        phase, sub = tag_part.split("__", 1)
        return (phase, sub)
    return (tag_part, "")


def is_mostly_dark(img: np.ndarray, threshold: int = 30, ratio: float = 0.92) -> bool:
    """判断是否大部分为黑（loading/transition 帧）"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    dark_pixels = np.sum(gray < threshold)
    return dark_pixels / gray.size > ratio


def classify_one(img: np.ndarray, filename: str) -> Verdict:
    phase, sub = parse_filename(filename)

    # 黑屏直接 skip
    if is_mostly_dark(img):
        return Verdict("skip", "mostly_dark_frame", phase, sub, 0)

    texts = _ocr_text(img)
    text_blob = "".join(texts).lower()

    # 0 文字命中 → 大概率游戏内 / 加载图（YOLO 价值低）
    if len(texts) == 0:
        return Verdict("skip", "no_text_detected", phase, sub, 0)

    # 收集关键字命中
    positive = PHASE_KEYWORDS.get(phase, [])
    negative = PHASE_NEGATIVES.get(phase, [])
    pos_hits = [k for k in positive if k.lower() in text_blob]
    neg_hits = [k for k in negative if k.lower() in text_blob]

    # 强反例：负向关键字命中 → suspicious（tag 大概率错）
    if neg_hits:
        return Verdict("suspicious", f"negative_keyword_hit:{neg_hits}",
                       phase, sub, len(texts), pos_hits, neg_hits)

    # phase 没有关键字白名单（init/unknown）→ 全归 suspicious 让用户裁定
    if not positive:
        return Verdict("suspicious", "phase_has_no_keywords",
                       phase, sub, len(texts), pos_hits, neg_hits)

    # 关键字未命中 → suspicious
    if not pos_hits:
        return Verdict("suspicious", "no_keyword_match",
                       phase, sub, len(texts), pos_hits, neg_hits)

    # 命中 → verified
    return Verdict("verified", f"matched:{pos_hits}",
                   phase, sub, len(texts), pos_hits, neg_hits)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="fixtures/yolo/raw_screenshots",
                    help="raw 截图目录（默认 fixtures/yolo/raw_screenshots）")
    ap.add_argument("--out", default="fixtures/yolo/verified_pool",
                    help="输出根目录（默认 fixtures/yolo/verified_pool）")
    ap.add_argument("--copy", action="store_true",
                    help="复制（保留 raw）。默认是移动（清理 raw）")
    ap.add_argument("--limit", type=int, default=0,
                    help="只处理最近 N 张（默认 0=全部）")
    ap.add_argument("--report", default=None,
                    help="把每张图的 verdict JSON 写到此文件（可选）")
    args = ap.parse_args()

    raw_dir = Path(args.raw)
    out_dir = Path(args.out)
    if not raw_dir.is_dir():
        print(f"[!] raw 目录不存在: {raw_dir}", file=sys.stderr)
        sys.exit(2)

    # 输出 3 个桶
    buckets = {b: out_dir / b for b in ["verified", "suspicious", "skip"]}
    for b in buckets.values():
        b.mkdir(parents=True, exist_ok=True)

    # 收集 raw（最近优先）
    files = sorted(raw_dir.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    files += sorted(raw_dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
    if args.limit > 0:
        files = files[: args.limit]
    if not files:
        print(f"[i] {raw_dir} 没有截图")
        return

    print(f"[i] 待处理 {len(files)} 张")
    counts = {"verified": 0, "suspicious": 0, "skip": 0}
    report_rows = []
    t0 = time.perf_counter()
    for i, f in enumerate(files, 1):
        try:
            img = cv2.imread(str(f))
            if img is None:
                continue
            v = classify_one(img, f.name)
        except Exception as e:
            print(f"[!] {f.name} 处理失败: {e}", file=sys.stderr)
            continue

        target = buckets[v.bucket] / f.name
        try:
            if args.copy:
                shutil.copy2(f, target)
            else:
                shutil.move(str(f), str(target))
        except Exception as e:
            print(f"[!] {f.name} 移动失败: {e}", file=sys.stderr)
            continue

        counts[v.bucket] += 1
        report_rows.append({"file": f.name, **asdict(v)})

        if i % 20 == 0 or i == len(files):
            print(f"[{i}/{len(files)}] verified={counts['verified']} "
                  f"suspicious={counts['suspicious']} skip={counts['skip']}")

    dt = time.perf_counter() - t0
    print(f"\n[done] {sum(counts.values())} 张，耗时 {dt:.1f}s "
          f"({sum(counts.values()) / max(dt, 0.1):.1f} 张/秒)")
    print(f"  verified  → {buckets['verified']} ({counts['verified']})")
    print(f"  suspicious→ {buckets['suspicious']} ({counts['suspicious']})")
    print(f"  skip      → {buckets['skip']} ({counts['skip']})")

    if args.report:
        Path(args.report).write_text(
            json.dumps(report_rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  report    → {args.report}")


if __name__ == "__main__":
    main()
