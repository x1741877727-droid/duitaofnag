"""扫 Ld9BoxHeadless.exe 找 libSecShell.so 的加载位置"""
import argparse
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from host_memscan import scan_process_memory, find_ldplayer_vbox_pids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, default=None)
    ap.add_argument("--timeout", type=float, default=60)
    args = ap.parse_args()
    pid = args.pid or find_ldplayer_vbox_pids()[0]["pid"]
    print(f"pid={pid}")

    keywords = [
        b"libSecShell.so",
        b"_Z9root_killP7_JNIEnvP7_jclass",
        b"_Z23is_magisk_check_processP7_JNIEnvP7_jclass",
        b"assetsCacheDir",
    ]
    result = scan_process_memory(pid, keywords, max_findings=50, timeout=args.timeout)
    print(f"Findings: {len(result['findings'])}")
    for f in result["findings"]:
        kw = f["keyword"]
        addr = f["addr"]
        ctx = f.get("context", "")[:60]
        print(f"  {kw!r}  @ 0x{addr:016x}  ctx={ctx!r}")


if __name__ == "__main__":
    main()
