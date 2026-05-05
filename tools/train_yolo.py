"""
YOLO 训练脚本 — Mac 一键训 + 导出 ONNX + 上传回 Windows

用法:
    pip install ultralytics requests
    python tools/train_yolo.py
    # 自定义参数:
    python tools/train_yolo.py --epochs 100 --imgsz 640 --batch 16 --no-upload

工作流程:
    1. 从 http://192.168.0.102:8900/api/labeler/export.zip 下载训练集
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

# 主 backend (8900) 而不是 debug_server (8901):
# 8901 上的 labeler 路由是早期遗留版本 (data 路径独立, classes.txt 卡在 2 类),
# 用户在前端 :8900/ui 标的全部数据 (包括 dialog/lobby/P5 类别) 都在 8900 这边.
# 历史教训 (2026-05-05): 之前默认 8901 导致训出 nc=2 模型, 覆盖 latest.onnx 后
# 老类别 (close_x/action_btn/dialog/lobby) 全废. 改默认到 8900.
DEBUG_SERVER = os.environ.get("GAMEBOT_DEBUG_SERVER", "http://192.168.0.102:8900")


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
    """写 ultralytics 要的 data.yaml + sanity check (防 nc 跟 label cid 不匹配)."""
    # Sanity check: 扫所有 label .txt, 看实际出现的 cid 范围
    from collections import Counter
    cid_count = Counter()
    for split in ("train", "val"):
        lbl_split = data_dir / "labels" / split
        if not lbl_split.is_dir():
            continue
        for f in lbl_split.glob("*.txt"):
            for line in f.read_text(encoding="utf-8").splitlines():
                parts = line.strip().split()
                if parts and parts[0].isdigit():
                    cid_count[int(parts[0])] += 1
    if cid_count:
        max_cid = max(cid_count.keys())
        if max_cid >= len(classes):
            raise ValueError(
                f"Sanity check 失败: label 文件里出现 cid={max_cid} 但 classes 只有"
                f" {len(classes)} 个 ({classes}). 数据集 / classes.txt 不一致, 中止训练."
            )
        print(f"     label cid 分布: {dict(sorted(cid_count.items()))}, max_cid={max_cid}")
        print(f"     classes 长度: {len(classes)}, names: {classes}")
    else:
        print(f"     [警告] 找不到任何 label box, 训练肯定挂")

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
    print(f"[3/6] data.yaml -> {yaml_path} (nc={len(classes)})")
    return yaml_path


def _patch_torch_save_retry():
    """
    workaround torch 2.11 + Windows 上 torch.save 偶发 IO 中断:
      ValueError: I/O operation on closed file
      RuntimeError: enforce fail at inline_container.cc:672

    用 BytesIO 缓冲 + 原子 rename 写盘，retry 5 次。
    保持 ultralytics 调用语义不变（直接 return _orig 拿到的返回）。
    """
    import torch as _t
    import io as _io
    import os as _os
    _orig = _t.save

    def _safe_save(obj, f, *args, **kwargs):
        import time as _time
        # 路径写：BytesIO 缓冲 + 原子 rename
        if not hasattr(f, "write"):
            buf = _io.BytesIO()
            _orig(obj, buf, *args, **kwargs)
            data = buf.getvalue()
            tmp = str(f) + ".tmp"
            last = None
            for i in range(5):
                try:
                    with open(tmp, "wb") as out:
                        out.write(data)
                    _os.replace(tmp, f)
                    return
                except (ValueError, OSError, RuntimeError) as e:
                    last = e
                    print(f"  [save retry {i+1}/5] {type(e).__name__}: {e}")
                    _time.sleep(0.5 * (i + 1))
            raise last
        # 文件对象：用原始 torch.save 但加 retry
        last = None
        for i in range(5):
            try:
                return _orig(obj, f, *args, **kwargs)
            except (ValueError, OSError, RuntimeError) as e:
                last = e
                print(f"  [save retry {i+1}/5] {type(e).__name__}: {e}")
                _time.sleep(0.5 * (i + 1))
        raise last

    _t.save = _safe_save
    try:
        import ultralytics.utils.patches as _p
        _p._torch_save = _safe_save
    except Exception:
        pass


def train(yaml_path: Path, epochs: int, imgsz: int, batch: int) -> Path:
    """跑训练。返回 best.pt 路径"""
    _patch_torch_save_retry()
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

    # Windows 上 DataLoader 多进程 (spawn) 经常挂 worker
    # workers=0 单进程加载，慢一点但稳；non-Windows 用默认 8 worker
    n_workers = 0 if os.name == "nt" else 8

    # patience=epochs 等价于关闭 early stop（确保跑满 epochs）
    # 数据集小（~100 张），ultralytics 可能认为收敛过早提前停 → 强行跑满
    results = model.train(
        data=str(yaml_path),
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        device=device,
        workers=n_workers,
        patience=epochs,    # 不提前停
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
