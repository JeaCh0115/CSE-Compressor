"""给 README 生成界面截图（真实页面 + 真实后端，headless Edge 抓图）。

  python tools/readme_shot.py [--dir D:/Workspace/CSE] [--out docs/screenshots]

产出的图会进仓库，所以要保证图里没有本机私有路径、测试垃圾文件。
默认直接截**本仓库自己的目录**——列出来的是 csengine/、csgui/ 这些真源码，
比摆一堆假数据诚实，也不泄露任何东西。
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp_client import WS, pick_page  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8913
DEBUG_PORT = 9343
EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def find_edge():
    for p in EDGE_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def wait_http(url, secs=30):
    for _ in range(int(secs * 4)):
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                r.read(1)
            return True
        except Exception:
            time.sleep(0.25)
    return False


def post(base, path, payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(base + path, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def shot(ws, path, dpr=2):
    """按 2 倍像素抓图，README 在高分屏上不糊。"""
    ws.call("Emulation.setDeviceMetricsOverride", width=W, height=H,
            deviceScaleFactor=dpr, mobile=False)
    time.sleep(0.45)
    data = ws.call("Page.captureScreenshot", format="png")["data"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(base64.b64decode(data))
    print(f"  {os.path.relpath(path, ROOT)}  {os.path.getsize(path) // 1024} KB")
    ws.call("Emulation.clearDeviceMetricsOverride")


W, H = 900, 540


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=ROOT, help="截图里显示的目录")
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "screenshots"))
    args = ap.parse_args()

    edge_path = find_edge()
    if not edge_path:
        print("没找到 Edge，跳过")
        return 0
    srv = edge = ws = None
    try:
        env = dict(os.environ, PYTHONPATH=ROOT, PYTHONIOENCODING="utf-8")
        srv = subprocess.Popen([sys.executable, "run.py", "ui", "--no-browser",
                                f"--port={PORT}"], cwd=ROOT, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{PORT}"
        if not wait_http(base + "/", 30):
            print("服务没起来")
            return 1

        profile = os.path.join(os.environ.get("TEMP", "."), f"cse_shot_{os.getpid()}")
        edge = subprocess.Popen([edge_path, "--headless=new", "--disable-gpu",
                                 "--hide-scrollbars", "--no-first-run",
                                 f"--remote-debugging-port={DEBUG_PORT}",
                                 f"--user-data-dir={profile}",
                                 f"--window-size={W},{H}", base + "/"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ws = WS(pick_page(DEBUG_PORT)["webSocketDebuggerUrl"])
        ws.call("Page.enable")
        time.sleep(2.2)

        # 一、主界面：地址栏输入仓库目录 → 前往
        ws.eval("""(() => {
          const el = document.getElementById('pathInput');
          el.value = %s;
          document.getElementById('navGo').click();
          return true;
        })()""" % json.dumps(args.dir.replace("\\", "\\\\")))
        time.sleep(2.0)
        shot(ws, os.path.join(args.out, "main.png"))

        # 二、压缩设置：走真实入口（和右键压缩同一条链路）
        post(base, "/api/launch", {"action": "add",
                                   "paths": [os.path.join(args.dir, "README.md")]})
        for _ in range(40):
            if ws.eval("!!document.querySelector('#cOut')"):
                break
            time.sleep(0.2)
        time.sleep(1.4)
        shot(ws, os.path.join(args.out, "compress.png"))
        print("完成")
        return 0
    finally:
        for p in (edge, srv):
            if p:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)],
                               capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
