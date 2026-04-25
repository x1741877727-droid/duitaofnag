/*
 * GameBot LDPlayer 截图 Hook (Day 3: glReadPixels + 共享内存)
 *
 * 工作流程：
 *   1. DllMain ATTACH → spawn worker thread
 *   2. Worker hook opengl32!wglSwapBuffers
 *   3. HookedSwapBuffers 每 N 帧（默认 30，即 2 FPS 抓帧）调一次：
 *        - glGetIntegerv(GL_VIEWPORT) 拿当前 fb 大小
 *        - glReadPixels(GL_RGBA) 读 framebuffer 到本地 buffer
 *        - 写到共享内存 "Local\GameBotCap_<PID>"（带 frame counter + 尺寸 header）
 *   4. Python 端用 mmap 打开同名共享内存，读出 ndarray
 *
 * 共享内存协议（布局）：
 *   offset 0:  uint32 magic = 0x42476843 ('GBhC')
 *   offset 4:  uint32 frame_n      // 单调递增帧号（reader 用来检测新帧）
 *   offset 8:  uint32 width
 *   offset 12: uint32 height
 *   offset 16: uint32 timestamp_ms
 *   offset 20: uint32 stride_bytes
 *   offset 24: uint32 reserved[2]
 *   offset 32: bytes frame[width*height*4]  // RGBA
 *
 * 注意：glReadPixels 默认从 GL_BACK 读，即将提交显示的画面。
 *       在调原版 wglSwapBuffers 之前抓，这样画面跟实际显示一致。
 */
#include <windows.h>
#include <stdio.h>
#include <stdint.h>
#include "MinHook.h"

// ───── OpenGL 常量 + 函数指针类型（不引 GL.h 避免 mingw GL 头版本麻烦）─
typedef unsigned int GLenum;
typedef int GLint;
typedef int GLsizei;
typedef unsigned int GLuint;

#define GL_VIEWPORT      0x0BA2
#define GL_RGBA          0x1908
#define GL_BGRA          0x80E1
#define GL_UNSIGNED_BYTE 0x1401
#define GL_BACK          0x0405

typedef void (WINAPI *PFN_glGetIntegerv)(GLenum, GLint*);
typedef void (WINAPI *PFN_glReadPixels)(GLint, GLint, GLsizei, GLsizei,
                                         GLenum, GLenum, void*);
typedef void (WINAPI *PFN_glReadBuffer)(GLenum);

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

// ───── 共享内存 ─────────────────────────────────────────────────
#define SHM_MAGIC 0x42476843u  // 'GBhC'
#define SHM_HEADER_BYTES 32
#define SHM_MAX_W 2560
#define SHM_MAX_H 1440
#define SHM_FRAME_BYTES (SHM_MAX_W * SHM_MAX_H * 4)
#define SHM_TOTAL_BYTES (SHM_HEADER_BYTES + SHM_FRAME_BYTES)

#pragma pack(push, 4)
typedef struct {
    uint32_t magic;
    uint32_t frame_n;
    uint32_t width;
    uint32_t height;
    uint32_t timestamp_ms;
    uint32_t stride_bytes;
    uint32_t reserved[2];
} ShmHeader;
#pragma pack(pop)

static HANDLE g_shm_handle = NULL;
static uint8_t *g_shm_view = NULL;

static int init_shm(void) {
    char name[64];
    snprintf(name, sizeof(name), "Local\\GameBotCap_%lu", GetCurrentProcessId());
    g_shm_handle = CreateFileMappingA(
            INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE,
            0, SHM_TOTAL_BYTES, name);
    if (!g_shm_handle) {
        log_to_file("CreateFileMapping(%s) 失败 err=%lu", name, GetLastError());
        return 0;
    }
    g_shm_view = (uint8_t*)MapViewOfFile(g_shm_handle,
            FILE_MAP_WRITE, 0, 0, SHM_TOTAL_BYTES);
    if (!g_shm_view) {
        log_to_file("MapViewOfFile 失败 err=%lu", GetLastError());
        CloseHandle(g_shm_handle);
        g_shm_handle = NULL;
        return 0;
    }
    // 写 magic 让 reader 校验
    ShmHeader *hdr = (ShmHeader*)g_shm_view;
    hdr->magic = SHM_MAGIC;
    hdr->frame_n = 0;
    hdr->width = 0;
    hdr->height = 0;
    log_to_file("共享内存就绪 name=%s size=%u bytes", name, (unsigned)SHM_TOTAL_BYTES);
    return 1;
}

// ───── GL 函数指针 ──────────────────────────────────────────────
static PFN_glGetIntegerv p_glGetIntegerv = NULL;
static PFN_glReadPixels p_glReadPixels = NULL;
static PFN_glReadBuffer p_glReadBuffer = NULL;

static int load_gl_funcs(void) {
    HMODULE hGL = GetModuleHandleA("opengl32.dll");
    if (!hGL) return 0;
    p_glGetIntegerv = (PFN_glGetIntegerv)GetProcAddress(hGL, "glGetIntegerv");
    p_glReadPixels = (PFN_glReadPixels)GetProcAddress(hGL, "glReadPixels");
    p_glReadBuffer = (PFN_glReadBuffer)GetProcAddress(hGL, "glReadBuffer");
    return p_glGetIntegerv && p_glReadPixels;
}

// ───── wglSwapBuffers hook ──────────────────────────────────────
typedef BOOL (WINAPI *SwapBuffersFn)(HDC hdc);

static SwapBuffersFn oSwapBuffers = NULL;
static volatile LONG g_swap_count = 0;
static volatile LONG g_capture_count = 0;

// 每 N 次 swap 抓一帧（30 = ~2 FPS @ 60Hz）
static const LONG CAPTURE_EVERY = 30;

static BOOL WINAPI HookedSwapBuffers(HDC hdc) {
    LONG n = InterlockedIncrement(&g_swap_count);

    // 每 CAPTURE_EVERY 次 swap 抓一帧
    if (n % CAPTURE_EVERY == 0 && p_glReadPixels && g_shm_view) {
        GLint vp[4] = {0, 0, 0, 0};
        p_glGetIntegerv(GL_VIEWPORT, vp);
        int w = vp[2], h = vp[3];
        if (w > 0 && h > 0 && w <= SHM_MAX_W && h <= SHM_MAX_H) {
            // 让 reader 不要在写中读：先把 frame_n 反转标记 in-progress
            ShmHeader *hdr = (ShmHeader*)g_shm_view;
            uint32_t old_n = hdr->frame_n;
            hdr->frame_n = old_n | 0x80000000u;  // 设最高位 = 写中

            // 读 framebuffer
            if (p_glReadBuffer) p_glReadBuffer(GL_BACK);
            uint8_t *frame_data = g_shm_view + SHM_HEADER_BYTES;
            p_glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, frame_data);

            // 完整写入后更新 header（清最高位 + frame_n+1）
            hdr->width = w;
            hdr->height = h;
            hdr->stride_bytes = w * 4;
            hdr->timestamp_ms = GetTickCount();
            hdr->frame_n = (old_n + 1) & 0x7FFFFFFFu;

            LONG c = InterlockedIncrement(&g_capture_count);
            if (c == 1 || c % 30 == 0) {
                log_to_file("捕帧 #%ld %dx%d (swap_n=%ld)", c, w, h, n);
            }
        }
    }

    return oSwapBuffers(hdc);
}

// ───── Worker thread ────────────────────────────────────────────
static DWORD WINAPI hook_init_thread(LPVOID arg) {
    (void)arg;
    log_to_file("hook_init_thread 启动");
    Sleep(1000);  // 等宿主初始化 OpenGL

    HMODULE hOpenGL = GetModuleHandleA("opengl32.dll");
    if (!hOpenGL) {
        log_to_file("opengl32.dll 没加载");
        return 1;
    }
    void *target = (void*)GetProcAddress(hOpenGL, "wglSwapBuffers");
    if (!target) {
        log_to_file("opengl32!wglSwapBuffers GetProcAddress 失败");
        return 2;
    }
    log_to_file("opengl32!wglSwapBuffers @ %p", target);

    if (!load_gl_funcs()) {
        log_to_file("load GL funcs 失败");
        return 3;
    }
    log_to_file("GL funcs: glGetIntegerv=%p glReadPixels=%p",
                p_glGetIntegerv, p_glReadPixels);

    if (!init_shm()) return 4;

    if (MH_Initialize() != MH_OK) { log_to_file("MH_Initialize 失败"); return 5; }
    if (MH_CreateHook(target, HookedSwapBuffers, (LPVOID*)&oSwapBuffers) != MH_OK) {
        log_to_file("MH_CreateHook 失败"); return 6;
    }
    if (MH_EnableHook(target) != MH_OK) { log_to_file("MH_EnableHook 失败"); return 7; }

    log_to_file("hook 全部就绪，等捕帧");
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
            if (g_shm_view) UnmapViewOfFile(g_shm_view);
            if (g_shm_handle) CloseHandle(g_shm_handle);
            break;
    }
    return TRUE;
}
