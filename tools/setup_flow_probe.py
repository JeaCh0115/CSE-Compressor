"""安装界面前端流程自检：点「开始安装」→ 进度 → 完成（以及卸载流程）。

用 headless Edge + CDP 跑真实 DOM 交互，桩掉 window.pywebview.api 并模拟后端回调，
验证 ui.html 里的按钮接线、视图切换、进度/完成文案没有断开（避免「按钮点了没反应」）。
"""
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp_client import WS, pick_page  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "build", "tmp")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9334
RESULTS = []


def rec(ok, name, detail=""):
    RESULTS.append((ok, name, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{('  —  ' + detail) if detail else ''}")


STUB = """
<script>
window.__opts = null;
window.pywebview = {api:{
  info: () => Promise.resolve({version:"0.1.0-beta.7", channel_label:"Beta 公测版",
    build_time:"2026-10-30 09:42", default_dir:"C:\\\\Program Files\\\\CSE Compressor",
    payload_mb:32.0, installed:false}),
  close(){ window.__closed = true; }, minimize(){ window.__min = true; },
  choose_dir(){ window.__pick = true; },
  launch(p){ window.__launched = p; },
  install(opts){
    window.__opts = opts;
    setTimeout(() => window.__progress({stage:"extract", pct:45, msg:"释放 cse-cli.exe"}), 30);
    setTimeout(() => window.__progress({stage:"assoc", pct:96, msg:"注册系统集成"}), 90);
    setTimeout(() => window.__done({ok:true, exe:"C:\\\\Program Files\\\\CSE Compressor\\\\CSECompressor.exe"}), 150);
  },
  confirm_uninstall(){
    window.__opts = {uninstall:true};
    setTimeout(() => window.__progress({stage:"uninstall", pct:40, msg:"删除程序文件"}), 30);
    setTimeout(() => window.__done({ok:true, removed:42}), 120);
  }
}};
window.addEventListener("load", () => window.dispatchEvent(new Event("pywebviewready")));
</script>
"""


def build(mode, name):
    import base64
    ui = open(os.path.join(ROOT, "packaging", "installer", "ui.html"), encoding="utf-8").read()
    ui = ui.replace("__MODE__", mode)
    logo = os.path.join(ROOT, "assets", "icons", "app-logo.png")
    b64 = base64.b64encode(open(logo, "rb").read()).decode()
    ui = ui.replace('src="app-logo.png"', f'src="data:image/png;base64,{b64}"')
    ui = ui.replace("</body>", STUB + "</body>")
    path = os.path.join(OUT, f"{name}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(ui)
    return "file:///" + path.replace("\\", "/")


def wait_for(ws, expr, timeout=6, label=""):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if ws.eval(expr):
                return True
        except RuntimeError:
            pass
        time.sleep(0.03)
    rec(False, label or expr, f"{timeout}s 内条件未成立: {expr}")
    return False


def main():
    os.makedirs(OUT, exist_ok=True)
    inst = build("install", "flow_install")
    un = build("uninstall", "flow_uninstall")

    profile = tempfile.mkdtemp(prefix="cse_edge_flow_")
    proc = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                             f"--remote-debugging-port={PORT}",
                             f"--user-data-dir={profile}",
                             "--window-size=620,540", inst],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        t = pick_page(PORT, 40)
        ws = WS(t["webSocketDebuggerUrl"])
        ws.call("Runtime.enable")

        if not wait_for(ws, "document.readyState==='complete' && !!document.getElementById('goBtn')",
                        10, "安装页加载完成"):
            return finish(proc)

        rec(ws.eval("!document.getElementById('v-welcome').hidden"), "欢迎页默认可见")
        rec(bool(ws.eval("document.getElementById('dir').value")), "安装位置已填入默认目录")
        rec(ws.eval("document.getElementById('upgNotice').hidden"), "未装过时不显示升级提示")
        rec(ws.eval("document.activeElement===document.body||document.activeElement===null||document.activeElement.tagName!=='BUTTON'"),
            "打开时没有按钮带焦点（不会像被选中）")

        # ---- 点击「开始安装」 ----
        ws.eval("document.getElementById('goBtn').click()")
        rec(ws.eval("!document.getElementById('v-progress').hidden && document.getElementById('v-welcome').hidden"),
            "点「开始安装」切到进度页")

        if wait_for(ws, "document.getElementById('p-stage').textContent.trim()==='释放程序文件'",
                    3, "进度阶段文案中文化"):
            pct = ws.eval("document.getElementById('p-pct').textContent")
            rec(pct.endswith("%") and pct != "0%", "进度百分比在推进", pct)

        opts = ws.eval("JSON.stringify(window.__opts)")
        rec(opts and '"dir":"C:\\\\Program Files\\\\CSE Compressor"' in opts
            if opts else False, "安装参数已传给后端", opts or "")
        rec(opts and opts.count("true") >= 3, "三个可选项默认勾选")

        if wait_for(ws, "!document.getElementById('v-done').hidden", 4, "回到完成页"):
            rec(ws.eval("document.getElementById('d-title').textContent") == "安装完成", "完成页标题")
            rec(ws.eval("document.getElementById('d-icon').textContent") == "✓", "完成页是对勾标记")
            rec(not ws.eval("document.getElementById('d-launch').hidden"), "完成页提供「立即运行」")
            ws.eval("document.getElementById('d-launch').click()")
            rec(bool(ws.eval("window.__launched")), "「立即运行」调用 launch", str(ws.eval("window.__launched")))

        # ---- 卸载流程 ----
        ws.eval(f"location.href='{un}'")
        if wait_for(ws, "document.readyState==='complete' && !document.getElementById('v-unconfirm').hidden",
                    10, "卸载页加载完成"):
            rec(True, "卸载确认页可见")
            ws.eval("document.querySelector('#v-unconfirm .btn.danger').click()")
            rec(ws.eval("document.getElementById('p-title').textContent") == "正在卸载…", "卸载进度标题")
            if wait_for(ws, "!document.getElementById('v-done').hidden", 4, "卸载完成页"):
                rec(ws.eval("document.getElementById('d-title').textContent") == "卸载完成", "卸载完成标题")
    finally:
        pass
    return finish(proc)


def finish(proc):
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        pass
    bad = [r for r in RESULTS if r[0] is False]
    print("\n" + "=" * 60)
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过"
          + (f"，{len(bad)} 项失败" if bad else "，全部通过"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
