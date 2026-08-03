"""稿费记录页：售出登记（平台/编辑/金额/打款月份）+ 列表 + 统计。"""
from __future__ import annotations

import re
from datetime import date

from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QTableWidget, QDialog, QFormLayout, QMessageBox,
    QAbstractItemView, QHeaderView, QPlainTextEdit, QDateEdit,
)

from ..models import Sale
from ..widgets import mk_item

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


class SaleDialog(QDialog):
    """新增/编辑售出记录对话框。"""

    def __init__(self, db, parent=None, sale: Sale | None = None,
                 preselect_manuscript_id: int | None = None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("编辑售出记录" if sale and sale.id else "新增售出记录")
        self.setMinimumWidth(440)
        self.sale = sale or Sale()
        self._manuscripts = db.list_manuscripts()

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(8)

        self.ms_combo = QComboBox()
        for m in self._manuscripts:
            self.ms_combo.addItem(f"{m.title} ({m.word_count}字)", m.id)
        if not self._manuscripts:
            self.ms_combo.addItem("（还没有文稿，请先到文稿库新建）", None)
        preselect = preselect_manuscript_id or self.sale.manuscript_id
        if preselect:
            idx = self.ms_combo.findData(preselect)
            if idx >= 0:
                self.ms_combo.setCurrentIndex(idx)
        form.addRow("文稿 *", self.ms_combo)

        self.platform_edit = QLineEdit(self.sale.platform)
        self.platform_edit.setPlaceholderText("如：番茄小说")
        form.addRow("平台", self.platform_edit)

        self.editor_edit = QLineEdit(self.sale.editor_name)
        self.editor_edit.setPlaceholderText("收稿编辑（可空）")
        form.addRow("编辑", self.editor_edit)

        self.amount_edit = QLineEdit(
            "" if self.sale.amount is None else f"{self.sale.amount:g}")
        self.amount_edit.setPlaceholderText("数字，单位元，可空")
        form.addRow("稿费金额", self.amount_edit)

        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        if self.sale.sale_date:
            self.date_edit.setDate(QDate.fromString(self.sale.sale_date, "yyyy-MM-dd"))
        else:
            self.date_edit.setDate(QDate.currentDate())
        form.addRow("售出日期", self.date_edit)

        self.month_edit = QLineEdit(self.sale.payment_month)
        self.month_edit.setPlaceholderText("如 2026-09")
        form.addRow("打款月份", self.month_edit)

        self.notes_edit = QPlainTextEdit(self.sale.notes)
        self.notes_edit.setMaximumHeight(64)
        form.addRow("备注", self.notes_edit)
        layout.addLayout(form)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)
        save_btn = QPushButton("保存")
        save_btn.setObjectName("primaryBtn")
        save_btn.clicked.connect(self._on_save)
        btns.addWidget(save_btn)
        layout.addLayout(btns)

    def _on_save(self):
        manuscript_id = self.ms_combo.currentData()
        if manuscript_id is None:
            QMessageBox.warning(self, "提示", "请选择文稿（文稿库为空时请先新建文稿）")
            return
        amount_text = self.amount_edit.text().strip()
        amount = None
        if amount_text:
            try:
                amount = float(amount_text)
            except ValueError:
                QMessageBox.warning(self, "提示", "稿费金额请填写数字")
                return
        month = self.month_edit.text().strip()
        if month and not MONTH_RE.match(month):
            QMessageBox.warning(self, "提示", "打款月份格式应为 yyyy-MM，如 2026-09")
            return
        self.sale.manuscript_id = manuscript_id
        self.sale.platform = self.platform_edit.text().strip()
        self.sale.editor_name = self.editor_edit.text().strip()
        self.sale.amount = amount
        self.sale.sale_date = self.date_edit.date().toString("yyyy-MM-dd")
        self.sale.payment_month = month
        self.sale.notes = self.notes_edit.toPlainText().strip()
        self.accept()


class SalesPage(QWidget):
    def __init__(self, db, store, main_window):
        super().__init__()
        self.db = db
        self.store = store
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        # 头部：统计 + 新增按钮
        top = QHBoxLayout()
        self.summary_label = QLabel()
        self.summary_label.setObjectName("hintText")
        top.addWidget(self.summary_label)
        top.addStretch()
        add_btn = QPushButton("新增售出记录")
        add_btn.setObjectName("primaryBtn")
        add_btn.clicked.connect(self._on_add)
        top.addWidget(add_btn)
        layout.addLayout(top)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["文稿", "平台", "编辑", "稿费(元)", "售出日期", "打款月份", "备注", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(7, QHeaderView.Fixed)
        self.table.setColumnWidth(7, 120)
        header.setStretchLastSection(False)
        layout.addWidget(self.table, 1)

        self.refresh()

    def refresh(self):
        count, total = self.db.sales_summary()
        self.summary_label.setText(f"已售出 {count} 篇 · 稿费合计 {total:g} 元")

        sales = self.db.list_sales()
        self.table.setRowCount(0)
        if not sales:
            self.table.setRowCount(1)
            item = mk_item("还没有售出记录，过稿之后来记一笔", Qt.AlignCenter)
            item.setForeground(Qt.gray)
            self.table.setItem(0, 0, item)
            self.table.setSpan(0, 0, 1, 8)
            return

        self.table.setRowCount(len(sales))
        for row, s in enumerate(sales):
            values = [s.manuscript_title or "（文稿已删除）", s.platform, s.editor_name,
                      "" if s.amount is None else f"{s.amount:g}",
                      s.sale_date, s.payment_month, s.notes]
            for col, text in enumerate(values):
                self.table.setItem(row, col, mk_item(text or ""))

            ops = QWidget()
            ops_layout = QHBoxLayout(ops)
            ops_layout.setContentsMargins(2, 0, 2, 0)
            ops_layout.setSpacing(4)
            edit_btn = QPushButton("编辑")
            edit_btn.setObjectName("iconBtn")
            edit_btn.clicked.connect(lambda _=False, sale=s: self._on_edit(sale))
            ops_layout.addWidget(edit_btn)
            del_btn = QPushButton("删除")
            del_btn.setObjectName("iconBtn")
            del_btn.setStyleSheet("color: #E03131;")
            del_btn.clicked.connect(lambda _=False, sale=s: self._on_delete(sale))
            ops_layout.addWidget(del_btn)
            self.table.setCellWidget(row, 7, ops)

    def _on_add(self):
        dlg = SaleDialog(self.db, self)
        if dlg.exec() == QDialog.Accepted:
            self.db.insert_sale(dlg.sale)
            self.main_window.data_changed.emit()
            self.refresh()

    def _on_edit(self, sale: Sale):
        dlg = SaleDialog(self.db, self, sale)
        if dlg.exec() == QDialog.Accepted:
            self.db.update_sale(dlg.sale)
            self.main_window.data_changed.emit()
            self.refresh()

    def _on_delete(self, sale: Sale):
        ret = QMessageBox.question(
            self, "确认删除",
            f"确定删除《{sale.manuscript_title}》的这条售出记录吗？")
        if ret == QMessageBox.Yes:
            self.db.delete_sale(sale.id)
            self.main_window.data_changed.emit()
            self.refresh()
