"""
YOLO 训练脚本 — Mac 一键训 + 导出 ONNX + 上传回 Windows

用法:
    pip install ultralytics requests
    python tools/train_yolo.py
    # 自定义参数:
    python tools/train_yolo.py --epochs 100 --imgsz 640 --batch 16 --no-upload

工作流程:
    1. 从 http://192.168.0.102:8901/api/labeler/export.zip 下载训练集
    2. 解压到 /tmp/yolo_train_<ts>/
    3. 80/20 划分 train / val
    4. 写 data.yaml
    5. 跑 ultralytics yolov8n.pt 训练
    6. 导出 ONNX (best.onnx)
    7. POST /api/yolo/upload_model 上传到 Windows 用户目录
"""
from __future__ import annotations

import argparse
import io
import json
import os
import random
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

DEBUG_SERVER = os.environ.get("GAMEBOT_DEBUG_SERVER", "http://192.168.0.102:8901")


def download_dataset(out_dir: Path) -> dict:
    """从 debug_server 下载 zip 并解压"""
    import requests
    url = f"{DEBUG_SERVER}/api/labeler/export.zip"
    print(f"[1/6] 下载数据集 {url} ...")
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    print(f"     收到 {len(r.content) / 1024 / 1024:.1f} MB")

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        zf.extractall(out_dir)

    manifest = json.loads((out_dir / "manifest.json").read_text())
    classes = (out_dir / "classes.txt").read_text().splitlines()
    classes = [c.strip() for c in classes if c.strip()]
    print(f"     classes={classes}")
    print(f"     labeled={manifest['labeled']}  bg_sampled={manifest['background_sampled']}")
    return {"classes": classes, **manifest}


def split_train_val(data_dir: Path, val_ratio: float = 0.2, seed: int = 42) -> tuple[int, int]:
    """80/20 划分。把 images/ 和 labels/ 分到 images/train,val 和 labels/train,val"""
    random.seed(seed)
    img_dir = data_dir / "images"
    lbl_dir = data_dir / "labels"

    images = sorted(img_dir.glob("*.png")) + sorted(img_dir.glob("*.jpg"))
    random.shuffle(images)
    n_val = max(1, int(len(images) * val_ratio))
    val_set = set(p.name for p in images[:n_val])

    for split in ("train", "val"):
        (img_dir / split).mkdir(parents=True, exist_ok=True)
        (lbl_dir / split).mkdir(parents=True, exist_ok=True)

    for p in images:
        split = "val" if p.name in val_set else "train"
        # move image
        new_img = img_dir / split / p.name
        if not new_img.exists():
            shutil.move(str(p), str(new_img))
        # move label (.txt with same stem)
        lbl = lbl_dir / f"{p.stem}.txt"
        if lbl.exists():
            new_lbl = lbl_dir / split / lbl.name
            if not new_lbl.exists():
                shutil.move(str(lbl), str(new_lbl))

    print(f"[2/6] 划分 train={len(images) - n_val}  val={n_val}")
    return len(images) - n_val, n_val


def write_data_yaml(data_dir: Path, classes: list[str]):
    """写 ultralytics 要的 data.yaml"""
    yaml_path = data_dir / "data.yaml"
    lines = [
        f"path: {data_dir.absolute()}",
        "train: images/train",
        "val: images/val",
        f"nc: {len(classes)}",
        "names:",
    ]
    for i, name in enumerate(classes):
        lines.append(f"  {i}: {name}")
    yaml_path.write_text("\n".join(lines) + "\n")
    print(f"[3/6] data.yaml -> {yaml_path}")
    return yaml_path


def train(yaml_path: Path, epochs: int, imgsz: int, batch: int) -> Path:
    """跑训练。返回 best.pt 路径"""
    from ultralytics import YOLO

    print(f"[4/6] 训练 yolov8n.pt epochs={epochs} imgsz={imgsz} batch={batch}")
    model = YOLO("yolov8n.pt")

    # 自动选最快的 device
    device = "cpu"
    try:
        import torch
        if torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            device = "cuda"
    except ImportError:
        pass
    print(f"     device={device}")

    results = model.train(
        data=str(yaml_path),
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        device=device,
        patience=20,    # 20 epoch 无提升提前停
        project=str(yaml_path.parent / "runs"),
        name="yolo_dismiss",
        exist_ok=True,
        verbose=True,
    )

    best = Path(results.save_dir) / "weights" / "best.pt"
    if not best.is_file():
        # ultralytics 旧版可能把 best.pt 放别处
        for p in Path(results.save_dir).rglob("best.pt"):
            best = p
            break
    print(f"     best.pt -> {best}")
    return best


def export_onnx(best_pt: Path, imgsz: int) -> Path:
    """导出 ONNX (CPU 推理友好，opset 12 兼容 onnxruntime)"""
    from ultralytics import YOLO
    print(f"[5/6] 导出 ONNX (imgsz={imgsz})")
    model = YOLO(str(best_pt))
    model.export(format="onnx", imgsz=imgsz, opset=12, dynamic=False, simplify=True)
    onnx_path = best_pt.with_suffix(".onnx")
    print(f"     ONNX -> {onnx_path} ({onnx_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return onnx_path


def upload_model(onnx_path: Path):
    """
    部署 ONNX 到用户目录。两种路径：
      A. Windows 本地跑训练 → 直接 copy 到 %APPDATA%\\GameBot\\... (无网络)
      B. Mac / 远程跑 → POST /api/yolo/upload_model 上传
    """
    # 优先尝试本地：本机就是 Windows 且 user_paths 模块能 import
    import os, shutil, sys
    if os.name == "nt":
        try:
            # 把项目根加到 sys.path 来 import backend.automation.user_paths
            here = Path(__file__).resolve().parent.parent
            if str(here) not in sys.path:
                sys.path.insert(0, str(here))
            from backend.automation.user_paths import user_yolo_dir
            models_dir = user_yolo_dir() / "models"
            models_dir.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            named = models_dir / f"dismiss_{ts}.onnx"
            latest = models_dir / "latest.onnx"
            shutil.copy(onnx_path, named)
            shutil.copy(onnx_path, latest)
            print(f"[6/6] 本地部署 ONNX (Windows 直写)")
            print(f"     -> {named}")
            print(f"     -> {latest}")
            return
        except Exception as e:
            print(f"     本地部署失败 ({e}), 走 HTTP 上传")

    # B. HTTP 上传
    import requests
    url = f"{DEBUG_SERVER}/api/yolo/upload_model"
    print(f"[6/6] HTTP 上传 ONNX 到 {url}")
    with open(onnx_path, "rb") as f:
        files = {"file": ("dismiss_v1.onnx", f, "application/octet-stream")}
        r = requests.post(url, files=files, timeout=60)
    r.raise_for_status()
    print(f"     {r.json()}")


def main():
    ap = argparse.ArgumentParser(description="YOLO 弹窗识别一键训练")
    ap.add_argument("--epochs", type=int, default=80, help="训练 epoch 数 (默认 80)")
    ap.add_argument("--imgsz", type=int, default=640, help="输入分辨率 (默认 640)")
    ap.add_argument("--batch", type=int, default=16, help="batch size (默认 16, Mac M1/M2 可设 8)")
    ap.add_argument("--no-upload", action="store_true", help="只训练不上传")
    ap.add_argument("--keep-tmp", action="store_true", help="保留 /tmp 目录便于检查")
    args = ap.parse_args()

    # 1. 下载
    work = Path(tempfile.mkdtemp(prefix=f"yolo_train_{int(time.time())}_"))
    print(f"[*] 工作目录: {work}")
    info = download_dataset(work)
    classes = info["classes"]

    # 2. 划分
    split_train_val(work, val_ratio=0.2)

    # 3. data.yaml
    yaml_path = write_data_yaml(work, classes)

    # 4. 训练
    best_pt = train(yaml_path, epochs=args.epochs, imgsz=args.imgsz, batch=args.batch)

    # 5. ONNX
    onnx_path = export_onnx(best_pt, imgsz=args.imgsz)

    # 6. 上传
    if not args.no_upload:
        upload_model(onnx_path)
    else:
        print(f"[skip upload] ONNX 在 {onnx_path}，自己 curl 上传")

    if not args.keep_tmp:
        # 保留 best.pt 和 onnx，删数据集
        keep_dir = Path.home() / ".gamebot_yolo_artifacts"
        keep_dir.mkdir(exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        shutil.copy(best_pt, keep_dir / f"best_{ts}.pt")
        shutil.copy(onnx_path, keep_dir / f"dismiss_{ts}.onnx")
        shutil.rmtree(work, ignore_errors=True)
        print(f"[cleanup] best.pt + onnx 备份到 {keep_dir}")
    else:
        print(f"[keep] 全部保留在 {work}")

    print("\n=== 训练完成 ===")
    print(f"模型已上传到 Windows: %APPDATA%\\GameBot\\data\\yolo\\models\\latest.onnx")
    print("下一步：写 yolo_dismisser.py 替换现有 dismiss 链")


if __name__ == "__main__":
    main()
