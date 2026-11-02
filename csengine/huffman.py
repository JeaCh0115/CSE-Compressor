"""规范化哈夫曼编码（静态码表，256 符号）。

文件布局：[256B 码长表][8B 大端原始长度][位流]
码字按 (码长, 符号值) 排序后规范分配，编解码共用同一生成函数。
"""

import heapq
import struct
from collections import Counter

from .bitio import BitWriter, BitReader

_NSymbols = 256


def build_code_lengths(data: bytes) -> list:
    """由频率构建每个符号的哈夫曼码长（0 表示未使用）。"""
    freq = Counter(data)
    lengths = [0] * _NSymbols
    syms = [s for s in range(_NSymbols) if freq.get(s)]
    if not syms:
        return lengths
    if len(syms) == 1:
        lengths[syms[0]] = 1
        return lengths

    parent = {}
    uid = 0
    heap = []
    for s in syms:
        heapq.heappush(heap, (freq[s], uid, s))
        uid += 1
    next_node = _NSymbols
    while len(heap) > 1:
        f1, _, n1 = heapq.heappop(heap)
        f2, _, n2 = heapq.heappop(heap)
        parent[n1] = next_node
        parent[n2] = next_node
        heapq.heappush(heap, (f1 + f2, uid, next_node))
        uid += 1
        next_node += 1

    for s in syms:
        depth = 0
        cur = s
        while cur in parent:
            cur = parent[cur]
            depth += 1
        lengths[s] = depth
    return lengths


def canonical_codes(lengths: list) -> list:
    """按规范哈夫曼规则为每个符号分配码字（整数，按码长位宽写入）。"""
    codes = [0] * _NSymbols
    pairs = sorted((l, s) for s, l in enumerate(lengths) if l > 0)
    if not pairs:
        return codes
    code = 0
    prev_len = pairs[0][0]
    for idx, (l, s) in enumerate(pairs):
        if idx > 0:
            code = (code + 1) << (l - prev_len)
        codes[s] = code
        prev_len = l
    return codes


def _reverse_bits(value: int, nbits: int) -> int:
    """位反转：规范码是 MSB-first 语义无前缀，而我们的位流是 LSB-first。"""
    result = 0
    for _ in range(nbits):
        result = (result << 1) | (value & 1)
        value >>= 1
    return result


def compress(data: bytes) -> bytes:
    lengths = build_code_lengths(data)
    codes = canonical_codes(lengths)
    bw = BitWriter()
    for b in data:
        bw.write_bits(_reverse_bits(codes[b], lengths[b]), lengths[b])
    bw.flush()
    return bytes(lengths) + struct.pack(">Q", len(data)) + bw.getvalue()


def decompress(data: bytes) -> bytes:
    if len(data) < _NSymbols + 8:
        raise ValueError("Huffman 数据过短")
    lengths = list(data[:_NSymbols])
    (expected,) = struct.unpack(">Q", data[_NSymbols:_NSymbols + 8])
    codes = canonical_codes(lengths)

    table = {}
    for sym in range(_NSymbols):
        l = lengths[sym]
        if l:
            table[(l, codes[sym])] = sym

    br = BitReader(memoryview(data)[_NSymbols + 8:])
    out = bytearray()
    raw = 0      # LSB-first 累积的原始位
    clen = 0
    try:
        while len(out) < expected:
            raw |= br.read_bits(1) << clen
            clen += 1
            sym = table.get((clen, _reverse_bits(raw, clen)))
            if sym is not None:
                out.append(sym)
                raw = 0
                clen = 0
            elif clen > 64:
                raise ValueError("Huffman 码流非法")
    except EOFError as exc:
        raise ValueError("Huffman 位流提前结束") from exc
    return bytes(out)
