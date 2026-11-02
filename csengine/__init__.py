"""csengine — CSE Compressor Engine（参考 7-Zip 模块化架构的压缩引擎）。

模块划分：
- bitio       : 位级读写流（LZ77 / Huffman 底层设施）
- lz77        : LZ77 滑动窗口字典压缩（15bit 偏移 / 8bit 匹配长度）
- huffman     : 规范化哈夫曼编码（静态码表，256 符号）
- compressors : 多后端压缩器调度（store / lzhuf / zlib / lzma）
- archive     : .cse v1 归档容器（兼容保留）
- exclusive   : .CSE-X（CSX2）独家格式：固实 + 去重 + 加密 + 元数据
- crypto      : ChaCha20 + HMAC-SHA256 认证加密
- converters  : 多格式互操作统一入口（cse/zip/tar/gz/bz2/xz/7z/rar…）
- exttools    : 外部解压后端探测与调用（7-Zip / UnRAR / py7zr）
- formats     : 格式化 / 格式识别模块
- version     : 版本与构建信息（Beta 渠道）
- winassoc    : Windows 文件关联 / 右键菜单 / 图标

导入策略（启动性能关键）：所有子模块按需惰性导入。
`from csengine import __version__` 只加载 version.py（几毫秒），
不会牵出 tarfile / lzma / zipfile 等重模块——桌面端冷启动因此明显更快。
"""

import importlib

# 公开名称 -> (子模块, 属性名)；属性名 None 表示导出子模块本身
_LAZY = {
    "__version__": ("version", "__version__"),
    "CHANNEL": ("version", "CHANNEL"),
    "CHANNEL_LABEL": ("version", "CHANNEL_LABEL"),
    "VERSION_TUPLE": ("version", "VERSION_TUPLE"),
    "PRODUCT": ("version", "PRODUCT"),
    "COMPANY": ("version", "COMPANY"),
    "COPYRIGHT": ("version", "COPYRIGHT"),
    "HOMEPAGE": ("version", "HOMEPAGE"),
    "BUILD_TIME": ("version", "BUILD_TIME"),
    "BUILD_ID": ("version", "BUILD_ID"),
    "about_data": ("version", "about_data"),
    "version_string": ("version", "version_string"),
    "is_beta": ("version", "is_beta"),
    "ArchiveWriter": ("archive", "ArchiveWriter"),
    "ArchiveReader": ("archive", "ArchiveReader"),
    "Entry": ("archive", "Entry"),
    "create_archive": ("archive", "create_archive"),
    "extract_archive": ("archive", "extract_archive"),
    "list_archive": ("archive", "list_archive"),
    "test_archive": ("archive", "test_archive"),
    "METHODS": ("compressors", "METHODS"),
    "compress_bytes": ("compressors", "compress_bytes"),
    "decompress_bytes": ("compressors", "decompress_bytes"),
    "version": ("version", None),
    "archive": ("archive", None),
    "compressors": ("compressors", None),
    "converters": ("converters", None),
    "exclusive": ("exclusive", None),
    "formats": ("formats", None),
    "crypto": ("crypto", None),
    "exttools": ("exttools", None),
    "winassoc": ("winassoc", None),
}

__all__ = sorted(_LAZY)


def __getattr__(name):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module("." + target[0], __name__)
    value = module if target[1] is None else getattr(module, target[1])
    globals()[name] = value  # 缓存，后续访问零成本
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY))
