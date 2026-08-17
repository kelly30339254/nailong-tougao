"""卡密激活：机器指纹、本地激活状态、在线核销。

- 激活接口地址硬编码（ACTIVATE_URL），用户不可见、不可修改。
- 激活成功后在本机数据目录写 license.json（卡密哈希 + 机器指纹哈希）；
  每次启动本地校验 license.json 与本机指纹匹配即放行。
- 卡密一次性由服务端保证（CloudBase 云函数核销）；机器指纹绑定防止
  把整个数据目录拷贝到别的电脑白嫖。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

from .db import data_dir

# 激活接口（CloudBase 云函数 HTTP 路由；部署见 server/README.md）
ACTIVATE_URL = ("https://nailong-d4g922z6h6d9ff59e-1455870789"
                ".tcloudbaseapp.com/api/activate")

_LICENSE_FILE = "license.json"
_KEY_RE = re.compile(r"^[A-Z0-9]{4,64}$")


def _ssl_context() -> ssl.SSLContext:
    """HTTPS 校验用的 CA 证书上下文。

    PyInstaller 打包的 macOS 应用不携带系统 CA 证书，默认上下文会因
    「unable to get local issuer certificate」校验失败；用 certifi 的
    cacert.pem 兜底（certifi 需打入包内，见 requirements.txt）。
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def machine_id() -> str:
    """本机指纹（SHA-256 前 32 位）。

    Windows 优先取注册表 MachineGuid（重装系统才变）；失败回退 MAC 地址。
    """
    raw = ""
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography") as k:
                raw, _ = winreg.QueryValueEx(k, "MachineGuid")
        except OSError:
            raw = ""
    if not raw:
        import uuid
        raw = f"mac-{uuid.getnode()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def normalize_key(card_key: str) -> str:
    """去掉空格和连字符、转大写，便于用户按分组格式输入。"""
    return re.sub(r"[\s-]+", "", card_key or "").upper()


def _license_path() -> str:
    return os.path.join(data_dir(), _LICENSE_FILE)


def _key_hash(card_key: str) -> str:
    return hashlib.sha256(card_key.encode("utf-8")).hexdigest()


def is_activated() -> bool:
    """本地存在有效激活记录且机器指纹匹配本机。"""
    try:
        with open(_license_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    if not data.get("key_hash") or not data.get("machine"):
        return False
    return data["machine"] == machine_id()


def _write_license(card_key: str):
    path = _license_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"key_hash": _key_hash(card_key), "machine": machine_id()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def activate(card_key: str, url: str = ACTIVATE_URL,
             timeout: int = 20) -> tuple[bool, str]:
    """联网核销卡密。成功返回 (True, 提示)，失败返回 (False, 原因)。"""
    key = normalize_key(card_key)
    if not _KEY_RE.match(key):
        return False, "卡密格式不正确"
    body = json.dumps({"card_key": key, "machine_id": machine_id()}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "User-Agent": "NailongPost/1.0 (+activate)",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=_ssl_context()) as resp:
            raw = resp.read(64 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        # 服务器返回 4xx/5xx：读取响应体里的业务错误信息，别伪装成"网络问题"
        detail = ""
        try:
            detail = exc.read(4 * 1024).decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        extra = f"（{detail.strip()}）" if detail.strip() else ""
        return False, f"激活服务器返回错误（HTTP {exc.code}）{extra}"
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        # URLError/OSError 涵盖连接失败、超时、TLS 证书校验失败（SSLError 是 OSError 子类）
        reason = getattr(exc, "reason", exc)
        return False, f"无法连接激活服务器，请检查网络后重试（{reason}）"
    except Exception:
        return False, "无法连接激活服务器，请检查网络后重试"
    if len(raw) > 64 * 1024:
        return False, "激活服务器响应异常"
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except ValueError:
        return False, "激活服务器响应异常"
    if not isinstance(payload, dict):
        return False, "激活服务器响应异常"
    if payload.get("ok") is True:
        _write_license(key)
        return True, str(payload.get("msg") or "激活成功")
    return False, str(payload.get("msg") or "激活失败，请检查卡密是否正确")
