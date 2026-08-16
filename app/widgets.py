"""表格等小部件公共工具。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidgetItem, QWidget, QHBoxLayout, QLabel, QFrame


def mk_item(text: str, align=None) -> QTableWidgetItem:
    """带 tooltip 的 QTableWidgetItem：列宽不够时悬停可见完整文本。"""
    text = text or ""
    item = QTableWidgetItem(text)
    if text:
        item.setToolTip(text)
    if align is not None:
        item.setTextAlignment(align)
    return item


def make_dot(color: str, size: int = 8) -> QFrame:
    """彩色圆点（替代 emoji 图标）：投递=主色、回信=绿、未读=主色。"""
    dot = QFrame()
    dot.setFixedSize(size, size)
    dot.setStyleSheet(f"background: {color}; border: none; border-radius: {size // 2}px;")
    return dot


def badge_cell(text: str, kind: str, tooltip: str = "") -> QWidget:
    """状态/判定徽章单元格：QLabel#badge[kind] 样式在 style.qss 定义。"""
    wrap = QWidget()
    lay = QHBoxLayout(wrap)
    lay.setContentsMargins(4, 0, 4, 0)
    lay.setAlignment(Qt.AlignCenter)
    badge = QLabel(text)
    badge.setObjectName("badge")
    badge.setProperty("kind", kind)
    badge.setToolTip(tooltip or text)
    lay.addWidget(badge)
    return wrap
