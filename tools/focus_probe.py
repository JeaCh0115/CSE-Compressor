"""前台/焦点探针：窗口亮出来之后，它到底是不是前台窗口、有没有拿到焦点。

为什么在意：窗口改成 hidden=True 建、首帧就绪后再 show()，如果 show() 没把窗口
带到前台，用户双击图标可能"看起来没反应"（窗口开在别人后面），比闪黑更严重。
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


def find_cse():
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and title(hwnd).startswith("CSE Compressor"):
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else 0


def main():
    exe = sys.argv[1] if len(sys.argv) > 1 else ""
    subprocess.run(["taskkill", "/F", "/IM", "CSECompressor.exe"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.5)
    print("启动前的前台窗口:", title(user32.GetForegroundWindow()) or "(无)")
    proc = subprocess.Popen([exe], cwd=os.path.dirname(exe))
    try:
        hwnd = 0
        for _ in range(300):
            hwnd = find_cse()
            if hwnd:
                break
            time.sleep(0.01)
        if not hwnd:
            print("窗口没出现")
            return 1
        time.sleep(1.2)
        print(f"窗口 hwnd={hwnd} visible={bool(user32.IsWindowVisible(hwnd))} "
              f"iconic={bool(user32.IsIconic(hwnd))}")
        fg = user32.GetForegroundWindow()
        print(f"前台窗口 hwnd={fg} 「{title(fg)}」")
        print("是本窗口?:", "YES" if fg == hwnd else "NO")
        print("自身是否前台? (GetWindowThreadProcessId 比对):",
              user32.GetWindowThreadProcessId(fg, None) ==
              user32.GetWindowThreadProcessId(hwnd, None))
        print("焦点窗口:", f"hwnd={user32.GetFocus()}")
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        print(f"rect=({r.left},{r.top},{r.right},{r.bottom}) 在屏内?:",
              r.left >= -50 and r.top >= -50)
    finally:
        subprocess.run(["taskkill", "/F", "/IM", "CSECompressor.exe"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
