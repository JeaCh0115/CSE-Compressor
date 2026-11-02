"""主界面（860×500）里的压缩设置对话框，加上「已存在」提醒后是否还要滚动。

beta.10 起压缩对话框是两栏一页放完（左源项目自带滚动、右设置项一屏）。
beta.12 在输出框下面加了一条橙色提醒，多出约 17px——这里量一下溢出。

用法：python tools/dialog_fit_probe.py
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 9358
WORK = os.path.join(tempfile.gettempdir(), "cse_fit_test")
RESULTS = []


def rec(ok, name, detail=""):
    RESULTS.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def _child():
    sys.path.insert(0, ROOT)
    import webview
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
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _post(base, path, payload):
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode() or "{}")


def _measure(ws, label):
    m = ws.eval("""(() => {
      const card = document.querySelector('#dlg .modal-card');
      const body = document.querySelector('#dlgBody');
      const act = document.querySelector('#dlg .dlg-actions');
      const r = act ? act.getBoundingClientRect() : null;
      const c = card ? card.getBoundingClientRect() : null;
      return {
        vw: innerWidth, vh: innerHeight,
        overflow: body ? body.scrollHeight - body.clientHeight : -1,
        cardH: c ? Math.round(c.height) : -1,
        btnBottom: r ? Math.round(r.bottom) : -1,
        cardBottom: c ? Math.round(c.bottom) : -1,
      };
    })()""")
    print(f"    · {label}: 窗口 {m['vw']}×{m['vh']} 卡片高 {m['cardH']}  "
          f"溢出 {m['overflow']}px  按钮底 {m['btnBottom']} / 卡片底 {m['cardBottom']}")
    return m


def _parent():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from cdp_client import WS, pick_page

    _kill_windows()
    time.sleep(0.7)
    proc = subprocess.Popen([sys.executable, os.path.abspath(__file__)], cwd=ROOT,
                            env=dict(os.environ, CSE_FIT_CHILD="1", PYTHONPATH=ROOT))
    ws = None
    try:
        t = pick_page(PORT, 60)
        ws = WS(t["webSocketDebuggerUrl"])
        ws.call("Runtime.enable")
        base = ws.eval("location.origin")
        time.sleep(2.0)
        # 主界面模式下用 launch 队列打开压缩对话框
        _post(base, "/api/launch", {"action": "add", "paths": [os.path.join(WORK, "报告.txt")]})
        for _ in range(150):
            if ws.eval("!document.querySelector('#dlg').hidden"):
                break
            time.sleep(0.1)
        rec(True, "主界面压缩对话框已打开", ws.eval("document.querySelector('#dlgTitle').textContent"))
        time.sleep(0.8)

        # 基线：输出名不存在的普通情况
        ws.eval("""(() => {
          const b = [...document.querySelectorAll('#cFormats .pill')].find(x => x.dataset.fmt === 'zip');
          if (b) b.click();
        })()""")
        time.sleep(0.6)
        ws.eval("""(() => {
          const el = document.querySelector('#cOut');
          el.value = %s;
          el.dispatchEvent(new Event('input', {bubbles: true}));
        })()""" % json.dumps(os.path.join(WORK, "不存在的名字.zip")))
        time.sleep(0.6)
        m0 = _measure(ws, "无提醒条")
        rec(m0["overflow"] <= 1, "无提醒时一页放完（基线）", f"溢出 {m0['overflow']}px")

        # 有提醒：改成已存在的 报告.zip
        ws.eval("""(() => {
          const el = document.querySelector('#cOut');
          el.value = %s;
          el.dispatchEvent(new Event('input', {bubbles: true}));
        })()""" % json.dumps(os.path.join(WORK, "报告.zip")))
        time.sleep(0.8)
        hint = ws.eval("""(() => {
          const h = document.querySelector('#cOver');
          return h && !h.hidden ? h.textContent : '';
        })()""")
        rec("已存在" in hint, "提醒条已显示", hint[:36])
        m1 = _measure(ws, "带提醒条")
        rec(m1["overflow"] <= 1, "带提醒条也一页放完", f"溢出 {m1['overflow']}px")
        rec(m1["btnBottom"] <= m1["vh"], "「开始压缩」没有被推出窗口",
            f"{m1['btnBottom']} / {m1['vh']}")
    finally:
        try:
            if ws:
                ws.close()
        except Exception:
            pass
        _kill_windows()
        try:
            proc.kill()
        except Exception:
            pass


def main(argv):
    if os.environ.get("CSE_FIT_CHILD"):
        return _child()

    import shutil
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    with open(os.path.join(WORK, "报告.txt"), "w", encoding="utf-8") as f:
        f.write("hello fit\n" * 50)
    with zipfile.ZipFile(os.path.join(WORK, "报告.zip"), "w") as z:
        z.writestr("a.txt", "old")

    _parent()

    if "--keep" not in argv:
        shutil.rmtree(WORK, ignore_errors=True)
    ok = sum(1 for r in RESULTS if r[0])
    print(f"\n{ok}/{len(RESULTS)} 通过")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
