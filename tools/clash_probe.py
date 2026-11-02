"""压缩输出与源文件撞名的探针。

曾经的事故：源本身就是 `aria2下载助手_便携版(1).zip`，默认输出名算出来还是它
自己，点「开始压缩」后要么 `[Errno 13] Permission denied`，要么把源文件截成
一个几十字节的空壳（数据当场没了）。

这里真机验证两件事：
  A. 单个 zip 源 + ZIP 格式 → 默认输出自动改成 `名称_压缩.zip`，且压缩能跑通；
  B. 多批右键（先 a.txt，再 a.zip）并入同一对话框 → 输出名要跟着避开新加的源。

用法：
  python tools/clash_probe.py
  python tools/clash_probe.py --keep     # 保留临时文件看现场
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 9351
WORK = os.path.join(tempfile.gettempdir(), "cse_clash_test")

RESULTS = []


def rec(ok, name, detail=""):
    RESULTS.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def _child():
    """子进程：模拟资源管理器右键「压缩」一个 .zip 文件。"""
    sys.path.insert(0, ROOT)
    import webview
    from csgui.server import set_launch_request

    which = os.environ.get("CSE_CLASH_CHILD", "1")
    target = os.path.join(WORK, "样例(1).zip" if which == "1" else "报告.txt")
    set_launch_request({"action": "add", "paths": [target]})
    webview.settings["REMOTE_DEBUGGING_PORT"] = PORT
    from csgui.desktop import main as desktop_main
    return desktop_main(debug=True)


def _kill_windows():
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    pids = set()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(h, _):
        b = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(h, b, 256)
        if b.value.startswith("CSE Compressor"):
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            pids.add(pid.value)
        return True

    u.EnumWindows(cb, 0)
    for p in pids:
        subprocess.run(["taskkill", "/F", "/PID", str(p)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return pids


def _post(base, path, payload):
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode() or "{}")


def _parent():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from cdp_client import WS, pick_page

    _kill_windows()
    time.sleep(0.6)
    env = dict(os.environ, CSE_CLASH_CHILD="1", PYTHONPATH=ROOT)
    proc = subprocess.Popen([sys.executable, os.path.abspath(__file__)], cwd=ROOT, env=env)
    try:
        t = pick_page(PORT, 40)
        ws = WS(t["webSocketDebuggerUrl"])
        ws.call("Runtime.enable")
        ws.call("Page.enable")
        base = ws.eval("location.origin")
        time.sleep(4.0)

        if not ws.eval("!document.querySelector('#dlg').hidden"):
            rec(False, "压缩对话框已打开", "没等到对话框")
            return
        rec(True, "压缩对话框已打开", ws.eval("document.querySelector('#dlgTitle').textContent"))

        # ---- A：单个 zip 源，切到 ZIP 格式 ----
        cur = ws.eval("document.querySelector('#cOut').value")
        rec(not cur.lower().endswith("样例(1).zip"), "默认输出未与源同名", cur)
        ws.eval("""(() => {
          const b = [...document.querySelectorAll('#cFormats .pill')].find(x => x.dataset.fmt === 'zip');
          if (b) b.click();
        })()""")
        time.sleep(0.4)
        out1 = ws.eval("document.querySelector('#cOut').value")
        stem = os.path.basename(out1).lower()
        rec(stem.endswith("_压缩.zip"), "切到 ZIP 后输出名自动避开源", out1)
        rec(ws.eval("""[...document.querySelectorAll('#cSrcList .nm')].length""") == 1,
            "源列表只有这一个 zip")

        # 真点「开始压缩」，确认不再报 Permission denied
        ws.eval("document.querySelector('#cStart').click()")
        for _ in range(40):
            time.sleep(0.5)
            title = ws.eval("document.querySelector('#cmpTitle') ? document.querySelector('#cmpTitle').textContent : ''")
            if title and ("完成" in title or "失败" in title):
                break
        title = ws.eval("document.querySelector('#cmpTitle').textContent")
        detail = ws.eval("document.querySelector('#cmpSub') ? document.querySelector('#cmpSub').textContent : ''")
        rec("完成" in title and "失败" not in title, "同名 zip 压缩成功", f"{title} / {detail[:60]}")
        rec(os.path.exists(out1), "产物已生成", out1)

        # ---- B：另一批右键（新文件正好等于当前输出名）----
        _kill_windows()
        time.sleep(0.8)
        # 造一对：报告.txt 与 报告.zip。先只压 报告.txt（默认输出就是 报告.zip）
        with zipfile.ZipFile(os.path.join(WORK, "报告.zip"), "w") as z:
            z.writestr("a.txt", "x" * 200)
        open(os.path.join(WORK, "报告.txt"), "w", encoding="utf-8").write("hello\n")
        env = dict(os.environ, CSE_CLASH_CHILD="2", PYTHONPATH=ROOT)
        proc2 = subprocess.Popen([sys.executable, os.path.abspath(__file__)], cwd=ROOT, env=env)
        t = pick_page(PORT, 40)
        ws2 = WS(t["webSocketDebuggerUrl"])
        ws2.call("Runtime.enable")
        time.sleep(4.0)
        base2 = ws2.eval("location.origin")
        ws2.eval("""(() => {
          const b = [...document.querySelectorAll('#cFormats .pill')].find(x => x.dataset.fmt === 'zip');
          if (b) b.click();
        })()""")
        time.sleep(0.3)
        before = ws2.eval("document.querySelector('#cOut').value")
        rec(os.path.basename(before).lower() == "报告.zip", "初始输出名等于第二个待并入的源", before)
        # 第二批右键（资源管理器对每个文件各起一次进程 → 这里直接补一条 add 请求）
        _post(base2, "/api/launch", {"action": "add", "paths": [os.path.join(WORK, "报告.zip")]})
        time.sleep(2.5)
        after = ws2.eval("document.querySelector('#cOut').value")
        rec(os.path.basename(after).lower() != "报告.zip", "并入新源后输出名自动让开", after)
        rec(ws2.eval("document.querySelectorAll('#cSrcList .nm').length") == 2,
            "源列表已并入 2 项")
        proc2.kill()
    finally:
        _kill_windows()
        try:
            proc.kill()
        except Exception:
            pass


def main(argv):
    if os.environ.get("CSE_CLASH_CHILD"):
        return _child()

    os.makedirs(WORK, exist_ok=True)
    src = os.path.join(WORK, "样例(1).zip")
    if not os.path.exists(src):
        with zipfile.ZipFile(src, "w") as z:
            z.writestr("inner.txt", "hello clash\n" * 50)
    # 用户截图里那个文件是只读的（浏览器下载标记），这里复刻同样的属性
    subprocess.run(["attrib", "+R", src], capture_output=True)

    _parent()
    for name in ("样例(1)_压缩.zip",):
        p = os.path.join(WORK, name)
        if os.path.exists(p):
            subprocess.run(["attrib", "-R", p], capture_output=True)

    if "--keep" not in argv:
        import shutil
        shutil.rmtree(WORK, ignore_errors=True)
    ok = sum(1 for r in RESULTS if r[0])
    print(f"\n{ok}/{len(RESULTS)} 通过")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
