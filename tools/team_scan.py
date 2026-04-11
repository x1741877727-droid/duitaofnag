"""
队伍信息扫描 — 通过 ADB dd 逐个读取 libc_malloc 区域
每次 dd 是独立进程，不会导致游戏崩溃

用法: python tools/team_scan.py [--serial emulator-5556] [--pid AUTO]
"""
import subprocess
import sys
import platform
import time
import re
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ADB = r"D:\leidian\LDPlayer9\adb.exe"
SERIAL = sys.argv[sys.argv.index("--serial") + 1] if "--serial" in sys.argv else "emulator-5556"
PACKAGE = "com.tencent.tmgp.pubgmhd"
_SF = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0

# 搜索的关键词（ASCII 字段名，上下文会包含中文值）
KEYWORDS = [b"nickName", b"team_state", b"team_amount", b"online_state",
            b"team_max", b"query_carteam_rsp", b"notify_update_carteam"]


def adb(*args):
    cmd = [ADB, "-s", SERIAL] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=15, creationflags=_SF)
        return r.stdout
    except:
        return b""


def get_pid():
    out = adb("shell", f"su 0 pidof {PACKAGE}").decode().strip()
    return int(out) if out.isdigit() else None


def get_malloc_regions(pid):
    """获取 libc_malloc 区域"""
    out = adb("shell", f"su 0 cat /proc/{pid}/maps").decode(errors='replace')
    regions = []
    for line in out.splitlines():
        if 'libc_malloc' not in line or 'rw-p' not in line:
            continue
        parts = line.split()
        s, e = parts[0].split('-')
        start = int(s, 16)
        end = int(e, 16)
        size = end - start
        # 跳过 <4KB 的小区域
        if size < 4096:
            continue
        regions.append((start, end, size))
    return regions


MAX_DD_SIZE = 2 * 1024 * 1024  # 每次 dd 最多读 2MB（跟原始成功方案一致）

def scan_region(pid, start, size):
    """用 dd 读取单个区域，大区域拆成 2MB 小块"""
    result = b""
    offset = start
    remaining = size
    while remaining > 0:
        chunk = min(remaining, MAX_DD_SIZE)
        skip = offset // 4096
        count = chunk // 4096
        if count == 0:
            break
        data = adb("exec-out", f"su 0 dd if=/proc/{pid}/mem bs=4096 skip={skip} count={count} 2>/dev/null")
        result += data
        offset += chunk
        remaining -= chunk
    return result


def extract_context(data, idx, kw_len, extra=120):
    """提取关键词前后的上下文，解码为可读文本"""
    cs = max(0, idx - 30)
    ce = min(len(data), idx + kw_len + extra)
    raw = data[cs:ce]
    # 把 null 字节替换为分隔符
    text = raw.replace(b'\x00', b'|').decode('utf-8', errors='replace')
    return text


def main():
    pid = get_pid()
    if not pid:
        print("ERROR: 游戏未运行")
        return

    print(f"PID: {pid}")
    regions = get_malloc_regions(pid)
    print(f"libc_malloc 区域: {len(regions)} 个, 总大小: {sum(r[2] for r in regions)/1024/1024:.1f}MB")

    t0 = time.time()
    all_results = []

    for i, (start, end, size) in enumerate(regions):
        data = scan_region(pid, start, size)
        if not data:
            continue

        for kw in KEYWORDS:
            pos = 0
            while True:
                idx = data.find(kw, pos)
                if idx == -1:
                    break
                ctx = extract_context(data, idx, len(kw))
                addr = start + idx
                all_results.append({
                    "region": i,
                    "addr": f"0x{addr:x}",
                    "keyword": kw.decode(),
                    "context": ctx,
                })
                pos = idx + 1

    elapsed = time.time() - t0

    # 验证游戏存活
    pid2 = get_pid()
    alive = pid2 == pid

    # 输出结构化结果
    print(f"\n{'='*60}")
    print(f"扫描完成: {elapsed:.1f}秒, 找到 {len(all_results)} 条, 游戏{'正常' if alive else '已崩溃!'}")
    print(f"{'='*60}")

    # 按类型分组输出
    team_entries = [r for r in all_results
                    if any(k in r['keyword'] for k in ['team_amount', 'team_state', 'team_max',
                                                        'query_carteam', 'notify_update_carteam',
                                                        'online_state'])]
    nick_entries = [r for r in all_results
                    if r['keyword'] == 'nickName'
                    and 'extraJson' not in r['context']
                    and 'MSDKGroup' not in r['context']]

    if team_entries:
        print(f"\n--- 队伍结构 ({len(team_entries)} 条) ---")
        for r in team_entries:
            print(f"  [{r['keyword']}] {r['addr']}")
            print(f"    {r['context'][:200]}")

    if nick_entries:
        print(f"\n--- 队员名字 ({len(nick_entries)} 条) ---")
        for r in nick_entries:
            # 提取 nickName 后面的实际名字
            ctx = r['context']
            nk_pos = ctx.find('nickName')
            if nk_pos >= 0:
                after = ctx[nk_pos + 8:nk_pos + 40]
                # 名字在 nickName 后面，到下一个 ASCII 字段名为止
                name = ""
                for ch in after:
                    if ch == '|' or (ord(ch) < 0x20):
                        break
                    name += ch
                # 去掉尾部的字段名
                for field in ['picUrl', 'social_card', 'honor_label', 'segment_info',
                              'team_liked', 'collect_', 'escape_', 'heroic_', 'highest_',
                              'play_', 'city', 'plat', 'lobby', 'gender', 'label',
                              'rejoin_', 'ugcm_', 'hunter_', 'ai_find', 'popularity']:
                    if field in name:
                        name = name[:name.index(field)]
                        break
                if name:
                    print(f"  玩家: {name}")
                    print(f"    上下文: {ctx[:150]}")

    print(f"\n游戏状态: {'✓ 正常 (PID={pid})' if alive else '✗ 已崩溃!'}")


if __name__ == "__main__":
    main()
