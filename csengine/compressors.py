"""多后端压缩器调度（参考 7z 的 coder 链思路）。

方法编号：
  0 store  — 仅存储
  1 lzhuf  — 自研 LZ77 + Huffman（纯 Python 教学级实现）
  2 zlib   — DEFLATE（zlib 库）
  3 lzma   — LZMA（标准库）

auto 策略的性能约定（2026-10 优化）：
  · **不再把 lzhuf 放进自动候选**。它是纯 Python，压一块 2MB 要几百毫秒且会
    长时间占住 GIL（界面拖动直接卡成幻灯片），压缩率还不如 zlib/lzma；
    显式指定 method="lzhuf" 仍可正常使用与解压。
  · 大块数据不再"每种方法都压一遍"，而是先取头/中/尾采样（≤256KB）试压，
    选出赢家再整块压一次——2MB 固实块从 4 次全量压缩降到 1 次。
  · 采样判定"基本压不动"（图片/视频/已压缩包）时直接 store，避免拿 lzma
    去磨几百 MB 随机数据。
  · LZMA 的字典按块大小收缩（块 2MB 就给 2MB 字典）。LZMA2 原始流允许
    解码端字典不小于编码端，实测互通；这既省内存又让多线程并行不爆内存。
"""

import lzma
import struct
import zlib

from . import huffman, lz77

METHODS = {"store": 0, "lzhuf": 1, "zlib": 2, "lzma": 3}
METHOD_NAMES = {v: k for k, v in METHODS.items()}

# auto 的默认候选：store 兜底 + 两个 C 实现（都释放 GIL，可并行）
AUTO_CANDIDATES = ("zlib", "lzma")

_AUTO_LZMA_MIN = 16 * 1024        # 小于 16KB 不尝试 lzma（头部开销不划算）

PROBE_LIMIT = 256 * 1024          # 超过这个长度改为采样探测
_PROBE_PIECE = 64 * 1024          # 采样片段：头 64KB + 中 64KB + 尾 128KB
_INCOMPRESSIBLE = 0.985           # 采样压缩率高于此值 → 认为压不动，直接 store
_BIG_FILE = 64 * 1024 * 1024      # 超过 64MB 时 lzma 必须明显更优才用
_BIG_MARGIN = 0.8                 # lzma 采样结果需优于 zlib 的 80% 才值得

_LZMA_DICT_MIN = 64 * 1024
_LZMA_DICT_MAX = 8 * 1024 * 1024
# 解码端固定用 8MB 字典（≥ 任何编码字典），因此不必把字典大小写进归档
_LZMA_DEC_FILTERS = [{"id": lzma.FILTER_LZMA2, "preset": 6, "dict_size": _LZMA_DICT_MAX}]


def _norm(method):
    if isinstance(method, int):
        if method not in METHOD_NAMES:
            raise ValueError(f"未知压缩方法编号: {method}")
        return method
    key = str(method).lower()
    if key not in METHODS:
        raise ValueError(f"未知压缩方法: {method}（可选: {', '.join(METHODS)}）")
    return METHODS[key]


def _lzma_dict_for(size: int) -> int:
    """按数据量挑字典：向上取 2 的幂，夹在 [64KB, 8MB]。"""
    d = _LZMA_DICT_MIN
    while d < size and d < _LZMA_DICT_MAX:
        d <<= 1
    return min(d, _LZMA_DICT_MAX)


def lzma_compress(data: bytes) -> bytes:
    filters = [{"id": lzma.FILTER_LZMA2, "preset": 6,
                "dict_size": _lzma_dict_for(len(data))}]
    return lzma.compress(data, format=lzma.FORMAT_RAW, filters=filters)


def compress_bytes(data: bytes, method="auto") -> bytes:
    if isinstance(method, str) and method.lower() == "auto":
        return pick_best(data)[1]
    code = _norm(method)
    if code == 0:
        return data
    if code == 1:
        return huffman.compress(lz77.compress(data))
    if code == 2:
        return zlib.compress(data, 6)
    if code == 3:
        return lzma_compress(data)
    raise ValueError("不可达")


def decompress_bytes(data: bytes, method, orig_size: int) -> bytes:
    code = _norm(method)
    if code == 0:
        result = data
    elif code == 1:
        result = lz77.decompress(huffman.decompress(data))
    elif code == 2:
        result = zlib.decompress(data)
    elif code == 3:
        result = lzma.decompress(data, format=lzma.FORMAT_RAW, filters=_LZMA_DEC_FILTERS)
    else:
        raise ValueError("不可达")
    if len(result) != orig_size:
        raise ValueError(
            f"解压后大小不符: 期望 {orig_size}, 实际 {len(result)}"
        )
    return result


def _probe(data: bytes) -> bytes:
    """头 / 中 / 尾三段采样：单一位置的样本对混合数据太不靠谱。"""
    n = len(data)
    if n <= PROBE_LIMIT:
        return data
    piece = _PROBE_PIECE
    mid = max(0, n // 2 - piece // 2)
    tail = max(0, n - piece * 2)
    return data[:piece] + data[mid:mid + piece] + data[tail:]


def pick_best(data: bytes, candidates=AUTO_CANDIDATES):
    """auto 策略：返回 (method_code, 压缩后字节)。

    ≤256KB 全量试压（便宜且最准）；更大则用采样挑方法，再整块压一次。
    """
    if len(data) < _AUTO_LZMA_MIN:
        candidates = tuple(c for c in candidates if c != "lzma")
    cands = [c for c in candidates if c in METHODS] or ["store"]

    # 1) 小数据：全量试压，结果最准
    if len(data) <= PROBE_LIMIT:
        best_code, best_blob = 0, data
        for name in cands:
            blob = compress_bytes(data, name)
            if len(blob) < len(best_blob):
                best_code, best_blob = METHODS[name], blob
        return best_code, best_blob

    # 2) 大数据：先采样探测
    probe = _probe(data)
    scores = {name: len(compress_bytes(probe, name)) for name in cands}
    if min(scores.values()) >= len(probe) * _INCOMPRESSIBLE:
        return 0, data                      # 压不动，别浪费时间
    winner = min(scores, key=scores.get)
    if (len(data) > _BIG_FILE and winner == "lzma"
            and scores["zlib"] and scores["lzma"] > scores["zlib"] * _BIG_MARGIN):
        winner = "zlib"                     # 超大文件：lzma 优势不明显就用 zlib 保速度

    blob = compress_bytes(data, winner)
    if len(blob) >= len(data):
        return 0, data
    return METHODS[winner], blob


def crc32_of(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def pack_u64(n: int) -> bytes:
    return struct.pack("<Q", n)
