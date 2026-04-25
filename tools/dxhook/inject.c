/*
 * 32-bit DLL 注入器
 * 用法: inject.exe <PID> <DLL绝对路径>
 *
 * 走 OpenProcess + VirtualAllocEx + WriteProcessMemory +
 *    CreateRemoteThread(LoadLibraryA, dll_path)
 *
 * 必须用 32-bit 编译以注入 32-bit 目标（dnplayer.exe = i386）。
 */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: inject.exe <PID> <DLL_path>\n");
        return 1;
    }
    DWORD pid = (DWORD)strtoul(argv[1], NULL, 0);
    const char *dll_path = argv[2];
    size_t dll_path_len = strlen(dll_path) + 1;

    if (GetFileAttributesA(dll_path) == INVALID_FILE_ATTRIBUTES) {
        fprintf(stderr, "DLL 不存在: %s\n", dll_path);
        return 2;
    }

    HANDLE hProc = OpenProcess(
        PROCESS_CREATE_THREAD | PROCESS_VM_OPERATION |
        PROCESS_VM_WRITE | PROCESS_VM_READ | PROCESS_QUERY_INFORMATION,
        FALSE, pid);
    if (!hProc) {
        fprintf(stderr, "OpenProcess(%lu) 失败 err=%lu\n", pid, GetLastError());
        return 3;
    }

    LPVOID remoteAddr = VirtualAllocEx(hProc, NULL, dll_path_len,
                                        MEM_COMMIT, PAGE_READWRITE);
    if (!remoteAddr) {
        fprintf(stderr, "VirtualAllocEx 失败 err=%lu\n", GetLastError());
        CloseHandle(hProc);
        return 4;
    }

    SIZE_T written = 0;
    if (!WriteProcessMemory(hProc, remoteAddr, dll_path,
                             dll_path_len, &written)) {
        fprintf(stderr, "WriteProcessMemory 失败 err=%lu\n", GetLastError());
        VirtualFreeEx(hProc, remoteAddr, 0, MEM_RELEASE);
        CloseHandle(hProc);
        return 5;
    }

    HMODULE hKernel = GetModuleHandleA("kernel32.dll");
    LPTHREAD_START_ROUTINE pLoadLib =
        (LPTHREAD_START_ROUTINE)GetProcAddress(hKernel, "LoadLibraryA");

    HANDLE hThread = CreateRemoteThread(hProc, NULL, 0,
                                          pLoadLib, remoteAddr, 0, NULL);
    if (!hThread) {
        fprintf(stderr, "CreateRemoteThread 失败 err=%lu\n", GetLastError());
        VirtualFreeEx(hProc, remoteAddr, 0, MEM_RELEASE);
        CloseHandle(hProc);
        return 6;
    }

    WaitForSingleObject(hThread, 5000);
    DWORD exitCode = 0;
    GetExitCodeThread(hThread, &exitCode);
    printf("LoadLibraryA 返回 HMODULE=0x%08lX (PID=%lu)\n", exitCode, pid);

    CloseHandle(hThread);
    VirtualFreeEx(hProc, remoteAddr, 0, MEM_RELEASE);
    CloseHandle(hProc);
    return exitCode == 0 ? 7 : 0;
}
