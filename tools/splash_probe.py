"""启动首帧探针（截屏版）—— 已被 frame_probe.py / visible_probe.py 取代。

教训：本机外部进程抓屏对 WebView2 内容恒为空白（PrintWindow / GetWindowDC /
GetDIBits / PIL.ImageGrab 都试过），而且会话锁屏时抓到的只有锁屏壁纸，
据此判定"有没有黑屏"会得到假结论。现在判据改成两条可复现的机制证据：
  · tools/frame_probe.py   应用自报时间线：loaded 必须早于 show
  · tools/visible_probe.py 5ms 采样 IsWindowVisible：首帧前窗口一次都不许可见
本文件保留仅作历史参考。

做法：
  1. 启动前先截一张全屏做基线（桌面壁纸）；
  2. 启动 exe，轮询窗口句柄（要求**连续两次**都可见，避开 pywebview 建窗时
     Show→Hide 那一瞬）；
  3. 窗口稳定可见后，每 ~30ms 截窗口矩形，与基线同位置比对：
     与桌面明显不同的像素占比 > 15% 才算"窗口真的画出来了"。

判据不能只看亮度：桌面壁纸也可能很"亮"，那样会把没画出来的窗口误判成已绘制。

用法：python tools/splash_probe.py [exe路径] [--runs=3]
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
user32 = ctypes.windll.user32


def find_window():
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
    return found[0] if found else 0


def stable_window(timeout=8.0):
    """连续两次探测都可见才算数（建窗瞬间会有一帧 Show→Hide）。"""
    t0 = time.time()
    last = 0
    while time.time() - t0 < timeout:
        h = find_window()
        if h and h == last and user32.IsWindowVisible(h):
            return h
        last = h
        time.sleep(0.05)
    return 0


def rect_of(hwnd):
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def crop(im, rect):
    left, top, right, bottom = rect
    w, h = im.size
    left, top = max(0, left), max(0, top)
    right, bottom = min(w, right), min(h, bottom)
    if right - left < 8 or bottom - top < 8:
        return None
    return im.crop((left, top, right, bottom)).convert("L").resize((160, 90))


def diff_ratio(a, b):
    if a is None or b is None:
        return 0.0
    pa, pb = list(a.getdata()), list(b.getdata())
    n = min(len(pa), len(pb))
    diff = sum(1 for i in range(n) if abs(pa[i] - pb[i]) > 12)
    return diff / max(n, 1)


def kill():
    subprocess.run(["taskkill", "/F", "/IM", "CSECompressor.exe"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_once(exe):
    from PIL import ImageGrab

    kill()
    time.sleep(0.6)
    baseline = ImageGrab.grab().convert("L")
    t0 = time.time()
    proc = subprocess.Popen([exe], cwd=os.path.dirname(exe))
    hwnd = stable_window()
    visible_at = time.time() - t0 if hwnd else None
    painted_at = None
    samples = []
    try:
        if hwnd:
            rect = rect_of(hwnd)
            ref = crop(baseline, rect)
            while time.time() - t0 - (visible_at or 0) < 6:
                now = time.time() - t0
                cur = crop(ImageGrab.grab(), rect_of(hwnd))
                ratio = diff_ratio(ref, cur)
                samples.append((round(now, 3), round(ratio, 3)))
                if ratio > 0.15:
                    painted_at = now
                    break
                time.sleep(0.03)
    finally:
        kill()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    return {"visible": visible_at, "paint": painted_at, "samples": samples}


def latest_exe():
    base = os.path.join(ROOT, "packaging", "build", "dist")
    stages = sorted(os.listdir(base), reverse=True) if os.path.isdir(base) else []
    for stage in stages:
        p = os.path.join(base, stage, "CSECompressor", "CSECompressor.exe")
        if os.path.isfile(p):
            return p
    return ""


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    runs = 3
    for a in sys.argv[1:]:
        if a.startswith("--runs="):
            runs = int(a.split("=", 1)[1])
    exe = args[0] if args else latest_exe()
    if not os.path.isfile(exe):
        raise SystemExit(f"找不到 exe：{exe}")
    print(f"测量目标: {exe}")
    bad = 0
    for i in range(runs):
        r = run_once(exe)
        v, p = r["visible"], r["paint"]
        if v is None:
            print(f"  第 {i + 1} 次: 窗口未出现")
            bad += 1
            continue
        gap = (p - v) if p else None
        print(f"  第 {i + 1} 次: 窗口稳定可见 {v:.2f}s · 画面已绘制 "
              f"{'—' if p is None else f'{p:.2f}s'} · 空白窗口 "
              f"{'超时' if gap is None else f'{gap * 1000:.0f}ms'}")
        print(f"        (时刻, 与桌面差异比例): {r['samples'][:8]}")
        if gap is None or gap > 0.25:
            bad += 1
        time.sleep(0.4)
    print("\n结论:", "启动无黑屏/空白帧" if not bad else f"{bad} 次出现明显空白窗口")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
