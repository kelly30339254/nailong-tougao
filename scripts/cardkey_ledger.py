"""卡密发放台账：本地记录每张卡密的发放状态，避免重复发放或弄混。

台账保存在 server/cardkeys-ledger.json（匹配 .gitignore 的 cardkeys-*.json，
不会被 git 提交）。注意：台账只记录「发没发给买家」，卡密是否已被激活以
CloudBase 数据库里的 used 字段为准（查询命令见 server/README.md）。

用法：
  python scripts/cardkey_ledger.py import server/cardkeys-xxx.json   # 导入新批次（幂等）
  python scripts/cardkey_ledger.py give 3 --note "买家微信xxx"         # 取 3 张库存卡密并标记已发放
  python scripts/cardkey_ledger.py mark NLKXXXX... --note "原因"       # 手动标记指定卡密（如测试用掉）
  python scripts/cardkey_ledger.py status                              # 查看库存与发放统计
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.make_cardkeys import display_key

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER_PATH = os.path.join(_ROOT, "server", "cardkeys-ledger.json")


def load_ledger() -> dict:
    if not os.path.exists(LEDGER_PATH):
        return {}
    with open(LEDGER_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_ledger(ledger: dict):
    os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
    with open(LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=1)


def cmd_import(path: str) -> int:
    ledger = load_ledger()
    # 序号决定 give 的取卡顺序：按导入批次先后 + 文件内顺序，与生成文件一致
    seq = max((v.get("seq", 0) for v in ledger.values()), default=0)
    added = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            key = json.loads(line)["key"]
            if key not in ledger:
                seq += 1
                ledger[key] = {"status": "stock", "note": "", "given_at": "",
                               "seq": seq}
                added += 1
    save_ledger(ledger)
    print(f"导入完成：新增 {added} 张，台账共 {len(ledger)} 张")
    return 0


def _stock_in_order(ledger: dict) -> list[str]:
    """库存卡密按发放顺序（导入批次 + 文件内顺序）排列。"""
    return [k for k, _ in sorted(
        ((k, v) for k, v in ledger.items() if v["status"] == "stock"),
        key=lambda kv: kv[1].get("seq", 0))]


def cmd_give(count: int, note: str) -> int:
    ledger = load_ledger()
    stock = _stock_in_order(ledger)
    if len(stock) < count:
        print(f"库存不足：只剩 {len(stock)} 张，无法发放 {count} 张", file=sys.stderr)
        return 1
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    chosen = stock[:count]
    for key in chosen:
        ledger[key] = {"status": "given", "note": note, "given_at": now}
    save_ledger(ledger)
    print(f"已发放 {count} 张（备注：{note or '无'}）：")
    for key in chosen:
        print("  " + display_key(key))
    return 0


def cmd_mark(keys: list[str], note: str) -> int:
    ledger = load_ledger()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for key in keys:
        norm = key.replace("-", "").upper()
        if norm not in ledger:
            print(f"跳过 {key}：不在台账中", file=sys.stderr)
            continue
        ledger[norm] = {"status": "given", "note": note, "given_at": now}
        print(f"已标记 {display_key(norm)}（{note}）")
    save_ledger(ledger)
    return 0


def cmd_status() -> int:
    ledger = load_ledger()
    stock = [k for k, v in ledger.items() if v["status"] == "stock"]
    given = [(k, v) for k, v in ledger.items() if v["status"] == "given"]
    print(f"台账共 {len(ledger)} 张：库存 {len(stock)} 张，已发放 {len(given)} 张")
    if given:
        print("最近发放：")
        for key, v in sorted(given, key=lambda kv: kv[1]["given_at"])[-5:]:
            print(f"  {display_key(key)}  {v['given_at']}  {v['note']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="卡密发放台账")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("import", help="导入 make_cardkeys.py 生成的卡密文件")
    p.add_argument("file")
    p = sub.add_parser("give", help="取 N 张库存卡密并标记为已发放")
    p.add_argument("count", type=int)
    p.add_argument("--note", default="", help="买家备注（微信号/闲鱼号等）")
    p = sub.add_parser("mark", help="手动把指定卡密标记为已发放/已用")
    p.add_argument("keys", nargs="+")
    p.add_argument("--note", default="", help="标记原因")
    sub.add_parser("status", help="查看库存与发放统计")
    args = parser.parse_args()

    if args.cmd == "import":
        return cmd_import(args.file)
    if args.cmd == "give":
        return cmd_give(args.count, args.note)
    if args.cmd == "mark":
        return cmd_mark(args.keys, args.note)
    return cmd_status()


if __name__ == "__main__":
    sys.exit(main())
