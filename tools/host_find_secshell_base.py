"""扫描 ELF 头 \\x7fELF，找 SONAME=libSecShell.so 的那个"""
import argparse
import sys
import os
import ctypes
import ctypes.wintypes as wt
import struct
sys.path.insert(0, os.path.dirname(__file__))
from host_memscan import scan_process_memory, find_ldplayer_vbox_pids

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
OpenProcess = kernel32.OpenProcess
OpenProcess.restype = wt.HANDLE
OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
CloseHandle = kernel32.CloseHandle
ReadProcessMemory = kernel32.ReadProcessMemory
ReadProcessMemory.restype = wt.BOOL
ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400


def read_at(h, addr, size):
    buf = ctypes.create_string_buffer(size)
    nread = ctypes.c_size_t(0)
    ok = ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(nread))
    if not ok:
        return None
    return buf.raw[:nread.value]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, default=None)
    ap.add_argument("--timeout", type=float, default=120)
    args = ap.parse_args()
    pid = args.pid or find_ldplayer_vbox_pids()[0]["pid"]

    # Scan for ELF magic "\x7fELF\x01" (32-bit ELF) — there will be many
    magic = b"\x7fELF\x01\x01\x01"  # ELF32 LSB
    result = scan_process_memory(pid, [magic], max_findings=500, timeout=args.timeout)
    elfs = [f["addr"] for f in result["findings"]]
    print(f"Found {len(elfs)} ELF32 headers")

    h = OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    libsecshell_candidates = []
    try:
        for base in elfs:
            # Read header + look for SONAME in next 64KB (dynstr)
            # Easier: just read first 256KB and search for "libSecShell.so" string
            data = read_at(h, base, 256 * 1024)
            if data is None:
                continue
            idx = data.find(b"libSecShell.so\x00")
            if idx >= 0:
                libsecshell_candidates.append((base, idx))
                print(f"  ELF @ 0x{base:x}  has SONAME 'libSecShell.so' at offset 0x{idx:x} (rel)")
    finally:
        CloseHandle(h)

    if libsecshell_candidates:
        print(f"\n=== {len(libsecshell_candidates)} candidate(s) ===")
        for base, soname_off in libsecshell_candidates:
            print(f"  Base: 0x{base:x}  (idiv would be at 0x{base + 0x7bb1c:x})")


if __name__ == "__main__":
    main()
