# Mac 上交叉编译 32-bit Windows DLL + 注入器
#
# 用法:
#   make           — 编 DLL + 注入器
#   make clean     — 清理产物
#
# 输出:
#   build/gamebot_hook.dll  — 注入到 dnplayer.exe 的 32-bit DLL（含 MinHook + DXGI hook）
#   build/inject.exe        — 32-bit 注入器（在 Windows 上跑）

CC := i686-w64-mingw32-gcc
CXX := i686-w64-mingw32-g++

# DLL 编译参数
INCLUDES := -Iminhook/include
CFLAGS_DLL := -shared -static-libgcc -static -O2 -Wall \
              $(INCLUDES) -DUNICODE -D_UNICODE
LDFLAGS_DLL := -lopengl32 -luser32 -lgdi32 -lkernel32

# 注入器
CFLAGS_EXE := -static -O2 -Wall

BUILD := build

# MinHook 源
MH_SRC := minhook/src/buffer.c minhook/src/hook.c minhook/src/trampoline.c \
          minhook/src/hde/hde32.c

all: $(BUILD)/gamebot_hook.dll $(BUILD)/inject.exe

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/gamebot_hook.dll: hook.c $(MH_SRC) | $(BUILD)
	$(CC) $(CFLAGS_DLL) -o $@ $^ $(LDFLAGS_DLL)

$(BUILD)/inject.exe: inject.c | $(BUILD)
	$(CC) $(CFLAGS_EXE) -o $@ $<

clean:
	rm -rf $(BUILD)

.PHONY: all clean
