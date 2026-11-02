"""格式化模块：格式识别、大小格式化、hex dump。"""

import os

# 魔数签名表（前缀 -> 格式名）
SIGNATURES = [
    (b"CSX2", "CSE-X 归档 (本引擎)"),
    (b"CSE1", "CSE 归档 (本引擎)"),
    (b"PK\x03\x04", "ZIP"),
    (b"PK\x05\x06", "ZIP (空)"),
    (b"PK\x07\x08", "ZIP (分卷)"),
    (b"\x1f\x8b", "GZIP"),
    (b"7z\xbc\xaf\x27\x1c", "7-Zip"),
    (b"Rar!\x1a\x07\x01\x00", "RAR5"),
    (b"Rar!\x1a\x07\x00", "RAR"),
    (b"BZh", "BZIP2"),
    (b"\xfd7zXZ\x00", "XZ"),
    (b"\x28\xb5\x2f\xfd", "ZSTD"),
    (b"\x04\x22\x4d\x18", "LZ4"),
    (b"MSCF", "CAB"),
    (b"\x60\xea", "ARJ"),
    (b"\x1f\x9d", "Z (compress)"),
    (b"\x89PNG", "PNG"),
    (b"\xff\xd8\xff", "JPEG"),
    (b"%PDF", "PDF"),
]

# 需要偏移定位的签名（ISO 9660 的 "CD001" 在 0x8001 处）
OFFSET_SIGNATURES = [
    (0x8001, b"CD001", "ISO 光盘映像"),
    (0x8001, b"BEA01", "ISO 光盘映像"),
]


def detect_format(data: bytes) -> str:
    """按魔数识别格式；未知返回 '未知/纯数据'。"""
    for magic, label in SIGNATURES:
        if data.startswith(magic):
            return label
    for off, magic, label in OFFSET_SIGNATURES:
        if data[off:off + len(magic)] == magic:
            return label
    printable = sum(32 <= b < 127 or b in (9, 10, 13) for b in data[:512])
    if data[:512] and printable / min(len(data), 512) > 0.9:
        return "文本"
    return "未知/纯数据"


def detect_file(path: str) -> str:
    with open(path, "rb") as f:
        head = f.read(0x8006)  # 足够覆盖 ISO 的 0x8001 偏移
    return detect_format(head)


def human_size(n: float) -> str:
    """人性化大小：B / KB / MB / GB。"""
    if n < 0:
        raise ValueError("大小不能为负")
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def ratio_percent(orig: int, comp: int) -> str:
    if orig == 0:
        return "—"
    return f"{comp / orig * 100:.1f}%"


def hex_dump(data: bytes, limit: int = 256, width: int = 16) -> str:
    """经典 hexdump 视图：偏移 + 十六进制 + ASCII。"""
    lines = []
    for off in range(0, min(len(data), limit), width):
        chunk = data[off:off + width]
        hexpart = " ".join(f"{b:02x}" for b in chunk)
        asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{off:08x}  {hexpart:<{width * 3 - 1}}  |{asciipart}|")
    if len(data) > limit:
        lines.append(f"...（共 {len(data)} 字节，仅显示前 {limit} 字节）")
    return "\n".join(lines)


def describe_path(path: str) -> dict:
    """文件/目录的完整描述信息。"""
    st = os.stat(path)
    is_dir = os.path.isdir(path)
    return {
        "path": os.path.abspath(path),
        "size": st.st_size,
        "size_human": human_size(st.st_size),
        "format": "目录" if is_dir else detect_file(path),
        "is_dir": is_dir,
    }
