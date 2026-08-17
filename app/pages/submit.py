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
)

from ..models import Submission
from ..letter import build_letter, personalize_letter, vary_letter
from ..docx_reader import read_docx_text, count_cjk_words
from ..workers import SendWorker, AiRankWorker
from ..widgets import mk_item, PageBar
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
        self._temp_file_path = ""          # 临时选择的 docx（不入库）
        self._checked_ids: set[int] = set()  # 勾选的编辑 id（跨标签页保留）
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
        self.genre_combo = QComboBox()
        self.genre_combo.setMinimumContentsLength(8)
        self.genre_combo.addItem("全部类型")
        self.genre_combo.setToolTip("按稿件类型叠加筛选，例如先选短篇，再选正常收稿")
        self.genre_combo.currentIndexChanged.connect(self._on_filter_changed)
        filters.addWidget(self.genre_combo)
        self.direction_combo = QComboBox()
        self.direction_combo.setMinimumContentsLength(8)
        self.direction_combo.addItem("全部方向")
        self.direction_combo.currentIndexChanged.connect(self._on_filter_changed)
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
        self._fill_combo(self.genre_combo, "全部类型", self.db.distinct_genres())
        self._fill_combo(self.direction_combo, "全部方向", self.db.distinct_directions())
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
        genre = self._combo_value(self.genre_combo, "全部类型")
        direction = self._combo_value(self.direction_combo, "全部方向")
        status = self._combo_value(self.status_combo, "全部状态")
        editors = self._tab_editors()
        if platform:
            editors = [e for e in editors if (e.platform or "").strip() == platform]
        if genre:
            editors = [e for e in editors if genre in (e.genres or "")]
        if direction:
            editors = [e for e in editors if direction in (e.directions or "")]
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
        else:
            self._checked_ids.discard(editor_id)
        self._update_checked_label()

    def _update_checked_label(self):
        n = len(self._checked_ids)
        extra = ""
        if n:
            # 实时预览将跳过数（与 _build_jobs 相同规则）：同平台重复 + 一稿一投
            editors_by_id = getattr(self, "_editors_lookup", None) or {
                e.id: e for e in self.db.list_editors(include_blacklisted=True)}
            manuscript_id = self._current_manuscript_id()
            one_draft, _interval, _daily = self.store.get_strategy()
            protect = bool(one_draft and manuscript_id is not None)
            seen_platforms: set[str] = set()
            skip_dup = skip_protect = 0
            for editor_id in sorted(self._checked_ids):
                editor = editors_by_id.get(editor_id)
                if editor is None or editor.blacklisted or editor.email_invalid:
                    skip_dup += 1
                    continue
                platform = (editor.platform or "").strip()
                if platform and platform in seen_platforms:
                    skip_dup += 1
                    continue
                if platform:
                    seen_platforms.add(platform)
                if protect and self.db.find_pending(manuscript_id, editor_id) is not None:
                    skip_protect += 1
            parts = []
            if skip_dup:
                parts.append(f"同平台将跳过 {skip_dup} 家")
            if skip_protect:
                parts.append(f"一稿一投将跳过 {skip_protect} 家")
            if parts:
                extra = f"（{'，'.join(parts)}）"
        self.checked_label.setText(f"已选: {n} 家{extra}")

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
        data = self.manuscript_combo.itemData(index)
        if data is None:
            return
        if data == TEMP_ITEM_DATA:
            path, _ = QFileDialog.getOpenFileName(self, "临时选择文稿", "", "文稿文件 (*.docx *.txt)")
            if not path:
                self._reload_manuscript_combo()
                return
            try:
                text = read_manuscript_text(path)
            except Exception as exc:
                QMessageBox.warning(self, "读取失败", f"无法读取文件：{exc}")
                self._reload_manuscript_combo()
                return
            self._temp_file_path = path
            self.title_edit.setText(os.path.splitext(os.path.basename(path))[0])
            self.words_edit.setText(str(count_cjk_words(text)))
            self._reload_editors_table()
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
        self._ai_rank = {}
        self.ai_pick_btn.setEnabled(False)
        self._reload_editors_table()

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
        one_draft, _interval, _daily = self.store.get_strategy()
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
        """定时投递：同样校验与跳过逻辑，状态插 定时待发。"""
        if not self._validate_ready():
            return
        scheduled_at = self.schedule_edit.dateTime().toString("yyyy-MM-dd HH:mm") + ":00"
        jobs, skipped = self._build_jobs("定时待发", scheduled_at)

        def finish(ready_jobs, skipped_n):
            if not ready_jobs:
                self._log("没有可投递的编辑（全部被跳过）。")
                return
            time_text = self.schedule_edit.dateTime().toString("yyyy-MM-dd HH:mm")
            self._log(f"已加入定时队列 {len(ready_jobs)} 封，将于 {time_text} 发出"
                      + (f"（跳过 {skipped_n} 家）" if skipped_n else ""))
            self.main_window.data_changed.emit()
            self._reload_editors_table()

        self._run_jobs_after_vary(jobs, skipped, finish)

    def _on_start(self):
        if not self._validate_ready():
            return

        # 单日上限：每邮箱今日已发 < 该邮箱单日上限 才参与本轮
        mailboxes = [m for m in self.store.load_mailboxes() if m.enabled and m.address]
        available = [m for m in mailboxes if self.db.count_today(m.address) < m.daily_limit]
        if not available:
            QMessageBox.warning(self, "提示", "所有已启用邮箱今日投递已达上限，请明天再试。")
            return

        jobs, skipped = self._build_jobs("待发")

        def start_send(ready_jobs, skipped_n):
            if not ready_jobs:
                self._log("没有可投递的编辑（全部被跳过）。")
                return
            _one_draft, interval_seconds, _daily = self.store.get_strategy()
            self._success = self._failed = 0
            self._skipped = skipped_n
            self._failed_jobs = []
            self.progress_bar.setMaximum(len(ready_jobs))
            self.progress_bar.setValue(0)
            self._log(f"开始投递 {len(ready_jobs)} 封，使用 {len(available)} 个邮箱轮转……")
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self._send_worker = SendWorker(available, ready_jobs, interval_seconds, self, db=self.db)
            self._send_worker.progress.connect(self._on_send_progress)
            self._send_worker.item_done.connect(self._on_item_done)
            self._send_worker.all_done.connect(self._on_all_done)
            self._last_jobs = ready_jobs
            self._send_worker.start()

        self._run_jobs_after_vary(jobs, skipped, start_send)

    def _on_stop(self):
        if self._send_worker is not None:
            self._send_worker.stop()
            self.stop_btn.setEnabled(False)
            self._log("正在停止……")

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
