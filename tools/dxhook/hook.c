/*
 * GameBot DXGI 截图 Hook (Day 1: hello-world)
 *
 * 当前阶段：仅 DllMain 弹 MessageBox 验证 DLL 注入工作
 * 下阶段：加 MinHook + IDXGISwapChain::Present hook
 */
#include <windows.h>
#include <stdio.h>

static void log_to_file(const char *msg) {
    char path[MAX_PATH];
    DWORD n = GetTempPathA(MAX_PATH, path);
    if (n == 0 || n >= MAX_PATH) {
        strcpy(path, "C:\\");
    }
    strcat(path, "gamebot_hook.log");
    FILE *f = fopen(path, "a");
    if (f) {
        SYSTEMTIME st;
        GetLocalTime(&st);
        fprintf(f, "[%02d:%02d:%02d.%03d] PID=%lu %s\n",
                st.wHour, st.wMinute, st.wSecond, st.wMilliseconds,
                GetCurrentProcessId(), msg);
        fclose(f);
    }
}

BOOL WINAPI DllMain(HINSTANCE hInst, DWORD reason, LPVOID reserved) {
    switch (reason) {
        case DLL_PROCESS_ATTACH:
            DisableThreadLibraryCalls(hInst);
            log_to_file("DLL_PROCESS_ATTACH");
            break;
        case DLL_PROCESS_DETACH:
            log_to_file("DLL_PROCESS_DETACH");
            break;
    }
    return TRUE;
}
