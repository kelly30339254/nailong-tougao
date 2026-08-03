"""编辑列表页：搜索/筛选、收藏/小黑屋、增删改、CSV 导入导出。"""
from __future__ import annotations

import csv
import io
import os
import re

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QCheckBox, QPushButton, QTableWidget, QTableWidgetItem, QFrame,
    QDialog, QFormLayout, QFileDialog, QMessageBox, QAbstractItemView,
    QHeaderView,
)

from ..models import Editor
from ..widgets import mk_item
from ..icons import make_icon
from ..theme import theme_colors

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

CSV_HEADERS = ["名称", "平台", "邮箱", "题材", "稿费", "来源", "备注"]
_HEADER_ALIASES = {
    "name": ("名称", "name"),
    "platform": ("平台", "platform"),
    "email": ("邮箱", "email"),
    "genres": ("题材", "genres"),
    "fee_info": ("稿费", "fee_info"),
    "source_url": ("来源", "source_url"),
    "notes": ("备注", "notes"),
}


class EditorDialog(QDialog):
    """新增/编辑编辑对话框。"""

    def __init__(self, parent=None, editor: Editor | None = None):
        super().__init__(parent)
        self.setWindowTitle("编辑编辑" if editor and editor.id else "新增编辑")
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
        self.genres_edit.setPlaceholderText("如：言情/悬疑/世情")
        form.addRow("题材", self.genres_edit)
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        # 工具行
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索编辑 / 平台 / 邮箱 / 题材…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._reload_table)
        toolbar.addWidget(self.search_edit, 2)

        self.platform_combo = QComboBox()
        self.platform_combo.currentIndexChanged.connect(self._reload_table)
        toolbar.addWidget(self.platform_combo)
        self.genre_combo = QComboBox()
        self.genre_combo.currentIndexChanged.connect(self._reload_table)
        toolbar.addWidget(self.genre_combo)
        self.fav_check = QCheckBox("只看收藏")
        self.fav_check.toggled.connect(self._reload_table)
        toolbar.addWidget(self.fav_check)
        toolbar.addStretch(1)

        import_btn = QPushButton("导入")
        import_btn.clicked.connect(self._on_import)
        toolbar.addWidget(import_btn)
        export_btn = QPushButton("导出CSV")
        export_btn.clicked.connect(self._on_export)
        toolbar.addWidget(export_btn)
        add_btn = QPushButton("新增")
        add_btn.setObjectName("primaryBtn")
        add_btn.clicked.connect(self._on_add)
        toolbar.addWidget(add_btn)
        layout.addLayout(toolbar)

        # 提示条
        info = QFrame()
        info.setObjectName("infoBar")
        info_layout = QHBoxLayout(info)
        info_layout.setContentsMargins(12, 6, 12, 6)
        self.info_text = QLabel()
        self.info_text.setObjectName("infoBarText")
        info_layout.addWidget(self.info_text)
        layout.addWidget(info)

        # 表格
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            ["收藏", "名称", "平台", "邮箱", "题材", "稿费", "来源", "小黑屋", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 52)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.Fixed)
        self.table.setColumnWidth(7, 64)
        header.setSectionResizeMode(8, QHeaderView.Fixed)
        self.table.setColumnWidth(8, 200)
        header.setStretchLastSection(False)
        layout.addWidget(self.table, 1)

        self.refresh()

    # ---------- 数据 ----------
    def _current_editors(self) -> list[Editor]:
        keyword = self.search_edit.text().strip() or None
        platform = self.platform_combo.currentText()
        platform = None if platform in ("", "全部平台") else platform
        genre = self.genre_combo.currentText()
        genre = None if genre in ("", "全部题材") else genre
        return self.db.list_editors(keyword=keyword, platform=platform, genre=genre,
                                    favorites_only=self.fav_check.isChecked())

    def refresh(self):
        total = self.db.counts()["编辑总数"]
        self.info_text.setText(
            f"内置 {total} 位编辑（含各平台收稿邮箱），数据来自公开征稿信息，"
            "投稿前请自行核实邮箱有效性。")
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

        self._reload_table()

    def _reload_table(self):
        editors = self._current_editors()
        self.table.setRowCount(0)
        if not editors:
            self.table.setRowCount(1)
            item = QTableWidgetItem("没有匹配的编辑")
            item.setTextAlignment(Qt.AlignCenter)
            item.setForeground(Qt.gray)
            self.table.setItem(0, 0, item)
            self.table.setSpan(0, 0, 1, 9)
            return

        self.table.setRowCount(len(editors))
        star_color = theme_colors(self.store.get_theme())["primary"]
        for row, e in enumerate(editors):
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

            for col, text in ((1, e.name), (2, e.platform), (3, e.email),
                              (4, e.genres), (5, e.fee_info)):
                item = mk_item(text or "")
                if col == 3 and e.email_invalid:
                    item.setForeground(Qt.red)
                    item.setToolTip("该邮箱投递被退回，已自动跳过")
                self.table.setItem(row, col, item)

            # 来源链接
            if e.source_url:
                link_btn = QPushButton("链接")
                link_btn.setObjectName("iconBtn")
                link_btn.setStyleSheet("text-decoration: underline;")
                link_btn.setCursor(Qt.PointingHandCursor)
                link_btn.clicked.connect(
                    lambda _=False, u=e.source_url: QDesktopServices.openUrl(QUrl(u)))
                self.table.setCellWidget(row, 6, self._center(link_btn))
            else:
                item = QTableWidgetItem("—")
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, 6, item)

            # 小黑屋
            bl_btn = QPushButton("移出" if e.blacklisted else "加入")
            bl_btn.setObjectName("iconBtn")
            bl_btn.setStyleSheet("color: #E03131;" if e.blacklisted else "")
            bl_btn.clicked.connect(lambda _=False, eid=e.id: self._toggle_blacklist(eid))
            self.table.setCellWidget(row, 7, self._center(bl_btn))

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
            edit_btn = QPushButton("编辑")
            edit_btn.setObjectName("iconBtn")
            edit_btn.clicked.connect(lambda _=False, ed=e: self._on_edit(ed))
            ops_layout.addWidget(edit_btn)
            del_btn = QPushButton("删除")
            del_btn.setObjectName("iconBtn")
            del_btn.setStyleSheet("color: #E03131;")
            del_btn.clicked.connect(lambda _=False, ed=e: self._on_delete(ed))
            ops_layout.addWidget(del_btn)
            self.table.setCellWidget(row, 8, ops)

    @staticmethod
    def _center(widget: QWidget) -> QWidget:
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignCenter)
        lay.addWidget(widget)
        return wrap

    # ---------- 行内操作 ----------
    def _toggle_fav(self, editor_id: int):
        self.db.toggle_favorite(editor_id)
        self._reload_table()

    def _toggle_blacklist(self, editor_id: int):
        self.db.toggle_blacklisted(editor_id)
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
        ret = QMessageBox.question(self, "确认删除",
                                   f"确定删除编辑「{editor.name}」吗？此操作不可恢复。")
        if ret == QMessageBox.Yes:
            self.db.delete_editor(editor.id)
            self.main_window.data_changed.emit()
            self.refresh()

    # ---------- CSV ----------
    def _on_import(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入编辑 CSV", "", "CSV 文件 (*.csv)")
        if not path:
            return
        with open(path, "rb") as f:
            raw = f.read()
        text = None
        for enc in ("utf-8-sig", "gbk"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            QMessageBox.warning(self, "导入失败", "无法识别文件编码")
            return

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            QMessageBox.warning(self, "导入失败", "文件为空或缺少列头")
            return
        # 列头别名映射（小写去空格）
        header_map: dict[str, str] = {}
        for field in reader.fieldnames:
            key = (field or "").strip().lower()
            for attr, aliases in _HEADER_ALIASES.items():
                if key in aliases:
                    header_map[attr] = field

        imported = skipped = 0
        for row in reader:
            email = (row.get(header_map.get("email", ""), "") or "").strip()
            if not email:
                skipped += 1
                continue
            editor = Editor(
                name=(row.get(header_map.get("name", ""), "") or "").strip() or email,
                platform=(row.get(header_map.get("platform", ""), "") or "").strip(),
                email=email,
                genres=(row.get(header_map.get("genres", ""), "") or "").strip(),
                fee_info=(row.get(header_map.get("fee_info", ""), "") or "").strip(),
                source_url=(row.get(header_map.get("source_url", ""), "") or "").strip(),
                notes=(row.get(header_map.get("notes", ""), "") or "").strip(),
            )
            self.db.insert_editor(editor)
            imported += 1

        self.main_window.data_changed.emit()
        self.refresh()
        QMessageBox.information(self, "导入完成",
                                f"成功导入 {imported} 条，跳过 {skipped} 条（邮箱为空）。")

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
                                 e.fee_info, e.source_url, e.notes])
        QMessageBox.information(self, "导出完成", f"已导出 {len(editors)} 条到：\n{path}")
