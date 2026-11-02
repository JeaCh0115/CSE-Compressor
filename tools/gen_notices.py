"""生成 THIRD-PARTY-NOTICES.md：随安装包分发的组件、版本、许可证与许可原文。

用法
  python tools/gen_notices.py                 # 用 dist/ 里最新的 Portable.zip 作依据
  python tools/gen_notices.py --pkg <目录>     # 用解压后的包目录（内含 _internal/）
  python tools/gen_notices.py --pkg-dir <目录> # 同上，显式给出 _internal 的父目录

为什么要有这个文件
  · MIT / BSD-3 的硬性义务只有一条：分发时保留版权声明与许可全文；
  · PyInstaller 引导代码是 GPL-2.0 + Bootloader Exception，随包分发必须附许可文本；
  · py7zr / pyppmd / inflate64 / multivolumefile 是 LGPL-2.1+，除了许可文本，
    还应保证用户能替换这些库——本项目把它们作为独立 .pyd 放在 _internal 下，
    没有静态链接进 exe，替换即生效。
  许可原文一律从**实际打进包里的** dist-info 里抄，不手写、不凭记忆。
"""

import argparse
import glob
import hashlib
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 组件用途说明（写给人看的，别用"某依赖库"糊过去）
USE = {
    "pywebview": "桌面窗口外壳（Windows 上承载 Edge WebView2）",
    "pythonnet": "pywebview 的 WinForms 后端桥接（CLR 互操作）",
    "clr_loader": "pythonnet 的 .NET 运行时加载器",
    "py7zr": "7z 归档读写后端",
    "pyppmd": "py7zr 的 PPMd 解压依赖",
    "inflate64": "py7zr 的 deflate64 解压依赖",
    "multivolumefile": "py7zr 的分卷归档支持",
    "pycryptodomex": "py7zr 的 AES 加密支持（7z 加密归档）",
}

# py7zr 一系走 LGPL：独立模块随包分发，可替换
LGPL_NOTE = ("以独立模块（`_internal/` 下的 `.pyd`）随包分发，未静态链接进 exe；"
             "用户可直接替换同名模块，不受本项目代码约束。")


def _read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().replace("\r\n", "\n")


def _meta(dist_dir):
    """从 dist-info/METADATA 里取名字、版本、许可证声明。"""
    info = {"name": os.path.basename(dist_dir).rsplit("-", 1)[0],
            "version": "", "license": ""}
    meta = os.path.join(dist_dir, "METADATA")
    if not os.path.exists(meta):
        return info
    txt = _read(meta)
    for key, field in (("Name", "name"), ("Version", "version")):
        m = re.search(rf"^{key}: (.+)$", txt, re.M)
        if m:
            info[field] = m.group(1).strip()
    for pat in (r"^License-Expression: (.+)$", r"^License: (.+)$"):
        m = re.search(pat, txt, re.M)
        if m and m.group(1).strip() and m.group(1).strip() != "UNKNOWN":
            info["license"] = m.group(1).strip()
            break
    if not info["license"]:
        m = re.search(r"^Classifier: License :: (.+)$", txt, re.M)
        if m:
            info["license"] = m.group(1).strip()
    if not info["license"]:
        info["license"] = "见原文"
    if info["license"] == "见原文":
        for name, text in _license_texts(dist_dir):
            guess = _infer_license(text)
            if guess:
                info["license"] = guess
                break
    # 通用占位符换成好读的短名
    short = {
        "OSI Approved :: GNU Lesser General Public License v2 or later (LGPLv2+)": "LGPL-2.1-or-later",
        "OSI Approved :: BSD License": "BSD",
        "BSD, Public Domain": "BSD-2-Clause + Public Domain",
        "BSD 3-Clause License": "BSD-3-Clause",
    }
    info["license"] = short.get(info["license"], info["license"])
    return info


SKIP_TEXT = ("AUTHORS", "CONTRIBUTORS", "CHANGELOG", "HISTORY", "README", "METADATA")


def _infer_license(text):
    """METADATA 没写 License 时，从原文头几行猜一个短名（只认常见的几种）。"""
    head = text[:1200].lower()
    if "mit license" in head:
        return "MIT"
    if "gnu lesser general public license" in head:
        return "LGPL-2.1-or-later"
    if "gnu general public license" in head:
        return "GPL-2.0"
    if "apache license" in head:
        return "Apache-2.0"
    if "redistribution and use in source and binary forms" in head:
        return "BSD-3-Clause" if "neither the name" in head else "BSD-2-Clause"
    return ""


def _license_texts(dist_dir):
    out = []
    pats = ["LICENSE*", "COPYING*", "NOTICE*", "licenses/*", "licenses/*/*"]
    for pat in pats:
        for p in sorted(glob.glob(os.path.join(dist_dir, pat))):
            name = os.path.basename(p).upper()
            if any(name.startswith(s) for s in SKIP_TEXT):
                continue      # 作者名单/变更记录不是许可原文，别混进去
            if os.path.isfile(p) and os.path.getsize(p) > 40:
                out.append((os.path.basename(p), _read(p)))
    # 去重（同一目录里 LICENSE 与 licenses/LICENSE 常常是同一份）
    seen, uniq = set(), []
    for name, text in out:
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        uniq.append((name, text))
    return uniq


def _pyinstaller_license():
    """PyInstaller 的 COPYING.txt 在它自己的 dist-info/licenses 里。"""
    try:
        import importlib.metadata as md
    except ImportError:
        return None
    try:
        dist = md.distribution("pyinstaller")
    except md.PackageNotFoundError:
        return None
    for f in dist.files or []:
        s = str(f)
        if s.lower().endswith(("copying.txt", "license.txt")):
            p = dist.locate_file(f)
            if os.path.exists(p):
                return md.version("pyinstaller"), _read(p)
    return None


def collect(pkg_dir):
    """返回 (第三方包列表, 许可原文表)。"""
    internal = os.path.join(pkg_dir, "_internal") if os.path.isdir(
        os.path.join(pkg_dir, "_internal")) else pkg_dir
    packages = []
    for d in sorted(glob.glob(os.path.join(internal, "*.dist-info"))):
        if os.path.basename(d).startswith("setuptools"):
            continue          # 打包工具的残留，不是运行期组件
        info = _meta(d)
        info["texts"] = _license_texts(d)
        info["use"] = USE.get(info["name"].lower(), info["use"] if "use" in info else "")
        packages.append(info)
    return packages


def license_blocks(packages):
    """把许可原文按内容去重，返回 (blocks, key->id)。"""
    blocks, by_hash = [], {}
    for pkg in packages:
        refs = []
        for name, text in pkg["texts"]:
            h = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if h not in by_hash:
                letter = chr(ord("A") + len(blocks))
                by_hash[h] = letter
                title = f"{pkg['license']}"
                blocks.append({"id": letter, "title": title,
                               "who": [pkg["name"]], "text": text, "file": name})
            else:
                b = next(x for x in blocks if x["id"] == by_hash[h])
                if pkg["name"] not in b["who"]:
                    b["who"].append(pkg["name"])
            refs.append(by_hash[h])
        pkg["refs"] = sorted(set(refs))
    return blocks


def render(packages, blocks, source, extra=()):
    L = []
    a = L.append
    a("# 第三方组件与许可声明")
    a("")
    a("> 本文件由 `tools/gen_notices.py` 自动生成，**请勿手工修改**；"
      "改依赖后重新生成即可。")
    a(f"> 依据：`{source}`")
    a("")
    a("CSE Compressor 本体以 MIT 协议发布（见 [LICENSE](LICENSE)）。"
      "下面列出的是随安装包一起分发的第三方组件，以及运行时依赖但不随包分发的系统组件。")
    a("")
    a("## 一、随安装包分发的组件")
    a("")
    a("| 组件 | 版本 | 许可证 | 用途 | 许可原文 |")
    a("|---|---|---|---|---|")
    for p in packages:
        refs = "、".join(f"[{r}](#{_anchor(blocks, r)})" for r in p["refs"]) or "—"
        a(f"| {p['name']} | {p['version']} | {p['license']} | {p['use'] or '—'} | {refs} |")
    a("")
    a("此外还有几项不体现在 payload 的 `dist-info` 里、但确实进了 exe 的组件：")
    a("")
    a("| 组件 | 版本 | 许可证 | 用途 | 许可原文 |")
    a("|---|---|---|---|---|")
    for p in extra:
        refs = "、".join(f"[{r}](#{_anchor(blocks, r)})" for r in p.get("refs", [])) or "—"
        a(f"| {p['name']} | {p['version']} | {p['license']} | {p['use']} | {refs} |")
    a("")
    a("## 二、运行需要、但不随本包分发的组件")
    a("")
    a("| 组件 | 说明 |")
    a("|---|---|")
    a("| Microsoft Edge WebView2 运行时 | 界面渲染内核。属操作系统级组件（Win11 自带），"
      "**本安装包不包含也不安装它**；缺失时程序会提示到 Microsoft 官网获取。"
      "其使用受 Microsoft 的软件许可条款约束 |")
    a("| Microsoft .NET Framework 4.7.2+ | pythonnet 的 CLR 宿主，Windows 自带 |")
    a("| 7z.exe / UnRAR.exe（可选） | 仅在读 `.7z` / `.rar` 且未装 py7zr 时调用；"
      "由用户自行安装，本包不附带 |")
    a("")
    a("LGPL 组件（py7zr、pyppmd、inflate64、multivolumefile）" + LGPL_NOTE)
    a("")
    a("## 三、许可原文")
    a("")
    for b in blocks:
        a(f'<a id="{_anchor(blocks, b["id"])}"></a>')
        a("")
        a(f"### {b['id']}. {b['title']}")
        a("")
        a(f"适用于：{'、'.join(b['who'])}（原文文件 `{b['file']}`）")
        a("")
        a("```")
        a(b["text"].strip())
        a("```")
        a("")
    a("---")
    a("")
    a("重新生成：`python tools/gen_notices.py`（默认取 `dist/` 里最新的 Portable.zip）。")
    return "\n".join(L) + "\n"


def _anchor(blocks, letter):
    for b in blocks:
        if b["id"] == letter:
            slug = re.sub(r"[^a-z0-9]+", "-", b["title"].lower()).strip("-")
            return f"{letter.lower()}-{slug}"
    return letter.lower()


def find_latest_package():
    cands = sorted(glob.glob(os.path.join(ROOT, "dist", "*-Portable.zip")))
    if not cands:
        return None
    newest = max(cands, key=os.path.getmtime)
    tmp = os.path.join(ROOT, "build", "tmp", "notices_pkg")
    if os.path.isdir(tmp):
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    with zipfile.ZipFile(newest) as z:
        z.extractall(tmp)
    return tmp, os.path.basename(newest)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkg", help="解压后的包目录（含 _internal）")
    ap.add_argument("--out", default=os.path.join(ROOT, "THIRD-PARTY-NOTICES.md"))
    ap.add_argument("--label", default="", help="写进「依据」一行的说明，如 CSE-Compressor-0.1.0-beta.14 安装包")
    args = ap.parse_args(argv)

    if args.pkg:
        pkg_dir = args.pkg
        source = args.label or os.path.basename(args.pkg.rstrip("\\/"))
    else:
        found = find_latest_package()
        if not found:
            print("没找到 dist/*-Portable.zip，先用 --pkg 指一个解压后的包目录", file=sys.stderr)
            return 2
        pkg_dir, source = found
        if args.label:
            source = args.label

    packages = collect(pkg_dir)
    if not packages:
        print(f"{pkg_dir} 里没有 dist-info，确认这是解压后的发行包", file=sys.stderr)
        return 2

    # PyInstaller 与 CPython 的许可原文单独补两段（它们不在 payload 的 dist-info 里）
    extra = []
    pyi = _pyinstaller_license()
    if pyi:
        extra.append({"name": "PyInstaller", "version": pyi[0],
                      "license": "GPL-2.0 + Bootloader Exception",
                      "texts": [("COPYING.txt", pyi[1])], "use": "打包工具（引导代码进 exe）"})
    py_license = os.path.join(sys.prefix, "LICENSE.txt")
    if os.path.exists(py_license):
        extra.append({"name": "CPython", "version": sys.version.split()[0],
                      "license": "PSF-2.0", "texts": [("LICENSE.txt", _read(py_license))],
                      "use": "运行时"})

    for p in extra:
        p["refs"] = []
    blocks = license_blocks(packages)
    # 追加的组件也进原文区（用同一套去重）
    for p in extra:
        for name, text in p["texts"]:
            h = hashlib.sha256(text.encode("utf-8")).hexdigest()
            exist = [b for b in blocks if hashlib.sha256(b["text"].encode("utf-8")).hexdigest() == h]
            if exist:
                letter = exist[0]["id"]
                if p["name"] not in exist[0]["who"]:
                    exist[0]["who"].append(p["name"])
            else:
                letter = chr(ord("A") + len(blocks))
                blocks.append({"id": letter, "title": p["license"], "who": [p["name"]],
                               "text": text, "file": name})
            p["refs"].append(letter)
        p["refs"] = sorted(set(p["refs"]))

    md = render(packages, blocks, source, extra)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(md)

    print(f"写入 {os.path.relpath(args.out, ROOT)}")
    print(f"  组件 {len(packages)} 个（随包），另补 CPython/PyInstaller；许可原文 {len(blocks)} 份"
          f"（按内容去重）")
    print(f"  大小 {os.path.getsize(args.out) // 1024} KB")
    for p in packages:
        print(f"    {p['name']:<18} {p['version']:<9} {p['license']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
