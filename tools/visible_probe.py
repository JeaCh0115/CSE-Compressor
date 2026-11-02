"""可见性时间线探针：从进程启动起高频采样窗口是否可见（5ms 一次）。

目的：确认窗口在「首帧就绪」之前有没有短暂可见（pywebview 的 WinForms 后端
建窗时可能先 Show 再 Hide，那一瞬就是用户看到的"闪一下黑"）。
只采样 IsWindowVisible，不抓屏（锁屏时抓屏不可信）。
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

user32 = ctypes.windll.user32


def title(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def cse_windows():
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        t = title(hwnd)
        if t.startswith("CSE Compressor"):
            found.append((hwnd, bool(user32.IsWindowVisible(hwnd))))
        return True

    user32.EnumWindows(cb, 0)
    return found


def main():
    exe = sys.argv[1]
    subprocess.run(["taskkill", "/F", "/IM", "CSECompressor.exe"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    print("启动前残留窗口:", cse_windows() or "无")
    t0 = time.time()
    proc = subprocess.Popen([exe], cwd=os.path.dirname(exe))
    seen = []      # (时刻, hwnd, visible, 样式位)
    try:
        while time.time() - t0 < 4.0:
            now = time.time() - t0
            for hwnd, vis in cse_windows():
                st = user32.GetWindowLongW(hwnd, -16) & 0xFFFFFFFF
                seen.append((round(now, 3), hwnd, vis, hex(st)))
            time.sleep(0.005)
    finally:
        subprocess.run(["taskkill", "/F", "/IM", "CSECompressor.exe"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    # 压缩成「状态变化」序列，好看清有没有 可见→隐藏 的抖动
    changes = []
    last = None
    for t, hwnd, vis, st in seen:
        key = (hwnd, vis, st)
        if key != last:
            changes.append((t, hwnd, vis, st))
            last = key
    print(f"采样 {len(seen)} 帧（5ms 间隔），状态变化 {len(changes)} 次：")
    for t, hwnd, vis, st in changes:
        print(f"  {t:5.3f}s  hwnd={hwnd}  visible={vis}  style={st}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
