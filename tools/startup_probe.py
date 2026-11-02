"""冷启动耗时测量：进程启动 → 窗口可见 → 本地服务就绪。

用法：
  python tools/startup_probe.py [exe路径] [--runs=3]

默认测最新构建 dist/<时间戳>/CSECompressor/CSECompressor.exe。
注意：不要再逐个端口扫 + urllib 超时探测——那会把 21 个端口的超时
累加进结果（实测虚高 2 秒）；这里用 socket.connect_ex 立即返回，
并且窗口/服务两个指标并行轮询。
"""

import ctypes
import ctypes.wintypes as wt
import os
import socket
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EXE = r"C:\Program Files\CSE Compressor\CSECompressor.exe"
PORTS = range(8770, 8791)


def find_window_once():
    """单次枚举，返回标题以 CSE Compressor 开头的可见窗口句柄。"""
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


def port_open(port, timeout=0.05):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def kill_all(name="CSECompressor"):
    subprocess.run(["taskkill", "/F", "/IM", name + ".exe"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_once(exe):
    kill_all()
    time.sleep(0.5)
    env = dict(os.environ)
    t0 = time.time()
    proc = subprocess.Popen([exe], cwd=os.path.dirname(exe), env=env)
    win_t = svc_t = None
    port = 0
    try:
        while time.time() - t0 < 25:
            if win_t is None and find_window_once():
                win_t = time.time() - t0
            if svc_t is None:
                for p in PORTS:
                    if port_open(p):
                        svc_t, port = time.time() - t0, p
                        break
            if win_t is not None and svc_t is not None:
                break
            time.sleep(0.01)
    finally:
        kill_all()
        try:
            proc.wait(timeout=6)
        except Exception:
            proc.kill()
    return {"service": svc_t, "window": win_t, "port": port}


def _latest_exe():
    base = os.path.join(ROOT, "packaging", "build", "dist")
    if os.path.isdir(base):
        for stage in sorted(os.listdir(base), reverse=True):
            p = os.path.join(base, stage, "CSECompressor", "CSECompressor.exe")
            if os.path.isfile(p):
                return p
    return DEFAULT_EXE


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    runs = 3
    for a in sys.argv[1:]:
        if a.startswith("--runs="):
            runs = int(a.split("=", 1)[1])
    exe = args[0] if args else _latest_exe()
    tmp = None
    if not os.path.isfile(exe):
        portable = [f for f in os.listdir(os.path.join(ROOT, "dist"))
                    if f.endswith("-Portable.zip")]
        if not portable:
            raise SystemExit(f"未找到可执行文件：{exe}")
        portable.sort(key=lambda f: os.path.getmtime(os.path.join(ROOT, "dist", f)))
        tmp = tempfile.mkdtemp(prefix="cse_start_")
        zipfile.ZipFile(os.path.join(ROOT, "dist", portable[-1])).extractall(tmp)
        exe = os.path.join(tmp, "CSECompressor.exe")
    print(f"测量目标: {exe}")
    rows = []
    for i in range(runs):
        r = run_once(exe)
        rows.append(r)
        fmt = lambda v: "超时" if v is None else f"{v:.2f}s"   # noqa: E731
        print(f"  第 {i + 1} 次: 窗口可见 {fmt(r['window'])} · 服务就绪 {fmt(r['service'])}")
        time.sleep(0.6)
    ok = [r for r in rows if r["window"] is not None]
    if ok:
        avg = sum(r["window"] for r in ok) / len(ok)
        best = min(r["window"] for r in ok)
        savg = sum(r["service"] for r in ok if r["service"]) / max(1, len([r for r in ok if r["service"]]))
        print(f"\n窗口可见：平均 {avg:.2f}s · 最快 {best:.2f}s"
              f"（服务就绪平均 {savg:.2f}s · {len(ok)}/{len(rows)} 次成功）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
