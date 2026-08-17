"""打包前置脚本：确保 app/assets/app_icon.ico 存在。

若用户已放置 png/jpg/jpeg 格式的 app_icon，则自动转换为 ico；
若已存在 ico 则直接使用；否则跳过（PyInstaller 会使用默认图标）。
"""
from __future__ import annotations

import os
import sys


def ensure_icon() -> str | None:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assets = os.path.join(base, "app", "assets")
    os.makedirs(assets, exist_ok=True)

    ico_path = os.path.join(assets, "app_icon.ico")
    src = None
    src_mtime = 0.0
    for ext in (".png", ".jpg", ".jpeg"):
        candidate = os.path.join(assets, f"app_icon{ext}")
        if os.path.exists(candidate):
            src = candidate
            src_mtime = os.path.getmtime(candidate)
            break

    ico_exists = os.path.exists(ico_path)
    if ico_exists and (src is None or os.path.getmtime(ico_path) >= src_mtime):
        print(f"[ensure_ico] 已存在: {ico_path}")
        return ico_path

    if src is None:
        print("[ensure_ico] 未找到 app/assets/app_icon.*，跳过 ico 生成")
        return ico_path if ico_exists else None

    try:
        from PIL import Image
        img = Image.open(src)
        img = img.convert("RGBA")
        sizes = [16, 24, 32, 48, 64, 128, 256]
        img.save(ico_path, format="ICO", sizes=[(s, s) for s in sizes])
        print(f"[ensure_ico] 已转换: {src} -> {ico_path}")
        return ico_path
    except Exception as exc:
        print(f"[ensure_ico] 转换失败: {exc}", file=sys.stderr)
        return ico_path if ico_exists else None


if __name__ == "__main__":
    path = ensure_icon()
    sys.exit(0 if path else 0)  # 未找到也不阻断打包
