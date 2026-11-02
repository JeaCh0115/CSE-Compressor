"""全自动开发流水线（一键完成 开发→质量→演示→发布）。

用法: python tools/dev_pipeline.py [--skip demo formats ui package]

流程:
  1. clean    清理构建产物与 __pycache__
  2. compile  全量字节码编译（语法体检）
  3. tests    单元测试套件（引擎 + 独家格式 + 多格式）
  4. demo     压缩率报表 + CSE-X 独家能力（固实/去重/加密）对比
  5. formats  多格式矩阵：zip / tar / tar.gz / gz / bz2 / xz / cse 创建→解压→校验
  6. ui       现代化界面服务冒烟：静态资源 / API / 上传 / 加密压缩任务全链路
  7. package  用本引擎自己打包发布件 dist/CSE-Compressor-<ver>.cse（dogfood）
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from csengine import __version__, converters  # noqa: E402
from csengine.compressors import (  # noqa: E402
    compress_bytes, decompress_bytes, METHODS, METHOD_NAMES, pick_best,
)

LINE = "=" * 74


class Pipeline:
    def __init__(self):
        self.results = []

    def step(self, name):
        def deco(fn):
            def run(*a, **kw):
                print(f"\n{LINE}\n[步骤] {name}\n{LINE}")
                t0 = time.time()
                try:
                    detail = fn() or ""
                    ok = True
                except Exception as exc:
                    detail = f"{type(exc).__name__}: {exc}"
                    ok = False
                dt = time.time() - t0
                self.results.append((name, ok, dt, detail))
                print(f"{'✅ 通过' if ok else '❌ 失败'} ({dt:.2f}s) {detail}")
                if not ok:
                    raise SystemExit(f"流水线在步骤 [{name}] 失败，已中止。")
                return detail
            return run
        return deco

    def report(self):
        print(f"\n{LINE}\n流水线总结\n{LINE}")
        all_ok = True
        for name, ok, dt, _ in self.results:
            print(f"  {'✅' if ok else '❌'} {name:<28} {dt:6.2f}s")
            all_ok &= ok
        print(f"\n结论: {'全部通过，发布件就绪 🎉' if all_ok else '存在失败'}")
        return all_ok


pl = Pipeline()


@pl.step("clean 清理构建产物")
def step_clean():
    removed = 0
    for root, dirs, _files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", ".workbuddy", "dist", "build")]
        if "__pycache__" in dirs:
            shutil.rmtree(os.path.join(root, "__pycache__"), ignore_errors=True)
            dirs.remove("__pycache__")
            removed += 1
    dist = os.path.join(ROOT, "dist")
    if os.path.isdir(dist):
        shutil.rmtree(dist, ignore_errors=True)
        removed += 1
    # 注意：~/.cse_upload 是应用运行时的上传暂存（用户文件），不属于构建产物；
    # 不在流水线里批量删除，需要清理时走应用「文件 → 清空上传暂存」（带确认）。
    return f"清理 {removed} 项"


@pl.step("compile 全量字节码编译")
def step_compile():
    import compileall
    import re
    skip_re = re.compile(r"(\.workbuddy|/dist/|/build/|\\dist\\|\\build\\)")
    if not compileall.compile_dir(ROOT, quiet=2, force=True, rx=skip_re):
        raise RuntimeError("存在编译错误")
    return "全部模块编译通过"


@pl.step("tests 单元测试")
def step_tests():
    import unittest
    suite = unittest.defaultTestLoader.discover(os.path.join(ROOT, "tests"),
                                                top_level_dir=ROOT)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError(f"{len(result.failures)} 失败 / {len(result.errors)} 错误")
    return f"{result.testsRun} 个测试全部通过"


def _make_corpus(tmp):
    files = {}
    files["report.txt"] = ("CSE Compressor 支持 CSE-X 独家格式，"
                           "固实压缩 + 内容去重 + ChaCha20 加密。") * 320
    files["data.json"] = json.dumps(
        [{"id": i, "name": f"item-{i}", "tags": ["alpha", "beta"], "score": i % 100}
         for i in range(600)], ensure_ascii=False, indent=2)
    files["pixels.bin"] = bytes(i % 7 * 36 for i in range(64 * 1024))
    files["noise.bin"] = os.urandom(24 * 1024)
    files["zeros.bin"] = bytes(32 * 1024)
    out = {}
    for name, data in files.items():
        raw = data.encode("utf-8") if isinstance(data, str) else data
        with open(os.path.join(tmp, name), "wb") as f:
            f.write(raw)
        out[name] = raw
    return out


@pl.step("demo 压缩率报表 + CSE-X 独家能力")
def step_demo():
    py = sys.executable
    with tempfile.TemporaryDirectory(prefix="cse_demo_") as tmp:
        files = _make_corpus(tmp)
        print(f"\n{'文件':<14}{'原始':>10}" + "".join(f"{m:>10}" for m in METHODS) + "  auto")
        print("-" * 76)
        for name, data in sorted(files.items()):
            row = f"{name:<14}{len(data):>10}"
            for m in METHODS:
                blob = compress_bytes(data, m)
                assert decompress_bytes(blob, m, len(data)) == data, f"回环失败: {name}/{m}"
                row += f"{len(blob) / max(len(data), 1) * 100:>9.1f}%"
            auto_code, auto = pick_best(data)
            assert decompress_bytes(auto, auto_code, len(data)) == data
            row += f"{len(auto) / max(len(data), 1) * 100:>8.1f}%  ({METHOD_NAMES[auto_code]})"
            print(row)

        # ---- CSE-X 独家能力对比 ----
        src = os.path.join(tmp, "proj")
        os.makedirs(os.path.join(src, "assets"))
        for name, data in files.items():
            with open(os.path.join(src, name), "wb") as f:
                f.write(data)
        for i in range(6):  # 6 个完全重复的素材，验证去重
            with open(os.path.join(src, "assets", f"copy{i}.bin"), "wb") as f:
                f.write(b"SHARED-ASSET-CONTENT" * 3000)

        results = {}
        for label, kwargs in [
            ("普通（不固实/不去重）", dict(solid=False, dedup=False)),
            ("固实压缩", dict(solid=True, dedup=False)),
            ("固实 + 去重", dict(solid=True, dedup=True)),
        ]:
            arc = os.path.join(tmp, f"a_{len(results)}.cse")
            info = converters.create(arc, [src], fmt="cse", **kwargs)
            results[label] = (os.path.getsize(arc), info)

        print("\nCSE-X 独家能力对比（同一份语料）:")
        base = results["普通（不固实/不去重）"][0]
        for label, (size, info) in results.items():
            gain = (1 - size / base) * 100
            print(f"  {label:<22} {size:>8} 字节   数据块 {info.get('blobs', '-'):>3}   "
                  f"较基准 {gain:+.1f}%")

        secret = os.path.join(tmp, "secret.cse")
        t0 = time.time()
        converters.create(secret, [src], fmt="cse", password="demo-pass", solid=True)
        enc_time = time.time() - t0
        dest = os.path.join(tmp, "dec")
        converters.extract(secret, dest, password="demo-pass")
        ok = 0
        for n, data in files.items():
            with open(os.path.join(dest, "proj", n), "rb") as f:
                ok += f.read() == data
        print(f"\n加密归档: {os.path.getsize(secret)} 字节, 压缩+加密耗时 {enc_time:.2f}s, "
              f"解密校验 {ok}/{len(files)} 通过")

        cli_out = os.path.join(tmp, "cli.cse")
        cmds = [
            [py, os.path.join(ROOT, "run.py"), "c", src, "-o", cli_out, "-m", "lzma"],
            [py, os.path.join(ROOT, "run.py"), "l", cli_out],
            [py, os.path.join(ROOT, "run.py"), "t", cli_out],
            [py, os.path.join(ROOT, "run.py"), "c", os.path.join(src, "report.txt"),
             "-o", os.path.join(tmp, "cli.zip"), "-f", "zip"],
        ]
        for cmd in cmds:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if r.returncode != 0:
                raise RuntimeError(f"CLI 失败: {' '.join(cmd[2:])}\n{r.stderr}")
        print("CLI 冒烟: c / l / t / zip 全部通过")
        return "5 方法回环 + 固实/去重/加密 + CLI 全部正确"


@pl.step("formats 多格式互操作矩阵")
def step_formats():
    with tempfile.TemporaryDirectory(prefix="cse_fmt_") as tmp:
        src = os.path.join(tmp, "proj")
        os.makedirs(os.path.join(src, "sub"))
        payload = b"format matrix " * 900
        with open(os.path.join(src, "a.txt"), "wb") as f:
            f.write(payload)
        with open(os.path.join(src, "sub", "b.bin"), "wb") as f:
            f.write(bytes(range(256)) * 40)

        matrix = [("zip", "zip"), ("tar", "tar"), ("tar.gz", "targz"), ("gz", "gz"),
                  ("bz2", "bz2"), ("xz", "xz"), ("cse", "cse")]
        print(f"\n{'格式':<10}{'归档大小':>10}  {'条目':>4}  回环校验")
        print("-" * 46)
        for ext, fmt in matrix:
            single = os.path.join(src, "a.txt")
            inputs = [single] if fmt in ("gz", "bz2", "xz") else [src]
            arc = os.path.join(tmp, f"m_{fmt}.{ext}")
            converters.create(arc, inputs, fmt=fmt)
            dest = os.path.join(tmp, "out_" + fmt)
            converters.extract(arc, dest)
            items = converters.list_items(arc)
            check = (os.path.join(dest, items[0]["name"]) if fmt in ("gz", "bz2", "xz")
                     else os.path.join(dest, "proj", "a.txt"))
            with open(check, "rb") as f:
                assert f.read() == payload, f"{fmt} 回环失败"
            print(f"{fmt:<10}{os.path.getsize(arc):>10}  {len(items):>4}  ✓")
            assert converters.test(arc), f"{fmt} 完整性测试失败"
        return f"{len(matrix)} 种格式全部回环 + 完整性通过"


@pl.step("ui 现代化界面服务冒烟")
def step_ui():
    import urllib.parse
    from csgui.server import GuiServer

    srv = GuiServer(port=8899)
    port = srv.start(port_scan=12)
    base = f"http://127.0.0.1:{port}"

    def get(path):
        with urllib.request.urlopen(base + path, timeout=15) as r:
            return r.read()

    def post(path, body=None, raw=None):
        data = raw if raw is not None else json.dumps(body or {}).encode()
        req = urllib.request.Request(base + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def wait_job(jid, timeout=60):
        info = {}
        for _ in range(int(timeout / 0.2)):
            info = json.loads(get("/api/job?id=" + jid))
            if info["status"] in ("done", "error"):
                return info
            time.sleep(0.2)
        return info

    original = None
    try:
        html = get("/").decode("utf-8")
        assert "CSE Compressor" in html and "<title>" in html, "首页渲染异常"
        assert "menubar" in html and 'data-menu="about"' in html, "菜单栏缺失"
        assert 'id="toolbar"' in html and 'id="fileHead"' in html, "资源管理器式布局缺失"
        css, js = get("/static/app.css"), get("/static/app.js")
        assert len(css) > 4000 and len(js) > 6000, "静态资源异常"
        fav = get("/favicon.png")
        assert fav[:8] == b"\x89PNG\r\n\x1a\n", "favicon 异常"
        env = json.loads(get("/api/env"))
        assert env["version"] == __version__ and env["methods"], "env 接口异常"

        about = json.loads(get("/api/about"))
        assert about["opensource"] and about["credits"], "about 数据缺失"
        assert "打包" not in about["build_time"] or about["build_id"] == "dev"
        original = json.loads(get("/api/settings"))   # 备份用户设置，结束后还原
        st = json.loads(get("/api/settings"))
        assert {"theme", "format", "method"} <= set(st), "settings 键缺失"
        saved = post("/api/settings", {"theme": "dark", "format": "cse"})
        assert saved["theme"] == "dark", "settings 保存失败"
        post("/api/settings/reset")
        assoc = json.loads(get("/api/associate_status"))
        assert "supported" in assoc, "associate_status 异常"

        with tempfile.TemporaryDirectory(prefix="cse_ui_") as tmp:
            b = json.loads(get("/api/browse?path=" + urllib.parse.quote(tmp)))
            assert b["path"] and "dirs" in b

            up = post("/api/upload?name=demo.txt&rel=demo.txt&token=pipeline",
                      raw=b"upload via web ui " * 200)
            assert os.path.exists(up["path"]), "上传落盘失败"

            copied = os.path.join(tmp, "copydst")
            os.makedirs(copied, exist_ok=True)
            fs = post("/api/fs", {"op": "copy", "paths": [up["path"]], "dest": copied})
            assert fs["count"] == 1 and os.path.exists(os.path.join(copied, "demo.txt")), "复制接口失败"

            dest = os.path.join(tmp, "ui.cse")
            job = post("/api/job", {
                "action": "compress", "sources": [up["path"]], "dest": dest,
                "format": "cse", "method": "auto", "solid": True,
                "password": "ui-pass", "comment": "UI 冒烟",
            })
            info = wait_job(job["id"])
            assert info["status"] == "done", f"压缩任务失败: {info.get('error')}"
            assert os.path.getsize(info["result"]["dest"]) > 0

            prev = json.loads(get("/api/preview?path=" + urllib.parse.quote(dest)
                                  + "&password=ui-pass"))
            assert prev["entries"] and prev["summary"]["encrypted"] is True

            job2 = post("/api/job", {"action": "extract", "archive": dest,
                                     "dest": os.path.join(tmp, "un"),
                                     "password": "ui-pass"})
            info2 = wait_job(job2["id"])
            assert info2["status"] == "done", f"解压任务失败: {info2.get('error')}"
            with open(os.path.join(tmp, "un", "demo.txt"), "rb") as f:
                assert f.read() == b"upload via web ui " * 200

        return f"首页/工具栏/关于/设置/API/上传/加密压缩/预览/解压/复制 全通过 (端口 {port})"
    finally:
        if original:
            try:
                post("/api/settings", original)   # 还原用户设置，避免流水线改动本机偏好
            except Exception:
                pass
        srv.stop()


@pl.step("ui-probe 界面端到端回归（真实浏览器点击）")
def step_ui_probe():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "ui_probe.py")],
                       cwd=ROOT, capture_output=True, text=True, timeout=420)
    out = (r.stdout or "") + (r.stderr or "")
    if "SKIP" in out and r.returncode == 0:
        return "未检测到 Edge，跳过界面回归"
    if r.returncode != 0:
        tail = "\n".join(out.strip().splitlines()[-12:])
        raise AssertionError("界面回归失败：\n" + tail)
    line = [x for x in out.strip().splitlines() if x.startswith("通过 ")]
    return line[-1] if line else "界面回归通过"


@pl.step("desktop 桌面外壳导入自检")
def step_desktop():
    try:
        import webview  # noqa: F401
    except ImportError:
        return "未安装 pywebview，跳过（开发环境可选）"
    from csgui import desktop  # noqa: F401
    from csengine import winassoc
    assert winassoc.PROGID
    return "pywebview / 桌面外壳 / 文件关联模块导入正常"


@pl.step("win Windows 打包（exe + 安装程序 + 安装自检）")
def step_win():
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "build_win.py"), "--skip-icons", "--verify"],
        cwd=ROOT, timeout=1200)
    if r.returncode != 0:
        raise RuntimeError(f"build_win 失败 (exit {r.returncode})")
    from csengine import version
    setup = os.path.join(ROOT, "dist", f"CSE-Compressor-{version.__version__}-Setup.exe")
    assert os.path.isfile(setup), "安装程序未生成"
    return f"Setup: {os.path.getsize(setup) / 1048576:.1f} MB"


@pl.step("window 原生窗口行为（默认 860×500 / 可拉伸 / 任务栏可最小化）")
def step_window():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "window_probe.py")],
                       cwd=ROOT, capture_output=True, text=True, timeout=240)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        tail = "\n".join(out.strip().splitlines()[-10:])
        raise AssertionError("窗口行为校验失败：\n" + tail)
    line = [x for x in out.strip().splitlines() if x.startswith("通过 ")]
    return line[-1] if line else "窗口行为校验通过"


@pl.step("package 用本引擎打包发布件（dogfood）")
def step_package():
    from csengine.exclusive import ExclusiveWriter
    dist = os.path.join(ROOT, "dist")
    os.makedirs(dist, exist_ok=True)
    out = os.path.join(dist, f"CSE-Compressor-v{__version__}.cse")
    skip = {".git", ".workbuddy", "dist", "build", "__pycache__"}
    w = ExclusiveWriter(out, method="auto", solid=True, dedup=True,
                        comment=f"CSE Compressor v{__version__} 发布件")
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            if f.endswith(".pyc"):
                continue
            p = os.path.join(root, f)
            w.add(p, arcname=os.path.relpath(p, ROOT).replace(os.sep, "/"))
    info = w.write()
    summary = converters.summary_of(out)
    print(f"发布件: {out}")
    print(f"  条目 {info['entries']} · 数据块 {info['blobs']} · "
          f"{info['orig_size']} → {info['comp_size']} 字节 "
          f"({info['comp_size'] / max(info['orig_size'], 1) * 100:.1f}%)")
    print(f"  格式: {summary['format']} · 固实: {summary['solid']} · 去重: {summary['dedup']}")
    with tempfile.TemporaryDirectory() as td:
        converters.extract(out, td)
        assert os.path.exists(os.path.join(td, "run.py")), "发布件解压校验失败"
    return f"dist/{os.path.basename(out)} 验证可解压"


def main():
    ap = argparse.ArgumentParser(description="CSE Compressor 全自动开发流水线")
    ap.add_argument("--skip", nargs="*", default=[],
                    choices=["clean", "compile", "tests", "demo", "formats", "ui",
                             "ui-probe", "desktop", "win", "window", "package"])
    ap.add_argument("--win", action="store_true",
                    help="追加 Windows 打包（exe + 安装程序 + 安装/卸载自检）")
    args = ap.parse_args()
    t0 = time.time()
    print(f"{LINE}\nCSE Compressor v{__version__} 全自动开发流水线\n{LINE}")
    pipeline = [("clean", step_clean), ("compile", step_compile), ("tests", step_tests),
                ("demo", step_demo), ("formats", step_formats), ("ui", step_ui),
                ("ui-probe", step_ui_probe),
                ("desktop", step_desktop), ("window", step_window), ("package", step_package)]
    if args.win:
        pipeline.insert(-2, ("win", step_win))
    try:
        for name, fn in pipeline:
            if name not in args.skip:
                fn()
    except SystemExit as exc:
        print(exc)
        return 1
    ok = pl.report()
    print(f"总耗时 {time.time() - t0:.2f}s")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
