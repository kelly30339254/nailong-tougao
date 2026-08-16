"""文稿库页：文稿表格、新建/编辑（关联文件复制到 files\\）、上传 txt/docx 自动统计字数。"""
from __future__ import annotations

import os
import shutil

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QDialog, QFormLayout,
    QFileDialog, QMessageBox, QAbstractItemView, QHeaderView, QMenu,
)

from ..models import Manuscript
from ..docx_reader import read_docx_text, read_txt, count_cjk_words
from ..widgets import mk_item, badge_cell

CATEGORIES = ["言情", "悬疑", "世情", "脑洞", "惊悚", "奇幻", "科幻", "武侠", "现实", "其他"]
READER_GROUPS = ["男频", "女频", "通用"]
EMOTIONS = ["甜", "虐", "爽", "燃", "暖", "虐心", "轻松"]
STYLES = ["第一人称", "第三人称", "多视角"]
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
        btns.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)
        save_btn = QPushButton("保存")
        save_btn.setObjectName("primaryBtn")
        save_btn.clicked.connect(self._on_save)
        btns.addWidget(save_btn)
        layout.addLayout(btns)

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
        self.search_edit.textChanged.connect(lambda *_a: self.refresh())
        top.addWidget(self.search_edit, 1)
        upload_btn = QPushButton("上传 txt/docx")
        upload_btn.clicked.connect(self._on_upload)
        top.addWidget(upload_btn)
        add_btn = QPushButton("新建文稿")
        add_btn.setObjectName("primaryBtn")
        add_btn.clicked.connect(self._on_add)
        top.addWidget(add_btn)
        layout.addLayout(top)

        # 表格
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            ["标题", "状态", "字数", "分类", "读者", "情绪", "风格", "类型", "创建时间", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        self.table.setColumnWidth(1, 72)
        header.setSectionResizeMode(9, QHeaderView.Fixed)
        self.table.setColumnWidth(9, 170)
        header.setStretchLastSection(False)
        layout.addWidget(self.table, 1)

        self.refresh()

    def refresh(self):
        manuscripts = self.db.list_manuscripts()
        keyword = self.search_edit.text().strip().lower()
        if keyword:
            manuscripts = [m for m in manuscripts
                           if keyword in (m.title or "").lower()
                           or keyword in (m.category or "").lower()
                           or keyword in (m.genre_type or "").lower()]
        # 文稿 → 最新一条售出记录
        sold_map: dict[int, object] = {}
        for s in self.db.list_sales():
            if s.manuscript_id not in sold_map:
                sold_map[s.manuscript_id] = s
        self._row_manuscripts: list = []
        self.table.setRowCount(0)
        if not manuscripts:
            self.table.setRowCount(1)
            item = mk_item("还没有文稿，点右上角新建", Qt.AlignCenter)
            item.setForeground(Qt.gray)
            self.table.setItem(0, 0, item)
            self.table.setSpan(0, 0, 1, 10)
            return

        self.table.setRowCount(len(manuscripts))
        for row, m in enumerate(manuscripts):
            self._row_manuscripts.append(m)
            self.table.setItem(row, 0, mk_item(m.title or ""))
            sale = sold_map.get(m.id)
            if sale is not None:
                amount_text = f" {sale.amount:g}元" if sale.amount is not None else ""
                if sale.payment_date:
                    payment_text = f" {sale.payment_date} 打款"
                elif sale.payment_month:
                    payment_text = f" {sale.payment_month} 打款（仅月份）"
                else:
                    payment_text = ""
                self.table.setCellWidget(row, 1, badge_cell(
                    "已售", "pass",
                    tooltip=f"已售：{sale.platform} {sale.editor_name}{amount_text}{payment_text}"))
            else:
                # 未售出行也给出状态，而不是空白浪费一列
                self.table.setCellWidget(row, 1, badge_cell("未售", "other"))

            values = [str(m.word_count or ""), m.category, m.reader_group,
                      m.emotion, m.style, m.genre_type, m.created_at]
            for col, text in enumerate(values, start=2):
                self.table.setItem(row, col, mk_item(text or ""))

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
            self.table.setCellWidget(row, 9, ops)

        # 右键菜单：打开 / 编辑 / 售出 / 删除
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)

    def _on_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0 or row >= len(self._row_manuscripts):
            return
        m = self._row_manuscripts[row]
        menu = QMenu(self)
        if m.file_path:
            menu.addAction("打开文件", lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(m.file_path)))
        menu.addAction("编辑", lambda: self._on_edit(m.id))
        menu.addAction("登记售出", lambda: self._on_add_sale(m.id))
        menu.addAction("删除", lambda: self._on_delete(m))
        menu.exec(self.table.viewport().mapToGlobal(pos))

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
        extra = "\n该文稿的售出记录、投递记录与相关回信将一并删除。"
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
        ok = failed = 0
        errors: list[str] = []
        for path in paths:
            try:
                # 先读源文件，成功后再复制（失败不留下孤儿副本）
                text = read_manuscript_text(path)
                copied = copy_to_files_dir(self.db.files_dir, path)
                title = os.path.splitext(os.path.basename(path))[0]
                self.db.insert_manuscript(Manuscript(
                    title=title, file_path=copied,
                    word_count=count_cjk_words(text)))
                ok += 1
            except Exception as exc:
                failed += 1
                errors.append(f"{os.path.basename(path)}：{exc}")
        self.main_window.data_changed.emit()
        self.refresh()
        message = f"成功导入 {ok} 篇"
        if failed:
            message += f"，失败 {failed} 篇：\n" + "\n".join(errors)
        QMessageBox.information(self, "上传完成", message)
