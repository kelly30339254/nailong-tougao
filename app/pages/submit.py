"""投稿方案页：上卡片「稿件与邮件」+ 下卡片「选择收稿编辑」+ SendWorker 发信。"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QDateTime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QComboBox, QCheckBox, QPushButton, QTableWidget, QTableWidgetItem,
    QFrame, QPlainTextEdit, QFileDialog, QMessageBox, QAbstractItemView,
    QHeaderView, QTabBar, QProgressBar, QScrollArea, QDateTimeEdit,
)

from ..models import Submission
from ..letter import build_letter
from ..docx_reader import read_docx_text, count_cjk_words
from ..workers import SendWorker
from ..widgets import mk_item
from .manuscripts import CATEGORIES, READER_GROUPS, STYLES

EMOTIONS = ["甜", "虐", "爽", "燃", "暖", "轻松"]
TEMP_ITEM_DATA = -1  # 文稿下拉末项"临时选择 .docx"


class SubmitPage(QWidget):
    def __init__(self, db, store, main_window):
        super().__init__()
        self.db = db
        self.store = store
        self.main_window = main_window
        self._send_worker: SendWorker | None = None
        self._temp_file_path = ""          # 临时选择的 docx（不入库）
        self._checked_ids: set[int] = set()  # 勾选的编辑 id（跨标签页保留）
        self._current_editors: list = []   # 当前标签页+关键词过滤后的编辑
        self._success = self._failed = self._skipped = 0

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(12)
        layout.addWidget(self._build_mail_card())
        layout.addWidget(self._build_editors_card(), 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self.refresh()

    # ---------- 上卡片：稿件与邮件 ----------
    def _build_mail_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(8)
        title = QLabel("稿件与邮件")
        title.setObjectName("cardTitle")
        box.addWidget(title)

        # 选择文稿
        ms_row = QHBoxLayout()
        ms_row.addWidget(QLabel("选择文稿"))
        self.manuscript_combo = QComboBox()
        self.manuscript_combo.currentIndexChanged.connect(self._on_manuscript_changed)
        ms_row.addWidget(self.manuscript_combo, 1)
        box.addLayout(ms_row)

        # 作品信息两列网格
        grid = QGridLayout()
        grid.setSpacing(8)
        self.title_edit = QLineEdit()
        self.words_edit = QLineEdit()
        self.category_combo = QComboBox()
        self.category_combo.setEditable(True)
        self.category_combo.addItems(CATEGORIES)
        self.reader_combo = QComboBox()
        self.reader_combo.addItems(READER_GROUPS)
        self.emotion_combo = QComboBox()
        self.emotion_combo.addItems(EMOTIONS)
        self.style_combo = QComboBox()
        self.style_combo.addItems(STYLES)
        self.genre_edit = QLineEdit()
        self.genre_edit.setPlaceholderText("如：悬疑、言情、世情…")

        fields = [
            ("作品名称 *", self.title_edit), ("作品字数 *", self.words_edit),
            ("作品分类", self.category_combo), ("读者分类", self.reader_combo),
            ("读者情绪", self.emotion_combo), ("作品风格", self.style_combo),
            ("作品类型", self.genre_edit),
        ]
        for i, (label_text, widget) in enumerate(fields):
            row, col = divmod(i, 2)
            grid.addWidget(QLabel(label_text), row, col * 2)
            grid.addWidget(widget, row, col * 2 + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        box.addLayout(grid)

        # 邮件主题与正文
        box.addWidget(QLabel("邮件主题"))
        self.subject_edit = QLineEdit()
        self.subject_edit.setPlaceholderText("可手填，或点「生成投稿信」自动填充")
        box.addWidget(self.subject_edit)
        box.addWidget(QLabel("邮件正文"))
        self.body_edit = QPlainTextEdit()
        self.body_edit.setMinimumHeight(100)
        self.body_edit.setMaximumHeight(140)
        box.addWidget(self.body_edit)

        letter_row = QHBoxLayout()
        letter_row.addStretch()
        letter_btn = QPushButton("生成投稿信")
        letter_btn.clicked.connect(self._on_build_letter)
        letter_row.addWidget(letter_btn)
        box.addLayout(letter_row)
        return card

    # ---------- 下卡片：选择收稿编辑 ----------
    def _build_editors_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(8)
        title = QLabel("选择收稿编辑")
        title.setObjectName("cardTitle")
        box.addWidget(title)

        self.tab_bar = QTabBar()
        self.tab_bar.setExpanding(False)
        self.tab_bar.addTab("全部编辑")
        self.tab_bar.addTab("收藏分类")
        self.tab_bar.currentChanged.connect(self._reload_editors_table)
        box.addWidget(self.tab_bar)

        tool = QHBoxLayout()
        tool.setSpacing(8)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("在当前结果中筛选…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._reload_editors_table)
        tool.addWidget(self.filter_edit, 1)
        self.checked_label = QLabel("已选: 0 家")
        tool.addWidget(self.checked_label)
        self.select_all_check = QCheckBox("全选当前结果")
        self.select_all_check.toggled.connect(self._on_select_all)
        tool.addWidget(self.select_all_check)
        clear_btn = QPushButton("清空勾选")
        clear_btn.clicked.connect(self._on_clear_checked)
        tool.addWidget(clear_btn)
        box.addLayout(tool)

        info = QFrame()
        info.setObjectName("infoBar")
        info_layout = QHBoxLayout(info)
        info_layout.setContentsMargins(12, 6, 12, 6)
        info_text = QLabel("保存投递时，同平台重复编辑与小黑屋会自动跳过，避免一稿多投。")
        info_text.setObjectName("infoBarText")
        info_layout.addWidget(info_text)
        box.addWidget(info)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            ["", "序", "编辑名称", "平台", "邮箱", "7日已投", "历史投递", "回复", "过稿"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setMinimumHeight(260)
        self.table.itemChanged.connect(self._on_item_changed)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        for col in (0, 1, 5, 6, 7, 8):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setStretchLastSection(False)
        box.addWidget(self.table, 1)

        # 底部：开始/定时/停止 + 进度条 + 日志
        run_row = QHBoxLayout()
        self.start_btn = QPushButton("开始投稿")
        self.start_btn.setObjectName("primaryBtn")
        self.start_btn.clicked.connect(self._on_start)
        run_row.addWidget(self.start_btn)
        self.schedule_edit = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        self.schedule_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.schedule_edit.setMinimumDateTime(QDateTime.currentDateTime())
        self.schedule_edit.setCalendarPopup(True)
        run_row.addWidget(self.schedule_edit)
        self.schedule_btn = QPushButton("定时投递")
        self.schedule_btn.clicked.connect(self._on_schedule)
        run_row.addWidget(self.schedule_btn)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        run_row.addWidget(self.stop_btn)
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        run_row.addWidget(self.progress_bar, 1)
        box.addLayout(run_row)

        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMinimumHeight(100)
        self.log_edit.setMaximumHeight(120)
        box.addWidget(self.log_edit)
        return card

    # ---------- refresh ----------
    def refresh(self):
        self._reload_manuscript_combo()
        self._reload_editors_table()

    def _reload_manuscript_combo(self):
        current_id = self.manuscript_combo.currentData()
        self.manuscript_combo.blockSignals(True)
        self.manuscript_combo.clear()
        for i, m in enumerate(self.db.list_manuscripts()):
            display = f"{m.title} ({m.word_count}字)"
            self.manuscript_combo.addItem(display, m.id)
            self.manuscript_combo.setItemData(i, display, Qt.ToolTipRole)
        self.manuscript_combo.addItem("＋临时选择 .docx 文件…", TEMP_ITEM_DATA)
        idx = self.manuscript_combo.findData(current_id)
        self.manuscript_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.manuscript_combo.blockSignals(False)
        # 首次进入且表单为空时，自动带出当前选中文稿的信息
        data = self.manuscript_combo.currentData()
        if not self.title_edit.text() and data and data != TEMP_ITEM_DATA:
            self._on_manuscript_changed(self.manuscript_combo.currentIndex())

    def _tab_editors(self) -> list:
        # 默认排除小黑屋与失效邮箱
        if self.tab_bar.currentIndex() == 1:  # 收藏分类
            editors = self.db.list_editors(favorites_only=True)
        else:
            editors = self.db.list_editors()
        return [e for e in editors if not e.email_invalid]

    def _reload_editors_table(self):
        keyword = self.filter_edit.text().strip().lower()
        editors = self._tab_editors()
        if keyword:
            editors = [e for e in editors if keyword in (e.name or "").lower()
                       or keyword in (e.email or "").lower()
                       or keyword in (e.platform or "").lower()
                       or keyword in (e.genres or "").lower()]
        self._current_editors = editors

        # 每个编辑的投递统计（一次取全量，内存聚合）
        stats: dict[int, dict] = {}
        for s in self.db.list_submissions():
            st = stats.setdefault(s.editor_id, {"total": 0, "replied": 0, "passed": 0})
            st["total"] += 1
            if s.reply_status and s.reply_status != "无":
                st["replied"] += 1
                if s.reply_status == "过稿":
                    st["passed"] += 1

        self.table.blockSignals(True)
        self.table.setRowCount(0)
        if not editors:
            self.table.setRowCount(1)
            item = QTableWidgetItem("没有可选的编辑")
            item.setTextAlignment(Qt.AlignCenter)
            item.setForeground(Qt.gray)
            self.table.setItem(0, 0, item)
            self.table.setSpan(0, 0, 1, 9)
            self.table.blockSignals(False)
            self._update_checked_label()
            return

        self.table.setRowCount(len(editors))
        for row, e in enumerate(editors):
            check_item = QTableWidgetItem()
            check_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            check_item.setCheckState(Qt.Checked if e.id in self._checked_ids else Qt.Unchecked)
            check_item.setData(Qt.UserRole, e.id)
            self.table.setItem(row, 0, check_item)

            seq = mk_item(str(row + 1), Qt.AlignCenter)
            self.table.setItem(row, 1, seq)
            for col, text in ((2, e.name), (3, e.platform), (4, e.email)):
                self.table.setItem(row, col, mk_item(text or ""))
            st = stats.get(e.id, {"total": 0, "replied": 0, "passed": 0})
            last7 = self.db.count_editor_last_days(e.id, 7)
            for col, num in ((5, last7), (6, st["total"]), (7, st["replied"]), (8, st["passed"])):
                self.table.setItem(row, col, mk_item(str(num), Qt.AlignCenter))
        self.table.blockSignals(False)
        self._update_checked_label()

    # ---------- 勾选 ----------
    def _on_item_changed(self, item: QTableWidgetItem):
        if item.column() != 0:
            return
        editor_id = item.data(Qt.UserRole)
        if editor_id is None:
            return
        if item.checkState() == Qt.Checked:
            self._checked_ids.add(editor_id)
        else:
            self._checked_ids.discard(editor_id)
        self._update_checked_label()

    def _update_checked_label(self):
        self.checked_label.setText(f"已选: {len(self._checked_ids)} 家")

    def _on_select_all(self, checked: bool):
        for e in self._current_editors:
            if checked:
                self._checked_ids.add(e.id)
            else:
                self._checked_ids.discard(e.id)
        self._reload_editors_table()

    def _on_clear_checked(self):
        self._checked_ids.clear()
        self.select_all_check.setChecked(False)
        self._reload_editors_table()

    # ---------- 文稿选择 ----------
    def _on_manuscript_changed(self, index: int):
        data = self.manuscript_combo.itemData(index)
        if data is None:
            return
        if data == TEMP_ITEM_DATA:
            path, _ = QFileDialog.getOpenFileName(self, "临时选择文稿", "", "Word 文档 (*.docx)")
            if not path:
                self._reload_manuscript_combo()
                return
            try:
                text = read_docx_text(path)
            except Exception as exc:
                QMessageBox.warning(self, "读取失败", f"无法读取文件：{exc}")
                self._reload_manuscript_combo()
                return
            self._temp_file_path = path
            self.title_edit.setText(os.path.splitext(os.path.basename(path))[0])
            self.words_edit.setText(str(count_cjk_words(text)))
            return
        manuscript = self.db.get_manuscript(data)
        if manuscript is None:
            return
        self._temp_file_path = ""
        self.title_edit.setText(manuscript.title)
        self.words_edit.setText(str(manuscript.word_count or ""))
        self.category_combo.setCurrentText(manuscript.category or "")
        if manuscript.reader_group in READER_GROUPS:
            self.reader_combo.setCurrentText(manuscript.reader_group)
        if manuscript.emotion in EMOTIONS:
            self.emotion_combo.setCurrentText(manuscript.emotion)
        if manuscript.style in STYLES:
            self.style_combo.setCurrentText(manuscript.style)
        self.genre_edit.setText(manuscript.genre_type or "")

    def _current_manuscript_id(self) -> int | None:
        data = self.manuscript_combo.currentData()
        return data if data and data != TEMP_ITEM_DATA else None

    def _current_attachment(self) -> str:
        mid = self._current_manuscript_id()
        if mid:
            m = self.db.get_manuscript(mid)
            return (m.file_path or "") if m else ""
        return self._temp_file_path

    # ---------- 生成投稿信 ----------
    def _on_build_letter(self):
        title = self.title_edit.text().strip()
        words = self.words_edit.text().strip()
        category = self.category_combo.currentText().strip()
        if not title or not words:
            QMessageBox.warning(self, "提示", "请先填写作品名称和字数")
            return
        if self.subject_edit.text().strip() or self.body_edit.toPlainText().strip():
            ret = QMessageBox.question(self, "确认覆盖", "已有邮件内容，确定用生成的投稿信覆盖吗？")
            if ret != QMessageBox.Yes:
                return
        subject_tpl, body_tpl = self.store.get_letter_template()
        subject, body = build_letter(title, words, category, "老师",
                                     subject_tpl, body_tpl)
        self.subject_edit.setText(subject)
        self.body_edit.setPlainText(body)

    # ---------- 开始投稿 / 定时投递 ----------
    def _log(self, message: str):
        self.log_edit.appendPlainText(message)

    def _validate_ready(self) -> bool:
        """开始投稿/定时投递共用的前置校验，不通过时弹提示。"""
        mailboxes = [m for m in self.store.load_mailboxes() if m.enabled and m.address]
        if not mailboxes:
            QMessageBox.warning(self, "提示", "还没有已启用的发信邮箱，请先到设置页配置。")
            self.main_window.navigate("settings")
            return False
        title = self.title_edit.text().strip()
        words = self.words_edit.text().strip()
        if not title or not words:
            QMessageBox.warning(self, "提示", "请填写作品名称和字数。")
            return False
        if not self.subject_edit.text().strip() or not self.body_edit.toPlainText().strip():
            QMessageBox.warning(self, "提示", "邮件主题或正文为空，可点「生成投稿信」自动填充。")
            return False
        if not self._checked_ids:
            QMessageBox.warning(self, "提示", "请至少勾选一家收稿编辑。")
            return False
        return True

    def _build_jobs(self, status: str, scheduled_at: str = "") -> tuple[list, int]:
        """逐勾选编辑构建 job（一稿一投/同平台/小黑屋/失效跳过），返回 (jobs, skipped)。"""
        one_draft, _interval, _daily = self.store.get_strategy()
        manuscript_id = self._current_manuscript_id()
        attachment = self._current_attachment() or None
        subject = self.subject_edit.text().strip()
        body = self.body_edit.toPlainText().strip()

        editors_by_id = {e.id: e for e in self.db.list_editors(include_blacklisted=True)}
        jobs: list[dict] = []
        seen_platforms: set[str] = set()
        skipped = 0
        for editor_id in sorted(self._checked_ids):
            editor = editors_by_id.get(editor_id)
            if editor is None or editor.blacklisted or editor.email_invalid:
                continue
            if one_draft and manuscript_id and self.db.find_pending(manuscript_id, editor.id):
                self._log(f"跳过（未回复）：{editor.name}")
                skipped += 1
                continue
            platform = (editor.platform or "").strip()
            if platform and platform in seen_platforms:
                self._log(f"跳过（同平台重复）：{editor.name}")
                skipped += 1
                continue
            if platform:
                seen_platforms.add(platform)
            sid = self.db.insert_submission(Submission(
                manuscript_id=manuscript_id, editor_id=editor.id,
                to_email=editor.email, subject=subject, body=body,
                status=status, scheduled_at=scheduled_at))
            jobs.append({"submission_id": sid, "to": editor.email,
                         "subject": subject, "body": body,
                         "attachment_path": attachment})
        return jobs, skipped

    def _on_schedule(self):
        """定时投递：同样校验与跳过逻辑，状态插 定时待发。"""
        if not self._validate_ready():
            return
        scheduled_at = self.schedule_edit.dateTime().toString("yyyy-MM-dd HH:mm") + ":00"
        jobs, skipped = self._build_jobs("定时待发", scheduled_at)
        if not jobs:
            self._log("没有可投递的编辑（全部被跳过）。")
            return
        time_text = self.schedule_edit.dateTime().toString("yyyy-MM-dd HH:mm")
        self._log(f"已加入定时队列 {len(jobs)} 封，将于 {time_text} 发出"
                  + (f"（跳过 {skipped} 家）" if skipped else ""))
        self.main_window.data_changed.emit()
        self._reload_editors_table()

    def _on_start(self):
        if not self._validate_ready():
            return

        # 单日上限：每邮箱今日已发 < 该邮箱单日上限 才参与本轮
        mailboxes = [m for m in self.store.load_mailboxes() if m.enabled and m.address]
        available = [m for m in mailboxes if self.db.count_today(m.address) < m.daily_limit]
        if not available:
            QMessageBox.warning(self, "提示", "所有已启用邮箱今日投递已达上限，请明天再试。")
            return

        jobs, self._skipped = self._build_jobs("待发")
        if not jobs:
            self._log("没有可投递的编辑（全部被跳过）。")
            return

        _one_draft, interval_seconds, _daily = self.store.get_strategy()
        self._success = self._failed = 0
        self.progress_bar.setMaximum(len(jobs))
        self.progress_bar.setValue(0)
        self._log(f"开始投递 {len(jobs)} 封，使用 {len(available)} 个邮箱轮转……")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        self._send_worker = SendWorker(available, jobs, interval_seconds, self)
        self._send_worker.progress.connect(self._on_send_progress)
        self._send_worker.item_done.connect(self._on_item_done)
        self._send_worker.all_done.connect(self._on_all_done)
        self._send_worker.start()

    def _on_stop(self):
        if self._send_worker is not None:
            self._send_worker.stop()
            self.stop_btn.setEnabled(False)
            self._log("正在停止……")

    def _on_send_progress(self, current: int, total: int, message: str):
        self.progress_bar.setMaximum(max(total, 1))
        self.progress_bar.setValue(current)
        self._log(message)

    def _on_item_done(self, submission_id: int, ok: bool, error: str, mailbox_address: str):
        if submission_id > 0:
            self.db.update_status(submission_id, "已发" if ok else "失败")
            if mailbox_address:
                self.db.update_from_mailbox(submission_id, mailbox_address)
        if ok:
            self._success += 1
        else:
            self._failed += 1
            self._log(f"发送失败（{mailbox_address}）：{error}")

    def _on_all_done(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._log(f"全部完成：成功 {self._success} 失败 {self._failed} 跳过 {self._skipped}")
        self._send_worker = None
        self.main_window.data_changed.emit()
        self._reload_editors_table()
