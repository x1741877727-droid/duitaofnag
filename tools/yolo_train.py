#!/usr/bin/env python3
"""YOLOv8n 训练 — 用 ultralytics 一键训

依赖：pip install ultralytics
模型：默认 yolov8n.pt（最小，~3MB ONNX 导出）

用法：
    python tools/yolo_train.py
    python tools/yolo_train.py --data fixtures/yolo/dataset/data.yaml --epochs 100
    python tools/yolo_train.py --model yolov8s.pt   # 用 small 版（速度还能接受）

输出：runs/detect/train_<N>/weights/{best.pt, last.pt}
"""
from __future__ import annotations

import argparse
import os
import sys

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data",
                    default=os.path.join(_PROJ_ROOT, "fixtures", "yolo", "dataset", "data.yaml"),
                    help="data.yaml 路径（autolabel 生成）")
    ap.add_argument("--model", default="yolov8n.pt",
                    help="基础模型（yolov8n=最小; yolov8s=小; yolov8m=中）")
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default="",
                    help="GPU 设备（'' 自动；'0' 单卡；'cpu' 强制 CPU）")
    ap.add_argument("--project",
                    default=os.path.join(_PROJ_ROOT, "fixtures", "yolo", "runs"),
                    help="训练结果根目录")
    ap.add_argument("--name", default="train", help="本次 run 子目录名")
    args = ap.parse_args(argv)

    if not os.path.exists(args.data):
        print(f"❌ data.yaml 不存在：{args.data}", file=sys.stderr)
        print(f"   先跑 python tools/yolo_autolabel.py 生成数据集", file=sys.stderr)
        return 1

    try:
        from ultralytics import YOLO
    except ImportError:
        print("❌ ultralytics 未安装。pip install ultralytics", file=sys.stderr)
        return 1

    print(f"基础模型: {args.model}")
    print(f"数据:     {args.data}")
    print(f"epochs:   {args.epochs}")
    print(f"imgsz:    {args.imgsz}")
    print(f"batch:    {args.batch}")
    print(f"device:   {args.device or '自动'}")

    model = YOLO(args.model)
    model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device or None,
        project=args.project,
        name=args.name,
        exist_ok=False,
    )
    print(f"\n✅ 训练完成")
    print(f"   下一步：python tools/yolo_export.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
