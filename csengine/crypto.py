"""ChaCha20 流加密 + HMAC-SHA256 认证（纯 Python，RFC 8439）。

用于 CSE-X 归档加密：PBKDF2-HMAC-SHA256 派生 64 字节（32 加密 + 32 认证），
每个数据块独立随机 96bit nonce，密文后附 16 字节截断 HMAC（encrypt-then-MAC）。
"""

import hashlib
import hmac
import os
import struct

MASK = 0xFFFFFFFF
_CONST = b"expand 32-byte k"
DEFAULT_ITERS = 120_000
KEY_LEN = 32
NONCE_LEN = 12
TAG_LEN = 16


def _rotl(v: int, c: int) -> int:
    return ((v << c) | (v >> (32 - c))) & MASK


def _quarter(x, a, b, c, d):
    x[a] = (x[a] + x[b]) & MASK
    x[d] ^= x[a]
    x[d] = _rotl(x[d], 16)
    x[c] = (x[c] + x[d]) & MASK
    x[b] ^= x[c]
    x[b] = _rotl(x[b], 12)
    x[a] = (x[a] + x[b]) & MASK
    x[d] ^= x[a]
    x[d] = _rotl(x[d], 8)
    x[c] = (x[c] + x[d]) & MASK
    x[b] ^= x[c]
    x[b] = _rotl(x[b], 7)


def _block(key_words, counter: int, nonce_words) -> bytes:
    state = [
        0x61707865, 0x3320646E, 0x79622D32, 0x6B206574,
        *key_words, counter, *nonce_words,
    ]
    work = state[:]
    for _ in range(10):  # 10 组双轮 = 20 轮
        _quarter(work, 0, 4, 8, 12)
        _quarter(work, 1, 5, 9, 13)
        _quarter(work, 2, 6, 10, 14)
        _quarter(work, 3, 7, 11, 15)
        _quarter(work, 0, 5, 10, 15)
        _quarter(work, 1, 6, 11, 12)
        _quarter(work, 2, 7, 8, 13)
        _quarter(work, 3, 4, 9, 14)
    out = [(work[i] + state[i]) & MASK for i in range(16)]
    return struct.pack("<16I", *out)


def keystream(key: bytes, nonce: bytes, length: int, counter: int = 1) -> bytes:
    key_words = struct.unpack("<8I", key)
    nonce_words = struct.unpack("<3I", nonce)
    blocks = []
    n = 0
    while n < length:
        blocks.append(_block(key_words, (counter + n // 64) & MASK, nonce_words))
        n += 64
    return b"".join(blocks)[:length]


def _xor(data: bytes, stream: bytes) -> bytes:
    """大整数异或（C 级速度），比逐字节循环快一个数量级。"""
    if not data:
        return b""
    return (int.from_bytes(data, "little") ^ int.from_bytes(stream, "little")).to_bytes(
        len(data), "little")


def derive_keys(password: str, salt: bytes, iters: int = DEFAULT_ITERS):
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters, dklen=64)
    return dk[:KEY_LEN], dk[KEY_LEN:]


def new_salt() -> bytes:
    return os.urandom(16)


def _tag(mac_key: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    return hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()[:TAG_LEN]


def encrypt_blob(plaintext: bytes, enc_key: bytes, mac_key: bytes) -> bytes:
    """返回 nonce(12) + tag(16) + ciphertext。"""
    nonce = os.urandom(NONCE_LEN)
    ct = _xor(plaintext, keystream(enc_key, nonce, len(plaintext)))
    return nonce + _tag(mac_key, nonce, ct) + ct


def decrypt_blob(blob: bytes, enc_key: bytes, mac_key: bytes) -> bytes:
    if len(blob) < NONCE_LEN + TAG_LEN:
        raise ValueError("密文长度非法")
    nonce, tag, ct = blob[:NONCE_LEN], blob[NONCE_LEN:NONCE_LEN + TAG_LEN], blob[NONCE_LEN + TAG_LEN:]
    if not hmac.compare_digest(tag, _tag(mac_key, nonce, ct)):
        raise ValueError("密码错误或数据已被篡改（认证失败）")
    return _xor(ct, keystream(enc_key, nonce, len(ct)))
