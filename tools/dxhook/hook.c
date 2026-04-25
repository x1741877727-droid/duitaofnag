/*
 * GameBot LDPlayer 截图 Hook (Day 2 修正版: wglSwapBuffers)
 *
 * 重要发现：LDPlayer 9 dnplayer.exe 用 OpenGL 渲染（ldopengl32x.dll → opengl32.dll），
 * 不是 DirectX 11。所以 hook 目标是 opengl32.dll!wglSwapBuffers，不是 IDXGISwapChain::Present。
 *
 * 流程（Day 2）：
 *   1. DllMain ATTACH → spawn worker thread
 *   2. Worker GetProcAddress(opengl32.dll, "wglSwapBuffers")
 *   3. MinHook 拦截 → HookedSwapBuffers
 *   4. HookedSwapBuffers 调原版 + 写日志（Day 3 加 glReadPixels 抓帧）
 */
#include <windows.h>
#include <stdio.h>
#include <stdint.h>
#include "MinHook.h"

// ───── 日志 ─────────────────────────────────────────────────────
static void log_to_file(const char *fmt, ...) {
    char path[MAX_PATH];
    DWORD n = GetTempPathA(MAX_PATH, path);
    if (n == 0 || n >= MAX_PATH) strcpy(path, "C:\\");
    strcat(path, "gamebot_hook.log");
    FILE *f = fopen(path, "a");
    if (!f) return;
    SYSTEMTIME st;
    GetLocalTime(&st);
    fprintf(f, "[%02d:%02d:%02d.%03d] PID=%lu ",
            st.wHour, st.wMinute, st.wSecond, st.wMilliseconds,
            GetCurrentProcessId());
    va_list ap;
    va_start(ap, fmt);
    vfprintf(f, fmt, ap);
    va_end(ap);
    fputc('\n', f);
    fclose(f);
}

// ───── wglSwapBuffers hook ──────────────────────────────────────
typedef BOOL (WINAPI *SwapBuffersFn)(HDC hdc);

static SwapBuffersFn oSwapBuffers = NULL;
static volatile LONG g_swap_count = 0;

static BOOL WINAPI HookedSwapBuffers(HDC hdc) {
    LONG n = InterlockedIncrement(&g_swap_count);
    if (n == 1 || n % 60 == 0) {
        log_to_file("HookedSwapBuffers 第 %ld 次 hdc=%p", n, hdc);
    }
    return oSwapBuffers(hdc);
}

// ───── Worker thread ────────────────────────────────────────────
static DWORD WINAPI hook_init_thread(LPVOID arg) {
    (void)arg;
    log_to_file("hook_init_thread 启动");

    // 等 1 秒让宿主完成 OpenGL 初始化
    Sleep(1000);

    // 找 wglSwapBuffers — 优先 opengl32.dll（系统）
    HMODULE hOpenGL = GetModuleHandleA("opengl32.dll");
    if (!hOpenGL) {
        log_to_file("opengl32.dll 没加载（dnplayer 可能尚未初始化 GL）");
        return 1;
    }

    void *target = (void*)GetProcAddress(hOpenGL, "wglSwapBuffers");
    if (!target) {
        log_to_file("opengl32!wglSwapBuffers GetProcAddress 失败");
        return 2;
    }
    log_to_file("opengl32!wglSwapBuffers @ %p", target);

    if (MH_Initialize() != MH_OK) {
        log_to_file("MH_Initialize 失败");
        return 3;
    }

    if (MH_CreateHook(target, HookedSwapBuffers, (LPVOID*)&oSwapBuffers) != MH_OK) {
        log_to_file("MH_CreateHook 失败");
        return 4;
    }

    if (MH_EnableHook(target) != MH_OK) {
        log_to_file("MH_EnableHook 失败");
        return 5;
    }

    log_to_file("wglSwapBuffers hook 启用成功，等待宿主 SwapBuffers 调用");
    return 0;
}

// ───── DllMain ──────────────────────────────────────────────────
BOOL WINAPI DllMain(HINSTANCE hInst, DWORD reason, LPVOID reserved) {
    (void)reserved;
    switch (reason) {
        case DLL_PROCESS_ATTACH: {
            DisableThreadLibraryCalls(hInst);
            log_to_file("DLL_PROCESS_ATTACH");
            HANDLE h = CreateThread(NULL, 0, hook_init_thread, NULL, 0, NULL);
            if (h) CloseHandle(h);
            break;
        }
        case DLL_PROCESS_DETACH:
            log_to_file("DLL_PROCESS_DETACH");
            MH_DisableHook(MH_ALL_HOOKS);
            MH_Uninitialize();
            break;
    }
    return TRUE;
}
