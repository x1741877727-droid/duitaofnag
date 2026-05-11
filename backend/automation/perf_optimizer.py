"""
Performance optimizer — 跨硬件 LDPlayer 9 自动调优 (Python 等价于 ps1+bat)

设计文档: docs/PERF_TUNING.md

入口:
  detect_hardware() → HardwareInfo
  compute_plan(hw, target=12) → Plan
  apply_plan(plan, on_step) → AppliedResult     (异步, 上报进度)
  load_state() / save_state() → JSON in %APPDATA%/GameBot/state/

仅 Windows 有效 (ldconsole + dnplayer 都是 win32). 其他平台 detect 仍能跑, apply 报错.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# ─────────────── 常量 ───────────────
#
# 数字依据 (用户实测 + agent 调查综合):
#   - PUBG Mobile 中国版 (UE4) 实际占用峰值 ~1.0-1.2 GB
#   - Android system + LDPlayer overhead ~600-800 MB
#   - 用户实测 2048 经常闪退 → MIN 提到 3072
#   - 客户主流 4096 / 3 核 (E5-2673v3 / 2696v3 + 32-64 GB) → DEFAULT 4096
#   - 项目硬性要求 6 实例同跑 → TARGET 6 (不再 12)
#   - LDPlayer 9 + PUBG 强制锁 960×540 dpi 240 (用户实测)

MIN_RAM_PER_INST = 3072        # 实测 2048 闪退率高
DEFAULT_RAM_PER_INST = 4096    # 客户主流配置
HOST_RESERVE_GB = 4            # Win + python backend (服务器机器一般不开 Chrome)
DEFAULT_TARGET_COUNT = 6       # 项目硬性要求

# 强制配置 (不暴露给用户选)
RESOLUTION = (960, 540)
DPI = 240
DEFAULT_CPU_PER_INST = 3       # PUBG 多开实测最佳
MIN_CPU_PER_INST = 2           # 弱机降级底线

# ldconsole 候选路径 (跨机器探测)
LDPLAYER_CANDIDATES = [
    r"D:\leidian\LDPlayer9",
    r"C:\leidian\LDPlayer9",
    r"E:\leidian\LDPlayer9",
    r"D:\Program Files\leidian\LDPlayer9",
    r"C:\Program Files\leidian\LDPlayer9",
    r"D:\ChangZhi\LDPlayer9",
]

# 14 个无用系统 app, disable 不卸载 (可逆)
SAFE_DISABLE_PACKAGES = [
    "com.android.calendar", "com.android.providers.calendar",
    "com.android.contacts", "com.android.providers.contacts",
    "com.android.gallery3d", "com.android.email", "com.android.deskclock",
    "com.android.printspooler", "com.android.bookmarkprovider",
    "com.android.wallpaper.livepicker", "com.android.dreams.basic",
    "com.android.bluetooth", "com.android.nfc", "com.android.htmlviewer",
]

# ─────────────── 数据类 ───────────────


@dataclass
class HardwareInfo:
    cpu_name: str = ""
    cpu_sockets: int = 0
    cpu_physical: int = 0
    cpu_logical: int = 0
    cpu_has_ht: bool = False
    ram_gb: float = 0.0
    ram_free_gb: float = 0.0
    gpu_name: str = "NONE"
    gpu_vendor: str = "OTHER"        # NVIDIA / AMD / Intel / OTHER / NONE
    disk_type: str = "Unknown"       # NVMe / SATA / HDD / Unknown
    ldplayer_path: str = ""
    instance_count_existing: int = 0
    instance_indexes: list[int] = field(default_factory=list)


@dataclass
class Plan:
    instance_count: int
    ram_per_inst_mb: int
    cpu_per_inst: int
    resolution_w: int                # 强制 960
    resolution_h: int                # 强制 540
    dpi: int                         # 强制 240
    threads_per_inst: int
    masks: list[int]                 # CPU affinity mask per instance
    startup_interval_s: int
    gpu_mode_recommendation: str     # 文字提示, 用户 GUI 设
    tier: str = "balanced"           # 命中哪一档 (1=optimal / 2=balanced / 3=marginal / 4=stretched)
    warnings: list[str] = field(default_factory=list)
    # 是否硬件达标 (False 时 = 极致压榨档, 仍可运行但风险高)
    fits_target: bool = True
    # 调试用: 估算闪退率 (0-100)
    estimated_crash_rate_pct: int = 0


@dataclass
class AppliedChange:
    """单条改动记录, 给前端 ResultList 渲染."""
    category: str                    # "全局" / "实例" / "系统" / "绑核"
    label: str
    detail: str = ""


@dataclass
class AppliedResult:
    success: bool
    changes: list[AppliedChange] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    error: Optional[str] = None
    duration_s: float = 0.0


# ─────────────── 工具 ───────────────


def _run(cmd: list[str], timeout: int = 30, encoding: str = "utf-8") -> tuple[int, str, str]:
    """run subprocess, 兼容 ldconsole 中文 GBK 输出."""
    try:
        p = subprocess.run(
            cmd, capture_output=True, timeout=timeout,
            text=False,  # 拿 bytes 自己 decode
        )
        # 先试 utf-8, 失败回 GBK
        for enc in (encoding, "gbk", "cp936"):
            try:
                stdout = p.stdout.decode(enc)
                stderr = p.stderr.decode(enc)
                return p.returncode, stdout, stderr
            except UnicodeDecodeError:
                continue
        return p.returncode, p.stdout.decode("utf-8", errors="replace"), p.stderr.decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError as e:
        return -1, "", str(e)


def _ps(script: str, timeout: int = 30) -> tuple[int, str]:
    """跑一段 PowerShell, 返 (rc, stdout)."""
    rc, out, _ = _run(["powershell", "-NoProfile", "-Command", script], timeout=timeout)
    return rc, out.strip()


def _find_ldplayer() -> Optional[str]:
    for p in LDPLAYER_CANDIDATES:
        if os.path.isfile(os.path.join(p, "ldconsole.exe")):
            return p
    # 注册表
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\leidian\LDPlayer9") as k:
            v, _ = winreg.QueryValueEx(k, "InstallDir")
            if v and os.path.isfile(os.path.join(v, "ldconsole.exe")):
                return v
    except Exception:
        pass
    return None


# ─────────────── 探测 ───────────────


def detect_hardware() -> HardwareInfo:
    """探测当前机器硬件 + LDPlayer 实例. 跨硬件适配."""
    hw = HardwareInfo()

    if sys.platform != "win32":
        hw.cpu_name = "non-Windows (detect 仅作 demo)"
        return hw

    # CPU
    rc, out = _ps(
        "Get-CimInstance Win32_Processor | "
        "Select Name,NumberOfCores,NumberOfLogicalProcessors | ConvertTo-Json -Compress"
    )
    if rc == 0 and out:
        try:
            data = json.loads(out)
            if isinstance(data, dict):
                data = [data]
            hw.cpu_sockets = len(data)
            hw.cpu_name = data[0].get("Name", "").strip()
            hw.cpu_physical = sum(int(d.get("NumberOfCores", 0)) for d in data)
            hw.cpu_logical = sum(int(d.get("NumberOfLogicalProcessors", 0)) for d in data)
            hw.cpu_has_ht = hw.cpu_logical > hw.cpu_physical
        except Exception as e:
            logger.warning(f"CPU 探测解析失败: {e}")

    # RAM
    rc, out = _ps(
        "$os=Get-CimInstance Win32_OperatingSystem; "
        "@{total=[math]::Round($os.TotalVisibleMemorySize/1MB,1); "
        "free=[math]::Round($os.FreePhysicalMemory/1MB,1)} | ConvertTo-Json -Compress"
    )
    if rc == 0:
        try:
            data = json.loads(out)
            hw.ram_gb = float(data.get("total", 0))
            hw.ram_free_gb = float(data.get("free", 0))
        except Exception:
            pass

    # GPU
    rc, out = _ps(
        "Get-CimInstance Win32_VideoController | "
        "Where-Object {$_.Name -notmatch 'Basic|Mirror|Microsoft|Remote' -and $_.AdapterRAM -gt 0} | "
        "Select -First 1 Name | ConvertTo-Json -Compress"
    )
    if rc == 0 and out and out != "null":
        try:
            data = json.loads(out)
            hw.gpu_name = data.get("Name", "NONE")
        except Exception:
            pass
    if hw.gpu_name == "NONE":
        hw.gpu_vendor = "NONE"
    elif re.search(r"NVIDIA|GeForce|Quadro|RTX|GTX|Tesla", hw.gpu_name, re.I):
        hw.gpu_vendor = "NVIDIA"
    elif re.search(r"AMD|Radeon|RX ", hw.gpu_name, re.I):
        hw.gpu_vendor = "AMD"
    elif re.search(r"Intel|Arc|Iris|UHD|HD Graphics", hw.gpu_name, re.I):
        hw.gpu_vendor = "Intel"

    # Disk type (system disk)
    rc, out = _ps(
        "$d=Get-Partition -DriveLetter $($env:SystemDrive.TrimEnd(':')) -EA SilentlyContinue; "
        "if($d){$pd=Get-PhysicalDisk | Where-Object {$_.DeviceId -eq $d.DiskNumber}; "
        "@{media=$pd.MediaType; bus=$pd.BusType} | ConvertTo-Json -Compress}"
    )
    if rc == 0 and out:
        try:
            data = json.loads(out)
            bus = data.get("bus", "")
            media = data.get("media", "")
            if bus == "NVMe":
                hw.disk_type = "NVMe"
            elif bus in ("SATA", "RAID", "SAS"):
                hw.disk_type = "SATA SSD" if media == "SSD" else "HDD"
            else:
                hw.disk_type = f"{media} / {bus}"
        except Exception:
            pass

    # LDPlayer
    hw.ldplayer_path = _find_ldplayer() or ""
    if hw.ldplayer_path:
        config_dir = Path(hw.ldplayer_path) / "vms" / "config"
        if config_dir.is_dir():
            for f in config_dir.glob("leidian*.config"):
                m = re.match(r"leidian(\d+)\.config", f.name)
                if m:
                    hw.instance_indexes.append(int(m.group(1)))
            hw.instance_indexes.sort()
            hw.instance_count_existing = len(hw.instance_indexes)

    return hw


# ─────────────── 算计划 ───────────────


def compute_plan(hw: HardwareInfo, target_count: int = DEFAULT_TARGET_COUNT) -> Plan:
    """4 档自动适配 (软警告版): 不拒绝弱机, 极致压榨 + 闪退风险提示.

    档 1 推荐: RAM>=(target*4096+host) & CPU_log>=18  -> 4096/3核, 0 警告
    档 2 平衡: RAM>=(target*3072+host) & CPU_log>=18  -> 3072/3核, 闪退~10%
    档 3 边缘: RAM>=(target*3072+host) & CPU_log>=12  -> 3072/2核, 闪退~20%
    档 4 压榨: 硬件不达标                              -> avail/N + cpu/N + 红色警告

    target_count 默认 6 (项目硬性要求). 用户软警告可选更多.
    """
    warnings: list[str] = []
    avail_mb = max(0, int((hw.ram_gb - HOST_RESERVE_GB) * 1024))
    cpu_log = hw.cpu_logical or hw.cpu_physical
    actual_count = max(1, target_count)

    # ─── 4 档判定 ───
    tier = ""
    crash_rate = 0
    fits = True

    # 档 1: 推荐
    if avail_mb >= actual_count * 4096 and cpu_log >= actual_count * 3:
        per_ram = 4096
        per_cpu = 3
        tier = "optimal"

    # 档 2: 平衡 (RAM 紧)
    elif avail_mb >= actual_count * 3072 and cpu_log >= actual_count * 3:
        per_ram = 3072
        per_cpu = 3
        tier = "balanced"
        crash_rate = 10
        warnings.append(f"RAM 紧张 (推荐 {actual_count*4096}MB, 实际 {avail_mb}MB), 闪退率 ~10%")

    # 档 3: 边缘 (RAM + CPU 都紧)
    elif avail_mb >= actual_count * 3072 and cpu_log >= actual_count * 2:
        per_ram = 3072
        per_cpu = 2
        tier = "marginal"
        crash_rate = 20
        warnings.append(f"RAM+CPU 紧张, 闪退率 ~20%, 单实例游戏内可能卡顿")

    # 档 4: 极致压榨 (硬件不达标, 但仍跑)
    else:
        # RAM: 平均分配, 向下取整到 256MB 对齐, 不低于 MIN_RAM_PER_INST 边界
        per_ram = max(MIN_RAM_PER_INST, (avail_mb // actual_count // 256) * 256)
        # CPU: logical / count, 至少 1
        per_cpu = max(1, cpu_log // actual_count)
        # 边界保护: per_ram 可能超 avail, 则降 count? — 用户要求软警告不拒绝, 直接报红
        tier = "stretched"
        fits = False

        # 估闪退率 (经验式)
        ram_ratio = (per_ram * actual_count) / max(avail_mb, 1)   # 越接近 1 越紧
        cpu_ratio = (per_cpu * actual_count) / max(cpu_log, 1)
        crash_rate = min(80, int(20 + max(ram_ratio - 1, 0) * 50 + max(cpu_ratio - 1, 0) * 30))

        warnings.append(
            f"硬件不达标: 6 实例最低需 24GB RAM + 12 logical CPU, "
            f"你的机器 {hw.ram_gb}GB + {cpu_log}T"
        )
        warnings.append(
            f"已极致压榨: RAM {per_ram}MB / CPU {per_cpu}核, 估闪退率 ~{crash_rate}%"
        )
        warnings.append(
            f"建议升级到 32GB RAM + E5-2673v3 (12C/24T) 以上"
        )

    # ─── CPU affinity mask ───
    threads_per_inst = 2 if hw.cpu_has_ht else 1
    # 让 affinity 占的 host 线程数跟 LDPlayer cpu 配置一致
    affinity_threads = per_cpu if hw.cpu_has_ht else max(1, per_cpu // 2)
    masks = [
        ((1 << affinity_threads) - 1) << (i * affinity_threads)
        for i in range(actual_count)
    ]

    # ─── NUMA 警告 ───
    if hw.cpu_sockets > 1:
        per_socket_phys = hw.cpu_physical / hw.cpu_sockets
        if actual_count > per_socket_phys:
            warnings.append(
                f"双 socket 检测: {actual_count} 实例超过单 socket 物理核 {int(per_socket_phys)}, "
                f"建议绑核到一个 socket 防跨 socket 访存"
            )

    # ─── 启动间隔 (按 disk type) ───
    # 数据来源: NVMe IOPS 500k+ vs SATA SSD ~100k vs HDD ~200; 模拟器启动 IO 主要在 vmdk 解压 + Android boot
    # 8/12/25 是经验值, 没找到精确实测论文, 跟 disk IOPS 趋势吻合
    interval = {
        "NVMe": 8,
        "SATA SSD": 12,
        "HDD": 25,
    }.get(hw.disk_type, 15)

    # ─── GPU 模式建议 ───
    gpu_rec = _gpu_mode_recommend(hw.gpu_name, hw.gpu_vendor)

    # ─── 弱机额外警告 ───
    if hw.gpu_vendor == "NONE":
        warnings.append("没探测到独立 GPU, 软件渲染会让 fps 跌到 5-10, 不建议生产")
    if hw.ram_gb < 16:
        warnings.append(f"RAM 仅 {hw.ram_gb}GB, 6 实例 PUBG 极端紧张, 强烈建议升级到 32GB+")

    return Plan(
        instance_count=int(actual_count),
        ram_per_inst_mb=int(per_ram),
        cpu_per_inst=int(per_cpu),
        resolution_w=RESOLUTION[0],
        resolution_h=RESOLUTION[1],
        dpi=DPI,
        threads_per_inst=threads_per_inst,
        masks=masks,
        startup_interval_s=interval,
        gpu_mode_recommendation=gpu_rec,
        tier=tier,
        warnings=warnings,
        fits_target=fits,
        estimated_crash_rate_pct=crash_rate,
    )


def _gpu_mode_recommend(name: str, vendor: str) -> str:
    if vendor == "NONE":
        return "软件渲染 (无独显, 性能极差, 仅作兜底)"
    if vendor == "NVIDIA":
        if re.search(r"RTX (30|40|50)|GTX 16|GTX 10", name, re.I):
            return "DirectX (极速)"
        if re.search(r"GT 1030|Quadro|MX", name, re.I):
            return "OpenGL"
        return "DirectX (默认 NVIDIA)"
    if vendor == "AMD":
        return "OpenGL (AMD 在 LDPlayer 9 OpenGL 更稳)"
    if vendor == "Intel":
        if re.search(r"Arc|Iris Xe", name, re.I):
            return "DirectX 11"
        return "OpenGL (集显, fps 受限)"
    return "DirectX (默认)"


# ─────────────── 应用 ───────────────


ProgressCallback = Callable[[str, str], Awaitable[None]]   # (step_id, message)


async def apply_plan(
    hw: HardwareInfo,
    plan: Plan,
    on_step: Optional[ProgressCallback] = None,
    audit: Optional[AuditReport] = None,
) -> AppliedResult:
    """执行优化. 上报进度通过 on_step('step_id', '描述').

    audit 不为 None 时启用增量模式: 跳过已 applied 的步骤, 只跑差异.
      - global_fps_audio applied -> 跳过 globalsetting
      - adb_doze_packages_logcat applied -> 跳过 ADB 调优
      - ram/cpu/resolution/dpi 任何一项 missing/drift -> 走 modify+restart 流程
                                  全部 applied/below_recommended -> 跳过 modify+restart

    audit 为 None 时走完整流程 (跟旧行为一致).
    """
    t0 = time.time()
    result = AppliedResult(success=False)

    if not hw.ldplayer_path:
        result.error = "找不到 LDPlayer 9 安装路径"
        return result

    ldconsole = os.path.join(hw.ldplayer_path, "ldconsole.exe")
    adb = os.path.join(hw.ldplayer_path, "adb.exe")

    async def _step(sid: str, msg: str):
        if on_step:
            try:
                await on_step(sid, msg)
            except Exception:
                pass
        logger.info(f"[perf_optimizer] [{sid}] {msg}")

    # ─── 增量决策 ───
    # incremental=True 且 audit 提供时, 按 audit 状态跳过 applied 步骤.
    incremental = audit is not None
    applied_keys = {it.key for it in (audit.applied if audit else [])}
    # ram/cpu/resolution/dpi 任何一项不是 applied 就需要 modify
    instance_cfg_keys = {"ram", "cpu", "resolution", "dpi"}
    needs_modify = (
        not incremental
        or any(k not in applied_keys for k in instance_cfg_keys)
    )
    needs_globalsetting = (
        not incremental
        or "global_fps_audio" not in applied_keys
    )
    needs_adb_tweaks = (
        not incremental
        or "adb_doze_packages_logcat" not in applied_keys
    )
    # 绑核只在 modify 引发重启时做 (重启会丢 affinity, 不重启则保留之前的)
    needs_bind_affinity = needs_modify

    if incremental:
        skipped_steps = []
        if not needs_modify:
            skipped_steps.append("modify+restart (LDPlayer 配置已符合)")
        if not needs_globalsetting:
            skipped_steps.append("globalsetting (之前已应用)")
        if not needs_adb_tweaks:
            skipped_steps.append("ADB 调优 (之前已应用)")
        if skipped_steps:
            await _step("incremental", "增量模式: 跳过 " + ", ".join(skipped_steps))

    # 用前 N 个实例 (按 index 升序), 不超过 plan.instance_count
    indexes = hw.instance_indexes[: plan.instance_count]

    try:
        # ─── 1. 关所有实例 (只在需要 modify 或 globalsetting 时) ───
        if needs_modify or needs_globalsetting:
            await _step("quitall", "关闭所有 LDPlayer 实例 (准备改配置)")
            _run([ldconsole, "quitall"], timeout=15)
            await asyncio.sleep(5)

        # ─── 2. 全局设置 (直写 leidians.config, 因为 LDPlayer 9.1.67 ldconsole 没 globalsetting 命令) ───
        if needs_globalsetting:
            await _step("globalsetting", "全局: 写 leidians.config (fps 30 / 音频关 / fastplay / cleanmode)")
            cfg_path = Path(hw.ldplayer_path) / "vms" / "config" / "leidians.config"
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
                cfg["framesPerSecond"] = 30
                cfg["reduceAudio"] = True
                cfg["vmdkFastMode"] = True
                cfg["cleanMode"] = True
                cfg_path.parent.mkdir(parents=True, exist_ok=True)
                cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
                result.changes.append(AppliedChange(
                    category="全局",
                    label="fps 30 + 音频关 + 启动加速 + 退出清缓存",
                    detail=f"写 {cfg_path}: framesPerSecond=30 reduceAudio=True vmdkFastMode=True cleanMode=True",
                ))
            except Exception as e:
                logger.warning(f"[perf_optimizer] 写 leidians.config 失败: {e}")
                result.changes.append(AppliedChange(
                    category="全局",
                    label="(失败) 写 leidians.config",
                    detail=str(e),
                ))
        else:
            result.skipped.append("全局设置 (audit 显示已应用)")

        # ─── 3. 改每实例配置 (含分辨率/dpi 强制锁死) ───
        if needs_modify:
            for idx in indexes:
                await _step(
                    f"modify_{idx}",
                    f"配置 inst{idx}: RAM={plan.ram_per_inst_mb}, CPU={plan.cpu_per_inst}, "
                    f"分辨率={plan.resolution_w}x{plan.resolution_h} dpi={plan.dpi}"
                )
                _run([
                    ldconsole, "modify",
                    "--index", str(idx),
                    "--memory", str(plan.ram_per_inst_mb),
                    "--cpu", str(plan.cpu_per_inst),
                    "--resolution", f"{plan.resolution_w},{plan.resolution_h},{plan.dpi}",
                    "--autorotate", "0",
                ], timeout=10)
            result.changes.append(AppliedChange(
                category="实例",
                label=(f"{len(indexes)} 实例: RAM {plan.ram_per_inst_mb}MB / CPU {plan.cpu_per_inst}核 / "
                       f"{plan.resolution_w}x{plan.resolution_h} dpi {plan.dpi} / 关自动转屏"),
                detail=f"index={indexes}",
            ))

            # ─── 4. 启动 (间隔 startup_interval_s) ───
            for idx in indexes:
                await _step(f"launch_{idx}", f"启动 inst{idx} (间隔 {plan.startup_interval_s}s 防 IO 抢)")
                _run([ldconsole, "launch", "--index", str(idx)], timeout=10)
                await asyncio.sleep(plan.startup_interval_s)
            result.changes.append(AppliedChange(
                category="启动",
                label=f"顺序启动 {len(indexes)} 实例 (间隔 {plan.startup_interval_s}s)",
            ))

            # ─── 5. 等待 boot ───
            await _step("wait_boot", "等模拟器 boot 完成 (30s) + 准备绑核")
            await asyncio.sleep(30)
        else:
            result.skipped.append("LDPlayer 配置 (RAM/CPU/分辨率/DPI 已符合)")

        # ─── 6. 绑核 (modify 引发重启时做; 不重启则保留 affinity) ───
        if needs_bind_affinity:
            bind_script = (
                "$masks=@(" + ",".join(str(m) for m in plan.masks) + ");"
                "$procs=Get-Process dnplayer -EA SilentlyContinue | Sort-Object StartTime;"
                "$bound=0;"
                "for($i=0;$i -lt [math]::Min($procs.Count, $masks.Count); $i++){"
                "  try{$procs[$i].ProcessorAffinity=[IntPtr]$masks[$i]; $bound++}"
                "  catch{}"
                "}"
                "Write-Host \"bound=$bound\""
            )
            rc, out = _ps(bind_script, timeout=15)
            bound = 0
            m = re.search(r"bound=(\d+)", out)
            if m:
                bound = int(m.group(1))
            await _step("bind_affinity", f"CPU 亲缘性绑定: {bound}/{len(indexes)} 个 dnplayer 进程")
            result.changes.append(AppliedChange(
                category="绑核",
                label=f"{bound}/{len(indexes)} 实例绑 P-core/物理核",
                detail=f"masks={[hex(m) for m in plan.masks]}",
            ))

        # ─── 7. ADB 系统裁剪 ───
        if needs_adb_tweaks:
            for idx in indexes:
                port = 5554 + idx * 2
                serial = f"127.0.0.1:{port}"
                await _step(f"adb_{idx}", f"Android 调优 inst{idx} (Doze/系统包/logcat)")

                _run([adb, "connect", serial], timeout=5)

                # 等 boot 完成 (最多 60s)
                for _ in range(12):
                    rc, out, _ = _run([adb, "-s", serial, "shell", "getprop", "sys.boot_completed"], timeout=5)
                    if rc == 0 and out.strip() == "1":
                        break
                    await asyncio.sleep(5)

                _run([adb, "-s", serial, "shell", "dumpsys", "deviceidle", "disable"], timeout=10)
                _run([adb, "-s", serial, "shell", "dumpsys", "deviceidle", "whitelist", "+com.tencent.tmgp.pubgmhd"], timeout=10)

                for pkg in SAFE_DISABLE_PACKAGES:
                    _run([adb, "-s", serial, "shell", "pm", "disable-user", "--user", "0", pkg], timeout=10)

                _run([adb, "-s", serial, "shell", "setprop", "debug.hwui.profile", "false"], timeout=5)
                _run([adb, "-s", serial, "shell", "setprop", "debug.hwui.show_dirty_regions", "false"], timeout=5)

                _run([adb, "-s", serial, "shell", "logcat", "-G", "64K"], timeout=5)
                _run([adb, "-s", serial, "shell", "logcat", "-c"], timeout=5)

            result.changes.append(AppliedChange(
                category="系统",
                label=f"Doze 关 + pubgmhd 白名单 + 14 无用包 disable + logcat 64K (x {len(indexes)})",
            ))
        else:
            result.skipped.append("ADB 系统调优 (audit 显示已应用)")

        # ─── 8. 收尾 ───
        result.success = True
        result.skipped.append(f"GPU 渲染模式 (需 GUI 设, LDPlayer 9 不存配置文件): 推荐 {plan.gpu_mode_recommendation}")
        await _step("done", "完成")

    except Exception as e:
        logger.error(f"[perf_optimizer] apply 失败: {e}", exc_info=True)
        result.error = str(e)

    result.duration_s = round(time.time() - t0, 1)
    return result


def estimate_apply_steps(plan: Plan, audit: Optional[AuditReport] = None) -> dict:
    """估算 apply 总步数 + 耗时, 给前端显示进度.

    返回 {total_steps: int, est_seconds: int, will_skip: list[str], will_run: list[str]}
    步数定义跟 apply_plan 里 _step() 调用对齐.
    """
    incremental = audit is not None
    applied_keys = {it.key for it in (audit.applied if audit else [])}
    instance_cfg_keys = {"ram", "cpu", "resolution", "dpi"}
    needs_modify = not incremental or any(k not in applied_keys for k in instance_cfg_keys)
    needs_globalsetting = not incremental or "global_fps_audio" not in applied_keys
    needs_adb_tweaks = not incremental or "adb_doze_packages_logcat" not in applied_keys

    n = plan.instance_count
    steps = 0
    seconds = 0
    will_run, will_skip = [], []

    if needs_modify or needs_globalsetting:
        steps += 1  # quitall
        seconds += 5
    if needs_globalsetting:
        steps += 1
        seconds += 1
        will_run.append("全局设置 (fps/audio/fastplay/cleanmode)")
    else:
        will_skip.append("全局设置")
    if needs_modify:
        steps += n           # modify x N
        steps += n           # launch x N
        steps += 1           # wait_boot
        steps += 1           # bind_affinity
        seconds += n         # modify ~1s/inst
        seconds += n * plan.startup_interval_s
        seconds += 30        # wait_boot
        seconds += 2         # bind
        will_run.append(f"重启 {n} 实例配 RAM/CPU/分辨率")
    else:
        will_skip.append(f"实例配置 ({n} 实例已符合)")
    if needs_adb_tweaks:
        steps += n           # adb_<idx> x N
        seconds += n * 10    # 估每实例 10s (包含 boot 检测)
        will_run.append(f"ADB 调优 {n} 实例 (Doze/14包/logcat)")
    else:
        will_skip.append("ADB 调优")
    steps += 1  # done
    return {
        "total_steps": steps,
        "est_seconds": seconds,
        "will_run": will_run,
        "will_skip": will_skip,
    }


# ─────────────── 状态持久化 ───────────────


def _state_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", "")) / "GameBot"
    else:
        base = Path.home() / ".gamebot"
    p = base / "state"
    p.mkdir(parents=True, exist_ok=True)
    return p / "perf-optimized.json"


def _hw_signature(hw: HardwareInfo) -> str:
    """硬件指纹: 换硬件 / 加内存 触发重优化提示."""
    cpu_short = re.sub(r"\(R\)|\(TM\)|CPU|@.*", "", hw.cpu_name).strip()
    return f"{cpu_short}|{hw.ram_gb}GB|{hw.gpu_name}|{hw.cpu_physical}C/{hw.cpu_logical}T"


def load_state() -> Optional[dict]:
    p = _state_path()
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_state(hw: HardwareInfo, plan: Plan, result: AppliedResult) -> None:
    payload = {
        "applied_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hardware_signature": _hw_signature(hw),
        "hardware": asdict(hw),
        "plan": asdict(plan),
        "result": {
            "success": result.success,
            "duration_s": result.duration_s,
            "changes": [asdict(c) for c in result.changes],
            "skipped": result.skipped,
            "error": result.error,
        },
    }
    try:
        _state_path().write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"save state 失败: {e}")


# ─────────────── Audit (增量补全) ───────────────


@dataclass
class AuditItem:
    """单项配置的状态."""
    key: str                         # "ram" / "cpu" / "resolution" / "dpi" / "fps" / "doze" / ...
    label: str                       # 中文显示名
    expected: str                    # 期望值 (字符串化)
    actual: str = ""                 # 当前值 (空 = 没读到)
    status: str = "missing"          # "applied" / "missing" / "drift" / "below_recommended"
    auto_fixable: bool = True        # 能不能一键补


@dataclass
class AuditReport:
    """整体审计结果. wizard 启动时调 audit_optimization_state(hw) 拿到, 给前端展示."""
    applied: list[AuditItem] = field(default_factory=list)
    missing: list[AuditItem] = field(default_factory=list)
    drift: list[AuditItem] = field(default_factory=list)
    below_recommended: list[AuditItem] = field(default_factory=list)
    instance_indexes_audited: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def needs_action(self) -> bool:
        # missing/drift = 必修; below_recommended = 可升级 (硬件限制下)
        # 用户希望 below 也提示, 让用户自己决定要不要升级到推荐配置
        return bool(self.missing or self.drift or self.below_recommended)

    @property
    def has_critical(self) -> bool:
        # 真的"非修不可" (区别于 below 的"可改进")
        return bool(self.missing or self.drift)

    @property
    def total_items(self) -> int:
        return len(self.applied) + len(self.missing) + len(self.drift) + len(self.below_recommended)


def _read_leidian_config(hw: HardwareInfo, idx: int) -> dict:
    """读 leidian{N}.config 提取 RAM/CPU/分辨率/dpi. 不存在 / 解析失败返回 {}.

    LDPlayer 9 config 是 flat keys with dot notation (实测 leidian0.config):
      "advancedSettings.memorySize": 3072,
      "advancedSettings.cpuCount": 2,
      "advancedSettings.resolution": { "width": 960, "height": 540 },
      "advancedSettings.resolutionDpi": 240,
    """
    if not hw.ldplayer_path:
        return {}
    cfg = Path(hw.ldplayer_path) / "vms" / "config" / f"leidian{idx}.config"
    if not cfg.is_file():
        return {}
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        res = data.get("advancedSettings.resolution", {}) or {}
        return {
            "memory_mb": data.get("advancedSettings.memorySize"),
            "cpu_count": data.get("advancedSettings.cpuCount"),
            "res_w": res.get("width"),
            "res_h": res.get("height"),
            "dpi": data.get("advancedSettings.resolutionDpi"),
        }
    except Exception as e:
        logger.debug(f"[audit] read leidian{idx}.config 失败: {e}")
        return {}


def audit_optimization_state(
    hw: Optional[HardwareInfo] = None,
    target_count: int = DEFAULT_TARGET_COUNT,
    expected_plan: Optional[Plan] = None,
) -> AuditReport:
    """检查 LDPlayer 当前实际配置 vs 推荐 plan, 返回缺啥/有啥/偏差.

    用户已设置过的 (applied) 不动, 缺的 (missing) 一键补, 偏差 (drift) 询问修复.
    """
    if hw is None:
        hw = detect_hardware()
    if expected_plan is None:
        expected_plan = compute_plan(hw, target_count=target_count)

    report = AuditReport()
    if not hw.ldplayer_path:
        report.notes.append("找不到 LDPlayer 安装路径, 无法 audit")
        return report

    # 看前 target_count 个实例 (跟 wizard apply 对齐)
    indexes = hw.instance_indexes[:target_count]
    if not indexes:
        report.notes.append("没检测到 LDPlayer 实例, 需要先创建")
        return report
    report.instance_indexes_audited = list(indexes)

    # ─── 每实例配置审计 ───
    ram_mismatch_count = 0
    cpu_mismatch_count = 0
    res_mismatch_count = 0
    dpi_mismatch_count = 0
    ram_below_count = 0
    cpu_below_count = 0

    for idx in indexes:
        cfg = _read_leidian_config(hw, idx)
        if not cfg:
            # 读不到 = 没配置过, 全 missing
            ram_mismatch_count += 1
            cpu_mismatch_count += 1
            res_mismatch_count += 1
            dpi_mismatch_count += 1
            continue

        # RAM
        actual_ram = cfg.get("memory_mb")
        if actual_ram is None or int(actual_ram) < MIN_RAM_PER_INST:
            ram_mismatch_count += 1
        elif int(actual_ram) < expected_plan.ram_per_inst_mb:
            ram_below_count += 1
        # CPU
        actual_cpu = cfg.get("cpu_count")
        if actual_cpu is None or int(actual_cpu) < MIN_CPU_PER_INST:
            cpu_mismatch_count += 1
        elif int(actual_cpu) < expected_plan.cpu_per_inst:
            cpu_below_count += 1
        # 分辨率
        if cfg.get("res_w") != RESOLUTION[0] or cfg.get("res_h") != RESOLUTION[1]:
            res_mismatch_count += 1
        # dpi
        if cfg.get("dpi") != DPI:
            dpi_mismatch_count += 1

    n = len(indexes)

    def _summarize(key: str, label: str, expected: str, mismatch_n: int, below_n: int = 0):
        if mismatch_n == 0 and below_n == 0:
            report.applied.append(AuditItem(key=key, label=label, expected=expected,
                                            actual=f"{n}/{n} 实例符合", status="applied"))
        elif mismatch_n > 0:
            status = "drift" if (n - mismatch_n) > 0 else "missing"
            report.missing.append(AuditItem(
                key=key, label=label, expected=expected,
                actual=f"{n - mismatch_n}/{n} 已配置, {mismatch_n} 缺/异常",
                status=status,
            )) if status == "missing" else report.drift.append(AuditItem(
                key=key, label=label, expected=expected,
                actual=f"{n - mismatch_n}/{n} 已配置, {mismatch_n} 缺/异常",
                status=status,
            ))
        else:
            report.below_recommended.append(AuditItem(
                key=key, label=label, expected=expected,
                actual=f"{below_n}/{n} 低于推荐 (硬件限制, 已极致压榨)",
                status="below_recommended", auto_fixable=False,
            ))

    _summarize("ram", "RAM 每实例",
               f"{expected_plan.ram_per_inst_mb} MB",
               ram_mismatch_count, ram_below_count)
    _summarize("cpu", "CPU 每实例",
               f"{expected_plan.cpu_per_inst} 核",
               cpu_mismatch_count, cpu_below_count)
    _summarize("resolution", "分辨率",
               f"{RESOLUTION[0]}×{RESOLUTION[1]} (强制锁死)",
               res_mismatch_count)
    _summarize("dpi", "DPI",
               f"{DPI} (强制锁死)",
               dpi_mismatch_count)

    # ─── 全局设置 audit (真读 vms/config/leidians.config) ───
    # 之前是看 state.json 的 success 标记, LDPlayer 自更新 / 重启会覆盖配置
    # state.json 仍标 success 但实际配置已被改回 → audit 误报 applied
    global_cfg = _read_leidians_global(hw)
    expected_global = {
        "framesPerSecond": 30,
        "reduceAudio": True,
        "vmdkFastMode": True,    # = LDPlayer 9 的 fastplay 等价字段
        "cleanMode": True,
    }
    drift_fields = []
    if global_cfg is None:
        report.missing.append(AuditItem(
            key="global_fps_audio",
            label="全局: fps 30 / 音频关 / fastplay / cleanmode",
            expected="一键应用",
            actual="leidians.config 读不到",
            status="missing",
        ))
    else:
        for k, expected_val in expected_global.items():
            actual_val = global_cfg.get(k)
            if actual_val != expected_val:
                drift_fields.append(f"{k}={actual_val} (期望 {expected_val})")
        if not drift_fields:
            report.applied.append(AuditItem(
                key="global_fps_audio",
                label="全局: fps 30 / 音频关 / fastplay / cleanmode",
                expected="一键应用",
                actual="4/4 字段符合",
                status="applied",
            ))
        else:
            # 任何字段偏离 -> drift (LDPlayer 重置 / 用户手动改了)
            report.drift.append(AuditItem(
                key="global_fps_audio",
                label="全局: fps 30 / 音频关 / fastplay / cleanmode",
                expected="一键应用",
                actual=f"{4 - len(drift_fields)}/4 符合, 偏离: {', '.join(drift_fields)}",
                status="drift",
            ))

    # ─── ADB 调优 audit (Doze/14 包/logcat 真读 adb 太重, 仍信任 state.json) ───
    # 这部分有 false-positive 风险 (LDPlayer 重启 ADB 状态可能 lost), 但 query 全部
    # 6 实例的 deviceidle / pm list / logcat -g 要 30+ adb 命令, audit 太慢.
    # 折中: state.json 有就标 applied; 用户能在 wizard apply 时手动重跑.
    state = load_state()
    if state and state.get("result", {}).get("success"):
        report.applied.append(AuditItem(
            key="adb_doze_packages_logcat",
            label="Android: Doze 关 / 14 包 disable / logcat 64K",
            expected=f"{n} 实例",
            actual="之前已应用 (未真验证, 重启可能 lost)",
            status="applied",
        ))
    else:
        report.missing.append(AuditItem(
            key="adb_doze_packages_logcat",
            label="Android: Doze 关 / 14 包 disable / logcat 64K",
            expected=f"{n} 实例", status="missing",
        ))

    return report


def _read_leidians_global(hw: HardwareInfo) -> Optional[dict]:
    """读 vms/config/leidians.config (LDPlayer 全局设置). 不存在返 None."""
    if not hw.ldplayer_path:
        return None
    cfg = Path(hw.ldplayer_path) / "vms" / "config" / "leidians.config"
    if not cfg.is_file():
        return None
    try:
        return json.loads(cfg.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug(f"[audit] read leidians.config 失败: {e}")
        return None


# ─────────────── 状态持久化 (沿用) ───────────────


def is_optimized(hw: Optional[HardwareInfo] = None) -> tuple[bool, Optional[str]]:
    """返 (已优化?, 硬件签名是否变).

    用于前端首次进中控台决定是否浮 modal.
      (False, None)        — 没优化过, 自动浮
      (True, "matched")     — 已优化 + 硬件没变, 不浮
      (True, "changed")     — 已优化但硬件变了 (换了 / 加内存 etc), 浮新提示
    """
    state = load_state()
    if not state or not state.get("result", {}).get("success"):
        return False, None
    if hw is None:
        hw = detect_hardware()
    sig_now = _hw_signature(hw)
    sig_old = state.get("hardware_signature", "")
    return True, ("matched" if sig_now == sig_old else "changed")
