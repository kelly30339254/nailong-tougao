"""回信中心页：立即收信（FetchWorker + reply_ingest）、回信列表、标记已读、查看全文。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QPushButton,
    QTableWidgetItem, QFrame, QDialog, QMessageBox, QInputDialog,
    QHeaderView, QPlainTextEdit, QComboBox, QLineEdit,
)

from ..theme import theme_colors
from ..workers import FetchWorker, AiCallWorker
from ..reply_ingest import ingest_results
from ..widgets import mk_item, badge_cell, make_dot, PagedTable

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
        full = getattr(reply, "body_full", "") or ""
        if full:
            body.setPlainText(full)
        else:
            snippet = reply.snippet or "（无内容）"
            body.setPlainText(snippet + "\n\n——\n该回信为旧版本截取，仅保存了摘要。")
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
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索发件人 / 主题 / 摘要…")
        self.search_edit.setClearButtonEnabled(True)
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(200)
        self._search_debounce.timeout.connect(self._on_filter_changed)
        self.search_edit.textChanged.connect(lambda *_a: self._search_debounce.start())
        top.addWidget(self.search_edit, 1)
        self.unread_check = QCheckBox("只看未读")
        self.unread_check.toggled.connect(self._on_filter_changed)
        top.addWidget(self.unread_check)
        self.verdict_combo = QComboBox()
        self.verdict_combo.addItems(["全部判定", "过稿", "退稿", "需修改", "待确认", "自动回复", "其他"])
        self.verdict_combo.currentIndexChanged.connect(self._on_filter_changed)
        top.addWidget(self.verdict_combo)
        self.batch_read_btn = QPushButton("批量已读")
        self.batch_read_btn.setEnabled(False)
        self.batch_read_btn.clicked.connect(self._on_batch_read)
        top.addWidget(self.batch_read_btn)
        self.batch_delete_btn = QPushButton("批量删除")
        self.batch_delete_btn.setEnabled(False)
        self.batch_delete_btn.clicked.connect(self._on_batch_delete)
        top.addWidget(self.batch_delete_btn)
        self.mark_all_btn = QPushButton("全部标已读")
        self.mark_all_btn.clicked.connect(self._on_mark_all_read)
        top.addWidget(self.mark_all_btn)
        self.ai_btn = QPushButton("AI判定")
        self.ai_btn.setToolTip("对当前筛选里「待确认 / 其他」的回信用大模型给建议，仍需你确认")
        self.ai_btn.clicked.connect(self._on_ai_batch)
        top.addWidget(self.ai_btn)
        self.fetch_btn = QPushButton("立即收信")
        self.fetch_btn.setObjectName("primaryBtn")
        self.fetch_btn.clicked.connect(self._on_fetch)
        top.addWidget(self.fetch_btn)
        self.stop_fetch_btn = QPushButton("停止收信")
        self.stop_fetch_btn.setEnabled(False)
        self.stop_fetch_btn.clicked.connect(self._on_stop_fetch)
        top.addWidget(self.stop_fetch_btn)
        layout.addLayout(top)

        self.status_label = QLabel("")
        self.status_label.setObjectName("hintText")
        layout.addWidget(self.status_label)

        self.paged = PagedTable(
            ["", "编辑 / 发件邮箱", "判定", "主题", "摘要", "时间", "操作"],
            sort_keys=[None, "from_email", "verdict", "subject", "snippet", "received_at", None],
            action_cols={0, 6},
            empty_text="还没有收到编辑回信",
            store=store,
            width_key="table_widths_replies",
        )
        self.table = self.paged.table
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 36)
        header.setSectionResizeMode(6, QHeaderView.Fixed)
        self.table.setColumnWidth(6, 230)
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

    def _fetch(self, offset, limit, order_by, desc):
        return self.db.list_replies_page(
            unread_only=self.unread_check.isChecked(),
            verdict=self.verdict_combo.currentText(),
            keyword=self.search_edit.text().strip() or None,
            offset=offset, limit=limit, order_by=order_by, desc=desc)

    def _reload(self, reset_page: bool = True):
        self._editors_by_email = {
            e.email.lower(): e for e in self.db.list_editors(include_blacklisted=True)
            if e.email}
        self._primary = QColor(theme_colors(self.store.get_theme())["primary"])
        self.paged.reload(reset_page=reset_page)
        self._update_batch_btns()

    def _update_batch_btns(self):
        selected = self.paged.selected_items()
        self.batch_delete_btn.setEnabled(bool(selected))
        self.batch_read_btn.setEnabled(any(not r.is_read for r in selected))

    def _bind_row(self, table, row, r):
        editors = getattr(self, "_editors_by_email", {})
        primary = getattr(self, "_primary", QColor("#E8590C"))
        if not r.is_read:
            dot_wrap = QWidget()
            dot_lay = QHBoxLayout(dot_wrap)
            dot_lay.setContentsMargins(0, 0, 0, 0)
            dot_lay.setAlignment(Qt.AlignCenter)
            dot_lay.addWidget(make_dot(primary.name(), 8))
            table.setCellWidget(row, 0, dot_wrap)

        editor = editors.get((r.from_email or "").lower())
        mail_item = mk_item(
            f"{editor.name}（{r.from_email}）" if editor else r.from_email)
        mail_item.setData(Qt.UserRole, r.id)
        table.setItem(row, 1, mail_item)

        table.setCellWidget(row, 2, badge_cell(
            r.verdict or "其他", _VERDICT_KIND.get(r.verdict, "other")))

        table.setItem(row, 3, mk_item(r.subject or ""))
        snippet_item = mk_item((r.snippet or "")[:80])
        snippet_item.setToolTip(r.snippet or "")
        table.setItem(row, 4, snippet_item)
        table.setItem(row, 5, mk_item(r.received_at or ""))

        ops = QWidget()
        ops_layout = QHBoxLayout(ops)
        ops_layout.setContentsMargins(2, 0, 2, 0)
        ops_layout.setSpacing(4)
        if not r.is_read:
            read_btn = QPushButton("标记已读")
            read_btn.setObjectName("iconBtn")
            read_btn.clicked.connect(lambda _=False, rid=r.id: self._on_mark_read(rid))
            ops_layout.addWidget(read_btn)
        if r.verdict in ("待确认", "其他"):
            ai_btn = QPushButton("AI判定")
            ai_btn.setObjectName("iconBtn")
            ai_btn.clicked.connect(lambda _=False, rep=r: self._on_ai_one(rep))
            ops_layout.addWidget(ai_btn)
        confirm_btn = QPushButton("确认判定" if r.verdict == "待确认" else "改判")
        confirm_btn.setObjectName("iconBtn")
        confirm_btn.clicked.connect(lambda _=False, rep=r: self._on_confirm(rep))
        ops_layout.addWidget(confirm_btn)
        view_btn = QPushButton("查看全文")
        view_btn.setObjectName("iconBtn")
        view_btn.clicked.connect(lambda _=False, rep=r: self._on_view(rep))
        ops_layout.addWidget(view_btn)
        del_btn = QPushButton("删除")
        del_btn.setObjectName("iconBtn")
        del_btn.setStyleSheet("color: #E03131;")
        del_btn.clicked.connect(lambda _=False, rid=r.id: self._on_delete(rid))
        ops_layout.addWidget(del_btn)
        table.setCellWidget(row, 6, ops)

    def _on_confirm(self, reply):
        verdict, ok = QInputDialog.getItem(
            self, "确认回信判定", "请选择实际结果：",
            ["过稿", "退稿", "需修改", "其他", "自动回复"], 0, False)
        if not ok:
            return
        if not self.db.confirm_reply_verdict(reply.id, verdict):
            QMessageBox.warning(self, "提示", "该回信已不存在，请刷新后重试。")
            return
        self.main_window.data_changed.emit()
        self._reload(reset_page=False)
        if reply.submission_id is None and verdict != "其他":
            QMessageBox.information(
                self, "已保存判定",
                "该回信无法唯一关联到某次投稿，因此只更新了回信判定，未修改投稿状态。")

    def _on_mark_read(self, reply_id: int):
        self.db.mark_read(reply_id)
        self.main_window.data_changed.emit()
        self._reload(reset_page=False)

    def _on_batch_read(self):
        ids = [r.id for r in self.paged.selected_items() if not r.is_read]
        if not ids:
            return
        self.db.mark_read_many(ids)
        self.main_window.data_changed.emit()
        self._reload(reset_page=False)

    def _on_batch_delete(self):
        items = self.paged.selected_items()
        if not items:
            return
        ret = QMessageBox.question(
            self, "批量删除",
            f"确定删除选中的 {len(items)} 封回信？投稿状态不会改动。")
        if ret != QMessageBox.Yes:
            return
        self.db.delete_replies([r.id for r in items])
        self.main_window.data_changed.emit()
        self._reload(reset_page=False)

    def _on_mark_all_read(self):
        unread = self.db.list_replies(unread_only=True)
        if not unread:
            return
        for r in unread:
            self.db.mark_read(r.id)
        self.main_window.data_changed.emit()
        self._reload()

    def _on_view(self, reply):
        ReplyDetailDialog(reply, self).exec()

    def _on_delete(self, reply_id: int):
        ret = QMessageBox.question(self, "删除回信", "确定删除这封回信？投稿状态不会改动。")
        if ret != QMessageBox.Yes:
            return
        self.db.delete_reply(reply_id)
        self.main_window.data_changed.emit()
        self._reload(reset_page=False)

    def _on_ai_one(self, reply):
        from ..ai_ui import require_ai_config
        from .. import ai_smart
        cfg = require_ai_config(self, self.store, self.main_window)
        if cfg is None:
            return
        self.status_label.setText("AI 正在判定……")
        worker = AiCallWorker(
            lambda: ai_smart.classify_reply_ai(cfg, reply.subject, reply.snippet), self)
        worker.finished_ok.connect(lambda out, r=reply: self._apply_ai_verdict(r, out))
        worker.failed.connect(lambda msg: self._ai_failed(msg))
        self._ai_worker = worker
        worker.start()

    def _on_ai_batch(self):
        from ..ai_ui import require_ai_config
        cfg = require_ai_config(self, self.store, self.main_window)
        if cfg is None:
            return
        targets = [r for r in self.db.list_replies()
                   if r.verdict in ("待确认", "其他")]
        if self.unread_check.isChecked():
            targets = [r for r in targets if not r.is_read]
        if not targets:
            QMessageBox.information(self, "提示", "当前没有「待确认 / 其他」的回信。")
            return
        self._ai_batch = list(targets)
        self._ai_cfg = cfg
        self.ai_btn.setEnabled(False)
        self._run_next_ai()

    def _run_next_ai(self):
        from .. import ai_smart
        if not getattr(self, "_ai_batch", None):
            self.ai_btn.setEnabled(True)
            self.status_label.setText("AI 判定已结束")
            return
        reply = self._ai_batch.pop(0)
        self.status_label.setText(f"AI 判定剩余 {len(self._ai_batch) + 1} 封……")
        worker = AiCallWorker(
            lambda r=reply: ai_smart.classify_reply_ai(
                self._ai_cfg, r.subject, r.snippet), self)
        worker.finished_ok.connect(lambda out, r=reply: self._batch_one_done(r, out))
        worker.failed.connect(lambda msg, r=reply: self._batch_one_fail(r, msg))
        self._ai_worker = worker
        worker.start()

    def _batch_one_done(self, reply, out):
        self._offer_verdict(reply, out)
        self._run_next_ai()

    def _batch_one_fail(self, reply, message: str):
        self.status_label.setText(f"「{reply.subject or '无主题'}」判定失败：{message}")
        self._run_next_ai()

    def _apply_ai_verdict(self, reply, out):
        self.status_label.setText("")
        self._offer_verdict(reply, out)

    def _offer_verdict(self, reply, out):
        verdict, reason = out
        ret = QMessageBox.question(
            self, "AI 判定建议",
            f"建议判定为「{verdict}」。\n依据：{reason}\n\n要写入这条回信吗？仍可稍后改判。")
        if ret != QMessageBox.Yes:
            return
        if not self.db.confirm_reply_verdict(reply.id, verdict):
            QMessageBox.warning(self, "提示", "该回信已不存在，请刷新后重试。")
            return
        self.main_window.data_changed.emit()
        self._reload()

    def _ai_failed(self, message: str):
        self.status_label.setText("")
        QMessageBox.warning(self, "AI 判定失败", str(message))

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
        self._fetch_failures: list[tuple[str, str]] = []
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("收信中…")
        self.stop_fetch_btn.setEnabled(True)
        self.status_label.setText("")
        self._fetch_worker = FetchWorker(mailboxes, editor_emails, lookback_days, self)
        self._fetch_worker.progress.connect(lambda msg: self.status_label.setText(msg))
        self._fetch_worker.mailbox_result.connect(self._on_mailbox_result)
        self._fetch_worker.mailbox_failed.connect(self._on_mailbox_failed)
        self._fetch_worker.all_done.connect(self._on_fetch_done)
        self._fetch_worker.start()

    def _on_stop_fetch(self):
        if self._fetch_worker is not None and self._fetch_worker.isRunning():
            self._fetch_worker.stop()
            self.stop_fetch_btn.setEnabled(False)
            self.status_label.setText("正在停止收信……")

    def _on_mailbox_failed(self, address: str, error: str):
        self._fetch_failures.append((address, error))

    def _on_mailbox_result(self, address: str, results: list):
        res = ingest_results(self.db, address, results)
        self._new_count += res.new_replies
        self._invalid_count += res.invalid_marks

    def _on_fetch_done(self):
        self._fetch_worker = None
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("立即收信")
        self.stop_fetch_btn.setEnabled(False)
        text = f"本次新到 {self._new_count} 封"
        if self._invalid_count:
            text += f"，标记失效邮箱 {self._invalid_count} 个"
        failures = getattr(self, "_fetch_failures", [])
        if failures:
            addresses = "、".join(address for address, _error in failures)
            text += f"；{len(failures)} 个邮箱收信失败：{addresses}"
        self.status_label.setText(text)
        self.main_window.data_changed.emit()
        self._reload()
