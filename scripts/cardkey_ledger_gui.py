"""卡密发放台账图形界面：按钮操作，替代命令行菜单。

双击「卡密台账.bat」启动，或直接运行：
  python scripts/cardkey_ledger_gui.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from scripts import cardkey_ledger as ledger_mod
from scripts.make_cardkeys import display_key


class MarkDialog(QDialog):
    """手动标记卡密：输入卡密（多张可换行/空格分隔）和标记原因。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("手动标记卡密")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.keys_edit = QPlainTextEdit()
        self.keys_edit.setPlaceholderText("多张卡密用换行或空格分隔")
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("标记原因（如：测试用掉），可留空")
        form.addRow("卡密：", self.keys_edit)
        form.addRow("原因：", self.note_edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def keys(self) -> list[str]:
        return self.keys_edit.toPlainText().split()

    def note(self) -> str:
        return self.note_edit.text().strip()


class LedgerWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("卡密发放台账")
        self.setMinimumSize(640, 560)

        root = QVBoxLayout(self)

        self.stats_label = QLabel()
        root.addWidget(self.stats_label)

        # 取卡发放
        give_row = QHBoxLayout()
        give_row.addWidget(QLabel("取"))
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 999)
        give_row.addWidget(self.count_spin)
        give_row.addWidget(QLabel("张，备注："))
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("买家微信/闲鱼号，可留空")
        give_row.addWidget(self.note_edit, 1)
        give_btn = QPushButton("取卡发放")
        give_btn.clicked.connect(self.on_give)
        give_row.addWidget(give_btn)
        root.addLayout(give_row)

        # 本次取到的卡密
        self.result_edit = QPlainTextEdit()
        self.result_edit.setReadOnly(True)
        self.result_edit.setPlaceholderText("取到的卡密会显示在这里")
        self.result_edit.setMaximumHeight(120)
        root.addWidget(self.result_edit)
        copy_btn = QPushButton("复制卡密")
        copy_btn.clicked.connect(self.on_copy)
        root.addWidget(copy_btn)

        # 其他操作
        ops_row = QHBoxLayout()
        import_btn = QPushButton("导入新批次…")
        import_btn.clicked.connect(self.on_import)
        mark_btn = QPushButton("手动标记卡密…")
        mark_btn.clicked.connect(self.on_mark)
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh)
        ops_row.addWidget(import_btn)
        ops_row.addWidget(mark_btn)
        ops_row.addStretch(1)
        ops_row.addWidget(refresh_btn)
        root.addLayout(ops_row)

        # 明细列表
        self.tabs = QTabWidget()
        self.given_table = QTableWidget(0, 3)
        self.given_table.setHorizontalHeaderLabels(["卡密", "发放时间", "备注"])
        self.stock_table = QTableWidget(0, 1)
        self.stock_table.setHorizontalHeaderLabels(["库存卡密（按发放顺序）"])
        for table in (self.given_table, self.stock_table):
            table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            table.verticalHeader().setVisible(False)
        self.tabs.addTab(self.given_table, "发放记录")
        self.tabs.addTab(self.stock_table, "库存")
        root.addWidget(self.tabs, 1)

        self.refresh()

    def refresh(self):
        ledger = ledger_mod.load_ledger()
        stock = ledger_mod._stock_in_order(ledger)
        given = [(k, v) for k, v in ledger.items() if v["status"] == "given"]
        self.stats_label.setText(
            f"台账共 {len(ledger)} 张：库存 <b>{len(stock)}</b> 张，"
            f"已发放 <b>{len(given)}</b> 张")

        self.stock_table.setRowCount(len(stock))
        for row, key in enumerate(stock):
            self.stock_table.setItem(row, 0, QTableWidgetItem(display_key(key)))

        given.sort(key=lambda kv: kv[1]["given_at"], reverse=True)
        self.given_table.setRowCount(len(given))
        for row, (key, v) in enumerate(given):
            self.given_table.setItem(row, 0, QTableWidgetItem(display_key(key)))
            self.given_table.setItem(row, 1, QTableWidgetItem(v["given_at"]))
            self.given_table.setItem(row, 2, QTableWidgetItem(v["note"]))

    def on_give(self):
        count = self.count_spin.value()
        note = self.note_edit.text().strip()
        try:
            keys = ledger_mod.give_keys(count, note)
        except ValueError as e:
            QMessageBox.warning(self, "库存不足", str(e))
            return
        self.result_edit.setPlainText("\n".join(display_key(k) for k in keys))
        self.on_copy()
        self.refresh()

    def on_copy(self):
        text = self.result_edit.toPlainText().strip()
        if text:
            QGuiApplication.clipboard().setText(text)
            self.statusBar_hint("卡密已复制到剪贴板")

    def statusBar_hint(self, msg: str):
        self.stats_label.setToolTip(msg)
        self.setWindowTitle(f"卡密发放台账 — {msg}")

    def on_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择卡密文件", os.path.join(
                os.path.dirname(ledger_mod.LEDGER_PATH)),
            "卡密文件 (*.json *.txt);;所有文件 (*)")
        if not path:
            return
        try:
            added, total = ledger_mod.import_file(path)
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))
            return
        QMessageBox.information(
            self, "导入完成", f"新增 {added} 张，台账共 {total} 张")
        self.refresh()

    def on_mark(self):
        dlg = MarkDialog(self)
        if dlg.exec() != QDialog.Accepted or not dlg.keys():
            return
        marked, skipped = ledger_mod.mark_keys(dlg.keys(), dlg.note())
        msg = f"已标记 {len(marked)} 张"
        if skipped:
            msg += f"，{len(skipped)} 张不在台账中已跳过：\n" + "\n".join(skipped)
        QMessageBox.information(self, "标记完成", msg)
        self.refresh()


def main() -> int:
    app = QApplication(sys.argv)
    win = LedgerWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
