"""SVG 图标工具：内置线性图标 + 指定颜色渲染 QIcon（替代 emoji，杜绝豆腐块）。"""
from __future__ import annotations

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
