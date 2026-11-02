"""窗口按钮「原生提示」检测：鼠标悬停最小化/关闭时不该冒出「最小化」黑框提示。

用户反馈：安装包一打开，鼠标停在最小化上就弹出「最小化」文字提示。

为什么用截屏而不是枚举窗口：WebView2 的网页 title 提示是画在渲染层里的，
桌面上没有任何独立的 tooltip 窗口可枚举（实测 hover 前后顶层窗口数不变），
所以只能在按钮下方取一小块画面、比较 hover 前后的像素差。

  python tools/tooltip_probe.py              # 安装向导（默认）
  python tools/tooltip_probe.py --app=main    # 主程序
"""

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(ROOT, "build", "tmp")
u32 = ctypes.windll.user32
RESULTS = []

TARGETS = {
    "setup": {
        "title_has": "安装向导",
        "cmd": [sys.executable, os.path.join(ROOT, "packaging", "installer", "setup_app.py")],
        "btn": 34,          # 按钮宽
        "pad": 5,           # 标题栏右内边距
        "bar": 34,          # 标题栏高
        "gap": 0,
    },
    "main": {
        "title_has": "CSE Compressor",
        "cmd": [sys.executable, "-c",
                "import sys; from csgui.desktop import main; sys.exit(main())"],
        "btn": 42,
        "pad": 10,
        "bar": 40,
        "gap": 2,          # 最小化与关闭之间还隔着"最大化"
    },
}


def rec(ok, name, detail=""):
    RESULTS.append((ok, name, detail))
    flag = {True: "PASS", False: "FAIL", None: "SKIP"}[ok]
    print(f"[{flag}] {name}{('  —  ' + detail) if detail else ''}")


def find_window(hint):
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(hwnd, buf, 256)
        if hint in buf.value and u32.IsWindowVisible(hwnd):
            out.append(hwnd)
        return True

    u32.EnumWindows(cb, 0)
    return out[0] if out else 0


def pid_of(hwnd):
    pid = wt.DWORD()
    u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def rect_of(hwnd):
    r = wt.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def diff_ratio(a, b):
    """两张同尺寸图里，明显不同的像素占比。"""
    from PIL import ImageChops
    d = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    hist = d.convert("L").histogram()
    changed = sum(hist[20:])
    total = a.width * a.height
    return changed / max(1, total)


def _foreground(hwnd):
    """把窗口提到前台。

    窗口不在前台时 WebView2 不弹 title 提示，先挪鼠标再回来也没用；
    宿主里常有别的置顶窗口压着，这里按老办法 AttachThreadInput 三连。
    """
    if u32.GetForegroundWindow() == hwnd:
        return True
    fg = u32.GetForegroundWindow()
    tid_fg = u32.GetWindowThreadProcessId(fg, None) if fg else 0
    tid_me = ctypes.windll.kernel32.GetCurrentThreadId()
    attached = False
    if tid_fg and tid_fg != tid_me:
        attached = bool(u32.AttachThreadInput(tid_me, tid_fg, True))
    try:
        u32.BringWindowToTop(hwnd)
        u32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            u32.AttachThreadInput(tid_me, tid_fg, False)
    time.sleep(0.35)
    return u32.GetForegroundWindow() == hwnd


def probe_btn(name, hwnd, cfg, left_x, right_x, y_center):
    """把鼠标在两个 x 之间来回移，比较「按钮下方那一条」的画面变化。"""
    from PIL import ImageGrab
    _foreground(hwnd)
    l, t, r, b = rect_of(hwnd)
    # 只看按钮下方：tooltip 弹在鼠标右下，按钮自身的高亮在上方，不干扰
    box = (max(0, right_x - 110), y_center + 12,
           min(r, right_x + 75), min(b, y_center + 92))
    u32.SetCursorPos(left_x, y_center)
    time.sleep(0.8)
    base = ImageGrab.grab(bbox=box)
    _foreground(hwnd)
    u32.SetCursorPos(int(right_x), int(y_center))
    time.sleep(1.8)          # 原生提示延迟约 0.5~1s
    hot = ImageGrab.grab(bbox=box)
    ratio = diff_ratio(base, hot)
    u32.SetCursorPos(10, 10)
    tag = f"{name}"
    base.save(os.path.join(TMP, f"tooltip_{tag}_before.png"))
    hot.save(os.path.join(TMP, f"tooltip_{tag}_after.png"))
    ok = ratio < 0.03
    rec(ok, f"悬停「{name}」不弹文字提示",
        f"按钮下方画面变化 {ratio * 100:.1f}%（阈值 3%）"
        + ("" if ok else "，说明冒出了提示框"))


def main():
    app = "setup"
    for a in sys.argv[1:]:
        if a.startswith("--app="):
            app = a.split("=", 1)[1]
    cfg = TARGETS[app]
    os.makedirs(TMP, exist_ok=True)

    old = find_window(cfg["title_has"])
    if old:
        subprocess.run(["taskkill", "/F", "/PID", str(pid_of(old))], capture_output=True)
        time.sleep(0.6)

    env = dict(os.environ, PYTHONPATH=ROOT)
    proc = subprocess.Popen(cfg["cmd"], cwd=ROOT, env=env)
    try:
        t0 = time.time()
        hwnd = 0
        while time.time() - t0 < 25:
            hwnd = find_window(cfg["title_has"])
            if hwnd:
                break
            time.sleep(0.05)
        if not hwnd:
            rec(False, "窗口出现", "25 秒内没找到窗口")
            return finish(proc)
        time.sleep(2.5)      # 等界面画完，按钮才在真实位置
        l, t, r, b = rect_of(hwnd)
        rec(True, "窗口出现", f"rect=({l},{t},{r},{b})")

        btn, pad, bar = cfg["btn"], cfg["pad"], cfg["bar"]
        close_x = r - pad - btn // 2
        min_x = close_x - btn * (cfg["gap"] if cfg["gap"] > 1 else 1)
        y = t + bar // 2
        # 只测最小化：它是用户实际碰到的那一个，而且右侧没有别的按钮干扰判断
        probe_btn("最小化", hwnd, cfg, 10, min_x, y)
        return finish(proc)
    finally:
        pass


def finish(proc):
    try:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    passed = sum(1 for o, _, _ in RESULTS if o is True)
    failed = [n for o, n, _ in RESULTS if o is False]
    print(f"\n通过 {passed}/{len(RESULTS)}" + (f"，失败：{failed}" if failed else ""))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
