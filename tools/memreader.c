/*
 * memreader — 游戏进程内存扫描器（模拟器本地执行）
 *
 * 设计原则：
 *   1. 单进程，一次 su 执行完毕
 *   2. process_vm_readv() 优先（无文件系统痕迹），失败回退 pread
 *   3. 只扫匿名 rw-p 区域（跳过 .so/.dex 等文件映射）
 *   4. 每次读取间隔 20ms，避免频率检测
 *
 * 编译: docker run --rm -v $(pwd)/tools:/src alpine sh -c \
 *         "apk add gcc musl-dev && cd /src && gcc -static -O2 -o memreader memreader.c"
 *
 * 用法: su 0 /data/local/tmp/memreader <PID> <关键词1> [关键词2] ...
 *
 * 输出:
 *   SCAN_START:pid=<N>,keywords=<N>,regions=<N>
 *   FOUND:<keyword_index>:0x<addr>:<utf8_context>
 *   DONE:found=<N>,regions=<N>,time=<seconds>
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <time.h>
#include <sys/uio.h>

/* ── 常量 ── */
#define MAX_KEYWORDS     8
#define MAX_KEYWORD_LEN  128
#define MAX_REGIONS      4096
#define MAX_FINDINGS     128
#define MAX_REGION_SIZE  (8 * 1024 * 1024)   /* 8 MB */
#define MIN_REGION_SIZE  4096
#define READ_CHUNK       (2 * 1024 * 1024)   /* 2 MB per read */
#define THROTTLE_US      20000               /* 20 ms between region reads */
#define CONTEXT_BYTES    80                  /* 上下文前后各取 N 字节 */

/* ── 数据结构 ── */
struct region {
    unsigned long start;
    unsigned long end;
};

struct finding {
    int kw_idx;
    unsigned long addr;
    char context[256];
};

/* ── 全局 ── */
static unsigned char read_buf[READ_CHUNK];
static struct finding findings[MAX_FINDINGS];
static int n_findings = 0;

/* ── 辅助函数 ── */

/* 判断一行 maps 是否应该扫描 */
static int should_scan(const char *line) {
    /* 必须是 rw-p */
    if (!strstr(line, "rw-p"))
        return 0;

    /* 跳过有文件路径的映射（.so, .dex, .oat, .apk, .jar, .art） */
    if (strstr(line, ".so") || strstr(line, ".dex") ||
        strstr(line, ".oat") || strstr(line, ".apk") ||
        strstr(line, ".jar") || strstr(line, ".art") ||
        strstr(line, ".vdex") || strstr(line, ".odex"))
        return 0;

    /* 跳过特殊区域 */
    if (strstr(line, "[stack") || strstr(line, "[vdso") ||
        strstr(line, "[vectors") || strstr(line, "[vsyscall"))
        return 0;

    return 1;
}

/* 在 buf 中搜索 keyword，记录结果 */
static void search_buffer(const unsigned char *buf, size_t buf_len,
                          const unsigned char *keyword, size_t kw_len,
                          int kw_idx, unsigned long base_addr)
{
    if (buf_len < kw_len) return;

    size_t i;
    for (i = 0; i <= buf_len - kw_len; i++) {
        if (buf[i] != keyword[0]) continue;          /* 首字节快速跳过 */
        if (memcmp(buf + i, keyword, kw_len) != 0) continue;

        if (n_findings >= MAX_FINDINGS) return;

        struct finding *f = &findings[n_findings];
        f->kw_idx = kw_idx;
        f->addr = base_addr + i;

        /* 提取上下文 */
        size_t ctx_s = (i > CONTEXT_BYTES) ? i - CONTEXT_BYTES : 0;
        size_t ctx_e = i + kw_len + CONTEXT_BYTES;
        if (ctx_e > buf_len) ctx_e = buf_len;

        int ci = 0;
        size_t j;
        for (j = ctx_s; j < ctx_e && ci < (int)sizeof(f->context) - 1; j++) {
            unsigned char c = buf[j];
            if (c >= 0x20 || c >= 0x80) {
                f->context[ci++] = (char)c;
            } else if (c == 0x00) {
                if (ci > 0 && f->context[ci - 1] != '|')
                    f->context[ci++] = '|';
            }
        }
        f->context[ci] = '\0';
        n_findings++;
    }
}

/* 用 process_vm_readv 读取，失败则用 pread */
static ssize_t safe_read(int mem_fd, pid_t pid,
                         void *buf, size_t len, unsigned long addr)
{
    /* 优先 process_vm_readv（无文件系统痕迹） */
    struct iovec local  = { buf, len };
    struct iovec remote = { (void *)addr, len };
    ssize_t n = process_vm_readv(pid, &local, 1, &remote, 1, 0);
    if (n > 0) return n;

    /* 回退到 pread */
    if (mem_fd >= 0) {
        n = pread(mem_fd, buf, len, (off_t)addr);
        if (n > 0) return n;
    }

    return -1;
}

/* ── main ── */
int main(int argc, char *argv[])
{
    if (argc < 3) {
        fprintf(stderr, "Usage: %s <PID> <keyword1> [keyword2] ...\n", argv[0]);
        return 1;
    }

    pid_t pid = (pid_t)atoi(argv[1]);
    if (pid <= 0) {
        fprintf(stderr, "Invalid PID: %s\n", argv[1]);
        return 1;
    }

    /* 收集关键词 */
    int n_keywords = argc - 2;
    if (n_keywords > MAX_KEYWORDS) n_keywords = MAX_KEYWORDS;

    const char *keywords[MAX_KEYWORDS];
    size_t kw_lens[MAX_KEYWORDS];
    int ki;
    for (ki = 0; ki < n_keywords; ki++) {
        keywords[ki] = argv[2 + ki];
        kw_lens[ki] = strlen(keywords[ki]);
    }

    /* 打开 /proc/pid/mem 作为回退 */
    char mem_path[64];
    snprintf(mem_path, sizeof(mem_path), "/proc/%d/mem", pid);
    int mem_fd = open(mem_path, O_RDONLY);
    /* mem_fd < 0 也没关系，还有 process_vm_readv */

    /* 解析 /proc/pid/maps */
    char maps_path[64];
    snprintf(maps_path, sizeof(maps_path), "/proc/%d/maps", pid);
    FILE *maps = fopen(maps_path, "r");
    if (!maps) {
        fprintf(stderr, "Cannot open %s: %s\n", maps_path, strerror(errno));
        if (mem_fd >= 0) close(mem_fd);
        return 1;
    }

    struct region regions[MAX_REGIONS];
    int n_regions = 0;
    char line[512];
    while (fgets(line, sizeof(line), maps) && n_regions < MAX_REGIONS) {
        if (!should_scan(line)) continue;

        unsigned long s, e;
        if (sscanf(line, "%lx-%lx", &s, &e) != 2) continue;

        unsigned long sz = e - s;
        if (sz < MIN_REGION_SIZE || sz > MAX_REGION_SIZE) continue;

        regions[n_regions].start = s;
        regions[n_regions].end = e;
        n_regions++;
    }
    fclose(maps);

    /* 开始扫描 */
    struct timespec ts_start, ts_end;
    clock_gettime(CLOCK_MONOTONIC, &ts_start);

    fprintf(stdout, "SCAN_START:pid=%d,keywords=%d,regions=%d\n",
            pid, n_keywords, n_regions);
    fflush(stdout);

    int ri;
    for (ri = 0; ri < n_regions; ri++) {
        unsigned long rstart = regions[ri].start;
        unsigned long rend   = regions[ri].end;

        /* 分块读取 */
        unsigned long offset = rstart;
        while (offset < rend) {
            size_t to_read = rend - offset;
            if (to_read > READ_CHUNK) to_read = READ_CHUNK;

            ssize_t got = safe_read(mem_fd, pid, read_buf, to_read, offset);
            if (got <= 0) break;

            /* 搜索每个关键词 */
            for (ki = 0; ki < n_keywords; ki++) {
                search_buffer(read_buf, (size_t)got,
                              (const unsigned char *)keywords[ki],
                              kw_lens[ki], ki, offset);
            }

            offset += (unsigned long)got;
        }

        /* 节流：每个区域读完后等待 */
        usleep(THROTTLE_US);

        if (n_findings >= MAX_FINDINGS) break;
    }

    clock_gettime(CLOCK_MONOTONIC, &ts_end);
    double elapsed = (ts_end.tv_sec - ts_start.tv_sec)
                   + (ts_end.tv_nsec - ts_start.tv_nsec) / 1e9;

    /* 输出结果 */
    int i;
    for (i = 0; i < n_findings; i++) {
        /* 用 write 避免 printf 的编码问题 */
        char header[128];
        int hlen = snprintf(header, sizeof(header), "FOUND:%d:0x%lx:",
                            findings[i].kw_idx, findings[i].addr);
        write(STDOUT_FILENO, header, hlen);
        write(STDOUT_FILENO, findings[i].context, strlen(findings[i].context));
        write(STDOUT_FILENO, "\n", 1);
    }

    char done_line[128];
    int dlen = snprintf(done_line, sizeof(done_line),
                        "DONE:found=%d,regions=%d,time=%.1f\n",
                        n_findings, n_regions, elapsed);
    write(STDOUT_FILENO, done_line, dlen);

    if (mem_fd >= 0) close(mem_fd);
    return 0;
}
