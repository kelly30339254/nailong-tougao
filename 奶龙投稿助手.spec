# -*- mode: python ; coding: utf-8 -*-
# Qt6 / PySide6：务必关闭 UPX，否则 Windows 上易出现
# 「DLL load failed while importing QtWidgets: 找不到指定的模块」
import sys


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('app/style.qss', 'app'), ('app/assets', 'app/assets'), ('app/data', 'app/data')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='奶龙投稿助手',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='奶龙投稿助手.app',
        icon=None,
        bundle_identifier='com.nailong.tougao',
    )
