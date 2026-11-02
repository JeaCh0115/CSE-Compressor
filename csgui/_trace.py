"""启动阶段计时探针（仅 CSE_TIMING=1 时写日志，发布版零开销）。

用法：在关键节点调用 mark("阶段名")，日志追加到 %TEMP%\\CSE-startup.log。
"""

import os
import time

T0 = time.perf_counter()
_enabled = os.environ.get("CSE_TIMING") == "1"
_last = T0
_reset_done = False


def mark(label: str) -> None:
    """记录一条相对启动起点的耗时（含与上一条的间隔）。"""
    global _last
    if not _enabled:
        return
    now = time.perf_counter()
    try:
        path = os.path.join(os.environ.get("TEMP") or ".", "CSE-startup.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{now - T0:7.3f}s  (+{now - _last:6.3f}s)  {label}\n")
    except OSError:
        pass
    _last = now


def _process_age() -> float:
    """进程从「被创建」到此刻的秒数（含 PyInstaller 引导与解释器启动）。

    Python 里拿不到进程启动时刻，只能问内核 GetProcessTimes。
    """
    try:
        import ctypes
        import ctypes.wintypes as wt

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]

        k32 = ctypes.windll.kernel32
        # 句柄是 64 位：不声明 argtypes/restype 会被截断成 int，调用直接失败
        k32.GetCurrentProcess.restype = wt.HANDLE
        k32.GetProcessTimes.argtypes = [wt.HANDLE, ctypes.POINTER(FILETIME),
                                        ctypes.POINTER(FILETIME),
                                        ctypes.POINTER(FILETIME),
                                        ctypes.POINTER(FILETIME)]
        k32.GetProcessTimes.restype = wt.BOOL
        c, e, kt, ut = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        if not k32.GetProcessTimes(k32.GetCurrentProcess(), ctypes.byref(c),
                                   ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut)):
            return -1.0
        # FILETIME：自 1601-01-01 起的 100 纳秒数
        created = ((c.dwHighDateTime << 32) | c.dwLowDateTime) / 1e7
        import time as _t
        return _t.time() - (created - 11644473600)
    except Exception:
        return -1.0


def reset() -> None:
    """每次启动清空日志（同一进程只清一次，别把入口阶段的记录抹掉）。"""
    global _reset_done
    if not _enabled or _reset_done:
        return
    _reset_done = True
    age = _process_age()
    try:
        path = os.path.join(os.environ.get("TEMP") or ".", "CSE-startup.log")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# CSE startup trace  {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            if age >= 0:
                f.write(f"  引导+解释器启动耗时 {age:.3f}s（进程创建 → 本行）\n")
    except OSError:
        pass
