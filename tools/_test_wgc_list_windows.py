"""列出所有 LDPlayer 9 实例窗口（HWND + title + class + 大小）"""

import sys
import win32gui
import win32process


def list_ldplayer_windows():
    results = []

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        cls = win32gui.GetClassName(hwnd)
        # 命中 LDPlayer 关键词或常见 class
        title_l = title.lower()
        if "ldplayer" in title_l or "雷电" in title or cls in (
            "subWin", "TheRender", "LDPlayerMainFrame",
        ):
            try:
                rect = win32gui.GetWindowRect(hwnd)
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
            except Exception:
                w = h = 0
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                pid = 0
            results.append({
                "hwnd": hwnd,
                "title": title,
                "class": cls,
                "size": (w, h),
                "pid": pid,
            })
        return True

    win32gui.EnumWindows(callback, None)
    return results


def main():
    wins = list_ldplayer_windows()
    print(f"找到 {len(wins)} 个候选窗口:")
    for w in wins:
        print(f"  HWND=0x{w['hwnd']:08x} pid={w['pid']:6d} size={w['size'][0]}x{w['size'][1]:>4} class={w['class']:<25} title={w['title']}")


if __name__ == "__main__":
    main()
