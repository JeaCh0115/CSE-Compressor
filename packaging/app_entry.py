"""PyInstaller 冻结态的应用入口：CSECompressor.exe

用法：
  CSECompressor.exe                       打开桌面应用
  CSECompressor.exe <归档路径>             打开应用并查看该归档（双击关联文件）
  CSECompressor.exe --add <路径...>        资源管理器右键「压缩」：带入压缩列表
  CSECompressor.exe --extract <归档>       资源管理器右键「解压」：弹解压设置（先问解压到哪）

**多选文件右键压缩**：资源管理器的 `%1` 动词对每个选中项各调起一次进程，
所以 N 个文件 = N 个进程。这里靠命名互斥锁选举主实例：主实例照常开窗，
其余进程等主实例的服务起来、把参数转发过去后立刻退出——否则会出现
"N 个窗口、每个只有 1 个文件"。

**冷启动速度**：判定"是否已有实例"必须是毫秒级。端口探测交给 csgui.runtime
（读 runtime.json + 枚举监听端口），不再逐个 connect 端口——那些端口多半落在
Hyper-V 保留段里，连接会黑洞挂到超时，一次右键就白等 4~5 秒。

启动路径上只做必要的事：所有重模块（urllib/json/引擎）都按需延迟导入。
"""

import os
import sys

from csgui import _trace as trace

MUTEX_NAME = "CSE.Compressor.SingleInstance"
FORWARD_TIMEOUT = 6.0          # 等主实例把服务拉起来的上限（秒）


def _parse_action(argv) -> str:
    if "--extract" in argv:
        return "extract"
    if "--add" in argv:
        return "add"
    return "view"


def _forward(port: int, req: dict) -> bool:
    import json
    import urllib.request

    try:
        data = json.dumps(req).encode("utf-8")
        r = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/launch", data=data,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=1.5):
            return True
    except Exception:
        return False


def _activate_existing_window() -> bool:
    if os.name != "nt":
        return False
    try:
        from csgui.desktop import _activate_existing
        return bool(_activate_existing())
    except Exception:
        return False


def main():
    trace.reset()
    trace.mark("入口：开始")
    if getattr(sys, "frozen", False):
        import multiprocessing
        multiprocessing.freeze_support()

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    argv = sys.argv[1:]
    action = _parse_action(argv)
    paths = [os.path.abspath(a) for a in argv if not a.startswith("-")]
    paths = [p for p in paths if os.path.exists(p)]
    trace.mark("入口：解析参数")

    from csgui import runtime

    req = {"action": action, "paths": paths} if paths else None

    # 1) 已有实例在跑：直接转发（读状态文件 + 校验端口，毫秒级）
    if req:
        port = runtime.find_running_port()
        if port and _forward(port, req):
            trace.mark(f"入口：转发到既有实例 :{port}")
            _activate_existing_window()
            return 0

    # 2) 选举主实例
    if not runtime.acquire_singleton(MUTEX_NAME):
        # 已有主实例：窗口已在就直接唤到前台
        if not req and _activate_existing_window():
            return 0
        # 窗口还没出来（主实例正在启动）：等它把服务拉起来，再把参数转过去
        data = runtime.read_runtime()
        leader = int(data.get("pid") or 0)
        port = runtime.wait_for_port(FORWARD_TIMEOUT, stop_when_pid_gone=leader)
        if port:
            if req:
                _forward(port, req)
            _activate_existing_window()
            trace.mark(f"入口：等待后转发到 :{port}")
            return 0
        if not req and _activate_existing_window():
            return 0
        trace.mark("入口：主实例无响应，兜底自启")
        # 落到下面自己起（极少数：主实例刚崩、互斥锁还没释放完）

    os.environ["CSE_SINGLETON_OWNER"] = "1"   # 告诉桌面外壳：锁已由本进程持有
    if req:
        runtime.write_runtime(0)              # 占位：让后来者知道我在启动
        trace.mark("入口：设置待办请求")
        from csgui.server import set_launch_request
        set_launch_request(req)

    from csgui.desktop import main as desktop_main
    trace.mark("入口：导入桌面外壳")
    return desktop_main()


if __name__ == "__main__":
    sys.exit(main() or 0)
