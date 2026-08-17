"""投递记录页：状态/回复筛选、表格、删除/重发记录。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QTableWidgetItem, QMessageBox, QHeaderView,
    QLineEdit, QDialog, QPlainTextEdit, QDateTimeEdit,
)

from ..workers import SendWorker
from ..widgets import mk_item, badge_cell, PagedTable, export_csv

STATUS_FILTERS = ["全部状态", "待发", "已发", "发送中", "已跳过", "失败", "定时待发"]
REPLY_FILTERS = ["全部", "未回复", "过稿", "退稿", "需修改"]

_ORANGE = QColor("#E8590C")
_GRAY = QColor("#A0989E")

_STATUS_KIND = {
    "待发": "pending", "已发": "sent", "发送中": "sending", "已跳过": "skip",
    "失败": "fail", "定时待发": "scheduled",
}
_VERDICT_KIND = {"过稿": "pass", "退稿": "reject", "需修改": "revise"}


def _humanize_send_error(raw: str) -> str:
    text = raw or ""
    low = text.lower()
    if "535" in text or "authentication" in low or "授权" in text:
        return f"登录/授权失败。请到设置里重新测试授权码。\n原文：{text}"
    if "spam" in low or "junk" in low or "垃圾" in text:
        return f"可能被对方当成垃圾邮件。可降低发信频率或微调正文。\n原文：{text}"
    if "timeout" in low or "timed out" in low or "超时" in text:
        return f"连接超时，请检查网络后重发。\n原文：{text}"
    if "connection" in low or "连接" in text:
        return f"连不上邮箱服务器，请检查网络和 SMTP 设置。\n原文：{text}"
    return text


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
        self.status_combo.currentIndexChanged.connect(self._on_filter_changed)
        tool.addWidget(self.status_combo)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索文稿 / 编辑 / 邮箱…")
        self.search_edit.setClearButtonEnabled(True)
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(200)
        self._search_debounce.timeout.connect(self._on_filter_changed)
        self.search_edit.textChanged.connect(lambda *_a: self._search_debounce.start())
        tool.addWidget(self.search_edit, 1)
        export_btn = QPushButton("导出 CSV")
        export_btn.clicked.connect(self._on_export)
        tool.addWidget(export_btn)
        self.reply_combo = QComboBox()
        self.reply_combo.addItems(REPLY_FILTERS)
        self.reply_combo.currentIndexChanged.connect(self._on_filter_changed)
        tool.addWidget(self.reply_combo)
        self.batch_resend_btn = QPushButton("批量重发")
        self.batch_resend_btn.setEnabled(False)
        self.batch_resend_btn.clicked.connect(self._on_batch_resend)
        tool.addWidget(self.batch_resend_btn)
        self.batch_delete_btn = QPushButton("批量删除")
        self.batch_delete_btn.setEnabled(False)
        self.batch_delete_btn.clicked.connect(self._on_batch_delete)
        tool.addWidget(self.batch_delete_btn)
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh)
        tool.addWidget(refresh_btn)
        tool.addStretch()
        layout.addLayout(tool)

        self.paged = PagedTable(
            ["文稿标题", "编辑", "平台", "发信邮箱", "发信时间", "状态", "回复判定", "操作"],
            sort_keys=["title", "editor", "platform", "from_mailbox",
                       "sent_at", "status", "reply_status", None],
            action_cols={7},
            empty_text="暂无投递记录",
            store=store,
            width_key="table_widths_records",
        )
        self.table = self.paged.table
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(7, QHeaderView.Fixed)
        self.table.setColumnWidth(7, 260)
        header.setStretchLastSection(False)
        self.paged.set_loader(self._fetch)
        self.paged.set_binder(self._bind_row)
        self.paged.selection_changed.connect(self._update_batch_btns)
        layout.addWidget(self.paged, 1)

        self.refresh()

    def refresh(self):
        self._reload(reset_page=False)

    def _on_filter_changed(self):
        self._reload(reset_page=True)

    def _filters(self):
        status = self.status_combo.currentText()
        status = None if status == "全部状态" else status
        reply = self.reply_combo.currentText()
        keyword = (self.search_edit.text() if hasattr(self, "search_edit") else "").strip()
        return status, reply, keyword or None

    def _fetch(self, offset, limit, order_by, desc):
        status, reply, keyword = self._filters()
        return self.db.list_submissions_page(
            status_filter=status, reply_filter=reply, keyword=keyword,
            offset=offset, limit=limit, order_by=order_by, desc=desc)

    def _reload(self, reset_page: bool = True):
        self._manuscripts = {m.id: m for m in self.db.list_manuscripts()}
        self._editors = {e.id: e for e in self.db.list_editors(include_blacklisted=True)}
        self._stale_ids = {s.id for s in self.db.stale_submissions(self.store.get_urge_days())}
        self._urge_days = self.store.get_urge_days()
        self.paged.reload(reset_page=reset_page)
        self._update_batch_btns()

    def _update_batch_btns(self):
        selected = self.paged.selected_items()
        self.batch_delete_btn.setEnabled(bool(selected))
        self.batch_resend_btn.setEnabled(any(
            s.status in ("失败", "已跳过", "待发") for s in selected))

    def _bind_row(self, table, row, s):
        manuscripts = getattr(self, "_manuscripts", {})
        editors = getattr(self, "_editors", {})
        stale_ids = getattr(self, "_stale_ids", set())
        urge_days = getattr(self, "_urge_days", 7)
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
            item = mk_item(text)
            if col == 0:
                item.setData(Qt.UserRole, s.id)
            table.setItem(row, col, item)
        if s.id in stale_ids:
            sent_item = table.item(row, 4)
            sent_item.setForeground(_ORANGE)
            sent_item.setToolTip(f"已超过 {urge_days} 天未回复")

        if s.status == "定时待发" and s.scheduled_at:
            status_text = f"定时待发 {s.scheduled_at[5:16]}"
        else:
            status_text = s.status
        status_tooltip = ""
        if s.status == "失败" and s.last_error:
            status_tooltip = _humanize_send_error(s.last_error)
        elif s.status == "定时待发" and s.scheduled_at:
            status_tooltip = f"计划 {s.scheduled_at}"
        table.setCellWidget(row, 5, badge_cell(
            status_text, _STATUS_KIND.get(s.status, "other"),
            tooltip=status_tooltip))

        verdict = s.reply_status or "无"
        if verdict == "无":
            verdict_item = mk_item("-", Qt.AlignCenter)
            verdict_item.setForeground(_GRAY)
            table.setItem(row, 6, verdict_item)
        else:
            table.setCellWidget(row, 6, badge_cell(
                verdict, _VERDICT_KIND.get(verdict, "other")))

        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setAlignment(Qt.AlignCenter)
        if s.status in ("失败", "已跳过", "待发"):
            resend_btn = QPushButton("重发")
            resend_btn.setObjectName("iconBtn")
            resend_btn.setToolTip("使用当时发出的主题和正文，不再微调")
            resend_btn.clicked.connect(lambda _=False, sub=s: self._on_resend(sub))
            lay.addWidget(resend_btn)
        if s.status == "定时待发":
            preview_btn = QPushButton("预览")
            preview_btn.setObjectName("iconBtn")
            preview_btn.clicked.connect(lambda _=False, sub=s: self._on_preview(sub))
            lay.addWidget(preview_btn)
            cancel_btn = QPushButton("取消定时")
            cancel_btn.setObjectName("iconBtn")
            cancel_btn.setStyleSheet("color: #E8590C;")
            cancel_btn.clicked.connect(lambda _=False, sub=s: self._on_cancel_scheduled(sub))
            lay.addWidget(cancel_btn)
        if s.id in stale_ids and s.status == "已发" and (s.reply_status or "无") == "无":
            urge_btn = QPushButton("催稿")
            urge_btn.setObjectName("iconBtn")
            urge_btn.clicked.connect(lambda _=False, sub=s: self._on_urge(sub))
            lay.addWidget(urge_btn)
        if s.reply_status == "退稿" and s.manuscript_id:
            retarget_btn = QPushButton("改投")
            retarget_btn.setObjectName("iconBtn")
            retarget_btn.clicked.connect(lambda _=False, sub=s: self._on_retarget(sub))
            lay.addWidget(retarget_btn)
        del_btn = QPushButton("删除")
        del_btn.setObjectName("iconBtn")
        del_btn.setStyleSheet("color: #E03131;")
        del_btn.clicked.connect(lambda _=False, sub=s: self._on_delete(sub))
        lay.addWidget(del_btn)
        table.setCellWidget(row, 7, wrap)

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
        self._reload(reset_page=False)

    def _on_resend_all_done(self):
        self._resend_worker = None
        self.main_window.data_changed.emit()
        self._reload(reset_page=False)

    def _on_export(self):
        status, reply, keyword = self._filters()
        _total, subs = self.db.list_submissions_page(
            status_filter=status, reply_filter=reply, keyword=keyword,
            offset=0, limit=100000, order_by="id", desc=True)
        manuscripts = {m.id: m for m in self.db.list_manuscripts()}
        editors = {e.id: e for e in self.db.list_editors(include_blacklisted=True)}
        rows = []
        for s in subs:
            m = manuscripts.get(s.manuscript_id)
            e = editors.get(s.editor_id)
            rows.append([
                m.title if m else "（文稿已删除）",
                e.name if e else "（编辑已删除）",
                e.platform if e else "",
                s.from_mailbox or "",
                s.sent_at or "",
                s.status,
                s.reply_status or "",
            ])
        export_csv(self, ["文稿", "编辑", "平台", "发信邮箱", "发信时间", "状态", "回复判定"],
                   rows, "投递记录.csv")

    def _on_batch_resend(self):
        items = [s for s in self.paged.selected_items()
                 if s.status in ("失败", "已跳过", "待发")]
        if not items:
            QMessageBox.information(self, "提示", "请先选中失败、已跳过或待发的记录。")
            return
        ret = QMessageBox.question(
            self, "批量重发",
            f"将重发选中的 {len(items)} 条记录，使用当时发出的主题和正文。确定？")
        if ret != QMessageBox.Yes:
            return
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
        jobs = []
        for submission in items:
            attachment = None
            if submission.manuscript_id:
                manuscript = self.db.get_manuscript(submission.manuscript_id)
                if manuscript and manuscript.file_path:
                    attachment = manuscript.file_path
            jobs.append({
                "submission_id": submission.id, "to": submission.to_email,
                "subject": submission.subject, "body": submission.body,
                "message_id": submission.message_id,
                "attachment_path": attachment,
            })
            self.db.update_status(submission.id, "待发", sent_at="")
        self._resend_worker = SendWorker(available, jobs, 0, self, db=self.db)
        self._resend_worker.item_done.connect(self._on_resend_item_done)
        self._resend_worker.all_done.connect(self._on_resend_all_done)
        self._resend_worker.start()
        self.main_window.data_changed.emit()

    def _on_batch_delete(self):
        items = self.paged.selected_items()
        if not items:
            return
        ret = QMessageBox.question(
            self, "批量删除",
            f"确定删除选中的 {len(items)} 条投递记录吗？此操作不可恢复。")
        if ret != QMessageBox.Yes:
            return
        self.db.delete_submissions([s.id for s in items])
        self.main_window.data_changed.emit()
        self._reload(reset_page=False)

    def _on_preview(self, submission):
        dlg = QDialog(self)
        dlg.setWindowTitle("定时任务预览")
        dlg.setMinimumSize(480, 360)
        box = QVBoxLayout(dlg)
        box.addWidget(QLabel(f"主题：{submission.subject}"))
        body = QPlainTextEdit()
        body.setReadOnly(True)
        body.setPlainText(submission.body or "")
        box.addWidget(body, 1)
        when = QDateTimeEdit()
        when.setCalendarPopup(True)
        when.setDisplayFormat("yyyy-MM-dd HH:mm")
        from PySide6.QtCore import QDateTime
        when.setDateTime(QDateTime.fromString((submission.scheduled_at or "")[:16], "yyyy-MM-dd HH:mm")
                         or QDateTime.currentDateTime())
        box.addWidget(QLabel("计划时间"))
        box.addWidget(when)
        save = QPushButton("保存时间")
        save.setObjectName("primaryBtn")

        def save_time():
            text = when.dateTime().toString("yyyy-MM-dd HH:mm") + ":00"
            self.db.update_scheduled_at(submission.id, text)
            dlg.accept()
            self._reload(reset_page=False)

        save.clicked.connect(save_time)
        box.addWidget(save)
        dlg.exec()

    def _on_urge(self, submission):
        from ..letter import render_template, DEFAULT_URGE_BODY, DEFAULT_URGE_SUBJECT
        editors = {e.id: e for e in self.db.list_editors(include_blacklisted=True)}
        manuscripts = {m.id: m for m in self.db.list_manuscripts()}
        editor = editors.get(submission.editor_id)
        manuscript = manuscripts.get(submission.manuscript_id)
        mapping = {
            "编辑称呼": (editor.name if editor else "编辑"),
            "作品名": (manuscript.title if manuscript else ""),
            "字数": (manuscript.word_count if manuscript else ""),
            "分类": (manuscript.category if manuscript else ""),
            "原投日期": (submission.sent_at or "")[:10],
        }
        subject_tpl, body_tpl = self.store.get_urge_template() if hasattr(self.store, "get_urge_template") else (
            DEFAULT_URGE_SUBJECT, DEFAULT_URGE_BODY)
        subject = render_template(subject_tpl, mapping)
        body = render_template(body_tpl, mapping)
        dlg = QDialog(self)
        dlg.setWindowTitle("催稿信预览")
        dlg.setMinimumSize(480, 360)
        box = QVBoxLayout(dlg)
        box.addWidget(QLabel(subject))
        view = QPlainTextEdit()
        view.setPlainText(body)
        box.addWidget(view, 1)
        send = QPushButton("确认发送")
        send.setObjectName("primaryBtn")

        def do_send():
            mailboxes = [m for m in self.store.load_mailboxes() if m.enabled and m.address]
            if not mailboxes:
                QMessageBox.warning(dlg, "提示", "没有可用发信邮箱")
                return
            from ..workers import SendWorker
            job = {"submission_id": submission.id, "to": submission.to_email,
                   "subject": view.toPlainText() and subject, "body": view.toPlainText(),
                   "message_id": "", "attachment_path": None}
            # 催稿不改原投稿状态，单独发一封：用 submission_id=0 避免覆盖
            job["submission_id"] = 0
            job["subject"] = subject
            self._resend_worker = SendWorker(mailboxes, [job], 0, self, db=self.db)
            self._resend_worker.all_done.connect(lambda: self.db.mark_urged(submission.id))
            self._resend_worker.start()
            dlg.accept()
            QMessageBox.information(self, "催稿", "已加入发送队列。")

        send.clicked.connect(do_send)
        box.addWidget(send)
        dlg.exec()

    def _on_retarget(self, submission):
        self.main_window.navigate("submit")
        page = self.main_window._pages.get("submit")
        if page is None or not submission.manuscript_id:
            return
        idx = page.manuscript_combo.findData(submission.manuscript_id)
        if idx >= 0:
            page.manuscript_combo.setCurrentIndex(idx)
        # 排除已投过该稿的编辑
        already = {s.editor_id for s in self.db.list_submissions_for_manuscript(submission.manuscript_id)}
        page._checked_ids -= already
        page._reload_editors_table()
        QMessageBox.information(self, "改投", "已打开投稿页并预填该文稿，已投过的编辑不会自动勾选。")

    def _on_delete(self, submission):
        ret = QMessageBox.question(
            self, "确认删除",
            f"确定删除这条投递记录（发往 {submission.to_email}）吗？")
        if ret == QMessageBox.Yes:
            self.db.delete_submission(submission.id)
            self.main_window.data_changed.emit()
            self._reload()
