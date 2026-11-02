"""CSE Compressor 单元测试套件（unittest）。"""

import os
import shutil
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csengine import lz77, huffman, compress_bytes, decompress_bytes, METHODS
from csengine.archive import (
    create_archive, extract_archive, list_archive, ArchiveReader, MAGIC,
)
from csengine.formats import detect_format, human_size, hex_dump, ratio_percent


def sample_corpus():
    return {
        "empty": b"",
        "one": b"A",
        "text": (b"The quick brown fox jumps over the lazy dog. " * 80),
        "repeat": b"AB" * 4096,
        "zeros": bytes(2048),
        "random": struct.pack("<64B", *range(64)) * 48 + os.urandom(3000),
        "binary": bytes(range(256)) * 10,
    }


class TestLZ77(unittest.TestCase):
    def test_roundtrip(self):
        for name, data in sample_corpus().items():
            with self.subTest(case=name):
                packed = lz77.compress(data)
                self.assertEqual(lz77.decompress(packed), data)

    def test_header(self):
        packed = lz77.compress(b"hello world")
        self.assertEqual(struct.unpack(">Q", packed[:8])[0], 11)

    def test_corrupt_raises(self):
        packed = lz77.compress(b"some data " * 10)
        with self.assertRaises(ValueError):
            lz77.decompress(packed[:4])


class TestHuffman(unittest.TestCase):
    def test_roundtrip(self):
        for name, data in sample_corpus().items():
            with self.subTest(case=name):
                packed = huffman.compress(data)
                self.assertEqual(huffman.decompress(packed), data)

    def test_single_symbol(self):
        packed = huffman.compress(b"zzzz")
        self.assertEqual(huffman.decompress(packed), b"zzzz")

    def test_codes_prefix_free_lsb_first(self):
        """位流按 LSB-first 写出，码字（位反转后）必须互相无前缀冲突。"""
        data = b"aabbccddeeabc" * 30
        lengths = huffman.build_code_lengths(data)
        codes = huffman.canonical_codes(lengths)
        emitted = [(lengths[s], huffman._reverse_bits(codes[s], lengths[s]))
                   for s in range(256) if lengths[s]]
        for l1, c1 in emitted:
            for l2, c2 in emitted:
                if (l1, c1) != (l2, c2) and l2 > l1:
                    self.assertNotEqual(c2 & ((1 << l1) - 1), c1)


class TestCompressors(unittest.TestCase):
    def test_all_methods_roundtrip(self):
        for name, data in sample_corpus().items():
            for m in METHODS:
                with self.subTest(case=name, method=m):
                    blob = compress_bytes(data, m)
                    self.assertEqual(decompress_bytes(blob, m, len(data)), data)

    def test_auto_picks_smallest(self):
        text = b"hello compression " * 2000
        blob = compress_bytes(text, "auto")
        best = min(len(compress_bytes(text, m)) for m in METHODS)
        self.assertLessEqual(len(blob), best)

    def test_store_is_identity(self):
        data = os.urandom(100)
        self.assertEqual(compress_bytes(data, "store"), data)

    def test_lzhuf_beats_store_on_text(self):
        text = b"compression is fun " * 500
        self.assertLess(len(compress_bytes(text, "lzhuf")), len(text))

    def test_unknown_method_raises(self):
        with self.assertRaises(ValueError):
            compress_bytes(b"x", "nope")


class TestArchive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cse_test_")
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(os.path.join(self.src, "sub"))
        with open(os.path.join(self.src, "a.txt"), "wb") as f:
            f.write(b"alpha alpha alpha " * 100)
        with open(os.path.join(self.src, "sub", "b.bin"), "wb") as f:
            f.write(os.urandom(2048))
        self.out = os.path.join(self.tmp, "test.cse")
        self.dest = os.path.join(self.tmp, "dest")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_roundtrip(self):
        entries = create_archive([self.src], self.out, method="lzhuf")
        self.assertTrue(os.path.exists(self.out))
        names = {e.name for e in entries}
        self.assertIn("src/a.txt", names)
        self.assertIn("src/sub/b.bin", names)
        self.assertIn("src/sub/", names)

        files = extract_archive(self.out, self.dest)
        with open(os.path.join(self.dest, "src", "a.txt"), "rb") as f:
            self.assertEqual(f.read(), b"alpha alpha alpha " * 100)
        with open(os.path.join(self.dest, "src", "sub", "b.bin"), "rb") as f:
            self.assertEqual(f.read(), open(os.path.join(self.src, "sub", "b.bin"), "rb").read())

    def test_magic(self):
        create_archive([self.src], self.out, method="store")
        with open(self.out, "rb") as f:
            self.assertEqual(f.read(4), MAGIC)

    def test_crc_corruption_detected(self):
        create_archive([self.src], self.out, method="store")
        raw = bytearray(open(self.out, "rb").read())
        raw[-1] ^= 0xFF  # 破坏最后一个字节
        bad = os.path.join(self.tmp, "bad.cse")
        open(bad, "wb").write(bytes(raw))
        with self.assertRaises(ValueError):
            extract_archive(bad, self.dest)

    def test_zip_slip_blocked(self):
        create_archive([self.src], self.out, method="store")
        reader = ArchiveReader.__new__(ArchiveReader)
        from csengine.archive import _safe_join
        with self.assertRaises(ValueError):
            _safe_join(self.dest, "../../evil.txt")
        self.assertEqual(_safe_join(self.dest, "ok/dir/x.txt"),
                         os.path.join(self.dest, "ok", "dir", "x.txt"))


class TestFormats(unittest.TestCase):
    def test_detect(self):
        self.assertEqual(detect_format(b"CSE1..."), "CSE 归档 (本引擎)")
        self.assertEqual(detect_format(b"PK\x03\x04xxxx"), "ZIP")
        self.assertEqual(detect_format(b"7z\xbc\xaf\x27\x1czz"), "7-Zip")
        self.assertEqual(detect_format(b"\x1f\x8b\x08xx"), "GZIP")
        self.assertEqual(detect_format(b"hello world, plain text file content here"), "文本")

    def test_human_size(self):
        self.assertEqual(human_size(0), "0 B")
        self.assertEqual(human_size(512), "512 B")
        self.assertEqual(human_size(2048), "2.00 KB")
        self.assertEqual(human_size(3 * 1024 * 1024), "3.00 MB")

    def test_hex_dump(self):
        dump = hex_dump(b"ABCD", width=8)
        self.assertIn("00000000", dump)
        self.assertIn("|ABCD|", dump)

    def test_ratio(self):
        self.assertEqual(ratio_percent(100, 50), "50.0%")
        self.assertEqual(ratio_percent(0, 0), "—")


if __name__ == "__main__":
    unittest.main(verbosity=2)
