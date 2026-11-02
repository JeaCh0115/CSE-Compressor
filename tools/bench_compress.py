"""压缩性能基准：造一批文件，计时 create_archive，再解压校验。

用法: python tools/bench_compress.py [文件数] [--keep]
"""
import os
import random
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from csengine import converters  # noqa: E402

WORDS = ("compressor archive solid dedup block huffman lzma zlib entropy window "
         "字典 压缩 归档 固实 去重 校验 元数据 加密").split()


def make_corpus(root, n_files):
    src = os.path.join(root, "src")
    os.makedirs(src, exist_ok=True)
    rnd = random.Random(20261029)
    total = 0
    for i in range(n_files):
        sub = os.path.join(src, f"d{i % 20:02d}")
        os.makedirs(sub, exist_ok=True)
        body = " ".join(rnd.choice(WORDS) for _ in range(rnd.randint(50, 400)))
        p = os.path.join(sub, f"f{i:05d}.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        total += os.path.getsize(p)
    for i in range(3):  # 几个大一点的二进制
        p = os.path.join(src, f"blob{i}.bin")
        with open(p, "wb") as f:
            f.write(os.urandom(6 * 1024 * 1024) if i == 0 else
                    bytes(bytearray(rnd.getrandbits(8) for _ in range(3 * 1024 * 1024))))
        total += os.path.getsize(p)
    return src, total


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 2000
    tmp = tempfile.mkdtemp(prefix="cse_bench_")
    try:
        src, total = make_corpus(tmp, n)
        arc = os.path.join(tmp, "out.cse")
        t0 = time.time()
        info = converters.create(arc, [src], method="auto", progress=None)
        dt = time.time() - t0
        size = os.path.getsize(arc)
        print(f"源 {total/1048576:.1f} MB / {n+3} 文件 → {size/1048576:.2f} MB "
              f"（{size/total*100:.1f}%）")
        print(f"压缩耗时 {dt:.2f}s   吞吐 {total/1048576/dt:.1f} MB/s   块数 {info.get('blobs')}")
        t1 = time.time()
        dest = os.path.join(tmp, "out")
        files = converters.extract(arc, dest)
        print(f"解压 {len(files)} 个文件 {time.time()-t1:.2f}s")
        converters.test(arc)
        print("CRC 校验通过")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
