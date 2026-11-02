"""安装程序真机探针：验证「打开不闪白」+ 紧凑尺寸。

原理（不依赖屏幕解锁，锁屏也能跑）：
  安装窗口先建在**所有屏幕之外**热身，等 WebView2 画出首帧内容（PrintWindow 抓到
  多种颜色）后才挪到屏幕中央。探针据此断言三件事：
    1) 窗口在屏幕外时不算数：任何时候都不能有「已出现在用户屏幕上但内容还是空白的窗口」；
    2) 挪到中央的那一帧，窗口里已经有真实内容；
    3) 时序上 loaded/热身 早于「移到中央」。

用法：
  python tools/setup_probe.py                       # 开发态（python setup_app.py）
  python tools/setup_probe.py --exe dist\\...Setup.exe
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(ROOT, "build", "tmp")
LOG = os.path.join(os.environ.get("TEMP", "."), "CSE-setup.log")
TITLE_HINT = "安装向导"
u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32
RESULTS = []


def rec(ok, name, detail=""):
    RESULTS.append((ok, name, detail))
    flag = {True: "PASS", False: "FAIL", None: "SKIP"}[ok]
    print(f"[{flag}] {name}{('  —  ' + detail) if detail else ''}")


def virtual_screen():
    gsm = u32.GetSystemMetrics
    return gsm(76), gsm(77), gsm(78), gsm(79)


def find_hwnd():
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(hwnd, buf, 256)
        if TITLE_HINT in buf.value and "CSE" in buf.value:
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


def content_colors(hwnd):
    """PrintWindow 抓一帧，中心区域的颜色种类（纯底色 1~2 种）。"""
    from PIL import Image
    l, t, r, b = rect_of(hwnd)
    w, h = r - l, b - t
    if w < 80 or h < 80:
        return 0, None
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
    crop = im.crop((w // 2 - 140, h // 2 - 70, w // 2 + 140, h // 2 + 70))
    return len(crop.getcolors(maxcolors=200000) or []), im


def parse_trace():
    marks = {}
    if not os.path.isfile(LOG):
        return marks
    for line in open(LOG, encoding="utf-8", errors="ignore"):
        parts = line.strip().split(" ", 1)
        if len(parts) != 2:
            continue
        ts, msg = parts
        try:
            ts = float(ts)
        except ValueError:
            continue
        if "loaded" in msg:
            marks.setdefault("loaded", ts)
        if "移到屏幕中央" in msg and "准备" in msg:
            marks.setdefault("ready", ts)
        if "屏幕中央显示" in msg:
            marks.setdefault("placed", ts)
    return marks


def main():
    os.makedirs(TMP, exist_ok=True)
    exe = ""
    for a in sys.argv[1:]:
        if a.startswith("--exe="):
            exe = a.split("=", 1)[1]

    if os.path.isfile(LOG):
        os.remove(LOG)
    env = dict(os.environ, CSE_SETUP_TIMING="1", PYTHONPATH=ROOT)
    if exe:
        cmd, cwd = [os.path.abspath(exe)], os.path.dirname(os.path.abspath(exe))
    else:
        cmd = [sys.executable, os.path.join(ROOT, "packaging", "installer", "setup_app.py")]
        cwd = ROOT

    t0 = time.time()
    proc = subprocess.Popen(cmd, cwd=cwd, env=env)
    print(f"启动: {' '.join(cmd)}")

    hwnd = 0
    while time.time() - t0 < 25:
        hwnd = find_hwnd()
        if hwnd and u32.IsWindowVisible(hwnd):
            break
        time.sleep(0.005)
    if not hwnd:
        rec(False, "安装向导窗口创建", "25 秒内没找到窗口")
        return finish(proc)
    rec(True, "安装向导窗口创建", f"进程启动后 {(time.time() - t0):.2f}s")

    first_rect = rect_of(hwnd)
    rec(not on_screen(first_rect), "热身位置在屏幕外（用户看不到空白窗）",
        f"rect={first_rect}；虚拟桌面={virtual_screen()}")

    # 跟踪：窗口什么时候真的挪到屏幕上，以及那一刻里面有没有内容
    placed = None
    blank_on_screen = []
    while time.time() - t0 < 20:
        rect = rect_of(hwnd)
        if on_screen(rect):
            n, im = content_colors(hwnd)
            if placed is None:
                placed = (time.time() - t0, n)
                if im is not None:
                    im.save(os.path.join(TMP, "setup_probe_placed.png"))
            if n <= 3:
                blank_on_screen.append((round(time.time() - t0, 2), n))
            if n > 3:
                break
        time.sleep(0.01)

    if placed is None:
        rec(False, "窗口最终出现在屏幕上", "20 秒内窗口一直没移到可见位置")
        return finish(proc)

    t_place, n_place = placed
    rec(t_place < 18, "窗口最终出现在屏幕上", f"进程启动后 {t_place:.2f}s")
    rec(n_place > 3, "出现在屏幕上时界面已画好（不闪白）",
        f"那一刻中心区域 {n_place} 种颜色（纯底色 1~2 种）")
    rec(not blank_on_screen, "屏幕上从未出现空白窗口",
        f"{len(blank_on_screen)} 次空帧" if blank_on_screen else "0 次空帧")

    marks = parse_trace()
    if "placed" not in marks:
        rec(None, "时序（loaded → 移到中央）", "trace 未记录（可能未开启计时）")
    else:
        gap = (marks["placed"] - marks.get("loaded", marks["placed"])) * 1000
        rec(gap >= 0, "时序：先画好再挪到中央", f"loaded → 移入 {gap:.0f}ms")

    size = rect_of(hwnd)
    w, h = size[2] - size[0], size[3] - size[1]
    rec(w <= 640 and h <= 460, "窗口尺寸紧凑", f"{w}x{h}（含边框）")

    css = open(os.path.join(ROOT, "packaging", "installer", "ui.html"), encoding="utf-8").read()
    src = open(os.path.join(ROOT, "packaging", "installer", "setup_app.py"), encoding="utf-8").read()
    rec('SETUP_BG = "#f5f6f8"' in src and "--bg:#f5f6f8" in css.replace(" ", ""),
        "窗口底色与界面底色一致")

    return finish(proc)


def finish(proc):
    try:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    bad = [r for r in RESULTS if r[0] is False]
    print("\n" + "=" * 60)
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过"
          + (f"，{len(bad)} 项失败" if bad else "，全部通过"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
