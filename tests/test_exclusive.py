"""CSE-X 独家格式测试。"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csengine import exclusive, crypto, converters


def build_tree(root):
    os.makedirs(os.path.join(root, "sub", "deep"))
    files = {
        "readme.txt": "CSE-X 独家格式测试，包含中文与 emoji 🚀".encode("utf-8") * 60,
        "sub/data.json": b'{"items": [1, 2, 3], "name": "demo"}' * 40,
        "sub/deep/same1.bin": b"DUPLICATE-CONTENT" * 500,
        "sub/deep/same2.bin": b"DUPLICATE-CONTENT" * 500,   # 与 same1 完全一致
        "empty.txt": b"",
        "big.bin": bytes(range(256)) * 900,                 # 超过固实小文件阈值
    }
    for rel, data in files.items():
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
    return files


class TestExclusiveFormat(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="csex_")
        self.src = os.path.join(self.tmp, "tree")
        self.files = build_tree(self.src)
        self.arc = os.path.join(self.tmp, "out.cse")
        self.dest = os.path.join(self.tmp, "dest")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _verify(self, dest, root_name="tree"):
        for rel in self.files:
            p = os.path.join(dest, root_name, *rel.split("/"))
            self.assertTrue(os.path.exists(p), f"缺失 {rel}")
            with open(p, "rb") as f:
                self.assertEqual(f.read(), self.files[rel], f"内容不符 {rel}")

    def test_roundtrip_solid(self):
        info = exclusive.create_archive([self.src], self.arc, method="auto", solid=True)
        self.assertTrue(info["solid"])
        self.assertEqual(info["files"], len(self.files))
        exclusive.extract_archive(self.arc, self.dest)
        self._verify(self.dest)

    def test_roundtrip_non_solid(self):
        exclusive.create_archive([self.src], self.arc, method="zlib", solid=False)
        exclusive.extract_archive(self.arc, self.dest)
        self._verify(self.dest)

    def test_dedup_merges_identical_files(self):
        info = exclusive.create_archive([self.src], self.arc, method="zlib",
                                        solid=False, dedup=True)
        # 两个相同文件在非固实模式下应合并为一个数据块
        self.assertLess(info["blobs"], info["files"])

    def test_dedup_disabled_keeps_all(self):
        info = exclusive.create_archive([self.src], self.arc, method="zlib",
                                        solid=False, dedup=False)
        self.assertEqual(info["blobs"], info["files"])

    def test_encrypted_roundtrip(self):
        exclusive.create_archive([self.src], self.arc, method="lzma",
                                 password="s3cret", kdf_iters=1000)
        summary = exclusive.summary_of(self.arc, "s3cret")
        self.assertTrue(summary["encrypted"])
        exclusive.extract_archive(self.arc, self.dest, password="s3cret")
        self._verify(self.dest)

    def test_wrong_password_rejected(self):
        exclusive.create_archive([self.src], self.arc, password="right", kdf_iters=1000)
        with self.assertRaises(ValueError):
            exclusive.list_entries(self.arc, password="wrong")

    def test_password_required(self):
        exclusive.create_archive([self.src], self.arc, password="right", kdf_iters=1000)
        with self.assertRaises(ValueError):
            exclusive.extract_archive(self.arc, self.dest)

    def test_tamper_detected(self):
        exclusive.create_archive([self.src], self.arc, method="zlib", password="pw",
                                 kdf_iters=1000)
        with open(self.arc, "rb") as f:
            raw = bytearray(f.read())
        raw[-5] ^= 0xFF
        bad = os.path.join(self.tmp, "bad.cse")
        with open(bad, "wb") as f:
            f.write(bytes(raw))
        with self.assertRaises(ValueError):
            exclusive.extract_archive(bad, self.dest, password="pw")

    def test_mtime_preserved(self):
        target = os.path.join(self.src, "readme.txt")
        os.utime(target, (1500000000, 1500000000))
        exclusive.create_archive([self.src], self.arc, method="store")
        exclusive.extract_archive(self.arc, self.dest)
        st = os.stat(os.path.join(self.dest, "tree", "readme.txt"))
        self.assertAlmostEqual(st.st_mtime, 1500000000, delta=2)

    def test_listing_metadata(self):
        exclusive.create_archive([self.src], self.arc, method="zlib", comment="测试备注")
        items = exclusive.list_entries(self.arc)
        names = {i["name"] for i in items}
        self.assertIn("tree/readme.txt", names)
        self.assertIn("tree/sub/deep/", names)
        summary = exclusive.summary_of(self.arc)
        self.assertEqual(summary["comment"], "测试备注")
        self.assertEqual(summary["format"], "CSE-X")

    def test_magic_and_version(self):
        exclusive.create_archive([self.src], self.arc, method="store")
        with open(self.arc, "rb") as f:
            self.assertEqual(f.read(4), b"CSX2")
        self.assertTrue(exclusive.is_exclusive(self.arc))


class TestCrypto(unittest.TestCase):
    def test_chacha20_rfc8439_vector(self):
        key = bytes(range(32))
        nonce = bytes.fromhex("000000090000004a00000000")
        ks = crypto.keystream(key, nonce, 64, counter=1)
        expected = bytes.fromhex(
            "10f1e7e4d13b5915500fdd1fa32071c4c7d1f4c733c068030422aa9ac3d46c4e"
            "d2826446079faa0914c2d705d98b02a2b5129cd1de164eb9cbd083e8a2503c4e")
        self.assertEqual(ks, expected)

    def test_encrypt_decrypt_roundtrip(self):
        enc, mac = crypto.derive_keys("pw", b"0" * 16, 1000)
        data = b"hello chacha " * 200
        blob = crypto.encrypt_blob(data, enc, mac)
        self.assertEqual(crypto.decrypt_blob(blob, enc, mac), data)

    def test_tampered_ciphertext(self):
        enc, mac = crypto.derive_keys("pw", b"0" * 16, 1000)
        blob = bytearray(crypto.encrypt_blob(b"data" * 50, enc, mac))
        blob[-1] ^= 1
        with self.assertRaises(ValueError):
            crypto.decrypt_blob(bytes(blob), enc, mac)

    def test_wrong_key(self):
        enc, mac = crypto.derive_keys("pw", b"0" * 16, 1000)
        enc2, mac2 = crypto.derive_keys("other", b"0" * 16, 1000)
        blob = crypto.encrypt_blob(b"secret" * 10, enc, mac)
        with self.assertRaises(ValueError):
            crypto.decrypt_blob(blob, enc2, mac2)


class TestLegacyCompatibility(unittest.TestCase):
    def test_cse_v1_still_readable(self):
        tmp = tempfile.mkdtemp(prefix="legacy_")
        try:
            src = os.path.join(tmp, "s")
            os.makedirs(src)
            with open(os.path.join(src, "a.txt"), "wb") as f:
                f.write(b"legacy " * 100)
            arc = os.path.join(tmp, "old.cse")
            from csengine import archive as legacy
            legacy.create_archive([src], arc, method="lzhuf")
            items = converters.list_items(arc)
            self.assertEqual(len(items), 2)  # 目录 + 文件
            dest = os.path.join(tmp, "out")
            converters.extract(arc, dest)
            with open(os.path.join(dest, "s", "a.txt"), "rb") as f:
                self.assertEqual(f.read(), b"legacy " * 100)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
