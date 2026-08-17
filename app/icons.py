"""SVG 图标工具：内置线性图标 + 指定颜色渲染 QIcon（替代 emoji，杜绝豆腐块）。"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt, QByteArray, QRectF
from PySide6.QtGui import QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer

# 24x24 线性图标（stroke 风格，仿 feather icons）
_PATHS = {
    "dashboard": ('<rect x="3" y="3" width="7" height="9" rx="1.5"/>'
                  '<rect x="14" y="3" width="7" height="5" rx="1.5"/>'
                  '<rect x="14" y="12" width="7" height="9" rx="1.5"/>'
                  '<rect x="3" y="16" width="7" height="5" rx="1.5"/>'),
    "submit": ('<path d="M22 2 11 13"/>'
               '<path d="M22 2 15 22l-4-9-9-4z"/>'),
    "records": ('<path d="M9 6h11M9 12h11M9 18h11"/>'
                '<path d="M4 6h.01M4 12h.01M4 18h.01"/>'),
    "replies": ('<rect x="3" y="5" width="18" height="14" rx="2"/>'
                '<path d="m3 8 9 6 9-6"/>'),
    "manuscripts": ('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
                    '<path d="M14 2v6h6"/>'
                    '<path d="M8 13h8M8 17h5"/>'),
    "editors": ('<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>'
                '<path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>'
                '<path d="M9 7h7M9 11h5"/>'),
    "settings": ('<circle cx="12" cy="12" r="3"/>'
                 '<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83'
                 'l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0'
                 'v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1'
                 '-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0'
                 ' 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0'
                 ' 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33 1.65 1.65 0 0 0 1-1.51V3a2 2 0'
                 ' 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0'
                 ' 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82 1.65 1.65 0 0 0 1.51 1H21a2'
                 ' 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>'),
    "star": ('<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77'
             ' 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>'),
    "sales": ('<circle cx="12" cy="12" r="9"/>'
              '<path d="m8.5 7.5 3.5 4.5 3.5-4.5"/>'
              '<path d="M12 12v5"/>'
              '<path d="M9 14h6M9 16.5h6"/>'),
    "stats": ('<path d="M4 19V9M10 19V5M16 19v-7M22 19V8"/>'),
}


def make_icon(name: str, color: str, size: int = 16, filled: bool = False) -> QIcon:
    """把内置 SVG 图标以指定颜色渲染成 QIcon（2x 渲染保证清晰）。

    filled=True 时用实心填充（如收藏星标），否则线性描边。
    """
    fill = color if filled else "none"
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{fill}" '
           f'stroke="{color}" stroke-width="2" stroke-linecap="round" '
           f'stroke-linejoin="round">{_PATHS[name]}</svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter, QRectF(0, 0, size * 2, size * 2))
    painter.end()
    pixmap.setDevicePixelRatio(2.0)
    return QIcon(pixmap)


# 应用图标文件名约定（按优先级尝试）
_APP_ICON_NAMES = ["app_icon.ico", "app_icon.png", "app_icon.jpg", "app_icon.jpeg"]

# Windows 任务栏/跳转列表身份。必须与 installer.iss 的 AppUserModelID 一致，
# 且必须在创建 QApplication 之前调用 apply_windows_app_id()。
APP_USER_MODEL_ID = "com.nailong.tougao"

_ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
_cached_app_icon: QIcon | None = None


def _assets_dir() -> str:
    from .theme import resource_path
    return resource_path(os.path.join("app", "assets"))


def _find_icon_file() -> str | None:
    assets = _assets_dir()
    for name in _APP_ICON_NAMES:
        path = os.path.join(assets, name)
        if os.path.exists(path):
            return path
    return None


def app_icon() -> QIcon:
    """加载应用图标：优先使用 app/assets/app_icon.*，否则回退到内置 SVG。

    显式注册多尺寸，避免 Windows 任务栏/标题栏只抽到 256px 再糊成小图。
    """
    global _cached_app_icon
    if _cached_app_icon is not None and not _cached_app_icon.isNull():
        return _cached_app_icon

    found = _find_icon_file()
    if found:
        file_icon = QIcon(found)
        icon = QIcon()
        for size in _ICON_SIZES:
            pix = file_icon.pixmap(size, size)
            if not pix.isNull():
                icon.addPixmap(pix)
        if icon.isNull():
            icon = file_icon
    else:
        icon = make_icon("dashboard", "#D6336C", 64, filled=True)

    _cached_app_icon = icon
    return icon


def apply_windows_app_id() -> None:
    """告诉 Windows 本进程的应用身份，任务栏才不会沿用 python.exe / 旧缓存图标。

    必须在创建 QApplication 之前调用。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def refresh_shell_icons() -> None:
    """通知资源管理器图标已变，并重写桌面/开始菜单快捷方式的图标路径。

    用带版本号的 ico 文件名打破 Explorer 缓存，避免覆盖安装后仍显示旧图标。
    测试 / 无界面环境直接跳过。
    """
    if sys.platform != "win32":
        return
    if os.environ.get("NAILONG_SMOKE") or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return
    if os.environ.get("NAILONG_DATA_DIR"):
        return
    try:
        _refresh_shell_icons_impl()
    except Exception:
        pass


def _refresh_shell_icons_impl() -> None:
    import ctypes

    icon_path = _install_versioned_icon()
    exe_path = sys.executable if getattr(sys, "frozen", False) else None
    target_icon = icon_path or exe_path
    if target_icon:
        _retarget_shortcuts(target_icon, exe_path)

    shell32 = ctypes.windll.shell32
    shell32.SHChangeNotify.argtypes = [
        ctypes.c_ulong, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    SHCNE_ASSOCCHANGED = 0x08000000
    SHCNE_UPDATEITEM = 0x00002000
    SHCNF_IDLIST = 0x0000
    SHCNF_PATHW = 0x0005
    SHCNF_FLUSHNOWAIT = 0x2000
    shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
    for path in filter(None, (exe_path, icon_path)):
        if os.path.exists(path):
            shell32.SHChangeNotify(
                SHCNE_UPDATEITEM, SHCNF_PATHW | SHCNF_FLUSHNOWAIT,
                ctypes.c_wchar_p(path), None)


def _install_versioned_icon() -> str | None:
    """把当前 ico 拷到数据目录，文件名带版本号，强制 Explorer 重新读图。"""
    src = _find_icon_file()
    if not src or not src.lower().endswith(".ico"):
        return None
    from . import APP_VERSION
    from .db import data_dir
    dest_dir = data_dir()
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"app_icon_{APP_VERSION}.ico")
    if not os.path.exists(dest) or os.path.getsize(dest) != os.path.getsize(src):
        import shutil
        shutil.copy2(src, dest)
    # 清掉其他版本的副本，避免数据目录堆积
    prefix = "app_icon_"
    for name in os.listdir(dest_dir):
        if name.startswith(prefix) and name.endswith(".ico") and name != os.path.basename(dest):
            try:
                os.remove(os.path.join(dest_dir, name))
            except OSError:
                pass
    return dest


def _csidl_path(csidl: int) -> str:
    import ctypes
    buf = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0:
        return buf.value
    return ""


def _shortcut_dirs() -> list[str]:
    # 0=桌面 25=公共桌面 2=开始菜单程序 23=公共开始菜单程序
    dirs = []
    for csidl in (0, 25, 2, 23):
        path = _csidl_path(csidl)
        if path and os.path.isdir(path):
            dirs.append(path)
            nested = os.path.join(path, "奶龙投稿助手")
            if os.path.isdir(nested):
                dirs.append(nested)
    return dirs


def _retarget_shortcuts(icon_path: str, exe_path: str | None) -> None:
    names = {"奶龙投稿助手.lnk"}
    for folder in _shortcut_dirs():
        try:
            entries = os.listdir(folder)
        except OSError:
            continue
        for name in entries:
            if name.lower() != "奶龙投稿助手.lnk" and name not in names:
                continue
            lnk = os.path.join(folder, name)
            _set_lnk_icon(lnk, icon_path, exe_path)


def _set_lnk_icon(lnk_path: str, icon_path: str, exe_path: str | None) -> None:
    """改写 .lnk 的 IconLocation（必要时校正 Target），打破图标缓存。"""
    import subprocess

    def _ps_quote(value: str) -> str:
        return value.replace("'", "''")

    lines = [
        f"$s = New-Object -ComObject WScript.Shell",
        f"$l = $s.CreateShortcut('{_ps_quote(lnk_path)}')",
        f"$l.IconLocation = '{_ps_quote(icon_path)},0'",
    ]
    if exe_path and os.path.isfile(exe_path):
        lines.append(f"$l.TargetPath = '{_ps_quote(exe_path)}'")
    lines.append("$l.Save()")
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
         "-Command", ";".join(lines)],
        timeout=15,
        capture_output=True,
        creationflags=flags,
    )
