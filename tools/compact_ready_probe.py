"""紧凑小窗「一次到位」探针：验证右键压缩/解压不会先闪一块空白窗。

用户反馈的现象是「选中的文件先闪一下，然后压缩界面什么都没有闪一下，才出现
压缩界面」。原因有两层：
  1) 窗口先显示启动页，再 load_url 换正式界面 —— 中间那一下是空的；
  2) 窗口一出现就抢焦点，资源管理器里的选中高亮先褪色一次。
现在的做法：紧凑窗建在虚拟桌面之外，SW_SHOWNOACTIVATE 预热（不抢焦点），
前端把面板铺好后调 compact_ready，桌面外壳才把它挪到屏幕中央。

探针据此断言（用 PrintWindow 采样，锁屏也能跑）：
  1) 窗口创建时在屏幕外（用户看不到热身过程）；
  2) 屏幕外期间窗口确实画出了内容（不然挪进来还是白屏）；
  3) 挪到屏幕上的那一刻，中心区域已有内容（>3 种颜色，纯底色 1~2 种）；
  4) 屏幕上从未出现过空白窗口（空帧计数 = 0）；
  5) 最终尺寸等于对应紧凑尺寸，且居中。

用法：
  python tools/compact_ready_probe.py --kind=add
  python tools/compact_ready_probe.py --kind=extract
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(ROOT, "build", "tmp")
LOG = os.path.join(os.environ.get("TEMP", "."), "CSE-startup.log")
PORT = 9337
u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32
RESULTS = []


def rec(ok, name, detail=""):
    RESULTS.append((ok, name, detail))
    flag = {True: "PASS", False: "FAIL", None: "SKIP"}[ok]
    print(f"[{flag}] {name}{('  —  ' + detail) if detail else ''}")


def virtual_screen():
    g = u32.GetSystemMetrics
    return g(76), g(77), g(78), g(79)


def find_hwnd():
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(hwnd, buf, 256)
        if buf.value.startswith("CSE Compressor"):
            found.append(hwnd)
        return True

    u32.EnumWindows(cb, 0)
    return found[0] if found else 0


def rect_of(hwnd):
    r = wt.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def on_screen(rect):
    vx, vy, vw, vh = virtual_screen()
    l, t, r, b = rect
    return not (r < vx or b < vy or l > vx + vw or t > vy + vh)


class BIH(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_ulong), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_ushort),
                ("biBitCount", ctypes.c_ushort), ("biCompression", ctypes.c_ulong),
                ("biSizeImage", ctypes.c_ulong), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_ulong),
                ("biClrImportant", ctypes.c_ulong)]


class BI(ctypes.Structure):
    _fields_ = [("bmiHeader", BIH), ("bmiColors", ctypes.c_ulong * 3)]


def content_colors(hwnd, save=None):
    """PrintWindow 抓一帧，返回中心区域颜色种类（纯底色 1~2 种）。"""
    from PIL import Image
    l, t, r, b = rect_of(hwnd)
    w, h = r - l, b - t
    if w < 80 or h < 80:
        return 0
    hdc = u32.GetWindowDC(hwnd)
    mem = g32.CreateCompatibleDC(hdc)
    bmp = g32.CreateCompatibleBitmap(hdc, w, h)
    g32.SelectObject(mem, bmp)
    bi = BI()
    bi.bmiHeader.biSize = ctypes.sizeof(BIH)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    u32.PrintWindow(hwnd, mem, 0x2)   # PW_RENDERFULLCONTENT
    g32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    g32.DeleteObject(bmp)
    g32.DeleteDC(mem)
    u32.ReleaseDC(hwnd, hdc)
    im = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    if save:
        im.save(save)
    crop = im.crop((w // 2 - 140, h // 2 - 70, w // 2 + 140, h // 2 + 70))
    return len(crop.getcolors(maxcolors=200000) or [])


def _all_windows():
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(hwnd, buf, 256)
        if buf.value.startswith("CSE Compressor"):
            pid = wt.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            out.append(pid.value)
        return True

    u32.EnumWindows(cb, 0)
    return out


def kill_stale():
    for pid in set(_all_windows()):
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True)


def child():
    sys.path.insert(0, ROOT)
    kind = os.environ.get("CSE_PROBE_KIND", "add")
    paths = json.loads(os.environ.get("CSE_PROBE_PATHS", "[]"))
    import webview
    from csgui.server import set_launch_request

    set_launch_request({"action": kind, "paths": paths})
    if os.environ.get("CSE_PROBE_CDP") == "1":
        webview.settings["REMOTE_DEBUGGING_PORT"] = PORT
    from csgui.desktop import main as desktop_main
    return desktop_main(debug=os.environ.get("CSE_PROBE_CDP") == "1")


def make_fixtures(kind):
    d = os.path.join(tempfile.gettempdir(), "cse_ready_test")
    os.makedirs(d, exist_ok=True)
    src = os.path.join(d, "报告草稿.txt")
    if not os.path.exists(src):
        with open(src, "w", encoding="utf-8") as f:
            f.write("hello compact\n" * 50)
    if kind == "extract":
        arc = os.path.join(d, "样例归档.zip")
        if not os.path.exists(arc):
            import zipfile
            with zipfile.ZipFile(arc, "w") as z:
                z.writestr("a.txt", "a" * 100)
                z.writestr("b/c.txt", "c" * 100)
        return [arc]
    return [src]


def cdp_eval(expr, timeout=30):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from cdp_client import WS, pick_page
    t = pick_page(PORT, timeout)
    ws = WS(t["webSocketDebuggerUrl"])
    ws.call("Runtime.enable")
    return ws.eval(expr)


def parent(kind):
    os.makedirs(TMP, exist_ok=True)
    paths = make_fixtures(kind)
    print(f"场景: {kind}  文件: {paths}")
    kill_stale()
    time.sleep(0.4)
    if os.path.isfile(LOG):
        os.remove(LOG)
    env = dict(os.environ, CSE_PROBE_KIND=kind, CSE_PROBE_CDP="1",
               CSE_PROBE_PATHS=json.dumps(paths), CSE_TIMING="1",
               PYTHONPATH=ROOT)
    t0 = time.time()
    proc = subprocess.Popen([sys.executable, os.path.abspath(__file__)],
                            cwd=ROOT, env=env)
    try:
        hwnd = 0
        while time.time() - t0 < 25:
            hwnd = find_hwnd()
            if hwnd and u32.IsWindowVisible(hwnd):
                break
            time.sleep(0.005)
        if not hwnd:
            rec(False, "紧凑窗口创建", "25 秒内没找到窗口")
            return finish(proc)

        first = rect_of(hwnd)
        rec(not on_screen(first), "建窗位置在屏幕外（热身时不露空白）",
            f"rect={first}；虚拟桌面={virtual_screen()}")

        # 屏幕外阶段：等它真正出画（证明屏幕外 WebView2 也在渲染）
        off_painted_at, off_colors = None, 0
        while time.time() - t0 < 12:
            if on_screen(rect_of(hwnd)):
                break
            n = content_colors(hwnd)
            if n > 3:
                off_painted_at, off_colors = time.time() - t0, n
                break
            time.sleep(0.02)
        rec(off_painted_at is not None, "屏幕外期间已画出内容（不是挪进来才画）",
            f"{off_painted_at:.2f}s 时 {off_colors} 种颜色" if off_painted_at else "屏幕外始终空白")

        # 跟踪挪入时刻
        placed = None
        blank_on_screen = []
        shot_done = False
        while time.time() - t0 < 20:
            rect = rect_of(hwnd)
            if on_screen(rect):
                n = content_colors(hwnd, save=None if shot_done else os.path.join(
                    TMP, f"compact_ready_{kind}.png"))
                shot_done = True
                if placed is None:
                    placed = (time.time() - t0, n)
                if n <= 3:
                    blank_on_screen.append((round(time.time() - t0, 2), n))
                if n > 3:
                    break
            time.sleep(0.01)

        if placed is None:
            rec(False, "窗口最终出现在屏幕上", "20 秒内一直停在屏幕外")
            return finish(proc)
        t_place, n_place = placed
        rec(t_place < 18, "窗口出现在屏幕上", f"进程启动后 {t_place:.2f}s")
        rec(n_place > 3, "出现在屏幕上时面板已画好（不闪空白）",
            f"那一刻中心区域 {n_place} 种颜色")
        rec(not blank_on_screen, "屏幕上从未出现空白窗口",
            f"{len(blank_on_screen)} 次空帧" if blank_on_screen else "0 次空帧")

        w = rect[2] - rect[0]
        h = rect[3] - rect[1]
        want = (560, 560) if kind == "add" else (540, 420)
        rec((w, h) == want, "窗口尺寸符合紧凑尺寸", f"{w}x{h}（期望 {want[0]}x{want[1]}）")

        vx, vy, vw, vh = virtual_screen()
        cx = (rect[0] + rect[2]) // 2
        cy = (rect[1] + rect[3]) // 2
        rec(abs(cx - (vx + vw // 2)) <= 40 and abs(cy - (vy + vh // 2)) <= 60,
            "窗口居中", f"中心 ({cx},{cy})，屏幕中心 ({vx + vw // 2},{vy + vh // 2})")

        # DOM 侧：解压要能看到「解压到」和「选择位置」
        try:
            dom = cdp_eval("""(() => {
              const q = (s) => document.querySelector(s);
              return {
                compact: document.body.classList.contains('compact'),
                dlg: !q('#dlg').hidden,
                title: (q('#dlgTitle') || {}).textContent || '',
                dest: q('#xDest') ? q('#xDest').value : null,
                pick: !!q('#xDestPick'),
                start: !!q('#xStart'),
                emptyBody: (q('#dlgBody') || {}).innerHTML === '',
              };
            })()""", timeout=25)
            if kind == "extract":
                rec(bool(dom.get("dlg")), "解压设置面板已打开", str(dom.get("title")))
                rec(bool(dom.get("dest")), "面板里有「解压到」目标目录",
                    str(dom.get("dest")))
                rec(bool(dom.get("pick")), "面板里有「选择位置」按钮")
                rec((dom.get("dest") or "").endswith("_解压"), "默认目标是「名称_解压」")
            else:
                rec(bool(dom.get("dlg")), "压缩设置面板已打开", str(dom.get("title")))
        except Exception as e:
            rec(None, "DOM 检查", f"CDP 不可用：{e}")

        marks = trace_marks()
        rec(None, "时序（trace）", marks)
    finally:
        return finish(proc)


def trace_marks():
    out = []
    if not os.path.isfile(LOG):
        return "无日志"
    for line in open(LOG, encoding="utf-8", errors="ignore"):
        if any(k in line for k in ("紧凑", "已载入正式界面", "已显示")):
            out.append(line.strip())
    return " | ".join(out[-4:]) or "无相关记录"


def finish(proc):
    try:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    passed = sum(1 for ok, _, _ in RESULTS if ok is True)
    failed = [n for ok, n, _ in RESULTS if ok is False]
    print(f"\n通过 {passed}/{len(RESULTS)}" + (f"，失败：{failed}" if failed else ""))
    return 0 if not failed else 1


if __name__ == "__main__":
    if os.environ.get("CSE_PROBE_KIND"):
        sys.exit(child() or 0)
    kind = "add"
    for a in sys.argv[1:]:
        if a.startswith("--kind="):
            kind = a.split("=", 1)[1]
    sys.exit(parent(kind))
