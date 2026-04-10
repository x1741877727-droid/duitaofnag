/* memscan - 搜索 /proc/pid/mem 中的字节模式, 支持 64-bit 地址 */
/* 用法: memscan <pid> <search|write> [args...] */
/* 编译: musl-gcc -static -O2 -o memscan memscan.c */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>

/* 解析 /proc/pid/maps, 对每个 rw-p 段搜索 pattern */
int do_search(int pid, const char *pattern, int pattern_len) {
    char maps_path[64], mem_path[64];
    snprintf(maps_path, sizeof(maps_path), "/proc/%d/maps", pid);
    snprintf(mem_path, sizeof(mem_path), "/proc/%d/mem", pid);

    FILE *maps = fopen(maps_path, "r");
    if (!maps) { perror("fopen maps"); return 1; }

    int memfd = open(mem_path, O_RDONLY);
    if (memfd < 0) { perror("open mem"); return 1; }

    char line[512];
    unsigned char buf[65536];
    int total_hits = 0;

    while (fgets(line, sizeof(line), maps)) {
        if (!strstr(line, "rw-p")) continue;

        unsigned long long start, end;
        if (sscanf(line, "%llx-%llx", &start, &end) != 2) continue;

        unsigned long long size = end - start;
        if (size < 4096 || size > 0x10000000ULL) continue;

        /* 逐块读取并搜索 */
        unsigned long long offset = 0;
        while (offset < size) {
            unsigned long long chunk = size - offset;
            if (chunk > sizeof(buf)) chunk = sizeof(buf);

            ssize_t n = pread(memfd, buf, (size_t)chunk, start + offset);
            if (n <= 0) break;

            /* 在 buf 中搜索 pattern */
            for (ssize_t i = 0; i <= n - pattern_len; i++) {
                if (memcmp(buf + i, pattern, pattern_len) == 0) {
                    unsigned long long addr = start + offset + i;
                    printf("FOUND 0x%llx in 0x%llx-0x%llx\n", addr, start, end);

                    /* 打印上下文 */
                    long long ctx_start = (long long)(start + offset + i) - 32;
                    if (ctx_start < (long long)start) ctx_start = start;
                    unsigned char ctx[256];
                    ssize_t cn = pread(memfd, ctx, 256, ctx_start);
                    if (cn > 0) {
                        /* 打印可见字符 */
                        printf("  CTX: ");
                        for (ssize_t j = 0; j < cn && j < 200; j++) {
                            if (ctx[j] >= 32 && ctx[j] < 127)
                                putchar(ctx[j]);
                            else if (ctx[j] == 0)
                                putchar('|');
                            else
                                putchar('.');
                        }
                        printf("\n");
                    }
                    total_hits++;
                    if (total_hits > 100) goto done;
                }
            }
            offset += n;
        }
    }
done:
    printf("TOTAL: %d hits\n", total_hits);
    close(memfd);
    fclose(maps);
    return 0;
}

/* 在指定地址写入字节 */
int do_write(int pid, unsigned long long addr, unsigned char *data, int len) {
    char mem_path[64];
    snprintf(mem_path, sizeof(mem_path), "/proc/%d/mem", pid);

    int memfd = open(mem_path, O_WRONLY);
    if (memfd < 0) { perror("open mem for write"); return 1; }

    ssize_t n = pwrite(memfd, data, len, addr);
    if (n != len) {
        perror("pwrite");
        close(memfd);
        return 1;
    }

    printf("WROTE %d bytes at 0x%llx\n", len, addr);
    close(memfd);
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "Usage:\n");
        fprintf(stderr, "  memscan <pid> search <string>\n");
        fprintf(stderr, "  memscan <pid> write <hex_addr> <hex_bytes>\n");
        return 1;
    }

    int pid = atoi(argv[1]);
    const char *cmd = argv[2];

    if (strcmp(cmd, "search") == 0 && argc >= 4) {
        return do_search(pid, argv[3], strlen(argv[3]));
    }
    else if (strcmp(cmd, "write") == 0 && argc >= 5) {
        unsigned long long addr;
        sscanf(argv[3], "%llx", &addr);
        /* 解析 hex bytes */
        const char *hex = argv[4];
        int len = strlen(hex) / 2;
        unsigned char *data = malloc(len);
        for (int i = 0; i < len; i++) {
            unsigned int byte;
            sscanf(hex + i*2, "%02x", &byte);
            data[i] = (unsigned char)byte;
        }
        int ret = do_write(pid, addr, data, len);
        free(data);
        return ret;
    }

    fprintf(stderr, "Unknown command: %s\n", cmd);
    return 1;
}
