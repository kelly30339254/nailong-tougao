# -*- mode: python ; coding: utf-8 -*-
# Qt6 / PySide6：务必关闭 UPX，否则 Windows 上易出现
# 「DLL load failed while importing QtWidgets: 找不到指定的模块」
import os
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

ico_path = 'app/assets/app_icon.ico'
icns_path = 'app/assets/app_icon.icns'

if sys.platform == 'darwin':
    if not os.path.exists(icns_path) and os.path.exists(ico_path):
        try:
            from PIL import Image
            Image.open(ico_path).save(icns_path)
        except Exception:
            icns_path = ''
    mac_icon = icns_path if icns_path and os.path.exists(icns_path) else None
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='奶龙投稿助手',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=mac_icon,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name='奶龙投稿助手',
    )
    app = BUNDLE(
        coll,
        name='奶龙投稿助手.app',
        icon=mac_icon,
        bundle_identifier='com.nailong.tougao',
    )
else:
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
        icon=ico_path if os.path.exists(ico_path) else None,
    )
