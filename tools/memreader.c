/*
 * memreader v6 — 游戏进程内存扫描器
 *
 * 策略：不限区域数量，限制总时间（12秒软超时 + 15秒硬杀）
 * 优先扫小区域（<1MB），游戏数据结构通常在小区域里
 *
 * 安全保护：
 *   1. alarm(15) — 15秒内核强杀，绝对不会卡
 *   2. 12秒软超时 — 主动退出并输出已找到的结果
 *   3. 单区域最大 4MB
 *   4. 5ms 节流
 *   5. 找到 MAX_FINDINGS 个后提前停止
 *
 * 用法: su 0 /data/local/tmp/memreader <PID> <关键词1> [关键词2] ...
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <time.h>
#include <signal.h>
#include <sys/uio.h>

/* ── 限制 ── */
#define MAX_REGIONS      4096                /* 收集上限（排序用） */
#define MAX_FINDINGS     10
#define MAX_KEYWORDS     8
#define MAX_REGION_SIZE  (4 * 1024 * 1024)
#define MIN_REGION_SIZE  4096
#define READ_CHUNK       (512 * 1024)
#define THROTTLE_US      5000                /* 5ms */
#define HARD_TIMEOUT     15                  /* 硬杀 */
#define SOFT_TIMEOUT     12                  /* 软超时 */
#define CONTEXT_BYTES    80

struct region {
    unsigned long start;
    unsigned long end;
    unsigned long size;
};

struct finding {
    int kw_idx;
    unsigned long addr;
    char context[256];
};

static unsigned char read_buf[READ_CHUNK];
static struct finding findings[MAX_FINDINGS];
static int n_findings = 0;
static struct timespec ts_start;

static void timeout_handler(int sig) {
    (void)sig;
    const char msg[] = "TIMEOUT:killed\n";
    write(STDOUT_FILENO, msg, sizeof(msg) - 1);
    _exit(1);
}

static double elapsed_sec(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (now.tv_sec - ts_start.tv_sec)
         + (now.tv_nsec - ts_start.tv_nsec) / 1e9;
}

static int should_scan(const char *line) {
    if (!strstr(line, "rw-p")) return 0;
    /* 只扫 libc_malloc 堆区域 — 游戏数据结构在这里 */
    /* 之前分析：只有 ~35 个区域，总共 ~142MB，几秒扫完 */
    if (strstr(line, "[anon:libc_malloc]")) return 1;
    return 0;
}

static void search_buffer(const unsigned char *buf, size_t buf_len,
                          const unsigned char *keyword, size_t kw_len,
                          int kw_idx, unsigned long base_addr)
{
    if (buf_len < kw_len) return;
    size_t i;
    for (i = 0; i <= buf_len - kw_len; i++) {
        if (buf[i] != keyword[0]) continue;
        if (memcmp(buf + i, keyword, kw_len) != 0) continue;
        if (n_findings >= MAX_FINDINGS) return;

        struct finding *f = &findings[n_findings];
        f->kw_idx = kw_idx;
        f->addr = base_addr + i;

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

static ssize_t safe_read(int mem_fd, pid_t pid,
                         void *buf, size_t len, unsigned long addr)
{
    (void)pid;  /* 不用 process_vm_readv，避免竞态导致游戏崩溃 */
    if (mem_fd >= 0) {
        ssize_t n = pread(mem_fd, buf, len, (off_t)addr);
        if (n > 0) return n;
    }
    return -1;
}

/* 按区域大小排序（小的优先） */
static int cmp_region(const void *a, const void *b) {
    const struct region *ra = (const struct region *)a;
    const struct region *rb = (const struct region *)b;
    if (ra->size < rb->size) return -1;
    if (ra->size > rb->size) return 1;
    return 0;
}

int main(int argc, char *argv[])
{
    if (argc < 3) {
        fprintf(stderr, "Usage: %s <PID> <keyword1> [keyword2] ...\n", argv[0]);
        return 1;
    }

    signal(SIGALRM, timeout_handler);
    alarm(HARD_TIMEOUT);
    clock_gettime(CLOCK_MONOTONIC, &ts_start);

    pid_t pid = (pid_t)atoi(argv[1]);
    if (pid <= 0) { fprintf(stderr, "Invalid PID\n"); return 1; }

    int n_keywords = argc - 2;
    if (n_keywords > MAX_KEYWORDS) n_keywords = MAX_KEYWORDS;
    const char *keywords[MAX_KEYWORDS];
    size_t kw_lens[MAX_KEYWORDS];
    int ki;
    for (ki = 0; ki < n_keywords; ki++) {
        keywords[ki] = argv[2 + ki];
        kw_lens[ki] = strlen(keywords[ki]);
    }

    char mem_path[64];
    snprintf(mem_path, sizeof(mem_path), "/proc/%d/mem", pid);
    int mem_fd = open(mem_path, O_RDONLY);

    char maps_path[64];
    snprintf(maps_path, sizeof(maps_path), "/proc/%d/maps", pid);
    FILE *maps = fopen(maps_path, "r");
    if (!maps) {
        fprintf(stderr, "Cannot open %s\n", maps_path);
        if (mem_fd >= 0) close(mem_fd);
        return 1;
    }

    /* 收集所有匿名 rw-p 区域 */
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
        regions[n_regions].size = sz;
        n_regions++;
    }
    fclose(maps);

    /* 按大小排序：小区域优先（游戏数据结构通常在小区域） */
    qsort(regions, n_regions, sizeof(struct region), cmp_region);

    char start_line[128];
    int slen = snprintf(start_line, sizeof(start_line),
                        "SCAN:pid=%d,kw=%d,regions=%d,timeout=%ds\n",
                        pid, n_keywords, n_regions, SOFT_TIMEOUT);
    write(STDOUT_FILENO, start_line, slen);

    /* 扫描（时间到了就停） */
    int ri;
    int timed_out = 0;
    for (ri = 0; ri < n_regions; ri++) {
        if (elapsed_sec() > SOFT_TIMEOUT) {
            timed_out = 1;
            break;
        }

        unsigned long rstart = regions[ri].start;
        unsigned long rend   = regions[ri].end;
        unsigned long offset = rstart;

        while (offset < rend) {
            size_t to_read = rend - offset;
            if (to_read > READ_CHUNK) to_read = READ_CHUNK;
            ssize_t got = safe_read(mem_fd, pid, read_buf, to_read, offset);
            if (got <= 0) break;
            for (ki = 0; ki < n_keywords; ki++) {
                search_buffer(read_buf, (size_t)got,
                              (const unsigned char *)keywords[ki],
                              kw_lens[ki], ki, offset);
            }
            offset += (unsigned long)got;
        }

        if (THROTTLE_US > 0) usleep(THROTTLE_US);
        if (n_findings >= MAX_FINDINGS) break;
    }

    double total = elapsed_sec();

    /* 输出结果 */
    int i;
    for (i = 0; i < n_findings; i++) {
        char header[128];
        int hlen = snprintf(header, sizeof(header), "FOUND:%d:0x%lx:",
                            findings[i].kw_idx, findings[i].addr);
        write(STDOUT_FILENO, header, hlen);
        write(STDOUT_FILENO, findings[i].context, strlen(findings[i].context));
        write(STDOUT_FILENO, "\n", 1);
    }

    char done_line[128];
    int dlen = snprintf(done_line, sizeof(done_line),
                        "DONE:found=%d,scanned=%d/%d,time=%.1f%s\n",
                        n_findings, ri, n_regions, total,
                        timed_out ? ",timeout" : "");
    write(STDOUT_FILENO, done_line, dlen);

    if (mem_fd >= 0) close(mem_fd);
    alarm(0);
    return 0;
}
