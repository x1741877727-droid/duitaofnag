# Mac 上交叉编译 Windows DLL + 注入器（32 + 64 位双版本）
#
# 用法:
#   make             — 同时构建 32 + 64 位
#   make all32       — 仅 32-bit（注入 dnplayer.exe）
#   make all64       — 仅 64-bit（注入 Ld9BoxHeadless.exe）
#   make clean
#
# 输出:
#   build/x86/gamebot_hook.dll    — 32-bit DLL
#   build/x86/inject.exe          — 32-bit 注入器
#   build/x64/gamebot_hook.dll    — 64-bit DLL
#   build/x64/inject.exe          — 64-bit 注入器

CC32 := i686-w64-mingw32-gcc
CC64 := x86_64-w64-mingw32-gcc

INCLUDES := -Iminhook/include
CFLAGS_DLL := -shared -static-libgcc -static -O2 -Wall \
              $(INCLUDES) -DUNICODE -D_UNICODE
LDFLAGS_DLL := -lopengl32 -luser32 -lgdi32 -lkernel32

CFLAGS_EXE := -static -O2 -Wall

BUILD := build
B32 := $(BUILD)/x86
B64 := $(BUILD)/x64

# MinHook 源（32 / 64 位用不同 hde）
MH_SRC_COMMON := minhook/src/buffer.c minhook/src/hook.c minhook/src/trampoline.c
MH_SRC_32 := $(MH_SRC_COMMON) minhook/src/hde/hde32.c
MH_SRC_64 := $(MH_SRC_COMMON) minhook/src/hde/hde64.c

all: all32 all64

all32: $(B32)/gamebot_hook.dll $(B32)/inject.exe
all64: $(B64)/gamebot_hook.dll $(B64)/inject.exe

$(B32):
	mkdir -p $(B32)
$(B64):
	mkdir -p $(B64)

$(B32)/gamebot_hook.dll: hook.c $(MH_SRC_32) | $(B32)
	$(CC32) $(CFLAGS_DLL) -o $@ $^ $(LDFLAGS_DLL)

$(B64)/gamebot_hook.dll: hook.c $(MH_SRC_64) | $(B64)
	$(CC64) $(CFLAGS_DLL) -o $@ $^ $(LDFLAGS_DLL)

$(B32)/inject.exe: inject.c | $(B32)
	$(CC32) $(CFLAGS_EXE) -o $@ $<

$(B64)/inject.exe: inject.c | $(B64)
	$(CC64) $(CFLAGS_EXE) -o $@ $<

clean:
	rm -rf $(BUILD)

.PHONY: all all32 all64 clean
