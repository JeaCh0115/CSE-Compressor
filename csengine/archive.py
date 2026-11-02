""".cse 归档容器格式（参考 7-Zip 的 archive 封装）。

格式布局（v1，全部小端）：
  头部   : "CSE1"(4B) + version(u8) + default_method(u8) + reserved(u16) + entry_count(u32)
  条目   : name_len(u16) + name(utf-8) + method(u8) + flags(u8, bit0=is_dir)
           + orig_size(u64) + crc32(u32) + comp_size(u64) + data
"""

import os
import struct
from dataclasses import dataclass

from .compressors import (
    METHODS, METHOD_NAMES, compress_bytes, decompress_bytes, pick_best, crc32_of,
)

MAGIC = b"CSE1"
VERSION = 1


@dataclass
class Entry:
    name: str
    method: str
    is_dir: bool
    size: int
    comp_size: int
    crc: int


def _safe_join(dest: str, name: str) -> str:
    """防止路径穿越（zip-slip 攻击）。"""
    norm = name.replace("\\", "/")
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    if not parts:
        raise ValueError(f"非法归档内路径: {name!r}")
    if any(p == ".." for p in parts) or (len(norm) > 1 and norm[1] == ":"):
        raise ValueError(f"检测到不安全的归档路径: {name!r}")
    return os.path.join(dest, *parts)


class ArchiveWriter:
    def __init__(self, out_path: str, method: str = "auto"):
        if method not in METHODS and method != "auto":
            raise ValueError(f"未知压缩方法: {method}")
        self.out_path = out_path
        self.method = method
        self._entries = []  # (arcname, fs_path, is_dir)

    def add(self, fs_path: str, arcname: str = None) -> "ArchiveWriter":
        fs_path = os.path.abspath(fs_path)
        base = arcname or os.path.basename(fs_path.rstrip("\\/"))
        if os.path.isdir(fs_path):
            self._entries.append((base.replace(os.sep, "/") + "/", None, True))
            for root, dirs, files in os.walk(fs_path):
                dirs.sort()
                rel = os.path.relpath(root, fs_path).replace(os.sep, "/")
                prefix = base + "/" + rel + "/" if rel != "." else base + "/"
                for d in dirs:
                    self._entries.append((prefix + d + "/", None, True))
                for f in sorted(files):
                    self._entries.append((prefix + f, os.path.join(root, f), False))
        else:
            self._entries.append((base.replace(os.sep, "/"), fs_path, False))
        return self

    def write(self, progress=None) -> list:
        entries = []
        with open(self.out_path, "wb") as f:
            f.write(MAGIC)
            f.write(struct.pack("<BBHI", VERSION, METHODS.get(self.method, 0), 0, len(self._entries)))
            for idx, (arcname, fs_path, is_dir) in enumerate(self._entries):
                name_bytes = arcname.encode("utf-8")
                if len(name_bytes) > 0xFFFF:
                    raise ValueError(f"文件名过长: {arcname}")
                if is_dir:
                    f.write(struct.pack("<H", len(name_bytes)))
                    f.write(name_bytes)
                    f.write(struct.pack("<BBQIQ", 0, 1, 0, 0, 0))
                else:
                    with open(fs_path, "rb") as src:
                        raw = src.read()
                    if self.method == "auto":
                        mcode, blob = pick_best(raw)
                    else:
                        mcode = METHODS[self.method]
                        blob = compress_bytes(raw, mcode)
                    f.write(struct.pack("<H", len(name_bytes)))
                    f.write(name_bytes)
                    f.write(struct.pack("<BBQIQ", mcode, 0, len(raw), crc32_of(raw), len(blob)))
                    f.write(blob)
                if progress:
                    progress(idx + 1, len(self._entries), arcname)
        return self.out_path


def create_archive(inputs, out_path: str, method: str = "auto", progress=None) -> list:
    """把若干文件/目录打包为 .cse。返回条目摘要列表。"""
    writer = ArchiveWriter(out_path, method)
    for item in inputs:
        writer.add(item)
    entries = writer.write(progress=progress)
    # 重新读一次生成准确摘要
    return list_archive(out_path)


class ArchiveReader:
    def __init__(self, path: str):
        self.path = path
        self.default_method = 0
        self._raw = None

    def _load(self):
        if self._raw is None:
            with open(self.path, "rb") as f:
                self._raw = f.read()
            if self._raw[:4] != MAGIC:
                raise ValueError("不是有效的 .cse 文件（魔数不符）")
            version, dmethod, _res, count = struct.unpack("<BBHI", self._raw[4:12])
            if version != VERSION:
                raise ValueError(f"不支持的 .cse 版本: {version}")
            self.default_method = dmethod
            self.count = count
        return self._raw

    def entries(self) -> list:
        raw = self._load()
        pos = 12
        result = []
        for _ in range(self.count):
            (name_len,) = struct.unpack_from("<H", raw, pos)
            pos += 2
            name = raw[pos:pos + name_len].decode("utf-8")
            pos += name_len
            mcode, flags, orig, crc, comp = struct.unpack_from("<BBQIQ", raw, pos)
            pos += 22
            pos += comp
            result.append(Entry(
                name=name,
                method="dir" if flags & 1 else METHOD_NAMES[mcode],
                is_dir=bool(flags & 1),
                size=orig,
                comp_size=comp,
                crc=crc,
            ))
        return result

    def extract(self, dest: str, progress=None) -> list:
        raw = self._load()
        os.makedirs(dest, exist_ok=True)
        pos = 12
        written = []
        for idx in range(self.count):
            (name_len,) = struct.unpack_from("<H", raw, pos)
            pos += 2
            name = raw[pos:pos + name_len].decode("utf-8")
            pos += name_len
            mcode, flags, orig, crc, comp = struct.unpack_from("<BBQIQ", raw, pos)
            pos += 22
            blob = raw[pos:pos + comp]
            pos += comp
            target = _safe_join(dest, name.rstrip("/"))
            if flags & 1:
                os.makedirs(target, exist_ok=True)
            else:
                data = decompress_bytes(blob, mcode, orig)
                if crc32_of(data) != crc:
                    raise ValueError(f"CRC 校验失败: {name}")
                os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
                with open(target, "wb") as f:
                    f.write(data)
                written.append(target)
            if progress:
                progress(idx + 1, self.count, name)
        return written


def list_archive(path: str) -> list:
    return ArchiveReader(path).entries()


def extract_archive(path: str, dest: str, progress=None) -> list:
    return ArchiveReader(path).extract(dest, progress=progress)


def test_archive(path: str, progress=None) -> bool:
    """完整性测试：解压所有条目并校验 CRC。"""
    reader = ArchiveReader(path)
    reader._load()
    total = reader.count
    done = [0]

    import tempfile

    def cb(i, n, name):
        done[0] = i
        if progress:
            progress(i, n, name)

    with tempfile.TemporaryDirectory() as tmp:
        reader.extract(tmp, progress=cb)
    return True
