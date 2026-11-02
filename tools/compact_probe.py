"""紧凑模式（右键「压缩」只弹压缩界面）探针。

真窗口是 WebView2，外部抓屏看不到内容细节，所以这里直接开 CDP：
子进程按「右键压缩」的方式启动应用（先塞一条 add 请求，再起桌面外壳），
父进程连上 WebView2 的调试端口，查 DOM 度量 + 截图。

用法：
  python tools/compact_probe.py                  # 检查 + 截图到 %TEMP%\\cse_compact.png
  python tools/compact_probe.py --shot=out.png
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
PORT = 9335
FILES = [
    os.path.join(tempfile.gettempdir(), "cse_cmp_test", "报告草稿.txt"),
    os.path.join(tempfile.gettempdir(), "cse_cmp_test", "数据.csv"),
]


def _child():
    """子进程：模拟资源管理器右键「压缩」。"""
    sys.path.insert(0, ROOT)
    import webview
    from csgui.server import set_launch_request

    set_launch_request({"action": "add", "paths": [p for p in FILES if os.path.exists(p)]})
    webview.settings["REMOTE_DEBUGGING_PORT"] = PORT
    from csgui.desktop import main as desktop_main
    return desktop_main(debug=True)


def _kill_windows():
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


def _parent(argv):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from cdp_client import WS, pick_page

    def shot(ws, path):
        import base64
        r = ws.call("Page.captureScreenshot", format="png")
        data = r.get("data") or r.get("result", {}).get("data")
        if data:
            with open(path, "wb") as f:
                f.write(base64.b64decode(data))
            print("截图:", path)

    out = os.path.join(tempfile.gettempdir(), "cse_compact.png")
    for a in argv:
        if a.startswith("--shot="):
            out = a.split("=", 1)[1]

    os.makedirs(os.path.join(tempfile.gettempdir(), "cse_cmp_test"), exist_ok=True)
    for name, text in (("报告草稿.txt", "hello compact\n"), ("数据.csv", "a,b\n1,2\n")):
        p = os.path.join(tempfile.gettempdir(), "cse_cmp_test", name)
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)

    _kill_windows()
    time.sleep(0.6)
    env = dict(os.environ, CSE_COMPACT_CHILD="1", PYTHONPATH=ROOT)
    proc = subprocess.Popen([sys.executable, os.path.abspath(__file__)],
                            cwd=ROOT, env=env)
    try:
        t = pick_page(PORT, 40)
        ws = WS(t["webSocketDebuggerUrl"])
        ws.call("Runtime.enable")
        ws.call("Page.enable")
        time.sleep(4.0)          # 等 launch 轮询把 add 请求领走、对话框弹出
        metrics = ws.eval("""(() => {
          const q = (s) => document.querySelector(s);
          const body = q('#dlgBody');
          const card = q('#dlg .modal-card');
          return {
            compact: document.body.classList.contains('compact'),
            dlgVisible: !q('#dlg').hidden,
            cmpVisible: !q('#cmpBox').hidden,
            menubarShown: getComputedStyle(q('#menubar')).display !== 'none',
            listShown: getComputedStyle(q('#listwrap')).display !== 'none',
            dlgBodyTop: body ? Math.round(body.getBoundingClientRect().top) : null,
            dlgBodyScrollTop: body ? Math.round(body.scrollTop) : null,
            dlgBodyScrollH: body ? Math.round(body.scrollHeight) : null,
            dlgBodyClientH: body ? Math.round(body.clientHeight) : null,
            cardH: card ? Math.round(card.getBoundingClientRect().height) : null,
            docScrollTop: Math.round(document.scrollingElement.scrollTop || 0),
            viewH: innerHeight,
            srcVisible: !!q('#cSrcCount') &&
              q('#cSrcCount').getBoundingClientRect().top >= (body ? body.getBoundingClientRect().top : 0),
            startBtnBottom: q('#cStart') ? Math.round(q('#cStart').getBoundingClientRect().bottom) : null,
            activeEl: document.activeElement ? document.activeElement.tagName +
              (document.activeElement.id ? '#' + document.activeElement.id : '') : '',
            // 样式是否真的生效（排查"改了 CSS 没反应"这类问题用）
            fieldMargin: (() => {
              const f = document.querySelectorAll('#dlgBody .field')[1];
              return f ? getComputedStyle(f).marginTop : null;
            })(),
            srcListH: q('#cSrcList') ? Math.round(q('#cSrcList').getBoundingClientRect().height) : null,
            actionsSticky: (() => {
              const a = q('#dlgBody .dlg-actions');
              return a ? getComputedStyle(a).position : null;
            })(),
            cssBytes: (() => {
              const s = [...document.styleSheets].map(x => {
                try { return x.cssRules.length; } catch (e) { return -1; }
              });
              return s;
            })(),
          };
        })()""")
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
        shot(ws, out)

        if "--run" in argv:
            # 走完整个紧凑流程：点开始压缩 → 进度面板 → 完成面板
            ws.eval("document.querySelector('#cStart').click()")
            state = {}
            for _ in range(60):
                time.sleep(0.5)
                state = ws.eval("""(() => {
                  const b = document.querySelector('#cmpBox');
                  return {
                    cmpVisible: !b.hidden,
                    isDone: b.classList.contains('is-done'),
                    isError: b.classList.contains('is-error'),
                    title: document.querySelector('#cmpTitle').textContent,
                    sub: document.querySelector('#cmpSub').textContent,
                    pct: document.querySelector('#cmpFill').style.width,
                    doneBtn: !document.querySelector('#cmpDone').hidden,
                    openBtn: !document.querySelector('#cmpOpen').hidden,
                    mainBtn: !document.querySelector('#cmpMain').hidden,
                    dlgHidden: document.querySelector('#dlg').hidden,
                  };
                })()""")
                if state.get("doneBtn"):
                    break
            print("紧凑流程:", json.dumps(state, ensure_ascii=False, indent=2))
            shot(ws, out.replace(".png", "_done.png"))
    finally:
        _kill_windows()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    return 0


if __name__ == "__main__":
    if os.environ.get("CSE_COMPACT_CHILD") == "1":
        sys.exit(_child() or 0)
    sys.exit(_parent(sys.argv[1:]))
