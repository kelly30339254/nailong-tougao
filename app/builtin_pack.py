"""内置编辑数据包：压缩加密后随软件分发，仅本程序解密使用。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import zlib

PACK_FILENAME = "builtin_editors.dat"
_MAGIC = b"NLBE1"
_NONCE_LEN = 16
_TAG_LEN = 32
_FIELD_PREFIX = "NLB1."
_FIELD_NONCE_LEN = 8
_FIELD_TAG_LEN = 16


def _master_material() -> bytes:
    # 分段拼接，避免源码里出现完整口令明文
    a = bytes((0x6E, 0x61, 0x69, 0x6C, 0x6F, 0x6E, 0x67))
    b = b"builtin.editors"
    c = bytes((0x70, 0x72, 0x6F, 0x74, 0x65, 0x63, 0x74, 0x2E, 0x76, 0x31))
    d = hashlib.sha256(b"tougao168/submission-pack").digest()
    return b"|".join((a, b, c, d))


def _file_key() -> bytes:
    return hashlib.sha256(b"file:" + _master_material()).digest()


def _field_key() -> bytes:
    return hashlib.sha256(b"field:" + _master_material()).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(
            key, nonce + counter.to_bytes(8, "big"), hashlib.sha256
        ).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def _xor(data: bytes, stream: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, stream))


def encrypt_bytes(plain: bytes) -> bytes:
    key = _file_key()
    nonce = os.urandom(_NONCE_LEN)
    ct = _xor(plain, _keystream(key, nonce, len(plain)))
    tag = hmac.new(key, nonce + ct, hashlib.sha256).digest()
    return _MAGIC + nonce + tag + ct


def decrypt_bytes(blob: bytes) -> bytes:
    if not blob.startswith(_MAGIC) or len(blob) < 5 + _NONCE_LEN + _TAG_LEN:
        raise ValueError("内置编辑包格式无效")
    nonce = blob[5:5 + _NONCE_LEN]
    tag = blob[5 + _NONCE_LEN:5 + _NONCE_LEN + _TAG_LEN]
    ct = blob[5 + _NONCE_LEN + _TAG_LEN:]
    key = _file_key()
    expect = hmac.new(key, nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expect):
        raise ValueError("内置编辑包已损坏或被篡改")
    return _xor(ct, _keystream(key, nonce, len(ct)))


def email_key(email: str) -> str:
    return hashlib.sha256((email or "").strip().lower().encode("utf-8")).hexdigest()


def compute_pack_version(items: list[dict]) -> str:
    emails = sorted((d.get("email") or "").strip().lower() for d in items)
    digest = hashlib.sha256("\n".join(emails).encode("utf-8")).hexdigest()[:12]
    return f"{len(items)}-{digest}"


def is_protected(value: str) -> bool:
    return bool(value) and value.startswith(_FIELD_PREFIX)


def protect_text(value: str) -> str:
    text = value or ""
    if not text or is_protected(text):
        return text
    raw = text.encode("utf-8")
    key = _field_key()
    nonce = os.urandom(_FIELD_NONCE_LEN)
    ct = _xor(raw, _keystream(key, nonce, len(raw)))
    tag = hmac.new(key, nonce + ct, hashlib.sha256).digest()[:_FIELD_TAG_LEN]
    blob = nonce + ct + tag
    return _FIELD_PREFIX + base64.urlsafe_b64encode(blob).decode("ascii")


def reveal_text(value: str) -> str:
    text = value or ""
    if not text or not is_protected(text):
        return text
    try:
        blob = base64.urlsafe_b64decode(text[len(_FIELD_PREFIX):].encode("ascii"))
        if len(blob) < _FIELD_NONCE_LEN + _FIELD_TAG_LEN:
            return ""
        nonce = blob[:_FIELD_NONCE_LEN]
        tag = blob[-_FIELD_TAG_LEN:]
        ct = blob[_FIELD_NONCE_LEN:-_FIELD_TAG_LEN]
        key = _field_key()
        expect = hmac.new(key, nonce + ct, hashlib.sha256).digest()[:_FIELD_TAG_LEN]
        if not hmac.compare_digest(tag, expect):
            return ""
        return _xor(ct, _keystream(key, nonce, len(ct))).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return ""


def load_builtin_editors(path: str) -> list[dict]:
    with open(path, "rb") as f:
        blob = f.read()
    payload = json.loads(zlib.decompress(decrypt_bytes(blob)).decode("utf-8"))
    if isinstance(payload, dict):
        items = payload.get("editors", [])
    else:
        items = payload
    if not isinstance(items, list):
        raise ValueError("内置编辑包内容无效")
    return [d for d in items if isinstance(d, dict)]


def save_builtin_editors(path: str, items: list[dict]) -> None:
    raw = json.dumps(items, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    blob = encrypt_bytes(zlib.compress(raw, 9))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(blob)


def default_pack_path() -> str:
    from .theme import resource_path
    return resource_path(os.path.join("app", "data", PACK_FILENAME))
