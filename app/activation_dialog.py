"""首次启动激活对话框：输入卡密 → 在线核销，失败不允许进入主界面。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
)

from . import license as lic


class _ActivateWorker(QThread):
    """后台线程联网核销，避免卡界面。"""

    result = Signal(bool, str)

    def __init__(self, card_key: str, parent=None):
        super().__init__(parent)
        self.card_key = card_key

    def run(self):
        try:
            ok, msg = lic.activate(self.card_key)
        except Exception as exc:
            ok, msg = False, f"激活失败：{exc}"
        self.result.emit(ok, msg)


class ActivationDialog(QDialog):
    """卡密激活。exec() 返回 Accepted 表示激活成功；取消/关闭即拒绝。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("激活奶龙投稿助手")
        self.setMinimumWidth(420)
        self.setModal(True)
        self._worker = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel("首次使用需要激活")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        hint = QLabel("请输入购买时获得的卡密（激活需要联网，一张卡密只能激活一台设备）：")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("例如：NLK-XXXX-XXXX-XXXX")
        self.key_edit.setMaxLength(80)
        layout.addWidget(self.key_edit)

        self.error_label = QLabel("")
        self.error_label.setObjectName("hintText")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        row = QHBoxLayout()
        row.addStretch()
        self.cancel_btn = QPushButton("退出")
        self.cancel_btn.clicked.connect(self.reject)
        row.addWidget(self.cancel_btn)
        self.ok_btn = QPushButton("激活")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self._on_activate)
        row.addWidget(self.ok_btn)
        layout.addLayout(row)

        self.key_edit.setFocus()

    def _on_activate(self):
        key = self.key_edit.text().strip()
        if not key:
            self.error_label.setText("请输入卡密")
            return
        self._set_busy(True)
        self.error_label.setText("正在激活，请稍候……")
        self._worker = _ActivateWorker(key, self)
        self._worker.result.connect(self._on_result)
        self._worker.start()

    def _on_result(self, ok: bool, msg: str):
        self._set_busy(False)
        if ok:
            self.accept()
        else:
            self.error_label.setText(msg)

    def _set_busy(self, busy: bool):
        self.ok_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(not busy)
        self.key_edit.setEnabled(not busy)
