"""批量生成卡密：输出 CloudBase 云数据库可导入的 JSON 文件。

用法：
    python scripts/make_cardkeys.py 100            # 生成 100 个卡密
    python scripts/make_cardkeys.py 100 -o keys.json

生成的 JSON 每行一条记录（CloudBase 控制台「数据库 → cardkeys 集合 → 导入」
可直接识别的 JSON Lines 格式），字段：key / used / machine_id / activated_at / bound_user。
key 为规范化形式（如 NLKAAAABBBBCCCC，无连字符）；发给用户时显示为
NLK-AAAA-BBBB-CCCC，客户端和云函数都会先去掉连字符再比对。
字母表去掉了 0/O/1/I/L 等易混字符。
v1.3.0 起卡密绑定到账号（bound_user=用户 uid）而非机器，machine_id 仅作旧数据兼容。
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import datetime

# 去掉易混淆字符 0/O/1/I/L
_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def make_key() -> str:
    """返回规范化卡密（无连字符，入库用）。"""
    groups = ["".join(secrets.choice(_ALPHABET) for _ in range(4))
              for _ in range(3)]
    return "NLK" + "".join(groups)


def display_key(key: str) -> str:
    """展示用格式：NLK-XXXX-XXXX-XXXX。客户端激活时会去掉连字符再比对。"""
    return f"{key[:3]}-{key[3:7]}-{key[7:11]}-{key[11:]}"


def main() -> int:
    parser = argparse.ArgumentParser(description="批量生成卡密")
    parser.add_argument("count", type=int, help="生成数量，如 100")
    parser.add_argument("-o", "--output", default="", help="输出文件路径")
    args = parser.parse_args()

    if args.count <= 0:
        print("数量必须大于 0", file=sys.stderr)
        return 1
    if args.count > 100000:
        print("单次最多生成 100000 个", file=sys.stderr)
        return 1

    keys: set[str] = set()
    while len(keys) < args.count:
        keys.add(make_key())

    output = args.output or f"cardkeys-{datetime.now():%Y%m%d-%H%M%S}.json"
    with open(output, "w", encoding="utf-8") as f:
        for key in sorted(keys):
            f.write(json.dumps(
                {"key": key, "used": False, "machine_id": "",
                 "activated_at": "", "bound_user": ""},
                ensure_ascii=False) + "\n")

    sample = [display_key(k) for k in sorted(keys)[:5]]
    print(f"已生成 {len(keys)} 个卡密 → {output}")
    print("示例（发给用户时用此带连字符格式）：" + "、".join(sample))
    print("下一步：到 CloudBase 控制台 → 数据库 → cardkeys 集合 → 导入此文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
