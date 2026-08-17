"""稿费记录页：售出登记（平台/编辑/金额/打款日期）+ 列表 + 统计。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QDate, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QDialog, QFormLayout, QMessageBox,
    QHeaderView, QPlainTextEdit, QDateEdit, QCheckBox,
)

from ..models import Sale
from ..widgets import mk_item, PagedTable, export_csv


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

        self.payment_date_edit = QLineEdit(self.sale.payment_date)
        self.payment_date_edit.setPlaceholderText("yyyy-MM-dd，可空")
        form.addRow("打款日期", self.payment_date_edit)
        if self.sale.payment_month and not self.sale.payment_date:
            legacy_hint = QLabel(
                f"历史记录仅保存到月份：{self.sale.payment_month}，请确认后补充具体日期。")
            legacy_hint.setObjectName("hintText")
            legacy_hint.setWordWrap(True)
            form.addRow("", legacy_hint)

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
        payment_date = self.payment_date_edit.text().strip()
        if payment_date and not QDate.fromString(payment_date, "yyyy-MM-dd").isValid():
            QMessageBox.warning(self, "提示", "打款日期格式应为 yyyy-MM-dd，如 2026-09-15")
            return
        self.sale.manuscript_id = manuscript_id
        self.sale.platform = self.platform_edit.text().strip()
        self.sale.editor_name = self.editor_edit.text().strip()
        self.sale.amount = amount
        self.sale.sale_date = self.date_edit.date().toString("yyyy-MM-dd")
        self.sale.payment_date = payment_date
        if payment_date:
            self.sale.payment_month = ""
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

        # 头部：统计 + 搜索 + 新增
        top = QHBoxLayout()
        self.summary_label = QLabel()
        self.summary_label.setObjectName("hintText")
        top.addWidget(self.summary_label)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索文稿 / 平台 / 编辑…")
        self.search_edit.setClearButtonEnabled(True)
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(200)
        self._search_debounce.timeout.connect(self._on_filter_changed)
        self.search_edit.textChanged.connect(lambda *_a: self._search_debounce.start())
        top.addWidget(self.search_edit, 1)
        self.use_date = QCheckBox("按售出日期")
        self.use_date.toggled.connect(self._on_filter_changed)
        top.addWidget(self.use_date)
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDisplayFormat("yyyy-MM-dd")
        self.date_from.setDate(QDate.currentDate().addMonths(-3))
        self.date_from.dateChanged.connect(self._on_filter_changed)
        top.addWidget(self.date_from)
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDisplayFormat("yyyy-MM-dd")
        self.date_to.setDate(QDate.currentDate())
        self.date_to.dateChanged.connect(self._on_filter_changed)
        top.addWidget(self.date_to)
        export_btn = QPushButton("导出 CSV")
        export_btn.clicked.connect(self._on_export)
        top.addWidget(export_btn)
        self.batch_delete_btn = QPushButton("批量删除")
        self.batch_delete_btn.setEnabled(False)
        self.batch_delete_btn.clicked.connect(self._on_batch_delete)
        top.addWidget(self.batch_delete_btn)
        add_btn = QPushButton("新增售出记录")
        add_btn.setObjectName("primaryBtn")
        add_btn.clicked.connect(self._on_add)
        top.addWidget(add_btn)
        layout.addLayout(top)

        self.paged = PagedTable(
            ["文稿", "平台", "编辑", "稿费(元)", "售出日期", "打款日期", "备注", "操作"],
            sort_keys=["title", "platform", "editor_name", "amount",
                       "sale_date", "payment_date", "notes", None],
            action_cols={7},
            empty_text="还没有售出记录，过稿之后来记一笔",
            store=store,
            width_key="table_widths_sales",
        )
        self.table = self.paged.table
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(7, QHeaderView.Fixed)
        self.table.setColumnWidth(7, 120)
        header.setStretchLastSection(False)
        self.paged.set_loader(self._fetch)
        self.paged.set_binder(self._bind_row)
        self.paged.selection_changed.connect(self._update_batch_btns)
        layout.addWidget(self.paged, 1)

        self.refresh()

    def refresh(self):
        count, total = self.db.sales_summary()
        self.summary_label.setText(f"已售出 {count} 篇 · 稿费合计 {total:g} 元")
        self._reload(reset_page=False)

    def _on_filter_changed(self):
        self._reload(reset_page=True)

    def _date_range(self):
        if not self.use_date.isChecked():
            return None, None
        return (self.date_from.date().toString("yyyy-MM-dd"),
                self.date_to.date().toString("yyyy-MM-dd"))

    def _fetch(self, offset, limit, order_by, desc):
        date_from, date_to = self._date_range()
        return self.db.list_sales_page(
            keyword=self.search_edit.text().strip() or None,
            date_from=date_from, date_to=date_to,
            offset=offset, limit=limit, order_by=order_by, desc=desc)

    def _reload(self, reset_page: bool = True):
        self.paged.reload(reset_page=reset_page)
        self._update_batch_btns()

    def _update_batch_btns(self):
        self.batch_delete_btn.setEnabled(bool(self.paged.selected_items()))

    def _bind_row(self, table, row, s):
        payment_text = s.payment_date or (
            f"{s.payment_month}（仅月份）" if s.payment_month else "")
        values = [s.manuscript_title or "（文稿已删除）", s.platform, s.editor_name,
                  "" if s.amount is None else f"{s.amount:g}",
                  s.sale_date, payment_text, s.notes]
        for col, text in enumerate(values):
            item = mk_item(text or "")
            if col == 0:
                item.setData(Qt.UserRole, s.id)
            table.setItem(row, col, item)

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
        table.setCellWidget(row, 7, ops)

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

    def _on_batch_delete(self):
        items = self.paged.selected_items()
        if not items:
            return
        ret = QMessageBox.question(
            self, "批量删除",
            f"确定删除选中的 {len(items)} 条售出记录吗？此操作不可恢复。")
        if ret != QMessageBox.Yes:
            return
        self.db.delete_sales([s.id for s in items])
        self.main_window.data_changed.emit()
        self.refresh()

    def _on_export(self):
        date_from, date_to = self._date_range()
        _total, sales = self.db.list_sales_page(
            keyword=self.search_edit.text().strip() or None,
            date_from=date_from, date_to=date_to,
            offset=0, limit=100000, order_by="id", desc=True)
        rows = []
        for s in sales:
            payment_text = s.payment_date or (
                f"{s.payment_month}（仅月份）" if s.payment_month else "")
            rows.append([
                s.manuscript_title or "", s.platform, s.editor_name,
                "" if s.amount is None else f"{s.amount:g}",
                s.sale_date, payment_text, s.notes,
            ])
        export_csv(self, ["文稿", "平台", "编辑", "稿费(元)", "售出日期", "打款日期", "备注"],
                   rows, "稿费记录.csv")
