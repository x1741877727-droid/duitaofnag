#!/usr/bin/env python3
"""首次启动自动配置 — 跨硬件适配 OCR 后端

工作流：
  1. 探测硬件（OS + CPU + GPU vendor + RAM + Python 版本）
  2. 决定最优 OCR backend（DirectML / CUDA / CoreML / CPU）
  3. 安装对应 wheel，3 级 fallback：
       a. PyPI 默认源（海外/已配国内源用户）
       b. 清华源（pypi.tuna.tsinghua.edu.cn）
       c. 自有镜像服务器（国内 PyPI 全挂时兜底，SHA256 校验）
  4. 基准测试 OCR 速度
  5. 推荐最大实例数（按硬件分档）
  6. 持久化到 config/runtime.json

之后启动直接读 config/runtime.json，跳过探测。
强制重新探测：python tools/auto_configure.py --force
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_PROJ_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = _PROJ_ROOT / "config" / "runtime.json"

# ────────────── 镜像源（按优先级降级）──────────────
PYPI_MIRRORS = [
    None,  # 默认源
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple/",
]
# 自有镜像服务器（兜底）—— 部署见 tools/wheel_mirror_setup.md
OWN_WHEEL_SERVER = os.environ.get(
    "GAMEBOT_WHEEL_SERVER", "http://171.80.4.221:9902/wheels"
)
OWN_MANIFEST_URL = f"{OWN_WHEEL_SERVER}/manifest.json"

PYPI_TIMEOUT = 60
WHEEL_TIMEOUT = 120


# ════════════════════════════════════════
# 硬件探测
# ════════════════════════════════════════

def detect_hardware() -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "os": platform.system(),
        "os_version": platform.version(),
        "arch": platform.machine(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "py_tag": f"cp{sys.version_info.major}{sys.version_info.minor}",
        "cpu_count": os.cpu_count() or 1,
    }
    info["gpu_vendor"] = _detect_gpu_vendor(info["os"])
    info["gpu_name"] = _detect_gpu_name(info["os"])
    info["ram_gb"] = _detect_ram_gb()
    info["region_hint"] = _detect_region_hint()
    return info


def _detect_gpu_vendor(os_name: str) -> str:
    """返回 nvidia / amd / intel / apple_silicon / none"""
    if os_name == "Windows":
        try:
            r = subprocess.run(
                ["wmic", "path", "Win32_VideoController", "get", "Name"],
                capture_output=True, text=True, timeout=10,
            )
            names = r.stdout.lower()
            if "nvidia" in names or "geforce" in names or "rtx" in names:
                return "nvidia"
            if "amd" in names or "radeon" in names:
                return "amd"
            if "intel" in names:
                return "intel"
        except Exception:
            pass
    elif os_name == "Linux":
        # NV 优先
        if shutil.which("nvidia-smi"):
            try:
                r = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0 and r.stdout.strip():
                    return "nvidia"
            except Exception:
                pass
        # lspci 兜底
        if shutil.which("lspci"):
            try:
                r = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5)
                lo = r.stdout.lower()
                if "nvidia" in lo:
                    return "nvidia"
                if "amd" in lo or "radeon" in lo:
                    return "amd"
                if "intel" in lo and ("vga" in lo or "graphics" in lo):
                    return "intel"
            except Exception:
                pass
    elif os_name == "Darwin":
        if platform.machine() == "arm64":
            return "apple_silicon"
        return "intel"
    return "none"


def _detect_gpu_name(os_name: str) -> str:
    if os_name == "Windows":
        try:
            r = subprocess.run(
                ["wmic", "path", "Win32_VideoController", "get", "Name"],
                capture_output=True, text=True, timeout=10,
            )
            lines = [l.strip() for l in r.stdout.splitlines() if l.strip() and l.strip() != "Name"]
            return lines[0] if lines else ""
        except Exception:
            return ""
    return ""


def _detect_ram_gb() -> float:
    try:
        import psutil  # type: ignore
        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except ImportError:
        return 0.0


def _detect_region_hint() -> str:
    """通过 timezone 粗判用户大致位置（决定优先用国内还是海外源）"""
    try:
        # Python 3.9+ zoneinfo
        import time as _t
        tz = _t.tzname[0] if _t.tzname else ""
        if "China" in tz or "CST" in tz or "Asia" in tz:
            return "cn_likely"
    except Exception:
        pass
    return "unknown"


# ════════════════════════════════════════
# 选 OCR 后端
# ════════════════════════════════════════

def select_ocr_backend(hw: Dict[str, Any]) -> Dict[str, Any]:
    """根据硬件返回需要安装的 wheel + 启用参数"""
    os_n = hw["os"]
    gpu = hw["gpu_vendor"]

    if os_n == "Windows":
        if gpu in ("nvidia", "amd", "intel"):
            return {
                "package": "onnxruntime-directml",
                "version_spec": ">=1.18.0",
                "ocr_params": {"EngineConfig.onnxruntime.use_dml": True},
                "expected_provider": "DmlExecutionProvider",
                "tier": "S" if gpu == "nvidia" else "A",
            }
        return _cpu_backend()

    if os_n == "Linux" and gpu == "nvidia":
        return {
            "package": "onnxruntime-gpu",
            "version_spec": ">=1.18.0",
            "ocr_params": {"EngineConfig.onnxruntime.use_cuda": True},
            "expected_provider": "CUDAExecutionProvider",
            "tier": "S",
        }

    if os_n == "Darwin":
        # CoreML 在默认 onnxruntime 里自带（macOS only）
        return {
            "package": "onnxruntime",
            "version_spec": ">=1.18.0",
            "ocr_params": (
                {"EngineConfig.onnxruntime.use_coreml": True}
                if gpu == "apple_silicon" else {}
            ),
            "expected_provider": "CoreMLExecutionProvider" if gpu == "apple_silicon" else "CPUExecutionProvider",
            "tier": "A" if gpu == "apple_silicon" else "C",
        }

    return _cpu_backend()


def _cpu_backend() -> Dict[str, Any]:
    return {
        "package": "onnxruntime",
        "version_spec": ">=1.18.0",
        "ocr_params": {},
        "expected_provider": "CPUExecutionProvider",
        "tier": "C",
    }


# ════════════════════════════════════════
# 安装（3 级 fallback）
# ════════════════════════════════════════

def install_with_fallback(spec: Dict[str, Any], region_hint: str = "unknown") -> Tuple[bool, str]:
    """按优先级试 PyPI / 国内镜像 / 自有镜像。返回 (success, used_source)"""
    pkg = spec["package"]
    spec_str = f"{pkg}{spec['version_spec']}"

    # 国内用户优先国内源
    mirrors = PYPI_MIRRORS[:]
    if region_hint == "cn_likely":
        mirrors = [PYPI_MIRRORS[1], PYPI_MIRRORS[2], PYPI_MIRRORS[0]]

    for idx, mirror in enumerate(mirrors, 1):
        label = mirror or "PyPI 默认"
        print(f"  [{idx}/{len(mirrors) + 1}] 尝试 {label}...")
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", spec_str]
        if mirror:
            cmd.extend(["-i", mirror])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=PYPI_TIMEOUT)
            if r.returncode == 0:
                print(f"    ✓ 成功（{label}）")
                return True, label
            err_tail = (r.stderr or "")[-200:].strip()
            print(f"    ✗ 失败：{err_tail or 'returncode={}'.format(r.returncode)}")
        except subprocess.TimeoutExpired:
            print(f"    ✗ 超时（{PYPI_TIMEOUT}s）")
        except Exception as e:
            print(f"    ✗ {e}")

    # 第 N+1 级：自有镜像服务器
    print(f"  [{len(mirrors) + 1}/{len(mirrors) + 1}] 尝试自有镜像 {OWN_WHEEL_SERVER}...")
    if _install_from_own_mirror(spec):
        return True, "own_mirror"

    return False, "all_failed"


def _install_from_own_mirror(spec: Dict[str, Any]) -> bool:
    """从我们的服务器下载 wheel 并 SHA256 校验"""
    try:
        # 1. 拉 manifest
        with urllib.request.urlopen(OWN_MANIFEST_URL, timeout=10) as resp:
            manifest = json.load(resp)
    except (urllib.error.URLError, socket.timeout) as e:
        print(f"    ✗ 镜像 manifest 拉取失败：{e}")
        return False
    except Exception as e:
        print(f"    ✗ 镜像未配置：{e}")
        return False

    pkg = spec["package"]
    py_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    plat_tag = _platform_tag()
    key = f"{pkg}-{py_tag}-{plat_tag}"

    entry = manifest.get("wheels", {}).get(key)
    if not entry:
        print(f"    ✗ manifest 没有 {key}（可用：{list(manifest.get('wheels', {}).keys())[:5]}）")
        return False

    url = entry["url"]
    sha256_expected = entry["sha256"]

    # 2. 下载到临时
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".whl")
    tmp.close()
    try:
        with urllib.request.urlopen(url, timeout=WHEEL_TIMEOUT) as resp, open(tmp.name, "wb") as f:
            shutil.copyfileobj(resp, f)
        # 3. SHA256 校验
        h = hashlib.sha256()
        with open(tmp.name, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        if h.hexdigest() != sha256_expected:
            print(f"    ✗ SHA256 不匹配（预期 {sha256_expected[:16]}, 实际 {h.hexdigest()[:16]}）")
            return False
        # 4. pip install 本地
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", tmp.name],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode == 0:
            print(f"    ✓ 自有镜像下载安装成功（SHA256 ✓）")
            return True
        print(f"    ✗ pip install 失败：{r.stderr[-200:]}")
        return False
    except Exception as e:
        print(f"    ✗ {e}")
        return False
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


def _platform_tag() -> str:
    """生成 PyPI wheel 平台 tag，简化版"""
    s = platform.system()
    m = platform.machine().lower()
    if s == "Windows":
        return "win_amd64" if m in ("amd64", "x86_64") else "win32"
    if s == "Linux":
        return "manylinux2014_x86_64" if m in ("x86_64", "amd64") else f"manylinux2014_{m}"
    if s == "Darwin":
        return "macosx_arm64" if m == "arm64" else "macosx_x86_64"
    return "unknown"


# ════════════════════════════════════════
# Bench
# ════════════════════════════════════════

def benchmark_ocr(spec: Dict[str, Any]) -> Tuple[float, str]:
    """跑 5 次 OCR 取均值。返回 (avg_ms, actual_provider)"""
    try:
        import cv2  # noqa
        from rapidocr import RapidOCR
        import onnxruntime as ort
    except ImportError as e:
        return -1, f"import_error: {e}"

    test_img_path = _PROJ_ROOT / "fixtures" / "golden_set" / "lobby_smoke" / "frame.png"
    if test_img_path.exists():
        import cv2
        img = cv2.imread(str(test_img_path))
    else:
        # synth：1280×720 黑底 + 一行白字（OCR 跑得起来即可）
        import numpy as np
        img = np.zeros((720, 1280, 3), dtype=np.uint8)

    params = spec.get("ocr_params", {})
    ocr = RapidOCR(params=params) if params else RapidOCR()
    ocr(img)  # warmup

    ts = []
    for _ in range(5):
        t0 = time.perf_counter()
        ocr(img)
        ts.append((time.perf_counter() - t0) * 1000)
    avg = sum(ts) / len(ts)

    # 实测启用了哪个 provider
    avail = ort.get_available_providers()
    return round(avg, 1), ",".join(avail)


# ════════════════════════════════════════
# 主入口
# ════════════════════════════════════════

def estimate_script_overhead(avg_ms: float) -> Dict[str, Any]:
    """估算脚本本身（OCR/识别）的资源占用，方便用户决定加几个实例。

    *不限制*用户多开数 —— 12 开你的机器跑得起来就跑得起来。
    我们只汇报：每实例每分钟，脚本会消耗多少 GPU/CPU 时间。

    估算前提：单实例平均每秒 1-2 次 OCR（实测的轮询频率）。
    """
    if avg_ms < 0:
        return {"per_instance_per_sec_ms": -1, "note": "bench failed"}
    # 单次 OCR avg_ms × 1-2 calls/sec ≈ avg_ms-2*avg_ms ms 推理时间/秒
    per_sec_low = round(avg_ms * 1.0, 1)
    per_sec_high = round(avg_ms * 2.0, 1)
    # 假如 12 实例并发，GPU 占用估算（仅参考，真实值取决于 batch / 并发）
    twelve_concurrent_gpu_pct = round(per_sec_high * 12 / 1000 * 100, 1)
    return {
        "per_instance_per_sec_ms": [per_sec_low, per_sec_high],
        "if_12_instances_gpu_pct": twelve_concurrent_gpu_pct,
        "note": "估算每实例每秒 1-2 次 OCR 调用，真实值看实际轮询",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="强制重跑（忽略已有 config/runtime.json）")
    ap.add_argument("--skip-install", action="store_true", help="跳过 pip install（仅 bench + 写 config）")
    ap.add_argument("--skip-bench", action="store_true", help="跳过基准测试")
    args = ap.parse_args(argv)

    if CONFIG_PATH.exists() and not args.force:
        print(f"已有配置 {CONFIG_PATH}，跳过（--force 强制重跑）")
        with open(CONFIG_PATH) as f:
            print(json.dumps(json.load(f), indent=2, ensure_ascii=False))
        return 0

    print("=" * 60)
    print("GameBot 首次启动配置")
    print("=" * 60)

    print("\n[1/4] 探测硬件...")
    hw = detect_hardware()
    for k, v in hw.items():
        print(f"  {k:18}: {v}")

    print("\n[2/4] 选 OCR 后端...")
    spec = select_ocr_backend(hw)
    print(f"  package:           {spec['package']} {spec['version_spec']}")
    print(f"  expected_provider: {spec['expected_provider']}")
    print(f"  tier:              {spec['tier']}")
    print(f"  ocr_params:        {spec['ocr_params']}")

    install_source = "skipped"
    if not args.skip_install:
        print(f"\n[3/4] 安装 {spec['package']}（3 级 fallback）...")
        ok, install_source = install_with_fallback(spec, hw["region_hint"])
        if not ok:
            print(f"  ⚠️  所有渠道都失败，使用现有 onnxruntime（CPU）")
            spec = _cpu_backend()
            install_source = "fallback_cpu"

    avg_ms = -1
    actual_providers = ""
    if not args.skip_bench:
        print(f"\n[4/4] 基准测试...")
        avg_ms, actual_providers = benchmark_ocr(spec)
        print(f"  OCR avg:   {avg_ms} ms (5 次均值)")
        print(f"  providers: {actual_providers}")

    overhead = estimate_script_overhead(avg_ms)
    print(f"\n  脚本开销估算（不限制实例数，由你决定开几个）:")
    if overhead.get("per_instance_per_sec_ms", [0, 0])[0] >= 0:
        lo, hi = overhead["per_instance_per_sec_ms"]
        print(f"    每实例每秒 OCR 推理: {lo}-{hi} ms")
        print(f"    若开 12 实例同时跑: GPU/CPU 占用约 {overhead['if_12_instances_gpu_pct']}%")
        print(f"    （实际值取决于轮询频率 + 多 instance 并发模式）")

    config = {
        "schema": 1,
        "configured_at": datetime.now(timezone.utc).isoformat(),
        "hardware": hw,
        "ocr_backend": spec,
        "install_source": install_source,
        "ocr_avg_ms": avg_ms,
        "ocr_providers": actual_providers,
        "script_overhead_estimate": overhead,
    }
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 60}")
    # 避开 Windows cp936 不支持的特殊字符
    print(f"[OK] 配置已保存: {CONFIG_PATH}")
    print(f"{'=' * 60}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
