"""随安装包发布的本地公告读取与发版校验。"""
from __future__ import annotations

import json
from pathlib import Path

from .theme import resource_path

ANNOUNCEMENTS_RELATIVE_PATH = Path("app") / "data" / "announcements.json"


def load_announcements(path: str | Path | None = None) -> list[dict[str, str]]:
    target = Path(path) if path else Path(resource_path(str(ANNOUNCEMENTS_RELATIVE_PATH)))
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    result: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        version = str(item.get("version", "")).strip()
        date = str(item.get("date", "")).strip()
        title = str(item.get("title", "")).strip()
        summary = str(item.get("summary", "")).strip()
        details = str(item.get("details", summary)).strip()
        if version and date and title and summary:
            result.append({
                "version": version, "date": date, "title": title,
                "summary": summary, "details": details,
            })
    result.sort(key=lambda item: (item["date"], item["version"]), reverse=True)
    return result


def validate_release_announcement(version: str, path: str | Path | None = None) -> None:
    announcements = load_announcements(path)
    if not any(item["version"] == str(version).strip() for item in announcements):
        raise ValueError(f"公告资源中缺少版本 {version} 的公告")
