"""文稿库页：文稿表格、新建/编辑（关联文件复制到 files\\）、上传 txt/docx 自动统计字数。"""
from __future__ import annotations

import os
import shutil

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QDialog, QFormLayout,
    QFileDialog, QMessageBox, QHeaderView, QMenu,
)

from ..models import Manuscript, CATEGORIES, READER_GROUPS, EMOTIONS, STYLES
from ..docx_reader import read_docx_text, read_txt, count_cjk_words
from ..widgets import mk_item, badge_cell, PagedTable, export_csv, ProgressDialog

FILE_FILTER = "文稿文件 (*.docx *.txt)"


def read_manuscript_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        return read_docx_text(path)
    return read_txt(path)


def copy_to_files_dir(files_dir: str, src: str) -> str:
    """复制文件到数据目录 files\\，重名加序号。返回复制后路径。"""
    base = os.path.basename(src)
    stem, ext = os.path.splitext(base)
    dst = os.path.join(files_dir, base)
    i = 1
    while os.path.exists(dst):
        dst = os.path.join(files_dir, f"{stem}_{i}{ext}")
        i += 1
    shutil.copy2(src, dst)
    return dst


class ManuscriptDialog(QDialog):
    """新建/编辑文稿对话框。"""

    def __init__(self, db, parent=None, manuscript: Manuscript | None = None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("编辑文稿" if manuscript and manuscript.id else "新建文稿")
        self.setMinimumWidth(440)
        self.manuscript = manuscript or Manuscript()
        self.file_path = self.manuscript.file_path

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(8)

        self.title_edit = QLineEdit(self.manuscript.title)
        self.title_edit.setPlaceholderText("必填，作品标题")
        form.addRow("标题 *", self.title_edit)

        self.words_edit = QLineEdit(str(self.manuscript.word_count or ""))
        self.words_edit.setPlaceholderText("数字，选择文件后自动统计")
        form.addRow("字数", self.words_edit)

        self.category_combo = QComboBox()
        self.category_combo.setEditable(True)
        self.category_combo.addItems(CATEGORIES)
        self.category_combo.setCurrentText(self.manuscript.category or "")
        form.addRow("作品分类", self.category_combo)

        self.reader_combo = QComboBox()
        self.reader_combo.addItems(READER_GROUPS)
        if self.manuscript.reader_group in READER_GROUPS:
            self.reader_combo.setCurrentText(self.manuscript.reader_group)
        form.addRow("读者分类", self.reader_combo)

        self.emotion_combo = QComboBox()
        self.emotion_combo.addItems(EMOTIONS)
        if self.manuscript.emotion in EMOTIONS:
            self.emotion_combo.setCurrentText(self.manuscript.emotion)
        form.addRow("读者情绪", self.emotion_combo)

        self.style_combo = QComboBox()
        self.style_combo.addItems(STYLES)
        if self.manuscript.style in STYLES:
            self.style_combo.setCurrentText(self.manuscript.style)
        form.addRow("作品风格", self.style_combo)

        self.genre_edit = QLineEdit(self.manuscript.genre_type)
        self.genre_edit.setPlaceholderText("如：悬疑、言情、世情…")
        form.addRow("作品类型", self.genre_edit)

        file_row = QHBoxLayout()
        self.file_label = QLabel(os.path.basename(self.file_path) if self.file_path else "未关联文件")
        self.file_label.setObjectName("hintText")
        file_row.addWidget(self.file_label, 1)
        choose_btn = QPushButton("重新选择" if self.file_path else "选择文件")
        choose_btn.clicked.connect(self._on_choose_file)
        file_row.addWidget(choose_btn)
        file_wrap = QWidget()
        file_wrap.setLayout(file_row)
        form.addRow("关联文件", file_wrap)
        copy_hint = QLabel("所选文件会被复制到软件数据目录；之后修改原文件不会同步，需重新选择。")
        copy_hint.setObjectName("hintText")
        copy_hint.setWordWrap(True)
        form.addRow("", copy_hint)

        layout.addLayout(form)

        btns = QHBoxLayout()
        ai_btn = QPushButton("AI 补标签")
        ai_btn.setToolTip("根据标题和正文开头建议分类、篇幅、读者群等，需接入 API")
        ai_btn.clicked.connect(self._on_ai_tags)
        btns.addWidget(ai_btn)
        btns.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)
        save_btn = QPushButton("保存")
        save_btn.setObjectName("primaryBtn")
        save_btn.clicked.connect(self._on_save)
        btns.addWidget(save_btn)
        layout.addLayout(btns)

    def _on_ai_tags(self):
        page = self.parent()
        store = getattr(page, "store", None)
        main_window = getattr(page, "main_window", None)
        if store is None or main_window is None:
            QMessageBox.warning(self, "提示", "无法读取 AI 设置")
            return
        from ..ai_ui import require_ai_config
        from ..workers import AiCallWorker
        from .. import ai_smart
        cfg = require_ai_config(self, store, main_window)
        if cfg is None:
            return
        excerpt = ""
        if self.file_path:
            try:
                excerpt = read_manuscript_text(self.file_path)[:1800]
            except Exception:
                excerpt = ""
        title = self.title_edit.text().strip()
        if not title and not excerpt:
            QMessageBox.warning(self, "提示", "请先填写标题或选择文件")
            return
        self._ai_worker = AiCallWorker(
            lambda: ai_smart.suggest_manuscript_tags(cfg, title, excerpt), self)

        def ok(tags: dict):
            if tags.get("category"):
                self.category_combo.setCurrentText(tags["category"])
            if tags.get("reader_group") in READER_GROUPS:
                self.reader_combo.setCurrentText(tags["reader_group"])
            if tags.get("emotion") in EMOTIONS:
                self.emotion_combo.setCurrentText(tags["emotion"])
            if tags.get("style") in STYLES:
                self.style_combo.setCurrentText(tags["style"])
            if tags.get("genre_type"):
                self.genre_edit.setText(tags["genre_type"])
            extra = tags.get("reason") or ""
            QMessageBox.information(
                self, "已填入建议",
                "已写入分类 / 篇幅 / 读者群等标签，请核对后保存。"
                + (f"\n依据：{extra}" if extra else ""))

        def fail(msg):
            QMessageBox.warning(self, "AI 补标签失败", str(msg))

        self._ai_worker.finished_ok.connect(ok)
        self._ai_worker.failed.connect(fail)
        self._ai_worker.start()

    def _on_choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择文稿文件", "", FILE_FILTER)
        if not path:
            return
        # 先读源文件统计字数，成功后才复制进数据目录（避免失败留下孤儿副本）
        try:
            text = read_manuscript_text(path)
            words = count_cjk_words(text)
        except Exception as exc:
            QMessageBox.warning(self, "读取失败", f"无法读取文件：{exc}")
            return
        self.file_path = copy_to_files_dir(self.db.files_dir, path)
        self.words_edit.setText(str(words))
        self.file_label.setText(os.path.basename(self.file_path))

    def _on_save(self):
        title = self.title_edit.text().strip()
        if not title:
            QMessageBox.warning(self, "提示", "请填写标题")
            return
        words_text = self.words_edit.text().strip()
        if words_text and not words_text.isdigit():
            QMessageBox.warning(self, "提示", "字数请填写数字")
            return
        self.manuscript.title = title
        self.manuscript.word_count = int(words_text) if words_text else 0
        self.manuscript.category = self.category_combo.currentText().strip()
        self.manuscript.reader_group = self.reader_combo.currentText()
        self.manuscript.emotion = self.emotion_combo.currentText()
        self.manuscript.style = self.style_combo.currentText()
        self.manuscript.genre_type = self.genre_edit.text().strip()
        self.manuscript.file_path = self.file_path
        self.accept()


class ManuscriptsPage(QWidget):
    def __init__(self, db, store, main_window):
        super().__init__()
        self.db = db
        self.store = store
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        # 顶部：搜索 + 操作
        top = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索标题 / 分类 / 类型…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search)
        top.addWidget(self.search_edit, 1)
        export_btn = QPushButton("导出 CSV")
        export_btn.clicked.connect(self._on_export)
        top.addWidget(export_btn)
        self.batch_delete_btn = QPushButton("批量删除")
        self.batch_delete_btn.setEnabled(False)
        self.batch_delete_btn.clicked.connect(self._on_batch_delete)
        top.addWidget(self.batch_delete_btn)
        upload_btn = QPushButton("上传 txt/docx")
        upload_btn.clicked.connect(self._on_upload)
        top.addWidget(upload_btn)
        add_btn = QPushButton("新建文稿")
        add_btn.setObjectName("primaryBtn")
        add_btn.clicked.connect(self._on_add)
        top.addWidget(add_btn)
        layout.addLayout(top)

        self.paged = PagedTable(
            ["标题", "状态", "字数", "分类", "读者", "情绪", "风格", "类型", "创建时间", "操作"],
            sort_keys=["title", None, "word_count", "category", "reader_group",
                       "emotion", "style", "genre_type", "created_at", None],
            action_cols={1, 9},
            empty_text="还没有文稿，点右上角新建",
            store=store,
            width_key="table_widths_manuscripts",
        )
        self.table = self.paged.table
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        self.table.setColumnWidth(1, 72)
        header.setSectionResizeMode(9, QHeaderView.Fixed)
        self.table.setColumnWidth(9, 170)
        header.setStretchLastSection(False)
        self.paged.set_binder(self._bind_row)
        self.paged.selection_changed.connect(self._update_batch_btns)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self.paged, 1)

        self.refresh()

    def _filtered_manuscripts(self) -> list:
        manuscripts = self.db.list_manuscripts()
        keyword = self.search_edit.text().strip().lower()
        if keyword:
            manuscripts = [m for m in manuscripts
                           if keyword in (m.title or "").lower()
                           or keyword in (m.category or "").lower()
                           or keyword in (m.genre_type or "").lower()]
        return manuscripts

    def refresh(self):
        sold_map: dict[int, object] = {}
        for s in self.db.list_sales():
            if s.manuscript_id not in sold_map:
                sold_map[s.manuscript_id] = s
        self._sold_map = sold_map
        items = self._filtered_manuscripts()
        self._row_manuscripts = items
        self.paged.set_items(items, reset_page=False)
        self._update_batch_btns()

    def _update_batch_btns(self):
        self.batch_delete_btn.setEnabled(bool(self.paged.selected_items()))

    def _bind_row(self, table, row, m):
        sold_map = getattr(self, "_sold_map", {})
        title_item = mk_item(m.title or "")
        title_item.setData(Qt.UserRole, m.id)
        table.setItem(row, 0, title_item)
        sale = sold_map.get(m.id)
        if sale is not None:
            amount_text = f" {sale.amount:g}元" if sale.amount is not None else ""
            if sale.payment_date:
                payment_text = f" {sale.payment_date} 打款"
            elif sale.payment_month:
                payment_text = f" {sale.payment_month} 打款（仅月份）"
            else:
                payment_text = ""
            table.setCellWidget(row, 1, badge_cell(
                "已售", "pass",
                tooltip=f"已售：{sale.platform} {sale.editor_name}{amount_text}{payment_text}"))
        else:
            table.setCellWidget(row, 1, badge_cell("未售", "other"))

        values = [str(m.word_count or ""), m.category, m.reader_group,
                  m.emotion, m.style, m.genre_type, m.created_at]
        for col, text in enumerate(values, start=2):
            table.setItem(row, col, mk_item(text or ""))

        ops = QWidget()
        ops_layout = QHBoxLayout(ops)
        ops_layout.setContentsMargins(2, 0, 2, 0)
        ops_layout.setSpacing(4)
        if m.file_path:
            open_btn = QPushButton("打开")
            open_btn.setObjectName("iconBtn")
            open_btn.setToolTip("打开文稿文件")
            open_btn.clicked.connect(
                lambda _=False, fp=m.file_path: QDesktopServices.openUrl(
                    QUrl.fromLocalFile(fp)))
            ops_layout.addWidget(open_btn)
        track_btn = QPushButton("轨迹")
        track_btn.setObjectName("iconBtn")
        track_btn.clicked.connect(lambda _=False, mid=m.id: self._on_track(mid))
        ops_layout.addWidget(track_btn)
        edit_btn = QPushButton("编辑")
        edit_btn.setObjectName("iconBtn")
        edit_btn.clicked.connect(lambda _=False, mid=m.id: self._on_edit(mid))
        ops_layout.addWidget(edit_btn)
        sale_btn = QPushButton("售出")
        sale_btn.setObjectName("iconBtn")
        sale_btn.setStyleSheet("color: #2F9E44;")
        sale_btn.clicked.connect(lambda _=False, mid=m.id: self._on_add_sale(mid))
        ops_layout.addWidget(sale_btn)
        del_btn = QPushButton("删除")
        del_btn.setObjectName("iconBtn")
        del_btn.setStyleSheet("color: #E03131;")
        del_btn.clicked.connect(lambda _=False, mm=m: self._on_delete(mm))
        ops_layout.addWidget(del_btn)
        table.setCellWidget(row, 9, ops)

    def _on_search(self):
        sold_map: dict[int, object] = {}
        for s in self.db.list_sales():
            if s.manuscript_id not in sold_map:
                sold_map[s.manuscript_id] = s
        self._sold_map = sold_map
        items = self._filtered_manuscripts()
        self._row_manuscripts = items
        self.paged.set_items(items, reset_page=True)
        self._update_batch_btns()

    def _on_context_menu(self, pos):
        items = self.paged.page_items
        row = self.table.rowAt(pos.y())
        if row < 0 or row >= len(items):
            return
        m = items[row]
        menu = QMenu(self)
        if m.file_path:
            menu.addAction("打开文件", lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(m.file_path)))
        menu.addAction("编辑", lambda: self._on_edit(m.id))
        menu.addAction("登记售出", lambda: self._on_add_sale(m.id))
        menu.addAction("删除", lambda: self._on_delete(m))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _on_track(self, manuscript_id: int):
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QTableWidget, QHeaderView
        subs = self.db.list_submissions_for_manuscript(manuscript_id)
        editors = {e.id: e for e in self.db.list_editors(include_blacklisted=True)}
        dlg = QDialog(self)
        dlg.setWindowTitle("投递轨迹")
        dlg.setMinimumSize(640, 360)
        box = QVBoxLayout(dlg)
        table = QTableWidget(len(subs), 6)
        table.setHorizontalHeaderLabels(["时间", "编辑", "平台", "状态", "回复", "主题"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for row, s in enumerate(subs):
            e = editors.get(s.editor_id)
            vals = [s.sent_at or s.scheduled_at or s.created_at if hasattr(s, "created_at") else (s.sent_at or s.scheduled_at or ""),
                    e.name if e else "", e.platform if e else "", s.status,
                    s.reply_status or "", s.subject or ""]
            for col, text in enumerate(vals):
                table.setItem(row, col, mk_item(str(text or "")))
        box.addWidget(table)
        dlg.exec()

    def _on_add(self):
        dlg = ManuscriptDialog(self.db, self)
        if dlg.exec() == QDialog.Accepted:
            self.db.insert_manuscript(dlg.manuscript)
            self.main_window.data_changed.emit()
            self.refresh()

    def _on_edit(self, manuscript_id: int):
        fresh = self.db.get_manuscript(manuscript_id)
        if fresh is None:
            self.refresh()
            return
        dlg = ManuscriptDialog(self.db, self, fresh)
        if dlg.exec() == QDialog.Accepted:
            self.db.update_manuscript(dlg.manuscript)
            self.main_window.data_changed.emit()
            self.refresh()

    def _on_delete(self, manuscript: Manuscript):
        n_sub, n_rep = self.db.count_manuscript_related(manuscript.id)
        extra = f"\n将连带删除 {n_sub} 条投递记录和 {n_rep} 封回信，且不可恢复。"
        ret = QMessageBox.question(self, "确认删除",
                                   f"确定删除文稿《{manuscript.title}》吗？{extra}")
        if ret == QMessageBox.Yes:
            self.db.delete_manuscript(manuscript.id)
            self.main_window.data_changed.emit()
            self.refresh()

    def _on_add_sale(self, manuscript_id: int):
        """行内"售出"按钮：打开售出对话框并预选该文稿。"""
        from .sales import SaleDialog
        dlg = SaleDialog(self.db, self, preselect_manuscript_id=manuscript_id)
        if dlg.exec() == QDialog.Accepted:
            self.db.insert_sale(dlg.sale)
            self.main_window.data_changed.emit()
            self.refresh()

    def _on_upload(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "上传文稿", "", FILE_FILTER)
        if not paths:
            return
        from ..workers import ImportManuscriptsWorker
        dlg = ProgressDialog("导入文稿", self)
        worker = ImportManuscriptsWorker(self.db, paths, self)

        def on_progress(i, n, text):
            dlg.set_progress(i, n, f"正在导入第 {i}/{n} 篇：{text}")
            if dlg.is_cancelled():
                worker.stop()

        def on_done(ok, failed, errors):
            dlg.accept()
            self.main_window.data_changed.emit()
            self.refresh()
            message = f"成功导入 {ok} 篇"
            if failed:
                message += f"，失败 {failed} 篇：\n" + "\n".join(errors or [])
            QMessageBox.information(self, "上传完成", message)

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
        rows = []
        for m in self._filtered_manuscripts():
            rows.append([
                m.title, m.word_count, m.category, m.reader_group,
                m.emotion, m.style, m.genre_type, m.created_at,
            ])
        export_csv(self, ["标题", "字数", "分类", "读者", "情绪", "风格", "类型", "创建时间"],
                   rows, "文稿库.csv")

    def _on_batch_delete(self):
        items = self.paged.selected_items()
        if not items:
            return
        n_sub, n_rep = self.db.count_manuscripts_related_many([m.id for m in items])
        ret = QMessageBox.question(
            self, "批量删除",
            f"确定删除选中的 {len(items)} 篇文稿吗？\n"
            f"将连带删除 {n_sub} 条投递记录和 {n_rep} 封回信，且不可恢复。")
        if ret != QMessageBox.Yes:
            return
        for m in items:
            self.db.delete_manuscript(m.id)
        self.main_window.data_changed.emit()
        self.refresh()
