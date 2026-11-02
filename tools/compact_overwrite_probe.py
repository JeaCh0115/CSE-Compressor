"""紧凑小窗里的「已存在」提醒 / 覆盖确认层不能把小窗撑出滚动条。

beta.11 之后加了两个新东西：
  · 输出框下面的橙色提醒条（#cOver / #xOver）
  · 点「开始」后盖在面板上的确认层（.sheet，三个按钮）
小窗本身只有 560×560（解压 540×420），多一行就可能要滚动——用户明确要求
「一页显示所有设置，不要下滑块」。这里在真机小窗上用 CDP 量像素：

  1) 提醒条出现后 #dlgBody 有没有溢出（scrollHeight > clientHeight）；
  2) 主按钮是否还在窗口内（getBoundingClientRect().bottom <= innerHeight）；
  3) 确认层的三个按钮是否全在窗口内、层本身是否溢出。

用法：python tools/compact_overwrite_probe.py
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 9356
WORK = os.path.join(tempfile.gettempdir(), "cse_compact_ow")
RESULTS = []


def rec(ok, name, detail=""):
    RESULTS.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def _child():
    sys.path.insert(0, ROOT)
    import webview
    from csgui.server import set_launch_request

    kind = os.environ.get("CSE_COW_KIND", "add")
    target = (os.path.join(WORK, "报告.txt") if kind == "add"
              else os.path.join(WORK, "pack.zip"))
    set_launch_request({"action": "add" if kind == "add" else "extract", "paths": [target]})
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


def _open_child(kind):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from cdp_client import WS, pick_page

    _kill_windows()
    time.sleep(0.7)
    env = dict(os.environ, CSE_COW_CHILD="1", CSE_COW_KIND=kind, PYTHONPATH=ROOT)
    proc = subprocess.Popen([sys.executable, os.path.abspath(__file__)], cwd=ROOT, env=env)
    t = pick_page(PORT, 60)
    ws = WS(t["webSocketDebuggerUrl"])
    ws.call("Runtime.enable")
    for _ in range(150):
        if ws.eval("!document.querySelector('#dlg').hidden"):
            break
        time.sleep(0.1)
    user32 = __import__("ctypes").windll.user32
    return ws, proc, user32


def _metrics(ws, label):
    """量 #dlgBody 是否溢出、主按钮是否在窗口内。"""
    m = ws.eval("""(() => {
      const body = document.querySelector('#dlgBody');
      const act = document.querySelector('#dlg .dlg-actions');
      const r = act ? act.getBoundingClientRect() : null;
      return {
        vw: innerWidth, vh: innerHeight,
        overflow: body ? body.scrollHeight - body.clientHeight : -1,
        btnBottom: r ? Math.round(r.bottom) : -1,
        btnTop: r ? Math.round(r.top) : -1,
      };
    })()""")
    print(f"    · {label}: {m['vw']}×{m['vh']}  溢出 {m['overflow']}px  "
          f"按钮 y={m['btnTop']}..{m['btnBottom']}")
    return m


def _sheet_metrics(ws):
    return ws.eval("""(() => {
      const s = document.querySelector('#dlg .sheet');
      if (!s) return null;
      const btns = [...s.querySelectorAll('button')].map((b) => {
        const r = b.getBoundingClientRect();
        return {t: Math.round(r.top), b: Math.round(r.bottom), k: b.dataset.k};
      });
      return {vh: innerHeight, sh: s.scrollHeight, ch: s.clientHeight, btns};
    })()""")


def _run(kind):
    ws, proc, _ = _open_child(kind)
    try:
        print(f"\n== 紧凑窗 · {kind} ==")
        time.sleep(1.0)
        if kind == "add":
            ws.eval("""(() => {
              const b = [...document.querySelectorAll('#cFormats .pill')].find(x => x.dataset.fmt === 'zip');
              if (b) b.click();
            })()""")
            time.sleep(1.0)
            hint = ws.eval("""(() => {
              const h = document.querySelector('#cOver');
              return h && !h.hidden ? h.textContent : '';
            })()""")
            rec("同名文件已存在" in hint, "小窗里也挂出提醒条", hint[:40])
            m = _metrics(ws, "带提醒条")
            rec(m["overflow"] <= 1, "提醒条没把小窗撑出滚动条", f"溢出 {m['overflow']}px")
            rec(0 <= m["btnBottom"] <= m["vh"], "「开始压缩」仍在窗口内",
                f"bottom={m['btnBottom']} / {m['vh']}")

            # 手改成已存在的名字 → 确认层
            ws.eval("""(() => {
              const el = document.querySelector('#cOut');
              el.value = %s;
              el.dispatchEvent(new Event('input', {bubbles: true}));
            })()""" % json.dumps(os.path.join(WORK, "报告.zip")))
            time.sleep(0.4)
            m2 = _metrics(ws, "手改回原名")
            rec(m2["overflow"] <= 1, "手改原名后仍不溢出", f"溢出 {m2['overflow']}px")
            ws.eval("document.querySelector('#cStart').click()")
            for _ in range(80):
                if ws.eval("!!document.querySelector('#dlg .sheet')"):
                    break
                time.sleep(0.1)
            s = _sheet_metrics(ws)
            rec(bool(s), "小窗里弹出覆盖确认")
            if s:
                rec(s["sh"] - s["ch"] <= 1, "确认层内容不需要滚动",
                    f"溢出 {s['sh'] - s['ch']}px")
                bottom = max((b["b"] for b in s["btns"]), default=-1)
                rec(bottom <= s["vh"], "三个按钮都在窗口内", f"{s['btns']} / vh={s['vh']}")
        else:
            hint = ws.eval("""(() => {
              const h = document.querySelector('#xOver');
              return h && !h.hidden ? h.textContent : '';
            })()""")
            rec("已有" in hint, "解压小窗里提示目录非空", hint[:40])
            m = _metrics(ws, "带提示条")
            rec(m["overflow"] <= 1, "解压小窗没被撑出滚动条", f"溢出 {m['overflow']}px")
            rec(0 <= m["btnBottom"] <= m["vh"], "「开始解压」仍在窗口内",
                f"bottom={m['btnBottom']} / {m['vh']}")
            ws.eval("document.querySelector('#xStart').click()")
            for _ in range(120):
                if ws.eval("!!document.querySelector('#dlg .sheet')"):
                    break
                time.sleep(0.1)
            s = _sheet_metrics(ws)
            rec(bool(s), "解压小窗里弹出覆盖确认")
            if s:
                rec(s["sh"] - s["ch"] <= 1, "确认层内容不需要滚动",
                    f"溢出 {s['sh'] - s['ch']}px")
                bottom = max((b["b"] for b in s["btns"]), default=-1)
                rec(bottom <= s["vh"], "三个按钮都在窗口内", f"{s['btns']} / vh={s['vh']}")
    finally:
        try:
            ws.close()
        except Exception:
            pass
        _kill_windows()
        try:
            proc.kill()
        except Exception:
            pass
        time.sleep(0.5)


def main(argv):
    if os.environ.get("CSE_COW_CHILD"):
        return _child()

    import shutil
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    with open(os.path.join(WORK, "报告.txt"), "w", encoding="utf-8") as f:
        f.write("hello\n")
    with zipfile.ZipFile(os.path.join(WORK, "报告.zip"), "w") as z:
        z.writestr("a.txt", "old")
    with zipfile.ZipFile(os.path.join(WORK, "pack.zip"), "w") as z:
        z.writestr("a.txt", "NEW-CONTENT")
    os.makedirs(os.path.join(WORK, "pack_解压"), exist_ok=True)
    with open(os.path.join(WORK, "pack_解压", "a.txt"), "wb") as f:
        f.write(b"OLD-CONTENT")

    _run("add")
    _run("extract")

    if "--keep" not in argv:
        shutil.rmtree(WORK, ignore_errors=True)
    ok = sum(1 for r in RESULTS if r[0])
    print(f"\n{ok}/{len(RESULTS)} 通过")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
