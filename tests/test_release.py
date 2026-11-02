"""版本 / 设置 / 格式识别 / 集成模块测试。"""

import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import csengine  # noqa: E402
from csengine import formats, version  # noqa: E402


class TestVersion(unittest.TestCase):
    def test_beta_channel(self):
        self.assertEqual(version.CHANNEL, "beta")
        self.assertTrue(version.is_beta())
        self.assertIn("beta", csengine.__version__)
        # 版本号与四段整数保持一致（大版本号可自由重置，不必硬编码）
        semver = tuple(int(x) for x in csengine.__version__.split("-")[0].split("."))
        self.assertEqual(version.VERSION_TUPLE[:3], semver)

    def test_build_info_exists(self):
        self.assertTrue(version.BUILD_TIME)
        self.assertTrue(version.BUILD_ID)

    def test_about_data(self):
        a = version.about_data()
        self.assertEqual(a["product"], "CSE Compressor")
        self.assertGreaterEqual(len(a["opensource"]), 5)
        self.assertGreaterEqual(len(a["credits"]), 3)
        self.assertTrue(a["license"])
        # 每个开源条目字段完整
        for p in a["opensource"]:
            self.assertTrue(p["name"] and p["license"] and p["use"])


class TestEntryScripts(unittest.TestCase):
    """打包入口脚本必须真的能跑起来（缺 __main__ 会让 exe 静默退出，极难排查）。"""

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _src(self, name):
        with open(os.path.join(self.ROOT, "packaging", name), encoding="utf-8") as f:
            return f.read()

    def test_entry_has_main_guard(self):
        for name in ("app_entry.py", "cli_entry.py"):
            src = self._src(name)
            self.assertIn('if __name__ == "__main__"', src, f"{name} 缺少 __main__ 调用块")
            self.assertIn("def main(", src, f"{name} 缺少 main()")

    def test_entry_imports_os(self):
        # cli_entry 用过 os.path，忘记 import os 会在冻结态直接 NameError
        src = self._src("cli_entry.py")
        self.assertIn("import os", src, "cli_entry.py 未导入 os")

    def test_entry_compiles(self):
        import py_compile
        for name in ("app_entry.py", "cli_entry.py"):
            py_compile.compile(os.path.join(self.ROOT, "packaging", name), doraise=True)


class TestSplash(unittest.TestCase):
    """启动页模板必须能渲染（占位符残留/格式化报错都会让窗口根本出不来）。"""

    def test_splash_renders(self):
        from csgui.desktop import _splash_html
        html = _splash_html()
        self.assertIn("CSE Compressor", html)
        for token in ("__BG__", "__FG__", "__MUTED__", "__TRACK__"):
            self.assertNotIn(token, html)
        self.assertNotIn("%(", html)

    def test_window_created_hidden(self):
        """窗口必须以 hidden 建出来：否则 WebView2 出画前会闪一块纯色（看起来全黑）。"""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "csgui", "desktop.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("hidden=True", src, "窗口未以隐藏状态创建，会闪黑屏")
        self.assertIn("_reveal_when_painted", src, "缺少「首帧就绪再显示」的兜底逻辑")

    def test_splash_bg_matches_theme(self):
        """窗口底色必须与启动页一致，否则深色底 + 浅色启动页会闪一下黑。"""
        from csgui import desktop, settings
        tmp = tempfile.mkdtemp(prefix="cse_splash_")
        old = os.environ.get("APPDATA")
        os.environ["APPDATA"] = tmp
        try:
            settings.save({"theme": "light"})
            light_bg, light_html = desktop._splash_bg(), desktop._splash_html()
            settings.save({"theme": "dark"})
            dark_bg, dark_html = desktop._splash_bg(), desktop._splash_html()
        finally:
            if old is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = old
        self.assertEqual(light_bg.lower(), "#f4f6fb")
        self.assertEqual(dark_bg.lower(), "#0b1020")
        self.assertIn(light_bg, light_html)
        self.assertIn(dark_bg, dark_html)
        self.assertNotEqual(light_bg, dark_bg)


class TestSetupInstaller(unittest.TestCase):
    """安装包打开时也不能闪黑：窗口要「首帧就绪才显示」且底色是浅色。"""

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _read(self, *parts):
        with open(os.path.join(self.ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def _setup_src(self):
        return self._read("packaging", "installer", "setup_app.py")

    def _setup_bg(self):
        m = re.search(r'SETUP_BG\s*=\s*"(#[0-9a-fA-F]{6})"', self._setup_src())
        self.assertIsNotNone(m, "setup_app.py 缺少 SETUP_BG 常量")
        return m.group(1)

    def test_window_created_hidden(self):
        src = self._setup_src()
        self.assertIn("hidden=True", src, "安装窗口未以隐藏状态创建，会闪一块底色")
        self.assertIn("events.loaded", src, "缺少「首帧就绪再显示」的触发点")
        # loaded 万一不来也要把窗口亮出来，否则双击 exe 就像没反应
        self.assertIn("_reveal_guard", src, "缺少显示窗口的兜底线程")

    def test_offscreen_warmup(self):
        """窗口要先去屏幕外热身，等内容画出来再挪到中央——否则会闪一下空白。"""
        src = self._setup_src()
        for need in ("_offscreen_pos", "_center_pos", "_painted", "window.move"):
            self.assertIn(need, src, f"缺少屏幕外热身流程的 {need}")

    def test_window_bg_is_light(self):
        bg = self._setup_bg()
        r, g, b = (int(bg[i:i + 2], 16) for i in (1, 3, 5))
        self.assertGreater((r * 299 + g * 587 + b * 114) / 1000, 200,
                           f"安装器底色 {bg} 不是浅色，出画前会看成一团黑")

    def test_ui_bg_matches_window_bg(self):
        bg = self._setup_bg().lower()
        html = self._read("packaging", "installer", "ui.html").replace(" ", "").lower()
        self.assertIn(f"--bg:{bg}", html, "窗口底色与安装界面底色不一致，切换时会闪")

    def test_ui_is_light_theme(self):
        html = self._read("packaging", "installer", "ui.html").lower()
        self.assertNotIn("#0b1020", html, "安装界面不应再用深色底")
        for need in ("__mode__", "pywebview.api.install", "pywebview.api.confirm_uninstall"):
            self.assertIn(need, html)


class TestCompactWindows(unittest.TestCase):
    """右键压缩/解压的小窗：屏幕外先画好，再一次性出现在屏幕中央。

    之前的坑：窗口先显示启动页、再换正式界面，用户看到「什么都没有闪一下」；
    而且一显示就抢焦点，资源管理器里选中的文件跟着闪。
    """

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _read(self, *parts):
        with open(os.path.join(self.ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_desktop_offscreen_warmup(self):
        src = self._read("csgui", "desktop.py")
        for need in ("_offscreen_pos", "_painted", "SW_SHOWNOACTIVATE",
                     "compact_ready", "_place_compact"):
            self.assertIn(need, src, f"紧凑小窗缺少「屏幕外热身」流程的 {need}")

    def test_compact_ready_reported_by_frontend(self):
        js = self._read("csgui", "web", "app.js")
        self.assertIn("function compactReady", js, "前端没有上报「面板已就绪」")
        # 压缩面板、解压面板、启动兜底都要上报，少了哪个都会先露一块空白
        self.assertGreaterEqual(js.count("compactReady();"), 2,
                                "面板打开后没有上报就绪，窗口会停在屏幕外或提前露出")

    def test_compact_extract_asks_where(self):
        """右键解压必须先问「解压到哪」，不能闷头解到默认目录。"""
        js = self._read("csgui", "web", "app.js")
        i = js.index('req.action === "extract"')
        branch = js[i:i + 400]
        self.assertIn("openExtract", branch, "右键解压又变成直接解压了")
        self.assertNotIn("extractNow", branch, "右键解压不应跳过设置面板")

    def test_extract_panel_has_dest_picker(self):
        js = self._read("csgui", "web", "app.js")
        start = js.index("function openExtract")
        body = js[start:start + 3600]
        # 第三项只认前缀：选完目录还要重建"目录非空"提示，回调会跟着改
        for need in ("xDest", "xDestPick", 'openBrowser("xDest", "dir"'):
            self.assertIn(need, body, f"解压面板缺少 {need}")

    def test_window_buttons_have_no_native_tooltip(self):
        """窗口按钮不要再带 title：鼠标一悬停就冒出「最小化」的黑底提示。"""
        for parts in (("csgui", "web", "index.html"), ("packaging", "installer", "ui.html")):
            html = self._read(*parts)
            for tag in re.findall(r'<button class="tb-btn[^>]*>', html):
                self.assertNotIn("title=", tag,
                                 f"{'/'.join(parts)} 的窗口按钮还带 title 提示")

    def test_compact_integrity_test_keeps_window(self):
        """小窗里点「完整性测试」只换面板，不能当成取消把窗口关掉。"""
        js = self._read("csgui", "web", "app.js")
        start = js.index('$("#xTest").onclick')
        body = js[start:start + 500]
        self.assertIn("keep", body, "「完整性测试」会把紧凑小窗一起关掉（少了 keep）")
        self.assertNotIn("closeDlg();", body, "「完整性测试」又用了不带 keep 的 closeDlg")

    def test_compress_output_never_hits_source(self):
        """默认输出名不能等于任何一个源文件：x.zip 压成 ZIP 会算成 x.zip 自己。

        引擎端也有拦截，但默认值本身就该是对的，否则用户只能看到一句报错。
        """
        js = self._read("csgui", "web", "app.js")
        start = js.index("function openCompress")
        body = js[start:js.index("function openExtract")]
        self.assertIn("avoidClash", body, "压缩默认输出名没有避开源文件")
        self.assertIn("_压缩", body, "撞名时没有给出可用的备用名")
        # 手动改的名字也要在发出去之前挡一次
        submit = body[body.index('$("#cStart").onclick'):]
        self.assertIn("takenBySource", submit[:1200], "点「开始压缩」前没有再检查撞名")

    def test_extract_verb_single_selection(self):
        """多选文件时不该冒出「解压」：extract 动词的 MultiSelectModel 必须是 Single。"""
        src = self._read("csengine", "winassoc.py")
        # ① 注册右键菜单（SystemFileAssociations\<ext>\shell\CSE.Extract）
        self.assertIn('multi="Single"', src,
                      "解压动词没有按 Single（单选）注册：多选时会冒出来")
        # ② 归档 ProgID 自带的那条解压动词
        i = src.index("def _write_archive_progid")
        block = src[i:i + 2500]
        self.assertIn('"MultiSelectModel", 0, winreg.REG_SZ, "Single"', block.replace("\n", " ")
                      .replace("  ", " "),
                      "归档 ProgID 的解压动词不是 Single")
        # 压缩要保留 Player：多选压缩是主力场景，额度 100 项
        self.assertIn('multi: str = "Player"', src, "压缩动词的默认额度被改窄，多选会整条消失")

    def test_compress_dialog_fits_one_page(self):
        """压缩设置要一页放完：两栏版式（左源项目自带滚动，右设置项），主体不滚。"""
        js = self._read("csgui", "web", "app.js")
        css = self._read("csgui", "web", "app.css")
        start = js.index("function openCompress")
        body = js[start:start + 4000]
        for need in ("cmp-src", "cmp-opts", '"cmp"'):
            self.assertIn(need, body, f"压缩对话框缺少两栏结构 {need}")
        self.assertIn("#dlgBody.cmp-body", css, "缺少两栏版式的样式")
        self.assertIn(".cmp-src .src-preview", css, "源项目列表没有独立滚动区")
        # 紧凑小窗不能又长回去：一页放完的前提是窗口别太高
        py = self._read("csgui", "desktop.py")
        m = re.search(r"COMPACT_SIZE = \((\d+), (\d+)\)", py)
        self.assertTrue(m, "找不到 COMPACT_SIZE")
        self.assertLessEqual(int(m.group(2)), 600,
                             "紧凑压缩窗太高，设置项又会被挤得要滚动")


class TestOverwriteGuard(unittest.TestCase):
    """目标已存在时必须提醒，绝不静默覆盖。

    同名同目录压缩一次就能把旧归档整包换掉，解压则会逐条顶掉用户改过的文件；
    这类"数据没了"的路径要在三层都设防：前端提示 + 提交确认 + 引擎拒绝。
    """

    def setUp(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.root = root

    def _read(self, *parts):
        with open(os.path.join(self.root, *parts), encoding="utf-8") as f:
            return f.read()

    def test_engine_has_overwrite_switch(self):
        src = self._read("csengine", "converters.py")
        self.assertIn("overwrite: bool = True", src, "create() 没有 overwrite 开关")
        self.assertIn("已取消覆盖", src, "目标存在时没有可读的拒绝文案")
        self.assertIn("def unique_path", src, "缺少避开覆盖的候选名生成")
        self.assertIn("def conflicts", src, "缺少解压覆盖数量统计")

    def test_server_passes_overwrite_and_exposes_routes(self):
        src = self._read("csgui", "server.py")
        self.assertIn('payload.get("overwrite"', src, "压缩任务没有把 overwrite 传给引擎")
        for route in ("/api/pathinfo", "/api/freename", "/api/extract_conflicts"):
            self.assertIn(route, src, f"缺少接口 {route}")

    def test_frontend_warns_and_confirms(self):
        js = self._read("csgui", "web", "app.js")
        self.assertIn("function askOverwrite", js, "没有覆盖确认层")
        # 压缩：提示条 + 提交前确认 + payload 带 overwrite
        start = js.index("function openCompress")
        body = js[start:js.index("function openExtract")]
        self.assertIn('id="cOver"', body, "压缩对话框没有「已存在」提示位")
        self.assertIn("pathinfo", body, "压缩没有查目标是否已存在")
        submit = body[body.index('$("#cStart").onclick'):]
        self.assertIn("askOverwrite", submit[:1400], "点「开始压缩」时没有确认覆盖")
        self.assertIn("overwrite,", submit, "提交时没有带 overwrite 标记")
        # 解压：查询冲突数 + 提交前确认
        ext = js[js.index("function openExtract"):js.index("function extractNow")]
        self.assertIn('id="xOver"', ext, "解压对话框没有「目录非空」提示位")
        self.assertIn("extract_conflicts", ext, "解压没有统计会被覆盖的文件数")
        self.assertIn("askOverwrite", ext, "点「开始解压」时没有确认覆盖")

    def test_confirm_layer_is_default_safe(self):
        """确认层里「覆盖」是危险色、另存一份是主按钮——默认动作必须是不毁文件那个。"""
        js = self._read("csgui", "web", "app.js")
        body = js[js.index("function askOverwrite"):js.index("function askOverwrite") + 1800]
        self.assertIn('data-k="over"', body)
        self.assertIn("btn danger", body, "覆盖按钮没有用危险色")
        self.assertLess(body.index('data-k="over"'), body.index('data-k="alt"'),
                        "另存一份应该排在覆盖之后（视觉主体）")
        self.assertIn("btn primary", body, "没有主按钮（另存/换目录）")
        css = self._read("csgui", "web", "app.css")
        self.assertIn(".sheet {", css, "确认层没有样式")
        self.assertIn("position: relative", css, "modal-card 没设定位，确认层会跑出卡片")


class TestSettings(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("APPDATA")
        self.tmp = tempfile.mkdtemp(prefix="cse_settings_")
        os.environ["APPDATA"] = self.tmp

    def tearDown(self):
        if self._old is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self._old

    def test_defaults_and_roundtrip(self):
        from csgui import settings
        s = settings.load()
        self.assertEqual(s["theme"], "dark")
        self.assertTrue(s["solid"])
        s2 = settings.save({"theme": "light", "window": {"w": 1000}})
        self.assertEqual(s2["theme"], "light")
        self.assertEqual(s2["window"]["w"], 1000)
        self.assertEqual(s2["window"]["h"], settings.DEFAULTS["window"]["h"])  # 其余保留
        s3 = settings.reset()
        self.assertEqual(s3["theme"], "dark")


class TestFormatsExtras(unittest.TestCase):
    def test_csex_magic(self):
        self.assertEqual(formats.detect_format(b"CSX2" + b"\x00" * 32), "CSE-X 归档 (本引擎)")
        self.assertEqual(formats.detect_format(b"CSE1" + b"\x00" * 32), "CSE 归档 (本引擎)")

    def test_describe_dir(self):
        d = formats.describe_path(tempfile.mkdtemp())
        self.assertTrue(d["is_dir"])
        self.assertEqual(d["format"], "目录")


class TestWindowsIntegration(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "仅 Windows")
    def test_associate_status_query(self):
        from csengine import winassoc
        self.assertIsInstance(winassoc.is_registered(), bool)

    def test_imports_guard(self):
        from csgui import settings  # noqa: F401
        if os.name == "nt":
            from csgui import desktop  # noqa: F401  # 导入不应启动窗口


class TestProcessDiscovery(unittest.TestCase):
    """冷启动速度的关键：判定"已否有实例"必须走毫秒级路径。

    旧实现逐个端口 urlopen，落在 Hyper-V 保留段里的端口连接会黑洞到超时，
    一次右键白等 4~5 秒（实测 4.6s）。这里把"没有实例时也要快"钉成断言。
    """

    def test_find_port_is_fast_when_idle(self):
        from csgui import runtime
        import time
        runtime.clear_runtime()
        t0 = time.perf_counter()
        port = runtime.find_running_port()
        cost = time.perf_counter() - t0
        self.assertIn(port, (0, *runtime.PORTS))
        self.assertLess(cost, 0.5, f"探测耗时 {cost * 1000:.0f}ms，疑似又回到逐个端口 connect")

    @unittest.skipUnless(os.name == "nt", "仅 Windows")
    def test_listening_ports_enumeration(self):
        from csgui import runtime
        ports = runtime.listening_ports()
        self.assertIsInstance(ports, set)
        self.assertTrue(all(isinstance(p, int) and 0 < p < 65536 for p in ports))

    def test_runtime_file_roundtrip(self):
        from csgui import runtime
        runtime.write_runtime(8770)
        try:
            data = runtime.read_runtime()
            self.assertEqual(data.get("port"), 8770)
            self.assertEqual(data.get("pid"), os.getpid())
        finally:
            runtime.clear_runtime()
        self.assertFalse(runtime.read_runtime())


class TestLaunchQueue(unittest.TestCase):
    """多选文件右键压缩：资源管理器按文件逐个调起进程，请求要并成一批。"""

    def setUp(self):
        from csgui import server
        server.take_launches()

    def test_add_requests_merge(self):
        from csgui import server
        server.set_launch_request({"action": "add", "paths": ["C:/a.txt"]})
        server.set_launch_request({"action": "add", "paths": ["C:/b.txt"]})
        server.set_launch_request({"action": "add", "paths": ["C:/a.txt", "C:/c.txt"]})
        q = server.take_launches()
        self.assertEqual(len(q), 1, q)
        self.assertEqual(q[0]["action"], "add")
        self.assertEqual(q[0]["paths"], ["C:/a.txt", "C:/b.txt", "C:/c.txt"])

    def test_other_actions_stay_separate(self):
        from csgui import server
        server.set_launch_request({"action": "add", "paths": ["C:/a.txt"]})
        server.set_launch_request({"action": "extract", "paths": ["C:/x.zip"]})
        q = server.take_launches()
        self.assertEqual([x["action"] for x in q], ["add", "extract"])


class TestCompressionSpeed(unittest.TestCase):
    """auto 策略的性能约束：不许把纯 Python 的 lzhuf 拉进自动候选，
    否则一块 2MB 要几百毫秒且长时间霸占 GIL（界面拖动直接卡死）。"""

    def test_auto_never_uses_lzhuf(self):
        from csengine import compressors as C
        calls = {"lzhuf": 0}
        orig = C.compress_bytes

        def spy(data, method="auto"):
            if isinstance(method, str) and method == "lzhuf":
                calls["lzhuf"] += 1
            return orig(data, method)

        text = b"compressor solid block dedup lzma zlib " * 20000   # ~800KB
        C.compress_bytes = spy
        try:
            code, blob = C.pick_best(text)
        finally:
            C.compress_bytes = orig
        self.assertEqual(calls["lzhuf"], 0, "auto 又用上 lzhuf 了")
        self.assertLess(len(blob), len(text) // 10)
        self.assertIn(C.METHOD_NAMES[code], ("zlib", "lzma"))

    def test_incompressible_is_fast(self):
        """随机数据（图片/视频/已压缩包）不该拿 lzma 去磨。"""
        import time
        from csengine import compressors as C
        data = os.urandom(8 * 1024 * 1024)
        t0 = time.perf_counter()
        code, blob = C.pick_best(data)
        dt = time.perf_counter() - t0
        self.assertEqual(code, 0, "压不动的数据应直接 store")
        self.assertEqual(len(blob), len(data))
        self.assertLess(dt, 1.5, f"探测耗时 {dt:.2f}s 过长")

    def test_big_blocks_roundtrip(self):
        """并行分块压缩后，块顺序必须与 manifest 一致（否则解压全乱）。"""
        import hashlib
        from csengine import converters
        tmp = tempfile.mkdtemp(prefix="cse_par_")
        src = os.path.join(tmp, "src")
        os.makedirs(src)
        names = []
        for i in range(6):
            p = os.path.join(src, f"big{i}.txt")
            body = (f"block-{i}-" .encode() + b"lorem ipsum dolor sit amet " * 120000)
            with open(p, "wb") as f:
                f.write(body)
            names.append((p, hashlib.sha256(body).hexdigest()))
        arc = os.path.join(tmp, "o.cse")
        info = converters.create(arc, [src], method="auto")
        self.assertGreaterEqual(info["blobs"], 3)
        dest = os.path.join(tmp, "out")
        converters.extract(arc, dest)
        for p, digest in names:
            with open(os.path.join(dest, "src", os.path.basename(p)), "rb") as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(), digest)


class TestArchiveProgId(unittest.TestCase):
    """统一图标的同时要接管双击打开：每种扩展名一个独立 ProgID。"""

    def test_progid_naming(self):
        from csengine import winassoc
        self.assertEqual(winassoc.archive_progid(".zip"), "CSE.Archive.zip")
        self.assertEqual(winassoc.archive_progid(".7z"), "CSE.Archive.7z")

    def test_openable_exts(self):
        from csengine import winassoc
        exts = winassoc.openable_exts()
        self.assertNotIn(".cse", exts, ".cse 走 register() 的专用 ProgID")
        self.assertNotIn(".tar.gz", exts, "双扩展名无法单独关联")
        for ext in (".zip", ".7z", ".rar", ".tar", ".gz", ".xz", ".iso"):
            self.assertIn(ext, exts)

    @unittest.skipUnless(os.name == "nt", "仅 Windows")
    def test_resolve_returns_string(self):
        from csengine import winassoc
        self.assertIsInstance(winassoc.resolve_open_exe(".zip"), str)


class TestThirdPartyNotices(unittest.TestCase):
    """分发二进制时保留第三方声明是硬性义务，不是"补充说明"。

    MIT / BSD-3 要求保留版权与许可全文；PyInstaller 引导代码是 GPL-2.0 +
    Bootloader Exception，随包分发必须附许可文本；py7zr 一系是 LGPL-2.1。
    这份文件由 tools/gen_notices.py 从实际打包的 dist-info 里生成，不许手写。
    """

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _read(self, *parts):
        with open(os.path.join(self.ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_notices_file_exists_and_lists_bundled_components(self):
        txt = self._read("THIRD-PARTY-NOTICES.md")
        for name in ("pywebview", "pythonnet", "clr_loader", "py7zr",
                     "pyppmd", "inflate64", "multivolumefile", "pycryptodomex"):
            self.assertIn(name, txt, f"第三方声明里没有 {name}")
        self.assertIn("PyInstaller", txt)
        self.assertIn("CPython", txt)
        self.assertIn("WebView2", txt)

    def test_notices_carry_full_license_texts(self):
        txt = self._read("THIRD-PARTY-NOTICES.md")
        self.assertIn("## 三、许可原文", txt)
        # 许可原文必须真的抄进来，不能只写个名字
        self.assertIn("Permission is hereby granted, free of charge", txt)   # MIT
        self.assertIn("GNU LESSER GENERAL PUBLIC LICENSE", txt)              # LGPL-2.1
        self.assertIn("Redistribution and use in source and binary forms", txt)  # BSD
        self.assertIn("Python Software Foundation", txt)                     # PSF
        self.assertGreater(len(txt), 20000, "许可原文太短，八成只写了摘要")

    def test_webview2_is_stated_as_not_redistributed(self):
        """WebView2 是系统组件，说成"随包分发"反而是错的（也可能违反 MS 条款）。"""
        txt = self._read("THIRD-PARTY-NOTICES.md")
        self.assertIn("不随本包分发", txt)
        self.assertIn("Microsoft", txt)

    def test_build_copies_notices_into_package(self):
        src = self._read("tools", "build_win.py")
        start = src.index("def make_payload")
        body = src[start:start + 1600]
        self.assertIn("THIRD-PARTY-NOTICES.md", body, "打包时没把第三方声明放进安装包")
        self.assertIn("LICENSE.txt", body, "打包时没把 MIT 正文放进安装包")
        self.assertIn("gen_notices.py", body, "声明没从实际打包结果重新生成")

    def test_readme_states_distribution_licenses(self):
        md = self._read("README.md")
        self.assertIn("THIRD-PARTY-NOTICES.md", md)
        self.assertIn("PyInstaller", md)
        self.assertIn("WebView2", md)

    def test_generator_is_importable(self):
        import py_compile
        py_compile.compile(os.path.join(self.ROOT, "tools", "gen_notices.py"), doraise=True)


class TestConsoleEncoding(unittest.TestCase):
    """英文版 Windows 控制台是 cp1252，中文输出会抛 UnicodeEncodeError。

    真机是中文系统（cp936）所以一直没看见；CI 上第一条「压缩中 -> ...」就把
    命令带走了。用 PYTHONIOENCODING 把流按 cp1252 钉死来复现。
    """

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _run(self, *args):
        import subprocess
        env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONPATH=self.ROOT)
        return subprocess.run([sys.executable, os.path.join(self.ROOT, "run.py"), *args],
                              cwd=self.ROOT, env=env, capture_output=True, timeout=120)

    def test_cli_chinese_output_on_cp1252_console(self):
        r = self._run("info", "README.md")
        self.assertEqual(r.returncode, 0,
                         "cp1252 控制台下命令崩了：" + r.stderr.decode("utf-8", "replace")[-600:])
        self.assertIn("路径", r.stdout.decode("utf-8", "replace"))

    def test_setup_console_is_idempotent(self):
        from cscli.console import setup_console
        setup_console()
        setup_console()
        self.assertTrue(callable(setup_console))


if __name__ == "__main__":
    unittest.main()

