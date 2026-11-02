"""紧凑小窗里点「完整性测试」不能把小窗关掉。

用户反馈：右键「解压」→ 点「完整性测试」，窗口直接退出了。
原因：那个按钮走的是 closeDlg()（紧凑模式下 = 关窗退出），后面才开始跑 job，
所以任务还在跑、窗口已经没了。

断言：
  1) 点完之后窗口仍然存在且可见；
  2) 对话框切成了进度/结果面板（#cmpBox）；
  3) 面板文案是「完整性测试」；
  4) 任务最终到达完成态（不是卡在 0%）。

用法：python tools/compact_test_probe.py
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(ROOT, "build", "tmp")
PORT = 9338
u32 = ctypes.windll.user32
RESULTS = []


def rec(ok, name, detail=""):
    RESULTS.append((ok, name, detail))
    flag = {True: "PASS", False: "FAIL", None: "SKIP"}[ok]
    print(f"[{flag}] {name}{('  —  ' + detail) if detail else ''}")


def make_fixture():
    d = os.path.join(tempfile.gettempdir(), "cse_ready_test")
    os.makedirs(d, exist_ok=True)
    arc = os.path.join(d, "样例归档.zip")
    if not os.path.exists(arc):
        import zipfile
        with zipfile.ZipFile(arc, "w") as z:
            z.writestr("a.txt", "a" * 100)
            z.writestr("b/c.txt", "c" * 100)
    return arc


def find_hwnd():
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(hwnd, buf, 256)
        if buf.value.startswith("CSE Compressor"):
            found.append(hwnd)
        return True

    u32.EnumWindows(cb, 0)
    return found[0] if found else 0


def kill_stale():
    hwnd = find_hwnd()
    while hwnd:
        pid = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        subprocess.run(["taskkill", "/F", "/PID", str(pid.value), "/T"],
                       capture_output=True)
        time.sleep(0.3)
        hwnd = find_hwnd()


def child():
    sys.path.insert(0, ROOT)
    import webview
    from csgui.server import set_launch_request

    set_launch_request({"action": "extract",
                        "paths": json.loads(os.environ["CSE_PROBE_PATHS"])})
    webview.settings["REMOTE_DEBUGGING_PORT"] = PORT
    from csgui.desktop import main as desktop_main
    return desktop_main(debug=True)


def parent():
    os.makedirs(TMP, exist_ok=True)
    arc = make_fixture()
    kill_stale()
    time.sleep(0.5)
    env = dict(os.environ, CSE_PROBE_PATHS=json.dumps([arc]), PYTHONPATH=ROOT)
    proc = subprocess.Popen([sys.executable, os.path.abspath(__file__)], cwd=ROOT, env=env)
    try:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import urllib.request
        from cdp_client import WS

        def app_page(timeout=40):
            end = time.time() + timeout
            while time.time() < end:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list",
                                                timeout=2) as r:
                        targets = json.loads(r.read().decode())
                except Exception:
                    time.sleep(0.4)
                    continue
                for t in targets:
                    if t.get("type") == "page" and "127.0.0.1" in (t.get("url") or "") \
                            and t.get("webSocketDebuggerUrl"):
                        return t
                time.sleep(0.4)
            raise RuntimeError("未找到界面页面")

        ws = WS(app_page()["webSocketDebuggerUrl"])
        ws.call("Runtime.enable")

        # 等解压设置面板出现
        for _ in range(40):
            if ws.eval("!!document.querySelector('#xTest')"):
                break
            time.sleep(0.3)
        rec(True, "解压设置面板已出现")
        rec(ws.eval("!!document.querySelector('#xTest')"), "面板里有「完整性测试」按钮")

        hwnd = find_hwnd()
        ws.eval("document.querySelector('#xTest').click()")
        time.sleep(1.2)

        alive = bool(find_hwnd()) and bool(u32.IsWindowVisible(find_hwnd()))
        rec(alive, "点「完整性测试」后窗口仍然存在", f"hwnd={find_hwnd()}")

        state = ws.eval("""(() => {
          const b = document.querySelector('#cmpBox');
          const t = document.querySelector('#cmpTitle');
          return {
            cmpVisible: !b.hidden,
            dlgHidden: document.querySelector('#dlg').hidden,
            title: t ? t.textContent : '',
            isDone: b.classList.contains('is-done'),
            sub: (document.querySelector('#cmpSub') || {}).textContent || '',
          };
        })()""")
        rec(bool(state.get("cmpVisible")), "切换成进度/结果面板", str(state.get("title")))
        rec("完整性" in (state.get("title") or ""), "面板标题是「完整性测试」",
            str(state.get("title")))

        # 等任务跑完
        done = False
        for _ in range(40):
            time.sleep(0.4)
            if ws.eval("document.querySelector('#cmpBox').classList.contains('is-done')"):
                done = True
                break
        final = ws.eval("""(() => ({
          title: (document.querySelector('#cmpTitle') || {}).textContent || '',
          sub: (document.querySelector('#cmpSub') || {}).textContent || '',
          doneBtn: !document.querySelector('#cmpDone').hidden,
        }))()""")
        rec(done, "完整性测试跑完并显示结果",
            f"{final.get('title')} / {final.get('sub')}")
        rec(bool(final.get("doneBtn")), "结果面板有「完成」按钮")

        import base64
        r = ws.call("Page.captureScreenshot", format="png")
        data = r.get("data") or r.get("result", {}).get("data")
        if data:
            open(os.path.join(TMP, "compact_test.png"), "wb").write(base64.b64decode(data))
            print("截图:", os.path.join(TMP, "compact_test.png"))
    finally:
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=10)
        except Exception:
            pass
        kill_stale()
    passed = sum(1 for ok, _, _ in RESULTS if ok is True)
    failed = [n for ok, n, _ in RESULTS if ok is False]
    print(f"\n通过 {passed}/{len(RESULTS)}" + (f"，失败：{failed}" if failed else ""))
    return 0 if not failed else 1


if __name__ == "__main__":
    if os.environ.get("CSE_PROBE_PATHS"):
        sys.exit(child() or 0)
    sys.exit(parent())
