"""多格式互操作层：统一入口，按扩展名 / 魔数分发到对应编解码器。

创建  .cse(CSE-X 独家) / .zip / .tar / .tar.gz|.tgz / .tar.bz2|.tbz2 / .tar.xz|.txz / .gz / .bz2 / .xz
读取  以上全部 + .7z / .rar / .iso / .cab / .arj / .lzh / .z（7z 走 py7zr 或 7z.exe，rar 走 7z/UnRAR）
加密  仅 CSE-X（ChaCha20 + HMAC）；zip/7z 的加密由外部后端处理，本引擎给出明确提示
"""

import bz2
import gzip
import lzma
import os
import shutil
import tarfile
import zipfile

from . import archive as legacy
from . import exclusive
from . import exttools
from .compressors import METHODS

# 扩展名 -> 内部格式标识
_EXT_MAP = {
    ".cse": "cse",
    ".zip": "zip", ".jar": "zip", ".apk": "zip", ".war": "zip", ".xpi": "zip",
    ".tar": "tar",
    ".tar.gz": "targz", ".tgz": "targz",
    ".tar.bz2": "tarbz2", ".tbz2": "tarbz2", ".tbz": "tarbz2",
    ".tar.xz": "tarxz", ".txz": "tarxz",
    ".gz": "gz", ".bz2": "bz2", ".xz": "xz",
    ".7z": "7z",
    ".rar": "rar",
    ".iso": "iso",
    ".cab": "cab",
    ".arj": "arj",
    ".lzh": "lzh", ".lha": "lzh",
    ".z": "z",
}

# 可创建（压缩）的格式
FORMAT_LABELS = {
    "cse": "CSE-X（独家 · 固实/去重/加密）",
    "zip": "ZIP（通用 · Windows 原生）",
    "targz": "TAR.GZ（Linux 通用）",
    "tarbz2": "TAR.BZ2",
    "tarxz": "TAR.XZ",
    "tar": "TAR（不压缩）",
    "gz": "GZIP（单文件）",
    "bz2": "BZIP2（单文件）",
    "xz": "XZ（单文件）",
}

# 仅解压（读取）的格式显示名
READ_LABELS = {
    "zip": "ZIP", "7z": "7-Zip", "rar": "RAR", "tar": "TAR", "targz": "TAR.GZ",
    "tarbz2": "TAR.BZ2", "tarxz": "TAR.XZ", "gz": "GZIP", "bz2": "BZIP2",
    "xz": "XZ", "iso": "ISO 光盘映像", "cab": "CAB 安装包",
    "arj": "ARJ", "lzh": "LZH", "z": "Z（Unix compress）", "cse": "CSE-X",
}

TAR_MODES = {"tar": "w", "targz": "w:gz", "tarbz2": "w:bz2", "tarxz": "w:xz"}


def guess_format(path: str, default: str = "cse") -> str:
    lower = path.lower()
    for ext in sorted(_EXT_MAP, key=len, reverse=True):
        if lower.endswith(ext):
            return _EXT_MAP[ext]
    return default


def archive_format(path: str):
    """该路径是否为可读取的归档；是则返回格式标识，否则 None。

    外部格式（7z / rar / iso …）在缺少后端时返回 None —— 界面上会按
    「不是可识别的归档」处理，而不是给出一个必然失败的操作入口。
    """
    lower = path.lower()
    for ext in sorted(_EXT_MAP, key=len, reverse=True):
        if lower.endswith(ext):
            fmt = _EXT_MAP[ext]
            if fmt in exttools.EXTERNAL_FORMATS and exttools.backend_for(fmt) is None:
                return None
            return fmt
    return None


_NATIVE = ("cse", "zip", "tar", "targz", "tarbz2", "tarxz", "gz", "bz2", "xz")


def read_formats() -> list:
    """当前环境实际可解压的格式（供界面展示 / 右键菜单注册）。"""
    out = [f for f in _NATIVE]
    out += [f for f in exttools.EXTERNAL_FORMATS if exttools.backend_for(f)]
    return out



def _zip_compression(method: str) -> int:
    return {"store": zipfile.ZIP_STORED}.get(method, zipfile.ZIP_DEFLATED)


def _walk_inputs(inputs, exclude=None):
    """(归档内路径, 本地路径, 是否目录) 三元组序列。

    exclude 是输出归档自身的路径（已 normcase）：把归档写进某个正在被压缩的
    目录里时，边写边遍历会把它自己也扫进去（塞进半截自己的副本），必须跳过。
    """
    for item in inputs:
        item = os.path.abspath(item)
        base = os.path.basename(item.rstrip("\\/"))
        if os.path.isdir(item):
            yield base + "/", item, True
            for root, dirs, files in os.walk(item):
                dirs.sort()
                rel = os.path.relpath(root, item).replace(os.sep, "/")
                prefix = base + "/" + rel + "/" if rel != "." else base + "/"
                for d in dirs:
                    yield prefix + d + "/", os.path.join(root, d), True
                for f in sorted(files):
                    p = os.path.join(root, f)
                    if exclude and os.path.normcase(os.path.abspath(p)) == exclude:
                        continue
                    yield prefix + f, p, False
        else:
            yield base, item, False


def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def split_ext(path: str):
    """拆出 (主干, 扩展名)。.tar.gz / .tar.bz2 / .tar.xz 这类多段扩展名整体算扩展名。"""
    low = path.lower()
    for ext in sorted(_EXT_MAP, key=len, reverse=True):
        if low.endswith(ext) and len(path) > len(ext):
            return path[:-len(ext)], path[-len(ext):]
    return os.path.splitext(path)


def unique_path(path: str, tag: str = "_副本") -> str:
    """path 已存在时给出第一个不存在的候选名，用来避开覆盖。

    tag 非空（文件用）：a.zip → a_压缩.zip → a_压缩2.zip
    tag 为空（目录用）：a_解压 → a_解压2 → a_解压3
    """
    if not os.path.exists(path):
        return path
    stem, ext = split_ext(path)
    start = 1 if tag else 2
    for i in range(start, start + 999):
        cand = stem + tag + (str(i) if i > 1 else "") + ext
        if not os.path.exists(cand):
            return cand
    raise ValueError(f"找不到可用的文件名：{path}")


def create(out_path: str, inputs, method: str = "auto", password: str = None,
           solid: bool = True, comment: str = "", progress=None, fmt: str = None,
           dedup: bool = True, overwrite: bool = True) -> dict:
    """创建归档，返回统计字典。fmt 缺省按输出文件扩展名推断。

    overwrite=False 时只要目标已存在就直接报错——界面用它做最后一道保险：
    用户没在确认框里点「覆盖」，这一层就不许动磁盘上已有的文件。
    """
    fmt = fmt or guess_format(out_path)
    if method not in METHODS and method != "auto":
        raise ValueError(f"未知压缩方法: {method}")
    if not overwrite and os.path.exists(out_path):
        raise ValueError(
            f"输出文件已存在，已取消覆盖：{os.path.basename(out_path)}")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)

    # 输出归档绝不能与源文件同名：zipfile / tarfile / gzip 都是先截断目标再回读源，
    # 同名时源文件当场被清空（只读文件则直接 [Errno 13] Permission denied）。
    # 这是唯一的"数据当场消失"路径，必须在写之前挡住。
    for item in inputs:
        if _same_file(item, out_path):
            raise ValueError(
                f"输出归档与源文件同名（{os.path.basename(out_path)}），"
                "继续会覆盖源文件，请换一个文件名")
    out_self = os.path.normcase(os.path.abspath(out_path))

    if fmt == "cse":
        return exclusive.create_archive(inputs, out_path, method=method, password=password,
                                        solid=solid, comment=comment, progress=progress,
                                        dedup=dedup, exclude=out_self)
    if password:
        raise ValueError(f"{fmt.upper()} 格式不支持本引擎内置加密，请选择 .cse（CSE-X 独家格式）")

    if fmt in TAR_MODES:
        count = 0
        with tarfile.open(out_path, TAR_MODES[fmt]) as tf:
            for arcname, fs_path, is_dir in _walk_inputs(inputs, exclude=out_self):
                tf.add(fs_path, arcname=arcname.rstrip("/") if not is_dir else arcname,
                       recursive=False)
                count += 1
                if progress:
                    progress(count, count, arcname)
        return {"format": fmt, "entries": count, "comp_size": os.path.getsize(out_path)}

    if fmt == "zip":
        count = 0
        with zipfile.ZipFile(out_path, "w", _zip_compression(method)) as zf:
            for arcname, fs_path, is_dir in _walk_inputs(inputs, exclude=out_self):
                if is_dir:
                    zf.writestr(zipfile.ZipInfo(arcname), b"")
                else:
                    zf.write(fs_path, arcname)
                count += 1
                if progress:
                    progress(count, count, arcname)
        return {"format": fmt, "entries": count, "comp_size": os.path.getsize(out_path)}

    if fmt in ("gz", "bz2", "xz"):
        files = [p for p in inputs if os.path.isfile(p)]
        if len(files) != 1:
            raise ValueError(f"{fmt.upper()} 只支持单个文件（当前 {len(files)} 个）")
        src_name = os.path.basename(files[0])
        if fmt == "gz":
            # GZIP 头内嵌原始文件名，解压时可还原
            with open(files[0], "rb") as src, open(out_path, "wb") as raw:
                with gzip.GzipFile(filename=src_name, mode="wb", fileobj=raw,
                                   compresslevel=6) as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
        else:
            opener = bz2.open if fmt == "bz2" else lzma.open
            kwargs = {"compresslevel": 9} if fmt == "bz2" else {"preset": 6}
            with open(files[0], "rb") as src, opener(out_path, "wb", **kwargs) as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
        if progress:
            progress(1, 1, os.path.basename(files[0]))
        return {"format": fmt, "entries": 1, "comp_size": os.path.getsize(out_path)}

    if fmt == "7z":
        try:
            import py7zr  # noqa: F401
        except ImportError as exc:
            raise ValueError("创建 7z 需要安装 py7zr（pip install py7zr）；"
                             "或改用 .cse / .zip") from exc
        import py7zr
        with py7zr.SevenZipFile(out_path, "w") as zf:
            for p in inputs:
                zf.write(p, os.path.basename(p.rstrip("\\/")))
        return {"format": fmt, "entries": len(inputs), "comp_size": os.path.getsize(out_path)}

    raise ValueError(f"不支持的输出格式: {fmt}")


def _strip_single_ext(path: str) -> str:
    for ext in (".gz", ".bz2", ".xz"):
        if path.lower().endswith(ext):
            return os.path.basename(path)[:-len(ext)]
    return os.path.basename(path) + ".out"


def _single_target_name(path: str) -> str:
    """单文件格式解压后的目标文件名：优先用容器内记录的原名。"""
    fallback = _strip_single_ext(path)
    if not path.lower().endswith(".gz"):
        return fallback
    try:
        with open(path, "rb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="rb") as gz:
                inner = os.path.basename(gz.name or "")
        if inner and inner != os.path.basename(path) and inner != os.path.basename(path)[:-3]:
            return inner.replace("\\", "_").replace("/", "_")
    except (OSError, EOFError):
        pass
    return fallback


def extract(path: str, dest: str, password: str = None, progress=None) -> list:
    with open(path, "rb") as f:
        magic = f.read(4)
    os.makedirs(dest, exist_ok=True)

    if magic == exclusive.MAGIC:
        return exclusive.extract_archive(path, dest, password=password, progress=progress)
    if magic == legacy.MAGIC:
        return legacy.extract_archive(path, dest, progress=progress)

    fmt = archive_format(path) or guess_format(path, default="")
    if fmt in exttools.EXTERNAL_FORMATS:
        return exttools.extract(fmt, path, dest, password=password, progress=progress)

    if fmt == "zip":
        written = []
        with zipfile.ZipFile(path) as zf:
            items = zf.infolist()
            total = len(items)
            for i, info in enumerate(items, 1):
                legacy._safe_join(dest, info.filename)  # 路径穿越校验
                zf.extract(info, dest)
                if not info.is_dir():
                    written.append(os.path.join(dest, info.filename))
                if progress:
                    progress(i, total, info.filename)
        return written

    if fmt in TAR_MODES:
        written = []
        mode = "r:" + TAR_MODES[fmt][2:] if fmt != "tar" else "r:"
        with tarfile.open(path, mode) as tf:
            members = tf.getmembers()
            total = len(members)
            for i, m in enumerate(members, 1):
                legacy._safe_join(dest, m.name)
                tf.extract(m, dest, filter="data")
                if m.isfile():
                    written.append(os.path.join(dest, m.name))
                if progress:
                    progress(i, total, m.name)
        return written

    if fmt in ("gz", "bz2", "xz"):
        opener = {"gz": gzip.open, "bz2": bz2.open, "xz": lzma.open}[fmt]
        target = os.path.join(dest, _single_target_name(path))
        with opener(path, "rb") as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out, 1024 * 1024)
        if progress:
            progress(1, 1, os.path.basename(target))
        return [target]

    if fmt == "7z":
        return exttools.extract("7z", path, dest, password=password, progress=progress)

    raise ValueError(f"无法识别的归档格式: {os.path.basename(path)}")


def list_items(path: str, password: str = None) -> list:
    """统一列出条目：{name, is_dir, size, comp_size, method}"""
    with open(path, "rb") as f:
        magic = f.read(4)
    if magic == exclusive.MAGIC:
        return exclusive.list_entries(path, password)
    if magic == legacy.MAGIC:
        return [{"name": e.name, "is_dir": e.is_dir, "size": e.size,
                 "comp_size": e.comp_size, "method": e.method}
                for e in legacy.list_archive(path)]

    fmt = archive_format(path) or guess_format(path, default="")
    if fmt in exttools.EXTERNAL_FORMATS:
        return exttools.list_entries(fmt, path, password)

    if fmt == "zip":
        with zipfile.ZipFile(path) as zf:
            return [{"name": i.filename.replace("\\", "/"), "is_dir": i.is_dir(),
                     "size": i.file_size, "comp_size": i.compress_size,
                     "method": {0: "store", 8: "deflate", 12: "bzip2", 14: "lzma"}.get(
                         i.compress_type, str(i.compress_type))}
                    for i in zf.infolist()]
    if fmt in TAR_MODES:
        mode = "r:" + TAR_MODES[fmt][2:] if fmt != "tar" else "r:"
        with tarfile.open(path, mode) as tf:
            return [{"name": m.name.replace("\\", "/"), "is_dir": m.isdir(), "size": m.size,
                     "comp_size": None, "method": "tar"} for m in tf.getmembers()]
    if fmt in ("gz", "bz2", "xz"):
        return [{"name": _single_target_name(path), "is_dir": False, "size": None,
                 "comp_size": os.path.getsize(path), "method": fmt}]
    if fmt == "7z":
        return exttools.list_entries("7z", path, password)
    raise ValueError(f"无法识别的归档格式: {os.path.basename(path)}")


def conflicts(path: str, dest: str, password: str = None) -> dict:
    """统计解压会覆盖掉多少已存在的文件（界面用来弹确认框）。

    只数文件：目录撞名只是合并，不会丢内容。加密归档没给密码时列表会抛错，
    由调用方决定降级成「目标目录非空」的粗提示。
    """
    items = list_items(path, password)
    names = [str(i.get("name") or "") for i in items
             if not i.get("is_dir") and not str(i.get("name") or "").endswith("/")]
    hit = []
    for n in names:
        rel = n.replace("\\", "/").strip("/")
        if not rel or rel.startswith("../") or "/../" in rel:
            continue
        try:
            if os.path.exists(os.path.join(dest, rel)):
                hit.append(rel)
        except OSError:
            continue
    return {"total": len(names), "conflicts": len(hit), "samples": hit[:8]}


def summary_of(path: str, password: str = None) -> dict:
    with open(path, "rb") as f:
        magic = f.read(4)
    if magic == exclusive.MAGIC:
        return exclusive.summary_of(path, password)
    if magic == legacy.MAGIC:
        reader = legacy.ArchiveReader(path)
        entries = reader.entries()
        return {"format": "CSE v1", "entries": len(entries),
                "size": sum(e.size for e in entries),
                "comp_size": os.path.getsize(path)}
    return {"format": READ_LABELS.get(archive_format(path) or "", "未知"),
            "comp_size": os.path.getsize(path)}



def test(path: str, password: str = None, dest: str = None) -> bool:
    """完整性测试：解到临时目录并逐条校验（CSE-X 每块校验 CRC）。"""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="cse_verify_") as tmp:
        extract(path, dest or tmp, password=password)
    return True
