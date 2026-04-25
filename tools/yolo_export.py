#!/usr/bin/env python3
"""导出 YOLOv8 训练结果为 ONNX，供运行时推理用

用法：
    # 默认从 fixtures/yolo/runs/train/weights/best.pt 导出
    python tools/yolo_export.py

    # 指定权重 + 输出路径
    python tools/yolo_export.py --weights fixtures/yolo/runs/train/weights/best.pt \
                                --out backend/automation/models/ui_yolo.onnx

输出 ONNX 后，runtime 会在 backend/automation/yolo_detector.py 自动 lazy-load。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights",
                    default=os.path.join(_PROJ_ROOT, "fixtures", "yolo", "runs", "train", "weights", "best.pt"))
    ap.add_argument("--out",
                    default=os.path.join(_PROJ_ROOT, "backend", "automation", "models", "ui_yolo.onnx"))
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--simplify", action="store_true", default=True,
                    help="onnx-simplifier 优化（默认开）")
    ap.add_argument("--opset", type=int, default=12)
    args = ap.parse_args(argv)

    if not os.path.exists(args.weights):
        print(f"❌ 权重不存在：{args.weights}", file=sys.stderr)
        print(f"   先跑 python tools/yolo_train.py 训练", file=sys.stderr)
        return 1

    try:
        from ultralytics import YOLO
    except ImportError:
        print("❌ ultralytics 未安装。pip install ultralytics", file=sys.stderr)
        return 1

    print(f"权重: {args.weights}")
    print(f"输出: {args.out}")
    print(f"imgsz: {args.imgsz}, opset: {args.opset}")

    model = YOLO(args.weights)
    onnx_path = model.export(
        format="onnx",
        imgsz=args.imgsz,
        simplify=args.simplify,
        opset=args.opset,
    )
    # ultralytics 通常输出到 weights 同目录的 .onnx
    print(f"\nultralytics 写到：{onnx_path}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    shutil.copy2(onnx_path, args.out)
    size_kb = os.path.getsize(args.out) / 1024
    print(f"\n✅ ONNX 拷贝到目标位置：{args.out} ({size_kb:.1f} KB)")
    print(f"\n运行时会自动 lazy-load 这个文件。重启 backend / GameBot.exe 即生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
