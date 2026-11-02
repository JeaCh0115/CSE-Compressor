"""多格式互操作测试（zip / tar / tar.gz / gz / bz2 / xz）。"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csengine import converters


class TestConverters(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="conv_")
        self.src = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(self.src, "sub"))
        with open(os.path.join(self.src, "a.txt"), "wb") as f:
            f.write(b"compress me " * 300)
        with open(os.path.join(self.src, "sub", "b.bin"), "wb") as f:
            f.write(bytes(range(256)) * 20)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_guess_format(self):
        cases = {"x.cse": "cse", "x.zip": "zip", "x.tar": "tar", "x.tar.gz": "targz",
                 "x.tgz": "targz", "x.tar.bz2": "tarbz2", "x.tar.xz": "tarxz",
                 "x.gz": "gz", "x.bz2": "bz2", "x.xz": "xz", "x.7z": "7z"}
        for name, fmt in cases.items():
            self.assertEqual(converters.guess_format(name), fmt, name)

    def test_zip_roundtrip(self):
        arc = os.path.join(self.tmp, "t.zip")
        converters.create(arc, [self.src], fmt="zip")
        items = converters.list_items(arc)
        self.assertTrue(any(i["name"].endswith("a.txt") for i in items))
        dest = os.path.join(self.tmp, "out_zip")
        converters.extract(arc, dest)
        with open(os.path.join(dest, "proj", "a.txt"), "rb") as f:
            self.assertEqual(f.read(), b"compress me " * 300)

    def test_tar_family_roundtrip(self):
        for ext, fmt in [("tar", "tar"), ("tar.gz", "targz")]:
            arc = os.path.join(self.tmp, f"t.{ext}")
            converters.create(arc, [self.src], fmt=fmt)
            dest = os.path.join(self.tmp, "out_" + fmt)
            converters.extract(arc, dest)
            with open(os.path.join(dest, "proj", "sub", "b.bin"), "rb") as f:
                self.assertEqual(f.read(), bytes(range(256)) * 20)

    def test_single_file_formats(self):
        src = os.path.join(self.src, "a.txt")
        for ext in ("gz", "bz2", "xz"):
            arc = os.path.join(self.tmp, f"one.txt.{ext}")
            converters.create(arc, [src], fmt=ext)
            dest = os.path.join(self.tmp, "out_" + ext)
            converters.extract(arc, dest)
            name = converters.list_items(arc)[0]["name"]
            with open(os.path.join(dest, name), "rb") as f:
                self.assertEqual(f.read(), b"compress me " * 300)

    def test_password_rejected_for_zip(self):
        arc = os.path.join(self.tmp, "t.zip")
        with self.assertRaises(ValueError):
            converters.create(arc, [self.src], fmt="zip", password="pw")

    def test_zip_slip_blocked(self):
        import zipfile
        arc = os.path.join(self.tmp, "evil.zip")
        with zipfile.ZipFile(arc, "w") as zf:
            zf.writestr("../../escape.txt", b"bad")
        with self.assertRaises(ValueError):
            converters.extract(arc, os.path.join(self.tmp, "out_evil"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escape.txt")))

    def test_test_and_summary(self):
        arc = os.path.join(self.tmp, "t.cse")
        converters.create(arc, [self.src])
        self.assertTrue(converters.test(arc))
        summary = converters.summary_of(arc)
        self.assertEqual(summary["format"], "CSE-X")

    def test_same_name_output_blocked(self):
        """源是 x.zip、输出也叫 x.zip：必须直接拒绝。

        zipfile / tarfile / gzip 都是先截断目标再回读源，同名时源文件当场被清空
        （只读文件则是 [Errno 13] Permission denied）——这是唯一会"数据消失"的路径。
        """
        import zipfile

        def read(path):
            with open(path, "rb") as f:
                return f.read()

        for ext, fmt in ((".zip", "zip"), (".tar", "tar"), (".tar.gz", "targz"),
                         (".gz", "gz"), (".bz2", "bz2"), (".xz", "xz"), (".cse", "cse")):
            src = os.path.join(self.tmp, "同名" + ext)
            # 用别的源造一个该格式的归档，再拿它自己当输入
            converters.create(src, [os.path.join(self.src, "a.txt")], fmt=fmt)
            before = read(src)
            with self.assertRaises(ValueError, msg=f"{fmt} 同名没被拦住"):
                converters.create(src, [src], fmt=fmt)
            self.assertEqual(read(src), before,
                             f"{fmt} 同名压缩把源文件改了")
        # 只读文件（浏览器的"已下载"标记常见）原来报 Errno 13，现在走同一条拒绝
        ro = os.path.join(self.tmp, "只读.zip")
        with zipfile.ZipFile(ro, "w") as zf:
            zf.writestr("a.txt", b"x" * 100)
        os.chmod(ro, 0o444)
        try:
            with self.assertRaises(ValueError):
                converters.create(ro, [ro], fmt="zip")
        finally:
            os.chmod(ro, 0o666)

    def test_output_inside_source_tree_not_self_included(self):
        """归档写进正在压缩的目录里时，不能把自己（写了一半的文件）塞进去。"""
        for fmt, name in (("zip", "out.zip"), ("tar", "out.tar"), ("cse", "out.cse")):
            out = os.path.join(self.src, name)
            converters.create(out, [self.src], fmt=fmt)
            names = [i["name"] for i in converters.list_items(out)]
            self.assertIn("proj/a.txt", names, f"{fmt} 少了正常文件")
            self.assertFalse(any(n.endswith(name) for n in names),
                             f"{fmt} 把自己塞进了归档：{names}")
            os.remove(out)


class TestNoOverwrite(unittest.TestCase):
    """覆盖保护：目标已存在时必须能拦下来，且原有文件一个字节都不能动。

    GUI 里的确认框是给用户看的，这一层是"就算前端判断漏了也不许毁文件"的兜底。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="conv_ow_")
        self.src = os.path.join(self.tmp, "a.txt")
        with open(self.src, "wb") as f:
            f.write(b"hello " * 100)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unique_path_file(self):
        p = os.path.join(self.tmp, "x.zip")
        self.assertEqual(converters.unique_path(p, "_压缩"), p)   # 不存在就原样返回
        with open(p, "wb") as f:
            f.write(b"1")
        first = converters.unique_path(p, "_压缩")
        self.assertEqual(os.path.basename(first), "x_压缩.zip")
        with open(first, "wb") as f:
            f.write(b"1")
        self.assertEqual(os.path.basename(converters.unique_path(p, "_压缩")), "x_压缩2.zip")

    def test_unique_path_multi_ext(self):
        """.tar.gz 是两段扩展名，改名要插在它前面，不能变成 x.tar_压缩.gz。"""
        p = os.path.join(self.tmp, "x.tar.gz")
        with open(p, "wb") as f:
            f.write(b"1")
        self.assertEqual(os.path.basename(converters.unique_path(p, "_压缩")), "x_压缩.tar.gz")

    def test_unique_path_dir_numeric(self):
        """目录用纯数字后缀：a_解压 → a_解压2（再加一层"_压缩"会读成"解压的压缩"）。"""
        p = os.path.join(self.tmp, "a_解压")
        os.makedirs(p)
        self.assertEqual(os.path.basename(converters.unique_path(p, "")), "a_解压2")
        os.makedirs(os.path.join(self.tmp, "a_解压2"))
        self.assertEqual(os.path.basename(converters.unique_path(p, "")), "a_解压3")

    def test_create_refuses_existing_target(self):
        for ext, fmt in ((".zip", "zip"), (".tar", "tar"), (".tar.gz", "targz"),
                         (".gz", "gz"), (".bz2", "bz2"), (".xz", "xz"), (".cse", "cse")):
            out = os.path.join(self.tmp, "已有" + ext)
            converters.create(out, [self.src], fmt=fmt)
            with open(out, "rb") as f:
                before = f.read()
            with self.assertRaises(ValueError, msg=f"{fmt} 没拦住覆盖"):
                converters.create(out, [self.src], fmt=fmt, overwrite=False)
            with open(out, "rb") as f:
                self.assertEqual(f.read(), before, f"{fmt} 目标被改写了")
            # 源文件也必须完好（同名那条路径曾经把源截断过）
            with open(self.src, "rb") as f:
                self.assertEqual(f.read(), b"hello " * 100)

    def test_create_overwrites_by_default(self):
        """CLI 等调用方不传 overwrite 时保持旧行为，兼容既有脚本。"""
        out = os.path.join(self.tmp, "d.zip")
        converters.create(out, [self.src], fmt="zip")
        converters.create(out, [self.src], fmt="zip")
        self.assertTrue(converters.test(out))

    def test_conflicts_counts_existing_files(self):
        arc = os.path.join(self.tmp, "c.zip")
        converters.create(arc, [self.src], fmt="zip")
        dest = os.path.join(self.tmp, "out")
        os.makedirs(dest)
        self.assertEqual(converters.conflicts(arc, dest)["conflicts"], 0)
        with open(os.path.join(dest, "a.txt"), "wb") as f:
            f.write(b"old")
        r = converters.conflicts(arc, dest)
        self.assertEqual(r["conflicts"], 1)
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["samples"], ["a.txt"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
