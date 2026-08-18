"""重建加密的内置编辑包。

用法：
    python scripts/rebuild_builtin.py --from-db
    python scripts/rebuild_builtin.py [editors-latest.json]
输出：
    app\\data\\builtin_editors.dat
    （不再生成明文 JSON）
"""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.builtin_pack import (  # noqa: E402
    PACK_FILENAME, compute_pack_version, save_builtin_editors,
)

PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_DIR.parent / "爬虫" / "editors-latest.json"
TARGET = PROJECT_DIR / "app" / "data" / PACK_FILENAME
LEGACY_JSON = PROJECT_DIR / "app" / "data" / "builtin_editors.json"


def _normalize(item: dict) -> dict:
    email = (item.get("email") or "").strip()
    platform = (item.get("platform") or "").strip() or "未知平台"
    return {
        "name": (item.get("name") or "").strip() or email,
        "platform": platform,
        "email": email,
        "genres": item.get("genres") or "",
        "directions": item.get("directions") or "",
        "status": item.get("status") or "未核实",
        "fee_info": item.get("fee_info") or "",
        "source_url": "",
        "notes": (item.get("notes") or "")[:500],
        "favorite": 0,
        "blacklisted": int(bool(item.get("blacklisted"))),
        "created_at": (item.get("created_at") or "")[:19],
    }


def from_crawler(source: Path) -> list[dict]:
    with source.open(encoding="utf-8") as f:
        payload = json.load(f)
    raw = payload.get("editors", payload if isinstance(payload, list) else [])
    items = []
    seen = set()
    for d in raw:
        email = (d.get("email") or "").strip()
        if not email:
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        directions = d.get("themeDirections") or d.get("directions") or []
        if isinstance(directions, list):
            directions = " / ".join(str(x) for x in directions)
        genres = d.get("categories", d.get("genres", ""))
        if isinstance(genres, list):
            genres = " / ".join(str(x) for x in genres)
        items.append(_normalize({
            "name": d.get("name", ""),
            "platform": d.get("platform", ""),
            "email": email,
            "genres": genres,
            "directions": directions,
            "status": d.get("status", "未核实"),
            "fee_info": d.get("feeInfo") or d.get("fee_info") or "",
            "notes": d.get("requirements") or d.get("notes") or "",
            "blacklisted": 1 if d.get("status") == "停止收稿" or d.get("blacklisted") else 0,
            "created_at": (d.get("updateTime") or d.get("created_at") or "")[:10],
        }))
    return items


def from_sqlite(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT name,platform,email,genres,directions,status,fee_info,"
        "notes,blacklisted,created_at FROM editors WHERE email != '' "
        "ORDER BY id"
    ).fetchall()
    conn.close()
    items = []
    seen = set()
    for r in rows:
        email = (r["email"] or "").strip()
        key = email.lower()
        if not email or key in seen:
            continue
        seen.add(key)
        items.append(_normalize(dict(r)))
    return items


def default_db_path() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share")
    return Path(base) / "NailongPost" / "nailong.db"


def main():
    parser = argparse.ArgumentParser(description="重建加密的软件内置编辑包")
    parser.add_argument("source", nargs="?", type=Path, default=None)
    parser.add_argument("--from-db", action="store_true", help="从本机 nailong.db 导出")
    parser.add_argument("--db", type=Path, default=None, help="指定 sqlite 路径")
    args = parser.parse_args()

    if args.from_db or args.db:
        db_path = args.db or default_db_path()
        if not db_path.exists():
            raise SystemExit(f"数据库不存在: {db_path}")
        items = from_sqlite(db_path)
        src_desc = str(db_path)
    else:
        source = args.source or DEFAULT_SOURCE
        if not source.exists():
            raise SystemExit(f"源文件不存在: {source}")
        items = from_crawler(source)
        src_desc = str(source)

    save_builtin_editors(str(TARGET), items)
    if LEGACY_JSON.exists():
        LEGACY_JSON.unlink()

    with_dir = sum(1 for it in items if (it["directions"] or "").strip())
    print(f"已重建加密内置编辑包: {len(items)} 条，含收稿方向 {with_dir} 条")
    print(f"版本: {compute_pack_version(items)}")
    print(f"来源: {src_desc}")
    print(f"输出: {TARGET}")


if __name__ == "__main__":
    main()
