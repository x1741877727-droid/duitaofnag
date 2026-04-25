#!/usr/bin/env python3
"""YOLO 半自动标注 — 用现有 OCR + 模板生成伪标签

输入：fixtures/yolo/raw_screenshots/*.png  （你 / 脚本采集的游戏截图）
输出：fixtures/yolo/dataset/{train,val}/{images,labels}/

每张截图：
  1. 跑 OCR 找文字按钮（"组队", "确定", ...）→ 用 OCR 命中点位 + ROI 估计 bbox
  2. 跑模板匹配找图标按钮（X 关闭, 加速器图标）
  3. 输出 YOLO 格式 labels：每行 `class_id cx cy w h`（归一化 0-1）

注意：
  - 这是**伪标签**。准确率不会 100%，需要人工抽查
  - 抽查工具：fixtures/yolo/review.html（生成可视化 HTML 标注预览）
  - 漏标的类（YOLO miss）人工补，错标的删掉
  - 90% 自动 + 10% 人工 是这步的目标

用法：
    # 把游戏截图丢到 fixtures/yolo/raw_screenshots/
    # 跑标注（默认 80/20 切分 train/val）
    python tools/yolo_autolabel.py
    python tools/yolo_autolabel.py --raw fixtures/yolo/raw_screenshots --val-ratio 0.2
    python tools/yolo_autolabel.py --review        # 同时生成 HTML 预览
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import shutil
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np
import yaml

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)


@dataclass
class YoloBox:
    class_id: int
    cx: float  # 归一化中心 x
    cy: float  # 归一化中心 y
    w: float
    h: float
    score: float = 0.0  # 标注置信度（仅给人工 review 用，不写进 .txt）

    def to_yolo_line(self) -> str:
        return f"{self.class_id} {self.cx:.6f} {self.cy:.6f} {self.w:.6f} {self.h:.6f}"


def _load_class_config() -> Tuple[dict, list]:
    """读 config/yolo_classes.yaml，返回 (name → cls_def, [按 id 升序的 cls_def 列表])"""
    path = os.path.join(_PROJ_ROOT, "config", "yolo_classes.yaml")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    classes = data.get("classes", [])
    classes.sort(key=lambda c: c["id"])
    by_name = {c["name"]: c for c in classes}
    return by_name, classes


def _ocr_pseudo_boxes(frame: np.ndarray, classes: list, ocr_obj) -> List[YoloBox]:
    """跑全屏 OCR + 按 anchor_roi 局部 OCR，对每个类匹配关键词产出 bbox。

    bbox 估算：以 OCR 命中点 (cx, cy) 为中心，给一个固定尺寸（按经验 80×40 像素）。
    人工 review 时会修。
    """
    from backend.automation.ocr_dismisser import OcrDismisser
    from backend.automation import roi_config

    h, w = frame.shape[:2]
    boxes: List[YoloBox] = []

    # 全屏 OCR 一次
    full_hits = ocr_obj._ocr_full(frame) if hasattr(ocr_obj, "_ocr_full") else []
    # 默认估算尺寸（按钮粗略大小）
    default_w_ratio = 0.08
    default_h_ratio = 0.05

    def _try_match(hits, cls):
        """对一个 cls 在 hits 列表里找命中"""
        for kw in cls.get("replaces_ocr", []):
            for hit in hits:
                if OcrDismisser.fuzzy_match(hit.text, kw):
                    cx = hit.cx / w
                    cy = hit.cy / h
                    bw = default_w_ratio
                    bh = default_h_ratio
                    boxes.append(YoloBox(
                        class_id=cls["id"], cx=cx, cy=cy, w=bw, h=bh,
                        score=0.7  # OCR 模糊匹配，给中等置信度
                    ))
                    return True
        return False

    for cls in classes:
        if not cls.get("replaces_ocr"):
            continue  # btn_close_x 之类，OCR 抓不到，留给模板匹配
        # 1) 优先用 anchor_roi 局部 OCR（更准）
        anchor = cls.get("anchor_roi") or ""
        if anchor:
            try:
                roi_hits = ocr_obj._ocr_roi_named(frame, anchor)
                if _try_match(roi_hits, cls):
                    continue
            except KeyError:
                pass
        # 2) fallback 全屏 OCR
        _try_match(full_hits, cls)

    return boxes


def _template_pseudo_boxes(frame: np.ndarray, classes: list,
                           templates_dir: str) -> List[YoloBox]:
    """模板匹配补充 OCR 抓不到的（X 按钮、纯图标按钮）"""
    try:
        from backend.recognition.template_matcher import TemplateMatcher
    except Exception as e:
        print(f"⚠️  TemplateMatcher 导入失败，跳过模板伪标签：{e}", file=sys.stderr)
        return []

    matcher = TemplateMatcher(templates_dir=templates_dir)
    matcher.load_templates()
    h, w = frame.shape[:2]
    boxes: List[YoloBox] = []

    # 名字映射约定：YOLO class.name 对应 template_key（caller 在 templates/ 放同名 .png 即可）
    for cls in classes:
        # 只对 replaces_ocr 为空的类用模板（OCR 已经覆盖的不需要）
        if cls.get("replaces_ocr"):
            continue
        # 模板 key 格式: "category/name" - 这里默认放在 lobby/ 或 popup/ 下，需要按你模板组织
        # 简化：尝试在所有已加载模板里找 name
        candidates = [k for k in matcher.templates.keys() if k.endswith("/" + cls["name"])]
        for tpl_key in candidates:
            try:
                result = matcher.match_one(frame, tpl_key)
            except Exception:
                continue
            if result.matched and result.confidence >= 0.85:
                cx = (result.x + result.width / 2) / w
                cy = (result.y + result.height / 2) / h
                bw = result.width / w
                bh = result.height / h
                boxes.append(YoloBox(
                    class_id=cls["id"], cx=cx, cy=cy, w=bw, h=bh,
                    score=result.confidence
                ))
                break  # 一个类一张图最多 1 个 box（避免双计）
    return boxes


def _write_dataset(images: List[Tuple[str, List[YoloBox]]],
                   out_dir: str, val_ratio: float = 0.2,
                   seed: int = 42) -> dict:
    """按 train/val 切分写入 YOLO 标准目录结构"""
    random.seed(seed)
    random.shuffle(images)
    n_val = max(1, int(len(images) * val_ratio))
    val_set = images[:n_val]
    train_set = images[n_val:]

    counts = {"train": 0, "val": 0, "labels_total": 0, "by_class": {}}
    for split, items in [("train", train_set), ("val", val_set)]:
        img_dir = os.path.join(out_dir, split, "images")
        lbl_dir = os.path.join(out_dir, split, "labels")
        os.makedirs(img_dir, exist_ok=True)
        os.makedirs(lbl_dir, exist_ok=True)
        for src_path, boxes in items:
            name = os.path.basename(src_path)
            stem = os.path.splitext(name)[0]
            shutil.copy2(src_path, os.path.join(img_dir, name))
            with open(os.path.join(lbl_dir, stem + ".txt"), "w", encoding="utf-8") as f:
                for b in boxes:
                    f.write(b.to_yolo_line() + "\n")
                    counts["labels_total"] += 1
                    counts["by_class"][b.class_id] = counts["by_class"].get(b.class_id, 0) + 1
            counts[split] += 1
    return counts


def _write_data_yaml(out_dir: str, classes: list) -> str:
    """生成 ultralytics 用的 data.yaml"""
    data_yaml = {
        "path": out_dir,
        "train": "train/images",
        "val": "val/images",
        "nc": len(classes),
        "names": [c["name"] for c in classes],
    }
    yml_path = os.path.join(out_dir, "data.yaml")
    with open(yml_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data_yaml, f, allow_unicode=True, sort_keys=False)
    return yml_path


def _write_review_html(out_dir: str, samples: List[Tuple[str, List[YoloBox]]],
                        classes: list, max_n: int = 50) -> str:
    """生成可视化 HTML 让人快速浏览伪标签质量"""
    cls_by_id = {c["id"]: c for c in classes}
    html_path = os.path.join(out_dir, "review.html")
    sample = samples[:max_n]
    with open(html_path, "w", encoding="utf-8") as f:
        f.write("<!doctype html><meta charset='utf-8'><title>YOLO autolabel review</title>")
        f.write("<style>body{font-family:sans-serif;background:#222;color:#eee;}")
        f.write(".item{margin:20px 0;border:1px solid #444;padding:10px;}")
        f.write(".canvas{position:relative;display:inline-block;}")
        f.write(".box{position:absolute;border:2px solid;}")
        f.write(".lbl{position:absolute;font-size:11px;padding:1px 4px;color:#fff;}")
        f.write("img{max-width:1200px;}</style>\n")
        for path, boxes in sample:
            name = os.path.basename(path)
            f.write(f"<div class='item'><h3>{name}  ({len(boxes)} boxes)</h3>")
            f.write(f"<div class='canvas'><img src='{path}' id='img'>")
            for b in boxes:
                cls = cls_by_id.get(b.class_id, {})
                color = f"hsl({(b.class_id * 47) % 360}, 70%, 55%)"
                f.write(
                    f"<div class='box' style='border-color:{color};"
                    f"left:calc({(b.cx - b.w / 2) * 100}%);top:calc({(b.cy - b.h / 2) * 100}%);"
                    f"width:calc({b.w * 100}%);height:calc({b.h * 100}%);'></div>"
                )
                f.write(
                    f"<div class='lbl' style='background:{color};"
                    f"left:calc({(b.cx - b.w / 2) * 100}%);top:calc({(b.cy - b.h / 2) * 100}% - 14px);'>"
                    f"{cls.get('name', '?')} {b.score:.2f}</div>"
                )
            f.write("</div></div>\n")
    return html_path


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="YOLO 半自动标注")
    ap.add_argument("--raw",
                    default=os.path.join(_PROJ_ROOT, "fixtures", "yolo", "raw_screenshots"),
                    help="原始截图目录")
    ap.add_argument("--out",
                    default=os.path.join(_PROJ_ROOT, "fixtures", "yolo", "dataset"),
                    help="数据集输出目录")
    ap.add_argument("--templates",
                    default=os.path.join(_PROJ_ROOT, "fixtures", "templates"),
                    help="模板目录（用于补充 OCR 抓不到的图标）")
    ap.add_argument("--val-ratio", type=float, default=0.2)
    ap.add_argument("--review", action="store_true", help="生成 HTML 可视化")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 张（debug 用）")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.raw):
        print(f"❌ 原始截图目录不存在：{args.raw}", file=sys.stderr)
        print(f"   把游戏截图（PNG/JPG）丢进去再跑此脚本。", file=sys.stderr)
        os.makedirs(args.raw, exist_ok=True)
        return 1

    paths = sorted(
        glob.glob(os.path.join(args.raw, "*.png"))
        + glob.glob(os.path.join(args.raw, "*.jpg"))
    )
    if args.limit > 0:
        paths = paths[: args.limit]
    if not paths:
        print(f"❌ {args.raw} 里没找到图片", file=sys.stderr)
        return 1

    by_name, classes = _load_class_config()

    from backend.automation.ocr_dismisser import OcrDismisser
    ocr_obj = OcrDismisser()

    print(f"加载 {len(paths)} 张图片，{len(classes)} 个类，开始伪标注...")
    items: List[Tuple[str, List[YoloBox]]] = []
    for i, p in enumerate(paths, 1):
        frame = cv2.imread(p)
        if frame is None:
            print(f"  ⚠️  跳过 {p}：读不出来")
            continue
        ocr_boxes = _ocr_pseudo_boxes(frame, classes, ocr_obj)
        tpl_boxes = _template_pseudo_boxes(frame, classes, args.templates)
        all_boxes = ocr_boxes + tpl_boxes
        items.append((p, all_boxes))
        print(f"  [{i}/{len(paths)}] {os.path.basename(p)}  → {len(all_boxes)} boxes "
              f"(OCR={len(ocr_boxes)}, tpl={len(tpl_boxes)})")

    if not items:
        print("❌ 没产出任何标签")
        return 1

    counts = _write_dataset(items, args.out, val_ratio=args.val_ratio)
    yml_path = _write_data_yaml(args.out, classes)

    print(f"\n✅ 写入完成")
    print(f"   train: {counts['train']} 张")
    print(f"   val:   {counts['val']} 张")
    print(f"   总标签: {counts['labels_total']}")
    print(f"   类分布:")
    for cid, n in sorted(counts["by_class"].items()):
        cname = next((c["name"] for c in classes if c["id"] == cid), "?")
        print(f"     [{cid}] {cname:25}  {n}")
    print(f"\n   data.yaml: {yml_path}")

    if args.review:
        html = _write_review_html(args.out, items, classes)
        print(f"\n   review HTML: {html}")
        print(f"   浏览器打开抽查标签质量；改完手动调 .txt（YOLO 格式）")

    print(f"\n下一步：python tools/yolo_train.py --data {yml_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
