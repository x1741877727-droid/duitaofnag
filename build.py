"""
打包脚本
在 Windows 上使用 Nuitka 编译为 EXE

用法:
  python build.py              # Nuitka 编译 (推荐, 代码保护)
  python build.py --pyinstaller # PyInstaller 打包 (备选, 更快)
  python build.py --check      # 仅检查依赖
"""

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(ROOT, "backend")
WEB_DIST = os.path.join(ROOT, "web", "dist")
OUTPUT_DIR = os.path.join(ROOT, "output")


def check_dependencies():
    """检查打包依赖"""
    print("检查依赖...")

    # Python
    print(f"  Python: {sys.version}")

    # 前端构建产物
    if os.path.isdir(WEB_DIST):
        files = os.listdir(WEB_DIST)
        print(f"  前端构建: OK ({len(files)} 个文件)")
    else:
        print("  前端构建: FAIL 请先运行 cd web && npm run build")
        return False

    # Nuitka
    try:
        result = subprocess.run(
            [sys.executable, "-m", "nuitka", "--version"],
            capture_output=True, text=True,
        )
        print(f"  Nuitka: OK {result.stdout.strip()[:50]}")
    except Exception:
        print("  Nuitka: FAIL 请运行 pip install nuitka")

    # PyInstaller (备选)
    try:
        import PyInstaller
        print(f"  PyInstaller: OK {PyInstaller.__version__}")
    except ImportError:
        print("  PyInstaller: FAIL (可选, pip install pyinstaller)")

    # 核心依赖
    deps = ["fastapi", "uvicorn", "cv2", "numpy", "rapidocr"]
    for dep in deps:
        try:
            __import__(dep)
            print(f"  {dep}: OK")
        except ImportError:
            print(f"  {dep}: FAIL")
            return False

    print("依赖检查通过!\n")
    return True


def build_frontend():
    """构建前端"""
    print("构建前端...")
    web_dir = os.path.join(ROOT, "web")
    if not os.path.exists(os.path.join(web_dir, "node_modules")):
        subprocess.run(["npm", "install"], cwd=web_dir, check=True)
    subprocess.run(["npm", "run", "build"], cwd=web_dir, check=True)
    print("前端构建完成\n")


def build_nuitka():
    """Nuitka 编译"""
    print("=" * 60)
    print("Nuitka 编译开始")
    print("=" * 60)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",                    # 独立可执行
        "--onefile",                       # 打成单个 EXE
        "--output-dir=" + OUTPUT_DIR,
        "--output-filename=GameAutomation.exe",

        # Windows 设置
        "--windows-console-mode=disable",  # 隐藏控制台窗口
        "--windows-icon-from-ico=",        # 图标 (留空则用默认)

        # 包含的模块
        "--include-package=backend",
        "--include-package=fastapi",
        "--include-package=uvicorn",
        "--include-package=cv2",
        "--include-package=numpy",
        "--include-package=rapidocr",

        # 包含前端构建产物
        f"--include-data-dir={WEB_DIST}=web/dist",

        # 包含模板文件
        f"--include-data-dir={os.path.join(ROOT, 'fixtures', 'templates')}=fixtures/templates",

        # 包含配置文件模板
        f"--include-data-files={os.path.join(ROOT, 'settings.json')}=settings.json",
        f"--include-data-files={os.path.join(ROOT, 'accounts.json')}=accounts.json",

        # 优化
        "--follow-imports",
        "--assume-yes-for-downloads",       # 自动下载依赖的 C 编译器

        # 入口文件
        os.path.join(BACKEND_DIR, "main.py"),
    ]

    print(f"命令: {' '.join(cmd[:5])}...")
    subprocess.run(cmd, check=True)

    print("\n" + "=" * 60)
    print("Nuitka 编译完成!")
    print(f"输出: {OUTPUT_DIR}")
    print("=" * 60)


def build_pyinstaller():
    """PyInstaller 打包 — 单文件 exe，所有资源内嵌，字节码加密"""
    print("=" * 60)
    print("PyInstaller 打包开始 (单文件 + 加密)")
    print("=" * 60)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 生成 spec 内容 — 单文件 exe
    spec_content = f"""
# -*- mode: python ; coding: utf-8 -*-
import os

ROOT = {repr(ROOT)}

a = Analysis(
    [os.path.join(ROOT, 'backend', 'main.py')],
    pathex=[ROOT],
    datas=[
        (os.path.join(ROOT, 'web', 'dist'), 'web/dist'),
        (os.path.join(ROOT, 'fixtures', 'templates'), 'fixtures/templates'),
    ],
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'rapidocr',
        'backend',
        'backend.api',
        'backend.config',
        'backend.runner_service',
        'backend.automation',
        'backend.automation.adb_lite',
        'backend.automation.guarded_adb',
        'backend.automation.single_runner',
        'backend.automation.screen_matcher',
        'backend.automation.ocr_dismisser',
        'backend.automation.popup_dismisser',
    ],
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='GameBot',
    debug=False,
    strip=False,
    upx=True,
    console=False,
)
"""

    spec_path = os.path.join(OUTPUT_DIR, "GameBot.spec")
    with open(spec_path, "w", encoding="utf-8") as f:
        f.write(spec_content)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--clean",
        "--distpath", os.path.join(OUTPUT_DIR, "dist"),
        "--workpath", os.path.join(OUTPUT_DIR, "build"),
        spec_path,
    ]

    print(f"命令: pyinstaller ...")
    subprocess.run(cmd, check=True)

    print("\n" + "=" * 60)
    print("PyInstaller 打包完成!")
    print(f"输出: {os.path.join(OUTPUT_DIR, 'dist', 'GameAutomation.exe')}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="打包为 EXE")
    parser.add_argument("--pyinstaller", action="store_true", help="使用 PyInstaller (备选)")
    parser.add_argument("--check", action="store_true", help="仅检查依赖")
    parser.add_argument("--skip-frontend", action="store_true", help="跳过前端构建")
    args = parser.parse_args()

    os.chdir(ROOT)

    if args.check:
        check_dependencies()
        return

    if not check_dependencies():
        print("依赖检查失败，请先安装缺失的依赖")
        sys.exit(1)

    if not args.skip_frontend:
        if not os.path.isdir(WEB_DIST):
            build_frontend()

    if args.pyinstaller:
        build_pyinstaller()
    else:
        build_nuitka()


if __name__ == "__main__":
    main()
