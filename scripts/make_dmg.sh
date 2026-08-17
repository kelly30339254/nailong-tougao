#!/usr/bin/env bash
# 生成 macOS .dmg 安装包（拖拽安装，内含 Applications 快捷方式）。
# 用法：先 pyinstaller --noconfirm --clean 奶龙投稿助手.spec，再 bash scripts/make_dmg.sh
set -euo pipefail
cd "$(dirname "$0")/.."

VER=$(python3 -c "from app import APP_VERSION; print(APP_VERSION)")
APP="dist/奶龙投稿助手.app"
if [ ! -d "$APP" ] && [ -d "dist/奶龙投稿助手/奶龙投稿助手.app" ]; then
  APP="dist/奶龙投稿助手/奶龙投稿助手.app"
fi
[ -d "$APP" ] || { echo "[错误] 未找到 $APP，请先运行 PyInstaller 打包"; exit 1; }

rm -rf build/dmg
mkdir -p build/dmg
cp -R "$APP" build/dmg/
ln -s /Applications build/dmg/Applications

OUT="奶龙投稿助手-${VER}-macos.dmg"
hdiutil create -volname "奶龙投稿助手" -srcfolder build/dmg -ov -format UDZO "$OUT"
echo "[完成] 产物：$OUT"

# 便携包：.app 直接压缩成 zip，解压即可用（-y 保留符号链接）
ROOT="$(pwd)"
ZIP_OUT="奶龙投稿助手-${VER}-macos.zip"
rm -f "$ZIP_OUT"
APP_DIR=$(dirname "$APP")
APP_NAME=$(basename "$APP")
(cd "$APP_DIR" && zip -r -y "$ROOT/$ZIP_OUT" "$APP_NAME")
echo "[完成] 产物：$ZIP_OUT"
