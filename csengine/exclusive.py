""".CSE-X（CSX2）—— CSE 独家压缩格式。

相对普通 zip/tar 的独家能力：
  1. 固实分块（solid）：小文件拼成大块统一压缩，跨文件冗余一起吃掉
  2. 内容去重（dedup）：相同内容只落一份（≤8MB 文件按 SHA-256 识别）
  3. 加密：PBKDF2-HMAC-SHA256 派生密钥 + ChaCha20 流加密 + HMAC 认证
  4. 元数据：保留修改时间 / 权限 / 备注，解压后自动还原
  5. 每块独立压缩方法（auto 逐块选优）+ CRC32 完整性校验

文件布局（小端）：
  header   : "CSX2" + ver(u8) + flags(u8) + method(u8) + reserved(u8)
             + block_size(u32) + salt(16B) + kdf_iters(u32)
             + entry_count(u32) + blob_count(u32)
             + manifest_comp(u64) + manifest_orig(u64)      共 56 字节
  manifest : LZMA 压缩（可选加密）的 JSON，描述条目与数据块
  blobs    : 依次排列的数据块，每块 = 压缩（可选加密）后的字节
"""

import hashlib
import json
import lzma
import os
import struct
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

from . import compressors as C
from . import crypto
from .archive import _safe_join

MAGIC = b"CSX2"
LEGACY_MAGIC = b"CSE1"
VERSION = 2
HEADER = struct.Struct("<4sBBBB I16sI II QQ")
HEADER_SIZE = HEADER.size  # 56

FLAG_ENCRYPTED = 1
FLAG_SOLID = 2
FLAG_DEDUP = 4

SOLID_FILE_MAX = 64 * 1024
DEDUP_MAX = 8 * 1024 * 1024
DEFAULT_BLOCK = 2 * 1024 * 1024

# 分块并行压缩：zlib / lzma 都是 C 实现，压大缓冲时会释放 GIL，所以线程池
# 能拿到真实并行；同时不会像纯 Python 那样长时间霸占 GIL（那会让窗口拖动卡死）。
# 块数上限压到 6，兼顾低配机内存与界面响应。
_BLOCK_WORKERS = max(2, min(6, (os.cpu_count() or 4)))
# 同时在飞的块数：超过就先把最早的一块落盘，限制内存峰值（约 2MB × 该值）
_BLOCK_INFLIGHT = _BLOCK_WORKERS + 2


def _yield_cpu():
    """让出一次 GIL，给界面消息循环喘息（压缩过程中拖窗口才不会一顿一顿）。"""
    time.sleep(0)


def is_exclusive(path: str) -> bool:
    with open(path, "rb") as f:
        return f.read(4) == MAGIC


class ExclusiveWriter:
    def __init__(self, out_path: str, method: str = "auto", solid: bool = True,
                 password: str = None, block_size: int = DEFAULT_BLOCK,
                 comment: str = "", dedup: bool = True, kdf_iters: int = crypto.DEFAULT_ITERS,
                 progress=None, exclude: str = None):
        self.out_path = out_path
        # 归档写进被压缩的目录里时，遍历要跳过它自己（否则会把自己塞进去）
        self._exclude = os.path.normcase(os.path.abspath(exclude)) if exclude else None
        self.method = method
        self.solid = solid
        self.dedup = dedup
        self.password = password
        self.block_size = max(block_size, 64 * 1024)
        self.comment = comment
        self.kdf_iters = kdf_iters
        self.progress = progress
        self._entries = []  # {name, type, size, mtime, mode, fs}

    # ---------- 收集条目 ----------
    def add(self, fs_path: str, arcname: str = None) -> "ExclusiveWriter":
        fs_path = os.path.abspath(fs_path)
        base = arcname or os.path.basename(fs_path.rstrip("\\/"))
        if os.path.isdir(fs_path):
            st = os.stat(fs_path)
            self._entries.append({"name": base + "/", "type": "d",
                                  "size": 0, "mtime": st.st_mtime, "mode": st.st_mode, "fs": None})
            for root, dirs, files in os.walk(fs_path):
                dirs.sort()
                rel = os.path.relpath(root, fs_path).replace(os.sep, "/")
                prefix = base + "/" + rel + "/" if rel != "." else base + "/"
                for d in dirs:
                    p = os.path.join(root, d)
                    st = os.stat(p)
                    self._entries.append({"name": prefix + d + "/", "type": "d", "size": 0,
                                          "mtime": st.st_mtime, "mode": st.st_mode, "fs": None})
                for f in sorted(files):
                    p = os.path.join(root, f)
                    if self._exclude and os.path.normcase(os.path.abspath(p)) == self._exclude:
                        continue
                    st = os.stat(p)
                    self._entries.append({"name": prefix + f, "type": "f", "size": st.st_size,
                                          "mtime": st.st_mtime, "mode": st.st_mode, "fs": p})
        else:
            st = os.stat(fs_path)
            self._entries.append({"name": base, "type": "f", "size": st.st_size,
                                  "mtime": st.st_mtime, "mode": st.st_mode, "fs": fs_path})
        return self

    # ---------- 写归档 ----------
    def write(self) -> dict:
        enc_key = mac_key = None
        salt = os.urandom(16)
        if self.password:
            enc_key, mac_key = crypto.derive_keys(self.password, salt, self.kdf_iters)

        blobs = []          # 每块: {"size","comp","method","crc"}（按块序号排列）
        placed = {}         # sha256+len -> (blob_id, offset, length)
        group_refs = {}     # 当前固实组内 sha256+len -> (offset, length)
        group_pending = []  # [(entry, blob_id_placeholder_offset)]  待组落盘后回填
        solid_buf = bytearray()
        spinner = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)
        pending = []        # [(blob_id, future)] 按提交顺序排队，落盘也按这个顺序
        pool = ThreadPoolExecutor(max_workers=_BLOCK_WORKERS,
                                  thread_name_prefix="cse-blk")

        def _compress_unit(raw: bytes):
            if self.method == "auto":
                code, blob = C.pick_best(raw, candidates=_auto_candidates(raw))
            else:
                code = C._norm(self.method)
                blob = C.compress_bytes(raw, code)
            if enc_key:
                blob = crypto.encrypt_blob(blob, enc_key, mac_key)
            return code, blob

        def drain(keep: int = 0):
            """把已提交的块按**提交顺序**取回并写入临时文件。

            并行压缩完成的先后是不可控的，但归档里块的排列必须与 manifest
            的 blob 顺序一致（解压靠累加 comp 算偏移），所以这里严格按队列出口。
            """
            while len(pending) > keep:
                bid, fut = pending.pop(0)
                code, blob = fut.result()
                spinner.write(blob)
                item = blobs[bid]
                item["comp"] = len(blob)
                item["method"] = C.METHOD_NAMES[code]
                _yield_cpu()

        def compress_blob(raw: bytes) -> int:
            bid = len(blobs)
            blobs.append({"size": len(raw), "comp": 0, "method": "store",
                          "crc": C.crc32_of(raw)})
            pending.append((bid, pool.submit(_compress_unit, raw)))
            drain(_BLOCK_INFLIGHT)
            return bid

        def flush_solid():
            nonlocal solid_buf, group_pending
            if not solid_buf and not group_pending:
                return
            bid = compress_blob(bytes(solid_buf))
            for entry, off in group_pending:
                entry["blob"] = bid
                entry["offset"] = off
                key = entry["_hash"]
                placed[key] = (bid, off, entry["size"])
            solid_buf = bytearray()
            group_pending = []
            group_refs.clear()

        def _auto_candidates(raw):
            if len(raw) < 16 * 1024:
                return ("store", "zlib")
            return C.AUTO_CANDIDATES

        files = [e for e in self._entries if e["type"] == "f"]
        done = 0
        try:
            for entry in self._entries:
                if entry["type"] == "d":
                    continue
                with open(entry["fs"], "rb") as f:
                    raw = f.read()
                entry["size"] = len(raw)
                key = (hashlib.sha256(raw).hexdigest(), len(raw))
                entry["_hash"] = key
                done += 1
                if self.progress:
                    self.progress(done, len(files), entry["name"])
                if not done % 16:
                    _yield_cpu()

                if self.dedup:
                    if key in placed:
                        bid, off, _ = placed[key]
                        entry["blob"], entry["offset"] = bid, off
                        continue
                    if key in group_refs:
                        off, _ = group_refs[key]
                        group_pending.append((entry, off))
                        continue

                if self.solid and len(raw) <= SOLID_FILE_MAX and len(raw) > 0:
                    if solid_buf and len(solid_buf) + len(raw) > self.block_size:
                        flush_solid()
                    off = len(solid_buf)
                    solid_buf += raw
                    group_refs[key] = (off, len(raw))
                    group_pending.append((entry, off))
                else:
                    flush_solid()
                    bid = compress_blob(raw)
                    placed[key] = (bid, 0, len(raw))
                    entry["blob"], entry["offset"] = bid, 0

            flush_solid()
            drain(0)
        finally:
            pool.shutdown(wait=True)

        manifest = {
            "format": "CSE-X",
            "version": VERSION,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "method": self.method,
            "solid": self.solid,
            "dedup": self.dedup,
            "encrypted": bool(self.password),
            "comment": self.comment,
            "entries": [{
                "name": e["name"],
                "type": e["type"],
                "size": e["size"],
                "mtime": round(e["mtime"], 3),
                "mode": e["mode"],
                **({"blob": e["blob"], "offset": e["offset"]} if e["type"] == "f" else {}),
            } for e in self._entries],
            "blobs": blobs,
        }
        raw_manifest = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        man_comp = lzma.compress(raw_manifest, preset=6)
        if enc_key:
            man_comp = crypto.encrypt_blob(man_comp, enc_key, mac_key)

        flags = ((FLAG_ENCRYPTED if self.password else 0)
                 | (FLAG_SOLID if self.solid else 0)
                 | (FLAG_DEDUP if self.dedup else 0))
        method_code = 0 if self.method == "auto" else C._norm(self.method)

        with open(self.out_path, "wb") as out:
            out.write(HEADER.pack(
                MAGIC, VERSION, flags, method_code, 0, self.block_size,
                salt if self.password else b"\0" * 16,
                self.kdf_iters if self.password else 0,
                len(self._entries), len(blobs), len(man_comp), len(raw_manifest),
            ))
            out.write(man_comp)
            spinner.seek(0)
            while True:
                chunk = spinner.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        spinner.close()

        return {
            "entries": len(self._entries),
            "files": len(files),
            "blobs": len(blobs),
            "orig_size": sum(b["size"] for b in blobs),
            "comp_size": os.path.getsize(self.out_path),
            "encrypted": bool(self.password),
            "solid": self.solid,
        }


class ExclusiveReader:
    def __init__(self, path: str, password: str = None):
        self.path = path
        self.password = password
        self._fh = None
        self._manifest = None
        with open(path, "rb") as f:
            head = f.read(HEADER_SIZE)
        if head[:4] != MAGIC:
            raise ValueError("不是 CSE-X（CSX2）归档")
        (magic, version, flags, method_code, _res, block_size, salt, iters,
         entry_count, blob_count, man_comp, man_orig) = HEADER.unpack(head)
        if version != VERSION:
            raise ValueError(f"不支持的 CSE-X 版本: {version}")
        self.flags = flags
        self.encrypted = bool(flags & FLAG_ENCRYPTED)
        self.solid = bool(flags & FLAG_SOLID)
        self.block_size = block_size
        self.method_code = method_code
        self.entry_count = entry_count
        self.blob_count = blob_count
        self._man_comp = man_comp
        self._man_orig = man_orig
        self._salt = salt
        self._iters = iters
        self.data_offset = HEADER_SIZE + man_comp

    # ---------- 解密密钥 ----------
    def _keys(self):
        if not self.encrypted:
            return None, None
        if not self.password:
            raise ValueError("该归档已加密，需要提供密码")
        return crypto.derive_keys(self.password, self._salt, self._iters)

    def manifest(self) -> dict:
        if self._manifest is None:
            with open(self.path, "rb") as f:
                f.seek(HEADER_SIZE)
                blob = f.read(self._man_comp)
            if len(blob) != self._man_comp:
                raise ValueError("归档被截断（manifest 不完整）")
            if self.encrypted:
                enc_key, mac_key = self._keys()
                blob = crypto.decrypt_blob(blob, enc_key, mac_key)
            raw = lzma.decompress(blob)
            if len(raw) != self._man_orig:
                raise ValueError("manifest 大小校验失败")
            self._manifest = json.loads(raw.decode("utf-8"))
        return self._manifest

    def entries(self) -> list:
        man = self.manifest()
        result = []
        for e in man["entries"]:
            is_dir = e["type"] == "d"
            if is_dir:
                result.append({"name": e["name"], "is_dir": True, "size": 0,
                               "comp_size": 0, "method": "dir"})
                continue
            blobs = man["blobs"]
            b = blobs[e["blob"]]
            # 独立块可直接给出压缩后大小；共享块按比例估算仅用于展示
            if b["size"] == e["size"] and e.get("offset", 0) == 0:
                comp = b["comp"]
            else:
                comp = int(b["comp"] * (e["size"] / max(b["size"], 1)))
            result.append({"name": e["name"], "is_dir": False, "size": e["size"],
                           "comp_size": comp, "method": b["method"],
                           "mtime": e.get("mtime")})
        return result

    def _read_blob(self, f, blob_index: int) -> bytes:
        """按 manifest 顺序累加偏移后读取指定块并解密解压。"""
        man = self.manifest()
        if not hasattr(self, "_offsets"):
            offsets = []
            pos = self.data_offset
            for b in man["blobs"]:
                offsets.append(pos)
                pos += b["comp"]
            self._offsets = offsets
        b = man["blobs"][blob_index]
        f.seek(self._offsets[blob_index])
        blob = f.read(b["comp"])
        if len(blob) != b["comp"]:
            raise ValueError("归档被截断（数据块不完整）")
        if self.encrypted:
            enc_key, mac_key = self._keys()
            blob = crypto.decrypt_blob(blob, enc_key, mac_key)
        raw = C.decompress_bytes(blob, C.METHODS[b["method"]], b["size"])
        if C.crc32_of(raw) != b["crc"]:
            raise ValueError("数据块 CRC 校验失败")
        return raw

    def extract(self, dest: str, progress=None, members=None) -> list:
        man = self.manifest()
        os.makedirs(dest, exist_ok=True)
        file_entries = [e for e in man["entries"] if e["type"] == "f"]
        total = len(file_entries)
        cache_id, cache_data = -1, b""
        written = []
        done = 0
        # 先建目录，保证空目录也能还原
        for e in man["entries"]:
            if e["type"] == "d":
                os.makedirs(_safe_join(dest, e["name"].rstrip("/")), exist_ok=True)

        with open(self.path, "rb") as f:
            for e in file_entries:
                if members and e["name"] not in members:
                    continue
                done += 1
                if progress:
                    progress(done, total, e["name"])
                if e["blob"] != cache_id:
                    cache_data = self._read_blob(f, e["blob"])
                    cache_id = e["blob"]
                off, size = e.get("offset", 0), e["size"]
                data = cache_data[off:off + size]
                if len(data) != size:
                    raise ValueError(f"条目数据越界: {e['name']}")
                target = _safe_join(dest, e["name"])
                os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
                with open(target, "wb") as out:
                    out.write(data)
                if e.get("mtime"):
                    try:
                        os.utime(target, (e["mtime"], e["mtime"]))
                    except OSError:
                        pass
                written.append(target)
        return written

    def summary(self) -> dict:
        man = self.manifest()
        return {
            "format": "CSE-X",
            "created": man.get("created"),
            "method": man.get("method"),
            "solid": man.get("solid"),
            "dedup": man.get("dedup"),
            "encrypted": man.get("encrypted"),
            "comment": man.get("comment"),
            "entries": len(man["entries"]),
            "blobs": len(man["blobs"]),
            "size": sum(b["size"] for b in man["blobs"]),
            "comp_size": os.path.getsize(self.path),
        }


def create_archive(inputs, out_path: str, method: str = "auto", password: str = None,
                   solid: bool = True, comment: str = "", progress=None, dedup: bool = True,
                   kdf_iters: int = crypto.DEFAULT_ITERS, exclude: str = None) -> dict:
    w = ExclusiveWriter(out_path, method=method, solid=solid, password=password,
                        comment=comment, progress=progress, dedup=dedup,
                        kdf_iters=kdf_iters, exclude=exclude)
    for item in inputs:
        w.add(item)
    return w.write()


def list_entries(path: str, password: str = None) -> list:
    return ExclusiveReader(path, password).entries()


def extract_archive(path: str, dest: str, password: str = None, progress=None) -> list:
    return ExclusiveReader(path, password).extract(dest, progress=progress)


def summary_of(path: str, password: str = None) -> dict:
    return ExclusiveReader(path, password).summary()
