"""从宿主进程读 N 字节"""
import argparse
import sys
import os
import ctypes
import ctypes.wintypes as wt

sys.path.insert(0, os.path.dirname(__file__))
from host_memscan import find_ldplayer_vbox_pids

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, default=None)
    ap.add_argument("--addr", required=True, help="hex address, e.g. 0x79126b1c")
    ap.add_argument("--size", type=int, default=64)
    args = ap.parse_args()
    pid = args.pid or find_ldplayer_vbox_pids()[0]["pid"]
    addr = int(args.addr, 16) if args.addr.startswith("0x") else int(args.addr)
    h = OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        print(f"OpenProcess fail: {ctypes.get_last_error()}")
        return
    try:
        buf = ctypes.create_string_buffer(args.size)
        nread = ctypes.c_size_t(0)
        ok = ReadProcessMemory(h, ctypes.c_void_p(addr), buf, args.size, ctypes.byref(nread))
        if not ok:
            print(f"ReadProcessMemory fail: err={ctypes.get_last_error()}")
            return
        data = buf.raw[:nread.value]
        print(f"Read {nread.value} bytes from 0x{addr:x}:")
        for i in range(0, len(data), 16):
            chunk = data[i:i+16]
            hex_s = ' '.join(f'{b:02x}' for b in chunk)
            asc_s = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
            print(f'  0x{addr+i:x}: {hex_s:47s}  {asc_s}')
    finally:
        CloseHandle(h)


if __name__ == "__main__":
    main()
