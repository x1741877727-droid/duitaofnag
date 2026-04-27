"""
GameBot 构建脚本
Nuitka standalone 编译（.py → C → .pyd 原生二进制，零源码暴露）

用法:
  python build.py                # Nuitka standalone 编译
  python build.py --pyinstaller  # PyInstaller 备选（开发调试用）
  python build.py --check        # 仅检查依赖
  python build.py --installer    # 编译后制作 Inno Setup 安装包
"""

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(ROOT, "backend")
WEB_DIST = os.path.join(ROOT, "web", "dist")
OUTPUT_DIR = os.path.join(ROOT, "output")


def get_version() -> str:
    """从 version.py 读取版本号"""
    v = {}
    with open(os.path.join(ROOT, "version.py"), encoding="utf-8") as f:
        exec(f.read(), v)
    return v.get("__version__", "0.0.0")


def check_dependencies():
    print("=== 依赖检查 ===")
    print(f"  Python: {sys.version.split()[0]}")

    if os.path.isdir(WEB_DIST):
        files = os.listdir(WEB_DIST)
        print(f"  frontend: OK ({len(files)} files)")
    else:
        print("  frontend: MISSING - run: cd web && npm run build")
        return False

    deps = ["fastapi", "uvicorn", "cv2", "numpy", "rapidocr"]
    for dep in deps:
        try:
            __import__(dep)
            print(f"  {dep}: OK")
        except ImportError:
            print(f"  {dep}: MISSING")
            return False

    print(f"  version: {get_version()}")
    print("=== OK ===\n")
    return True


def build_frontend():
    print("building frontend...")
    web_dir = os.path.join(ROOT, "web")
    if not os.path.exists(os.path.join(web_dir, "node_modules")):
        subprocess.run(["npm", "install"], cwd=web_dir, check=True)
    subprocess.run(["npm", "run", "build"], cwd=web_dir, check=True)


def build_nuitka():
    """Nuitka standalone 编译 — 文件夹模式，.py 编译为 .pyd"""
    version = get_version()

    print("=" * 60)
    print(f"Nuitka Standalone Build v{version}")
    print("=" * 60)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        f"--output-dir={OUTPUT_DIR}",

        # Windows
        "--windows-console-mode=disable",

        # 产品信息
        f"--product-version={version}",
        "--product-name=GameBot",
        "--company-name=GameBot",

        # 包含模块
        "--include-package=backend",
        "--include-package=fastapi",
        "--include-package=uvicorn",
        "--include-package=cv2",
        "--include-package=numpy",
        "--include-package=rapidocr",

        # 前端 + 模板
        f"--include-data-dir={WEB_DIST}=web/dist",
        f"--include-data-dir={os.path.join(ROOT, 'fixtures', 'templates')}=fixtures/templates",

        # 优化
        "--follow-imports",
        "--assume-yes-for-downloads",

        # 入口
        os.path.join(BACKEND_DIR, "main.py"),
    ]

    # 图标（如果有）
    icon = os.path.join(ROOT, "assets", "icon.ico")
    if os.path.exists(icon):
        cmd.insert(3, f"--windows-icon-from-ico={icon}")

    print(f"command: nuitka --standalone ...")
    subprocess.run(cmd, check=True)

    print("\n" + "=" * 60)
    dist_dir = os.path.join(OUTPUT_DIR, "main.dist")
    if os.path.isdir(dist_dir):
        # 重命名为 GameBot.dist
        target = os.path.join(OUTPUT_DIR, "GameBot.dist")
        if os.path.isdir(target):
            import shutil
            shutil.rmtree(target)
        os.rename(dist_dir, target)
        # 重命名 exe
        old_exe = os.path.join(target, "main.exe")
        new_exe = os.path.join(target, "GameBot.exe")
        if os.path.exists(old_exe):
            os.rename(old_exe, new_exe)
        print(f"Output: {target}")
        print(f"Exe: {new_exe}")

        # 验证无 .py 文件
        py_count = 0
        for dirpath, _, filenames in os.walk(target):
            for fn in filenames:
                if fn.endswith(('.py', '.pyc')):
                    py_count += 1
        print(f"Source files in output: {py_count} (should be 0)")
        _link_logs_to_project_root(target)
    else:
        print(f"Output: {OUTPUT_DIR}")
    print("=" * 60)


def _link_logs_to_project_root(dist_dir: str):
    """build 完后把 dist/GameBot/logs 建成 junction 指向项目根 logs/.
    没这个的话: PyInstaller build 清 dist 会顺便清掉 exe 之前跑过的所有 sessions —
    用户决策档案突然消失. junction 让 exe 写 logs 实际落到项目根, 跨 build 持久化,
    且 dev/exe 共享同一份历史."""
    if not os.path.isdir(dist_dir):
        return
    link_path = os.path.join(dist_dir, "logs")
    target = os.path.join(ROOT, "logs")
    os.makedirs(target, exist_ok=True)
    # build 刚清过 dist, link_path 一般不存在; 防御性处理 (旧 junction 残留 / 普通目录)
    if os.path.exists(link_path) or os.path.islink(link_path):
        try:
            if os.path.isdir(link_path) and not os.path.islink(link_path):
                import shutil
                shutil.rmtree(link_path)
            else:
                os.remove(link_path)
        except Exception as e:
            print(f"  WARN: 清理旧 logs 失败 ({e}), 跳过 junction")
            return
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", link_path, target],
                check=True, capture_output=True, text=True,
            )
            print(f"  logs junction: {link_path} -> {target}")
        except subprocess.CalledProcessError as e:
            print(f"  WARN: junction 失败: {e.stderr or e}")
    else:
        os.symlink(target, link_path)
        print(f"  logs symlink: {link_path} -> {target}")


def build_pyinstaller():
    """PyInstaller 文件夹模式 — 开发调试用"""
    version = get_version()

    print("=" * 60)
    print(f"PyInstaller Build v{version} (folder mode)")
    print("=" * 60)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    spec_content = f"""
# -*- mode: python ; coding: utf-8 -*-
import os, importlib
ROOT = {repr(ROOT)}

# 自动收集 rapidocr 的数据文件（yaml 配置 + ONNX 模型）
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules
rapidocr_datas = collect_data_files('rapidocr', include_py_files=False)
# onnxruntime DLLs（含 DirectML provider/dlls，使 GPU 加速生效）
onnx_binaries = collect_dynamic_libs('onnxruntime')
# PyAV libavcodec/libavformat 等 .pyd / DLL — UE4 截图依赖
av_binaries = collect_dynamic_libs('av')
av_hidden = collect_submodules('av')

a = Analysis(
    [os.path.join(ROOT, 'backend', 'main.py')],
    pathex=[ROOT],
    binaries=onnx_binaries + av_binaries,
    datas=[
        (os.path.join(ROOT, 'web', 'dist'), 'web/dist'),
        (os.path.join(ROOT, 'fixtures', 'templates'), 'fixtures/templates'),
        (os.path.join(ROOT, 'config'), 'config'),
        (os.path.join(ROOT, 'fixtures', 'golden_set'), 'fixtures/golden_set'),
        # DXHook 截图工具：64-bit DLL + 注入器（运行时由 DXHookStream 用）
        (os.path.join(ROOT, 'tools', 'dxhook', 'build', 'x64'), 'tools/dxhook/build/x64'),
    ] + rapidocr_datas,
    hiddenimports=[
        'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan', 'uvicorn.lifespan.on',
        'rapidocr', 'rapidocr.main', 'rapidocr.utils',
        'rapidocr.ch_ppocr_det', 'rapidocr.ch_ppocr_cls', 'rapidocr.ch_ppocr_rec',
        'rapidocr.cal_rec_boxes', 'rapidocr.inference_engine',
        'backend', 'backend.api', 'backend.config', 'backend.runner_service',
        'backend.automation', 'backend.automation.adb_lite',
        'backend.automation.guarded_adb', 'backend.automation.single_runner',
        'backend.automation.screen_matcher', 'backend.automation.ocr_dismisser',
        'backend.automation.popup_dismisser', 'backend.automation.metrics',
        'backend.automation.ocr_cache', 'backend.automation.roi_config',
        'backend.automation.yolo_detector', 'backend.automation.ocr_pool',
        'backend.automation.screenshot_collector',
        'backend.automation.recognizer', 'backend.automation.memory_l1',
        'backend.automation.watchdogs', 'backend.automation.lobby_check',
        'backend.automation.state_expectation',
        'yaml', 'psutil', 'multiprocessing',
    ] + av_hidden,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], name='GameBot', debug=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, name='GameBot')
"""

    spec_path = os.path.join(OUTPUT_DIR, "GameBot.spec")
    with open(spec_path, "w", encoding="utf-8") as f:
        f.write(spec_content)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--clean", "-y",
        "--distpath", os.path.join(OUTPUT_DIR, "dist"),
        "--workpath", os.path.join(OUTPUT_DIR, "build"),
        spec_path,
    ]

    subprocess.run(cmd, check=True)

    dist_dir = os.path.join(OUTPUT_DIR, "dist", "GameBot")
    print(f"\nOutput: {dist_dir}")
    if os.path.isdir(dist_dir):
        exe_path = os.path.join(dist_dir, "GameBot.exe")
        print(f"Exe: {exe_path}")
        print(f"Exists: {os.path.exists(exe_path)}")
        _link_logs_to_project_root(dist_dir)
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="GameBot Build")
    parser.add_argument("--pyinstaller", action="store_true", help="Use PyInstaller (dev)")
    parser.add_argument("--check", action="store_true", help="Check deps only")
    parser.add_argument("--skip-frontend", action="store_true", help="Skip frontend build")
    parser.add_argument("--installer", action="store_true", help="Build Inno Setup installer after")
    args = parser.parse_args()

    os.chdir(ROOT)

    if args.check:
        check_dependencies()
        return

    if not check_dependencies():
        print("Dependencies missing!")
        sys.exit(1)

    if not args.skip_frontend:
        if not os.path.isdir(WEB_DIST):
            build_frontend()

    if args.pyinstaller:
        build_pyinstaller()
    else:
        build_nuitka()

    if args.installer:
        print("\nBuilding installer...")
        iss = os.path.join(ROOT, "installer", "GameBot.iss")
        if os.path.exists(iss):
            subprocess.run(["iscc", iss], check=True)
        else:
            print(f"Installer script not found: {iss}")


if __name__ == "__main__":
    main()
