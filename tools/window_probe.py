"""原生窗口行为探针：默认尺寸 / 可拉伸 / 任务栏点击最小化 / 最大化还原。

直接对真实运行的窗口做 Win32 断言，而不是看代码写了什么。

用法：
  python tools/window_probe.py [exe路径]     默认取最新便携版构建
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
user32 = ctypes.windll.user32

GWL_STYLE = -16
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_THICKFRAME = 0x00040000
SW_RESTORE, SW_MAXIMIZE, SW_MINIMIZE = 9, 3, 6
WM_SYSCOMMAND, SC_MINIMIZE = 0x0112, 0xF020
WM_CLOSE = 0x0010
WM_NCHITTEST = 0x0084
HT_NAMES = {1: "HTCLIENT", 10: "HTLEFT", 11: "HTRIGHT", 12: "HTTOP", 13: "HTTOPLEFT",
            14: "HTTOPRIGHT", 15: "HTBOTTOM", 16: "HTBOTTOMLEFT", 17: "HTBOTTOMRIGHT"}


def _latest_exe():
    """取最近一次构建的 CSECompressor.exe。"""
    base = os.path.join(ROOT, "packaging", "build", "dist")
    if os.path.isdir(base):
        stages = sorted(os.listdir(base))
        for stage in reversed(stages):
            p = os.path.join(base, stage, "CSECompressor", "CSECompressor.exe")
            if os.path.isfile(p):
                return p
    portable = [f for f in os.listdir(os.path.join(ROOT, "dist"))
                if f.endswith("-Portable.zip")]
    portable.sort(key=lambda f: os.path.getmtime(os.path.join(ROOT, "dist", f)))
    tmp = tempfile.mkdtemp(prefix="cse_win_")
    zipfile.ZipFile(os.path.join(ROOT, "dist", portable[-1])).extractall(tmp)
    return os.path.join(tmp, "CSECompressor.exe")


def find_window(timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def cb(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value.startswith("CSE Compressor"):
                found.append(hwnd)
            return True

        user32.EnumWindows(cb, 0)
        if found:
            return found[0]
        time.sleep(0.03)
    return None


def rect(hwnd):
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


def style(hwnd):
    return user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF


def hit_test(hwnd, x, y):
    """在屏幕坐标 (x,y) 上问系统：此处命中窗口的哪个部件。"""
    lparam = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
    return user32.SendMessageW(hwnd, WM_NCHITTEST, 0, lparam) & 0xFFFF


def drag_mouse(x0, y0, x1, y1, steps=24):
    """真实鼠标拖动（不是 SetWindowPos）：验证用户实际能不能拉动边缘。"""
    user32 = ctypes.windll.user32
    user32.SetCursorPos(int(x0), int(y0))
    time.sleep(0.35)
    user32.mouse_event(0x0002, 0, 0, 0, 0)      # LEFTDOWN
    time.sleep(0.12)
    for i in range(1, steps + 1):
        user32.SetCursorPos(int(x0 + (x1 - x0) * i / steps),
                            int(y0 + (y1 - y0) * i / steps))
        time.sleep(0.02)
    time.sleep(0.12)
    user32.mouse_event(0x0004, 0, 0, 0, 0)      # LEFTUP
    time.sleep(0.4)


def dpi_scale(hwnd):
    try:
        dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
        return dpi / 96.0 if dpi else 1.0
    except Exception:
        return 1.0


def _settings_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "CSE Compressor", "settings.json")


def _reset_geometry():
    """临时清掉窗口几何记忆，才能验证"首次启动默认 860×500"。返回原始 JSON 文本。"""
    p = _settings_path()
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            raw = f.read()
        data = json.loads(raw)
        if "window" in data:
            del data["window"]
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        return raw
    except (OSError, ValueError):
        return None


def _restore_settings(raw):
    if raw is None:
        return
    try:
        with open(_settings_path(), "w", encoding="utf-8") as f:
            f.write(raw)
    except OSError:
        pass


def main():
    exe = sys.argv[1] if len(sys.argv) > 1 else _latest_exe()
    print(f"测量目标: {exe}")
    passed, failed, skipped = [], [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))

    def skip(name, why):
        skipped.append(name)
        print(f"SKIP {name}  [{why}]")

    raw_settings = _reset_geometry()      # 清掉几何记忆，验证首次启动的默认尺寸
    proc = subprocess.Popen([exe], cwd=os.path.dirname(exe))
    try:
        hwnd = find_window()
        if not hwnd:
            print("FAIL 未找到窗口")
            return 1
        time.sleep(2.5)                     # 等页面与服务就绪
        st = style(hwnd)
        w, h = rect(hwnd)
        s = dpi_scale(hwnd)

        check("窗口可见", bool(user32.IsWindowVisible(hwnd)), f"hwnd={hwnd}")
        check("默认尺寸 860×500", abs(w - 860) <= 6 and abs(h - 500) <= 6,
              f"实测 {w}×{h}（DPI {s * 100:.0f}%，按物理像素计）")
        check("样式位含 WS_MINIMIZEBOX（任务栏点击可最小化）", bool(st & WS_MINIMIZEBOX), hex(st))
        check("样式位含 WS_MAXIMIZEBOX", bool(st & WS_MAXIMIZEBOX), hex(st))

        # 边缘命中测试 = 鼠标拖边能否拉伸（无边框窗口靠 WM_NCHITTEST 实现）
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        mx, my = (r.left + r.right) // 2, (r.top + r.bottom) // 2
        hits = {
            "左": hit_test(hwnd, r.left + 2, my),
            "右": hit_test(hwnd, r.right - 3, my),
            "上": hit_test(hwnd, mx, r.top + 2),
            "下": hit_test(hwnd, mx, r.bottom - 3),
            "中": hit_test(hwnd, mx, my),
        }
        named = {k: HT_NAMES.get(v, str(v)) for k, v in hits.items()}
        check("四边命中 HT*（可鼠标拉伸）",
              hits["左"] == 10 and hits["右"] == 11 and hits["上"] == 12 and hits["下"] == 15,
              json.dumps(named, ensure_ascii=False))
        check("窗口内部仍为 HTCLIENT（不误挡点击）", hits["中"] == 1, named["中"])

        # 最大化 → 还原
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
        time.sleep(0.9)
        mw, mh = rect(hwnd)
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.9)
        rw, rh = rect(hwnd)
        check("最大化后能还原到原尺寸", abs(rw - w) <= 8 and abs(rh - h) <= 8,
              f"最大化 {mw}×{mh} → 还原 {rw}×{rh}（原 {w}×{h}）")

        # 模拟任务栏按钮点击：向窗口发送 SC_MINIMIZE
        user32.PostMessageW(hwnd, WM_SYSCOMMAND, SC_MINIMIZE, 0)
        time.sleep(0.9)
        icon = bool(user32.IsIconic(hwnd))
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.6)
        check("最小化命令生效（任务栏点击可收起）", icon, f"IsIconic={icon}")

        # 真实鼠标拖右边缘：前端热区 → 原生自控缩放（SetWindowPos 能改尺寸不算数）
        user32.SetForegroundWindow(hwnd)   # 非前台时第一次点击只会激活窗口
        time.sleep(0.5)
        rc = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rc))
        user32.SetCursorPos((rc.left + rc.right) // 2, (rc.top + rc.bottom) // 2)
        time.sleep(1.0)                    # 等最小化还原后前端事件重新就绪
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        before = r.right - r.left
        if user32.GetForegroundWindow() != hwnd:
            # 锁屏 / 远程断开 / 有别的窗口抢焦点时，鼠标事件根本到不了本窗口，
            # 此时判 FAIL 只会误报（实测锁屏时前台是「Windows 默认锁屏界面」）。
            why = "当前会话拿不到前台（锁屏或非交互），鼠标事件到不了窗口"
            skip("鼠标拖右边缘能拉伸（真实拖动）", why)
            skip("鼠标拖下边缘能拉伸（真实拖动）", why)
        else:
            drag_mouse(r.right - 3, (r.top + r.bottom) // 2,
                       r.right + 160, (r.top + r.bottom) // 2)
            nw, nh = rect(hwnd)
            check("鼠标拖右边缘能拉伸（真实拖动）", nw - before > 80, f"{before} → {nw}")

            user32.GetWindowRect(hwnd, ctypes.byref(r))
            before_h = r.bottom - r.top
            drag_mouse((r.left + r.right) // 2, r.bottom - 2,
                       (r.left + r.right) // 2, r.bottom + 120)
            nw2, nh2 = rect(hwnd)
            check("鼠标拖下边缘能拉伸（真实拖动）", nh2 - before_h > 60,
                  f"{before_h} → {nh2}")

        user32.ReleaseCapture()
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        time.sleep(2.0)
        check("关闭后进程退出", proc.poll() is not None, f"poll={proc.poll()}")
    finally:
        if proc.poll() is None:
            proc.kill()
        _restore_settings(raw_settings)

    print(f"\n通过 {len(passed)} 项，失败 {len(failed)} 项"
          + (f"，跳过 {len(skipped)} 项" if skipped else ""))
    if skipped:
        print("跳过项：" + "；".join(skipped) + "（需在解锁的桌面上重跑才能判定）")
    if failed:
        print("失败项：" + "；".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
