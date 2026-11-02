"""界面端到端回归：菜单 / 工具栏 / 列表选择 / 压缩 / 解压 / 归档视图 / 复制 / 确认对话框。

用真实 Edge（headless + CDP）驱动页面，断言可见行为而不只是接口返回。
测试数据放在系统临时目录，不污染仓库；未安装 Edge 时输出 SKIP 并返回 0。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp_client import WS, pick_page  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = tempfile.mkdtemp(prefix="cse_ui_probe_")
TESTDIR = os.path.join(WORK, "data")
PORT = 8907
DEBUG_PORT = 9337

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def find_edge():
    for p in EDGE_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def mkfixtures():
    os.makedirs(TESTDIR, exist_ok=True)
    with open(os.path.join(TESTDIR, "alpha.txt"), "w", encoding="utf-8") as f:
        f.write("CSE layout probe line\n" * 4000)
    os.makedirs(os.path.join(TESTDIR, "sub"), exist_ok=True)
    with open(os.path.join(TESTDIR, "sub", "beta.txt"), "w", encoding="utf-8") as f:
        f.write("nested payload\n" * 2000)


def mk_other_archives():
    """造几种常见格式的压缩包，用于验证"能解市面常见格式"。"""
    import zipfile
    alpha = os.path.join(TESTDIR, "alpha.txt")
    beta = os.path.join(TESTDIR, "sub", "beta.txt")
    made = {}

    zp = os.path.join(TESTDIR, "probe.zip")
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(alpha, "alpha.txt")
        zf.write(beta, "sub/beta.txt")
    made["zip"] = zp

    out = os.path.join(TESTDIR, "probe.tar.gz")
    import tarfile
    with tarfile.open(out, "w:gz") as tf:
        tf.add(alpha, "alpha.txt")
        tf.add(beta, "sub/beta.txt")
    made["tar.gz"] = out

    seven = ""
    for p in (r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if os.path.isfile(p):
            seven = p
            break
    if seven:
        sp = os.path.join(TESTDIR, "probe.7z")
        # 用相对路径 + cwd，7z 才会保留 sub/ 目录层级
        r = subprocess.run([seven, "a", "-t7z", "-y", sp, "alpha.txt", "sub"],
                           cwd=TESTDIR, capture_output=True, text=True)
        if r.returncode == 0 and os.path.isfile(sp):
            made["7z"] = sp
    return made


def wait_http(url, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def post(path, payload):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}",
                                data=json.dumps(payload).encode(),
                                headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=5).read().decode())


def main():
    edge_path = find_edge()
    if not edge_path:
        print("SKIP 未检测到 Microsoft Edge，跳过界面回归")
        return 0
    mkfixtures()
    env = dict(os.environ)
    srv = subprocess.Popen([sys.executable, "run.py", "ui", "--no-browser", f"--port={PORT}"],
                           cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    edge = None
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))

    try:
        if not wait_http(f"http://127.0.0.1:{PORT}/", 25):
            print("FAIL 本地服务未启动")
            return 1

        prof = os.path.join(WORK, "_edgeprofile")
        edge = subprocess.Popen([edge_path, "--headless=new", "--disable-gpu",
                                 f"--remote-debugging-port={DEBUG_PORT}",
                                 f"--user-data-dir={prof}", "--window-size=1320,860",
                                 f"http://127.0.0.1:{PORT}/"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        target = pick_page(DEBUG_PORT)
        ws = WS(target["webSocketDebuggerUrl"])
        ws.call("Runtime.enable")
        ws.call("Page.enable")
        # 确保调试的是应用页面（新开的 headless 实例可能先给出空白页）
        ws.call("Page.navigate", url=f"http://127.0.0.1:{PORT}/")
        ready = False
        for _ in range(60):
            time.sleep(0.3)
            try:
                if ws.eval("document.querySelectorAll('#menubar [data-cmd]').length > 0"):
                    ready = True
                    break
            except Exception:
                continue
        if not ready:
            print("FAIL 应用页面未就绪；target url =", target.get("url"))
            return 1
        time.sleep(1.2)

        # 1. 基本结构
        check("页面加载完成", ws.eval("document.readyState") == "complete")
        n_cmd = ws.eval("document.querySelectorAll('#menubar [data-cmd]').length")
        check("菜单命令项已渲染", (n_cmd or 0) >= 30, f"{n_cmd} 项")
        dup = ws.eval("""(() => {
          const seen = {}, dup = [];
          document.querySelectorAll('#menubar [data-cmd]').forEach(b => {
            const k = b.dataset.cmd;
            if (seen[k]) dup.push(k); else seen[k] = 1;
          });
          return dup; })()""")
        check("菜单无重复命令项", not dup, json.dumps(dup, ensure_ascii=False))
        tools = ws.eval("document.querySelectorAll('#toolbar .tool[data-act]:not(#themeBtn)').length")
        check("工具栏 7 个操作按钮", tools == 7, f"{tools} 个")
        heads = ws.eval("[...document.querySelectorAll('#fileHead th')].map(t=>t.textContent).join(',')")
        check("表头为资源管理器式列", heads == "名称,大小,修改时间,创建时间,注释,文件夹,文件", heads)

        # 1.1 列头点击排序（升 → 降 → 换列）
        keys = ws.eval("[...document.querySelectorAll('#fileHead th')].map(t=>t.dataset.sort||'').join(',')")
        check("列头带排序键", keys == "name,size,mtime,ctime,,,", keys)

        # 1.2 无边框窗口的边缘拉伸热区（前端这一半）。
        # 真实鼠标拖动只能在解锁的桌面上验证（锁屏时鼠标事件被锁屏吃掉），
        # 这里把 pywebview.api 换成桩，确认 6px 边缘的 pointerdown 确实发出了
        # start_resize（bit: 1 左 2 右 4 上 8 下），中间区域不误触。
        resz = ws.eval("""(() => {
          const calls = [];
          window.pywebview = {api: {start_resize: (k, x, y) => calls.push([k, x, y])}};
          const fire = (x, y) => document.dispatchEvent(new PointerEvent('pointerdown',
              {clientX: x, clientY: y, button: 0, bubbles: true, cancelable: true}));
          const W = innerWidth, H = innerHeight, mx = Math.floor(W/2), my = Math.floor(H/2);
          fire(2, my); fire(W - 2, my); fire(mx, 2); fire(mx, H - 2); fire(mx, my);
          delete window.pywebview;
          return {bits: calls.map(c => c[0]), n: calls.length};
        })()""")
        check("6px 边缘把缩放交给原生（左1/右2/上4/下8，中间不误触）",
              str(resz.get("bits")) == "[1, 2, 4, 8]", json.dumps(resz))

        curs = ws.eval("""(() => {
          const mv = (x, y) => document.dispatchEvent(new PointerEvent('pointermove',
              {clientX: x, clientY: y, bubbles: true}));
          const W = innerWidth, H = innerHeight, mx = Math.floor(W/2), my = Math.floor(H/2);
          const out = [];
          mv(2, my); out.push(document.documentElement.style.cursor);
          mv(W - 2, my); out.push(document.documentElement.style.cursor);
          mv(mx, 2); out.push(document.documentElement.style.cursor);
          mv(mx, H - 2); out.push(document.documentElement.style.cursor);
          mv(mx, my); out.push(document.documentElement.style.cursor);
          return out;
        })()""")
        check("边缘光标提示（左右 ew-resize / 上下 ns-resize / 中间复位）",
              curs[:4] == ["ew-resize", "ew-resize", "ns-resize", "ns-resize"]
              and curs[4] == "", json.dumps(curs))
        sort = ws.eval("""(() => {
          const click = (k) => document.querySelector(`#fileHead th[data-sort="${k}"]`).click();
          const mark = (k) => {
            const th = document.querySelector(`#fileHead th[data-sort="${k}"]`);
            return th.classList.contains('asc') ? 'asc'
                 : th.classList.contains('desc') ? 'desc' : '';
          };
          const sizes = () => [...document.querySelectorAll('#fileBody tr.row:not(.row-up)')]
            .map(r => r.querySelectorAll('td')[1].textContent.trim())
            .map(t => {
              const m = t.match(/^([\\d.]+)\\s*(B|KB|MB|GB)?$/);
              if (!m) return null;
              const u = {B:1, KB:1024, MB:1048576, GB:1073741824}[m[2] || 'B'];
              return parseFloat(m[1]) * u;
            }).filter(v => v !== null);
          click('size');  const first = mark('size');  const asc = sizes();
          click('size');  const second = mark('size'); const desc = sizes();
          click('name');  const third = mark('name');
          const groups = {dir: [], file: []};
          document.querySelectorAll('#fileBody tr.row:not(.row-up)').forEach(r => {
            const nm = r.querySelector('.nm').textContent;
            const isDir = !!r.querySelector('.ico.dir');
            groups[isDir ? 'dir' : 'file'].push(nm);
          });
          const sorted = (arr) => {
            const want = arr.slice().sort((x, y) => x.localeCompare(y, 'zh', {numeric: true}));
            return JSON.stringify(arr) === JSON.stringify(want);
          };
          return {first, second, third, asc, desc,
                  dirSorted: sorted(groups.dir), fileSorted: sorted(groups.file),
                  sample: groups.dir.slice(0, 3).concat(groups.file.slice(0, 3))};
        })()""")
        check("点「大小」首击为升序", sort["first"] == "asc", sort["first"])
        check("再点同一列切降序", sort["second"] == "desc", sort["second"])
        check("换列后回到升序且标记跟随", sort["third"] == "asc", sort["third"])
        check("升序时大小单调不减",
              all(a <= b for a, b in zip(sort["asc"], sort["asc"][1:])), sort["asc"][:6])
        check("降序时大小单调不增",
              all(a >= b for a, b in zip(sort["desc"], sort["desc"][1:])), sort["desc"][:6])
        check("按名称排序：文件夹在前且各自升序",
              bool(sort["dirSorted"]) and bool(sort["fileSorted"]), sort["sample"])
        sb = ws.eval("""(() => ({hasVer: !!document.getElementById('statVer'),
          text: document.querySelector('.statusbar').textContent.replace(/\\s+/g,' ')}))()""")
        check("右下角不显示版本号", (not sb["hasVer"]) and "0.1.0" not in sb["text"], sb["text"][:60])

        # 2. 五个菜单都能展开
        opened = ws.eval("""(() => {
          const out = {};
          document.querySelectorAll('#menubar .menu').forEach(m => {
            const b = m.querySelector('.menu-btn'); b.click();
            out[m.dataset.menu] = !m.querySelector('.menu-pop').hidden;
            b.click();
          });
          return out;
        })()""")
        check("文件/编辑/查看/工具/设置/关于 菜单均可展开", all(opened.values()), json.dumps(opened, ensure_ascii=False))

        # 2b. 点头部空白处（非菜单区域）应收起菜单
        cls = ws.eval("""(() => {
          const pop = document.querySelector('[data-menu="view"] .menu-pop');
          document.querySelector('[data-menu="view"] .menu-btn').click();
          const before = !pop.hidden;
          document.querySelector('#menubar .mb-spacer').click();
          return {before, after: !pop.hidden}; })()""")
        check("单击头部空白处菜单自动收起", cls["before"] is True and cls["after"] is False,
              json.dumps(cls, ensure_ascii=False))

        # 2c. 右上角主题按钮可切换（来回切两次回到原主题）
        th = ws.eval("""(() => {
          const root = document.documentElement;
          const t0 = root.dataset.theme;
          document.getElementById('themeBtn').click();
          const t1 = root.dataset.theme;
          document.getElementById('themeBtn').click();
          return {t0, t1, t2: root.dataset.theme}; })()""")
        check("主题按钮可切换深浅色", th["t1"] and th["t1"] != th["t0"] and th["t2"] == th["t0"],
              json.dumps(th, ensure_ascii=False))

        # 3. 关于对话框
        ws.eval("""document.querySelector('[data-cmd="about"]').click(); true""")
        time.sleep(0.5)
        about = ws.eval("""(() => { const d = document.getElementById('dlg');
          return {open: !d.hidden, title: document.getElementById('dlgTitle').textContent,
                  body: document.getElementById('dlgBody').textContent.replace(/\\s+/g,' ').slice(0,120)}; })()""")
        check("关于对话框弹出且含版本", about["open"] and "0.1.0" in about["body"], about["title"])
        ws.eval("document.getElementById('dlgClose').click(); true")

        # 3b. 支持的压缩格式清单
        ws.eval("""document.querySelector('[data-cmd="formats"]').click(); true""")
        time.sleep(0.6)
        fm = ws.eval("""(() => ({open: !document.getElementById('dlg').hidden,
          title: document.getElementById('dlgTitle').textContent,
          text: document.getElementById('dlgBody').textContent.replace(/\\s+/g,' ')}))()""")
        check("支持格式对话框列出常见格式",
              fm["open"] and fm["title"] == "支持的压缩格式"
              and all(k in fm["text"] for k in ("ZIP", "7-Zip", "RAR", "ISO")),
              fm["text"][:70])
        ws.eval("document.getElementById('fmOk').click(); true")

        # 4. 跳转到测试目录
        ws.eval(f"""(() => {{
          document.getElementById('pathInput').value = {json.dumps(TESTDIR)};
          document.getElementById('navGo').click(); return true; }})()""")
        time.sleep(1.2)
        rows = ws.eval("document.querySelectorAll('#fileBody tr.row').length")
        check("目录导航生效并列出项目", rows >= 2, f"{rows} 行")
        path_now = ws.eval("document.getElementById('pathInput').value")
        check("路径栏显示当前目录", os.path.normcase(path_now) == os.path.normcase(TESTDIR), path_now)

        # 5. 点击选中 → 状态栏联动
        ws.eval("document.querySelector('#fileBody tr.row').click(); true")
        st = ws.eval("document.getElementById('statSel').textContent")
        check("单击选中并更新状态栏", st.startswith("选定 1 /"), st)

        # 6. 双击文件夹进入
        ws.eval("""(() => {
          const tr = [...document.querySelectorAll('#fileBody tr.row')]
            .find(t => t.textContent.includes('sub'));
          tr.dispatchEvent(new MouseEvent('dblclick', {bubbles:true}));
          return true; })()""")
        time.sleep(1.2)
        inside = ws.eval("document.getElementById('pathInput').value")
        check("双击文件夹进入子目录", inside.replace("/", "\\").lower().endswith("\\sub"), inside)

        # 7. 上一级返回
        ws.eval("document.getElementById('navUp').click(); true")
        time.sleep(1.2)
        back = ws.eval("document.getElementById('pathInput').value")
        check("上一级按钮返回上级目录", os.path.normcase(back) == os.path.normcase(TESTDIR), back)

        # 8. 多选（文件 + 子文件夹）→ 工具栏「添加」→ 压缩对话框
        ws.eval("""(() => {
          const rows = [...document.querySelectorAll('#fileBody tr.row')];
          const a = rows.find(t => t.textContent.includes('alpha.txt'));
          const s = rows.find(t => t.textContent.includes('sub'));
          a.click();
          s.dispatchEvent(new MouseEvent('click', {bubbles:true, ctrlKey:true}));
          document.querySelector('#toolbar .tool[data-act="add"]').click();
          return true; })()""")
        time.sleep(0.7)
        cdlg = ws.eval("""(() => { const d = document.getElementById('dlg');
          return {open: !d.hidden, title: document.getElementById('dlgTitle').textContent,
                  hasSrc: !!document.querySelector('.src-preview'),
                  srcText: (document.querySelector('.src-preview')||{}).textContent,
                  sel: document.getElementById('statSel').textContent}; })()""")
        check("添加 → 打开压缩设置对话框", cdlg["open"] and cdlg["title"] == "压缩设置", cdlg["title"])
        check("对话框带入所选源项目", cdlg["hasSrc"] and "alpha.txt" in (cdlg["srcText"] or ""), str(cdlg["srcText"])[:40])
        check("Ctrl 多选文件与文件夹", str(cdlg["sel"]).startswith("选定 2 /"), str(cdlg["sel"]))

        # 9. 开始压缩 → 任务条走到 100%
        out = os.path.join(TESTDIR, "ui_probe.cse")
        ws.eval(f"""document.getElementById('cOut').value = {json.dumps(out)}; true""")
        ws.eval("document.getElementById('cStart').click(); true")
        prog = {}
        for _ in range(50):
            time.sleep(0.4)
            prog = ws.eval("""(() => ({hidden: document.getElementById('taskbar').hidden,
              pct: document.getElementById('tkPct').textContent,
              label: document.getElementById('tkLabel').textContent,
              done: document.getElementById('taskbar').classList.contains('is-done'),
              stat: document.getElementById('statResult').textContent}))()""")
            if prog["done"]:
                break
        check("任务条进度到 100% 并标记完成", prog.get("done") is True and prog.get("pct") == "100%",
              json.dumps(prog, ensure_ascii=False)[:120])
        check("任务条显示完成详情", str(prog.get("label", "")).startswith("完成"), str(prog.get("label"))[:60])
        check("状态栏显示操作结果", str(prog.get("stat", "")).startswith("✓"), str(prog.get("stat"))[:50])
        check("归档文件已生成", os.path.isfile(out), out)

        # 10. 双击归档 → 归档视图
        ws.eval("document.getElementById('navRefresh').click(); true")
        time.sleep(1.4)
        ws.eval("""(() => {
          const tr = [...document.querySelectorAll('#fileBody tr.row')]
            .find(t => t.textContent.includes('ui_probe.cse'));
          tr.dispatchEvent(new MouseEvent('dblclick', {bubbles:true}));
          return true; })()""")
        time.sleep(1.8)
        arc = ws.eval("""(() => ({p: document.getElementById('pathInput').value,
          arch: document.body.classList.contains('view-archive'),
          rows: document.querySelectorAll('#fileBody tr.row').length,
          info: document.getElementById('statInfo').textContent}))()""")
        check("双击归档进入归档视图", arc["arch"] is True and arc["p"].startswith("归档："), str(arc["p"])[:50])
        check("归档条目列表有内容", arc["rows"] >= 1, f'{arc["rows"]} 条 · {arc["info"]}')

        # 10b. 归档内层级：根目录只显示 alpha.txt 与 sub 文件夹（不展开成一行层）
        root = ws.eval("""(() => {
          const rows = [...document.querySelectorAll('#fileBody tr.row')];
          return {names: rows.map(r => r.dataset.path),
                  hasSub: rows.some(r => r.textContent.includes('sub')),
                  hasBeta: rows.some(r => r.textContent.includes('beta.txt'))}; })()""")
        check("归档根目录按层级折叠显示", root["hasSub"] and not root["hasBeta"], json.dumps(root["names"], ensure_ascii=False)[:100])

        # 10c. 双击归档内文件夹进入子目录
        ws.eval("""(() => {
          const tr = [...document.querySelectorAll('#fileBody tr.row')]
            .find(t => t.textContent.includes('sub'));
          tr.dispatchEvent(new MouseEvent('dblclick', {bubbles:true}));
          return true; })()""")
        time.sleep(1.0)
        sub = ws.eval("""(() => {
          const rows = [...document.querySelectorAll('#fileBody tr.row')];
          return {p: document.getElementById('pathInput').value,
                  names: rows.map(r => r.dataset.path),
                  hasBeta: rows.some(r => r.textContent.includes('beta.txt')),
                  hasUp: !!document.querySelector('#fileBody tr.row-up')}; })()""")
        check("双击归档内文件夹进入子目录", sub["p"].endswith("\\sub") and sub["hasBeta"],
              str(sub["p"])[:60])
        check("子目录内有「..」返回行", sub["hasUp"] is True, json.dumps(sub["names"], ensure_ascii=False)[:80])

        # 10d. 「..」返回归档根
        ws.eval("""document.querySelector('#fileBody tr.row-up')
          .dispatchEvent(new MouseEvent('dblclick', {bubbles:true})); true""")
        time.sleep(1.0)
        backroot = ws.eval("""(() => ({p: document.getElementById('pathInput').value,
          hasSub: [...document.querySelectorAll('#fileBody tr.row')].some(r => r.textContent.includes('sub')),
          hasUp: !!document.querySelector('#fileBody tr.row-up')}))()""")
        check("「..」返回归档根目录", backroot["hasSub"] and not backroot["hasUp"], str(backroot["p"])[:60])

        # 11. 返回磁盘
        ws.eval("document.getElementById('navDisk').click(); true")
        time.sleep(1.4)
        back2 = ws.eval("""(() => ({p: document.getElementById('pathInput').value,
          arch: document.body.classList.contains('view-archive')}))()""")
        check("返回磁盘恢复目录视图", back2["arch"] is False and os.path.normcase(back2["p"]) == os.path.normcase(TESTDIR), back2["p"])

        # 12. 删除确认对话框（只打开，不确认）
        ws.eval("""(() => {
          const tr = document.querySelector('#fileBody tr.row');
          tr.click(); return true; })()""")
        time.sleep(0.3)
        ws.eval("""document.querySelector('#toolbar .tool[data-act="delete"]').click(); true""")
        time.sleep(0.6)
        deldlg = ws.eval("""(() => { const b = document.getElementById('dlgBody');
          return {open: !document.getElementById('dlg').hidden,
                  hasWarn: !!document.querySelector('.warn-box'),
                  text: b ? b.textContent.replace(/\\s+/g,' ').slice(0,80) : ''}; })()""")
        check("删除弹出回收站确认警告", deldlg["open"] and deldlg["hasWarn"], deldlg["text"][:50])
        ws.eval("document.getElementById('delCancel').click(); true")
        time.sleep(0.3)

        # 13. Ctrl+A 全选
        ws.eval("document.getElementById('listwrap').focus(); true")
        ws.eval("""document.dispatchEvent(new KeyboardEvent('keydown', {key:'a', ctrlKey:true, bubbles:true})); true""")
        time.sleep(0.3)
        all_sel = ws.eval("document.getElementById('statSel').textContent")
        check("Ctrl+A 全选", "选定 " in all_sel and " / " in all_sel and all_sel.split("/")[0].split()[-1] != "0", all_sel)

        # 14. 归档视图 → 解压到指定目录
        ws.eval("""(() => {
          const tr = [...document.querySelectorAll('#fileBody tr.row')]
            .find(t => t.textContent.includes('ui_probe.cse'));
          tr.click(); tr.dispatchEvent(new MouseEvent('dblclick', {bubbles:true}));
          return true; })()""")
        time.sleep(1.6)
        ws.eval("""document.querySelector('#toolbar .tool[data-act="extract"]').click(); true""")
        time.sleep(0.7)
        xdlg = ws.eval("""(() => ({open: !document.getElementById('dlg').hidden,
          title: document.getElementById('dlgTitle').textContent,
          arc: (document.getElementById('xArc')||{}).value || ''}))()""")
        outdir = os.path.join(TESTDIR, "out")
        check("归档视图下点「解压」打开解压对话框",
              xdlg["open"] and xdlg["title"] == "解压到" and xdlg["arc"].endswith("ui_probe.cse"),
              json.dumps(xdlg, ensure_ascii=False)[:90])
        ws.eval(f"""document.getElementById('xDest').value = {json.dumps(outdir)}; true""")
        ws.eval("document.getElementById('xStart').click(); true")
        ok = {}
        for _ in range(50):
            time.sleep(0.4)
            ok = ws.eval("""(() => ({done: document.getElementById('taskbar').classList.contains('is-done'),
              label: document.getElementById('tkLabel').textContent}))()""")
            if ok["done"]:
                break
        check("解压任务完成", ok.get("done") is True, str(ok.get("label"))[:60])
        check("解压产出目录已创建", os.path.isdir(outdir) and bool(os.listdir(outdir)), outdir)

        # 15. 信息对话框
        ws.eval("document.getElementById('navDisk').click(); true")
        time.sleep(1.3)
        ws.eval("""(() => {
          const tr = [...document.querySelectorAll('#fileBody tr.row')]
            .find(t => t.textContent.includes('alpha.txt'));
          tr.click();
          document.querySelector('#toolbar .tool[data-act="info"]').click();
          return true; })()""")
        time.sleep(1.0)
        info = ws.eval("""(() => ({open: !document.getElementById('dlg').hidden,
          title: document.getElementById('dlgTitle').textContent,
          body: document.getElementById('dlgBody').textContent.replace(/\\s+/g,' ').slice(0,160)}))()""")
        check("信息对话框显示路径与时间", info["open"] and info["title"] == "信息" and "alpha.txt" in info["body"],
              info["body"][:70])
        ws.eval("document.getElementById('dlgClose').click(); true")

        # 16. 复制到指定文件夹
        copydst = os.path.join(TESTDIR, "copydst")
        os.makedirs(copydst, exist_ok=True)
        ws.eval("""document.querySelector('#toolbar .tool[data-act="copy"]').click(); true""")
        time.sleep(1.0)
        modal_open = ws.eval("!document.getElementById('modal').hidden")
        check("复制到 → 打开文件夹选择器", modal_open is True)
        ws.eval(f"""(() => {{ document.getElementById('modalPath').value = {json.dumps(copydst)};
          document.getElementById('goBtn').click(); return true; }})()""")
        time.sleep(1.0)
        ws.eval("document.getElementById('pickDirBtn').click(); true")
        time.sleep(1.8)
        check("复制完成且目标存在",
              os.path.isfile(os.path.join(copydst, "alpha.txt")), copydst)

        # 17. 常见格式（zip / tar.gz / 7z）都要能打开并按层级展示、能解出文件
        made = mk_other_archives()
        check("已准备常见格式样本", set(made) >= {"zip", "tar.gz"},
              json.dumps(sorted(made), ensure_ascii=False))
        for fmt, apath in made.items():
            ws.eval("document.getElementById('navRefresh').click(); true")
            time.sleep(1.2)
            base = os.path.basename(apath)
            ws.eval(f"""(() => {{
              const tr = [...document.querySelectorAll('#fileBody tr.row')]
                .find(t => t.textContent.includes({json.dumps(base)}));
              if (tr) tr.dispatchEvent(new MouseEvent('dblclick', {{bubbles:true}}));
              return true; }})()""")
            time.sleep(1.8)
            a2 = ws.eval("""(() => {
              const rows = [...document.querySelectorAll('#fileBody tr.row')];
              return {arch: document.body.classList.contains('view-archive'),
                      p: document.getElementById('pathInput').value,
                      names: rows.map(r => r.dataset.path)}; })()""")
            check(f"{fmt} 归档能在界面打开并保留层级",
                  a2["arch"] and any("sub" in (n or "") for n in a2["names"]),
                  json.dumps(a2["names"], ensure_ascii=False)[:80])

            if fmt in ("zip", "7z", "tar.gz"):
                dst = os.path.join(TESTDIR, "out_" + fmt.replace(".", "_"))
                ws.eval("""document.querySelector('#toolbar .tool[data-act="extract"]').click(); true""")
                time.sleep(0.7)
                ws.eval(f"""(() => {{ const el = document.getElementById('xDest');
                  if (el) el.value = {json.dumps(dst)}; return true; }})()""")
                ws.eval("document.getElementById('xStart').click(); true")
                done = {}
                for _ in range(60):
                    time.sleep(0.4)
                    done = ws.eval("""(() => ({done: document.getElementById('taskbar')
                      .classList.contains('is-done'),
                      label: document.getElementById('tkLabel').textContent}))()""")
                    if done["done"]:
                        break
                got = [os.path.join(r, f) for r, _d, fs in os.walk(dst) for f in fs]
                check(f"{fmt} 归档能解压出文件", bool(got) and done.get("done") is True,
                      f"{len(got)} 个文件 · {str(done.get('label'))[:40]}")
                ws.eval("document.getElementById('navDisk').click(); true")
                time.sleep(1.0)

        # 18. 检查控制台是否有 JS 错误提示（toast 里的错误）
        err_toasts = ws.eval("[...document.querySelectorAll('.toast.err')].map(t=>t.textContent)")
        check("无运行时错误提示", not err_toasts, json.dumps(err_toasts, ensure_ascii=False)[:120])

        # 截图存档（仅在显式要求时保留，避免产生仓库垃圾文件）
        if os.environ.get("CSE_PROBE_SHOT") == "1":
            import base64
            shot = ws.call("Page.captureScreenshot", format="png")
            out = os.path.join(WORK, "probe.png")
            with open(out, "wb") as f:
                f.write(base64.b64decode(shot["data"]))
            print("screenshot:", out)

        print(f"\n通过 {len(passed)} 项，失败 {len(failed)} 项")
        if failed:
            print("失败项：" + "；".join(failed))
            return 1
        return 0
    finally:
        if edge:
            edge.terminate()
            try:
                edge.wait(timeout=6)
            except Exception:
                edge.kill()
        srv.terminate()
        try:
            srv.wait(timeout=6)
        except Exception:
            srv.kill()
        shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
