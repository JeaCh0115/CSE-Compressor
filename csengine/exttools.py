"""外部 / 可选解压后端：7-Zip 命令行、UnRAR、py7zr。

为什么需要：Windows 自带的 `tar.exe`（bsdtar 3.3）构建时**未启用 LZMA 与 RAR 解码**，
遇到 .7z 会报 "LZMA codec is unsupported"、遇到 .rar 直接 "Unrecognized archive format"，
不能作为通用兜底。因此这里按优先级探测：

1. **py7zr**（纯 Python，随发行包一起分发）→ 7z，无需任何外部程序
2. **7z.exe / 7za.exe / 7zz.exe**（PATH 与常见安装目录）→ 7z / rar / iso / cab / arj / lzh / z…
3. **UnRAR.exe / WinRAR.exe**（WinRAR 自带命令行）→ rar

环境变量 CSE_7Z / CSE_UNRAR 可显式指定可执行文件路径。所有探测结果做进程内缓存。
"""

import os
import re
import shutil
import subprocess

# 7z 能读、但本引擎没有原生实现的格式（都走外部后端）
EXTERNAL_FORMATS = ("7z", "rar", "iso", "cab", "arj", "lzh", "z")

_EXE_NAMES = {
    "7z": ("7z.exe", "7za.exe", "7zz.exe"),
    "unrar": ("UnRAR.exe", "unrar.exe", "WinRAR.exe"),
}

_SEARCH_DIRS = (
    r"C:\Program Files\7-Zip",
    r"C:\Program Files (x86)\7-Zip",
    r"C:\Program Files\WinRAR",
    r"C:\Program Files (x86)\WinRAR",
    r"C:\Program Files\NanaZip",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\7-Zip"),
    os.path.expandvars(r"%ProgramW6432%\7-Zip"),
)

_cache = {}
_CREATE_NO_WINDOW = 0x08000000
_PCT = re.compile(r"^\s*(\d{1,3})%")


def _env(name):
    p = os.environ.get(name, "").strip()
    return p if p and os.path.isfile(p) else None


def _find(kind):
    if kind in _cache:
        return _cache[kind]
    found = _env("CSE_7Z" if kind == "7z" else "CSE_UNRAR")
    if not found:
        for name in _EXE_NAMES[kind]:
            found = shutil.which(name)
            if found:
                break
    if not found:
        for d in _SEARCH_DIRS:
            for name in _EXE_NAMES[kind]:
                p = os.path.join(d, name)
                if os.path.isfile(p):
                    found = p
                    break
            if found:
                break
    _cache[kind] = found
    return found


def find_7z():
    """7z.exe 路径（找不到返回 None）。"""
    return _find("7z")


def find_unrar():
    """UnRAR.exe / WinRAR.exe 路径（找不到返回 None）。"""
    return _find("unrar")


def find_py7zr():
    """py7zr 模块是否可用（不导入即失败时返回 False）。"""
    if "py7zr" in _cache:
        return _cache["py7zr"]
    try:
        import py7zr  # noqa: F401
        ok = True
    except Exception:
        ok = False
    _cache["py7zr"] = ok
    return ok


def backend_for(fmt: str):
    """返回该格式可用的后端名：py7zr / 7z / unrar / None。"""
    if fmt == "7z":
        if find_py7zr():
            return "py7zr"
        return "7z" if find_7z() else None
    if fmt in EXTERNAL_FORMATS:
        if find_7z():
            return "7z"
        if fmt == "rar" and find_unrar():
            return "unrar"
    return None


def capabilities() -> dict:
    """供界面展示的后端能力摘要。"""
    return {
        "7zip": find_7z() or "",
        "unrar": find_unrar() or "",
        "py7zr": find_py7zr(),
        "formats": [f for f in EXTERNAL_FORMATS if backend_for(f)],
    }


# ---------------- 7-Zip 命令行 ----------------
def _run(cmd, timeout=1800):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          stdin=subprocess.DEVNULL, timeout=timeout,
                          creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def _setup_7z():
    """7-Zip 控制台输出的编码在本机是 GBK，-sccUTF-8 强制 UTF-8。"""
    return ["-sccUTF-8", "-bd"]


def list_via_7z(path: str, password: str = None) -> list:
    exe = find_7z()
    if not exe:
        raise ValueError("解压该格式需要 7-Zip（未检测到 7z.exe）")
    cmd = [exe, "l", "-slt", "-ba"] + _setup_7z()
    if password:
        cmd.append("-p" + password)
    cmd += ["--", path]
    code, out = _run(cmd, timeout=300)
    if code > 1:
        raise ValueError(_friendly(out, "读取归档失败"))
    entries, cur = [], {}
    for line in out.splitlines():
        line = line.rstrip("\r")
        if not line.strip():
            if cur.get("Path"):
                entries.append(_entry_from_slt(cur))
            cur = {}
            continue
        if " = " in line:
            k, v = line.split(" = ", 1)
            cur[k.strip()] = v
        elif line.endswith(" ="):
            cur[line[:-2].strip()] = ""
    if cur.get("Path"):
        entries.append(_entry_from_slt(cur))
    return entries


def _entry_from_slt(d: dict) -> dict:
    attrs = (d.get("Attributes") or "").strip()
    is_dir = attrs[:1].upper() == "D" or (d.get("Folder") or "").strip() == "+"
    size = _int(d.get("Size"))
    comp = _int(d.get("Packed Size"))
    name = (d.get("Path") or "").replace("\\", "/")
    return {"name": name, "is_dir": is_dir, "size": size,
            "comp_size": comp, "method": (d.get("Method") or "").strip() or None}


def _int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def extract_via_7z(path: str, dest: str, password: str = None, progress=None) -> list:
    exe = find_7z()
    if not exe:
        raise ValueError("解压该格式需要 7-Zip（未检测到 7z.exe）")
    os.makedirs(dest, exist_ok=True)
    cmd = [exe, "x", "-y", "-bsp1", "-bb0"] + _setup_7z() + [f"-o{dest}"]
    if password:
        cmd.append("-p" + password)
    cmd += ["--", path]
    written = []
    _stream(cmd, progress)
    for root, _dirs, files in os.walk(dest):
        for f in files:
            written.append(os.path.join(root, f))
    return written


def _stream(cmd, progress=None):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL,
                            creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0)
    tail = []
    last = -1
    try:
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            tail.append(line)
            del tail[:-20]
            m = _PCT.match(line)
            if m and progress:
                pct = min(100, int(m.group(1)))
                if pct != last:
                    last = pct
                    name = line.split("-", 1)[-1].strip() if "-" in line else ""
                    progress(pct, 100, name or "解压中…")
    finally:
        proc.stdout.close()
        proc.wait()
    if proc.returncode > 1:
        raise ValueError(_friendly("\n".join(tail), "解压失败"))
    if progress:
        progress(100, 100, "完成")


# ---------------- UnRAR / WinRAR ----------------
def list_via_unrar(path: str, password: str = None) -> list:
    exe = find_unrar()
    if not exe:
        raise ValueError("解压 RAR 需要 WinRAR 或 7-Zip（均未检测到）")
    cmd = [exe, "lb", "-p-" if not password else "-p" + password, "--", path]
    code, out = _run(cmd, timeout=300)
    if code > 1:
        raise ValueError(_friendly(out, "读取归档失败"))
    items = []
    for line in out.splitlines():
        name = line.strip().replace("\\", "/")
        if not name:
            continue
        items.append({"name": name, "is_dir": name.endswith("/"),
                      "size": None, "comp_size": None, "method": "rar"})
    return items


def extract_via_unrar(path: str, dest: str, password: str = None, progress=None) -> list:
    exe = find_unrar()
    if not exe:
        raise ValueError("解压 RAR 需要 WinRAR 或 7-Zip（均未检测到）")
    os.makedirs(dest, exist_ok=True)
    cmd = [exe, "x", "-o+", "-y", "-p-" if not password else "-p" + password,
           "--", path, dest + os.sep]
    _stream(cmd, progress)
    written = []
    for root, _dirs, files in os.walk(dest):
        for f in files:
            written.append(os.path.join(root, f))
    return written


# ---------------- py7zr（纯 Python） ----------------
def list_via_py7zr(path: str, password: str = None) -> list:
    import py7zr

    with py7zr.SevenZipFile(path, "r", password=password) as zf:
        out = []
        for i in zf.list():
            out.append({
                "name": (i.filename or "").replace("\\", "/"),
                "is_dir": bool(i.is_directory),
                "size": i.uncompressed,
                "comp_size": i.compressed,
                "method": "lzma",
            })
        return out


def extract_via_py7zr(path: str, dest: str, password: str = None, progress=None) -> list:
    import py7zr

    os.makedirs(dest, exist_ok=True)
    with py7zr.SevenZipFile(path, "r", password=password) as zf:
        names = zf.getnames()
        total = max(1, len(names))
        if progress:
            progress(0, total, "正在解压…")
        zf.extractall(path=dest)
    if progress:
        progress(total, total, "完成")
    return [os.path.join(dest, n.replace("/", os.sep)) for n in names]


# ---------------- 统一入口 ----------------
def list_entries(fmt: str, path: str, password: str = None) -> list:
    backend = backend_for(fmt)
    if backend == "py7zr":
        return list_via_py7zr(path, password)
    if backend == "7z":
        return list_via_7z(path, password)
    if backend == "unrar":
        return list_via_unrar(path, password)
    raise ValueError(_missing_hint(fmt))


def extract(fmt: str, path: str, dest: str, password: str = None, progress=None) -> list:
    backend = backend_for(fmt)
    if backend == "py7zr":
        return extract_via_py7zr(path, dest, password, progress)
    if backend == "7z":
        return extract_via_7z(path, dest, password, progress)
    if backend == "unrar":
        return extract_via_unrar(path, dest, password, progress)
    raise ValueError(_missing_hint(fmt))


def _missing_hint(fmt: str) -> str:
    name = {"7z": "7z", "rar": "RAR", "iso": "ISO", "cab": "CAB",
            "arj": "ARJ", "lzh": "LZH", "z": "Z"}.get(fmt, fmt.upper())
    if fmt == "rar":
        return ("解压 RAR 需要外部解压程序：请安装 7-Zip 或 WinRAR 后重试"
                "（本引擎未内置 RAR 解码器，RAR 解压算法受专利/许可限制）")
    return f"解压 {name} 需要安装 7-Zip（未检测到 7z.exe）"


def _friendly(out: str, fallback: str) -> str:
    """从外部工具输出里挑一行最能说明问题的信息。"""
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    for ln in lines:
        low = ln.lower()
        if "error" in low or "wrong password" in low or "cannot" in low or "denied" in low:
            if "password" in low:
                return "密码不正确或归档已加密"
            return ln[:200]
    return lines[-1][:200] if lines else fallback
