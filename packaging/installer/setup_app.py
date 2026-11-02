"""CSE Compressor 独立安装程序（Windows）。

用法：
  Setup.exe                     图形界面安装
  Setup.exe --silent --dir=X    静默安装（可加 --no-shortcut --no-startmenu --no-assoc）
  Setup.exe --uninstall         图形界面卸载（读同目录 manifest.json）
  Setup.exe --uninstall --silent
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile

PROG = "CSE Compressor"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\CSE Compressor"


def _root():
    # PyInstaller 打包时项目根目录不可用；这里只为 winassoc/version 模块
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resource(name: str) -> str:
    """打包内嵌资源（payload.zip / app-logo.png）。"""
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, name)  # noqa: SLF001
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


def default_dir() -> str:
    # 默认安装到 C 盘程序目录；无权限时安装阶段会自动请求管理员提权
    pf = os.environ.get("ProgramFiles") or os.path.join(
        os.environ.get("SystemDrive", "C:") + os.sep, "Program Files")
    return os.path.join(pf, PROG)


def _dir_writable(dest: str) -> bool:
    try:
        os.makedirs(dest, exist_ok=True)
        probe = os.path.join(dest, ".cse_write_probe")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def _can_write_dir(d: str) -> bool:
    """只探测不创建（供卸载流程用）。"""
    if not os.path.isdir(d):
        return True  # 目录都不在了，谈不上无权限
    try:
        probe = os.path.join(d, ".cse_write_probe")
        with open(probe, "w") as f:
            pass
        os.remove(probe)
        return True
    except OSError:
        return False


def _elevate(args_list) -> bool:
    """以管理员身份重启自身执行 args_list（触发 UAC）；成功返回 True。"""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False
    try:
        import ctypes
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, subprocess.list2cmdline(args_list), None, 1)
        return int(rc) > 32
    except Exception:
        return False


def start_menu_dir() -> str:
    return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                        "Microsoft", "Windows", "Start Menu", "Programs", PROG)


def desktop_dir() -> str:
    return os.path.join(os.path.expanduser("~"), "Desktop")


# ---------------- 快捷方式 ----------------
def make_shortcut(lnk_path, target, icon=None, workdir=None):
    lnk_path = os.path.abspath(lnk_path)
    os.makedirs(os.path.dirname(lnk_path), exist_ok=True)
    icon = icon or target
    workdir = workdir or os.path.dirname(target)
    cmd = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{lnk_path}'); "
        f"$s.TargetPath = '{target}'; "
        f"$s.IconLocation = '{icon},0'; "
        f"$s.WorkingDirectory = '{workdir}'; "
        "$s.Save()"
    )
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                   check=True, capture_output=True, timeout=30)


# ---------------- 注册表 ----------------
def register_uninstall(install_dir, exe, icon):
    import winreg
    payload = os.path.getsize(_resource("payload.zip"))
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, PROG)
        winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, version_str())
        winreg.SetValueEx(k, "Publisher", 0, winreg.REG_SZ, "CSE Project")
        winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, install_dir)
        winreg.SetValueEx(k, "DisplayIcon", 0, winreg.REG_SZ, f"{icon},0")
        winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ, f'"{exe}" --uninstall')
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, payload // 1024 + 2048)


def unregister_uninstall():
    import winreg
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass


def version_str():
    from csengine.version import __version__
    return __version__


# ---------------- 安装 ----------------
def do_install(opts, report=lambda stage, pct, msg: None) -> dict:
    """执行安装，返回 manifest（也写入安装目录 manifest.json）。"""
    t0 = time.time()
    dest = opts["dir"]
    if not _dir_writable(dest):
        # Program Files 等受保护目录：以管理员权限接力安装（触发 UAC，选项原样保留）
        args = ["--silent", f"--dir={dest}"]
        if not opts.get("startmenu", True):
            args.append("--no-startmenu")
        if not opts.get("desktop", True):
            args.append("--no-shortcut")
        if not opts.get("assoc", True):
            args.append("--no-assoc")
        if not opts.get("shellmenu", True):
            args.append("--no-shellmenu")
        if _elevate(args):
            return {"elevated": True, "dir": dest, "product": PROG}
        raise PermissionError(f"没有权限写入 {dest}，请以管理员身份运行或改选其他目录")
    exe = os.path.join(dest, "CSECompressor.exe")
    csc = os.path.join(dest, "cse-cli.exe")
    icon_file = os.path.join(dest, "assets", "icons", "file-cse.ico")
    app_icon = os.path.join(dest, "assets", "icons", "cse-app.ico")
    os.makedirs(dest, exist_ok=True)
    os.makedirs(os.path.join(dest, "assets", "icons"), exist_ok=True)

    report("extract", 0, "释放程序文件…")
    payload = _resource("payload.zip")
    total_bytes = os.path.getsize(payload)
    done = 0
    with zipfile.ZipFile(payload) as zf:
        names = zf.namelist()
        for i, name in enumerate(names):
            zf.extract(name, dest)
            done += zf.getinfo(name).file_size
            if i % 12 == 0 or i == len(names) - 1:
                report("extract", int(done / max(total_bytes, 1) * 90),
                       f"释放 {os.path.basename(name)}")

    # PyInstaller 6 one-folder 布局把数据放在 _internal 下；
    # 顶层再放一份图标，供文件关联与快捷方式引用
    os.makedirs(os.path.join(dest, "assets", "icons"), exist_ok=True)
    # 把整套图标（应用 + 9 种压缩包格式）都放到顶层，供文件关联/图标覆盖引用
    icon_src = ""
    for cand in (os.path.join(dest, "_internal", "assets", "icons"),
                 os.path.join(dest, "assets", "icons")):
        if os.path.isdir(cand):
            icon_src = cand
            break
    if icon_src:
        for name in sorted(os.listdir(icon_src)):
            if not name.lower().endswith((".ico", ".png")):
                continue
            src = os.path.join(icon_src, name)
            top = os.path.join(dest, "assets", "icons", name)
            if os.path.abspath(src) != os.path.abspath(top) and os.path.isfile(src):
                shutil.copy2(src, top)

    report("shortcut", 92, "创建快捷方式…")
    shortcuts = []
    if opts.get("startmenu", True):
        sm = os.path.join(start_menu_dir(), f"{PROG}.lnk")
        make_shortcut(sm, exe, app_icon)
        shortcuts.append(sm)
    if opts.get("desktop", True):
        dk = os.path.join(desktop_dir(), f"{PROG}.lnk")
        make_shortcut(dk, exe, app_icon)
        shortcuts.append(dk)

    assoc = False
    if opts.get("assoc", True) and os.name == "nt":
        report("assoc", 94, "注册 .cse 文件关联…")
        from csengine import winassoc
        winassoc.register(exe=exe, icon=icon_file)
        assoc = True

    shellmenu = False
    if opts.get("shellmenu", True) and os.name == "nt":
        report("assoc", 96, "注册资源管理器右键菜单…")
        from csengine import winassoc
        try:
            winassoc.register_context_menu(exe=exe, icon=app_icon)
            shellmenu = True
        except Exception:
            pass  # 右键菜单失败不阻塞安装

    # 统一压缩包图标 + 把 double-click 交给本程序：给 zip/7z/rar/tar/gz/bz2/xz/iso
    # 各建一个独立 ProgID（自带图标与 open 命令），再把扩展名关联过去
    archive_icons = False
    if os.name == "nt":
        report("assoc", 97, "应用统一压缩包图标与打开方式…")
        from csengine import winassoc
        try:
            res = winassoc.register_archive_icons(
                icon_dir=os.path.join(dest, "assets", "icons"), exe=exe)
            archive_icons = bool(res.get("applied"))
        except Exception:
            archive_icons = False  # 图标失败不阻塞安装

    report("registry", 98, "写入卸载信息…")
    # 把自己复制为安装目录里的独立卸载器（设置 → 应用 调用它卸载）
    uninstaller = ""
    if getattr(sys, "frozen", False) and \
            os.path.abspath(sys.executable) != os.path.abspath(os.path.join(dest, "Uninstall.exe")):
        try:
            uninstaller = os.path.join(dest, "Uninstall.exe")
            shutil.copy2(sys.executable, uninstaller)
        except OSError:
            uninstaller = ""
    register_uninstall(dest, uninstaller or exe, app_icon)

    manifest = {
        "product": PROG,
        "version": version_str(),
        "dir": dest,
        "exe": exe,
        "csc": csc,
        "uninstaller": uninstaller,
        "shortcuts": shortcuts,
        "assoc": assoc,
        "shellmenu": shellmenu,
        "archive_icons": archive_icons,
        "uninstall_key": UNINSTALL_KEY,
        "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    report("done", 100, f"安装完成（{time.time() - t0:.1f} 秒）")
    return manifest


def _rmtree_delayed(path):
    """当前 exe 占用自身时，延迟自删。"""
    tail = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")),
                        f"cse_unins_{int(time.time())}.cmd")
    with open(tail, "w", encoding="gbk", errors="ignore") as f:
        f.write(f"@echo off\r\nping -n 2 127.0.0.1 >nul\r\nrmdir /s /q \"{path}\"\r\n"
                f"del \"%~f0\"\r\n")
    subprocess.Popen(["cmd", "/c", tail], creationflags=0x08000000)  # CREATE_NO_WINDOW


def do_uninstall(dir_hint=None, report=lambda stage, pct, msg: None) -> dict:
    """按 manifest 卸载；返回摘要。dir_hint 由 --silent --dir=X 提供；
    图形卸载（安装目录里的 uninstall.exe）则用 exe 同目录。"""
    here = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False)
                                           else __file__))
    manifest_path = os.path.join(dir_hint or here, "manifest.json")
    removed = {"shortcuts": 0, "files": 0, "assoc": False}
    try:
        with open(manifest_path, encoding="utf-8") as f:
            m = json.load(f)
    except OSError:
        m = {"dir": here, "shortcuts": [], "assoc": False}

    base = os.path.abspath(m.get("dir") or dir_hint or here)
    # 受保护目录（如 Program Files）：请求管理员提权后接力卸载
    if not _can_write_dir(base):
        if _elevate(["--uninstall", "--silent", f"--dir={base}"]):
            return {"elevated": True, "dir": base}
        raise PermissionError(f"没有权限删除 {base}，请以管理员身份运行卸载器")

    report("uninstall", 10, "移除文件关联与右键菜单…")
    if os.name == "nt":
        try:
            from csengine import winassoc
            winassoc.unregister_context_menu()
        except Exception:
            pass
        try:
            from csengine import winassoc
            winassoc.unregister_archive_icons()
        except Exception:
            pass
        if m.get("assoc"):
            try:
                from csengine import winassoc
                winassoc.unregister()
                removed["assoc"] = True
            except Exception:
                pass

    report("uninstall", 30, "删除快捷方式…")
    for lnk in m.get("shortcuts", []):
        try:
            os.remove(lnk)
            removed["shortcuts"] += 1
        except OSError:
            pass
    sm_dir = start_menu_dir()
    try:
        if os.path.isdir(sm_dir) and not os.listdir(sm_dir):
            os.rmdir(sm_dir)
    except OSError:
        pass

    report("uninstall", 55, "清理注册表…")
    unregister_uninstall()

    report("uninstall", 75, "删除程序文件…")
    base = os.path.abspath(m.get("dir") or dir_hint or here)
    n = 0
    for root, dirs, files in os.walk(base, topdown=False):
        for f in files:
            p = os.path.join(root, f)
            try:
                os.remove(p)
                n += 1
            except OSError:
                pass
        for d in dirs:
            try:
                os.rmdir(os.path.join(root, d))
            except OSError:
                pass
    removed["files"] = n
    try:
        os.rmdir(base)
    except OSError:
        pass

    report("done", 100, "卸载完成")
    removed["dir"] = base
    return removed


# ---------------- 图形界面 ----------------
SETUP_BG = "#f5f6f8"   # 与 ui.html 的 body 底色一致（浅色）
SETUP_W, SETUP_H = 600, 410   # 紧凑尺寸：窗口大小≈卡片大小，外面不留大圈空白


def _load_ui_html() -> str:
    return open(_resource("ui.html"), encoding="utf-8").read()


def _virtual_screen():
    """虚拟桌面范围（含所有显示器），用于把窗口热身在所有屏幕之外。"""
    import ctypes
    u = ctypes.windll.user32
    gsm = u.GetSystemMetrics
    x, y = gsm(76), gsm(77)        # SM_XVIRTUALSCREEN / SM_YVIRTUALSCREEN
    return x, y, gsm(78), gsm(79)  # SM_CXVIRTUALSCREEN / SM_CYVIRTUALSCREEN


def _offscreen_pos():
    x, y, w, _h = _virtual_screen()
    return x + w + 80, y


def _center_pos():
    """主显示器工作区居中。"""
    import ctypes

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    r = RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
    if not r.right or not r.bottom:
        return 200, 160
    return max(0, (r.left + r.right - SETUP_W) // 2), max(0, (r.top + r.bottom - SETUP_H) // 2)


def _painted(hwnd) -> bool:
    """窗口里是否已经有真实内容（而不是一整片底色）。

    WebView2 从「窗口可见」到「画出第一帧」要 100~400ms，这段时间窗口只有一片
    底色——就是用户看到的「打开安装包闪一下白」。用 PrintWindow 抓窗口画面，
    统计中心区域的颜色种类：纯底色只有 1~2 种，出现文字/边框后会有几十种。
    """
    import ctypes

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_ulong), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_ushort),
                    ("biBitCount", ctypes.c_ushort), ("biCompression", ctypes.c_ulong),
                    ("biSizeImage", ctypes.c_ulong), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_ulong),
                    ("biClrImportant", ctypes.c_ulong)]

    class BI(ctypes.Structure):
        _fields_ = [("bmiHeader", BIH), ("bmiColors", ctypes.c_ulong * 3)]

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    r = RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w < 80 or h < 80:
        return False
    hdc = u.GetWindowDC(hwnd)
    mem = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mem, bmp)
    try:
        if not u.PrintWindow(hwnd, mem, 0x2):   # PW_RENDERFULLCONTENT
            return True                          # 抓不到就当作已就绪，别卡住窗口
        # 只取中心 60x40，够判断"有没有内容"
        cx, cy = w // 2 - 30, h // 2 - 20
        colors = set()
        for yy in range(cy, cy + 40, 2):
            for xx in range(cx, cx + 60, 2):
                colors.add(g.GetPixel(mem, xx, yy) & 0xFFFFFF)
        return len(colors) > 3
    finally:
        g.DeleteObject(bmp)
        g.DeleteDC(mem)
        u.ReleaseDC(hwnd, hdc)


def _find_setup_hwnd(title_hint="安装向导"):
    import ctypes
    from ctypes import wintypes

    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
        if title_hint in buf.value and "CSE" in buf.value:
            found.append(hwnd)
        return True

    ctypes.windll.user32.EnumWindows(cb, 0)
    return found[0] if found else 0


def _trace(msg: str) -> None:
    """安装器启动计时。只有 CSE_SETUP_TIMING=1 才写 %TEMP%\\CSE-setup.log，
    用来证明「窗口是在首帧画好之后才显示」，而不是建窗即显示一块底色。"""
    if not os.environ.get("CSE_SETUP_TIMING"):
        return
    try:
        log = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")), "CSE-setup.log")
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"{time.time():.3f} {msg}\n")
    except OSError:
        pass



class InstallerApi:
    """暴露给安装界面 window.pywebview.api。"""

    def __init__(self):
        self._win = None
        self.cancelled = False

    def bind(self, window):
        self._win = window
        return self

    def _report(self, stage, pct, msg):
        try:
            self._win.evaluate_js(
                f"window.__progress && window.__progress({json.dumps({'stage': stage, 'pct': pct, 'msg': msg})})")
        except Exception:
            pass

    def info(self):
        payload = _resource("payload.zip")
        return {
            "version": version_str(),
            "channel_label": "Beta 公测版",
            "build_time": BUILD_TIME_STR,
            "default_dir": default_dir(),
            "payload_mb": round(os.path.getsize(payload) / 1048576, 1) if os.path.isfile(payload) else 0,
            "installed": os.path.isfile(os.path.join(default_dir(), "CSECompressor.exe")),
        }

    def choose_dir(self):
        try:
            import webview
            # 注意：此版本 pywebview 的 FileDialog.FOLDER == 20（旧版才是 3）
            result = self._win.create_file_dialog(webview.FileDialog.FOLDER)
            if result:
                self._win.evaluate_js(
                    f"window.__setDir && window.__setDir({json.dumps(result[0])})")
        except Exception as exc:
            _fatal_log(exc)
            self._win.evaluate_js(
                f"window.__err && window.__err({json.dumps(f'选择目录失败：{exc}')})")
        return True

    def install(self, opts):
        self.cancelled = False
        threading.Thread(target=self._install, args=(opts,), daemon=True).start()
        return True

    def _install(self, opts):
        try:
            manifest = do_install(opts, self._report)
            if manifest.get("elevated"):
                self._win.evaluate_js(
                    f"window.__done && window.__done({json.dumps({'ok': True, 'elevated': True, 'dir': manifest['dir']})})")
            else:
                self._win.evaluate_js(
                    f"window.__done && window.__done({json.dumps({'ok': True, 'exe': manifest['exe']})})")
        except Exception as exc:
            self._win.evaluate_js(
                f"window.__done && window.__done({json.dumps({'ok': False, 'error': str(exc)})})")

    def confirm_uninstall(self):
        threading.Thread(target=self._uninstall, daemon=True).start()
        return True

    def _uninstall(self):
        try:
            r = do_uninstall(self._report)
            if r.get("elevated"):
                self._win.evaluate_js(
                    f"window.__done && window.__done({json.dumps({'ok': True, 'elevated': True})})")
            else:
                self._win.evaluate_js(
                    f"window.__done && window.__done({json.dumps({'ok': True, 'removed': r['files']})})")
        except Exception as exc:
            self._win.evaluate_js(
                f"window.__done && window.__done({json.dumps({'ok': False, 'error': str(exc)})})")

    def launch(self, exe):
        try:
            os.startfile(exe)  # noqa: S606
        except Exception:
            pass
        return True

    def minimize(self):
        try:
            self._win.minimize()
        except Exception:
            pass
        return True

    def close(self):
        self._win.destroy()
        return True


BUILD_TIME_STR = "开发版（未打包）"


def gui(mode="install"):
    import base64

    import webview
    # 无边框 + 拖拽区子元素生效，与主程序视觉统一
    webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = False
    api = InstallerApi()
    html = _load_ui_html().replace("__MODE__", mode)
    # html= 方式加载时相对路径不可用，把 logo 内联成 base64
    logo = _resource("app-logo.png")
    if os.path.isfile(logo):
        b64 = base64.b64encode(open(logo, "rb").read()).decode()
        html = html.replace('src="app-logo.png"', f'src="data:image/png;base64,{b64}"')
    # 建窗位置放在所有屏幕之外：先在那里"热身"，等内容画出来再挪到屏幕中央。
    # 直接 show 到目标位置的话，WebView2 要 100~400ms 才画出第一帧，
    # 这段时间窗口里只有一片纯底色 —— 就是用户看到的「闪一下白」。
    off_x, off_y = _offscreen_pos()
    window = webview.create_window(
        f"{PROG} 安装向导", html=html, js_api=api,
        width=SETUP_W, height=SETUP_H, x=off_x, y=off_y, resizable=False,
        frameless=True, easy_drag=False, shadow=True,
        hidden=True,                        # 关键：首帧就绪前不显示
        background_color=SETUP_BG)          # 出画前的底色 = 界面底色（浅色）
    api.bind(window)
    _trace("窗口对象已创建（屏幕外 %s，底色 %s）" % ((off_x, off_y), SETUP_BG))

    state = {"shown": False, "placed": False}

    def _place_and_show():
        """屏幕外显示 → 等首帧内容 → 移到屏幕中央。"""
        hwnd = _find_setup_hwnd()
        if not hwnd:
            window.show()
            state["shown"] = True
            return
        try:
            window.show()          # 这时窗口在屏幕外，用户看不到
        except Exception:
            pass
        for _ in range(150):       # 最多等 ~2s
            if _painted(hwnd):
                break
            time.sleep(0.012)
        state["content_ready"] = time.time()
        _trace("界面首帧内容就绪，准备移到屏幕中央")
        x, y = _center_pos()
        try:
            window.move(x, y)
        except Exception:
            import ctypes
            ctypes.windll.user32.SetWindowPos(hwnd, 0, x, y, 0, 0, 0x0001 | 0x0004)
        state["shown"] = True
        _trace("窗口已在屏幕中央显示（%s）" % ((x, y),))

    def _reveal_guard():
        """守护：loaded 事件万一没来（渲染进程异常），也要把窗口摆到中间显示，
        否则用户会看到「双击没反应」。"""
        for _ in range(80):
            if state["shown"]:
                return
            time.sleep(0.05)
        if state["shown"]:
            return
        x, y = _center_pos()
        try:
            window.move(x, y)
            window.show()
        except Exception:
            pass
        state["shown"] = True
        _trace("守护线程兜底：直接显示窗口（未等到 loaded）")

    def on_loaded():
        _trace("界面首帧就绪（loaded）")
        threading.Thread(target=_place_and_show, name="cse-setup-place",
                         daemon=True).start()

    window.events.loaded += on_loaded

    def after_start():
        threading.Thread(target=_reveal_guard, name="cse-setup-guard",
                         daemon=True).start()

    # WinForms 的 Icon 类只接受 .ico（PNG 会静默崩溃），exe 本身已内嵌图标
    ico = _resource("cse-app.ico")
    webview.start(after_start, icon=ico if os.path.isfile(ico) else None,
                  gui="edgechromium")   # 明确后端，省掉平台探测
    return 0


def _parse_args(argv):
    opts = {"dir": default_dir(), "desktop": True, "startmenu": True,
            "assoc": True, "shellmenu": True}
    silent = "--silent" in argv
    uninstall = "--uninstall" in argv
    for a in argv:
        if a.startswith("--dir="):
            opts["dir"] = os.path.abspath(a.split("=", 1)[1])
    if "--no-shortcut" in argv or "--no-desktop" in argv:
        opts["desktop"] = False
    if "--no-startmenu" in argv:
        opts["startmenu"] = False
    if "--no-assoc" in argv:
        opts["assoc"] = False
    if "--no-shellmenu" in argv:
        opts["shellmenu"] = False
    return silent, uninstall, opts


def _fatal_log(exc: BaseException):
    """把致命错误写入 %TEMP%\\CSE-Setup.log（windowed exe 唯一的排障出口）。"""
    try:
        import traceback
        log = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")),
                           "CSE-Setup.log")
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            f.write(f"argv: {sys.argv}\n")
            f.write(f"frozen: {getattr(sys, 'frozen', False)}  meipass: "
                    f"{getattr(sys, '_MEIPASS', '')}\n")
            traceback.print_exception(exc, file=f)
        return log
    except Exception:
        return None


def _fatal_box(title, text):
    """无控制台环境下的最后兜底提示。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
    except Exception:
        pass


def main():
    global BUILD_TIME_STR
    try:
        from csengine.version import BUILD_TIME
        BUILD_TIME_STR = BUILD_TIME
    except Exception:
        pass

    try:
        silent, uninstall, opts = _parse_args(sys.argv[1:])
        if uninstall:
            if silent:
                do_uninstall(dir_hint=opts["dir"])
                return 0
            return gui(mode="uninstall")
        if silent:
            m = do_install(opts)
            print(json.dumps({"ok": True, "dir": m["dir"]}, ensure_ascii=False))
            return 0
        return gui(mode="install")
    except BaseException as exc:  # noqa: BLE001
        log = _fatal_log(exc)
        _fatal_box(
            f"{PROG} 安装程序错误",
            f"安装程序启动失败：{exc}\n\n"
            f"详细日志：{log or '（日志写入失败）'}\n\n"
            f"常见原因：杀毒软件拦截（请加入白名单后重试）、"
            f"系统缺少 WebView2 运行时（https://developer.microsoft.com/microsoft-edge/webview2）。")
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
