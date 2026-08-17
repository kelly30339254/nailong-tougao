"""编辑列表页：搜索/筛选、收藏/小黑屋、增删改、CSV 导入导出。"""
from __future__ import annotations

import csv
import io
import os
import re

from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtGui import QDesktopServices, QColor, QFontMetrics
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QCheckBox, QPushButton, QTableWidget, QTableWidgetItem, QFrame,
    QDialog, QFormLayout, QFileDialog, QMessageBox, QAbstractItemView,
    QHeaderView, QMenu,
)

from ..models import Editor
from ..widgets import mk_item, ProgressDialog
from ..icons import make_icon
from ..theme import theme_colors
from .. import updater
from ..workers import SyncEditorsWorker, ImportEditorsWorker

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DIRECTIONS_PREVIEW_LENGTH = 5
DIRECTIONS_COLUMN_WIDTH = 104
PAGE_SIZE = 50

CSV_HEADERS = ["名称", "平台", "邮箱", "题材", "收稿方向", "状态", "稿费", "来源", "备注"]
_HEADER_ALIASES = {
    "name": ("名称", "name"),
    "platform": ("平台", "platform"),
    "email": ("邮箱", "email"),
    "genres": ("题材", "genres"),
    "directions": ("收稿方向", "directions"),
    "status": ("状态", "status"),
    "fee_info": ("稿费", "fee_info"),
    "source_url": ("来源", "source_url"),
    "notes": ("备注", "notes"),
}


class EditorDialog(QDialog):
    """新增/编辑编辑对话框。"""

    def __init__(self, parent=None, editor: Editor | None = None):
        super().__init__(parent)
        self.setWindowTitle("编辑编辑信息" if editor and editor.id else "新增编辑")
        self.setMinimumWidth(420)
        self.editor = editor or Editor()

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(8)
        self.name_edit = QLineEdit(self.editor.name)
        self.name_edit.setPlaceholderText("必填，编辑或刊物名")
        form.addRow("名称 *", self.name_edit)
        self.platform_edit = QLineEdit(self.editor.platform)
        form.addRow("平台", self.platform_edit)
        self.email_edit = QLineEdit(self.editor.email)
        self.email_edit.setPlaceholderText("必填，收稿邮箱")
        form.addRow("邮箱 *", self.email_edit)
        self.genres_edit = QLineEdit(self.editor.genres)
        self.genres_edit.setPlaceholderText("如：短篇/长篇/短剧")
        form.addRow("题材", self.genres_edit)
        self.directions_edit = QLineEdit(self.editor.directions)
        self.directions_edit.setPlaceholderText("如：世情/追妻/虐文（收稿方向）")
        form.addRow("收稿方向", self.directions_edit)
        self.status_edit = QLineEdit(self.editor.status)
        self.status_edit.setPlaceholderText("如：正常收稿 / 停止收稿")
        form.addRow("收稿状态", self.status_edit)
        self.fee_edit = QLineEdit(self.editor.fee_info)
        self.fee_edit.setPlaceholderText("如：千字100")
        form.addRow("稿费", self.fee_edit)
        self.url_edit = QLineEdit(self.editor.source_url)
        self.url_edit.setPlaceholderText("征稿启事链接")
        form.addRow("来源链接", self.url_edit)
        self.notes_edit = QLineEdit(self.editor.notes)
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
        name = self.name_edit.text().strip()
        email = self.email_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请填写名称")
            return
        if not EMAIL_RE.match(email):
            QMessageBox.warning(self, "提示", "请填写有效的邮箱地址")
            return
        self.editor.name = name
        self.editor.email = email
        self.editor.platform = self.platform_edit.text().strip()
        self.editor.genres = self.genres_edit.text().strip()
        self.editor.directions = self.directions_edit.text().strip()
        self.editor.status = self.status_edit.text().strip()
        self.editor.fee_info = self.fee_edit.text().strip()
        self.editor.source_url = self.url_edit.text().strip()
        self.editor.notes = self.notes_edit.text().strip()
        self.accept()


class EditorsPage(QWidget):
    def __init__(self, db, store, main_window):
        super().__init__()
        self.db = db
        self.store = store
        self.main_window = main_window
        self._page = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        # 工具行：筛选行 + 操作行（避免 11 个控件挤在一行）
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索编辑 / 平台 / 邮箱 / 题材…")
        self.search_edit.setClearButtonEnabled(True)
        # 防抖：停止输入 200ms 后才重建表格，避免每敲一键全量刷新
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(200)
        self._search_debounce.timeout.connect(self._on_filter_changed)
        self.search_edit.textChanged.connect(lambda *_a: self._search_debounce.start())
        filter_row.addWidget(self.search_edit, 2)

        self.platform_combo = QComboBox()
        self.platform_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.platform_combo)
        self.genre_combo = QComboBox()
        self.genre_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.genre_combo)
        self.direction_combo = QComboBox()
        self.direction_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.direction_combo)
        self.blacklist_combo = QComboBox()
        self.blacklist_combo.addItems(["正常编辑", "小黑屋", "全部编辑"])
        self.blacklist_combo.setToolTip("查看正常编辑、小黑屋编辑，或全部编辑")
        self.blacklist_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.blacklist_combo)
        self.fav_check = QCheckBox("只看收藏")
        self.fav_check.toggled.connect(self._on_filter_changed)
        filter_row.addWidget(self.fav_check)
        self.accepting_check = QCheckBox("只看正在收稿")
        self.accepting_check.setToolTip("仅显示状态为“正常收稿”的编辑")
        self.accepting_check.toggled.connect(self._on_filter_changed)
        filter_row.addWidget(self.accepting_check)
        filter_row.addStretch(1)
        layout.addLayout(filter_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        action_row.addStretch(1)
        self.sync_btn = QPushButton("同步最新编辑")
        self.sync_btn.setObjectName("primaryBtn")
        self.sync_btn.setToolTip("从云端下载最新编辑信息（含收稿方向/状态），更新本地列表")
        self.sync_btn.clicked.connect(self._on_sync)
        action_row.addWidget(self.sync_btn)
        import_btn = QPushButton("导入")
        import_btn.clicked.connect(self._on_import)
        action_row.addWidget(import_btn)
        export_btn = QPushButton("导出CSV")
        export_btn.clicked.connect(self._on_export)
        action_row.addWidget(export_btn)
        self.batch_bl_btn = QPushButton("批量加入小黑屋")
        self.batch_bl_btn.setEnabled(False)
        self.batch_bl_btn.clicked.connect(lambda: self._on_batch_blacklist(True))
        action_row.addWidget(self.batch_bl_btn)
        self.batch_unbl_btn = QPushButton("批量移出小黑屋")
        self.batch_unbl_btn.setEnabled(False)
        self.batch_unbl_btn.clicked.connect(lambda: self._on_batch_blacklist(False))
        action_row.addWidget(self.batch_unbl_btn)
        add_btn = QPushButton("新增")
        add_btn.setObjectName("primaryBtn")
        add_btn.clicked.connect(self._on_add)
        action_row.addWidget(add_btn)
        layout.addLayout(action_row)

        # 提示条
        info = QFrame()
        info.setObjectName("infoBar")
        info_layout = QHBoxLayout(info)
        info_layout.setContentsMargins(12, 6, 12, 6)
        self.info_text = QLabel()
        self.info_text.setObjectName("infoBarText")
        info_layout.addWidget(self.info_text)
        layout.addWidget(info)

        # 表格（收藏/名称/平台/邮箱/题材/收稿方向/状态/稿费/来源/小黑屋/操作）
        self.table = QTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels(
            ["收藏", "名称", "平台", "邮箱", "题材", "收稿方向", "状态",
             "稿费", "来源", "小黑屋", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.itemSelectionChanged.connect(self._update_batch_btns)
        header = self.table.horizontalHeader()
        # 普通文本列的宽度在填充数据后一次性测量并固定；窗口不够时由
        # 横向滚动条承载，避免 Qt 的 ResizeToContents 在数千行数据上反复扫描。
        for col in (1, 2, 3, 4, 7):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 52)
        header.setSectionResizeMode(5, QHeaderView.Fixed)
        self.table.setColumnWidth(5, DIRECTIONS_COLUMN_WIDTH)
        header.setSectionResizeMode(6, QHeaderView.Fixed)
        self.table.setColumnWidth(6, 90)
        header.setSectionResizeMode(8, QHeaderView.Fixed)
        self.table.setColumnWidth(8, 64)
        header.setSectionResizeMode(9, QHeaderView.Fixed)
        self.table.setColumnWidth(9, 64)
        header.setSectionResizeMode(10, QHeaderView.Fixed)
        self.table.setColumnWidth(10, 260)
        header.setStretchLastSection(False)
        layout.addWidget(self.table, 1)

        # 分页栏：每页 PAGE_SIZE 人，避免一次性创建数千行的按钮控件
        pager = QHBoxLayout()
        pager.setSpacing(8)
        pager.addStretch()
        self.prev_btn = QPushButton("上一页")
        self.prev_btn.setObjectName("iconBtn")
        self.prev_btn.clicked.connect(lambda: self._goto_page(self._page - 1))
        pager.addWidget(self.prev_btn)
        self.page_label = QLabel()
        self.page_label.setObjectName("hintText")
        pager.addWidget(self.page_label)
        self.next_btn = QPushButton("下一页")
        self.next_btn.setObjectName("iconBtn")
        self.next_btn.clicked.connect(lambda: self._goto_page(self._page + 1))
        pager.addWidget(self.next_btn)
        pager.addStretch()
        layout.addLayout(pager)

        self.refresh()

    # ---------- 数据 ----------
    def _current_editors(self) -> list[Editor]:
        keyword = self.search_edit.text().strip() or None
        platform = self.platform_combo.currentText()
        platform = None if platform in ("", "全部平台") else platform
        genre = self.genre_combo.currentText()
        genre = None if genre in ("", "全部题材") else genre
        direction = self.direction_combo.currentText()
        direction = None if direction in ("", "全部方向") else direction
        editors = self.db.list_editors(
            keyword=keyword, platform=platform, genre=genre, direction=direction,
            favorites_only=self.fav_check.isChecked(), include_blacklisted=True)
        blacklist_mode = self.blacklist_combo.currentText()
        if blacklist_mode == "正常编辑":
            editors = [e for e in editors if not e.blacklisted]
        elif blacklist_mode == "小黑屋":
            editors = [e for e in editors if e.blacklisted]
        if self.accepting_check.isChecked():
            # 容错：状态是自由文本，"正常收稿（长期）"等变体也应命中
            editors = [e for e in editors
                       if (e.status or "").strip().startswith("正常收稿")]
        return editors

    def refresh(self):
        total = self.db.counts()["编辑总数"]
        self.info_text.setText(
            f"内置 {total} 位编辑（含各平台收稿邮箱/收稿方向），数据来自公开征稿信息，"
            "投稿前请自行核实邮箱有效性。可点击右上角「同步最新编辑」获取云端最新数据。")
        # 重建筛选下拉（保留当前选择）
        platform = self.platform_combo.currentText()
        self.platform_combo.blockSignals(True)
        self.platform_combo.clear()
        self.platform_combo.addItem("全部平台")
        self.platform_combo.addItems(self.db.distinct_platforms())
        self.platform_combo.setCurrentText(platform if platform else "全部平台")
        self.platform_combo.blockSignals(False)

        genre = self.genre_combo.currentText()
        self.genre_combo.blockSignals(True)
        self.genre_combo.clear()
        self.genre_combo.addItem("全部题材")
        self.genre_combo.addItems(self.db.distinct_genres())
        self.genre_combo.setCurrentText(genre if genre else "全部题材")
        self.genre_combo.blockSignals(False)

        direction = self.direction_combo.currentText()
        self.direction_combo.blockSignals(True)
        self.direction_combo.clear()
        self.direction_combo.addItem("全部方向")
        self.direction_combo.addItems(self.db.distinct_directions())
        self.direction_combo.setCurrentText(direction if direction else "全部方向")
        self.direction_combo.blockSignals(False)

        self._reload_table()

    def _on_filter_changed(self, *_args):
        """筛选条件变化：回到第一页。"""
        self._page = 0
        self._reload_table()

    def _goto_page(self, page: int):
        self._page = page
        self._reload_table()

    def _update_pager(self, total: int, pages: int):
        if total == 0:
            self.page_label.setText("共 0 人")
        else:
            self.page_label.setText(f"第 {self._page + 1} / {pages} 页（共 {total} 人）")
        self.prev_btn.setEnabled(self._page > 0)
        self.next_btn.setEnabled(self._page < pages - 1)

    def _reload_table(self):
        all_editors = self._current_editors()
        total = len(all_editors)
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        self._page = min(max(0, self._page), pages - 1)
        editors = all_editors[self._page * PAGE_SIZE:(self._page + 1) * PAGE_SIZE]
        self._update_pager(total, pages)

        self.table.setRowCount(0)
        self._row_editors = []
        if not editors:
            self.table.setRowCount(1)
            item = QTableWidgetItem("没有匹配的编辑")
            item.setTextAlignment(Qt.AlignCenter)
            item.setForeground(Qt.gray)
            self.table.setItem(0, 0, item)
            self.table.setSpan(0, 0, 1, 11)
            self._update_batch_btns()
            return

        self.table.setRowCount(len(editors))
        self._row_editors: list = []
        star_color = theme_colors(self.store.get_theme())["primary"]
        for row, e in enumerate(editors):
            self._row_editors.append(e)
            # 收藏星标（SVG 图标按钮，实心=已收藏，描边灰=未收藏）
            fav_btn = QPushButton()
            fav_btn.setObjectName("iconBtn")
            fav_btn.setStyleSheet("min-width: 0; padding: 0;")
            fav_btn.setFixedSize(30, 30)
            fav_btn.setIcon(make_icon("star", star_color if e.favorite else "#C8C0C6",
                                      18, filled=e.favorite))
            fav_btn.setCursor(Qt.PointingHandCursor)
            fav_btn.setToolTip("取消收藏" if e.favorite else "收藏")
            fav_btn.clicked.connect(lambda _=False, eid=e.id: self._toggle_fav(eid))
            self.table.setCellWidget(row, 0, self._center(fav_btn))

            directions = e.directions or ""
            directions_preview = directions[:DIRECTIONS_PREVIEW_LENGTH]
            if len(directions) > DIRECTIONS_PREVIEW_LENGTH:
                directions_preview += "…"
            for col, text in ((1, e.name), (2, e.platform), (3, e.email),
                              (4, e.genres), (5, directions_preview),
                              (7, e.fee_info)):
                item = mk_item(text or "")
                if col == 1:
                    item.setData(Qt.UserRole, e.id)
                if col == 3 and e.email_invalid:
                    item.setForeground(Qt.red)
                    item.setToolTip("该邮箱投递被退回，已自动跳过")
                if col == 5 and directions:
                    item.setToolTip(f"收稿方向：{directions}")
                self.table.setItem(row, col, item)

            # 收稿状态
            status_text = e.status or ""
            status_item = mk_item(status_text)
            if status_text.startswith("停止收稿"):
                status_item.setForeground(Qt.red)
            elif status_text.startswith("正常收稿"):
                status_item.setForeground(QColor("#2F9E44"))
            status_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 6, status_item)

            # 来源链接
            if e.source_url:
                link_btn = QPushButton("链接")
                link_btn.setObjectName("iconBtn")
                link_btn.setStyleSheet("text-decoration: underline;")
                link_btn.setCursor(Qt.PointingHandCursor)
                link_btn.clicked.connect(
                    lambda _=False, u=e.source_url: QDesktopServices.openUrl(QUrl(u)))
                self.table.setCellWidget(row, 8, self._center(link_btn))
            else:
                item = QTableWidgetItem("—")
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, 8, item)

            # 小黑屋
            bl_btn = QPushButton("移出" if e.blacklisted else "加入")
            bl_btn.setObjectName("iconBtn")
            bl_btn.setStyleSheet("color: #E03131;" if e.blacklisted else "")
            bl_btn.clicked.connect(lambda _=False, eid=e.id: self._toggle_blacklist(eid))
            self.table.setCellWidget(row, 9, self._center(bl_btn))

            # 操作
            ops = QWidget()
            ops_layout = QHBoxLayout(ops)
            ops_layout.setContentsMargins(2, 0, 2, 0)
            ops_layout.setSpacing(4)
            if e.email_invalid:
                restore_btn = QPushButton("恢复有效")
                restore_btn.setObjectName("iconBtn")
                restore_btn.setStyleSheet("color: #2F9E44;")
                restore_btn.clicked.connect(lambda _=False, eid=e.id: self._on_restore_valid(eid))
                ops_layout.addWidget(restore_btn)
            ai_btn = QPushButton("AI解读")
            ai_btn.setObjectName("iconBtn")
            ai_btn.clicked.connect(lambda _=False, ed=e: self._on_ai_summary(ed))
            ops_layout.addWidget(ai_btn)
            edit_btn = QPushButton("编辑")
            edit_btn.setObjectName("iconBtn")
            edit_btn.clicked.connect(lambda _=False, ed=e: self._on_edit(ed))
            ops_layout.addWidget(edit_btn)
            del_btn = QPushButton("删除")
            del_btn.setObjectName("iconBtn")
            del_btn.setStyleSheet("color: #E03131;")
            del_btn.clicked.connect(lambda _=False, ed=e: self._on_delete(ed))
            ops_layout.addWidget(del_btn)
            self.table.setCellWidget(row, 10, ops)

        # 列宽按全量（非当前页）测量：翻页/切筛选时列宽不再跳动
        self._fit_text_columns(all_editors)
        self._update_batch_btns()

        # 右键菜单：收藏 / 小黑屋 / 编辑 / 删除
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)

    def _on_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0 or row >= len(self._row_editors):
            return
        e = self._row_editors[row]
        menu = QMenu(self)
        menu.addAction("取消收藏" if e.favorite else "收藏",
                       lambda: self._toggle_fav(e.id))
        menu.addAction("移出小黑屋" if e.blacklisted else "加入小黑屋",
                       lambda: self._toggle_blacklist(e.id))
        if e.email_invalid:
            menu.addAction("恢复邮箱有效", lambda: self._on_restore_valid(e.id))
        menu.addAction("AI解读", lambda: self._on_ai_summary(e))
        menu.addAction("编辑", lambda: self._on_edit(e))
        menu.addAction("删除", lambda: self._on_delete(e))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _fit_text_columns(self, editors: list[Editor]):
        """按完整文本一次性计算列宽，兼顾不截字和大数据量性能。"""
        metrics = QFontMetrics(self.table.font())
        values = {
            1: (e.name or "" for e in editors),
            2: (e.platform or "" for e in editors),
            3: (e.email or "" for e in editors),
            4: (e.genres or "" for e in editors),
            7: (e.fee_info or "" for e in editors),
        }
        for col, texts in values.items():
            header_text = self.table.horizontalHeaderItem(col).text()
            text_width = max(
                [metrics.horizontalAdvance(header_text),
                 *(metrics.horizontalAdvance(text) for text in texts)])
            self.table.setColumnWidth(col, text_width + 28)

    @staticmethod
    def _center(widget: QWidget) -> QWidget:
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignCenter)
        lay.addWidget(widget)
        return wrap

    # ---------- 行内操作 ----------
    def _on_ai_summary(self, editor: Editor):
        from ..ai_ui import require_ai_config
        from ..workers import AiCallWorker
        from .. import ai_smart
        cfg = require_ai_config(self, self.store, self.main_window)
        if cfg is None:
            return
        self._ai_worker = AiCallWorker(
            lambda: ai_smart.summarize_editor(cfg, editor), self)

        def ok(info: dict):
            QMessageBox.information(
                self, f"AI解读 · {editor.name}",
                f"{info.get('summary') or '（无摘要）'}\n\n"
                f"适合度：{info.get('fit')}\n"
                f"{info.get('reason') or ''}\n\n"
                "仅供参考，投稿前请核对该编辑来源链接。")

        def fail(msg):
            QMessageBox.warning(self, "AI解读失败", str(msg))

        self._ai_worker.finished_ok.connect(ok)
        self._ai_worker.failed.connect(fail)
        self._ai_worker.start()

    def _toggle_fav(self, editor_id: int):
        self.db.toggle_favorite(editor_id)
        self._reload_table()

    def _toggle_blacklist(self, editor_id: int):
        self.db.toggle_blacklisted(editor_id)
        self._reload_table()

    def _selected_editors(self) -> list[Editor]:
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        editors = getattr(self, "_row_editors", [])
        return [editors[r] for r in rows if 0 <= r < len(editors)]

    def _update_batch_btns(self):
        selected = self._selected_editors()
        enabled = bool(selected)
        if hasattr(self, "batch_bl_btn"):
            self.batch_bl_btn.setEnabled(enabled)
            self.batch_unbl_btn.setEnabled(enabled)

    def _on_batch_blacklist(self, blacklisted: bool):
        selected = self._selected_editors()
        if not selected:
            return
        verb = "加入小黑屋" if blacklisted else "移出小黑屋"
        ret = QMessageBox.question(
            self, verb, f"确定将选中的 {len(selected)} 位编辑{verb}？")
        if ret != QMessageBox.Yes:
            return
        self.db.set_blacklisted_many([e.id for e in selected], blacklisted)
        self.main_window.data_changed.emit()
        self._reload_table()

    def _on_restore_valid(self, editor_id: int):
        self.db.clear_email_invalid(editor_id)
        self.main_window.data_changed.emit()
        self._reload_table()

    def _on_add(self):
        dlg = EditorDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.db.insert_editor(dlg.editor)
            self.main_window.data_changed.emit()
            self.refresh()

    def _on_edit(self, editor: Editor):
        fresh = self.db.get_editor(editor.id)
        if fresh is None:
            self.refresh()
            return
        dlg = EditorDialog(self, fresh)
        if dlg.exec() == QDialog.Accepted:
            self.db.update_editor(dlg.editor)
            self.main_window.data_changed.emit()
            self.refresh()

    def _on_delete(self, editor: Editor):
        n_sub, n_rep = self.db.count_editor_related(editor.id)
        ret = QMessageBox.question(
            self, "确认删除",
            f"删除该编辑将连带删除 {n_sub} 条投递记录和 {n_rep} 封回信，且不可恢复。\n"
            f"确定删除「{editor.name}」吗？")
        if ret == QMessageBox.Yes:
            self.db.delete_editor(editor.id)
            self.main_window.data_changed.emit()
            self.refresh()

    # ---------- CSV ----------
    def _on_import(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入编辑 CSV", "", "CSV 文件 (*.csv)")
        if not path:
            return
        dlg = ProgressDialog("导入编辑", self)
        worker = ImportEditorsWorker(self.db, path, self)

        def on_progress(i, n, text):
            dlg.set_progress(i, n, f"正在导入第 {i}/{n} 条：{text}")
            if dlg.is_cancelled():
                worker.stop()

        def on_done(imported, skipped):
            dlg.accept()
            self.main_window.data_changed.emit()
            self.refresh()
            QMessageBox.information(
                self, "导入完成",
                f"成功导入 {imported} 条，跳过 {skipped} 条（邮箱为空或重复）。")

        def on_fail(msg):
            dlg.reject()
            QMessageBox.warning(self, "导入失败", str(msg))

        worker.progress.connect(on_progress)
        worker.finished_ok.connect(on_done)
        worker.failed.connect(on_fail)
        self._import_worker = worker
        worker.start()
        dlg.exec()
        if dlg.is_cancelled() and worker.isRunning():
            worker.stop()

    def _on_export(self):
        editors = self._current_editors()
        if not editors:
            QMessageBox.information(self, "提示", "当前筛选结果为空，无可导出数据")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出编辑 CSV", "editors.csv",
                                              "CSV 文件 (*.csv)")
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADERS)
            for e in editors:
                writer.writerow([e.name, e.platform, e.email, e.genres,
                                 e.directions, e.status, e.fee_info,
                                 e.source_url, e.notes])
        QMessageBox.information(self, "导出完成", f"已导出 {len(editors)} 条到：\n{path}")

    # ---------- 云端同步 ----------
    def _on_sync(self):
        """点击"同步最新编辑"：后台下载云端数据并合并。
        链接已硬编码在代码中，用户不可见、不可修改。"""
        self.sync_btn.setEnabled(False)
        self.sync_btn.setText("同步中…")
        url = updater.build_data_url()
        self._sync_worker = SyncEditorsWorker(self.db, url, self)
        self._sync_worker.finished.connect(self._on_sync_done)
        self._sync_worker.start()

    def _on_sync_done(self, ok: bool, message: str, stats: dict):
        self.sync_btn.setEnabled(True)
        self.sync_btn.setText("同步最新编辑")
        if ok:
            self.main_window.data_changed.emit()
            self.refresh()
            QMessageBox.information(self, "同步结果", message)
        else:
            QMessageBox.warning(self, "同步失败", message)
