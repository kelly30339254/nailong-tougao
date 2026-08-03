"""回信中心页：立即收信（FetchWorker + reply_ingest）、回信列表、标记已读、查看全文。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QPushButton,
    QTableWidget, QTableWidgetItem, QFrame, QDialog, QMessageBox,
    QAbstractItemView, QHeaderView, QPlainTextEdit,
)

from ..theme import theme_colors
from ..workers import FetchWorker
from ..reply_ingest import ingest_results
from ..widgets import mk_item, badge_cell, make_dot

_VERDICT_KIND = {"过稿": "pass", "退稿": "reject", "需修改": "revise", "其他": "other"}


class ReplyDetailDialog(QDialog):
    """查看全文：主题 + 完整摘要。"""

    def __init__(self, reply, parent=None):
        super().__init__(parent)
        self.setWindowTitle("回信全文")
        self.setMinimumSize(480, 360)
        layout = QVBoxLayout(self)
        subject = QLabel(reply.subject or "（无主题）")
        subject.setObjectName("cardTitle")
        subject.setWordWrap(True)
        layout.addWidget(subject)
        meta = QLabel(f"来自：{reply.from_email}    时间：{reply.received_at}    判定：{reply.verdict}")
        meta.setObjectName("hintText")
        layout.addWidget(meta)
        body = QPlainTextEdit()
        body.setReadOnly(True)
        body.setPlainText(reply.snippet or "（无内容）")
        layout.addWidget(body, 1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, 0, Qt.AlignRight)


class RepliesPage(QWidget):
    def __init__(self, db, store, main_window):
        super().__init__()
        self.db = db
        self.store = store
        self.main_window = main_window
        self._fetch_worker: FetchWorker | None = None
        self._new_count = 0
        self._invalid_count = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        # 顶部：说明条 + 右侧操作
        top = QHBoxLayout()
        top.setSpacing(10)
        info = QFrame()
        info.setObjectName("infoBar")
        info_layout = QHBoxLayout(info)
        info_layout.setContentsMargins(12, 6, 12, 6)
        info_text = QLabel("从已启用邮箱收件箱拉取编辑列表中编辑的来信并自动判定，只读取不删改。")
        info_text.setObjectName("infoBarText")
        info_layout.addWidget(info_text)
        top.addWidget(info, 1)
        self.unread_check = QCheckBox("只看未读")
        self.unread_check.toggled.connect(self._reload)
        top.addWidget(self.unread_check)
        self.fetch_btn = QPushButton("立即收信")
        self.fetch_btn.setObjectName("primaryBtn")
        self.fetch_btn.clicked.connect(self._on_fetch)
        top.addWidget(self.fetch_btn)
        layout.addLayout(top)

        self.status_label = QLabel("")
        self.status_label.setObjectName("hintText")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["", "编辑 / 发件邮箱", "判定", "主题", "摘要", "时间", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 36)
        header.setSectionResizeMode(6, QHeaderView.Fixed)
        self.table.setColumnWidth(6, 170)
        header.setStretchLastSection(False)
        layout.addWidget(self.table, 1)

        self.refresh()

    def refresh(self):
        self._reload()

    def _reload(self):
        replies = self.db.list_replies(unread_only=self.unread_check.isChecked())
        editors = {e.email.lower(): e for e in self.db.list_editors(include_blacklisted=True)
                   if e.email}
        primary = QColor(theme_colors(self.store.get_theme())["primary"])

        self.table.setRowCount(0)
        if not replies:
            self.table.setRowCount(1)
            item = QTableWidgetItem("还没有收到编辑回信")
            item.setTextAlignment(Qt.AlignCenter)
            item.setForeground(Qt.gray)
            self.table.setItem(0, 0, item)
            self.table.setSpan(0, 0, 1, 7)
            return

        self.table.setRowCount(len(replies))
        for row, r in enumerate(replies):
            # 未读状态点（彩色圆点，不用字符图标）
            if not r.is_read:
                dot_wrap = QWidget()
                dot_lay = QHBoxLayout(dot_wrap)
                dot_lay.setContentsMargins(0, 0, 0, 0)
                dot_lay.setAlignment(Qt.AlignCenter)
                dot_lay.addWidget(make_dot(primary.name(), 8))
                self.table.setCellWidget(row, 0, dot_wrap)

            editor = editors.get((r.from_email or "").lower())
            self.table.setItem(row, 1, mk_item(
                f"{editor.name}（{r.from_email}）" if editor else r.from_email))

            self.table.setCellWidget(row, 2, badge_cell(
                r.verdict or "其他", _VERDICT_KIND.get(r.verdict, "other")))

            self.table.setItem(row, 3, mk_item(r.subject or ""))
            # 摘要列显示截断 80 字，tooltip 放完整摘要
            snippet_item = mk_item((r.snippet or "")[:80])
            snippet_item.setToolTip(r.snippet or "")
            self.table.setItem(row, 4, snippet_item)
            self.table.setItem(row, 5, mk_item(r.received_at or ""))

            ops = QWidget()
            ops_layout = QHBoxLayout(ops)
            ops_layout.setContentsMargins(2, 0, 2, 0)
            ops_layout.setSpacing(4)
            if not r.is_read:
                read_btn = QPushButton("标记已读")
                read_btn.setObjectName("iconBtn")
                read_btn.clicked.connect(lambda _=False, rid=r.id: self._on_mark_read(rid))
                ops_layout.addWidget(read_btn)
            view_btn = QPushButton("查看全文")
            view_btn.setObjectName("iconBtn")
            view_btn.clicked.connect(lambda _=False, rep=r: self._on_view(rep))
            ops_layout.addWidget(view_btn)
            self.table.setCellWidget(row, 6, ops)

    def _on_mark_read(self, reply_id: int):
        self.db.mark_read(reply_id)
        self.main_window.data_changed.emit()
        self._reload()

    def _on_view(self, reply):
        ReplyDetailDialog(reply, self).exec()

    # ---------- 立即收信 ----------
    def _on_fetch(self):
        if self._fetch_worker is not None and self._fetch_worker.isRunning():
            return
        mailboxes = self.store.load_mailboxes()
        if not any(m.enabled and m.address for m in mailboxes):
            QMessageBox.warning(self, "提示", "还没有已启用的邮箱，请先到设置页配置。")
            self.main_window.navigate("settings")
            return
        editor_emails = {e.email for e in self.db.list_editors(include_blacklisted=True)
                         if e.email}
        if not editor_emails:
            QMessageBox.warning(self, "提示", "编辑列表为空，请先添加编辑。")
            return
        _auto, _interval, lookback_days = self.store.get_fetch_config()
        self._new_count = 0
        self._invalid_count = 0
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("收信中…")
        self.status_label.setText("")
        self._fetch_worker = FetchWorker(mailboxes, editor_emails, lookback_days, self)
        self._fetch_worker.progress.connect(lambda msg: self.status_label.setText(msg))
        self._fetch_worker.mailbox_result.connect(self._on_mailbox_result)
        self._fetch_worker.all_done.connect(self._on_fetch_done)
        self._fetch_worker.start()

    def _on_mailbox_result(self, address: str, results: list):
        res = ingest_results(self.db, address, results)
        self._new_count += res.new_replies
        self._invalid_count += res.invalid_marks

    def _on_fetch_done(self):
        self._fetch_worker = None
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("立即收信")
        text = f"本次新到 {self._new_count} 封"
        if self._invalid_count:
            text += f"，标记失效邮箱 {self._invalid_count} 个"
        self.status_label.setText(text)
        self.main_window.data_changed.emit()
        self._reload()
