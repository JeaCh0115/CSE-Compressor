"""发行件冒烟：便携版 exe 里到底有没有 beta.12 的覆盖保护。

源码跑通了不代表打出来的 exe 是新的（前端资源漏进 datas、服务端模块没重新编译
都出现过）。这里直接解压刚打好的 Portable.zip，启动里面的 exe，然后敲它自己的
HTTP 接口验证三个新端点在发行件里都在，并检查前端 JS 里确实带了确认层。

用法：python tools/release_overwrite_smoke.py [Portable.zip]
"""

import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = []


def rec(ok, name, detail=""):
    RESULTS.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def _find_zip():
    cands = sorted(glob.glob(os.path.join(ROOT, "dist", "CSE-Compressor-*-Portable.zip")))
    cands = [c for c in cands if "beta.12" in os.path.basename(c)]
    return cands[-1] if cands else (cands[-1] if cands else None)


def _port_alive(port):
    with socket.socket() as s:
        s.settimeout(0.25)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return json.loads(r.read().decode() or "{}")


def _post(port, path, payload):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                data=json.dumps(payload).encode(),
                                headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode() or "{}")


def main(argv):
    zpath = argv[0] if argv else _find_zip()
    if not zpath or not os.path.exists(zpath):
        print("找不到 Portable.zip，先跑 tools/build_win.py")
        return 1
    print(f"发行件: {os.path.basename(zpath)}")

    tmp = tempfile.mkdtemp(prefix="cse_smoke_")
    try:
        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmp)
        exes = glob.glob(os.path.join(tmp, "**", "CSE*ompressor.exe"), recursive=True)
        if not exes:
            rec(False, "便携版里有 CSE-Compressor.exe", tmp)
            return 1
        rec(True, "便携版里有 CSE-Compressor.exe", exes[0])
        base = os.path.dirname(exes[0])

        # 前端资源是明文放进 _internal 的，直接查关键字
        # 打包时 csgui/web → _internal/web（server.py 的 _static 也按 web/ 找）
        cands = glob.glob(os.path.join(base, "**", "web", "app.js"), recursive=True)
        text = ""
        if cands:
            with open(cands[0], encoding="utf-8") as f:
                text = f.read()
        for need in ("function askOverwrite", 'id="cOver"', "extract_conflicts"):
            rec(need in text, f"发行件前端含 {need}")

        work = os.path.join(tmp, "data")
        os.makedirs(work, exist_ok=True)
        src = os.path.join(work, "报告.txt")
        with open(src, "w", encoding="utf-8") as f:
            f.write("hello smoke\n")
        old = os.path.join(work, "报告.zip")
        with zipfile.ZipFile(old, "w") as z:
            z.writestr("a.txt", "old")
        arc = os.path.join(work, "pack.zip")
        with zipfile.ZipFile(arc, "w") as z:
            z.writestr("a.txt", "NEW")

        subprocess.run(["taskkill", "/IM", "CSECompressor.exe", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc = subprocess.Popen([exes[0], "--add", src], cwd=base)

        port = 0
        for _ in range(120):
            for p in range(8770, 8791):
                if _port_alive(p):
                    try:
                        _get(p, "/api/env")
                        port = p
                        break
                    except Exception:
                        continue
            if port:
                break
            time.sleep(0.25)
        rec(bool(port), "发行件服务已就绪", f"端口 {port}")
        if not port:
            return 1

        info = _get(port, "/api/pathinfo?path=" + urllib.parse.quote(old))
        rec(info.get("exists") is True, "/api/pathinfo 在发行件里可用",
            f"exists={info.get('exists')} size={info.get('size')}")
        free = _get(port, "/api/freename?path=" + urllib.parse.quote(old) + "&tag="
                    + urllib.parse.quote("_压缩"))
        rec(os.path.basename(free.get("free", "")) == "报告_压缩.zip",
            "/api/freename 给出不撞名的候选", free.get("free", ""))
        dirlike = os.path.join(work, "pack_解压")
        os.makedirs(dirlike, exist_ok=True)
        with open(os.path.join(dirlike, "a.txt"), "w", encoding="utf-8") as f:
            f.write("OLD")
        cf = _post(port, "/api/extract_conflicts", {"archive": arc, "dest": dirlike})
        rec(cf.get("ok") and cf.get("conflicts") == 1, "/api/extract_conflicts 数出冲突",
            f"{cf.get('conflicts')} / {cf.get('samples')}")
    finally:
        subprocess.run(["taskkill", "/IM", "CSECompressor.exe", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.6)
        shutil.rmtree(tmp, ignore_errors=True)

    ok = sum(1 for r in RESULTS if r[0])
    print(f"\n{ok}/{len(RESULTS)} 通过")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
