"""检查 PE 文件位数（32/64-bit）"""
import struct
import sys


def check_bitness(path):
    with open(path, "rb") as f:
        f.seek(0x3c)
        pe_off = struct.unpack("<I", f.read(4))[0]
        f.seek(pe_off + 4)
        machine = struct.unpack("<H", f.read(2))[0]
    if machine == 0x14c:
        return "32-bit (i386)"
    if machine == 0x8664:
        return "64-bit (AMD64)"
    return f"unknown 0x{machine:04X}"


if __name__ == "__main__":
    p = sys.argv[1] if len(sys.argv) > 1 else r"D:\leidian\LDPlayer9\dnplayer.exe"
    print(f"{p}: {check_bitness(p)}")
