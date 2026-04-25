"""
host_memscan.py — 从 Windows 宿主机读取 LDPlayer 虚拟机进程内存
绕过 ACE 反作弊：ACE 在 VM 内部，看不到宿主机的 ReadProcessMemory

用法:
  python tools/host_memscan.py                          # 自动找进程，搜索 nickName
  python tools/host_memscan.py --keywords 冰雾阴阳 傲娇0v0  # 搜索指定关键词
  python tools/host_memscan.py --pid 12345              # 指定进程 PID
  python tools/host_memscan.py --instance 0             # 指定模拟器实例索引
"""

import ctypes
import ctypes.wintypes as wt
import struct
import sys
import time
import argparse
import os
import json
import io

# Windows 控制台 GBK 编码兼容
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ── Windows API 常量 ──
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100

# ── Windows API 结构 ──
class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wt.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


# ── Windows API ──
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)

OpenProcess = kernel32.OpenProcess
OpenProcess.restype = wt.HANDLE
OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]

CloseHandle = kernel32.CloseHandle
CloseHandle.restype = wt.BOOL
CloseHandle.argtypes = [wt.HANDLE]

ReadProcessMemory = kernel32.ReadProcessMemory
ReadProcessMemory.restype = wt.BOOL
ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]

VirtualQueryEx = kernel32.VirtualQueryEx
VirtualQueryEx.restype = ctypes.c_size_t
VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p,
                           ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t]

# ── 进程查找 ──

def find_processes_by_name(name: str) -> list[dict]:
    """通过 tasklist 找进程，返回 [{pid, name, cmdline}]"""
    import subprocess
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
            text=True, creationflags=0x08000000  # CREATE_NO_WINDOW
        )
    except Exception:
        return []

    results = []
    for line in out.strip().splitlines():
        parts = line.strip('"').split('","')
        if len(parts) >= 2 and parts[0].lower() == name.lower():
            results.append({"name": parts[0], "pid": int(parts[1])})
    return results


def find_ldplayer_vbox_pids() -> list[dict]:
    """找到所有 LDPlayer Headless 进程（支持 LD9 和旧版）"""
    # LDPlayer 9 用 Ld9BoxHeadless.exe，旧版用 LdVBoxHeadless.exe
    procs = find_processes_by_name("Ld9BoxHeadless.exe")
    if not procs:
        procs = find_processes_by_name("LdVBoxHeadless.exe")
    return procs


def find_pid_for_instance(instance_index: int) -> int | None:
    """找指定实例的 LdVBoxHeadless.exe PID

    LDPlayer 实例名: leidian0, leidian1, ...
    通过 WMIC 获取命令行参数来匹配实例
    """
    import subprocess
    try:
        out = subprocess.check_output(
            ["wmic", "process", "where",
             "name='Ld9BoxHeadless.exe' or name='LdVBoxHeadless.exe'",
             "get", "ProcessId,CommandLine", "/FORMAT:CSV"],
            text=True, creationflags=0x08000000
        )
    except Exception:
        return None

    target = f"leidian{instance_index}"
    for line in out.strip().splitlines():
        if target in line.lower():
            # CSV 格式: Node,CommandLine,ProcessId
            parts = line.strip().split(",")
            try:
                return int(parts[-1])
            except ValueError:
                continue
    return None


# ── 内存扫描 ──

CHUNK_SIZE = 4 * 1024 * 1024  # 4MB 每次读取


def scan_process_memory(pid: int, keywords: list[bytes],
                        max_findings: int = 20,
                        timeout: float = 120.0,
                        progress_cb=None) -> dict:
    """扫描指定进程的全部可读内存，搜索关键词

    返回:
        {
            "findings": [{"keyword": str, "addr": int, "context": str}, ...],
            "stats": {"regions": int, "bytes_read": int, "time": float}
        }
    """
    handle = OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        err = ctypes.get_last_error()
        raise PermissionError(f"OpenProcess 失败 (error={err})，需要管理员权限运行")

    try:
        return _do_scan(handle, keywords, max_findings, timeout, progress_cb)
    finally:
        CloseHandle(handle)


def _do_scan(handle, keywords: list[bytes], max_findings: int,
             timeout: float, progress_cb) -> dict:
    findings = []
    mbi = MEMORY_BASIC_INFORMATION()
    mbi_size = ctypes.sizeof(mbi)

    addr = 0
    max_addr = (1 << 47) - 1  # 用户空间上限（64位）
    regions_scanned = 0
    bytes_read = 0
    t0 = time.time()

    # 预编译关键词的首字节，加速跳过
    kw_first_bytes = set()
    for kw in keywords:
        kw_first_bytes.add(kw[0])

    buf = ctypes.create_string_buffer(CHUNK_SIZE)
    bytes_actually_read = ctypes.c_size_t(0)

    # 上一个 chunk 的尾部（处理跨 chunk 边界的匹配）
    overlap_size = max(len(kw) for kw in keywords) if keywords else 0
    prev_tail = b""
    prev_tail_addr = 0

    while addr < max_addr:
        # 超时检查
        elapsed = time.time() - t0
        if elapsed > timeout:
            break
        if len(findings) >= max_findings:
            break

        # 查询内存区域信息
        result = VirtualQueryEx(handle, ctypes.c_void_p(addr), ctypes.byref(mbi), mbi_size)
        if result == 0:
            break

        region_base = mbi.BaseAddress or addr
        region_size = mbi.RegionSize

        # 跳到下一个区域
        next_addr = region_base + region_size
        if next_addr <= addr:
            addr += 0x1000
            continue

        # 只扫描已提交的、可读的区域
        if (mbi.State != MEM_COMMIT
                or mbi.Protect & PAGE_NOACCESS
                or mbi.Protect & PAGE_GUARD
                or mbi.Protect == 0):
            addr = next_addr
            prev_tail = b""
            continue

        # 逐 chunk 读取这个区域
        offset = 0
        while offset < region_size:
            if len(findings) >= max_findings:
                break

            chunk_addr = region_base + offset
            to_read = min(CHUNK_SIZE, region_size - offset)

            ok = ReadProcessMemory(handle, ctypes.c_void_p(chunk_addr),
                                   buf, to_read, ctypes.byref(bytes_actually_read))
            if not ok or bytes_actually_read.value == 0:
                offset += to_read
                prev_tail = b""
                continue

            n = bytes_actually_read.value
            data = buf.raw[:n]
            bytes_read += n
            regions_scanned += 1

            # 检查跨 chunk 边界
            if prev_tail:
                boundary = prev_tail + data[:overlap_size]
                for kw in keywords:
                    search_start = len(prev_tail) - len(kw) + 1
                    if search_start < 0:
                        search_start = 0
                    pos = boundary.find(kw, search_start)
                    if pos != -1:
                        real_addr = prev_tail_addr + pos
                        ctx = _extract_context(boundary, pos, len(kw))
                        findings.append({
                            "keyword": kw.decode("utf-8", errors="replace"),
                            "addr": real_addr,
                            "context": ctx,
                        })

            # 在当前 chunk 中搜索
            for kw in keywords:
                search_pos = 0
                while search_pos <= n - len(kw):
                    idx = data.find(kw, search_pos)
                    if idx == -1:
                        break
                    real_addr = chunk_addr + idx
                    ctx = _extract_context(data, idx, len(kw))
                    findings.append({
                        "keyword": kw.decode("utf-8", errors="replace"),
                        "addr": real_addr,
                        "context": ctx,
                    })
                    search_pos = idx + 1
                    if len(findings) >= max_findings:
                        break

            # 保存尾部用于下次边界检查
            if n > overlap_size:
                prev_tail = data[n - overlap_size:]
                prev_tail_addr = chunk_addr + n - overlap_size
            else:
                prev_tail = data
                prev_tail_addr = chunk_addr

            offset += to_read

            # 进度回调
            if progress_cb and regions_scanned % 500 == 0:
                progress_cb(regions_scanned, bytes_read, time.time() - t0)

        addr = next_addr

    elapsed = time.time() - t0
    return {
        "findings": findings[:max_findings],
        "stats": {
            "regions": regions_scanned,
            "bytes_read": bytes_read,
            "bytes_read_mb": round(bytes_read / 1024 / 1024, 1),
            "time": round(elapsed, 1),
        }
    }


def _extract_context(data: bytes, idx: int, kw_len: int, extra: int = 80) -> str:
    """提取关键词前后的上下文，转为可读文本"""
    cs = max(0, idx - extra)
    ce = min(len(data), idx + kw_len + extra)
    raw = data[cs:ce]
    # null 字节 → 分隔符
    text = raw.replace(b'\x00', b'|').decode('utf-8', errors='replace')
    return text


# ── 结果解析 ──

# 队伍数据结构中的已知字段名（用于截断名字后面粘连的字段）
_KNOWN_FIELDS = [
    'team_amount', 'game_begin_time', 'status', 'team_max', 'team_state',
    'room_state', 'invite_uid', 'online_state', 'bind_group', 'team_id',
    'tacit_value', 'icon', 'leader', 'uid', 'announcement', 'members',
    'picUrl', 'social_card', 'honor_label', 'segment_info',
    'team_liked', 'collect_', 'escape_', 'heroic_', 'highest_',
    'play_', 'city', 'plat', 'lobby', 'gender', 'label',
    'rejoin_', 'ugcm_', 'hunter_', 'ai_find', 'popularity',
    'avatar_icon', 'new_rank', 'ban_display', 'ugc_pass',
    'nickName', 'lbs_warzone', 'use_title', 'carteam_id',
    'startup_type', 'week_heroic', 'client_os_type',
]


# ── 精确 TLV 解析（新增 v2 实现） ─────────────────────────────────
#
# 协议字段格式（实测 dump）：
#   \x{name_field_len}{field_name_bytes}\x03{value_len}{utf8_value}
# 队伍成员 name 字段实例：
#   \x04 'name' \x03 \x09 傲娇0v0
# nickName 等其他字段同模式（长度前缀 + name + 类型 marker + 长度 + value）

# 玩家昵称提取的 stop words（道具 / 系统名 / Lua 关键字）
_NAME_STOP_WORDS = {
    "显眼包", "空投卡", "纪念币", "金币", "勋章",
    "skin", "weapon", "item", "icon", "default",
    "Iam显眼包", "铂金空投卡",
}


def _parse_tlv_names(blob: bytes,
                     field_names: tuple = (b"name", b"nickName", b"nick_name")) -> list[str]:
    """从二进制 blob 提取所有 TLV 格式的玩家昵称。

    扫描 \\x{len}{field_name}\\x03{value_len}{utf8_value} 模式。
    去 stop words / 控制字符。返回去重 list（保留出现顺序）。
    """
    names = []
    seen = set()
    for field in field_names:
        # 字段名前的长度前缀字节就是字段名长度
        marker = bytes([len(field)]) + field + b"\x03"
        start = 0
        while True:
            i = blob.find(marker, start)
            if i < 0:
                break
            len_pos = i + len(marker)
            if len_pos >= len(blob):
                break
            name_len = blob[len_pos]
            if 2 <= name_len <= 30:
                name_bytes = blob[len_pos + 1: len_pos + 1 + name_len]
                # 严格 UTF-8 解码，含控制字符则丢
                try:
                    name = name_bytes.decode("utf-8", errors="strict")
                    if (name and name not in seen
                            and not any(ord(c) < 0x20 for c in name)
                            and not any(w in name for w in _NAME_STOP_WORDS)):
                        seen.add(name)
                        names.append(name)
                except UnicodeDecodeError:
                    pass
            start = i + len(marker)
    return names


def _read_blob_at(handle, addr: int, size: int = 2048) -> bytes:
    """ReadProcessMemory 读 addr 起 size 字节"""
    buf = ctypes.create_string_buffer(size)
    bytes_read = ctypes.c_size_t(0)
    if not ReadProcessMemory(handle, ctypes.c_void_p(addr), buf,
                             size, ctypes.byref(bytes_read)):
        return b""
    return buf.raw[:bytes_read.value]


def _extract_name_after(text: str, field: str) -> str:
    """从 field 后面提取名字（v1 旧实现，留作 fallback）"""
    pos = text.find(field)
    if pos < 0:
        return ""
    after = text[pos + len(field):pos + len(field) + 60]
    # 去掉前导分隔符和控制字符
    after = after.lstrip("|").lstrip("\x00")
    name = ""
    for ch in after:
        if ch == '|' or ord(ch) < 0x20:
            if name:
                break
            continue
        name += ch
    # 去掉尾部粘连的字段名
    for f in _KNOWN_FIELDS:
        if f in name:
            name = name[:name.index(f)]
            break
    return name.strip()


def parse_team_info(findings: list[dict]) -> dict:
    """从扫描结果中解析队伍信息

    优先从 query_carteam_rsp / notify_update_carteam_member 结构中提取，
    这些包含完整的队伍数据（所有成员名字、team_amount 等）。
    """
    team_info = {
        "members": [],
        "team_amount": None,
        "team_state": None,
        "team_max": None,
        "team_id": None,
        "raw_findings": len(findings),
    }

    seen_names = set()

    # 第一遍：从队伍结构中提取（优先级最高）
    for f in findings:
        ctx = f["context"]

        # 从 name 字段提取（队伍结构中的成员名字）
        if "name\t" in ctx or "name\n" in ctx or "name|" in ctx:
            name = _extract_name_after(ctx, "name")
            if name and name not in seen_names and len(name) <= 30:
                seen_names.add(name)
                team_info["members"].append(name)

        # 从 nickName 提取（个人资料）
        if "nickName" in ctx:
            name = _extract_name_after(ctx, "nickName")
            if name and name not in seen_names and len(name) <= 30:
                seen_names.add(name)
                team_info["members"].append(name)

        # 从 nick_name 提取（team_info_notify 格式）
        if "nick_name" in ctx:
            name = _extract_name_after(ctx, "nick_name")
            if name and name not in seen_names and len(name) <= 30:
                seen_names.add(name)
                team_info["members"].append(name)

        # 提取 team_amount（取第一个找到的值）
        if "team_amount" in ctx and team_info["team_amount"] is None:
            ta_pos = ctx.find("team_amount")
            after = ctx[ta_pos + 11:ta_pos + 20]
            for ch in after:
                if ch.isdigit():
                    team_info["team_amount"] = int(ch)
                    break

        # 提取 team_state
        if "team_state" in ctx and team_info["team_state"] is None:
            ts_pos = ctx.find("team_state")
            after = ctx[ts_pos + 10:ts_pos + 20]
            for ch in after:
                if ch.isdigit():
                    team_info["team_state"] = int(ch)
                    break

        # 提取 team_max
        if "team_max" in ctx and team_info["team_max"] is None:
            tm_pos = ctx.find("team_max")
            after = ctx[tm_pos + 8:tm_pos + 18]
            for ch in after:
                if ch.isdigit():
                    team_info["team_max"] = int(ch)
                    break

    return team_info


# ── 高层 API（给自动化系统调用） ──

def get_team_members(instance_index: int = None, pid: int = None,
                     timeout: float = 30.0) -> dict:
    """获取当前队伍成员信息（给自动化系统调用的主入口）

    参数:
        instance_index: LDPlayer 实例索引（0, 1, ...）
        pid: Ld9BoxHeadless.exe 的 PID（如果已知）
        timeout: 超时秒数

    返回:
        {
            "ok": True/False,
            "members": ["玩家A", "玩家B", ...],
            "team_amount": 2,
            "team_state": 0,
            "team_max": 4,
            "scan_time": 3.1,
            "error": ""  # 如果 ok=False
        }
    """
    try:
        # 找进程
        if pid is None and instance_index is not None:
            pid = find_pid_for_instance(instance_index)
        if pid is None:
            procs = find_ldplayer_vbox_pids()
            if procs:
                pid = procs[0]["pid"]

        if pid is None:
            return {"ok": False, "error": "找不到 LDPlayer 进程", "members": []}

        # ── v2 实现：以推送锚点 + 精确 TLV 解析 ──
        # 优先扫这两个 anchor，它们的 blob 含当前队伍 name 字段
        anchor_keywords = [
            b"notify_update_carteam_member",
            b"team_info_notify",
            b"notify_join_carteam",
            b"query_carteam_rsp",  # 兵团 backup（万一队伍 anchor 抢空了）
        ]
        result = scan_process_memory(pid, anchor_keywords,
                                     max_findings=100, timeout=timeout)
        anchors = result["findings"]
        scan_time = result["stats"]["time"]

        # 对每个锚点 ReadProcessMemory + TLV 解析
        members = []
        seen = set()
        handle = OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
        if not handle:
            return {"ok": False, "error": "OpenProcess 失败", "members": []}
        try:
            for f in anchors:
                blob = _read_blob_at(handle, f["addr"], 2048)
                if not blob:
                    continue
                for name in _parse_tlv_names(blob):
                    if name not in seen:
                        seen.add(name)
                        members.append(name)
        finally:
            CloseHandle(handle)

        # 如果 v2 0 命中，回退到 v1 parser
        if not members:
            team = parse_team_info(anchors)
            return {
                "ok": True,
                "members": team["members"],
                "team_amount": team["team_amount"],
                "team_state": team["team_state"],
                "team_max": team["team_max"],
                "scan_time": scan_time,
                "parser": "v1_fallback",
            }

        return {
            "ok": True,
            "members": members,
            "team_amount": None,  # v2 暂不解 team_amount，需要再扩展 TLV
            "team_state": None,
            "team_max": None,
            "scan_time": scan_time,
            "parser": "v2_tlv",
            "anchors": len(anchors),
        }

    except Exception as e:
        return {"ok": False, "error": str(e), "members": []}


def verify_player(instance_index: int, expected_name: str,
                  pid: int = None, timeout: float = 30.0) -> dict:
    """验证指定玩家是否在队伍中

    返回:
        {
            "ok": True/False,
            "found": True/False,
            "members": [...],
            "scan_time": float,
        }
    """
    try:
        if pid is None and instance_index is not None:
            pid = find_pid_for_instance(instance_index)
        if pid is None:
            procs = find_ldplayer_vbox_pids()
            if procs:
                pid = procs[0]["pid"]

        if pid is None:
            return {"ok": False, "error": "找不到 LDPlayer 进程",
                    "found": False, "members": []}

        # 直接搜索玩家名 + 队伍结构
        keywords = [
            expected_name.encode("utf-8"),
            b"query_carteam_rsp",
            b"team_amount",
        ]

        result = scan_process_memory(pid, keywords,
                                     max_findings=20,
                                     timeout=timeout)

        team = parse_team_info(result["findings"])

        # 检查玩家名是否在队伍成员中
        found = expected_name in team["members"]

        # 也检查原始结果中是否有这个名字
        if not found:
            for f in result["findings"]:
                if expected_name in f.get("context", ""):
                    found = True
                    break

        return {
            "ok": True,
            "found": found,
            "members": team["members"],
            "team_amount": team["team_amount"],
            "scan_time": result["stats"]["time"],
        }

    except Exception as e:
        return {"ok": False, "error": str(e), "found": False, "members": []}


# ── CLI ──

def main():
    parser = argparse.ArgumentParser(description="从宿主机读取 LDPlayer 虚拟机内存")
    parser.add_argument("--pid", type=int, help="LdVBoxHeadless.exe 的 PID")
    parser.add_argument("--instance", type=int, help="LDPlayer 实例索引 (0, 1, ...)")
    parser.add_argument("--keywords", nargs="+", default=["nickName", "team_amount", "team_state"],
                        help="搜索关键词")
    parser.add_argument("--max-findings", type=int, default=20, help="最大结果数")
    parser.add_argument("--timeout", type=float, default=120.0, help="超时秒数")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    args = parser.parse_args()

    # 找进程
    pid = args.pid
    if pid is None and args.instance is not None:
        pid = find_pid_for_instance(args.instance)
        if pid is None:
            print(f"ERROR: 找不到实例 {args.instance} 的 LdVBoxHeadless.exe 进程")
            sys.exit(1)
        print(f"实例 {args.instance} → PID {pid}")

    if pid is None:
        procs = find_ldplayer_vbox_pids()
        if not procs:
            print("ERROR: 找不到 LdVBoxHeadless.exe 进程，确认 LDPlayer 正在运行")
            sys.exit(1)
        if len(procs) == 1:
            pid = procs[0]["pid"]
            print(f"找到 LdVBoxHeadless.exe → PID {pid}")
        else:
            print(f"找到 {len(procs)} 个 LdVBoxHeadless.exe 进程:")
            for p in procs:
                print(f"  PID {p['pid']}")
            print("请用 --pid 或 --instance 指定")
            sys.exit(1)

    # 编码关键词
    kw_bytes = []
    for kw in args.keywords:
        kw_bytes.append(kw.encode("utf-8"))

    print(f"扫描 PID={pid}, 关键词={args.keywords}, 超时={args.timeout}s")

    def progress(regions, bytes_read, elapsed):
        mb = bytes_read / 1024 / 1024
        print(f"  进度: {regions} 区域, {mb:.0f}MB, {elapsed:.1f}s", flush=True)

    try:
        result = scan_process_memory(pid, kw_bytes,
                                     max_findings=args.max_findings,
                                     timeout=args.timeout,
                                     progress_cb=progress)
    except PermissionError as e:
        print(f"ERROR: {e}")
        print("请以管理员身份运行！")
        sys.exit(1)

    if args.json:
        # JSON 输出（给自动化用）
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        # 人类可读输出
        stats = result["stats"]
        print(f"\n{'='*60}")
        print(f"扫描完成: {stats['time']}s, {stats['bytes_read_mb']}MB, "
              f"{stats['regions']} 区域, {len(result['findings'])} 条结果")
        print(f"{'='*60}")

        for i, f in enumerate(result["findings"]):
            print(f"\n[{i}] {f['keyword']} @ 0x{f['addr']:x}")
            print(f"    {f['context'][:200]}")

        # 解析队伍信息
        team = parse_team_info(result["findings"])
        print(f"\n--- 队伍信息 ---")
        print(f"  人数: {team['team_amount']}")
        print(f"  状态: {team['team_state']}")
        print(f"  成员: {team['members']}")


if __name__ == "__main__":
    main()
