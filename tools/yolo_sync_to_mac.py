#!/usr/bin/env python3
"""把 Windows 上 yolo_verify 分桶后的截图增量同步到 Mac

流程：
  1. /exec 列出 Windows verified_pool/{verified,suspicious}/ 目录文件
  2. 跟本地 ~/yolo_review/{verified,suspicious}/ 比对，取差集
  3. /download 增量下载
  4. 可选 --clean-remote：成功下载后删 Windows 端原文件（清空 raw → verified_pool 流水线）

用法：
    python tools/yolo_sync_to_mac.py
    python tools/yolo_sync_to_mac.py --local ~/yolo_review --clean-remote
    python tools/yolo_sync_to_mac.py --bucket suspicious   # 只拉 suspicious
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import List, Set

import requests


DEFAULT_AGENT = "http://192.168.0.102:9100"
DEFAULT_TOKEN = "VUupUP5C_8_rTC6huCveQQ"
DEFAULT_REMOTE_BASE = r"D:\game-automation\duitaofnag\output\dist\GameBot\_internal\fixtures\yolo\verified_pool"
DEFAULT_LOCAL = Path.home() / "yolo_review"
DEFAULT_BUCKETS = ("verified", "suspicious")


def _exec(agent: str, token: str, cmd: str, timeout: int = 30) -> str:
    r = requests.post(
        f"{agent.rstrip('/')}/exec",
        headers={"X-Auth": token, "Content-Type": "application/json"},
        json={"cmd": cmd, "cwd": None, "timeout": timeout},
        timeout=timeout + 10,
    )
    r.raise_for_status()
    data = r.json()
    out = data.get("stdout", "") or ""
    err = data.get("stderr", "") or ""
    if data.get("returncode", 0) != 0 and err:
        sys.stderr.write(f"[exec stderr] {err}\n")
    return out


def list_remote_files(agent: str, token: str, remote_dir: str) -> List[str]:
    """用 PowerShell Get-ChildItem 列文件名。返回纯 basename list。"""
    # PowerShell 转义：单引号包裹路径
    ps = f"powershell -NoProfile -Command \"Get-ChildItem -Path '{remote_dir}' -File -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name\""
    out = _exec(agent, token, ps, timeout=30)
    files = [ln.strip() for ln in out.splitlines() if ln.strip()]
    return files


def download_file(agent: str, token: str, remote_path: str, local_path: Path,
                  timeout: int = 60) -> bool:
    """流式下载单文件"""
    r = requests.get(
        f"{agent.rstrip('/')}/download",
        headers={"X-Auth": token},
        params={"path": remote_path},
        timeout=timeout,
        stream=True,
    )
    if r.status_code != 200:
        sys.stderr.write(f"[!] {remote_path} HTTP {r.status_code}\n")
        return False
    local_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = local_path.with_suffix(local_path.suffix + ".part")
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(chunk_size=64 * 1024):
            if chunk:
                f.write(chunk)
    tmp.replace(local_path)
    return True


def remove_remote(agent: str, token: str, remote_path: str) -> bool:
    """删 Windows 文件（成功后清流水线）"""
    # 用 cmd del；路径双引号
    out = _exec(agent, token, f'del /F /Q "{remote_path}"', timeout=10)
    return True  # del 成功不输出


def sync_bucket(agent: str, token: str, remote_base: str, bucket: str,
                local_base: Path, clean: bool) -> dict:
    remote_dir = f"{remote_base}\\{bucket}"
    local_dir = local_base / bucket
    local_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[bucket={bucket}] 列出远端 {remote_dir} ...")
    try:
        remote_files = list_remote_files(agent, token, remote_dir)
    except requests.RequestException as e:
        print(f"[!] 列远端失败: {e}")
        return {"bucket": bucket, "error": str(e)}

    local_existing: Set[str] = {p.name for p in local_dir.glob("*")}
    todo = [f for f in remote_files if f not in local_existing]
    print(f"  远端 {len(remote_files)} / 本地已有 {len(local_existing)} / 待拉 {len(todo)}")

    pulled = 0
    cleaned = 0
    failed = 0
    t0 = time.perf_counter()
    for i, name in enumerate(todo, 1):
        remote_path = f"{remote_dir}\\{name}"
        local_path = local_dir / name
        try:
            ok = download_file(agent, token, remote_path, local_path)
        except requests.RequestException as e:
            sys.stderr.write(f"[!] {name} 下载异常: {e}\n")
            ok = False
        if ok:
            pulled += 1
            if clean:
                try:
                    if remove_remote(agent, token, remote_path):
                        cleaned += 1
                except Exception as e:
                    sys.stderr.write(f"[!] {name} 删远端失败: {e}\n")
        else:
            failed += 1
        if i % 10 == 0 or i == len(todo):
            print(f"  [{i}/{len(todo)}] pulled={pulled} cleaned={cleaned} failed={failed}")

    dt = time.perf_counter() - t0
    print(f"  [{bucket}] {pulled} 张 / {dt:.1f}s ({pulled / max(dt, 0.1):.1f} 张/秒)")
    return {"bucket": bucket, "pulled": pulled, "cleaned": cleaned, "failed": failed,
            "remote_total": len(remote_files), "local_existing": len(local_existing)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default=os.environ.get("REMOTE_AGENT", DEFAULT_AGENT),
                    help=f"Windows agent URL，默认 {DEFAULT_AGENT}")
    ap.add_argument("--token", default=os.environ.get("REMOTE_AGENT_TOKEN", DEFAULT_TOKEN),
                    help="X-Auth token")
    ap.add_argument("--remote-base", default=DEFAULT_REMOTE_BASE,
                    help=f"远端 verified_pool 根目录，默认 {DEFAULT_REMOTE_BASE}")
    ap.add_argument("--local", default=str(DEFAULT_LOCAL),
                    help=f"本地接收目录，默认 {DEFAULT_LOCAL}")
    ap.add_argument("--bucket", choices=DEFAULT_BUCKETS, default=None,
                    help="只同步指定 bucket（默认 verified+suspicious 都拉）")
    ap.add_argument("--clean-remote", action="store_true",
                    help="下载成功后删 Windows 原文件（清流水线）")
    args = ap.parse_args()

    local_base = Path(os.path.expanduser(args.local))
    buckets = (args.bucket,) if args.bucket else DEFAULT_BUCKETS

    print(f"[i] agent={args.agent}  local={local_base}  clean={args.clean_remote}")
    summaries = []
    for b in buckets:
        s = sync_bucket(args.agent, args.token, args.remote_base, b, local_base, args.clean_remote)
        summaries.append(s)

    print("\n[done]")
    for s in summaries:
        if "error" in s:
            print(f"  {s['bucket']}: ERROR {s['error']}")
        else:
            print(f"  {s['bucket']}: pulled={s['pulled']} cleaned={s['cleaned']} "
                  f"failed={s['failed']} (remote {s['remote_total']}, local {s['local_existing']})")


if __name__ == "__main__":
    main()
