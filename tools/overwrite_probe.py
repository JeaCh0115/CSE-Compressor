"""覆盖保护探针：目标同名时必须先提醒、再让用户确认，不允许静默替换。

用户报的是「同名没有提醒，直接就给覆盖了」——一次右键压缩就能把同名旧归档
整包换掉，解压则是逐条顶掉用户改过的文件。这里在真机上走完几条路径：

  A. 压缩默认名撞上磁盘上已有的 报告.zip → 自动改成 报告_压缩.zip，并挂提示；
  B. 用户手动改回原名 → 弹确认；选「另存为」→ 旧文件一个字节都不动；
  C. 再来一次选「覆盖」→ 旧文件才被替换；
  D. 解压到已有同名文件的目录 → 显示冲突数，选「换个文件夹」不碰旧文件；
  E. 明确选「仍然解压到这里」→ 这时才覆盖。

用法：
  python tools/overwrite_probe.py
  python tools/overwrite_probe.py --keep     # 保留临时目录看现场
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
PORT = 9353
WORK = os.path.join(tempfile.gettempdir(), "cse_overwrite_test")

RESULTS = []


def rec(ok, name, detail=""):
    RESULTS.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def _child():
    sys.path.insert(0, ROOT)
    import webview
    from csgui.server import set_launch_request

    set_launch_request({"action": "add", "paths": [os.path.join(WORK, "报告.txt")]})
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
    return pids


def _post(base, path, payload):
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode() or "{}")


def _wait_dlg(ws, secs=20):
    for _ in range(int(secs * 10)):
        if ws.eval("!document.querySelector('#dlg').hidden"):
            return True
        time.sleep(0.1)
    return False


def _wait_sheet(ws, secs=8):
    for _ in range(int(secs * 10)):
        if ws.eval("!!document.querySelector('#dlg .sheet')"):
            return True
        time.sleep(0.1)
    return False


def _click_sheet(ws, key):
    ws.eval("""(() => {
      const b = document.querySelector('#dlg .sheet [data-k="%s"]');
      if (b) b.click();
    })()""" % key)


def _job_title(ws):
    return ws.eval("document.querySelector('#cmpTitle') ? document.querySelector('#cmpTitle').textContent : ''")


def _wait_job(ws, prev="", secs=60, until=None):
    """等任务出结果，返回 (标题, 是否成功)。

    prev 是点按钮之前面板上的旧标题：上一次任务的「压缩完成」会留在 DOM 里，
    不等它换掉就会把上一次的结果当成本次完成（第一版探针就这么误判过）。
    连续两次同类型任务时标题根本不会变，所以再给一个落地判据 until
    （文件真被写出来才算数）。
    """
    for _ in range(int(secs * 5)):
        title = _job_title(ws)
        if title and title != prev and ("完成" in title or "失败" in title):
            return title, ("失败" not in title)
        if until is not None:
            try:
                if until():
                    return title or "完成", True
            except OSError:
                pass
        time.sleep(0.2)
    return "", False


def _hint(ws, sel):
    return ws.eval("""(() => {
      const h = document.querySelector('%s');
      return h && !h.hidden ? h.textContent : '';
    })()""" % sel)


def _set_out(ws, path):
    ws.eval("""(() => {
      const el = document.querySelector('#cOut');
      el.value = %s;
      el.dispatchEvent(new Event('input', {bubbles: true}));
    })()""" % json.dumps(path))


def _pick_zip(ws):
    ws.eval("""(() => {
      const b = [...document.querySelectorAll('#cFormats .pill')].find(x => x.dataset.fmt === 'zip');
      if (b) b.click();
    })()""")


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _parent():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from cdp_client import WS, pick_page

    _kill_windows()
    time.sleep(0.6)
    env = dict(os.environ, CSE_OW_CHILD="1", PYTHONPATH=ROOT)
    proc = subprocess.Popen([sys.executable, os.path.abspath(__file__)], cwd=ROOT, env=env)
    ws = None
    try:
        t = pick_page(PORT, 60)
        ws = WS(t["webSocketDebuggerUrl"])
        ws.call("Runtime.enable")
        base = ws.eval("location.origin")
        if not _wait_dlg(ws):
            rec(False, "压缩对话框已打开", "没等到对话框")
            return

        zip_path = os.path.join(WORK, "报告.zip")
        alt_path = os.path.join(WORK, "报告_压缩.zip")
        old_bytes = _read(zip_path)

        # 默认格式是 CSE-X（.cse），不会和已存在的 报告.zip 撞名，先切到 ZIP
        _pick_zip(ws)
        time.sleep(1.0)

        # ---- A：默认名撞上已有文件 ----
        out = ws.eval("document.querySelector('#cOut').value")
        rec(os.path.basename(out) == "报告_压缩.zip", "默认输出自动避开已有同名文件", out)
        hint = _hint(ws, "#cOver")
        rec("同名文件已存在" in hint, "输出框下面挂出提醒", hint[:48])

        # ---- B：手改回原名 → 必须弹确认；选「另存为」 ----
        _set_out(ws, zip_path)
        time.sleep(0.6)
        prev = _job_title(ws)
        ws.eval("document.querySelector('#cStart').click()")
        ok = _wait_sheet(ws)
        rec(ok, "改成已存在的名字后弹出确认", "等待确认层")
        if ok:
            title = ws.eval("document.querySelector('#dlg .sheet h4').textContent")
            body = ws.eval("document.querySelector('#dlg .sheet').textContent")
            rec("目标文件已存在" in title, "确认层标题说明原因", title)
            rec("无法恢复" in body, "确认层提示覆盖不可逆", body[:40].replace("\n", " ").strip())
            alt_label = ws.eval("""(() => {
              const b = document.querySelector('#dlg .sheet [data-k="alt"]');
              return b ? b.textContent : '';
            })()""")
            rec("另存为" in alt_label, "默认动作是另存一份", alt_label)
            _click_sheet(ws, "alt")
            title2, done = _wait_job(ws, prev, until=lambda: os.path.exists(alt_path))
            rec(done, "选另存后压缩完成", title2)
            rec(_read(zip_path) == old_bytes, "原 报告.zip 一个字节没动")
            rec(os.path.exists(alt_path), "新版落到 报告_压缩.zip")

        # ---- C：再来一次，这次选「覆盖」 ----
        _post(base, "/api/launch", {"action": "add", "paths": [os.path.join(WORK, "报告.txt")]})
        if not _wait_dlg(ws):
            rec(False, "第二次压缩对话框", "没打开")
            return
        time.sleep(0.8)
        _pick_zip(ws)
        _set_out(ws, zip_path)
        time.sleep(0.6)
        prev = _job_title(ws)
        ws.eval("document.querySelector('#cStart').click()")
        ok = _wait_sheet(ws)
        rec(ok, "再次确认（覆盖路径）")
        if ok:
            _click_sheet(ws, "over")
            title, done = _wait_job(ws, prev, until=lambda: _read(zip_path) != old_bytes)
            rec(done, "选覆盖后压缩完成", title)
            rec(_read(zip_path) != old_bytes, "选覆盖后旧文件才被替换")

        # ---- D：解压到已有同名文件的目录 ----
        arc = os.path.join(WORK, "pack.zip")
        dest = os.path.join(WORK, "pack_解压")
        os.makedirs(dest, exist_ok=True)
        old_txt = os.path.join(dest, "a.txt")
        with open(old_txt, "wb") as f:
            f.write(b"OLD-CONTENT")
        _post(base, "/api/launch", {"action": "extract", "paths": [arc]})
        if not _wait_dlg(ws):
            rec(False, "解压面板已打开", "没打开")
            return
        rec(True, "解压面板已打开", ws.eval("document.querySelector('#xDest').value"))
        time.sleep(0.8)
        xh = _hint(ws, "#xOver")
        rec("已有" in xh, "解压面板提示目标目录非空", xh[:44])
        prev = _job_title(ws)
        ws.eval("document.querySelector('#xStart').click()")
        ok = _wait_sheet(ws)
        rec(ok, "解压到非空目录弹出确认")
        if ok:
            title = ws.eval("document.querySelector('#dlg .sheet h4').textContent")
            rec("1 个文件将被覆盖" in title, "确认层给出冲突文件数", title)
            _click_sheet(ws, "alt")
            new_dir = os.path.join(WORK, "pack_解压2")
            title2, done = _wait_job(ws, prev,
                                     until=lambda: os.path.exists(os.path.join(new_dir, "a.txt")))
            rec(done, "选换个文件夹后解压完成", title2)
            rec(_read(old_txt) == b"OLD-CONTENT", "目标目录里的旧 a.txt 没被改")
            rec(os.path.exists(os.path.join(new_dir, "a.txt")), "内容解到了新目录 pack_解压2")

        # ---- E：解压并明确选「仍然解压到这里」 ----
        _post(base, "/api/launch", {"action": "extract", "paths": [arc]})
        if not _wait_dlg(ws):
            rec(False, "第二次解压面板", "没打开")
            return
        time.sleep(0.8)
        prev = _job_title(ws)
        ws.eval("document.querySelector('#xStart').click()")
        ok = _wait_sheet(ws)
        rec(ok, "第二次解压确认")
        if ok:
            _click_sheet(ws, "over")
            title, done = _wait_job(ws, prev, until=lambda: _read(old_txt) == b"NEW-CONTENT")
            rec(done, "选仍然解压后完成", title)
            rec(_read(old_txt) == b"NEW-CONTENT", "选覆盖后旧 a.txt 才被替换")
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
    if os.environ.get("CSE_OW_CHILD"):
        return _child()

    os.makedirs(WORK, exist_ok=True)
    import shutil
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    # 压缩源 + 一个同名旧归档（内容标记 OLD，用来判断有没有被替换）
    with open(os.path.join(WORK, "报告.txt"), "w", encoding="utf-8") as f:
        f.write("hello overwrite\n")
    with zipfile.ZipFile(os.path.join(WORK, "报告.zip"), "w") as z:
        z.writestr("OLD-MARK.txt", "old archive")
    # 解压用归档 + 已经解过一次的目录（里面那份 a.txt 是"用户改过的版本"）
    with zipfile.ZipFile(os.path.join(WORK, "pack.zip"), "w") as z:
        z.writestr("a.txt", "NEW-CONTENT")

    _parent()

    if "--keep" not in argv:
        shutil.rmtree(WORK, ignore_errors=True)
    ok = sum(1 for r in RESULTS if r[0])
    print(f"\n{ok}/{len(RESULTS)} 通过")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
