"""host_write_lib.py — 给 host_memscan 加 WriteProcessMemory 能力

只暴露 3 个函数：
  - open_write_handle(pid): 打开可写 handle
  - write_at(handle, addr, data): 写字节
  - close_handle(handle)
"""
import ctypes
import ctypes.wintypes as wt

PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

OpenProcess = kernel32.OpenProcess
OpenProcess.restype = wt.HANDLE
OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]

CloseHandle = kernel32.CloseHandle
CloseHandle.restype = wt.BOOL
CloseHandle.argtypes = [wt.HANDLE]

WriteProcessMemory = kernel32.WriteProcessMemory
WriteProcessMemory.restype = wt.BOOL
WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]

VirtualProtectEx = kernel32.VirtualProtectEx
VirtualProtectEx.restype = wt.BOOL
VirtualProtectEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                             wt.DWORD, ctypes.POINTER(wt.DWORD)]

PAGE_READWRITE = 0x04


def open_write_handle(pid: int):
    flags = (PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION
             | PROCESS_QUERY_INFORMATION)
    h = OpenProcess(flags, False, pid)
    if not h:
        err = ctypes.get_last_error()
        raise PermissionError(f"OpenProcess write handle failed err={err}")
    return h


def close_handle(h):
    if h:
        CloseHandle(h)


def write_at(handle, addr: int, data: bytes) -> tuple:
    """写 data 到 addr。返回 (success, bytes_written, err_str)"""
    # 先尝试改为可写
    old_protect = wt.DWORD(0)
    VirtualProtectEx(handle, ctypes.c_void_p(addr), len(data),
                     PAGE_READWRITE, ctypes.byref(old_protect))

    buf = ctypes.c_char_p(data)
    written = ctypes.c_size_t(0)
    ok = WriteProcessMemory(handle, ctypes.c_void_p(addr), buf,
                            len(data), ctypes.byref(written))

    # 还原保护
    if old_protect.value:
        tmp = wt.DWORD(0)
        VirtualProtectEx(handle, ctypes.c_void_p(addr), len(data),
                         old_protect.value, ctypes.byref(tmp))

    if not ok:
        return False, 0, f"err={ctypes.get_last_error()}"
    return True, written.value, ""
