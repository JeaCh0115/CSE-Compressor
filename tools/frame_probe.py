"""启动首帧探针（基于应用自报的时间线，不依赖外部截屏）。

为什么不用截屏：实测这台机器上，外部进程抓屏对 WebView2 内容恒为空白/壁纸
（PrintWindow、GetWindowDC、GetDIBits、PIL.ImageGrab 都试过），据此判定
"有没有黑屏"会得到假结论。

改用可复现的机制证据：
  1. 启动 exe 时带上 CSE_TIMING=1，应用会把关键节点写进 %TEMP%\\CSE-startup.log；
  2. 断言顺序：`启动页首帧就绪（loaded）` 必须早于 `窗口已显示（启动页首帧就绪）`
     —— 窗口在首帧画出来之前根本没被 Show()，屏幕上看不到任何东西，
        自然不会有"先黑一块再亮"的闪烁；
  3. 顺便量出 loaded → show 的间隔（越小越利落）与窗口出现总耗时。

用法：python tools/frame_probe.py [exe路径] [--runs=3]
"""

import ctypes
import ctypes.wintypes as wt
import os
import re
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(os.environ.get("TEMP") or tempfile.gettempdir(), "CSE-startup.log")

PAINT = "启动页首帧就绪（loaded）"
SHOWN = "窗口已显示（启动页首帧就绪）"
SHOWN_ALT = "窗口已显示"

_TS = re.compile(r"^\s*(\d+\.\d+)s\s+\(\+\s*(\d+\.\d+)s\)\s+(.*)$")


def find_window_once():
    user32 = ctypes.windll.user32
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


def kill_all(name="CSECompressor"):
    subprocess.run(["taskkill", "/F", "/IM", name + ".exe"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def parse_log():
    """返回 [(秒, 文本)]；文件不在就返回空表。"""
    out = []
    try:
        with open(LOG, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = _TS.match(line)
                if m:
                    out.append((float(m.group(1)), m.group(3).strip()))
    except OSError:
        pass
    return out


def first_time(rows, key):
    for t, text in rows:
        if text.startswith(key):
            return t
    return None


def run_once(exe):
    kill_all()
    time.sleep(0.5)
    try:
        os.remove(LOG)
    except OSError:
        pass
    env = dict(os.environ, CSE_TIMING="1")
    t0 = time.time()
    proc = subprocess.Popen([exe], cwd=os.path.dirname(exe), env=env)
    win_t = None
    try:
        while time.time() - t0 < 20:
            if find_window_once():
                win_t = time.time() - t0
                break
            time.sleep(0.01)
        # 等正式界面也载完，日志写全
        time.sleep(2.2)
    finally:
        kill_all()          # 直接杀进程，避免 WM_CLOSE 触发 _save_geometry 污染窗口记忆
        try:
            proc.wait(timeout=6)
        except Exception:
            proc.kill()
    rows = parse_log()
    return {
        "window": win_t,
        "paint": first_time(rows, PAINT),
        "shown": first_time(rows, SHOWN) if first_time(rows, SHOWN) is not None
                 else first_time(rows, SHOWN_ALT),
        "marks": rows,
    }


def _latest_exe():
    base = os.path.join(ROOT, "packaging", "build", "dist")
    if os.path.isdir(base):
        for stage in sorted(os.listdir(base), reverse=True):
            p = os.path.join(base, stage, "CSECompressor", "CSECompressor.exe")
            if os.path.isfile(p):
                return p
    return r"C:\Program Files\CSE Compressor\CSECompressor.exe"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    runs = 3
    for a in sys.argv[1:]:
        if a.startswith("--runs="):
            runs = int(a.split("=", 1)[1])
    exe = args[0] if args else _latest_exe()
    tmp = None
    if not os.path.isfile(exe):
        dist = os.path.join(ROOT, "dist")
        cand = [f for f in os.listdir(dist) if f.endswith("-Portable.zip")] if os.path.isdir(dist) else []
        if not cand:
            raise SystemExit(f"找不到 exe：{exe}")
        cand.sort(key=lambda f: os.path.getmtime(os.path.join(dist, f)))
        tmp = tempfile.mkdtemp(prefix="cse_frame_")
        zipfile.ZipFile(os.path.join(dist, cand[-1])).extractall(tmp)
        exe = os.path.join(tmp, "CSECompressor.exe")
    print(f"测量目标: {exe}\n" + "-" * 64)

    bad = 0
    for i in range(runs):
        r = run_once(exe)
        paint, shown, win = r["paint"], r["shown"], r["window"]
        tag = f"  第 {i + 1} 次"
        if win is None:
            print(f"{tag}: 窗口没出现（启动失败）")
            bad += 1
            continue
        if paint is None:
            print(f"{tag}: 窗口 {win:.2f}s 可见，但日志里没有首帧标记"
                  f"（CSE_TIMING 未生效？共 {len(r['marks'])} 条记录）")
            bad += 1
            continue
        if shown is None:
            print(f"{tag}: 窗口 {win:.2f}s 可见，缺『窗口已显示』记录")
            bad += 1
            continue
        gap = shown - paint
        ok = gap >= -0.001
        if not ok:
            bad += 1
        print(f"{tag}: 窗口可见 {win:.2f}s · 首帧就绪(loaded) {paint:.3f}s · "
              f"窗口 Show {shown:.3f}s")
        print(f"        loaded → show 间隔 {gap * 1000:+.0f}ms · "
              f"{'OK 先出画后亮窗' if ok else 'FAIL 亮窗早于出画'}")
        time.sleep(0.4)

    print("-" * 64)
    if bad:
        print(f"结论：{bad}/{runs} 次不满足『先出画后亮窗』，仍有黑屏风险")
    else:
        print("结论：全部满足『先出画后亮窗』——窗口亮出来时启动页已在位，无黑屏帧")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
