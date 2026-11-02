"""抓真实窗口内容（桌面解锁时可用）。

用法：python tools/shot_window.py [输出png] [--exe=路径]
先启动应用（若未启动），等窗口稳定后整屏截图并按窗口矩形裁剪。
注意：必须在**解锁**的桌面上跑——锁屏时只能抓到锁屏壁纸。
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

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


def main():
    out = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") \
        else os.path.join(os.environ.get("TEMP", "."), "cse_window.png")
    exe = ""
    for a in sys.argv[2:]:
        if a.startswith("--exe="):
            exe = a.split("=", 1)[1]

    proc = None
    hwnd = find_window()
    if not hwnd:
        if not exe:
            raise SystemExit("窗口未启动，且没给 --exe")
        proc = subprocess.Popen([exe], cwd=os.path.dirname(exe))
        for _ in range(400):
            hwnd = find_window()
            if hwnd:
                break
            time.sleep(0.02)
    if not hwnd:
        raise SystemExit("没找到窗口")
    time.sleep(2.5)

    # 置前，确保抓到的就是它
    user32.SetForegroundWindow(hwnd)
    time.sleep(1.0)

    from PIL import ImageGrab
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    im = ImageGrab.grab()
    crop = im.crop((r.left, r.top, r.right, r.bottom))
    crop.save(out)
    print(f"hwnd={hwnd} rect=({r.left},{r.top},{r.right},{r.bottom}) -> {out}")
    if proc:
        print("（应用由本脚本启动，未关闭）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
