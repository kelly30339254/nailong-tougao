"""云端数据同步：下载最新编辑数据并合并到本地库。

- 纯标准库实现（urllib），可在后台线程调用。
- 幂等合并：按 email 匹配，更新 directions/status/genres/fee_info/notes。
- 数据源地址硬编码在代码中（build_data_url()），用户不可见、不可修改。
"""
from __future__ import annotations

import json
import urllib.request

# 硬编码的数据源（用户不可见、不可修改；隐藏云端地址）
# 修改此常量即可切换数据源；重新打包 exe 后用户自动使用新地址。
_OWNER = "kelly30339254"
_REPO = "submission-data"
_BRANCH = "main"
_DATA_FILE = "editors-latest.json"

# 旧版设置键（兼容旧用户设置；新版忽略，强制使用硬编码地址）
SETTINGS_KEY = "sync_data_url"


def build_data_url() -> str:
    """构造云端数据 URL（硬编码、对用户不可见）。"""
    return (f"https://raw.githubusercontent.com/"
            f"{_OWNER}/{_REPO}/{_BRANCH}/{_DATA_FILE}")


# 旧版兼容函数（保留以免外部调用失败）
def build_default_url(owner: str = _OWNER, repo: str = _REPO) -> str:
    return build_data_url()


def fetch_json(url: str, timeout: int = 30) -> dict:
    """下载并解析 JSON，限制响应体大小以防止异常数据耗尽内存。"""
    req = urllib.request.Request(
        url, headers={
            "User-Agent": "NailongPost/1.0 (+github)",
            "Accept": "application/json",
            "Cache-Control": "no-cache",
        })
    max_bytes = 10 * 1024 * 1024
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("云端数据文件超过 10 MB，已拒绝导入")
    payload = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("云端数据格式错误：根节点必须是对象")
    return payload


def parse_payload(payload: dict) -> list[dict]:
    """将云端 payload 转为可合并的编辑条目列表。"""
    raw_items = payload.get("editors")
    if not isinstance(raw_items, list):
        raise ValueError("云端数据格式错误：缺少 editors 列表")
    items = []
    for d in raw_items:
        if not isinstance(d, dict):
            continue
        email = (d.get("email") or "").strip()
        if not email:
            continue
        directions = d.get("themeDirections") or d.get("directions") or []
        if isinstance(directions, list):
            directions = " / ".join(str(x) for x in directions)
        genres = d.get("categories", d.get("genres", ""))
        if isinstance(genres, list):
            genres = " / ".join(str(x) for x in genres)
        items.append({
            "name": d.get("name", ""),
            "platform": d.get("platform", ""),
            "email": email,
            "genres": genres,
            "directions": directions,
            "status": d.get("status", ""),
            "fee_info": d.get("feeInfo", ""),
            "notes": d.get("requirements", ""),
            "source_url": d.get("sourceUrl", ""),
        })
    return items


def sync_from_url(db, url: str) -> dict:
    """下载→解析→合并，返回统计信息。异常向上抛，由调用方处理。"""
    payload = fetch_json(url)
    items = parse_payload(payload)
    if not items:
        raise ValueError("云端数据中没有有效的编辑条目")
    result = db.sync_editors(items)
    result["version"] = payload.get("version", "")
    result["generatedAt"] = payload.get("generatedAt", "")
    result["source"] = payload.get("source", "")
    return result


def store_url(settings_store, url: str):  # noqa: ARG001 — 兼容旧接口
    """旧接口：不再保存。链接已硬编码在代码中。"""
    pass


def load_url(settings_store) -> str:  # noqa: ARG001 — 兼容旧接口
    """旧接口：始终返回硬编码地址。"""
    return build_data_url()
