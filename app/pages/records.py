"""投递记录页：状态/回复筛选、表格、删除/重发记录。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QTableWidget, QTableWidgetItem, QMessageBox, QAbstractItemView, QHeaderView,
)

from ..workers import SendWorker
from ..widgets import mk_item, badge_cell

STATUS_FILTERS = ["全部状态", "待发", "已发", "发送中", "已跳过", "失败", "定时待发"]
REPLY_FILTERS = ["全部", "未回复", "过稿", "退稿", "需修改"]

_ORANGE = QColor("#E8590C")
_GRAY = QColor("#A0989E")

_STATUS_KIND = {
    "待发": "pending", "已发": "sent", "发送中": "sending", "已跳过": "skip",
    "失败": "fail", "定时待发": "scheduled",
}
_VERDICT_KIND = {"过稿": "pass", "退稿": "reject", "需修改": "revise"}


class RecordsPage(QWidget):
    def __init__(self, db, store, main_window):
        super().__init__()
        self.db = db
        self.store = store
        self.main_window = main_window
        self._resend_worker: SendWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        tool = QHBoxLayout()
        tool.setSpacing(8)
        self.status_combo = QComboBox()
        self.status_combo.addItems(STATUS_FILTERS)
        self.status_combo.currentIndexChanged.connect(self._reload)
        tool.addWidget(self.status_combo)
        self.reply_combo = QComboBox()
        self.reply_combo.addItems(REPLY_FILTERS)
        self.reply_combo.currentIndexChanged.connect(self._reload)
        tool.addWidget(self.reply_combo)
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh)
        tool.addWidget(refresh_btn)
        tool.addStretch()
        layout.addLayout(tool)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["文稿标题", "编辑", "平台", "发信邮箱", "发信时间", "状态", "回复判定", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(7, QHeaderView.Fixed)
        self.table.setColumnWidth(7, 170)
        header.setStretchLastSection(False)
        layout.addWidget(self.table, 1)

        self.refresh()

    def refresh(self):
        # 保留筛选选择，重载表格
        self._reload()

    def _reload(self):
        status_filter = self.status_combo.currentText()
        status_filter = None if status_filter == "全部状态" else status_filter
        reply_filter = self.reply_combo.currentText()

        subs = self.db.list_submissions(status_filter=status_filter)
        if reply_filter == "未回复":
            subs = [s for s in subs if s.reply_status == "无"]
        elif reply_filter != "全部":
            subs = [s for s in subs if s.reply_status == reply_filter]

        manuscripts = {m.id: m for m in self.db.list_manuscripts()}
        editors = {e.id: e for e in self.db.list_editors(include_blacklisted=True)}
        urge_days = self.store.get_urge_days()
        stale_ids = {s.id for s in self.db.stale_submissions(urge_days)}

        self.table.setRowCount(0)
        if not subs:
            self.table.setRowCount(1)
            item = QTableWidgetItem("暂无投递记录")
            item.setTextAlignment(Qt.AlignCenter)
            item.setForeground(Qt.gray)
            self.table.setItem(0, 0, item)
            self.table.setSpan(0, 0, 1, 8)
            return

        self.table.setRowCount(len(subs))
        for row, s in enumerate(subs):
            m = manuscripts.get(s.manuscript_id)
            e = editors.get(s.editor_id)
            values = [
                m.title if m else "（文稿已删除）",
                e.name if e else "（编辑已删除）",
                e.platform if e else "",
                s.from_mailbox or "",
                s.sent_at or "",
            ]
            for col, text in enumerate(values):
                self.table.setItem(row, col, mk_item(text))
            # 超期未回复：发信时间标橙 + tooltip
            if s.id in stale_ids:
                sent_item = self.table.item(row, 4)
                sent_item.setForeground(_ORANGE)
                sent_item.setToolTip(f"已超过 {urge_days} 天未回复")

            if s.status == "定时待发" and s.scheduled_at:
                status_text = f"定时待发 {s.scheduled_at[5:16]}"
            else:
                status_text = s.status
            status_tooltip = ""
            if s.status == "失败" and s.last_error:
                status_tooltip = s.last_error
            elif s.status == "定时待发" and s.scheduled_at:
                status_tooltip = f"计划 {s.scheduled_at}"
            self.table.setCellWidget(row, 5, badge_cell(
                status_text, _STATUS_KIND.get(s.status, "other"),
                tooltip=status_tooltip))

            verdict = s.reply_status or "无"
            if verdict == "无":
                verdict_item = mk_item("-", Qt.AlignCenter)
                verdict_item.setForeground(_GRAY)
                self.table.setItem(row, 6, verdict_item)
            else:
                self.table.setCellWidget(row, 6, badge_cell(
                    verdict, _VERDICT_KIND.get(verdict, "other")))

            # 操作：可重发的状态（失败/已跳过/待发）提供「重发」按钮；定时待发提供「取消定时」
            wrap = QWidget()
            lay = QHBoxLayout(wrap)
            lay.setContentsMargins(2, 0, 2, 0)
            lay.setAlignment(Qt.AlignCenter)
            if s.status in ("失败", "已跳过", "待发"):
                resend_btn = QPushButton("重发")
                resend_btn.setObjectName("iconBtn")
                resend_btn.clicked.connect(lambda _=False, sub=s: self._on_resend(sub))
                lay.addWidget(resend_btn)
            if s.status == "定时待发":
                cancel_btn = QPushButton("取消定时")
                cancel_btn.setObjectName("iconBtn")
                cancel_btn.setStyleSheet("color: #E8590C;")
                cancel_btn.clicked.connect(lambda _=False, sub=s: self._on_cancel_scheduled(sub))
                lay.addWidget(cancel_btn)
            del_btn = QPushButton("删除")
            del_btn.setObjectName("iconBtn")
            del_btn.setStyleSheet("color: #E03131;")
            del_btn.clicked.connect(lambda _=False, sub=s: self._on_delete(sub))
            lay.addWidget(del_btn)
            self.table.setCellWidget(row, 7, wrap)

    def _on_cancel_scheduled(self, submission):
        ret = QMessageBox.question(
            self, "取消定时投递",
            f"确定取消这条定时投递吗？\n发往：{submission.to_email}\n计划：{submission.scheduled_at}")
        if ret == QMessageBox.Yes:
            self.db.delete_submission(submission.id)
            self.main_window.data_changed.emit()
            self._reload()

    # ---------- 重发 ----------
    def _on_resend(self, submission):
        if self._resend_worker is not None and self._resend_worker.isRunning():
            return
        mailboxes = [m for m in self.store.load_mailboxes() if m.enabled and m.address]
        if not mailboxes:
            QMessageBox.warning(self, "提示", "还没有已启用的发信邮箱，请先到设置页配置。")
            self.main_window.navigate("settings")
            return
        available = [m for m in mailboxes if self.db.count_today(m.address) < m.daily_limit]
        if not available:
            QMessageBox.warning(self, "提示", "所有已启用邮箱今日投递已达上限，请明天再试。")
            return
        attachment = None
        if submission.manuscript_id:
            manuscript = self.db.get_manuscript(submission.manuscript_id)
            if manuscript and manuscript.file_path:
                attachment = manuscript.file_path
        job = {"submission_id": submission.id, "to": submission.to_email,
               "subject": submission.subject, "body": submission.body,
               "message_id": submission.message_id,
               "attachment_path": attachment}
        # 重置为待发后立即重发（SendWorker 内部会做日额度预留）
        self.db.update_status(submission.id, "待发", sent_at="")
        self._resend_worker = SendWorker(available, [job], 0, self, db=self.db)
        self._resend_worker.item_done.connect(self._on_resend_item_done)
        self._resend_worker.all_done.connect(self._on_resend_all_done)
        self._resend_worker.start()
        self.main_window.data_changed.emit()

    def _on_resend_item_done(self, submission_id: int, ok: bool, error: str,
                             mailbox_address: str, skipped: bool = False):
        if submission_id > 0:
            if skipped:
                self.db.update_status(submission_id, "已跳过", sent_at="")
            else:
                self.db.update_status(
                    submission_id, "已发" if ok else "失败",
                    error=error if not ok else None)
                if mailbox_address:
                    self.db.update_from_mailbox(submission_id, mailbox_address)
        self._reload()

    def _on_resend_all_done(self):
        self._resend_worker = None
        self.main_window.data_changed.emit()
        self._reload()

    def _on_delete(self, submission):
        ret = QMessageBox.question(
            self, "确认删除",
            f"确定删除这条投递记录（发往 {submission.to_email}）吗？")
        if ret == QMessageBox.Yes:
            self.db.delete_submission(submission.id)
            self.main_window.data_changed.emit()
            self._reload()
