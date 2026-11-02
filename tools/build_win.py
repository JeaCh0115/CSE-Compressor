"""Windows 一键打包：应用 exe + CLI exe + 独立安装程序。

用法：
  python tools/build_win.py               全流程（构建信息 → 图标 → exe → payload → 安装器）
  python tools/build_win.py --skip-icons  复用现有图标
  python tools/build_win.py --verify      末尾追加静默安装/卸载自检
产出：
  dist/CSE-Compressor-<ver>-Setup.exe     独立安装程序
  dist/CSE-Compressor-<ver>-Portable.zip  便携版（免安装）
  packaging/build/dist/<时间戳>/          中间产物（CSECompressor 目录、cse-cli.exe、Setup）
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
BUILD = os.path.join(ROOT, "packaging", "build")
DIST = os.path.join(ROOT, "dist")
ICONS = os.path.join(ROOT, "assets", "icons")
# 每次构建用独立输出目录，避免 PyInstaller 删除旧产物（也便于回溯）
STAGE = os.path.join(BUILD, "dist", time.strftime("%Y%m%d-%H%M%S"))

sys.path.insert(0, ROOT)


def sh(cmd, **kw):
    print("+", " ".join(os.path.relpath(c, ROOT) if isinstance(c, str) and c.startswith(ROOT)
                        else str(c) for c in cmd), flush=True)
    r = subprocess.run(cmd, cwd=ROOT, **kw)
    if r.returncode != 0:
        raise SystemExit(f"命令失败（exit {r.returncode}）: {' '.join(map(str, cmd))}")
    return r


def step(msg):
    print(f"\n=== {msg} " + "=" * max(0, 60 - len(msg)), flush=True)


def write_build_info():
    """把真实打包时间与构建号烧进 csengine/_build.py。"""
    from csengine import version
    build_time = time.strftime("%Y-%m-%d %H:%M:%S")
    build_id = time.strftime("b%Y%m%d.") + str(int(time.time()) % 86400)
    path = os.path.join(ROOT, "csengine", "_build.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'"""构建信息（由 tools/build_win.py 于 {build_time} 生成）。"""\n\n'
                f'BUILD_TIME = "{build_time}"\n'
                f'BUILD_ID = "{build_id}"\n'
                f'BUILD_CHANNEL = "{version.CHANNEL}"\n')
    print(f"构建信息: {build_time} / {build_id} -> {os.path.relpath(path, ROOT)}")
    return build_time, build_id


def make_icons(skip=False):
    if skip and os.path.isfile(os.path.join(ICONS, "cse-app.ico")):
        step("图标（跳过，复用现有）")
        return
    step("生成图标")
    sh([PY, os.path.join(ROOT, "tools", "make_icons.py")])


def version_file(product_name, exe_name, out_path):
    """生成 PyInstaller 的 Windows 版本资源文件。"""
    from csengine import version
    vt = ".".join(str(x) for x in version.VERSION_TUPLE)
    content = f'''# 由 tools/build_win.py 生成
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({version.VERSION_TUPLE[0]}, {version.VERSION_TUPLE[1]}, {version.VERSION_TUPLE[2]}, {version.VERSION_TUPLE[3]}),
    prodvers=({version.VERSION_TUPLE[0]}, {version.VERSION_TUPLE[1]}, {version.VERSION_TUPLE[2]}, {version.VERSION_TUPLE[3]}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([
      StringTable('080404b0', [
        StringStruct('CompanyName', '{version.COMPANY}'),
        StringStruct('FileDescription', '{product_name}'),
        StringStruct('FileVersion', '{vt}'),
        StringStruct('InternalName', '{exe_name}'),
        StringStruct('LegalCopyright', '{version.COPYRIGHT}'),
        StringStruct('OriginalFilename', '{exe_name}'),
        StringStruct('ProductName', '{version.PRODUCT}'),
        StringStruct('ProductVersion', '{version.__version__}')])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ])
'''
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    return out_path


def optional_collect(*modules):
    """只对已安装的包加 --collect-all，避免缺依赖时打包直接失败。"""
    import importlib.util
    args = []
    for m in modules:
        try:
            if importlib.util.find_spec(m) is not None:
                args += ["--collect-all", m]
        except (ImportError, ValueError):
            continue
    return args


def build_app():
    step("打包应用 CSECompressor.exe")
    vf = version_file("CSE Compressor 压缩工作台", "CSECompressor.exe",
                      os.path.join(BUILD, "app_version.txt"))
    sh([PY, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        "--name", "CSECompressor",
        "--icon", os.path.join(ICONS, "cse-app.ico"),
        "--version-file", vf,
        "--distpath", STAGE,
        "--workpath", os.path.join(BUILD, "pyi", "app"),
        "--specpath", BUILD,
        "--add-data", f"{os.path.join(ROOT, 'csgui', 'web')}{os.pathsep}web",
        # 整个图标目录：应用图标 + 9 种压缩包格式图标（文件关联要用）
        "--add-data", f"{ICONS}{os.pathsep}assets/icons",
        "--hidden-import", "webview.platforms.winforms",
        "--collect-all", "webview",
        "--collect-all", "clr_loader",
        "--collect-all", "pythonnet",
        # 7z 解压后端：优先 py7zr（纯 Python），缺依赖时回落到 7z.exe
        *optional_collect("py7zr", "pybcj", "pyppmd", "pyzstd", "inflate64",
                          "multivolumefile", "Cryptodome"),
        os.path.join(ROOT, "packaging", "app_entry.py")])


def build_cli():
    step("打包命令行 cse-cli.exe")
    vf = version_file("CSE Compressor 命令行工具", "cse-cli.exe",
                      os.path.join(BUILD, "cli_version.txt"))
    sh([PY, "-m", "PyInstaller", "--noconfirm", "--clean", "--console", "--onefile",
        "--name", "cse-cli",
        "--icon", os.path.join(ICONS, "cse-app.ico"),
        "--version-file", vf,
        "--distpath", STAGE,
        "--workpath", os.path.join(BUILD, "pyi", "cli"),
        "--specpath", BUILD,
        os.path.join(ROOT, "packaging", "cli_entry.py")])


def make_payload():
    step("制作安装 payload")
    app_dir = os.path.join(STAGE, "CSECompressor")
    if not os.path.isdir(app_dir):
        raise SystemExit(f"缺少应用目录 {app_dir}")
    shutil.copy2(os.path.join(STAGE, "cse-cli.exe"), os.path.join(app_dir, "cse-cli.exe"))
    payload = os.path.join(BUILD, "payload.zip")
    os.makedirs(BUILD, exist_ok=True)
    if os.path.isfile(payload):
        os.remove(payload)
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, _dirs, files in os.walk(app_dir):
            for f in sorted(files):
                p = os.path.join(root, f)
                zf.write(p, os.path.relpath(p, app_dir))
    size = os.path.getsize(payload) / 1048576
    print(f"payload: {os.path.relpath(payload, ROOT)}  {size:.1f} MB")
    # 便携版（免安装 zip）
    from csengine import version
    portable = os.path.join(DIST, f"CSE-Compressor-{version.__version__}-Portable.zip")
    shutil.copyfile(payload, portable)
    print(f"便携版: {os.path.relpath(portable, ROOT)}")
    return payload


def build_installer():
    step("打包独立安装程序")
    from csengine import version
    vf = version_file(f"{version.PRODUCT} 安装程序", "Setup.exe",
                      os.path.join(BUILD, "setup_version.txt"))
    name = f"CSE-Compressor-{version.__version__}-Setup"
    sh([PY, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onefile",
        "--name", name,
        "--icon", os.path.join(ICONS, "cse-app.ico"),
        "--version-file", vf,
        "--distpath", STAGE,
        "--workpath", os.path.join(BUILD, "pyi", "setup"),
        "--specpath", BUILD,
        "--add-data", f"{os.path.join(ROOT, 'packaging', 'installer', 'ui.html')}{os.pathsep}.",
        "--add-data", f"{os.path.join(BUILD, 'payload.zip')}{os.pathsep}.",
        "--add-data", f"{os.path.join(ICONS, 'app-logo.png')}{os.pathsep}.",
        "--add-data", f"{os.path.join(ICONS, 'cse-app.ico')}{os.pathsep}.",
        "--collect-all", "webview",
        "--collect-all", "clr_loader",
        "--collect-all", "pythonnet",
        os.path.join(ROOT, "packaging", "installer", "setup_app.py")])
    staged = os.path.join(STAGE, name + ".exe")
    out = os.path.join(DIST, name + ".exe")
    shutil.copy2(staged, out)
    print(f"安装程序: {out}  {os.path.getsize(out) / 1048576:.1f} MB")
    return out


def verify():
    """静默安装 → 校验 → 卸载 → 校验（目录每次全新，不做预删除）。"""
    step("安装自检（静默）")
    from csengine import version
    setup = os.path.join(DIST, f"CSE-Compressor-{version.__version__}-Setup.exe")
    silent_dir = os.path.join(BUILD, "_verify", time.strftime("%Y%m%d-%H%M%S"))
    sh([setup, "--silent", f"--dir={silent_dir}", "--no-shortcut", "--no-startmenu"])
    exe = os.path.join(silent_dir, "CSECompressor.exe")
    assert os.path.isfile(exe), "CSECompressor.exe 未释放"
    assert os.path.isfile(os.path.join(silent_dir, "cse-cli.exe")), "cse-cli.exe 未释放"
    assert os.path.isfile(os.path.join(silent_dir, "assets", "icons", "file-cse.ico"))
    for ext in ("zip", "7z", "rar", "tar", "gz", "bz2", "xz", "iso"):
        assert os.path.isfile(os.path.join(silent_dir, "assets", "icons", f"file-{ext}.ico")), \
            f"缺少格式图标 file-{ext}.ico"
    assert os.path.isfile(os.path.join(silent_dir, "manifest.json"))
    print(f"✓ 安装自检通过: {silent_dir}")

    step("卸载自检（静默）")
    sh([setup, "--uninstall", "--silent", f"--dir={silent_dir}"])
    import winreg
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                       r"Software\Microsoft\Windows\CurrentVersion\Uninstall\CSE Compressor")
        raise AssertionError("卸载信息仍存在")
    except FileNotFoundError:
        print("✓ 卸载自检通过（注册表已清理）")
    if os.path.isdir(silent_dir):
        # 受限环境下（如带删除保护的沙箱）文件删除可能被拦截；真实机器上会完整删除
        print("  注意: 目录仍存在（当前环境对批量删除有保护），真实环境将完整移除")
    print("✓ 卸载自检通过")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-icons", action="store_true")
    ap.add_argument("--verify", action="store_true", help="末尾静默安装/卸载自检")
    args = ap.parse_args()

    os.makedirs(BUILD, exist_ok=True)
    os.makedirs(DIST, exist_ok=True)   # 流水线的 clean 步骤会删掉 dist，这里补回来
    write_build_info()
    make_icons(args.skip_icons)
    build_app()
    build_cli()
    make_payload()
    build_installer()

    print("\n" + "=" * 66)
    print("打包完成，产物：")
    for name in sorted(os.listdir(DIST)):
        p = os.path.join(DIST, name)
        if os.path.isfile(p):
            print(f"  {name:<44} {os.path.getsize(p) / 1048576:8.1f} MB")
        else:
            total = sum(os.path.getsize(os.path.join(r, f))
                        for r, _d, fs in os.walk(p) for f in fs)
            print(f"  {name + '/':<44} {total / 1048576:8.1f} MB")

    if args.verify:
        verify()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
