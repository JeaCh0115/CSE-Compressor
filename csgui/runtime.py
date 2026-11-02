"""运行期进程发现：单实例选举 + 本地服务端口定位。

**为什么需要这个模块**：右键「压缩」在没有实例运行时，新进程必须先判断
"是不是已经有窗口在跑"。旧实现逐个端口 urlopen（8770–8790，21 次 × 0.2s
超时）。这些端口常常落在 Hyper-V / WinNAT 的动态保留段里——连接既不返回
RST 也不返回 SYN-ACK，直接黑洞，于是每一次右键都白等 4~5 秒才起窗口。

现在的做法：
  1. 主实例启动后把 {"pid", "port"} 写进 runtime.json（进程刚起来时先写
     port=0 占位，服务监听后再补真实端口）；
  2. 后来者读文件 → 校验 pid 存活 → 一次 40ms 的 TCP 连接即可确认；
  3. 文件缺失/失效时才回落到枚举系统监听端口（一次 iphlpapi 调用，毫秒级，
     绝不逐个 connect 黑洞端口）。

单实例选举用命名互斥锁：CreateMutexW 返回 ERROR_ALREADY_EXISTS 即说明已有
主实例。必须**在进程入口**就选举，否则 N 个并发进程（资源管理器多选文件时
逐个调起）会同时判空、同时开窗。
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import socket
import sys
import time

MUTEX_NAME = "CSE.Compressor.SingleInstance"
PORT_BASE = 8770
PORT_COUNT = 21
PORTS = range(PORT_BASE, PORT_BASE + PORT_COUNT)

IS_WINDOWS = os.name == "nt"


# ---------------- 状态文件 ----------------
def state_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "CSE Compressor")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def runtime_file() -> str:
    return os.path.join(state_dir(), "runtime.json")


def write_runtime(port: int = 0, pid: int = None) -> None:
    """写运行期状态。port=0 表示"进程已起来、服务还没监听"。"""
    data = {"pid": int(pid or os.getpid()), "port": int(port), "ts": time.time()}
    tmp = runtime_file() + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, runtime_file())
    except OSError:
        pass


def read_runtime() -> dict:
    try:
        with open(runtime_file(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def clear_runtime(pid: int = None) -> None:
    """删除状态文件；只删自己写的（避免把新实例的记录抹掉）。"""
    data = read_runtime()
    if data and pid and data.get("pid") not in (None, int(pid)):
        return
    try:
        os.remove(runtime_file())
    except OSError:
        pass


# ---------------- 进程 / 端口探测 ----------------
def pid_alive(pid: int) -> bool:
    if not pid or not IS_WINDOWS:
        return False
    PROCESS_QUERY_LIMITED = 0x1000
    h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED, False, int(pid))
    if not h:
        return False
    try:
        code = wt.DWORD()
        if ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
            return code.value == 259  # STILL_ACTIVE
        return True
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def port_open(port: int, timeout: float = 0.04) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex(("127.0.0.1", int(port))) == 0
    finally:
        s.close()


class _MIB_TCPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [("dwState", wt.DWORD), ("dwLocalAddr", wt.DWORD),
                ("dwLocalPort", wt.DWORD), ("dwRemoteAddr", wt.DWORD),
                ("dwRemotePort", wt.DWORD), ("dwOwningPid", wt.DWORD)]


def listening_ports() -> set:
    """枚举本机 TCP LISTEN 端口（一次 iphlpapi 调用，毫秒级）。

    比逐个 connect 探测可靠：落在保留段里的端口连接会黑洞挂到超时，
    而这张表只反映真实监听者。
    """
    if not IS_WINDOWS:
        return set()
    AF_INET, TCP_TABLE_OWNER_PID_LISTENER = 2, 3
    iphlpapi = ctypes.windll.iphlpapi
    size = wt.DWORD(0)
    iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, AF_INET,
                                 TCP_TABLE_OWNER_PID_LISTENER, 0)
    if size.value <= 0:
        return set()
    buf = ctypes.create_string_buffer(size.value)
    rc = iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False, AF_INET,
                                      TCP_TABLE_OWNER_PID_LISTENER, 0)
    if rc != 0:
        return set()
    n = ctypes.cast(buf, ctypes.POINTER(wt.DWORD)).contents.value
    rows = ctypes.cast(ctypes.byref(buf, ctypes.sizeof(wt.DWORD)),
                       ctypes.POINTER(_MIB_TCPROW_OWNER_PID))
    out = set()
    for i in range(n):
        out.add(socket.ntohs(rows[i].dwLocalPort & 0xFFFF))
    return out


def candidate_ports() -> list:
    """本机正在监听、且落在我们端口段里的端口（按顺序）。"""
    live = listening_ports()
    return [p for p in PORTS if p in live]


def find_running_port(verify=True) -> int:
    """找已在运行的实例端口；0 = 没有实例。全部走毫秒级路径。"""
    data = read_runtime()
    port = int(data.get("port") or 0)
    if port and port in PORTS:
        if not data.get("pid") or pid_alive(int(data["pid"])):
            if not verify or port_open(port):
                return port
    # 状态文件缺失或过期：只探测真实在监听的端口，绝不盲连
    for p in candidate_ports():
        if port_open(p):
            return p
    return 0


def wait_for_port(timeout: float = 6.0, stop_when_pid_gone: int = 0) -> int:
    """等主实例把服务拉起来（多选文件时用来把请求汇到同一个窗口）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        port = find_running_port()
        if port:
            return port
        if stop_when_pid_gone and not pid_alive(stop_when_pid_gone):
            return 0          # 主实例已退出（崩了/被关），别再傻等
        time.sleep(0.05)
    return 0


# ---------------- 单实例选举 ----------------
_mutex_handle = None


def acquire_singleton(name: str = MUTEX_NAME) -> bool:
    """True = 本进程是主实例；False = 已有实例（互斥锁随进程退出自动释放）。"""
    global _mutex_handle
    if not IS_WINDOWS:
        return True
    if os.environ.get("CSE_NO_SINGLE") == "1":
        return True
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = wt.HANDLE
    _mutex_handle = k32.CreateMutexW(None, False, name)
    return k32.GetLastError() != 183          # ERROR_ALREADY_EXISTS
