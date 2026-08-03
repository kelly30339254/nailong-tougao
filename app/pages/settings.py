"""设置页：发信邮箱 / 投稿信模板 / 投递策略 / 收信设置 / 外观主题 / 数据备份。"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout, QLabel,
    QLineEdit, QComboBox, QSpinBox, QCheckBox, QPushButton, QTabWidget,
    QFrame, QRadioButton, QButtonGroup, QScrollArea, QPlainTextEdit,
    QFileDialog, QMessageBox,
)

from ..models import MailboxConfig
from ..settings_store import PROVIDER_NAMES, provider_preset
from ..theme import THEMES
from ..workers import TestMailboxWorker
from ..letter import DEFAULT_SUBJECT_TPL, DEFAULT_BODY_TPL, PLACEHOLDER_HINT


class MailboxCard(QFrame):
    """单个邮箱配置卡片。"""

    def __init__(self, index: int, parent_page: "SettingsPage"):
        super().__init__()
        self.setObjectName("card")
        self.index = index
        self.page = parent_page
        self._worker: TestMailboxWorker | None = None

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

        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1, 500)
        self.limit_spin.setValue(20)
        form.addRow("单日上限", self.limit_spin)
        layout.addLayout(form)

        bottom = QHBoxLayout()
        self.test_btn = QPushButton("测试此邮箱")
        self.test_btn.clicked.connect(self._on_test)
        bottom.addWidget(self.test_btn)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        bottom.addWidget(self.test_result, 1)
        layout.addLayout(bottom)

    def _on_provider_changed(self, provider: str):
        smtp_host, smtp_port, smtp_ssl, imap_host, imap_port = provider_preset(provider)
        if provider != "自定义":
            self.smtp_host_edit.setText(smtp_host)
            self.smtp_port_spin.setValue(smtp_port)
            self.smtp_ssl_check.setChecked(smtp_ssl)
            self.imap_host_edit.setText(imap_host)
            self.imap_port_spin.setValue(imap_port)

    def to_config(self) -> MailboxConfig:
        return MailboxConfig(
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
            daily_limit=self.limit_spin.value(),
        )

    def load_config(self, cfg: MailboxConfig):
        self.enabled_check.setChecked(cfg.enabled)
        self.provider_combo.blockSignals(True)
        self.provider_combo.setCurrentText(cfg.provider if cfg.provider in PROVIDER_NAMES else "自定义")
        self.provider_combo.blockSignals(False)
        self.address_edit.setText(cfg.address)
        self.auth_edit.setText(cfg.auth_code)
        self.name_edit.setText(cfg.display_name)
        self.smtp_host_edit.setText(cfg.smtp_host)
        self.smtp_port_spin.setValue(cfg.smtp_port)
        self.smtp_ssl_check.setChecked(cfg.smtp_ssl)
        self.imap_host_edit.setText(cfg.imap_host)
        self.imap_port_spin.setValue(cfg.imap_port)
        self.limit_spin.setValue(cfg.daily_limit)

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

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(10)

        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)

        self._build_mailbox_tab()
        self._build_letter_tab()
        self._build_strategy_tab()
        self._build_fetch_tab()
        self._build_theme_tab()
        self._build_backup_tab()

        # 底部栏
        bottom = QHBoxLayout()
        from ..db import data_dir
        dir_label = QLabel(f"数据目录：{data_dir()}")
        dir_label.setObjectName("hintText")
        bottom.addWidget(dir_label)
        bottom.addStretch()
        self.save_hint = QLabel("")
        bottom.addWidget(self.save_hint)
        save_btn = QPushButton("保存设置")
        save_btn.setObjectName("primaryBtn")
        save_btn.clicked.connect(self.save_all)
        bottom.addWidget(save_btn)
        outer.addLayout(bottom)

        self.refresh()

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

    def _on_add_mailbox(self):
        self._append_mailbox_card()
        self.save_hint.setText("已添加邮箱卡片，点「保存设置」后生效")

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
        vbox.addWidget(hint)
        vbox.addWidget(QLabel("主题模板"))
        self.letter_subject_edit = QLineEdit()
        vbox.addWidget(self.letter_subject_edit)
        vbox.addWidget(QLabel("正文模板"))
        self.letter_body_edit = QPlainTextEdit()
        vbox.addWidget(self.letter_body_edit, 1)
        reset_btn = QPushButton("恢复默认")
        reset_btn.clicked.connect(self._on_reset_letter_tpl)
        vbox.addWidget(reset_btn, 0, Qt.AlignLeft)
        layout.addWidget(card, 1)
        self.tabs.addTab(tab, "投稿信模板")

    def _on_reset_letter_tpl(self):
        self.letter_subject_edit.setText(DEFAULT_SUBJECT_TPL)
        self.letter_body_edit.setPlainText(DEFAULT_BODY_TPL)

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
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(5, 600)
        self.interval_spin.setValue(45)
        self.interval_spin.setSuffix(" 秒")
        form.addRow("每封间隔", self.interval_spin)
        self.daily_limit_spin = QSpinBox()
        self.daily_limit_spin.setRange(1, 500)
        self.daily_limit_spin.setValue(30)
        self.daily_limit_spin.setSuffix(" 封")
        form.addRow("每日上限", self.daily_limit_spin)
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
        reseed_btn.setToolTip("按邮箱去重导入内置编辑库，已有邮箱会跳过")
        reseed_btn.clicked.connect(self._on_reseed_editors)
        btn_row.addWidget(reseed_btn)
        btn_row.addStretch()
        vbox.addLayout(btn_row)
        hint = QLabel("备份包含全部编辑、文稿、投递记录、回信与设置。导入会覆盖当前数据。")
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

    def _refresh_backup_info(self):
        size = self.db.db_file_size()
        self.backup_size_label.setText(f"数据库大小：{size / 1024:.0f} KB")

    def _on_backup_export(self):
        from datetime import datetime
        default_name = f"奶龙投稿助手备份-{datetime.now().strftime('%Y%m%d')}.db"
        path, _ = QFileDialog.getSaveFileName(self, "导出备份", default_name,
                                              "数据库文件 (*.db)")
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
                                              "数据库文件 (*.db)")
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
        ret = QMessageBox.warning(
            self, "确认清空",
            "将删除全部文稿、投稿记录与回信（保留编辑列表和设置），不可恢复。确定继续吗？",
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
        from ..theme import resource_path
        path = resource_path(os.path.join("app", "data", "builtin_editors.json"))
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
        # 邮箱
        mailboxes = self.store.load_mailboxes()
        for card, cfg in zip(self.mailbox_cards, mailboxes):
            card.load_config(cfg)
        # 投稿信模板
        subject_tpl, body_tpl = self.store.get_letter_template()
        self.letter_subject_edit.setText(subject_tpl)
        self.letter_body_edit.setPlainText(body_tpl)
        # 策略
        one_draft, interval, daily = self.store.get_strategy()
        self.one_draft_check.setChecked(one_draft)
        self.interval_spin.setValue(interval)
        self.daily_limit_spin.setValue(daily)
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

    def save_all(self):
        for i, card in enumerate(self.mailbox_cards):
            self.store.save_mailbox(i, card.to_config())
        self.store.save_mailbox_count(len(self.mailbox_cards))
        self.store.save_letter_template(self.letter_subject_edit.text(),
                                        self.letter_body_edit.toPlainText())
        self.store.save_strategy(self.one_draft_check.isChecked(),
                                 self.interval_spin.value(),
                                 self.daily_limit_spin.value())
        self.store.save_urge_days(self.urge_days_spin.value())
        self.store.save_fetch_config(self.auto_fetch_check.isChecked(),
                                     self.fetch_interval_spin.value(),
                                     self.lookback_spin.value())
        self.main_window.update_mail_badge()
        self.main_window.data_changed.emit()
        self.save_hint.setText("已保存")
        self.save_hint.setStyleSheet("color: #2F9E44;")
