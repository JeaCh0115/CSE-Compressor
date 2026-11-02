"""LZ77 滑动窗口压缩（参考 DEFLATE 的 LZ77 阶段）。

token 布局（LSB-first 位流）：
  - 字面量 : [flag=0][8bit 字节]
  - 匹配   : [flag=1][15bit 偏移][8bit 长度-4]

文件布局：[8B 大端原始长度][位流]
"""

import struct

from .bitio import BitWriter, BitReader

MIN_MATCH = 4
MAX_MATCH = 259          # 4 + 255
OFFSET_BITS = 15
WINDOW = 1 << OFFSET_BITS  # 32768
MAX_CHAIN = 32           # 每个哈希桶最多回溯的候选数
_BUCKET_CAP = 64         # 每个哈希桶最多保留的位置数


def _key(data: bytes, p: int) -> int:
    return (data[p] << 16) | (data[p + 1] << 8) | data[p + 2]


def compress(data: bytes) -> bytes:
    n = len(data)
    bw = BitWriter()
    table = {}  # 3 字节前缀 -> 位置列表（最近的在尾部）

    def insert(p: int) -> None:
        if p + 3 <= n:
            k = _key(data, p)
            bucket = table.get(k)
            if bucket is None:
                table[k] = [p]
            else:
                bucket.append(p)
                if len(bucket) > _BUCKET_CAP:
                    del bucket[0]

    i = 0
    while i < n:
        best_len = 0
        best_off = 0
        if i + MIN_MATCH <= n:
            k = _key(data, i)
            bucket = table.get(k)
            if bucket:
                limit = min(MAX_MATCH, n - i)
                low = i - WINDOW
                tries = MAX_CHAIN
                for j in reversed(bucket):
                    if j < low or tries <= 0:
                        break
                    tries -= 1
                    l = 0
                    while l < limit and data[j + l] == data[i + l]:
                        l += 1
                    if l > best_len:
                        best_len = l
                        best_off = i - j
                        if l >= limit:
                            break

        if best_len >= MIN_MATCH:
            bw.write_bits(1, 1)
            bw.write_bits(best_off, OFFSET_BITS)
            bw.write_bits(best_len - MIN_MATCH, 8)
            end = min(i + best_len, n)
            for p in range(i, end):
                insert(p)
            i += best_len
        else:
            bw.write_bits(0, 1)
            bw.write_bits(data[i], 8)
            insert(i)
            i += 1

    bw.flush()
    return struct.pack(">Q", n) + bw.getvalue()


def decompress(data: bytes) -> bytes:
    if len(data) < 8:
        raise ValueError("LZ77 数据过短")
    (expected,) = struct.unpack(">Q", data[:8])
    br = BitReader(memoryview(data)[8:])
    out = bytearray()
    try:
        while len(out) < expected:
            if br.read_bits(1):
                off = br.read_bits(OFFSET_BITS)
                length = br.read_bits(8) + MIN_MATCH
                start = len(out) - off
                if start < 0:
                    raise ValueError("非法回溯偏移")
                for _ in range(length):
                    out.append(out[start])
                    start += 1
            else:
                out.append(br.read_bits(8))
    except EOFError as exc:
        raise ValueError("LZ77 位流提前结束") from exc
    return bytes(out)
