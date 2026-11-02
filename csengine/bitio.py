"""位级读写流：LZ77 与 Huffman 的公共底层设施。

约定：所有位按 LSB-first（低位在前）顺序写入/读出。
"""


class BitWriter:
    """把任意位宽的整数按 LSB-first 写入字节流。"""

    def __init__(self):
        self.out = bytearray()
        self._buf = 0
        self._cnt = 0

    def write_bits(self, value: int, nbits: int) -> None:
        if nbits <= 0:
            return
        value &= (1 << nbits) - 1
        self._buf |= value << self._cnt
        self._cnt += nbits
        while self._cnt >= 8:
            self.out.append(self._buf & 0xFF)
            self._buf >>= 8
            self._cnt -= 8

    def flush(self) -> None:
        """把残余位补零对齐到字节边界。"""
        if self._cnt:
            self.out.append(self._buf & 0xFF)
            self._buf = 0
            self._cnt = 0

    def getvalue(self) -> bytes:
        return bytes(self.out)


class BitReader:
    """从字节流按 LSB-first 读出任意位宽的整数。"""

    def __init__(self, data):
        self.data = data
        self.pos = 0
        self._buf = 0
        self._cnt = 0

    def read_bits(self, nbits: int) -> int:
        if nbits == 0:
            return 0
        while self._cnt < nbits:
            if self.pos >= len(self.data):
                raise EOFError("位流已耗尽")
            self._buf |= self.data[self.pos] << self._cnt
            self.pos += 1
            self._cnt += 8
        value = self._buf & ((1 << nbits) - 1)
        self._buf >>= nbits
        self._cnt -= nbits
        return value
