#!/usr/bin/env bash
# 生成 Linux .deb 安装包（Debian/Ubuntu）。
# 安装后：可执行文件 /usr/bin/nailong-post，应用菜单出现“奶龙投稿助手”。
# 用法：先 pyinstaller --noconfirm --clean 奶龙投稿助手.spec，再 bash scripts/make_deb.sh
set -euo pipefail
cd "$(dirname "$0")/.."

python3 scripts/validate_release.py
VER=$(python3 -c "from app import APP_VERSION; print(APP_VERSION)")
BIN="dist/奶龙投稿助手"
[ -f "$BIN" ] || { echo "[错误] 未找到 $BIN，请先运行 PyInstaller 打包"; exit 1; }

PKG="build/deb/nailong-post"
rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" "$PKG/usr/bin" "$PKG/usr/share/applications"

install -m 755 "$BIN" "$PKG/usr/bin/nailong-post"

cat > "$PKG/DEBIAN/control" <<EOF
Package: nailong-post
Version: ${VER}
Section: utils
Priority: optional
Architecture: amd64
Depends: libegl1, libgl1, libxkbcommon0, libxcb-cursor0, libdbus-1-3, libfontconfig1
Maintainer: nailong
Description: 奶龙投稿助手 - 期刊投稿邮件批量管理工具
EOF

cat > "$PKG/usr/share/applications/nailong-post.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=奶龙投稿助手
Comment=期刊投稿邮件批量管理工具
Exec=nailong-post
Categories=Office;
Terminal=false
EOF

OUT="奶龙投稿助手-${VER}-linux-amd64.deb"
dpkg-deb --build "$PKG" "$OUT" > /dev/null
echo "[完成] 产物：$OUT"

# 便携包：单文件二进制压缩成 zip，解压即可运行
ZIP_OUT="奶龙投稿助手-${VER}-linux-amd64.zip"
rm -f "$ZIP_OUT"
(cd dist && zip "../$ZIP_OUT" "奶龙投稿助手")
echo "[完成] 产物：$ZIP_OUT"
