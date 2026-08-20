"""设置页：发信邮箱 / 投稿信模板 / 投递策略 / 收信设置 / 外观主题 / 数据备份。"""
from __future__ import annotations

import os
import uuid

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout, QLabel,
    QLineEdit, QComboBox, QSpinBox, QCheckBox, QPushButton, QTabWidget,
    QFrame, QRadioButton, QButtonGroup, QScrollArea, QPlainTextEdit,
    QFileDialog, QMessageBox, QDialog,
)

from .. import APP_VERSION
from .. import license as lic
from ..models import LetterTemplate, MailboxConfig
from ..settings_store import PROVIDER_NAMES, provider_preset
from ..theme import THEMES
from ..workers import TestMailboxWorker, AiTestWorker, AiLetterTplWorker
from ..letter import DEFAULT_SUBJECT_TPL, DEFAULT_BODY_TPL, PLACEHOLDER_HINT
from ..ai_client import AI_PRESETS, DEFAULT_PROVIDER, preset_for


class MailboxCard(QFrame):
    """单个邮箱配置卡片。"""

    def __init__(self, index: int, parent_page: "SettingsPage"):
        super().__init__()
        self.setObjectName("card")
        self.index = index
        self.page = parent_page
        self._worker: TestMailboxWorker | None = None
        self.mailbox_id = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        head = QHBoxLayout()
        self.enabled_check = QCheckBox(f"启用邮箱 {index + 1}")
        self.enabled_check.setStyleSheet("font-weight: bold;")
        head.addWidget(self.enabled_check)
        head.addStretch()
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(PROVIDER_NAMES)
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)
        head.addWidget(QLabel("服务商"))
        head.addWidget(self.provider_combo)
        layout.addLayout(head)

        setup_hint = QLabel(
            "请填写完整邮箱地址和邮箱授权码（不是登录密码）；服务器地址会按服务商自动配置。")
        setup_hint.setObjectName("hintText")
        setup_hint.setWordWrap(True)
        layout.addWidget(setup_hint)

        form = QFormLayout()
        form.setSpacing(6)
        self.address_edit = QLineEdit()
        self.address_edit.setPlaceholderText("例如 example@qq.com")
        form.addRow("邮箱地址", self.address_edit)
        self.auth_edit = QLineEdit()
        self.auth_edit.setEchoMode(QLineEdit.Password)
        self.auth_edit.setPlaceholderText("授权码而非登录密码")
        form.addRow("授权码", self.auth_edit)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("留空用邮箱前缀")
        form.addRow("显示名", self.name_edit)

        smtp_row = QHBoxLayout()
        self.smtp_host_edit = QLineEdit()
        self.smtp_host_edit.setPlaceholderText("smtp.qq.com")
        self.smtp_port_spin = QSpinBox()
        self.smtp_port_spin.setRange(1, 65535)
        self.smtp_port_spin.setValue(465)
        self.smtp_ssl_check = QCheckBox("SSL")
        self.smtp_ssl_check.setChecked(True)
        smtp_row.addWidget(self.smtp_host_edit, 1)
        smtp_row.addWidget(self.smtp_port_spin)
        smtp_row.addWidget(self.smtp_ssl_check)
        smtp_wrap = QWidget()
        smtp_wrap.setLayout(smtp_row)
        form.addRow("SMTP", smtp_wrap)

        imap_row = QHBoxLayout()
        self.imap_host_edit = QLineEdit()
        self.imap_host_edit.setPlaceholderText("imap.qq.com")
        self.imap_port_spin = QSpinBox()
        self.imap_port_spin.setRange(1, 65535)
        self.imap_port_spin.setValue(993)
        imap_row.addWidget(self.imap_host_edit, 1)
        imap_row.addWidget(self.imap_port_spin)
        imap_wrap = QWidget()
        imap_wrap.setLayout(imap_row)
        form.addRow("IMAP", imap_wrap)

        limit_row = QHBoxLayout()
        self.limit_check = QCheckBox("启用本地每日保护")
        self.limit_check.setToolTip("默认关闭。邮箱服务商自身额度和风控仍然有效。")
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1, 100000)
        self.limit_spin.setValue(20)
        self.limit_spin.setSuffix(" 封")
        self.limit_check.toggled.connect(self.limit_spin.setEnabled)
        limit_row.addWidget(self.limit_check)
        limit_row.addWidget(self.limit_spin)
        limit_row.addStretch()
        limit_wrap = QWidget()
        limit_wrap.setLayout(limit_row)
        form.addRow("发送保护", limit_wrap)
        layout.addLayout(form)

        bottom = QHBoxLayout()
        self.test_btn = QPushButton("测试此邮箱")
        self.test_btn.clicked.connect(self._on_test)
        bottom.addWidget(self.test_btn)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        bottom.addWidget(self.test_result, 1)
        layout.addLayout(bottom)
        self._on_provider_changed(self.provider_combo.currentText())

    def _set_server_editable(self, editable: bool):
        for widget in (self.smtp_host_edit, self.smtp_port_spin, self.smtp_ssl_check,
                       self.imap_host_edit, self.imap_port_spin):
            widget.setEnabled(editable)

    def _on_provider_changed(self, provider: str):
        smtp_host, smtp_port, smtp_ssl, imap_host, imap_port = provider_preset(provider)
        if provider != "自定义":
            self.smtp_host_edit.setText(smtp_host)
            self.smtp_port_spin.setValue(smtp_port)
            self.smtp_ssl_check.setChecked(smtp_ssl)
            self.imap_host_edit.setText(imap_host)
            self.imap_port_spin.setValue(imap_port)
        self._set_server_editable(provider == "自定义")

    def to_config(self) -> MailboxConfig:
        if not self.mailbox_id:
            self.mailbox_id = uuid.uuid4().hex
        return MailboxConfig(
            mailbox_id=self.mailbox_id,
            enabled=self.enabled_check.isChecked(),
            provider=self.provider_combo.currentText(),
            address=self.address_edit.text().strip(),
            auth_code=self.auth_edit.text().strip(),
            display_name=self.name_edit.text().strip(),
            smtp_host=self.smtp_host_edit.text().strip(),
            smtp_port=self.smtp_port_spin.value(),
            smtp_ssl=self.smtp_ssl_check.isChecked(),
            imap_host=self.imap_host_edit.text().strip(),
            imap_port=self.imap_port_spin.value(),
            limit_enabled=self.limit_check.isChecked(),
            daily_limit=self.limit_spin.value(),
        )

    def load_config(self, cfg: MailboxConfig):
        self.mailbox_id = cfg.mailbox_id
        self.enabled_check.setChecked(cfg.enabled)
        provider = cfg.provider if cfg.provider in PROVIDER_NAMES else "自定义"
        self.provider_combo.blockSignals(True)
        self.provider_combo.setCurrentText(provider)
        self.provider_combo.blockSignals(False)
        smtp_host, smtp_port, smtp_ssl, imap_host, imap_port = provider_preset(provider)
        if provider == "自定义":
            smtp_host, smtp_port, smtp_ssl = cfg.smtp_host, cfg.smtp_port, cfg.smtp_ssl
            imap_host, imap_port = cfg.imap_host, cfg.imap_port
        else:
            if cfg.smtp_host:
                smtp_host, smtp_port, smtp_ssl = cfg.smtp_host, cfg.smtp_port, cfg.smtp_ssl
            if cfg.imap_host:
                imap_host, imap_port = cfg.imap_host, cfg.imap_port
        self.address_edit.setText(cfg.address)
        self.auth_edit.setText(cfg.auth_code)
        self.name_edit.setText(cfg.display_name)
        self.smtp_host_edit.setText(smtp_host)
        self.smtp_port_spin.setValue(smtp_port)
        self.smtp_ssl_check.setChecked(smtp_ssl)
        self.imap_host_edit.setText(imap_host)
        self.imap_port_spin.setValue(imap_port)
        self.limit_spin.setValue(cfg.daily_limit)
        self.limit_check.setChecked(cfg.limit_enabled)
        self.limit_spin.setEnabled(cfg.limit_enabled)
        self._set_server_editable(provider == "自定义")

    def _on_test(self):
        cfg = self.to_config()
        self.test_btn.setEnabled(False)
        self.test_result.setText("正在测试连接……")
        self.test_result.setStyleSheet("color: #8A8087;")
        self._worker = TestMailboxWorker(cfg, self)
        self._worker.result.connect(self._on_test_result)
        self._worker.finished.connect(lambda: self.test_btn.setEnabled(True))
        self._worker.start()

    def _on_test_result(self, ok: bool, message: str):
        self.test_result.setText(message)
        self.test_result.setStyleSheet(f"color: {'#2F9E44' if ok else '#E03131'};")


class SettingsPage(QWidget):
    def __init__(self, db, store, main_window):
        super().__init__()
        self.db = db
        self.store = store
        self.main_window = main_window
        self._loading = True
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(700)
        self._autosave_timer.timeout.connect(self._autosave_now)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(10)

        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)

        self._build_account_tab()
        self._build_mailbox_tab()
        self._build_letter_tab()
        self._build_ai_tab()
        self._build_strategy_tab()
        self._build_fetch_tab()
        self._build_theme_tab()
        self._build_backup_tab()
        self._build_about_tab()

        # 底部栏
        bottom = QHBoxLayout()
        from ..db import data_dir
        dir_label = QLabel(f"数据目录：{data_dir()}")
        dir_label.setObjectName("hintText")
        bottom.addWidget(dir_label)
        bottom.addStretch()
        self.save_hint = QLabel("修改后自动保存")
        bottom.addWidget(self.save_hint)
        outer.addLayout(bottom)

        self.refresh()
        self._connect_auto_save()
        self._loading = False

    # ---------- 标签页：账号 ----------
    def _build_account_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 18, 20, 18)

        self.account_email_label = QLabel("")
        self.account_email_label.setStyleSheet("font-weight: bold; font-size: 16px;")
        layout.addWidget(self.account_email_label)

        self.account_card_label = QLabel("")
        self.account_card_label.setObjectName("hintText")
        self.account_card_label.setWordWrap(True)
        layout.addWidget(self.account_card_label)

        first_hint = QLabel("第一次使用：先到「发信邮箱」填写授权码并测试，再到「投稿信模板」确认正文。")
        first_hint.setObjectName("hintText")
        first_hint.setWordWrap(True)
        layout.addWidget(first_hint)
        hint = QLabel(
            "账号采用「登录 + 卡密绑定」方式：登录后在同一账号下换电脑、重装系统，"
            "只要登录同一账号即可继续使用已绑定的卡密。同一账号同一时间仅允许一台设备在线，"
            "新设备登录后旧设备会被强制下线。")
        hint.setObjectName("hintText")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        bind_card_btn = QPushButton("绑定卡密")
        bind_card_btn.clicked.connect(self._on_bind_card)
        btn_row.addWidget(bind_card_btn)
        logout_btn = QPushButton("退出登录")
        logout_btn.clicked.connect(self._on_logout)
        btn_row.addWidget(logout_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()
        self.tabs.addTab(self._scrollable(tab), "账号")

    def _refresh_account(self):
        sess = lic.session()
        if not sess:
            self.account_email_label.setText("未登录")
            self.account_card_label.setText("")
            return
        email = sess.get("email", "")
        card_bound = bool(sess.get("card_bound"))
        self.account_email_label.setText(email)
        self.account_card_label.setText("卡密状态：已绑定" if card_bound else "卡密状态：未绑定")
        self.account_card_label.setStyleSheet(
            f"color: {'#2F9E44' if card_bound else '#E03131'};")

    def _on_bind_card(self):
        from ..auth_dialog import AuthDialog
        dlg = AuthDialog(self, initial_mode="card")
        if dlg.exec() == QDialog.Accepted:
            self._refresh_account()

    def _on_logout(self):
        ret = QMessageBox.question(
            self, "确认退出登录",
            "退出后需重新登录才能使用本软件，确定退出吗？",
            QMessageBox.Yes | QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        lic.logout()
        self.main_window._quit_app()

    # ---------- 标签页：发信邮箱 ----------
    def _build_mailbox_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(14, 14, 14, 14)
        self.mailbox_cards: list[MailboxCard] = []
        self.mailbox_grid = QGridLayout()
        self.mailbox_grid.setSpacing(12)
        for _ in self.store.load_mailboxes():
            self._append_mailbox_card()
        layout.addLayout(self.mailbox_grid)
        add_btn = QPushButton("＋ 添加邮箱")
        add_btn.setToolTip("邮箱数量不够用时点这里追加，保存后生效")
        add_btn.clicked.connect(self._on_add_mailbox)
        layout.addWidget(add_btn, 0, Qt.AlignLeft)
        test_all_btn = QPushButton("测试全部已启用邮箱")
        test_all_btn.clicked.connect(self._on_test_all)
        layout.addWidget(test_all_btn, 0, Qt.AlignLeft)
        layout.addStretch()
        self.tabs.addTab(self._scrollable(tab), "发信邮箱")

    def _append_mailbox_card(self):
        index = len(self.mailbox_cards)
        card = MailboxCard(index, self)
        self.mailbox_cards.append(card)
        self.mailbox_grid.addWidget(card, index // 2, index % 2)
        if not self._loading:
            self._connect_mailbox_auto_save(card)

    def _on_add_mailbox(self):
        self._append_mailbox_card()
        self._schedule_auto_save()

    def _on_test_all(self):
        for card in self.mailbox_cards:
            if card.enabled_check.isChecked():
                card._on_test()

    # ---------- 标签页：投稿信模板 ----------
    def _build_letter_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 18, 20, 18)
        card = QFrame()
        card.setObjectName("card")
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(20, 18, 20, 18)
        vbox.setSpacing(8)
        hint = QLabel(PLACEHOLDER_HINT)
        hint.setObjectName("hintText")
        hint.setWordWrap(True)
        vbox.addWidget(hint)
        select_row = QHBoxLayout()
        select_row.addWidget(QLabel("模板"))
        self.template_combo = QComboBox()
        self.template_combo.currentIndexChanged.connect(self._on_template_selected)
        select_row.addWidget(self.template_combo, 1)
        self.template_add_btn = QPushButton("新增")
        self.template_add_btn.clicked.connect(self._on_add_template)
        select_row.addWidget(self.template_add_btn)
        copy_btn = QPushButton("复制")
        copy_btn.clicked.connect(self._on_copy_template)
        select_row.addWidget(copy_btn)
        delete_btn = QPushButton("删除")
        delete_btn.clicked.connect(self._on_delete_template)
        select_row.addWidget(delete_btn)
        vbox.addLayout(select_row)
        self.template_name_edit = QLineEdit()
        self.template_name_edit.setPlaceholderText("模板名称")
        vbox.addWidget(self.template_name_edit)
        vbox.addWidget(QLabel("主题模板"))
        self.letter_subject_edit = QLineEdit()
        vbox.addWidget(self.letter_subject_edit)
        vbox.addWidget(QLabel("正文模板"))
        self.letter_body_edit = QPlainTextEdit()
        vbox.addWidget(self.letter_body_edit, 1)
        self.letter_vary_check = QCheckBox("自动发信时每封微调措辞（不使用AI）")
        self.letter_vary_check.setChecked(True)
        self.letter_vary_check.setToolTip(
            "同一模板发给不同编辑时，只替换客套话（如「冒昧来信」「期待审阅」），"
            "作品名、字数、分类和自定义 {变:A|B} 槽位按编辑轮换，降低正文完全相同被判垃圾邮件的概率。")
        vbox.addWidget(self.letter_vary_check)
        self.letter_ai_vary_check = QCheckBox()
        self.letter_ai_vary_check.setVisible(False)  # 兼容旧自动化；逐封 AI 已停用
        ai_note = QLabel("AI 模板改为在投稿批次开始前一次生成并审核，不会在逐封发送时调用。")
        ai_note.setObjectName("hintText")
        ai_note.setWordWrap(True)
        vbox.addWidget(ai_note)
        btn_row = QHBoxLayout()
        ai_tpl_btn = QPushButton("AI 生成模板")
        ai_tpl_btn.setToolTip("按你填写的要求生成可复用的主题和正文模板，需先接入 API")
        ai_tpl_btn.clicked.connect(self._on_ai_generate_tpl)
        btn_row.addWidget(ai_tpl_btn)
        reset_btn = QPushButton("恢复 5 套默认模板")
        reset_btn.clicked.connect(self._on_restore_templates)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        vbox.addLayout(btn_row)
        layout.addWidget(card, 1)
        self.tabs.addTab(tab, "投稿信模板")

    def _on_reset_letter_tpl(self):
        self.letter_subject_edit.setText(DEFAULT_SUBJECT_TPL)
        self.letter_body_edit.setPlainText(DEFAULT_BODY_TPL)

    def _load_template_list(self, preferred_id=None):
        templates = self.db.list_letter_templates()
        current_id = preferred_id or getattr(self, "_current_template_id", None)
        if not current_id:
            try:
                current_id = int(self.store.get("letter_current_template_id", "") or 0)
            except ValueError:
                current_id = None
        self.template_combo.blockSignals(True)
        self.template_combo.clear()
        for template in templates:
            origin = {"builtin": "默认", "legacy": "旧版"}.get(template.origin, "自建")
            self.template_combo.addItem(f"{template.name}（{origin}）", template.id)
        index = self.template_combo.findData(current_id)
        self.template_combo.setCurrentIndex(index if index >= 0 else (0 if templates else -1))
        self.template_combo.blockSignals(False)
        self._show_template(self.template_combo.currentData())

    def _show_template(self, template_id):
        template = self.db.get_letter_template(int(template_id)) if template_id else None
        self._current_template_id = template.id if template else None
        if template:
            self.store.set("letter_current_template_id", str(template.id))
        name = template.name if template else ""
        subject = template.subject if template else ""
        body = template.body if template else ""
        if self.template_name_edit.text() != name:
            self.template_name_edit.setText(name)
        if self.letter_subject_edit.text() != subject:
            self.letter_subject_edit.setText(subject)
        if self.letter_body_edit.toPlainText() != body:
            self.letter_body_edit.setPlainText(body)

    def _save_current_template(self):
        template_id = getattr(self, "_current_template_id", None)
        if not template_id:
            return
        template = self.db.get_letter_template(template_id)
        if template is None:
            return
        template.name = self.template_name_edit.text().strip() or "未命名模板"
        template.subject = self.letter_subject_edit.text()
        template.body = self.letter_body_edit.toPlainText()
        self.db.update_letter_template(template)
        index = self.template_combo.findData(template_id)
        if index >= 0:
            origin = {"builtin": "默认", "legacy": "旧版"}.get(template.origin, "自建")
            self.template_combo.setItemText(index, f"{template.name}（{origin}）")

    def _on_template_selected(self):
        if getattr(self, "_loading", False):
            return
        self._save_current_template()
        self._show_template(self.template_combo.currentData())

    def _on_add_template(self):
        template_id = self.db.insert_letter_template(LetterTemplate(
            name="新模板", subject=DEFAULT_SUBJECT_TPL, body=DEFAULT_BODY_TPL))
        self._load_template_list(template_id)

    def _on_copy_template(self):
        current = self.db.get_letter_template(getattr(self, "_current_template_id", 0))
        if current is None:
            self._on_add_template()
            return
        self._save_current_template()
        template_id = self.db.insert_letter_template(LetterTemplate(
            name=current.name + " 副本", subject=self.letter_subject_edit.text(),
            body=self.letter_body_edit.toPlainText()))
        self._load_template_list(template_id)

    def _on_delete_template(self):
        template_id = getattr(self, "_current_template_id", None)
        if not template_id:
            return
        if QMessageBox.question(self, "删除模板", "确定删除当前模板吗？") != QMessageBox.Yes:
            return
        self.db.delete_letter_template(template_id)
        self._load_template_list()

    def _on_restore_templates(self):
        self.db.restore_builtin_letter_templates()
        self._load_template_list()

    def _on_ai_generate_tpl(self):
        cfg = self.store.get_ai_config()
        if not cfg.configured():
            QMessageBox.information(
                self, "尚未接入 AI",
                "请先到「设置 → AI 接口」填写 API Key。")
            for i in range(self.tabs.count()):
                if self.tabs.tabText(i) == "AI 接口":
                    self.tabs.setCurrentIndex(i)
                    break
            return
        from ..ai_smart import DEFAULT_TPL_REQUIREMENTS
        dlg = QDialog(self)
        dlg.setWindowTitle("AI 生成投稿信模板")
        dlg.setMinimumSize(520, 380)
        box = QVBoxLayout(dlg)
        box.addWidget(QLabel("生成要求（可自行修改后再生成）："))
        req_edit = QPlainTextEdit()
        req_edit.setPlainText(DEFAULT_TPL_REQUIREMENTS)
        box.addWidget(req_edit, 1)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(dlg.reject)
        row.addWidget(cancel)
        go = QPushButton("生成")
        go.setObjectName("primaryBtn")
        row.addWidget(go)
        box.addLayout(row)

        def start_gen():
            text = req_edit.toPlainText().strip()
            if not text:
                QMessageBox.warning(dlg, "提示", "请填写生成要求")
                return
            go.setEnabled(False)
            go.setText("生成中…")
            worker = AiLetterTplWorker(cfg, text, dlg)

            def ok(subject: str, body: str):
                go.setEnabled(True)
                go.setText("生成")
                dlg.accept()
                self._preview_ai_template(subject, body)

            def fail(message: str):
                go.setEnabled(True)
                go.setText("生成")
                QMessageBox.warning(dlg, "生成失败", message)

            worker.finished_ok.connect(ok)
            worker.failed.connect(fail)
            dlg._worker = worker
            worker.start()

        go.clicked.connect(start_gen)
        dlg.exec()

    def _preview_ai_template(self, subject: str, body: str):
        preview = QDialog(self)
        preview.setWindowTitle("预览生成结果")
        preview.setMinimumSize(520, 420)
        box = QVBoxLayout(preview)
        box.addWidget(QLabel("主题"))
        subj = QLineEdit(subject)
        subj.setReadOnly(True)
        box.addWidget(subj)
        box.addWidget(QLabel("正文"))
        body_view = QPlainTextEdit()
        body_view.setPlainText(body)
        body_view.setReadOnly(True)
        box.addWidget(body_view, 1)
        row = QHBoxLayout()
        row.addStretch()
        discard = QPushButton("丢弃")
        discard.clicked.connect(preview.reject)
        row.addWidget(discard)
        apply_btn = QPushButton("写入模板")
        apply_btn.setObjectName("primaryBtn")
        row.addWidget(apply_btn)
        box.addLayout(row)

        def apply():
            cur_s = self.letter_subject_edit.text()
            cur_b = self.letter_body_edit.toPlainText()
            changed = (cur_s not in ("", DEFAULT_SUBJECT_TPL) or
                       cur_b not in ("", DEFAULT_BODY_TPL))
            if changed:
                ret = QMessageBox.question(
                    preview, "覆盖模板", "当前模板不是默认内容，确定用生成结果覆盖吗？")
                if ret != QMessageBox.Yes:
                    return
            self.letter_subject_edit.setText(subject)
            self.letter_body_edit.setPlainText(body)
            self._schedule_auto_save()
            preview.accept()

        apply_btn.clicked.connect(apply)
        preview.exec()

    # ---------- 标签页：AI 接口 ----------
    def _build_ai_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 18, 20, 18)
        card = QFrame()
        card.setObjectName("card")
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(20, 18, 20, 18)
        vbox.setSpacing(10)
        hint = QLabel(
            "可选接入大模型，用于投稿页的「AI智选」和发信时的「AI微调」。"
            "不填 Key 时仍可使用智选排序（不使用AI）和规则微调（不使用AI）。默认推荐 SpaceXAI（xAI Grok）。")
        hint.setObjectName("hintText")
        hint.setWordWrap(True)
        vbox.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(8)
        self.ai_provider_combo = QComboBox()
        self.ai_provider_combo.addItems(list(AI_PRESETS.keys()))
        self.ai_provider_combo.currentTextChanged.connect(self._on_ai_provider_changed)
        form.addRow("服务商", self.ai_provider_combo)
        self.ai_base_edit = QLineEdit()
        self.ai_base_edit.setPlaceholderText("https://api.x.ai/v1")
        form.addRow("接口地址", self.ai_base_edit)
        self.ai_model_edit = QLineEdit()
        self.ai_model_edit.setPlaceholderText("grok-4.5")
        form.addRow("模型", self.ai_model_edit)
        self.ai_key_edit = QLineEdit()
        self.ai_key_edit.setEchoMode(QLineEdit.Password)
        self.ai_key_edit.setPlaceholderText("API Key，保存在系统凭据里")
        form.addRow("API Key", self.ai_key_edit)
        vbox.addLayout(form)

        self.ai_hint_label = QLabel("")
        self.ai_hint_label.setObjectName("hintText")
        self.ai_hint_label.setWordWrap(True)
        vbox.addWidget(self.ai_hint_label)

        test_row = QHBoxLayout()
        self.ai_test_btn = QPushButton("测试连接")
        self.ai_test_btn.clicked.connect(self._on_test_ai)
        test_row.addWidget(self.ai_test_btn)
        self.ai_test_result = QLabel("")
        self.ai_test_result.setWordWrap(True)
        test_row.addWidget(self.ai_test_result, 1)
        vbox.addLayout(test_row)

        layout.addWidget(card)
        layout.addStretch()
        self.tabs.addTab(self._scrollable(tab), "AI 接口")
        self._ai_test_worker: AiTestWorker | None = None

    def _on_ai_provider_changed(self, provider: str):
        url, model, tip = preset_for(provider or DEFAULT_PROVIDER)
        if provider != "自定义":
            if url:
                self.ai_base_edit.setText(url)
            if model:
                self.ai_model_edit.setText(model)
        self.ai_hint_label.setText(tip)
        self.ai_base_edit.setEnabled(provider == "自定义")

    def _ai_config_from_form(self):
        from ..ai_client import AiConfig
        return AiConfig(
            provider=self.ai_provider_combo.currentText(),
            base_url=self.ai_base_edit.text().strip(),
            model=self.ai_model_edit.text().strip(),
            api_key=self.ai_key_edit.text().strip(),
        )

    def _on_test_ai(self):
        cfg = self._ai_config_from_form()
        if not cfg.configured():
            QMessageBox.warning(self, "提示", "请先填写 API Key、接口地址和模型。")
            return
        self.ai_test_btn.setEnabled(False)
        self.ai_test_result.setText("正在测试……")
        self.ai_test_result.setStyleSheet("color: #8A8087;")
        self._ai_test_worker = AiTestWorker(cfg, self)
        self._ai_test_worker.result.connect(self._on_ai_test_result)
        self._ai_test_worker.finished.connect(lambda: self.ai_test_btn.setEnabled(True))
        self._ai_test_worker.start()

    def _on_ai_test_result(self, ok: bool, message: str):
        self.ai_test_result.setText(message)
        self.ai_test_result.setStyleSheet(f"color: {'#2F9E44' if ok else '#E03131'};")

    # ---------- 标签页：投递策略 ----------
    def _build_strategy_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 18, 20, 18)
        card = QFrame()
        card.setObjectName("card")
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(20, 18, 20, 18)
        vbox.setSpacing(10)
        self.one_draft_check = QCheckBox("一稿一投保护")
        vbox.addWidget(self.one_draft_check)
        one_draft_hint = QLabel(
            "同一文稿同一编辑在收到回复前不会重复投递；同一邮箱每天只投同一编辑一次。")
        one_draft_hint.setObjectName("hintText")
        one_draft_hint.setWordWrap(True)
        vbox.addWidget(one_draft_hint)
        form = QFormLayout()
        form.setSpacing(10)
        self.interval_spin = QSpinBox()  # 兼容旧自动化，不再展示或参与批次调度
        self.interval_spin.setRange(5, 600)
        self.interval_spin.setValue(45)
        interval_note = QLabel("批次内每个邮箱首封立即发送，之后每次 SMTP 尝试随机等待 1–3 分钟。")
        interval_note.setObjectName("hintText")
        interval_note.setWordWrap(True)
        form.addRow("发送间隔", interval_note)
        self.urge_days_spin = QSpinBox()
        self.urge_days_spin.setRange(7, 180)
        self.urge_days_spin.setValue(30)
        self.urge_days_spin.setSuffix(" 天")
        form.addRow("催稿提醒", self.urge_days_spin)
        vbox.addLayout(form)
        layout.addWidget(card)
        layout.addStretch()
        self.tabs.addTab(tab, "投递策略")

    # ---------- 标签页：收信设置 ----------
    def _build_fetch_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 18, 20, 18)
        card = QFrame()
        card.setObjectName("card")
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(20, 18, 20, 18)
        vbox.setSpacing(10)
        self.auto_fetch_check = QCheckBox("后台自动收信")
        vbox.addWidget(self.auto_fetch_check)
        form = QFormLayout()
        form.setSpacing(10)
        self.fetch_interval_spin = QSpinBox()
        self.fetch_interval_spin.setRange(5, 1440)
        self.fetch_interval_spin.setValue(30)
        self.fetch_interval_spin.setSuffix(" 分钟")
        form.addRow("检查间隔", self.fetch_interval_spin)
        self.lookback_spin = QSpinBox()
        self.lookback_spin.setRange(1, 365)
        self.lookback_spin.setValue(45)
        self.lookback_spin.setSuffix(" 天")
        form.addRow("回溯天数", self.lookback_spin)
        vbox.addLayout(form)
        layout.addWidget(card)
        layout.addStretch()
        self.tabs.addTab(tab, "收信设置")

    # ---------- 标签页：外观主题 ----------
    def _build_theme_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.addWidget(QLabel("选择主题色，立即生效并自动保存："))
        self.theme_group = QButtonGroup(self)
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)
        self._theme_radios: dict[str, QRadioButton] = {}
        for name, colors in THEMES.items():
            card = QFrame()
            card.setObjectName("card")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 14, 14, 14)
            swatch = QFrame()
            swatch.setFixedSize(72, 48)
            swatch.setStyleSheet(
                f"background: {colors['primary']}; border-radius: 8px; border: none;")
            card_layout.addWidget(swatch, 0, Qt.AlignCenter)
            radio = QRadioButton(name)
            self._theme_radios[name] = radio
            self.theme_group.addButton(radio)
            card_layout.addWidget(radio, 0, Qt.AlignCenter)
            cards_row.addWidget(card)
        cards_row.addStretch()
        layout.addLayout(cards_row)
        layout.addStretch()
        self.theme_group.buttonToggled.connect(self._on_theme_radio)
        self.tabs.addTab(tab, "外观主题")

    # ---------- 标签页：数据备份 ----------
    def _build_backup_tab(self):
        from ..db import data_dir
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 18, 20, 18)
        card = QFrame()
        card.setObjectName("card")
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(20, 18, 20, 18)
        vbox.setSpacing(10)
        self.backup_dir_label = QLabel(f"数据目录：{data_dir()}")
        vbox.addWidget(self.backup_dir_label)
        self.backup_size_label = QLabel("")
        self.backup_size_label.setObjectName("hintText")
        vbox.addWidget(self.backup_size_label)
        btn_row = QHBoxLayout()
        export_btn = QPushButton("导出备份")
        export_btn.clicked.connect(self._on_backup_export)
        btn_row.addWidget(export_btn)
        import_btn = QPushButton("导入备份")
        import_btn.clicked.connect(self._on_backup_import)
        btn_row.addWidget(import_btn)
        reseed_btn = QPushButton("重新导入内置编辑")
        reseed_btn.setToolTip("按邮箱去重导入加密内置编辑库，已有邮箱会跳过；内置资料不可导出")
        reseed_btn.clicked.connect(self._on_reseed_editors)
        btn_row.addWidget(reseed_btn)
        btn_row.addStretch()
        vbox.addLayout(btn_row)
        open_bak = QPushButton("打开备份目录")
        open_bak.clicked.connect(self._on_open_backups)
        btn_row.addWidget(open_bak)
        log_btn = QPushButton("导出诊断日志")
        log_btn.clicked.connect(self._on_export_log)
        btn_row.addWidget(log_btn)
        hint = QLabel(
            "备份为 zip（含数据库和 files 附件），不含邮箱授权码，也不含内置编辑明细。"
            "导入会覆盖当前数据，恢复前会自动在 backups/ 再留一份兜底；"
            "内置编辑会在下次启动时从加密包重新载入。"
            "启动超过 7 天会自动备份并轮换保留 5 份。")
        hint.setObjectName("hintText")
        hint.setWordWrap(True)
        vbox.addWidget(hint)
        layout.addWidget(card)

        clear_card = QFrame()
        clear_card.setObjectName("card")
        clear_box = QVBoxLayout(clear_card)
        clear_box.setContentsMargins(20, 18, 20, 18)
        clear_box.setSpacing(8)
        clear_btn = QPushButton("清空文稿与投稿数据")
        clear_btn.setObjectName("dangerBtn")
        clear_btn.clicked.connect(self._on_clear_data)
        clear_box.addWidget(clear_btn, 0, Qt.AlignLeft)
        clear_hint = QLabel("删除全部文稿、投稿记录与回信，保留编辑列表和设置，不可恢复。")
        clear_hint.setObjectName("hintText")
        clear_box.addWidget(clear_hint)
        layout.addWidget(clear_card)
        layout.addStretch()
        self.tabs.addTab(tab, "数据备份")

    # ---------- 标签页：关于 ----------
    def _build_about_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 18, 20, 18)
        card = QFrame()
        card.setObjectName("card")
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(20, 18, 20, 18)
        vbox.setSpacing(10)
        version_label = QLabel(f"奶龙投稿助手  v{APP_VERSION}")
        vbox.addWidget(version_label)
        hint = QLabel("软件启动时会自动检查新版本；有新版本时会提示你到网盘下载最新安装包。")
        hint.setObjectName("hintText")
        hint.setWordWrap(True)
        vbox.addWidget(hint)
        check_btn = QPushButton("检查更新")
        check_btn.clicked.connect(lambda: self.main_window.check_update(manual=True))
        vbox.addWidget(check_btn, 0, Qt.AlignLeft)
        layout.addWidget(card)
        layout.addStretch()
        self.tabs.addTab(tab, "关于")

    def _on_open_backups(self):
        import os
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        from ..db import data_dir
        folder = os.path.join(data_dir(), "backups")
        os.makedirs(folder, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _on_export_log(self):
        from ..logging_setup import export_log_path
        import shutil
        src = export_log_path()
        path, _ = QFileDialog.getSaveFileName(self, "导出诊断日志", "nailong-app.log", "日志 (*.log)")
        if not path:
            return
        try:
            shutil.copy2(src, path)
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "已导出", path)

    def _refresh_backup_info(self):
        size = self.db.db_file_size()
        self.backup_size_label.setText(f"数据库大小：{size / 1024:.0f} KB")

    def _on_backup_export(self):
        from datetime import datetime
        default_name = f"奶龙投稿助手备份-{datetime.now().strftime('%Y%m%d')}.zip"
        path, _ = QFileDialog.getSaveFileName(self, "导出备份", default_name,
                                              "备份包 (*.zip);;旧版数据库 (*.db)")
        if not path:
            return
        try:
            self.db.backup_to(path)
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", f"备份失败：{exc}")
            return
        QMessageBox.information(self, "导出完成", f"已备份到：\n{path}")

    def _on_backup_import(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入备份", "",
                                              "备份文件 (*.zip *.db)")
        if not path:
            return
        ret = QMessageBox.warning(
            self, "确认导入",
            "导入将覆盖当前全部数据，且需要重启软件生效。确定继续吗？",
            QMessageBox.Yes | QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        try:
            self.db.restore_from(path)
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", f"恢复失败：{exc}")
            return
        QMessageBox.information(self, "导入完成", "已导入备份，请重启软件。")

    def _on_clear_data(self):
        live_batch = self.db.get_live_batch()
        if live_batch is not None:
            QMessageBox.warning(
                self, "暂不能清空",
                f"投稿批次“{live_batch.name}”仍处于运行、定时、暂停或等待状态。"
                "请先到投稿页取消该批次。")
            return
        ret = QMessageBox.warning(
            self, "确认清空",
            "将删除全部文稿、投稿批次、投稿记录与回信（保留编辑列表、模板库和设置），"
            "不可恢复。确定继续吗？",
            QMessageBox.Yes | QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        ret2 = QMessageBox.warning(
            self, "二次确认", "此操作不可恢复，真的要清空吗？",
            QMessageBox.Yes | QMessageBox.No)
        if ret2 != QMessageBox.Yes:
            return
        self.db.clear_business_data()
        self.main_window.data_changed.emit()
        self._refresh_backup_info()
        QMessageBox.information(self, "完成", "已清空文稿与投稿数据。")

    def _on_reseed_editors(self):
        """重新导入内置编辑（按 email 去重，已有跳过）。"""
        from ..builtin_pack import default_pack_path
        path = default_pack_path()
        self.db.clear_seed_marker()
        inserted, skipped = self.db.seed_builtin_editors(path)
        self.main_window.data_changed.emit()
        QMessageBox.information(self, "导入完成",
                                f"已导入 {inserted} 位，跳过已有 {skipped} 位。")

    def _on_theme_radio(self, button, checked: bool):
        if checked:
            from PySide6.QtWidgets import QApplication
            from ..theme import apply_theme
            name = button.text()
            apply_theme(QApplication.instance(), name)
            self.store.set_theme(name)
            combo = self.main_window.theme_combo
            if combo.currentText() != name:
                combo.blockSignals(True)
                combo.setCurrentText(name)
                combo.blockSignals(False)

    def sync_theme_selection(self, name: str):
        radio = self._theme_radios.get(name)
        if radio and not radio.isChecked():
            radio.blockSignals(True)
            radio.setChecked(True)
            radio.blockSignals(False)

    # ---------- 自动保存 ----------
    def _connect_mailbox_auto_save(self, card: MailboxCard):
        for signal in (
            card.enabled_check.toggled,
            card.provider_combo.currentIndexChanged,
            card.address_edit.textEdited,
            card.auth_edit.textEdited,
            card.name_edit.textEdited,
            card.smtp_host_edit.textEdited,
            card.smtp_port_spin.valueChanged,
            card.smtp_ssl_check.toggled,
            card.imap_host_edit.textEdited,
            card.imap_port_spin.valueChanged,
            card.limit_check.toggled,
            card.limit_spin.valueChanged,
        ):
            signal.connect(self._schedule_auto_save)

    def _connect_auto_save(self):
        for card in self.mailbox_cards:
            self._connect_mailbox_auto_save(card)
        for signal in (
            self.letter_subject_edit.textEdited,
            self.letter_body_edit.textChanged,
            self.template_name_edit.textEdited,
            self.letter_vary_check.toggled,
            self.letter_ai_vary_check.toggled,
            self.ai_provider_combo.currentIndexChanged,
            self.ai_base_edit.textEdited,
            self.ai_model_edit.textEdited,
            self.ai_key_edit.textEdited,
            self.one_draft_check.toggled,
            self.interval_spin.valueChanged,
            self.urge_days_spin.valueChanged,
            self.auto_fetch_check.toggled,
            self.fetch_interval_spin.valueChanged,
            self.lookback_spin.valueChanged,
        ):
            signal.connect(self._schedule_auto_save)

    def _schedule_auto_save(self, *_args):
        if self._loading:
            return
        self.save_hint.setText("保存中…")
        self.save_hint.setStyleSheet("color: #8A8087;")
        self._autosave_timer.start()

    def _autosave_now(self):
        if self._loading:
            return
        self._autosave_timer.stop()
        try:
            self.save_all()
        except Exception as exc:
            self.save_hint.setText(f"保存失败：{exc}")
            self.save_hint.setStyleSheet("color: #E03131;")

    def hideEvent(self, event):
        if self._autosave_timer.isActive():
            self._autosave_now()
        super().hideEvent(event)

    # ---------- 工具 ----------
    @staticmethod
    def _scrollable(widget: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidget(widget)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        return area

    # ---------- 载入 / 保存 ----------
    def refresh(self):
        self._loading = True
        # 邮箱
        mailboxes = self.store.load_mailboxes()
        for card, cfg in zip(self.mailbox_cards, mailboxes):
            card.load_config(cfg)
        # 投稿信模板库
        try:
            selected_template_id = int(
                self.store.get("letter_current_template_id", "") or 0) or None
        except ValueError:
            selected_template_id = getattr(self, "_current_template_id", None)
        self._load_template_list(selected_template_id)
        self.letter_vary_check.setChecked(self.store.get_letter_vary())
        self.letter_ai_vary_check.setChecked(False)
        ai_cfg = self.store.get_ai_config()
        self.ai_provider_combo.blockSignals(True)
        self.ai_provider_combo.setCurrentText(ai_cfg.provider or DEFAULT_PROVIDER)
        self.ai_provider_combo.blockSignals(False)
        self.ai_base_edit.setText(ai_cfg.base_url)
        self.ai_model_edit.setText(ai_cfg.model)
        if self.ai_key_edit.text() != ai_cfg.api_key:
            self.ai_key_edit.setText(ai_cfg.api_key)
        _url, _model, tip = preset_for(ai_cfg.provider or DEFAULT_PROVIDER)
        self.ai_hint_label.setText(tip)
        self.ai_base_edit.setEnabled((ai_cfg.provider or DEFAULT_PROVIDER) == "自定义")
        # 策略
        policy = self.store.get_strategy()
        self.one_draft_check.setChecked(policy.one_draft_protection)
        self.interval_spin.setValue(policy.legacy_interval_seconds)
        self.urge_days_spin.setValue(self.store.get_urge_days())
        # 收信
        auto, fetch_interval, lookback = self.store.get_fetch_config()
        self.auto_fetch_check.setChecked(auto)
        self.fetch_interval_spin.setValue(fetch_interval)
        self.lookback_spin.setValue(lookback)
        # 主题
        self.sync_theme_selection(self.store.get_theme())
        # 备份信息
        self._refresh_backup_info()
        # 账号
        self._refresh_account()
        self._loading = False

    def save_all(self):
        for i, card in enumerate(self.mailbox_cards):
            self.store.save_mailbox(i, card.to_config())
        self.store.save_mailbox_count(len(self.mailbox_cards))
        self._save_current_template()
        # 同步旧键以保持旧定时记录与旧版本回退可读。
        self.store.set("letter_subject_tpl", self.letter_subject_edit.text())
        self.store.set("letter_body_tpl", self.letter_body_edit.toPlainText())
        self.store.save_letter_vary(self.letter_vary_check.isChecked())
        self.store.save_letter_ai_vary(False)
        self.store.save_ai_config(
            self.ai_provider_combo.currentText(),
            self.ai_base_edit.text(),
            self.ai_model_edit.text(),
            self.ai_key_edit.text())
        # 每日上限以各邮箱卡片「单日上限」为准，此处不再提供全局死配置
        self.store.save_strategy(self.one_draft_check.isChecked(),
                                 self.interval_spin.value())
        self.store.save_urge_days(self.urge_days_spin.value())
        self.store.save_fetch_config(self.auto_fetch_check.isChecked(),
                                     self.fetch_interval_spin.value(),
                                     self.lookback_spin.value())
        self.main_window.update_mail_badge()
        # 不发 data_changed：当前页 refresh 会 setPlainText，把正在编辑的光标打回开头
        self.save_hint.setText("已保存")
        self.save_hint.setStyleSheet("color: #2F9E44;")
