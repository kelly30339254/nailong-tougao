"""检测更新：下载服务器上的 version.json 并与内置版本号比对。

- version.json 放在网站静态托管根目录（部署见 server/README.md）：
  {"version": "1.1.0", "notes": "更新说明", "download_url": "https://pan.quark.cn/s/..."}
- 地址硬编码（VERSION_URL），用户不可见、不可修改。
- 发版流程：改 app/__init__.py 的 APP_VERSION → 打包 → 上传网盘 → 更新 version.json。
"""
from __future__ import annotations

from . import APP_VERSION
from .updater import fetch_json

# 版本信息地址（静态托管根目录下的 version.json）
VERSION_URL = ("https://nailong-d4g922z6h6d9ff59e-1455870789"
               ".tcloudbaseapp.com/version.json")


def _parse_version(text: str) -> tuple[int, ...]:
    """"1.2.3" -> (1, 2, 3)，非数字段按 0 处理。"""
    parts = []
    for seg in str(text).strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in seg if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(remote: str, current: str = APP_VERSION) -> bool:
    a, b = _parse_version(remote), _parse_version(current)
    n = max(len(a), len(b))
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return a > b


def check_for_update(timeout: int = 15) -> dict | None:
    """有新版返回 {"version","notes","download_url"}，否则 None。异常向上抛。"""
    payload = fetch_json(VERSION_URL, timeout=timeout)
    remote = str(payload.get("version") or "").strip()
    if not remote or not is_newer(remote):
        return None
    return {
        "version": remote,
        "notes": str(payload.get("notes") or ""),
        "download_url": str(payload.get("download_url") or ""),
        "github_url": str(payload.get("github_url") or ""),
    }
