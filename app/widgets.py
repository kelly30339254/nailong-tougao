"""表格等小部件公共工具。"""
from __future__ import annotations

import csv
import json
import logging

from PySide6.QtCore import Qt, Signal, QObject, QEvent
from PySide6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QWidget, QHBoxLayout, QLabel, QFrame,
    QDialog, QVBoxLayout, QProgressBar, QPushButton, QFileDialog, QMessageBox,
    QAbstractItemView, QHeaderView, QComboBox, QLineEdit, QSpinBox,
    QDoubleSpinBox, QAbstractScrollArea,
)

_log = logging.getLogger(__name__)
PAGE_SIZES = (50, 100, 200)


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


class ProgressDialog(QDialog):
    """通用进度框：第 i/N 项 + 取消。"""

    def __init__(self, title: str = "请稍候", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)
        self._cancelled = False
        layout = QVBoxLayout(self)
        self.label = QLabel("准备中…")
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        layout.addWidget(self.bar)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self._on_cancel)
        layout.addWidget(cancel, 0, Qt.AlignRight)

    def _on_cancel(self):
        self._cancelled = True
        self.label.setText("正在取消…")

    def is_cancelled(self) -> bool:
        return self._cancelled

    def set_progress(self, current: int, total: int, text: str = ""):
        total = max(total, 1)
        self.bar.setMaximum(total)
        self.bar.setValue(min(current, total))
        self.label.setText(text or f"正在处理第 {current}/{total} 项")


def export_csv(parent, headers: list[str], rows: list[list], default_name: str) -> bool:
    path, _ = QFileDialog.getSaveFileName(
        parent, "导出 CSV", default_name, "CSV 文件 (*.csv)")
    if not path:
        return False
    try:
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)
    except OSError as exc:
        QMessageBox.warning(parent, "导出失败", str(exc))
        return False
    QMessageBox.information(parent, "已导出", path)
    return True


def _sort_value(value):
    if value is None:
        return ("", 0.0, "")
    if isinstance(value, (int, float)):
        return ("0", float(value), "")
    text = str(value).strip()
    try:
        return ("0", float(text.replace(",", "")), "")
    except ValueError:
        return ("1", 0.0, text.casefold())


class PageBar(QWidget):
    """页脚：上一页 / 下一页 / 跳页 / 每页 50·100·200。"""

    changed = Signal()

    def __init__(self, parent=None, page_sizes=PAGE_SIZES, default_size: int = 50):
        super().__init__(parent)
        self.setObjectName("pageBar")
        self._page = 1
        self._page_size = default_size if default_size in page_sizes else page_sizes[0]
        self._total = 0
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(8)
        layout.addStretch()
        self.prev_btn = QPushButton("上一页")
        self.prev_btn.setObjectName("iconBtn")
        self.prev_btn.clicked.connect(self._prev)
        layout.addWidget(self.prev_btn)
        self.info = QLabel("共 0 条")
        self.info.setObjectName("pageBarText")
        layout.addWidget(self.info)
        self.next_btn = QPushButton("下一页")
        self.next_btn.setObjectName("iconBtn")
        self.next_btn.clicked.connect(self._next)
        layout.addWidget(self.next_btn)
        layout.addWidget(QLabel("每页"))
        self.size_combo = QComboBox()
        for n in page_sizes:
            self.size_combo.addItem(str(n), n)
        idx = self.size_combo.findData(self._page_size)
        if idx >= 0:
            self.size_combo.setCurrentIndex(idx)
        self.size_combo.currentIndexChanged.connect(self._on_size)
        layout.addWidget(self.size_combo)
        self.jump_edit = QLineEdit()
        self.jump_edit.setPlaceholderText("页码")
        self.jump_edit.setFixedWidth(56)
        self.jump_edit.returnPressed.connect(self._jump)
        layout.addWidget(self.jump_edit)
        jump_btn = QPushButton("跳转")
        jump_btn.setObjectName("iconBtn")
        jump_btn.clicked.connect(self._jump)
        layout.addWidget(jump_btn)
        layout.addStretch()
        self._refresh()

    @property
    def page(self) -> int:
        return self._page

    @property
    def page_size(self) -> int:
        return self._page_size

    @property
    def offset(self) -> int:
        return (self._page - 1) * self._page_size

    @property
    def total(self) -> int:
        return self._total

    @property
    def pages(self) -> int:
        if self._total <= 0:
            return 1
        return max(1, (self._total + self._page_size - 1) // self._page_size)

    def set_total(self, total: int):
        self._total = max(0, int(total or 0))
        self._page = min(max(1, self._page), self.pages)
        self._refresh()

    def reset_page(self):
        self._page = 1
        self._refresh()

    def _refresh(self):
        if self._total == 0:
            self.info.setText("共 0 条")
        else:
            self.info.setText(f"第 {self._page}/{self.pages} 页 · 共 {self._total} 条")
        self.prev_btn.setEnabled(self._page > 1)
        self.next_btn.setEnabled(self._page < self.pages)

    def _prev(self):
        if self._page > 1:
            self._page -= 1
            self._refresh()
            self.changed.emit()

    def _next(self):
        if self._page < self.pages:
            self._page += 1
            self._refresh()
            self.changed.emit()

    def _on_size(self):
        size = self.size_combo.currentData()
        if not size or int(size) == self._page_size:
            return
        self._page_size = int(size)
        self._page = 1
        self._refresh()
        self.changed.emit()

    def _jump(self):
        text = self.jump_edit.text().strip()
        try:
            page = int(text)
        except ValueError:
            return
        page = min(max(1, page), self.pages)
        self.jump_edit.clear()
        if page != self._page:
            self._page = page
            self._refresh()
            self.changed.emit()


class PagedTable(QWidget):
    """分页表格：页脚 + 表头点击排序 + 多选。

    数据源二选一：
    - set_loader(fn)：fn(offset, limit, order_by, desc) -> (total, items)
    - set_items(items)：内存分页，order_by 按对象属性排序
    行填充：set_binder(fn)，fn(table, row, item)
    """

    selection_changed = Signal()
    page_changed = Signal()

    def __init__(self, headers: list[str], *,
                 sort_keys: list | None = None,
                 action_cols: set[int] | None = None,
                 empty_text: str = "暂无数据",
                 multi_select: bool = True,
                 store=None, width_key: str = "",
                 parent=None):
        super().__init__(parent)
        self.headers = list(headers)
        self.sort_keys = list(sort_keys) if sort_keys is not None else [None] * len(headers)
        while len(self.sort_keys) < len(headers):
            self.sort_keys.append(None)
        self.action_cols = set(action_cols or set())
        self.empty_text = empty_text
        self.store = store
        self.width_key = width_key
        self.order_by = "id"
        self.desc = True
        self._loader = None
        self._binder = None
        self._all_items: list = []
        self._items: list = []
        self._saving_widths = False

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)

        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        if multi_select:
            self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        else:
            self.table.setSelectionMode(QAbstractItemView.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self._on_header)
        header.sectionResized.connect(self._on_section_resized)
        self.table.itemSelectionChanged.connect(self.selection_changed.emit)
        box.addWidget(self.table, 1)

        self.bar = PageBar(self)
        self.bar.changed.connect(self._on_page)
        box.addWidget(self.bar)
        self._restore_widths()

    def set_loader(self, fn):
        self._loader = fn

    def set_binder(self, fn):
        self._binder = fn

    def reload(self, reset_page: bool = False):
        if reset_page:
            self.bar.reset_page()
        if self._loader is not None:
            total, items = self._loader(
                self.bar.offset, self.bar.page_size, self.order_by, self.desc)
            self._items = list(items or [])
            self.bar.set_total(int(total or 0))
            self._render()
            return
        self._apply_local()

    def set_items(self, items: list, *, reset_page: bool = True):
        self._loader = None
        self._all_items = list(items or [])
        if reset_page:
            self.bar.reset_page()
        self._apply_local()

    @property
    def page_items(self) -> list:
        return list(self._items)

    def selected_items(self) -> list:
        if not self._items:
            return []
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        return [self._items[r] for r in rows if 0 <= r < len(self._items)]

    def selected_ids(self, attr: str = "id") -> list:
        ids = []
        for obj in self.selected_items():
            value = obj.get(attr) if isinstance(obj, dict) else getattr(obj, attr, None)
            if value is not None:
                ids.append(value)
        return ids

    def _apply_local(self):
        items = list(self._all_items)
        key = self.order_by
        if key and key != "id":
            def value_of(obj):
                if isinstance(obj, dict):
                    return obj.get(key)
                return getattr(obj, key, None)
            items.sort(key=lambda obj: _sort_value(value_of(obj)), reverse=self.desc)
        elif key == "id":
            items.sort(key=lambda obj: _sort_value(
                obj.get("id") if isinstance(obj, dict) else getattr(obj, "id", 0)),
                reverse=self.desc)
        self.bar.set_total(len(items))
        start = self.bar.offset
        self._items = items[start:start + self.bar.page_size]
        self._render()

    def _render(self):
        table = self.table
        table.clearSpans()
        table.setRowCount(0)
        if not self._items:
            table.setRowCount(1)
            item = QTableWidgetItem(self.empty_text)
            item.setTextAlignment(Qt.AlignCenter)
            item.setForeground(Qt.gray)
            table.setItem(0, 0, item)
            if table.columnCount() > 1:
                table.setSpan(0, 0, 1, table.columnCount())
            self.page_changed.emit()
            return
        table.setRowCount(len(self._items))
        if self._binder:
            for row, obj in enumerate(self._items):
                self._binder(table, row, obj)
        self.page_changed.emit()

    def _on_page(self):
        if self._loader is not None:
            self.reload(reset_page=False)
        else:
            self._apply_local()

    def _on_header(self, col: int):
        if col in self.action_cols:
            return
        if col < 0 or col >= len(self.sort_keys) or not self.sort_keys[col]:
            return
        key = self.sort_keys[col]
        if self.order_by == key:
            self.desc = not self.desc
        else:
            self.order_by = key
            self.desc = True
        self.table.horizontalHeader().setSortIndicator(
            col, Qt.DescendingOrder if self.desc else Qt.AscendingOrder)
        self.bar.reset_page()
        if self._loader is not None:
            self.reload(reset_page=False)
        else:
            self._apply_local()

    def _restore_widths(self):
        if not self.store or not self.width_key:
            return
        raw = self.store.get(self.width_key, "")
        if not raw:
            return
        try:
            widths = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(widths, list):
            return
        header = self.table.horizontalHeader()
        self._saving_widths = True
        try:
            for i, width in enumerate(widths):
                if i in self.action_cols or i >= self.table.columnCount():
                    continue
                if isinstance(width, int) and width >= 40:
                    header.setSectionResizeMode(i, QHeaderView.Interactive)
                    self.table.setColumnWidth(i, width)
        finally:
            self._saving_widths = False

    def _on_section_resized(self, *_args):
        if self._saving_widths or not self.store or not self.width_key:
            return
        widths = [self.table.columnWidth(i) for i in range(self.table.columnCount())]
        try:
            self.store.set(self.width_key, json.dumps(widths))
        except Exception:
            _log.debug("保存列宽失败", exc_info=True)


class WheelBlocker(QObject):
    """全局滚轮过滤器：拦截下拉框/数值框的鼠标滚轮，避免误操作。

    悬停在 QComboBox / QSpinBox / QDoubleSpinBox 上滚动滚轮时，
    不再切换选项或增减数值，而是把滚轮手势转发给最近的可滚动容器
    （QScrollArea / 表格等）的垂直滚动条，实现「滚轮只控制页面上下滚动」。
    其余控件类型保持原有滚轮行为。
    """

    _WHEEL_TYPES = (QComboBox, QSpinBox, QDoubleSpinBox)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Wheel and isinstance(obj, self._WHEEL_TYPES):
            scroll = self._nearest_scrollable(obj)
            if scroll is not None:
                vbar = scroll.verticalScrollBar()
                if vbar is not None and vbar.maximum() > vbar.minimum():
                    vbar.event(event)
            return True  # 消费原事件，阻止控件自行切换/增减
        return super().eventFilter(obj, event)

    @staticmethod
    def _nearest_scrollable(widget):
        parent = widget.parent()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                return parent
            parent = parent.parent()
        return None
