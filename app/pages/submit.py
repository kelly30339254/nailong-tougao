"""投稿方案页：上卡片「稿件与邮件」+ 下卡片「选择收稿编辑」+ SendWorker 发信。"""
from __future__ import annotations

import os
import re
from email.utils import make_msgid

from PySide6.QtCore import Qt, QDateTime
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QComboBox, QCheckBox, QPushButton, QTableWidget, QTableWidgetItem,
    QFrame, QPlainTextEdit, QFileDialog, QMessageBox, QAbstractItemView,
    QHeaderView, QTabBar, QProgressBar, QScrollArea, QDateTimeEdit,
    QSpinBox, QDialog,
)

from ..models import BatchManuscript, LetterTemplate, Submission
from ..letter import (
    build_letter, personalize_letter, validate_letter_template, vary_letter,
)
from ..docx_reader import read_docx_text, count_cjk_words
from ..workers import BatchSendCoordinator, SendWorker, AiRankWorker
from ..widgets import mk_item, MultiSelectComboBox, PageBar
from ..batching import BatchPlanner, format_duration_range
from .. import smart_match
from ..models import CATEGORIES, READER_GROUPS, EMOTIONS, STYLES
from .manuscripts import read_manuscript_text
TEMP_ITEM_DATA = -1  # 文稿下拉末项"临时选择 .docx"


class SubmitPage(QWidget):
    def __init__(self, db, store, main_window):
        super().__init__()
        self.db = db
        self.store = store
        self.main_window = main_window
        self._send_worker: SendWorker | None = None
        self._batch_worker: BatchSendCoordinator | None = None
        self._batch_id: int | None = None
        self._batch_items: list[BatchManuscript] = []
        self._active_batch_item = -1
        self._batch_loading = False
        self._temp_file_path = ""          # 临时选择的 docx（不入库）
        self._checked_ids: set[int] = set()  # 勾选的编辑 id（跨标签页保留）
        self._checked_order: list[int] = []  # 当前稿件的配置顺序
        self._current_editors: list = []   # 当前标签页+关键词过滤后的编辑
        self._success = self._failed = self._skipped = 0
        self._ai_rank: dict[int, tuple[int, str, bool]] = {}
        self._ai_worker: AiRankWorker | None = None
        self._sort_key = ""
        self._sort_desc = False

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(12)
        layout.addWidget(self._build_batch_card())
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

    def _build_batch_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(8)
        head = QHBoxLayout()
        title = QLabel("投稿批次（最多 10 篇）")
        title.setObjectName("cardTitle")
        head.addWidget(title)
        self.batch_combo = QComboBox()
        self.batch_combo.currentIndexChanged.connect(self._on_batch_changed)
        head.addWidget(self.batch_combo, 1)
        new_btn = QPushButton("新建草稿")
        new_btn.clicked.connect(self._on_new_batch)
        head.addWidget(new_btn)
        self.batch_save_btn = QPushButton("保存草稿")
        self.batch_save_btn.clicked.connect(self._save_active_batch)
        head.addWidget(self.batch_save_btn)
        delete_btn = QPushButton("删除草稿")
        delete_btn.clicked.connect(self._on_delete_batch)
        head.addWidget(delete_btn)
        box.addLayout(head)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("批次名称"))
        self.batch_name_edit = QLineEdit()
        self.batch_name_edit.setPlaceholderText("例如：8 月短篇投稿")
        name_row.addWidget(self.batch_name_edit, 1)
        self.batch_status_label = QLabel("尚未选择批次")
        self.batch_status_label.setObjectName("hintText")
        name_row.addWidget(self.batch_status_label)
        box.addLayout(name_row)

        add_row = QHBoxLayout()
        self.library_add_combo = QComboBox()
        self.library_add_combo.setPlaceholderText("从文稿库选择")
        add_row.addWidget(self.library_add_combo, 1)
        add_btn = QPushButton("加入批次")
        add_btn.clicked.connect(self._on_add_batch_manuscript)
        add_row.addWidget(add_btn)
        up_btn = QPushButton("上移")
        up_btn.clicked.connect(lambda: self._move_batch_manuscript(-1))
        add_row.addWidget(up_btn)
        down_btn = QPushButton("下移")
        down_btn.clicked.connect(lambda: self._move_batch_manuscript(1))
        add_row.addWidget(down_btn)
        remove_btn = QPushButton("移除")
        remove_btn.clicked.connect(self._on_remove_batch_manuscript)
        add_row.addWidget(remove_btn)
        box.addLayout(add_row)

        self.batch_table = QTableWidget(0, 5)
        self.batch_table.setHorizontalHeaderLabels(
            ["顺序", "文稿", "收件编辑", "发件邮箱", "模板组"])
        self.batch_table.verticalHeader().setVisible(False)
        self.batch_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.batch_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.batch_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.batch_table.setMaximumHeight(190)
        self.batch_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.batch_table.cellClicked.connect(self._on_batch_row_clicked)
        box.addWidget(self.batch_table)

        config_row = QHBoxLayout()
        config_row.addWidget(QLabel("本篇发件邮箱"))
        self.batch_mailbox_combo = MultiSelectComboBox("请选择邮箱")
        self.batch_mailbox_combo.selectionChanged.connect(self._on_batch_config_changed)
        config_row.addWidget(self.batch_mailbox_combo, 1)
        config_row.addWidget(QLabel("本篇模板组"))
        self.batch_template_combo = MultiSelectComboBox("请选择模板")
        self.batch_template_combo.selectionChanged.connect(self._on_batch_config_changed)
        config_row.addWidget(self.batch_template_combo, 1)
        review_btn = QPushButton("审核模板组")
        review_btn.clicked.connect(self._review_batch_templates)
        config_row.addWidget(review_btn)
        self.ai_count_spin = QSpinBox()
        self.ai_count_spin.setRange(2, 10)
        self.ai_count_spin.setValue(5)
        self.ai_count_spin.setSuffix(" 套")
        config_row.addWidget(self.ai_count_spin)
        ai_btn = QPushButton("AI 生成候选")
        ai_btn.setToolTip("一次仅上传本篇标题和结构化标签，不上传正文")
        ai_btn.clicked.connect(self._on_generate_batch_ai)
        config_row.addWidget(ai_btn)
        box.addLayout(config_row)
        return card

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
        self.title_edit.setReadOnly(True)
        self.words_edit.setReadOnly(True)
        self.genre_edit.setReadOnly(True)
        for combo in (self.category_combo, self.reader_combo,
                      self.emotion_combo, self.style_combo):
            combo.setEnabled(False)

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
        preview_hint = QLabel("下方为单封预览；批次启动时以本篇最终模板组生成并冻结每封内容。")
        preview_hint.setObjectName("hintText")
        preview_hint.setWordWrap(True)
        box.addWidget(preview_hint)
        box.addWidget(QLabel("邮件主题预览"))
        self.subject_edit = QLineEdit()
        self.subject_edit.setPlaceholderText("可手填，或点「用模板生成」自动填充")
        box.addWidget(self.subject_edit)
        box.addWidget(QLabel("邮件正文"))
        self.body_edit = QPlainTextEdit()
        self.body_edit.setMinimumHeight(100)
        self.body_edit.setMaximumHeight(140)
        box.addWidget(self.body_edit)

        letter_row = QHBoxLayout()
        letter_row.addStretch()
        letter_btn = QPushButton("用模板生成")
        letter_btn.setToolTip("用设置里的投稿信模板填入当前作品信息，不调用 AI")
        letter_btn.clicked.connect(self._on_build_letter)
        letter_row.addWidget(letter_btn)
        ai_letter_btn = QPushButton("按本篇生成一封")
        ai_letter_btn.setToolTip("根据当前作品标签让 AI 写这一封信（需接入 API）")
        ai_letter_btn.clicked.connect(self._on_ai_letter)
        letter_row.addWidget(ai_letter_btn)
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
        box.addWidget(self.tab_bar)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.platform_combo = QComboBox()
        self.platform_combo.setMinimumContentsLength(8)
        self.platform_combo.addItem("全部平台")
        self.platform_combo.currentIndexChanged.connect(self._on_filter_changed)
        filters.addWidget(self.platform_combo)
        self.genre_combo = MultiSelectComboBox("全部类型")
        self.genre_combo.setToolTip("同组任一标签匹配；与方向、平台、状态同时满足")
        self.genre_combo.selectionChanged.connect(self._on_filter_changed)
        filters.addWidget(self.genre_combo)
        self.direction_combo = MultiSelectComboBox("全部方向")
        self.direction_combo.selectionChanged.connect(self._on_filter_changed)
        filters.addWidget(self.direction_combo)
        self.status_combo = QComboBox()
        self.status_combo.setMinimumContentsLength(8)
        self.status_combo.addItem("全部状态")
        self.status_combo.currentIndexChanged.connect(self._on_filter_changed)
        filters.addWidget(self.status_combo)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("再按名称 / 邮箱 / 平台关键词筛选…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._on_filter_changed)
        filters.addWidget(self.filter_edit, 1)
        box.addLayout(filters)

        smart_row = QHBoxLayout()
        smart_row.setSpacing(8)
        self.smart_check = QCheckBox("智选排序（不使用AI）")
        self.smart_check.setToolTip("原版规则匹配：按题材/篇幅/情绪等加权排序，不调用网络，不会自动勾选")
        self.smart_check.toggled.connect(self._reload_editors_table)
        smart_row.addWidget(self.smart_check)
        self.ai_smart_btn = QPushButton("AI智选")
        self.ai_smart_btn.setToolTip("调用已接入的大模型评估当前筛选结果。未接入 API 时不可用。不会自动勾选。")
        self.ai_smart_btn.clicked.connect(self._on_ai_smart)
        smart_row.addWidget(self.ai_smart_btn)
        self.ai_pick_btn = QPushButton("勾选AI推荐")
        self.ai_pick_btn.setToolTip("把最近一次 AI智选标记为推荐的编辑加入勾选")
        self.ai_pick_btn.clicked.connect(self._on_ai_pick)
        self.ai_pick_btn.setEnabled(False)
        smart_row.addWidget(self.ai_pick_btn)
        smart_row.addStretch()
        box.addLayout(smart_row)

        tool = QHBoxLayout()
        tool.setSpacing(8)
        self.checked_label = QLabel("已选: 0 家")
        tool.addWidget(self.checked_label)
        self.select_all_check = QCheckBox("全选当前结果")
        self.select_all_check.setToolTip("勾选当前筛选结果中的全部编辑，再点「开始投稿」即可批量投递")
        self.select_all_check.toggled.connect(self._on_select_all)
        tool.addWidget(self.select_all_check)
        fav_btn = QPushButton("收藏当前结果")
        fav_btn.setToolTip("把当前筛选出的编辑一键加入收藏分类")
        fav_btn.clicked.connect(self._on_fav_filtered)
        tool.addWidget(fav_btn)
        clear_btn = QPushButton("清空勾选")
        clear_btn.clicked.connect(self._on_clear_checked)
        tool.addWidget(clear_btn)
        tool.addStretch()
        box.addLayout(tool)

        info = QFrame()
        info.setObjectName("infoBar")
        info_layout = QHBoxLayout(info)
        info_layout.setContentsMargins(12, 6, 12, 6)
        info_text = QLabel("保存投递时，同平台重复编辑与小黑屋会自动跳过，避免一稿多投。")
        info_text.setObjectName("infoBarText")
        info_layout.addWidget(info_text)
        box.addWidget(info)

        self.table = QTableWidget(0, 13)
        self.table.setHorizontalHeaderLabels(
            ["", "序", "编辑名称", "平台", "邮箱", "稿件类型", "收稿方向",
             "收稿状态", "7日已投", "历史投递", "回复", "过稿", "智选匹配"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setMinimumHeight(260)
        self.table.itemChanged.connect(self._on_item_changed)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        for col in (0, 1, 2, 3, 5, 7, 8, 9, 10, 11):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        for col in (4, 6, 12):
            header.setSectionResizeMode(col, QHeaderView.Stretch)
        header.setMinimumSectionSize(50)
        header.setStretchLastSection(False)
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._on_editor_header)
        box.addWidget(self.table, 1)
        self.page_bar = PageBar(self)
        self.page_bar.changed.connect(self._render_editors_page)
        box.addWidget(self.page_bar)
        self.tab_bar.currentChanged.connect(self._on_tab_changed)

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
        self.schedule_btn.setToolTip("到点发送，期间请保持本软件运行（可缩到托盘，不要退出）")
        self.schedule_btn.clicked.connect(self._on_schedule)
        run_row.addWidget(self.schedule_btn)
        sched_hint = QLabel("定时需软件开着，可缩托盘")
        sched_hint.setObjectName("hintText")
        run_row.addWidget(sched_hint)
        self.stop_btn = QPushButton("暂停")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        run_row.addWidget(self.stop_btn)
        self.cancel_batch_btn = QPushButton("取消批次")
        self.cancel_batch_btn.clicked.connect(self._on_cancel_active_batch)
        run_row.addWidget(self.cancel_batch_btn)
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

    # ---------- 批次草稿 ----------
    def _reload_batches(self, preferred_id=None):
        current_id = preferred_id if preferred_id is not None else self._batch_id
        batches = self.db.list_batches(include_finished=False)
        self._batch_loading = True
        self.batch_combo.blockSignals(True)
        self.batch_combo.clear()
        labels = {
            "draft": "草稿", "scheduled": "已定时", "running": "发送中",
            "paused": "已暂停", "waiting": "等待处理",
        }
        for batch in batches:
            self.batch_combo.addItem(
                f"{batch.name} · {labels.get(batch.status, batch.status)}", batch.id)
        index = self.batch_combo.findData(current_id)
        self.batch_combo.setCurrentIndex(index if index >= 0 else (0 if batches else -1))
        self.batch_combo.blockSignals(False)
        self._batch_loading = False
        self._load_batch(self.batch_combo.currentData())

    def _load_batch(self, batch_id):
        self._batch_loading = True
        self._batch_id = int(batch_id) if batch_id else None
        batch = self.db.get_batch(self._batch_id) if self._batch_id else None
        self._batch_items = self.db.list_batch_manuscripts(self._batch_id) if batch else []
        self._active_batch_item = -1
        self.batch_name_edit.setText(batch.name if batch else "")
        status_names = {
            "draft": "草稿可编辑", "scheduled": f"定时：{batch.scheduled_at}" if batch else "",
            "running": "正在发送", "paused": "已暂停，可继续",
            "waiting": "等待限额或邮箱处理", "completed": "已完成",
            "cancelled": "已取消",
        }
        self.batch_status_label.setText(status_names.get(batch.status, "尚未选择批次") if batch else "尚未选择批次")
        editable = bool(batch and batch.status == "draft")
        self.batch_name_edit.setEnabled(editable)
        self.batch_save_btn.setEnabled(editable)
        self._reload_batch_selectors()
        self._reload_manuscript_combo()
        self._refresh_batch_table()
        self._batch_loading = False
        if self._batch_items:
            self._select_batch_item(0)
        else:
            self._checked_ids.clear()
            self._checked_order.clear()
            self._reload_editors_table()

    def _reload_batch_selectors(self):
        mailboxes = [m for m in self.store.load_mailboxes() if m.enabled and m.address]
        self.batch_mailbox_combo.set_options([
            (m.address, m.mailbox_id) for m in mailboxes])
        self.batch_template_combo.set_options([
            (template.name, str(template.id)) for template in self.db.list_letter_templates()])
        manuscripts = self.db.list_manuscripts()
        current = self.library_add_combo.currentData()
        self.library_add_combo.blockSignals(True)
        self.library_add_combo.clear()
        for manuscript in manuscripts:
            display = f"{manuscript.title}（{manuscript.word_count}字）"
            self.library_add_combo.addItem(display, manuscript.id)
            self.library_add_combo.setItemData(
                self.library_add_combo.count() - 1, display, Qt.ToolTipRole)
        index = self.library_add_combo.findData(current)
        if index >= 0:
            self.library_add_combo.setCurrentIndex(index)
        self.library_add_combo.blockSignals(False)

    def _store_current_batch_item(self):
        index = self._active_batch_item
        if self._batch_loading or not (0 <= index < len(self._batch_items)):
            return
        item = self._batch_items[index]
        ordered = [eid for eid in self._checked_order if eid in self._checked_ids]
        ordered.extend(sorted(self._checked_ids.difference(ordered)))
        item.target_editor_ids = ordered
        item.mailbox_ids = self.batch_mailbox_combo.checked_values()
        item.template_ids = [int(value) for value in
                             self.batch_template_combo.checked_values()]

    def _select_batch_item(self, index: int):
        if not (0 <= index < len(self._batch_items)):
            return
        self._store_current_batch_item()
        self._batch_loading = True
        self._active_batch_item = index
        item = self._batch_items[index]
        self._checked_ids = set(item.target_editor_ids)
        self._checked_order = list(item.target_editor_ids)
        self.batch_mailbox_combo.set_checked_values(item.mailbox_ids)
        self.batch_template_combo.set_checked_values([str(i) for i in item.template_ids])
        combo_index = self.manuscript_combo.findData(item.manuscript_id)
        self.manuscript_combo.blockSignals(True)
        self.manuscript_combo.setCurrentIndex(combo_index)
        self.manuscript_combo.blockSignals(False)
        self.batch_table.selectRow(index)
        self._batch_loading = False
        self._show_manuscript(item.manuscript_id)
        self._reload_editors_table()

    def _refresh_batch_table(self):
        self.batch_table.setRowCount(len(self._batch_items))
        mailbox_names = {m.mailbox_id: m.address for m in self.store.load_mailboxes()}
        template_names = {t.id: t.name for t in self.db.list_letter_templates()}
        for row, item in enumerate(self._batch_items):
            manuscript = self.db.get_manuscript(item.manuscript_id)
            values = [
                str(row + 1), manuscript.title if manuscript else "已删除文稿",
                f"{len(item.target_editor_ids)} 位",
                "、".join(mailbox_names.get(mid, "失效邮箱") for mid in item.mailbox_ids) or "未选择",
                ("、".join(template_names.get(tid, "失效模板") for tid in item.template_ids)
                 + (f" + AI {sum(1 for x in item.ai_templates if x.get('selected', True))} 套"
                    if item.ai_templates else "")) or "未选择",
            ]
            for col, value in enumerate(values):
                self.batch_table.setItem(row, col, mk_item(value))

    def _on_batch_changed(self):
        if self._batch_loading:
            return
        self._save_active_batch(silent=True)
        self._load_batch(self.batch_combo.currentData())

    def _on_new_batch(self):
        self._save_active_batch(silent=True)
        batch_id = self.db.create_batch()
        self._reload_batches(batch_id)

    def _on_delete_batch(self):
        batch = self.db.get_batch(self._batch_id) if self._batch_id else None
        if batch is None:
            return
        if batch.status != "draft":
            QMessageBox.information(self, "不能删除", "运行、定时或暂停批次请使用“取消批次”。")
            return
        if QMessageBox.question(self, "删除草稿", f"确定删除“{batch.name}”吗？") != QMessageBox.Yes:
            return
        self.db.delete_batch(batch.id)
        self._batch_id = None
        self._reload_batches()

    def _save_active_batch(self, *_args, silent=False):
        if not self._batch_id:
            if not silent:
                QMessageBox.information(self, "提示", "请先新建批次草稿。")
            return False
        batch = self.db.get_batch(self._batch_id)
        if batch is None or batch.status != "draft":
            return False
        self._store_current_batch_item()
        try:
            self.db.update_batch(
                self._batch_id,
                name=self.batch_name_edit.text().strip() or batch.name)
            self.db.save_batch_configuration(self._batch_id, self._batch_items)
            active = max(0, self._active_batch_item)
            self._batch_items = self.db.list_batch_manuscripts(self._batch_id)
            self._reload_manuscript_combo()
            self._refresh_batch_table()
            if self._batch_items:
                self._select_batch_item(min(active, len(self._batch_items) - 1))
            if not silent:
                self._log("批次草稿已保存。")
            return True
        except Exception as exc:
            if not silent:
                QMessageBox.warning(self, "保存失败", str(exc))
            return False

    def _on_add_batch_manuscript(self):
        manuscript_id = self.library_add_combo.currentData()
        if not manuscript_id:
            QMessageBox.information(self, "提示", "文稿库为空，请先添加文稿。")
            return
        if not self._batch_id:
            self._on_new_batch()
        batch = self.db.get_batch(self._batch_id)
        if batch is None or batch.status != "draft":
            QMessageBox.information(self, "提示", "当前批次不可编辑，请新建草稿。")
            return
        self._store_current_batch_item()
        if len(self._batch_items) >= 10:
            QMessageBox.warning(self, "已达上限", "每个批次最多 10 篇稿件。")
            return
        if any(item.manuscript_id == manuscript_id for item in self._batch_items):
            QMessageBox.information(self, "提示", "这篇文稿已在当前批次中。")
            return
        self._batch_items.append(BatchManuscript(
            batch_id=self._batch_id, manuscript_id=manuscript_id,
            position=len(self._batch_items)))
        self._active_batch_item = len(self._batch_items) - 1
        self._save_active_batch(silent=True)

    def _on_remove_batch_manuscript(self):
        index = self._active_batch_item
        if not (0 <= index < len(self._batch_items)):
            return
        self._batch_items.pop(index)
        self._active_batch_item = min(index, len(self._batch_items) - 1)
        self._save_active_batch(silent=True)
        if not self._batch_items:
            self._reload_manuscript_combo()
            self._refresh_batch_table()

    def _move_batch_manuscript(self, delta: int):
        index = self._active_batch_item
        target = index + delta
        if not (0 <= index < len(self._batch_items) and 0 <= target < len(self._batch_items)):
            return
        self._store_current_batch_item()
        self._batch_items[index], self._batch_items[target] = (
            self._batch_items[target], self._batch_items[index])
        self._active_batch_item = target
        self._save_active_batch(silent=True)

    def _on_batch_row_clicked(self, row: int, _column: int):
        self._select_batch_item(row)

    def _on_batch_config_changed(self):
        if self._batch_loading:
            return
        self._store_current_batch_item()
        self._refresh_batch_table()

    def _on_generate_batch_ai(self):
        if not (0 <= self._active_batch_item < len(self._batch_items)):
            QMessageBox.information(self, "提示", "请先在批次中选择一篇稿件。")
            return
        from ..ai_ui import require_ai_config
        from ..workers import AiCallWorker
        from .. import ai_smart
        cfg = require_ai_config(self, self.store, self.main_window)
        if cfg is None:
            return
        item = self._batch_items[self._active_batch_item]
        manuscript = self.db.get_manuscript(item.manuscript_id)
        if manuscript is None:
            return
        metadata = {
            "title": manuscript.title, "word_count": manuscript.word_count,
            "category": manuscript.category, "genre_type": manuscript.genre_type,
            "reader_group": manuscript.reader_group, "emotion": manuscript.emotion,
            "style": manuscript.style,
        }
        count = self.ai_count_spin.value()
        self._log(f"正在为《{manuscript.title}》一次生成 {count} 套 AI 候选（不上传正文）……")
        worker = AiCallWorker(
            lambda: ai_smart.generate_batch_letter_templates(cfg, metadata, count), self)

        def ok(candidates):
            item.ai_templates = list(candidates)
            self._review_batch_templates()

        worker.finished_ok.connect(ok)
        worker.failed.connect(lambda message: QMessageBox.warning(self, "生成失败", str(message)))
        self._batch_ai_worker = worker
        worker.start()

    def _review_batch_templates(self):
        if not (0 <= self._active_batch_item < len(self._batch_items)):
            return
        item = self._batch_items[self._active_batch_item]
        rows: list[dict] = []
        for template in self.db.list_letter_templates():
            rows.append({
                "kind": "library", "id": template.id, "name": template.name,
                "subject": template.subject, "body": template.body,
                "selected": template.id in item.template_ids,
                "original_subject": template.subject, "original_body": template.body,
            })
        for candidate in item.ai_templates:
            rows.append({"kind": "candidate", **candidate})
        dlg = QDialog(self)
        dlg.setWindowTitle("审核本篇最终轮换模板组")
        dlg.resize(920, 520)
        box = QVBoxLayout(dlg)
        note = QLabel("勾选最终轮换组；可直接修改名称、主题和正文。缺少必需占位符时不能保存。")
        note.setObjectName("hintText")
        note.setWordWrap(True)
        box.addWidget(note)
        table = QTableWidget(len(rows), 5)
        table.setHorizontalHeaderLabels(["选用", "名称", "主题", "正文", "来源"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for row, candidate in enumerate(rows):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            check.setCheckState(Qt.Checked if candidate.get("selected") else Qt.Unchecked)
            check.setData(Qt.UserRole, candidate)
            table.setItem(row, 0, check)
            table.setItem(row, 1, QTableWidgetItem(str(candidate.get("name", ""))))
            table.setItem(row, 2, QTableWidgetItem(str(candidate.get("subject", ""))))
            table.setItem(row, 3, QTableWidgetItem(str(candidate.get("body", ""))))
            table.setItem(row, 4, mk_item("模板库" if candidate.get("kind") == "library" else "本批 AI"))
        box.addWidget(table, 1)
        save_library = QCheckBox("将勾选且经过编辑的批次候选另存到模板库")
        box.addWidget(save_library)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(dlg.reject)
        buttons.addWidget(cancel)
        accept = QPushButton("保存最终模板组")
        accept.setObjectName("primaryBtn")
        buttons.addWidget(accept)
        box.addLayout(buttons)

        def apply_review():
            template_ids: list[int] = []
            candidates: list[dict] = []
            problems: list[str] = []
            for row in range(table.rowCount()):
                if table.item(row, 0).checkState() != Qt.Checked:
                    continue
                original = table.item(row, 0).data(Qt.UserRole) or {}
                name = table.item(row, 1).text().strip() or f"候选 {row + 1}"
                subject = table.item(row, 2).text()
                body = table.item(row, 3).text()
                issues = validate_letter_template(subject, body)
                if issues:
                    problems.append(f"{name}：{'；'.join(issues)}")
                    continue
                unchanged_library = (
                    original.get("kind") == "library"
                    and subject == original.get("original_subject")
                    and body == original.get("original_body"))
                if unchanged_library:
                    template_ids.append(int(original["id"]))
                    continue
                candidate = {
                    "name": name, "subject": subject, "body": body,
                    "selected": True,
                    "origin": "ai" if original.get("kind") == "candidate" else "edited-library",
                }
                if save_library.isChecked():
                    template_ids.append(self.db.insert_letter_template(LetterTemplate(
                        name=name, subject=subject, body=body, origin="user")))
                else:
                    candidates.append(candidate)
            if problems:
                QMessageBox.warning(dlg, "模板不能使用", "\n".join(problems))
                return
            if not template_ids and not candidates:
                QMessageBox.warning(dlg, "提示", "至少勾选一套最终模板。")
                return
            item.template_ids = list(dict.fromkeys(template_ids))
            item.ai_templates = candidates
            self._batch_template_combo_refresh(item)
            self._save_active_batch(silent=True)
            dlg.accept()

        accept.clicked.connect(apply_review)
        dlg.exec()

    def _batch_template_combo_refresh(self, item):
        self._batch_loading = True
        self.batch_template_combo.set_options([
            (template.name, str(template.id)) for template in self.db.list_letter_templates()])
        self.batch_template_combo.set_checked_values([str(i) for i in item.template_ids])
        self._batch_loading = False

    # ---------- refresh ----------
    def refresh(self):
        self._reload_batches()
        self._reload_filter_combos()
        self._reload_editors_table()

    def _fill_combo(self, combo: QComboBox, all_label: str, values: list[str]):
        current = combo.currentText() or all_label
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(all_label)
        combo.addItems(values)
        combo.setCurrentText(current if combo.findText(current) >= 0 else all_label)
        combo.blockSignals(False)

    def _reload_filter_combos(self):
        self._fill_combo(self.platform_combo, "全部平台", self.db.distinct_platforms())
        self.genre_combo.set_options(self.db.distinct_genres())
        self.direction_combo.set_options(self.db.distinct_directions())
        self._fill_combo(self.status_combo, "全部状态", self.db.distinct_statuses())

    def _on_tab_changed(self, *_args):
        if not hasattr(self, "page_bar"):
            return
        self.page_bar.reset_page()
        self._reload_editors_table()

    def _on_filter_changed(self, *_args):
        if not hasattr(self, "page_bar"):
            return
        self.select_all_check.blockSignals(True)
        self.select_all_check.setChecked(False)
        self.select_all_check.blockSignals(False)
        self.page_bar.reset_page()
        self._reload_editors_table()

    def _reload_manuscript_combo(self):
        current_id = self.manuscript_combo.currentData()
        self.manuscript_combo.blockSignals(True)
        self.manuscript_combo.clear()
        for item in self._batch_items:
            m = self.db.get_manuscript(item.manuscript_id)
            if m is None:
                continue
            display = f"{m.title} ({m.word_count}字)"
            self.manuscript_combo.addItem(display, m.id)
            self.manuscript_combo.setItemData(
                self.manuscript_combo.count() - 1, display, Qt.ToolTipRole)
        idx = self.manuscript_combo.findData(current_id)
        self.manuscript_combo.setCurrentIndex(idx if idx >= 0 else (0 if self.manuscript_combo.count() else -1))
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

    def _manuscript_query(self) -> dict:
        manuscript = None
        manuscript_id = self._current_manuscript_id()
        if manuscript_id:
            manuscript = self.db.get_manuscript(manuscript_id)
        return smart_match.manuscript_query(
            manuscript,
            self.category_combo.currentText(),
            self.genre_edit.text(),
            self.reader_combo.currentText(),
            self.emotion_combo.currentText(),
            self.style_combo.currentText())

    def _editor_match(self, editor) -> tuple[int, str]:
        return smart_match.match_editor(editor, self._manuscript_query())

    def _combo_value(self, combo: QComboBox, all_label: str) -> str:
        text = combo.currentText().strip()
        return "" if text in ("", all_label) else text

    def _reload_editors_table(self):
        keyword = self.filter_edit.text().strip().lower()
        platform = self._combo_value(self.platform_combo, "全部平台")
        genres = {value.casefold() for value in self.genre_combo.checked_values()}
        directions = {value.casefold() for value in self.direction_combo.checked_values()}
        status = self._combo_value(self.status_combo, "全部状态")
        editors = self._tab_editors()
        if platform:
            editors = [e for e in editors if (e.platform or "").strip() == platform]
        if genres:
            editors = [e for e in editors
                       if genres.intersection(
                           tag.casefold() for tag in self.db._split_tags(e.genres or ""))]
        if directions:
            editors = [e for e in editors
                       if directions.intersection(
                           tag.casefold() for tag in self.db._split_tags(e.directions or ""))]
        if status:
            editors = [e for e in editors if (e.status or "").strip().startswith(status)
                       or status in (e.status or "")]
        if keyword:
            editors = [e for e in editors if keyword in (e.name or "").lower()
                       or keyword in (e.email or "").lower()
                       or keyword in (e.platform or "").lower()
                       or keyword in (e.genres or "").lower()
                       or keyword in (e.directions or "").lower()
                       or keyword in (e.status or "").lower()]
        match_info = {editor.id: self._editor_match(editor) for editor in editors}
        if self.smart_check.isChecked() or self._ai_rank:
            editors = [editor for editor in editors
                       if not (editor.status or "").strip().startswith("停止收稿")]
            if self._ai_rank:
                editors.sort(key=lambda e: (
                    0 if self._ai_rank.get(e.id, (0, "", False))[2] else 1,
                    -self._ai_rank.get(e.id, (0, "", False))[0],
                    smart_match.sort_key(e, match_info[e.id][0])))
            elif self.smart_check.isChecked():
                editors.sort(key=lambda e: smart_match.sort_key(e, match_info[e.id][0]))
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

        last7_map = self.db.count_editors_last_days([e.id for e in editors if e.id], 7)
        self._editors_lookup = {e.id: e for e in self.db.list_editors(include_blacklisted=True)}
        self._editor_stats = stats
        self._last7_map = last7_map
        self._match_info = match_info
        if self._sort_key:
            self._apply_editor_sort()
        self.page_bar.set_total(len(editors))
        self._render_editors_page()

    def _apply_editor_sort(self):
        key = self._sort_key
        stats = getattr(self, "_editor_stats", {})
        last7 = getattr(self, "_last7_map", {})
        match_info = getattr(self, "_match_info", {})

        def value_of(e):
            if key == "last7":
                return last7.get(e.id, 0)
            if key == "total":
                return stats.get(e.id, {}).get("total", 0)
            if key == "replied":
                return stats.get(e.id, {}).get("replied", 0)
            if key == "passed":
                return stats.get(e.id, {}).get("passed", 0)
            if key == "score":
                return match_info.get(e.id, (0, ""))[0]
            return getattr(e, key, "") or ""

        self._current_editors.sort(key=lambda e: value_of(e), reverse=self._sort_desc)

    def _on_editor_header(self, col: int):
        keys = {
            2: "name", 3: "platform", 4: "email", 5: "genres",
            6: "directions", 7: "status", 8: "last7", 9: "total",
            10: "replied", 11: "passed", 12: "score",
        }
        key = keys.get(col)
        if not key:
            return
        if self._sort_key == key:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_key = key
            self._sort_desc = True
        self.table.horizontalHeader().setSortIndicator(
            col, Qt.DescendingOrder if self._sort_desc else Qt.AscendingOrder)
        self.page_bar.reset_page()
        if self._current_editors:
            self._apply_editor_sort()
            self._render_editors_page()

    def _render_editors_page(self):
        editors = self._current_editors
        stats = getattr(self, "_editor_stats", {})
        last7_map = getattr(self, "_last7_map", {})
        match_info = getattr(self, "_match_info", {})
        start = self.page_bar.offset
        page = editors[start:start + self.page_bar.page_size]
        self.table.blockSignals(True)
        self.table.clearSpans()
        self.table.setRowCount(0)
        if not page:
            self.table.setRowCount(1)
            item = QTableWidgetItem("没有可选的编辑")
            item.setTextAlignment(Qt.AlignCenter)
            item.setForeground(Qt.gray)
            self.table.setItem(0, 0, item)
            self.table.setSpan(0, 0, 1, 13)
            self.table.blockSignals(False)
            self._update_checked_label()
            return
        self.table.setRowCount(len(page))
        for row, e in enumerate(page):
            check_item = QTableWidgetItem()
            check_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            check_item.setCheckState(Qt.Checked if e.id in self._checked_ids else Qt.Unchecked)
            check_item.setData(Qt.UserRole, e.id)
            self.table.setItem(row, 0, check_item)

            seq = mk_item(str(start + row + 1), Qt.AlignCenter)
            self.table.setItem(row, 1, seq)
            for col, text in ((2, e.name), (3, e.platform), (4, e.email)):
                self.table.setItem(row, col, mk_item(text or ""))

            genres = (e.genres or "").strip() or "未标注"
            directions = (e.directions or "").strip() or "未标注"
            status = (e.status or "").strip() or "未核实"
            genres_item = mk_item(genres)
            genres_item.setToolTip(f"稿件类型：{genres}")
            self.table.setItem(row, 5, genres_item)
            directions_preview = directions if len(directions) <= 24 else directions[:24] + "…"
            directions_item = mk_item(directions_preview)
            directions_item.setToolTip(f"收稿方向：{directions}")
            self.table.setItem(row, 6, directions_item)
            status_item = mk_item(status, Qt.AlignCenter)
            if status == "停止收稿":
                status_item.setForeground(Qt.red)
            elif status == "正常收稿":
                status_item.setForeground(QColor("#2F9E44"))
            else:
                status_item.setForeground(QColor("#D97706"))
            status_item.setToolTip(f"收稿状态：{status}")
            self.table.setItem(row, 7, status_item)

            st = stats.get(e.id, {"total": 0, "replied": 0, "passed": 0})
            last7 = last7_map.get(e.id, 0)
            for col, num in ((8, last7), (9, st["total"]), (10, st["replied"]), (11, st["passed"])):
                self.table.setItem(row, col, mk_item(str(num), Qt.AlignCenter))
            score, reason = match_info.get(e.id, (0, ""))
            if e.id in self._ai_rank:
                ai_score, ai_reason, recommend = self._ai_rank[e.id]
                tag = "推荐 · " if recommend else ""
                match_text = f"AI {ai_score}分 · {tag}{ai_reason}"
            elif self.smart_check.isChecked():
                match_text = f"{score}分 · {reason}"
            else:
                match_text = "勾选智选排序（不使用AI），或点击 AI智选"
            match_item = mk_item(match_text)
            match_item.setToolTip(
                f"匹配结果：{reason}\n稿件类型：{genres}\n"
                f"收稿方向：{directions}\n收稿状态：{status}")
            self.table.setItem(row, 12, match_item)
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
            if editor_id not in self._checked_order:
                self._checked_order.append(editor_id)
        else:
            self._checked_ids.discard(editor_id)
            if editor_id in self._checked_order:
                self._checked_order.remove(editor_id)
        self._update_checked_label()

    def _update_checked_label(self):
        n = len(self._checked_ids)
        extra = ""
        if n:
            # 实时预览将跳过数（与 _build_jobs 相同规则）：同平台重复 + 一稿一投
            editors_by_id = getattr(self, "_editors_lookup", None) or {
                e.id: e for e in self.db.list_editors(include_blacklisted=True)}
            manuscript_id = self._current_manuscript_id()
            policy = self.store.get_strategy()
            protect = bool(policy.one_draft_protection and manuscript_id is not None)
            seen_platforms: set[str] = set()
            seen_addresses: set[str] = set()
            skip_dup = skip_protect = 0
            preview_order = [eid for eid in self._checked_order if eid in self._checked_ids]
            preview_order.extend(sorted(self._checked_ids.difference(preview_order)))
            for editor_id in preview_order:
                editor = editors_by_id.get(editor_id)
                if editor is None or editor.blacklisted or editor.email_invalid:
                    skip_dup += 1
                    continue
                address = (editor.email or "").strip().casefold()
                if not address or address in seen_addresses:
                    skip_dup += 1
                    continue
                platform = (editor.platform or "").strip().casefold()
                if platform and platform in seen_platforms:
                    skip_dup += 1
                    continue
                if protect and self.db.find_pending(manuscript_id, editor_id) is not None:
                    skip_protect += 1
                    continue
                seen_addresses.add(address)
                if platform:
                    seen_platforms.add(platform)
            parts = []
            if skip_dup:
                parts.append(f"同平台/失效/重复将跳过 {skip_dup} 家")
            if skip_protect:
                parts.append(f"一稿一投将跳过 {skip_protect} 家")
            if parts:
                extra = f"（{'，'.join(parts)}）"
        self.checked_label.setText(f"已选: {n} 家{extra}")

    def _on_select_all(self, checked: bool):
        for e in self._current_editors:
            if checked:
                self._checked_ids.add(e.id)
                if e.id not in self._checked_order:
                    self._checked_order.append(e.id)
            else:
                self._checked_ids.discard(e.id)
                if e.id in self._checked_order:
                    self._checked_order.remove(e.id)
        self._reload_editors_table()

    def _on_clear_checked(self):
        self._checked_ids.clear()
        self._checked_order.clear()
        self.select_all_check.setChecked(False)
        self._reload_editors_table()

    def _on_fav_filtered(self):
        editors = [e for e in self._current_editors if e.id]
        if not editors:
            QMessageBox.information(self, "提示", "当前筛选没有可收藏的编辑。")
            return
        unfav = [e for e in editors if not e.favorite]
        if not unfav:
            QMessageBox.information(self, "提示", "当前筛选结果都已在收藏分类中。")
            return
        ret = QMessageBox.question(
            self, "加入收藏",
            f"将当前筛选出的 {len(unfav)} 位编辑加入收藏分类？")
        if ret != QMessageBox.Yes:
            return
        self.db.set_favorites([e.id for e in unfav], True)
        self.main_window.data_changed.emit()
        self._reload_editors_table()
        QMessageBox.information(self, "已收藏", f"已加入收藏分类 {len(unfav)} 位，可在「收藏分类」标签查看。")

    def _on_ai_smart(self):
        cfg = self.store.get_ai_config()
        if not cfg.configured():
            QMessageBox.information(
                self, "尚未接入 AI",
                "请先到「设置 → AI 接口」填写 API Key。\n未接入时请使用「智选排序（不使用AI）」。")
            self.main_window.navigate("settings")
            return
        editors = list(self._current_editors)
        if not editors:
            QMessageBox.information(self, "提示", "当前筛选没有可评估的编辑。")
            return
        if self._ai_worker is not None and self._ai_worker.isRunning():
            QMessageBox.information(self, "提示", "AI智选正在进行，请稍候。")
            return
        self.ai_smart_btn.setEnabled(False)
        self.ai_smart_btn.setText("AI智选中…")
        self._log(f"AI智选：正在评估 {len(editors)} 位编辑……")
        self._ai_worker = AiRankWorker(cfg, self._manuscript_query(), editors, self)
        self._ai_worker.finished_ok.connect(self._on_ai_rank_ok)
        self._ai_worker.failed.connect(self._on_ai_rank_fail)
        self._ai_worker.finished.connect(self._on_ai_rank_done)
        self._ai_worker.start()

    def _on_ai_rank_ok(self, result):
        self._ai_rank = dict(result or {})
        rec = sum(1 for _sid, item in self._ai_rank.items() if item[2])
        self._log(f"AI智选完成：评估 {len(self._ai_rank)} 位，推荐 {rec} 位。")
        self.ai_pick_btn.setEnabled(rec > 0)
        self._reload_editors_table()

    def _on_ai_rank_fail(self, message: str):
        self._log(f"AI智选失败：{message}")
        QMessageBox.warning(self, "AI智选失败", str(message) + "\n可改用「智选排序（不使用AI）」。")

    def _on_ai_rank_done(self):
        self.ai_smart_btn.setEnabled(True)
        self.ai_smart_btn.setText("AI智选")

    def _on_ai_pick(self):
        added = 0
        for eid, item in self._ai_rank.items():
            if item[2] and eid not in self._checked_ids:
                self._checked_ids.add(eid)
                self._checked_order.append(eid)
                added += 1
        self._reload_editors_table()
        if added:
            self._log(f"已勾选 AI 推荐 {added} 位。")
            QMessageBox.information(
                self, "已勾选",
                f"已勾选 AI 推荐 {added} 位，请再核对收稿方向和状态后再开始投稿。")
        else:
            QMessageBox.information(self, "提示", "没有新的 AI 推荐可勾选。")

    # ---------- 文稿选择 ----------
    def _on_manuscript_changed(self, index: int):
        if self._batch_loading:
            return
        data = self.manuscript_combo.itemData(index)
        if data is None:
            return
        for item_index, item in enumerate(self._batch_items):
            if item.manuscript_id == data:
                self._select_batch_item(item_index)
                return

    def _show_manuscript(self, manuscript_id: int):
        manuscript = self.db.get_manuscript(manuscript_id)
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
        self._ai_rank = {}
        self.ai_pick_btn.setEnabled(False)

    def _current_manuscript_id(self) -> int | None:
        data = self.manuscript_combo.currentData()
        return data if data else None

    def _current_attachment(self) -> str:
        mid = self._current_manuscript_id()
        if mid:
            m = self.db.get_manuscript(mid)
            return (m.file_path or "") if m else ""
        return ""

    # ---------- 生成投稿信 ----------
    def _on_build_letter(self):
        title = self.title_edit.text().strip()
        words = self.words_edit.text().strip()
        category = self.category_combo.currentText().strip()
        if not title or not words:
            QMessageBox.warning(self, "提示", "请先填写作品名称和字数")
            return
        if not re.fullmatch(r"\d+", words):
            QMessageBox.warning(self, "提示", "作品字数请填写数字。")
            return
        if self.subject_edit.text().strip() or self.body_edit.toPlainText().strip():
            ret = QMessageBox.question(self, "确认覆盖", "已有邮件内容，确定用生成的投稿信覆盖吗？")
            if ret != QMessageBox.Yes:
                return
        subject_tpl, body_tpl = self.store.get_letter_template()
        subject, body = build_letter(title, words, category, "{编辑称呼}",
                                     subject_tpl, body_tpl)
        self.subject_edit.setText(subject)
        self.body_edit.setPlainText(body)

    def _on_ai_letter(self):
        from ..ai_ui import require_ai_config
        from ..workers import AiCallWorker
        from .. import ai_smart
        title = self.title_edit.text().strip()
        words = self.words_edit.text().strip()
        if not title or not words:
            QMessageBox.warning(self, "提示", "请先填写作品名称和字数")
            return
        cfg = require_ai_config(self, self.store, self.main_window)
        if cfg is None:
            return
        if self.subject_edit.text().strip() or self.body_edit.toPlainText().strip():
            ret = QMessageBox.question(self, "确认覆盖", "已有邮件内容，确定用 AI 生成的这一封覆盖吗？")
            if ret != QMessageBox.Yes:
                return
        self._log("正在按本篇生成投稿信……")
        worker = AiCallWorker(
            lambda: ai_smart.generate_letter_for_work(cfg, self._manuscript_query() | {
                "title": title, "word_count": words,
            }), self)

        def ok(out):
            subject, body = out
            self.subject_edit.setText(subject)
            self.body_edit.setPlainText(body)
            self._log("已写入按本篇生成的投稿信，发送前请再通读一遍。")

        def fail(msg):
            QMessageBox.warning(self, "生成失败", str(msg))

        worker.finished_ok.connect(ok)
        worker.failed.connect(fail)
        self._ai_letter_worker = worker
        worker.start()

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
        if not re.fullmatch(r"\d+", words):
            QMessageBox.warning(self, "提示", "作品字数请填写数字。")
            return False
        if not self.subject_edit.text().strip() or not self.body_edit.toPlainText().strip():
            QMessageBox.warning(self, "提示", "邮件主题或正文为空，可点「用模板生成」或「按本篇生成一封」。")
            return False
        if not self._checked_ids:
            QMessageBox.warning(self, "提示", "请至少勾选一家收稿编辑。")
            return False
        return True

    def _build_jobs(self, status: str, scheduled_at: str = "") -> tuple[list, int]:
        """逐编辑原子建任务，并保存该编辑实际收到的个性化内容。"""
        policy = self.store.get_strategy()
        one_draft = policy.one_draft_protection
        manuscript_id = self._current_manuscript_id()
        if one_draft and not manuscript_id:
            self._log("提示：未关联文稿，本次一稿一投保护未生效（同一编辑可重复投递）。")
        attachment = self._current_attachment() or None
        base_subject = self.subject_edit.text().strip()
        base_body = self.body_edit.toPlainText().strip()

        editors_by_id = {e.id: e for e in self.db.list_editors(include_blacklisted=True)}
        jobs: list[dict] = []
        seen_platforms: set[str] = set()
        skipped = 0
        for editor_id in sorted(self._checked_ids):
            editor = editors_by_id.get(editor_id)
            if editor is None or editor.blacklisted or editor.email_invalid:
                continue
            platform = (editor.platform or "").strip()
            if platform and platform in seen_platforms:
                self._log(f"跳过（同平台重复）：{editor.name}")
                skipped += 1
                continue
            subject, body = personalize_letter(
                base_subject, base_body, editor.name or "编辑老师")
            subject, body = self._apply_letter_vary(
                subject, body, editor, manuscript_id, base_subject)
            message_id = make_msgid(domain="nailong.local")
            submission = Submission(
                manuscript_id=manuscript_id, editor_id=editor.id,
                to_email=editor.email, subject=subject, body=body,
                status=status, scheduled_at=scheduled_at, message_id=message_id)
            sid = self.db.insert_submission_if_allowed(
                submission, protect=bool(one_draft and manuscript_id is not None))
            if sid is None:
                self._log(f"跳过（一稿一投保护）：{editor.name}")
                skipped += 1
                continue
            if platform:
                seen_platforms.add(platform)
            jobs.append({"submission_id": sid, "to": editor.email,
                         "editor_name": editor.name or "编辑老师",
                         "subject": subject, "body": body,
                         "message_id": message_id,
                         "attachment_path": attachment})
        return jobs, skipped

    def _apply_letter_vary(self, subject: str, body: str, editor, manuscript_id, base_subject):
        # AI 微调在后台 Worker 里做，这里只做不卡界面的规则微调
        if self.store.get_letter_ai_vary() and self.store.get_ai_config().configured():
            return subject, body
        if self.store.get_letter_vary():
            seed = f"{editor.id}:{manuscript_id or 0}:{base_subject}:{editor.email}"
            subject, body = vary_letter(subject, body, seed)
        return subject, body

    def _run_jobs_after_vary(self, jobs: list, skipped: int, starter):
        """AI 微调走后台线程；否则直接进入发送/定时。"""
        cfg = self.store.get_ai_config()
        if self.store.get_letter_ai_vary() and cfg.configured() and jobs:
            from ..widgets import ProgressDialog
            from ..workers import VaryLettersWorker
            dlg = ProgressDialog("AI 微调投稿信", self)
            extra = f"字数={self.words_edit.text().strip()} 分类={self.category_combo.currentText().strip()}"
            worker = VaryLettersWorker(
                jobs, cfg, self.title_edit.text().strip(), extra, self, db=self.db)
            worker.progress.connect(dlg.set_progress)
            dlg.rejected.connect(worker.stop)

            def done(updated):
                dlg.accept()
                starter(updated, skipped)

            worker.finished_ok.connect(done)
            worker.start()
            dlg.exec()
            return
        starter(jobs, skipped)

    def _on_schedule(self):
        if not self._batch_id:
            QMessageBox.information(self, "提示", "请先新建批次并加入文稿。")
            return
        batch = self.db.get_batch(self._batch_id)
        if batch is None or batch.status != "draft":
            QMessageBox.information(self, "提示", "只有草稿批次可以设置启动时间。")
            return
        if not self._save_active_batch(silent=True):
            return
        scheduled_at = self.schedule_edit.dateTime().toString("yyyy-MM-dd HH:mm") + ":00"
        planner = BatchPlanner(self.db, self.store)
        preflight = planner.preflight(self._batch_id)
        if not self._confirm_preflight(preflight, scheduled_at=scheduled_at):
            return
        try:
            _final, ids = planner.activate(self._batch_id, scheduled_at=scheduled_at)
        except Exception as exc:
            QMessageBox.warning(self, "无法定时", str(exc))
            return
        self._log(f"批次已冻结 {len(ids)} 封，将于 {scheduled_at[:16]} 启动。")
        self.main_window.data_changed.emit()
        self._reload_batches(self._batch_id)

    def _on_start(self):
        if not self._batch_id:
            QMessageBox.information(self, "提示", "请先新建批次并从文稿库加入 1–10 篇稿件。")
            return
        batch = self.db.get_batch(self._batch_id)
        if batch is None:
            return
        if batch.status == "scheduled":
            QMessageBox.information(self, "已定时", f"该批次将在 {batch.scheduled_at} 启动。")
            return
        if batch.status == "running" and self._batch_worker is not None:
            QMessageBox.information(self, "提示", "该批次正在发送。")
            return
        if batch.status == "draft":
            if not self._save_active_batch(silent=True):
                return
            planner = BatchPlanner(self.db, self.store)
            preflight = planner.preflight(self._batch_id)
            if not self._confirm_preflight(preflight):
                return
            try:
                _final, ids = planner.activate(self._batch_id)
            except Exception as exc:
                QMessageBox.warning(self, "无法启动", str(exc))
                return
            self._skipped = preflight.skipped_total + max(0, preflight.total - len(ids))
        elif batch.status not in ("paused", "waiting", "running"):
            QMessageBox.information(self, "提示", "当前批次不能继续。")
            return
        self._start_batch_coordinator(self._batch_id)

    def _confirm_preflight(self, preflight, scheduled_at: str = "") -> bool:
        if preflight.errors:
            QMessageBox.warning(self, "预检未通过", "\n".join(preflight.errors))
            return False
        mailbox_names = {m.mailbox_id: m.address for m in self.store.load_mailboxes()}
        lines = []
        for item in preflight.manuscripts:
            skips = "、".join(f"{key} {value}" for key, value in item.skipped.items())
            lines.append(f"《{item.title}》：有效 {item.effective} 位"
                         + (f"；跳过 {skips}" if skips else ""))
        lines.append("")
        lines.append(f"实际总封数：{preflight.total}")
        lines.append("邮箱任务量：" + "；".join(
            f"{mailbox_names.get(mid, mid)} {count} 封"
            for mid, count in preflight.mailbox_counts.items()))
        lines.append("预计完成时间：" + format_duration_range(
            preflight.min_duration_seconds, preflight.max_duration_seconds))
        if scheduled_at:
            lines.append(f"整个批次启动时间：{scheduled_at}")
        lines.append("")
        lines.append("本地无上限、随机间隔和模板变化不能取消服务商限流，也不保证送达或规避风控。")
        if os.environ.get("NAILONG_SMOKE") or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return True
        return QMessageBox.question(
            self, "确认物化投稿批次", "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def _start_batch_coordinator(self, batch_id: int):
        if self._batch_worker is not None and self._batch_worker.isRunning():
            return
        mailboxes = self.store.load_mailboxes()
        pending = self.db.list_batch_submissions(
            batch_id, BatchSendCoordinator.PENDING_STATUSES)
        self._success = self._failed = 0
        self.progress_bar.setRange(0, max(1, len(pending)))
        self.progress_bar.setValue(0)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._log(f"批次开始：{len(pending)} 封；各邮箱首封并行，后续随机等待 1–3 分钟。")
        worker = BatchSendCoordinator(self.db, batch_id, mailboxes, self)
        worker.progress.connect(self._on_send_progress)
        worker.item_done.connect(self._on_batch_item_done)
        worker.mailbox_paused.connect(
            lambda mailbox_id, reason: self._log(f"邮箱已暂停：{mailbox_id}：{reason}"))
        worker.batch_done.connect(self._on_batch_done)
        self._batch_worker = worker
        worker.start()

    def _on_batch_item_done(self, submission_id: int, status: str,
                            error: str, mailbox_address: str):
        if status == "已发":
            self._success += 1
        elif status == "失败":
            self._failed += 1
            self._log(f"单封失败（{mailbox_address}）：{error}")

    def _on_batch_done(self, status: str):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._batch_worker = None
        labels = {"completed": "完成", "paused": "暂停", "waiting": "等待处理",
                  "cancelled": "取消"}
        self._log(f"批次{labels.get(status, status)}：成功 {self._success}，失败 {self._failed}。")
        self.main_window.data_changed.emit()
        self._reload_batches(self._batch_id)

    def _on_stop(self):
        if self._batch_worker is not None:
            self._batch_worker.pause()
            self.stop_btn.setEnabled(False)
            self._log("正在暂停；当前 SMTP 调用结束后不再领取新任务……")
        elif self._send_worker is not None:
            self._send_worker.stop()
            self.stop_btn.setEnabled(False)

    def _on_cancel_active_batch(self):
        if not self._batch_id:
            return
        if not (os.environ.get("NAILONG_SMOKE") or os.environ.get("QT_QPA_PLATFORM") == "offscreen"):
            if QMessageBox.question(
                    self, "取消批次", "取消后所有未发送任务都不会再领取，确定继续吗？") != QMessageBox.Yes:
                return
        if self._batch_worker is not None:
            self._batch_worker.cancel()
        else:
            self.db.cancel_batch(self._batch_id)
            self.main_window.data_changed.emit()
            self._reload_batches(self._batch_id)

    def _on_send_progress(self, current: int, total: int, message: str):
        self.progress_bar.setMaximum(max(total, 1))
        self.progress_bar.setValue(current)
        self._log(message)

    def _on_item_done(self, submission_id: int, ok: bool, error: str,
                      mailbox_address: str, skipped: bool = False):
        if submission_id > 0:
            if skipped:
                # 跳过（额度/状态变化）≠ 失败：不污染失败统计，保留可重试
                self.db.update_status(submission_id, "已跳过", sent_at="")
                self._skipped += 1
            else:
                self.db.update_status(
                    submission_id, "已发" if ok else "失败",
                    error=error if not ok else None)
                if mailbox_address:
                    self.db.update_from_mailbox(submission_id, mailbox_address)
        if ok:
            self._success += 1
        elif not skipped:
            self._failed += 1
            self._failed_jobs.append((submission_id, mailbox_address, error))
            self._log(f"发送失败（{mailbox_address}）：{error}")

    def _on_all_done(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._log(f"全部完成：成功 {self._success} 失败 {self._failed} 跳过 {self._skipped}")
        self._send_worker = None
        self.main_window.data_changed.emit()
        self._reload_editors_table()
        if os.environ.get("NAILONG_SMOKE") or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        lines = [f"成功 {self._success} 封，失败 {self._failed} 封，跳过 {self._skipped} 家"]
        fails = getattr(self, "_failed_jobs", [])
        if fails:
            lines.append("")
            for _sid, addr, err in fails[:12]:
                lines.append(f"· {addr}：{err}")
        box = QMessageBox(self)
        box.setWindowTitle("投递完成")
        box.setText("\n".join(lines))
        if fails:
            retry = box.addButton("一键重发失败件", QMessageBox.AcceptRole)
            box.addButton("关闭", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is retry:
                failed_ids = {sid for sid, _a, _e in fails if sid}
                jobs = [j for j in getattr(self, "_last_jobs", []) if j.get("submission_id") in failed_ids]
                if jobs:
                    self._failed_jobs = []
                    self._success = self._failed = 0
                    mailboxes = [m for m in self.store.load_mailboxes() if m.enabled and m.address]
                    self._send_worker = SendWorker(mailboxes, jobs, 0, self, db=self.db)
                    self._send_worker.item_done.connect(self._on_item_done)
                    self._send_worker.all_done.connect(self._on_all_done)
                    self.start_btn.setEnabled(False)
                    self._send_worker.start()
        else:
            box.exec()
