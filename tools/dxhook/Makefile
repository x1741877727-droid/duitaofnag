# Mac 上交叉编译 32-bit Windows DLL + 注入器
#
# 用法:
#   make           — 编 DLL + 注入器
#   make clean     — 清理产物
#
# 输出:
#   build/gamebot_hook.dll  — 注入到 dnplayer.exe 的 32-bit DLL
#   build/inject.exe        — 32-bit 注入器（在 Windows 上跑）

CC := i686-w64-mingw32-gcc
CXX := i686-w64-mingw32-g++

# DLL 编译参数：static link runtime 减少依赖
CFLAGS_DLL := -shared -static-libgcc -O2 -Wall

# 注入器：static + 不产 stub 错误
CFLAGS_EXE := -static -O2 -Wall

BUILD := build

all: $(BUILD)/gamebot_hook.dll $(BUILD)/inject.exe

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/gamebot_hook.dll: hook.c | $(BUILD)
	$(CC) $(CFLAGS_DLL) -o $@ $<

$(BUILD)/inject.exe: inject.c | $(BUILD)
	$(CC) $(CFLAGS_EXE) -o $@ $<

clean:
	rm -rf $(BUILD)

.PHONY: all clean
