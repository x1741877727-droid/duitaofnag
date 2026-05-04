/*
 * hook_v8.c  — Phase 1 探查 (修正版): hook opengl32 端
 *
 * 之前误判: LDPlayer 内部走 libOpenglRender2 的 GLES1 translator export.
 * 实测: GLES1@gles1@translator 完全无调用; LDPlayer 直接用 desktop OpenGL
 *       (opengl32!glBindTexture / glTexImage2D / glTexSubImage2D / wglSwapBuffers).
 *
 * v8 重写: 在 opengl32 端 hook 这几个函数, 维护
 *   - 每个 texture id 的最近 (w, h, internalformat)
 *   - 每个 texture id 的 TexImage2D / TexSubImage2D 调用计数
 *   - 当前 thread bound TEXTURE_2D
 *   - swap 计数, 每秒由 swap hook dump 摘要
 *
 * 目的: 找出 size=960x540 RGBA8 那个 texture 的 GL id,
 *       看每秒被 TexImage2D 重新分配次数 + TexSubImage2D 更新次数,
 *       确认它就是 Android FB 目标 texture.
 */
#include <windows.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include "MinHook.h"

typedef unsigned int GLenum;
typedef int GLint;
typedef int GLsizei;
typedef unsigned int GLuint;

#define GL_TEXTURE_2D 0x0DE1

typedef void (WINAPI *PFN_glBindTexture)(GLenum, GLuint);
typedef void (WINAPI *PFN_glTexImage2D)(GLenum, GLint, GLint, GLsizei, GLsizei,
        GLint, GLenum, GLenum, const void *);
typedef void (WINAPI *PFN_glTexSubImage2D)(GLenum, GLint, GLint, GLint,
        GLsizei, GLsizei, GLenum, GLenum, const void *);
typedef BOOL (WINAPI *PFN_wglSwapBuffers)(HDC);

/* ───── log ───────────────────────────────── */
static void log_to_file(const char *fmt, ...) {
    char path[MAX_PATH];
    DWORD n = GetTempPathA(MAX_PATH, path);
    if (n == 0 || n >= MAX_PATH) strcpy(path, "C:\\");
    strcat(path, "gamebot_hook_v8.log");
    FILE *f = fopen(path, "a");
    if (!f) return;
    SYSTEMTIME st; GetLocalTime(&st);
    fprintf(f, "[%02d:%02d:%02d.%03d] PID=%lu ",
            st.wHour, st.wMinute, st.wSecond, st.wMilliseconds, GetCurrentProcessId());
    va_list ap; va_start(ap, fmt);
    vfprintf(f, fmt, ap); va_end(ap);
    fputc('\n', f); fclose(f);
}

/* ───── 全局 state ──────────────────────────── */
static CRITICAL_SECTION g_cs;

#define MAX_TEX 1024
typedef struct {
    int used;
    GLuint id;
    GLsizei w, h;
    GLint ifmt;
    uint32_t teximg_n;        /* total */
    uint32_t texsub_n;        /* total */
    uint32_t teximg_n_window; /* current 1s window */
    uint32_t texsub_n_window;
} TexInfo;
static TexInfo g_tex[MAX_TEX];

static __thread GLuint t_current_tex2d = 0;

static volatile LONG g_swap_total = 0;
static volatile LONG g_swap_in_window = 0;
static DWORD g_window_start_tick = 0;

/* ───── helpers ─────────────────────────────── */
static TexInfo *tex_find_or_alloc(GLuint id) {
    if (id == 0) return NULL;
    int free_idx = -1;
    for (int i = 0; i < MAX_TEX; i++) {
        if (!g_tex[i].used) { if (free_idx < 0) free_idx = i; continue; }
        if (g_tex[i].id == id) return &g_tex[i];
    }
    if (free_idx < 0) return NULL;
    TexInfo *t = &g_tex[free_idx];
    memset(t, 0, sizeof(*t));
    t->used = 1; t->id = id;
    return t;
}

/* ───── originals ───────────────────────────── */
static PFN_glBindTexture o_BindTexture;
static PFN_glTexImage2D  o_TexImage2D;
static PFN_glTexSubImage2D o_TexSubImage2D;
static PFN_wglSwapBuffers o_wglSwapBuffers;

/* ───── hooks ───────────────────────────────── */
static void WINAPI HBindTexture(GLenum tgt, GLuint id) {
    if (tgt == GL_TEXTURE_2D) t_current_tex2d = id;
    o_BindTexture(tgt, id);
}

static void WINAPI HTexImage2D(GLenum t, GLint l, GLint ifmt, GLsizei w, GLsizei h,
        GLint b, GLenum f, GLenum tp, const void *p) {
    if (t == GL_TEXTURE_2D && l == 0 && t_current_tex2d != 0) {
        EnterCriticalSection(&g_cs);
        TexInfo *ti = tex_find_or_alloc(t_current_tex2d);
        if (ti) {
            ti->w = w; ti->h = h; ti->ifmt = ifmt;
            ti->teximg_n++; ti->teximg_n_window++;
        }
        LeaveCriticalSection(&g_cs);
    }
    o_TexImage2D(t, l, ifmt, w, h, b, f, tp, p);
}

static void WINAPI HTexSubImage2D(GLenum t, GLint l, GLint xo, GLint yo,
        GLsizei w, GLsizei h, GLenum f, GLenum tp, const void *p) {
    if (t == GL_TEXTURE_2D && l == 0 && t_current_tex2d != 0) {
        EnterCriticalSection(&g_cs);
        TexInfo *ti = tex_find_or_alloc(t_current_tex2d);
        if (ti) { ti->texsub_n++; ti->texsub_n_window++; }
        LeaveCriticalSection(&g_cs);
    }
    o_TexSubImage2D(t, l, xo, yo, w, h, f, tp, p);
}

static void dump_summary(DWORD elapsed_ms) {
    EnterCriticalSection(&g_cs);
    LONG swaps = InterlockedExchange(&g_swap_in_window, 0);
    log_to_file("=== %ums | swaps=%ld total_swap=%ld ===",
            elapsed_ms, swaps, g_swap_total);
    /* 列出所有有 (teximg_n_window + texsub_n_window > 0) OR (w >= 320) 的 texture */
    int idx[MAX_TEX]; int n = 0;
    for (int i = 0; i < MAX_TEX; i++) {
        if (!g_tex[i].used) continue;
        TexInfo *t = &g_tex[i];
        if (t->teximg_n_window > 0 || t->texsub_n_window > 0 || (t->w >= 320 && t->h >= 240)) {
            idx[n++] = i;
        }
    }
    /* sort by (texsub_n_window + teximg_n_window) desc */
    for (int i = 0; i < n - 1; i++)
        for (int j = 0; j < n - 1 - i; j++) {
            uint32_t a = g_tex[idx[j]].teximg_n_window + g_tex[idx[j]].texsub_n_window;
            uint32_t b = g_tex[idx[j+1]].teximg_n_window + g_tex[idx[j+1]].texsub_n_window;
            if (a < b) { int t = idx[j]; idx[j] = idx[j+1]; idx[j+1] = t; }
        }
    int dn = n < 30 ? n : 30;
    for (int i = 0; i < dn; i++) {
        TexInfo *t = &g_tex[idx[i]];
        log_to_file("  tex=%u %dx%d ifmt=0x%04X teximg=%u(+%u) texsub=%u(+%u)",
                t->id, t->w, t->h, t->ifmt, t->teximg_n, t->teximg_n_window, t->texsub_n, t->texsub_n_window);
        t->teximg_n_window = 0; t->texsub_n_window = 0;
    }
    /* clear windows for non-listed too */
    for (int i = 0; i < MAX_TEX; i++) {
        if (g_tex[i].used) { g_tex[i].teximg_n_window = 0; g_tex[i].texsub_n_window = 0; }
    }
    LeaveCriticalSection(&g_cs);
}

static BOOL WINAPI HSwapBuffers(HDC hdc) {
    InterlockedIncrement(&g_swap_total);
    InterlockedIncrement(&g_swap_in_window);
    DWORD now = GetTickCount();
    if (g_window_start_tick == 0) g_window_start_tick = now;
    if (now - g_window_start_tick >= 1000) {
        DWORD el = now - g_window_start_tick;
        g_window_start_tick = now;
        dump_summary(el);
    }
    return o_wglSwapBuffers(hdc);
}

/* ───── init thread ─────────────────────────── */
static DWORD WINAPI init_thread(LPVOID arg) {
    (void)arg;
    log_to_file("v8 init_thread (rev2: hook opengl32 端)");
    Sleep(2000);

    HMODULE hGL = GetModuleHandleA("opengl32.dll");
    if (!hGL) { log_to_file("opengl32.dll not loaded"); return 2; }
    log_to_file("opengl32 @ %p", hGL);

    InitializeCriticalSection(&g_cs);
    memset(g_tex, 0, sizeof(g_tex));

    void *p_BindTex = (void*)GetProcAddress(hGL, "glBindTexture");
    void *p_TexImg  = (void*)GetProcAddress(hGL, "glTexImage2D");
    void *p_TexSub  = (void*)GetProcAddress(hGL, "glTexSubImage2D");
    void *p_wglSwap = (void*)GetProcAddress(hGL, "wglSwapBuffers");

    log_to_file("BindTex=%p TexImg=%p TexSub=%p wglSwap=%p",
            p_BindTex, p_TexImg, p_TexSub, p_wglSwap);

    if (MH_Initialize() != MH_OK) { log_to_file("MH_Init fail"); return 3; }

#define TRY_HOOK(target, hook, orig, name) do { \
    if (target) { \
        if (MH_CreateHook(target, hook, (LPVOID*)&orig) == MH_OK && MH_EnableHook(target) == MH_OK) \
            log_to_file("hook " name " OK"); \
        else log_to_file("hook " name " FAIL"); \
    } \
} while(0)

    TRY_HOOK(p_BindTex, HBindTexture,   o_BindTexture,    "BindTexture");
    TRY_HOOK(p_TexImg,  HTexImage2D,    o_TexImage2D,     "TexImage2D");
    TRY_HOOK(p_TexSub,  HTexSubImage2D, o_TexSubImage2D,  "TexSubImage2D");
    TRY_HOOK(p_wglSwap, HSwapBuffers,   o_wglSwapBuffers, "wglSwapBuffers");

    log_to_file("v8 ready");
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE hi, DWORD r, LPVOID rs) {
    (void)rs;
    if (r == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hi);
        log_to_file("v8 ATTACH");
        HANDLE h = CreateThread(NULL, 0, init_thread, NULL, 0, NULL);
        if (h) CloseHandle(h);
    }
    return TRUE;
}
