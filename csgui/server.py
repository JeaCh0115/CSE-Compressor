"""CSE Compressor — 本地 Web GUI 服务（零第三方依赖）。

启动后浏览器访问 http://127.0.0.1:<port>，前端是 csgui/web 下的单页应用。
所有归档操作都通过 JSON API 调用本地引擎，文件不出本机。
"""

import json
import mimetypes
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote, quote

from csengine import __version__, CHANNEL_LABEL, converters, exttools, formats, version as version_mod
from csengine.compressors import METHODS
from . import settings

# 打包（PyInstaller one-folder）时，前端资源位于 _MEIPASS/web
if getattr(sys, "frozen", False):
    WEB_ROOT = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "web")
else:
    WEB_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
STAGING = os.path.join(os.path.expanduser("~"), ".cse_upload")

IS_WINDOWS = os.name == "nt"
SHELL_MODE = os.environ.get("CSE_SHELL") == "1"


def _desktop_dir() -> str:
    """起始目录：桌面优先，其次主目录（一次算好，省掉前端往返）。"""
    home = os.path.expanduser("~")
    for name in ("Desktop", "桌面"):
        p = os.path.join(home, name)
        if os.path.isdir(p):
            return p
    return home

# ---------------- 资源管理器 / 命令行带入 ----------------
# 右键「压缩 / 解压」或双击关联文件时，前端通过轮询 /api/launch 领取请求执行
LAUNCH_ACTIONS = ("view", "add", "extract")
_launch_queue: list = []
_launch_lock = threading.Lock()


def set_launch_file(path: str):
    """兼容旧接口：双击关联文件 → view 请求。"""
    set_launch_request({"action": "view", "paths": [path]})


def set_launch_request(req: dict):
    """收一笔带入请求。

    **合并语义**：多选文件右键压缩时，资源管理器对每个文件各调起一次进程，
    这些请求会几乎同时到达。若每条都单独立项，前端就会反复重开压缩对话框、
    最后只剩一个文件。所以连续到达的同类 add 请求并入上一条（同一批）。
    """
    if not isinstance(req, dict) or req.get("action") not in LAUNCH_ACTIONS:
        return
    paths = [p for p in (req.get("paths") or []) if isinstance(p, str) and p]
    if not paths:
        return
    action = req["action"]
    with _launch_lock:
        if _launch_queue and _launch_queue[-1]["action"] == action == "add":
            batch = _launch_queue[-1]
            seen = set(batch["paths"])
            for p in paths:
                if p not in seen and len(batch["paths"]) < 512:
                    batch["paths"].append(p)
                    seen.add(p)
            merged = True
        else:
            merged = False
            _launch_queue.append({"action": action, "paths": paths[:512]})
            del _launch_queue[:-64]
    if action == "view":
        # 双击归档：窗口还在创建时就并行解析条目，前端拿到 launch 请求后可直接命中缓存
        for p in paths[:4]:
            preload_preview(p)
    return merged


# ---------------- 归档列表预热缓存 ----------------
# 目的：双击压缩包时，页面首次请求 /api/preview 不必再等一次全表解析
_preview_cache: dict = {}
_preview_lock = threading.Lock()
PREVIEW_CACHE_MAX = 8


def _preview_payload(path: str, password=None) -> dict:
    from csengine import converters
    info = formats.describe_path(path)
    if not info["is_dir"]:
        try:
            info["entries"] = converters.list_items(path, password)
            info["summary"] = converters.summary_of(path, password)
        except Exception:
            info["entries"] = None
    return info


def preload_preview(path: str, password=None) -> bool:
    """后台线程预热某个归档的条目列表（失败静默，不影响主流程）。"""
    if not path or not os.path.isfile(path):
        return False
    try:
        st = os.stat(path)
    except OSError:
        return False
    key = (st.st_mtime_ns, st.st_size)

    def work():
        try:
            payload = _preview_payload(path, password)
        except Exception:
            return
        with _preview_lock:
            _preview_cache[path] = (key, payload)
            while len(_preview_cache) > PREVIEW_CACHE_MAX:
                _preview_cache.pop(next(iter(_preview_cache)))

    threading.Thread(target=work, name="cse-preload", daemon=True).start()
    return True


def take_launches() -> list:
    with _launch_lock:
        out = _launch_queue.copy()
        _launch_queue.clear()
    return out


def pending_launch_actions() -> list:
    """当前待领取请求的动作列表（只读，不清空）。

    桌面外壳在**建窗前**用它判断这次是不是"右键压缩"——是的话直接把窗口建成
    小窗、页面带 ?compact=1，用户第一眼看到的就是压缩对话框，而不是主界面。
    """
    with _launch_lock:
        return [r["action"] for r in _launch_queue]


class Job:
    def __init__(self, action: str):
        self.id = uuid.uuid4().hex[:12]
        self.action = action
        self.status = "running"      # running | done | error
        self.done = 0
        self.total = 0
        self.message = "准备中…"
        self.result = None
        self.error = None
        self.logs = []
        self.started = time.time()
        self.lock = threading.Lock()

    def progress(self, done, total, name=""):
        with self.lock:
            self.done, self.total = done, total
            self.message = name
            if name:
                self.logs.append(f"[{done}/{total}] {name}")
                if len(self.logs) > 400:
                    del self.logs[:100]

    def to_dict(self):
        with self.lock:
            return {
                "id": self.id, "action": self.action, "status": self.status,
                "done": self.done, "total": self.total, "message": self.message,
                "result": self.result, "error": self.error,
                "logs": self.logs[-60:],
                "elapsed": round(time.time() - self.started, 2),
            }


class Registry:
    def __init__(self):
        self.jobs = {}
        self.lock = threading.Lock()

    def new(self, action):
        job = Job(action)
        with self.lock:
            self.jobs[job.id] = job
            if len(self.jobs) > 50:  # 简单清理
                oldest = sorted(self.jobs.values(), key=lambda j: j.started)[:20]
                for j in oldest:
                    self.jobs.pop(j.id, None)
        return job

    def get(self, jid):
        with self.lock:
            return self.jobs.get(jid)


JOBS = Registry()


def _about_payload() -> dict:
    """关于对话框数据：版本事实 + 运行环境 + 开源清单。"""
    data = version_mod.about_data()
    data["shell"] = SHELL_MODE
    data["backend"] = ("原生窗口 · Microsoft Edge WebView2" if SHELL_MODE
                       else "浏览器模式（本机服务）")
    data["engine_backends"] = ["store", "lzhuf（自研 LZ77+Huffman）", "zlib/DEFLATE", "lzma"]
    data["staging"] = STAGING
    staging_size = 0
    try:
        for root, _dirs, files in os.walk(STAGING):
            for f in files:
                try:
                    staging_size += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    data["staging_size"] = staging_size
    return data


# ---------------- 任务执行 ----------------
def _path_state(path: str) -> dict:
    """路径现状：是否存在 / 是否目录 / 目录里有没有东西。

    界面用它做「覆盖前先提醒」：目标已经存在时必须让用户明确选一次，
    否则一次右键压缩就能把同名旧归档（可能是唯一的一份备份）直接替换掉。
    """
    p = os.path.abspath(os.path.expanduser(path or ""))
    st = {"path": p, "name": os.path.basename(p.rstrip("\\/")), "exists": False,
          "is_dir": False, "nonempty": False, "count": 0, "size": 0, "mtime": 0.0}
    try:
        info = os.stat(p)
    except OSError:
        return st
    st["exists"] = True
    st["is_dir"] = os.path.isdir(p)
    st["size"] = 0 if st["is_dir"] else info.st_size
    st["mtime"] = info.st_mtime
    if st["is_dir"]:
        try:
            names = os.listdir(p)
        except OSError:
            names = []
        st["count"] = len(names)
        st["nonempty"] = bool(names)
    return st


def _run_compress(job: Job, payload: dict):
    sources = [p for p in payload.get("sources", []) if p and os.path.exists(p)]
    if not sources:
        raise ValueError("没有可压缩的源文件")
    dest = payload.get("dest") or os.path.join(
        os.path.dirname(sources[0].rstrip("\\/")) or os.getcwd(),
        (os.path.basename(sources[0].rstrip("\\/")) or "archive") + ".cse")
    fmt = payload.get("format") or converters.guess_format(dest)
    if fmt == "cse" and not dest.lower().endswith(".cse"):
        dest += ".cse"
    info = converters.create(
        dest, sources,
        method=payload.get("method", "auto"),
        password=payload.get("password") or None,
        solid=bool(payload.get("solid", True)),
        comment=payload.get("comment", ""),
        progress=job.progress,
        fmt=fmt,
        overwrite=bool(payload.get("overwrite", True)),
    )
    job.total = job.total or 1
    job.done = job.total
    entries = converters.list_items(dest, payload.get("password") or None)
    total_orig = sum(e.get("size") or 0 for e in entries if not e.get("is_dir"))
    job.result = {
        "dest": dest,
        "format": fmt,
        "entries": len(entries),
        "orig_size": total_orig,
        "comp_size": os.path.getsize(dest),
        "ratio": round(os.path.getsize(dest) / total_orig * 100, 1) if total_orig else None,
        "info": info,
    }


def _run_extract(job: Job, payload: dict):
    arc = payload.get("archive")
    if not arc or not os.path.exists(arc):
        raise ValueError("归档不存在")
    dest = payload.get("dest") or os.path.splitext(arc)[0] + "_解压"
    files = converters.extract(arc, dest, password=payload.get("password") or None,
                               progress=job.progress)
    job.result = {"dest": dest, "files": len(files),
                  "sample": [os.path.relpath(f, dest) for f in files[:20]]}


def _run_list(job: Job, payload: dict):
    arc = payload.get("archive")
    if not arc or not os.path.exists(arc):
        raise ValueError("归档不存在")
    items = converters.list_items(arc, password=payload.get("password") or None)
    summary = converters.summary_of(arc, password=payload.get("password") or None)
    job.total = job.done = len(items)
    job.result = {
        "archive": arc,
        "file_size": os.path.getsize(arc),
        "summary": summary,
        "format_label": formats.detect_file(arc),
        "entries": items,
    }


def _run_test(job: Job, payload: dict):
    arc = payload.get("archive")
    if not arc or not os.path.exists(arc):
        raise ValueError("归档不存在")
    converters.test(arc, password=payload.get("password") or None)
    job.result = {"ok": True, "archive": arc}


ACTIONS = {"compress": _run_compress, "extract": _run_extract,
           "list": _run_list, "test": _run_test}


def _worker(job: Job, payload: dict):
    fn = ACTIONS[job.action]
    try:
        fn(job, payload)
        job.status = "done"
        job.message = "完成"
    except Exception as exc:  # 回传给前端
        job.status = "error"
        job.error = str(exc) or type(exc).__name__
        job.logs.append("✖ " + traceback.format_exc(limit=2))


# ---------------- 文件操作（复制 / 移动 / 移入回收站） ----------------
def _recycle(paths):
    """移入回收站（永不永久删除），返回 (成功数, 失败列表)。"""
    import ctypes
    import ctypes.wintypes as wt

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wt.HWND), ("wFunc", wt.UINT), ("pFrom", wt.LPCWSTR),
            ("pTo", wt.LPCWSTR), ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wt.BOOL), ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wt.LPCWSTR),
        ]

    FO_DELETE = 3
    FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO = 0x0004, 0x0010, 0x0040
    FOF_NOERRORUI = 0x0400

    done, failed = 0, []
    for p in paths:
        op = SHFILEOPSTRUCTW()
        op.hwnd = None
        op.wFunc = FO_DELETE
        op.pFrom = p + "\0\0"
        op.pTo = None
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if rc == 0 and not op.fAnyOperationsAborted:
            done += 1
        else:
            failed.append(p)
    return done, failed


def _fs_ops(payload: dict) -> dict:
    import shutil

    op = payload.get("op")
    raw = [p for p in (payload.get("paths") or []) if isinstance(p, str) and p.strip()]
    dest = (payload.get("dest") or "").strip()
    if not raw:
        raise ValueError("没有选择任何项目")
    paths = [os.path.abspath(p) for p in raw]
    for p in paths:
        if not os.path.exists(p):
            raise ValueError(f"路径不存在：{p}")
        # 拒绝针对盘符根目录的危险操作
        if os.path.dirname(p) == p:
            raise ValueError("不允许对磁盘根目录执行该操作")

    if op in ("copy", "move"):
        if not dest or not os.path.isdir(dest):
            raise ValueError("目标文件夹不存在")
        done, failed = 0, []
        for p in paths:
            try:
                target = os.path.join(dest, os.path.basename(p.rstrip("\\/")))
                if os.path.abspath(target) == os.path.abspath(p):
                    continue
                if os.path.exists(target):
                    failed.append(f"{p}（目标已存在同名项）")
                    continue
                if os.path.isdir(p):
                    shutil.copytree(p, target) if op == "copy" else shutil.move(p, target)
                else:
                    shutil.copy2(p, target) if op == "copy" else shutil.move(p, target)
                done += 1
            except Exception as exc:  # 单项失败不影响其余
                failed.append(f"{p}（{exc}）")
        return {"ok": not failed, "op": op, "count": done, "dest": dest, "failed": failed}

    if op == "delete":
        if os.name != "nt":
            raise ValueError("移入回收站仅支持 Windows")
        done, failed = _recycle(paths)
        return {"ok": not failed, "op": "delete", "count": done, "failed": failed}

    raise ValueError(f"未知操作：{op}")


# ---------------- HTTP 层 ----------------
class Handler(BaseHTTPRequestHandler):
    server_version = f"CSEServer/{__version__}"

    def log_message(self, fmt, *args):  # 静音
        pass

    # -------- 工具 --------
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, msg, code=400):
        self._json({"error": msg}, code)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _static(self, rel):
        path = os.path.normpath(os.path.join(WEB_ROOT, rel.lstrip("/")))
        if not path.startswith(WEB_ROOT) or not os.path.isfile(path):
            self._err("资源不存在", 404)
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8"
                         if ctype.startswith("text/") or ctype.endswith("javascript") else ctype)
        self.send_header("Content-Length", str(len(data)))
        # 本地静态资源一律不缓存：WebView2 的磁盘缓存会跨版本留着旧 CSS/JS，
        # 升级后用户看到的是上一版的界面（实测踩过：改完样式重跑程序毫无变化）。
        # 文件只有几十 KB，每次重读的代价可以忽略。
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # -------- GET --------
    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        route = parsed.path

        if route in ("/", "/index.html"):
            self._static("index.html")
        elif route in ("/favicon.png", "/favicon.ico"):
            self._static("favicon.png")
        elif route.startswith("/static/"):
            self._static(route[len("/static/"):])
        elif route == "/api/env":
            self._json({
                "version": __version__,
                "channel": version_mod.CHANNEL,
                "channel_label": CHANNEL_LABEL,
                "build_time": version_mod.BUILD_TIME,
                "formats": [{"id": k, "label": v} for k, v in converters.FORMAT_LABELS.items()],
                "read_formats": converters.read_formats(),
                "backend": exttools.capabilities(),
                "methods": ["auto"] + list(METHODS),
                "home": os.path.expanduser("~"),
                "desktop": _desktop_dir(),
                "cwd": os.getcwd(),
                "staging": STAGING,
                "shell": SHELL_MODE,
                "is_windows": IS_WINDOWS,
                "settings": settings.load(),
                "launch_file": None,  # 已由 /api/launch 轮询机制取代
            })
        elif route == "/api/about":
            self._json(_about_payload())
        elif route == "/api/settings":
            self._json(settings.load())
        elif route == "/api/browse":
            self._browse(qs.get("path", [os.path.expanduser("~")])[0])
        elif route == "/api/job":
            job = JOBS.get(qs.get("id", [""])[0])
            if not job:
                self._err("任务不存在", 404)
            else:
                self._json(job.to_dict())
        elif route == "/api/preview":
            self._preview(qs.get("path", [""])[0], (qs.get("password", [""])[0] or None))
        elif route == "/api/detect":
            self._detect(qs.get("path", [""])[0])
        elif route == "/api/pathinfo":
            self._json(_path_state(qs.get("path", [""])[0]))
        elif route == "/api/freename":
            p = qs.get("path", [""])[0]
            # tag 允许为空（目录改名用纯数字后缀 a_解压 → a_解压2）。
            # parse_qs 默认丢掉空值，那样 "tag=" 会被当成没传、退回 "_副本"。
            tag = parse_qs(parsed.query, keep_blank_values=True).get("tag", ["_副本"])[0]
            try:
                self._json({"path": p, "free": converters.unique_path(p, tag)})
            except ValueError as exc:
                self._err(str(exc), 400)
        elif route == "/api/launch":
            self._json({"requests": take_launches()})
        elif route == "/api/shellmenu_status":
            if not IS_WINDOWS:
                self._json({"supported": False, "registered": False})
                return
            from csengine import winassoc
            self._json({"supported": True,
                        "registered": winassoc.is_context_menu_registered()})
        elif route == "/api/archive_icons_status":
            if not IS_WINDOWS:
                self._json({"supported": False, "registered": False})
                return
            from csengine import winassoc
            self._json({"supported": True,
                        "registered": winassoc.is_archive_icons_registered()})
        elif route == "/api/associate_status":
            if not IS_WINDOWS:
                self._json({"supported": False, "registered": False})
                return
            from csengine import winassoc
            self._json({"supported": True, "registered": winassoc.is_registered()})
        else:
            self._err("未知接口", 404)

    def _browse(self, path):
        path = os.path.abspath(os.path.expanduser(path or "~"))
        if not os.path.isdir(path):
            parent = os.path.dirname(path)
            path = parent if os.path.isdir(parent) else os.path.expanduser("~")
        items, dirs, files = [], [], []
        try:
            for name in sorted(os.listdir(path), key=lambda s: s.lower()):
                if name.startswith(".") and name not in (".", ".."):
                    continue
                full = os.path.join(path, name)
                try:
                    st = os.stat(full)
                    is_dir = os.path.isdir(full)
                    ent = {"name": name, "path": full, "is_dir": is_dir,
                           "size": None if is_dir else st.st_size,
                           "mtime": st.st_mtime, "ctime": st.st_ctime}
                except OSError:
                    continue
                (dirs if is_dir else files).append(ent)
        except PermissionError:
            self._err("无权限访问该目录", 403)
            return
        items = dirs + files
        parent = os.path.dirname(path)
        self._json({"path": path, "parent": parent if parent != path else None,
                    "items": items, "dirs": dirs, "files": files,
                    "total": len(items),
                    "drive_root": os.path.splitdrive(path)[0] + os.sep})

    def _preview(self, path, password):
        if not path or not os.path.exists(path):
            self._err("文件不存在", 404)
            return
        if not password:
            try:
                st = os.stat(path)
                key = (st.st_mtime_ns, st.st_size)
            except OSError:
                key = None
            with _preview_lock:
                hit = _preview_cache.get(path)
            if hit and key and hit[0] == key:
                self._json(hit[1])       # 预热命中：双击打开时直接出列表
                return
        try:
            self._json(_preview_payload(path, password))
        except Exception as exc:
            self._err(str(exc), 500)

    def _detect(self, path):
        if not path or not os.path.exists(path):
            self._err("文件不存在", 404)
            return
        try:
            self._json(formats.describe_path(path))
        except Exception as exc:
            self._err(str(exc), 500)

    # -------- POST --------
    def do_POST(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        route = parsed.path
        try:
            if route == "/api/upload":
                self._upload(qs)
            elif route == "/api/settings":
                patch = json.loads(self._body().decode("utf-8") or "{}")
                self._json(settings.save(patch))
            elif route == "/api/settings/reset":
                self._json(settings.reset())
            elif route == "/api/open_path":
                path = (json.loads(self._body().decode("utf-8") or "{}").get("path") or "").strip()
                if not path or not os.path.exists(path):
                    self._err("路径不存在", 404)
                    return
                if os.name == "nt":
                    os.startfile(path)  # noqa: S606 - 本机资源管理器
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", path])
                else:
                    subprocess.Popen(["xdg-open", path])
                self._json({"ok": True})
            elif route == "/api/open_url":
                url = (json.loads(self._body().decode("utf-8") or "{}").get("url") or "").strip()
                if not url.startswith(("http://", "https://")):
                    self._err("仅允许 http/https 链接")
                    return
                webbrowser.open(url)
                self._json({"ok": True})
            elif route == "/api/clear_staging":
                import shutil
                removed = 0
                if os.path.isdir(STAGING):
                    for name in os.listdir(STAGING):
                        p = os.path.join(STAGING, name)
                        try:
                            shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
                            removed += 1
                        except OSError:
                            pass
                self._json({"ok": True, "removed": removed})
            elif route == "/api/cleanup_staging":
                # 压缩完就把这批「拖入副本」删掉，别让缓存目录无声长大。
                # 只认 token 这一层（字母数字），杜绝 ../ 之类的路径穿越。
                import shutil
                token = (json.loads(self._body().decode("utf-8") or "{}").get("token") or "")
                if not token or not token.isalnum():
                    self._json({"ok": True, "removed": 0})
                    return
                target = os.path.join(STAGING, token)
                if os.path.isdir(target):
                    shutil.rmtree(target, ignore_errors=True)
                    self._json({"ok": True, "removed": 1})
                else:
                    self._json({"ok": True, "removed": 0})
            elif route == "/api/default_apps":
                # Windows 10/11 会保护「默认应用」，某些类型（如 .rar/.gz）只认
                # 用户亲手确认过一次的 UserChoice。这里直接把用户送到系统页面，
                # 并尽量定位到 CSE Compressor 自己（Win11 支持 registeredAppUser，
                # 不认就退回默认应用首页，用户仍需自己找一下，但不会报错）。
                if os.name == "nt":
                    opened = False
                    for uri in ("ms-settings:defaultapps?registeredAppUser="
                                + quote("CSE Compressor"),
                                "ms-settings:defaultapps"):
                        try:
                            os.startfile(uri)  # noqa: S606
                            opened = True
                            break
                        except OSError:
                            continue
                    if not opened:
                        self._err("无法打开系统设置，请手动打开「设置 → 应用 → 默认应用」", 500)
                        return
                self._json({"ok": True})
            elif route == "/api/associate":
                if not IS_WINDOWS:
                    self._err("文件关联仅支持 Windows", 400)
                    return
                from csengine import winassoc
                try:
                    self._json(winassoc.register())
                except RuntimeError as exc:
                    self._err(str(exc), 400)
            elif route == "/api/associate_status":
                from csengine import winassoc
                if not IS_WINDOWS:
                    self._json({"supported": False, "registered": False})
                    return
                self._json({"supported": True, "registered": winassoc.is_registered()})
            elif route == "/api/associate/refresh":
                if IS_WINDOWS:
                    from csengine import winassoc
                    winassoc.refresh_icons(hard=True)
                self._json({"ok": True})
            elif route == "/api/launch":
                body = json.loads(self._body().decode("utf-8") or "{}")
                set_launch_request(body)
                self._json({"ok": True})
            elif route == "/api/shellmenu":
                if not IS_WINDOWS:
                    self._err("右键菜单仅支持 Windows", 400)
                    return
                op = (json.loads(self._body().decode("utf-8") or "{}").get("op") or "")
                from csengine import winassoc
                if op == "register":
                    try:
                        self._json(winassoc.register_context_menu())
                    except RuntimeError as exc:
                        self._err(str(exc), 400)
                elif op == "remove":
                    winassoc.unregister_context_menu()
                    self._json({"ok": True})
                else:
                    self._err(f"未知操作: {op}")
            elif route == "/api/archive_icons":
                if not IS_WINDOWS:
                    self._err("图标注册仅支持 Windows", 400)
                    return
                op = (json.loads(self._body().decode("utf-8") or "{}").get("op") or "")
                from csengine import winassoc
                try:
                    if op == "register":
                        self._json(winassoc.register_archive_icons())
                    elif op == "remove":
                        winassoc.unregister_archive_icons()
                        self._json({"ok": True})
                    else:
                        self._err(f"未知操作: {op}")
                except RuntimeError as exc:
                    self._err(str(exc), 400)
            elif route == "/api/extract_conflicts":
                # 解压前先数一数会覆盖掉几个已有文件，界面据此弹确认框。
                # 加密归档没给密码时列不出来，返回 ok=False 让前端退回粗提示。
                payload = json.loads(self._body().decode("utf-8") or "{}")
                arc = (payload.get("archive") or "").strip()
                dest = (payload.get("dest") or "").strip()
                if not arc or not os.path.exists(arc) or not dest:
                    self._err("归档或目标路径无效")
                    return
                if not os.path.isdir(dest):
                    self._json({"ok": True, "total": 0, "conflicts": 0, "samples": []})
                    return
                try:
                    self._json({"ok": True, **converters.conflicts(
                        arc, dest, payload.get("password") or None)})
                except Exception as exc:
                    self._json({"ok": False, "error": str(exc) or type(exc).__name__,
                                "total": 0, "conflicts": 0, "samples": []})
            elif route == "/api/fs":
                payload = json.loads(self._body().decode("utf-8") or "{}")
                self._json(_fs_ops(payload))
            elif route == "/api/job":
                payload = json.loads(self._body().decode("utf-8") or "{}")
                action = payload.get("action")
                if action not in ACTIONS:
                    self._err(f"未知动作: {action}")
                    return
                job = JOBS.new(action)
                threading.Thread(target=_worker, args=(job, payload), daemon=True).start()
                self._json({"id": job.id})
            else:
                self._err("未知接口", 404)
        except json.JSONDecodeError:
            self._err("请求体不是合法 JSON")
        except Exception as exc:
            self._err(f"{type(exc).__name__}: {exc}", 500)

    def _upload(self, qs):
        name = unquote(qs.get("name", ["upload.bin"])[0])
        rel = unquote(qs.get("rel", [""])[0]) or name
        token = qs.get("token", ["default"])[0]
        rel = rel.replace("\\", "/").lstrip("/")
        if ".." in rel.split("/"):
            self._err("非法文件名")
            return
        target = os.path.join(STAGING, token, *rel.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        data = self._body()
        with open(target, "wb") as f:
            f.write(data)
        self._json({"path": target, "size": len(data), "name": rel,
                    "base": os.path.join(STAGING, token)})


class GuiServer:
    def __init__(self, host="127.0.0.1", port=8770):
        self.host = host
        self.port = port
        self.httpd = None
        self.thread = None

    def start(self, port_scan=20):
        last_err = None
        for offset in range(port_scan):
            try:
                self.httpd = ThreadingHTTPServer((self.host, self.port + offset), Handler)
                self.httpd.daemon_threads = True
                self.port = self.port + offset
                break
            except OSError as exc:
                last_err = exc
        if not self.httpd:
            raise RuntimeError(f"端口启动失败: {last_err}")
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self.port

    def url(self):
        return f"http://{self.host}:{self.port}/"

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


def _port_free(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def main(open_browser=True, port=8770):
    srv = GuiServer(port=port)
    actual = srv.start()
    url = srv.url()
    print(f"CSE Compressor GUI 已启动: {url}   (Ctrl+C 退出)")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n正在关闭…")
        srv.stop()


if __name__ == "__main__":
    main()
