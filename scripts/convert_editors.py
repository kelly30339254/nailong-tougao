"""一次性转换脚本：webnovel-radar 编辑数据 → app/data/builtin_editors.json。

用法：.venv/Scripts/python.exe scripts/convert_editors.py
源文件只读，不修改。
"""
import json
import os
import re
import sys
from datetime import datetime

SRC = r"C:/Users/20440/Documents/kimi/workspace/webnovel-radar/public/data/submission-editors.json"
DST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "app", "data", "builtin_editors.json")

STATUS_BLACKLIST = "停止收稿"
STATUS_UNVERIFIED = "未核实"


def first_http_url(text: str) -> str:
    """source_url 常含两个 URL（逗号/空格分隔），取第一个 http 开头的。"""
    for part in re.split(r"[,\s]+", text or ""):
        part = part.strip()
        if part.startswith("http"):
            return part
    return ""


def main():
    with open(SRC, encoding="utf-8") as f:
        data = json.load(f)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out = []
    stats = {"total": 0, "blacklisted": 0, "unverified": 0, "skipped_no_email": 0}
    for d in data:
        email = (d.get("email") or "").strip()
        if "@" not in email:
            stats["skipped_no_email"] += 1
            continue
        status = d.get("status") or ""
        blacklisted = 0
        prefix = ""
        if status == STATUS_BLACKLIST:
            blacklisted = 1
            prefix = "【已停止收稿】"
            stats["blacklisted"] += 1
        elif status == STATUS_UNVERIFIED:
            prefix = "【信息未核实】"
            stats["unverified"] += 1
        requirements = (d.get("requirements") or "")[:500]
        notes = prefix + requirements
        out.append({
            "name": (d.get("name") or "").strip() or email,
            "platform": (d.get("platform") or "").strip() or "未知平台",
            "email": email,
            "genres": "、".join(d.get("workTypes") or []),
            "fee_info": d.get("payment") or "",
            "source_url": "",   # 来源链接按用户要求清空，不内置
            "notes": notes,
            "favorite": 0,
            "blacklisted": blacklisted,
            "created_at": (d.get("收录日期") or "").strip() or now,
        })
    stats["total"] = len(out)

    os.makedirs(os.path.dirname(DST), exist_ok=True)
    with open(DST, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    print(f"源条数: {len(data)}")
    print(f"转换输出: {stats['total']}（跳过无 @ 邮箱 {stats['skipped_no_email']} 条）")
    print(f"blacklisted（停止收稿）: {stats['blacklisted']}")
    print(f"未核实前缀: {stats['unverified']}")
    assert stats["total"] == 2481, stats
    # 1048 条停止收稿中有 4 条无 @ 邮箱被跳过，实际 blacklisted 为 1044
    assert stats["blacklisted"] == 1044, stats
    assert all(e["email"] for e in out)
    assert all("@" in e["email"] for e in out)
    assert all(e["source_url"] == "" for e in out)  # 来源链接清空
    size = os.path.getsize(DST)
    print(f"写入 {DST}（{size / 1024:.0f} KB）")


if __name__ == "__main__":
    sys.exit(main())
