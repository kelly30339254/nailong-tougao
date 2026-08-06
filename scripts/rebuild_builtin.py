"""重建内置编辑数据：从爬虫生成的 editors-latest.json 转为软件内置格式。

用法：
    python scripts/rebuild_builtin.py [editors-latest.json]
输出：
    app\\data\\builtin_editors.json （覆盖）
"""
import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_DIR.parent / "爬虫" / "editors-latest.json"
TARGET = PROJECT_DIR / "app" / "data" / "builtin_editors.json"


def main():
    parser = argparse.ArgumentParser(description="重建软件内置编辑数据")
    parser.add_argument("source", nargs="?", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()
    with args.source.open(encoding="utf-8") as f:
        payload = json.load(f)

    items = []
    for d in payload.get("editors", []):
        email = (d.get("email") or "").strip()
        if not email:
            continue
        directions = d.get("themeDirections") or []
        if isinstance(directions, list):
            directions = " / ".join(str(x) for x in directions)
        items.append({
            "name": d.get("name", ""),
            "platform": d.get("platform", ""),
            "email": email,
            "genres": d.get("categories", ""),
            "directions": directions,
            "status": d.get("status", "未核实"),
            "fee_info": d.get("feeInfo", ""),
            "source_url": "",
            "notes": (d.get("requirements") or "")[:500],
            "favorite": 0,
            "blacklisted": 1 if d.get("status") == "停止收稿" else 0,
            "created_at": d.get("updateTime", "")[:10],
        })

    with open(TARGET, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)

    with_dir = sum(1 for it in items if it["directions"])
    print(f"已重建内置编辑: {len(items)} 条，含收稿方向 {with_dir} 条")
    print(f"输出: {TARGET}")


if __name__ == "__main__":
    main()
