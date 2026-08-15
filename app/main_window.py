"""主窗口：顶栏 + 侧栏导航 + QStackedWidget。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QUrl, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QComboBox, QListWidget, QListWidgetItem, QStackedWidget, QFrame,
    QMessageBox,
)

from . import APP_VERSION
from .theme import THEMES, DEFAULT_THEME, apply_theme
from .settings_store import SettingsStore
from .tutorial import TutorialDialog

TUTORIAL_ACTION = "local:tutorial"

LINKS = [
    ("智语写作", "AI辅助写作·短篇收稿风向", "AI辅助写作与短篇收稿风向，点击前往",
     "https://zhiyuxiezuo.com/login?invited=HKMLyO"),
    ("奶龙数据站", "长篇网文风向·实用创作工具",
     "长篇网文风向与实用创作工具，点击前往",
     "https://nailong.zhiyuxiezuo.com/"),
    ("使用教程", "功能列表·文字操作指南", "查看奶龙投稿助手功能列表和文字使用教程",
     TUTORIAL_ACTION),
]

# 拍平的 7 个导航项（无分组标题）
NAV_ITEMS = [
    ("dashboard", "工作台"),
    ("submit", "发起投稿"),
    ("records", "投递记录"),
    ("replies", "回信中心"),
    ("sales", "稿费记录"),
    ("manuscripts", "文稿库"),
    ("editors", "编辑列表"),
    ("settings", "设置"),
]


class PromoButton(QFrame):
    """顶栏双行按钮：可打开外部链接或执行本地操作。"""

    def __init__(self, title: str, subtitle: str, tooltip: str, url: str,
                 on_click=None, parent=None):
        super().__init__(parent)
        self.setObjectName("promoBtn")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)
        self._url = url
        self._on_click = on_click
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 6, 16, 6)
        layout.setSpacing(0)
        title_label = QLabel(title)
        title_label.setObjectName("promoTitle")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(title_label)
        sub_label = QLabel(subtitle)
        sub_label.setObjectName("promoSub")
        sub_label.setAlignment(Qt.AlignCenter)
        sub_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(sub_label)

    def mousePressEvent(self, _event):
        if self._on_click is not None:
            self._on_click()
        elif self._url:
            QDesktopServices.openUrl(QUrl(self._url))


class PlaceholderPage(QWidget):
    """后续阶段实现的内容页占位。遵守页面契约：构造 (db, store, main_window) + refresh()。"""

    def __init__(self, db, store, main_window, title: str = ""):
        super().__init__()
        self.db, self.store, self.main_window = db, store, main_window
        layout = QVBoxLayout(self)
        layout.addStretch()
        label = QLabel(f"「{title}」页面建设中")
        label.setAlignment(Qt.AlignCenter)
        label.setObjectName("hintText")
        layout.addWidget(label)
        layout.addStretch()

    def refresh(self):
        pass


class MainWindow(QMainWindow):
    data_changed = Signal()

    def __init__(self, db, store: SettingsStore):
        super().__init__()
        self.db = db
        self.store = store
        self.setWindowTitle("奶龙投稿助手")
        self.resize(1280, 800)

        root = QWidget()
        root.setObjectName("centralRoot")
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_topbar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 14, 20, 2)
        self.page_title = QLabel("工作台")
        self.page_title.setObjectName("pageTitle")
        header_layout.addWidget(self.page_title)
        header_layout.addStretch()
        right.addWidget(header)
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        body.addLayout(right, 1)
        root_layout.addLayout(body, 1)

        self._pages: dict[str, QWidget] = {}
        self._build_pages()

        self.data_changed.connect(self._on_data_changed)
        self.update_mail_badge()
        self.navigate("dashboard")

        # 定时投稿调度：每 30 秒检查到点的"定时待发"记录
        self._sched_worker = None
        self._sched_timer = QTimer(self)
        self._sched_timer.timeout.connect(self._check_scheduled)
        self._sched_timer.start(30 * 1000)

        # 启动 5 秒后后台检查新版本（静默，失败不打扰）
        self._update_worker = None
        QTimer.singleShot(5000, self.check_update)

    # ---------- 顶栏 ----------
    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topBar")
        bar.setFixedHeight(56)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(10)

        title = QLabel("奶龙投稿助手")
        title.setObjectName("appTitle")
        layout.addWidget(title)
        layout.addSpacing(16)

        for name, subtitle, tooltip, url in LINKS:
            on_click = self._show_tutorial if url == TUTORIAL_ACTION else None
            promo = PromoButton(name, subtitle, tooltip, url, on_click, bar)
            promo.setFixedHeight(40)
            layout.addWidget(promo, 0, Qt.AlignVCenter)

        layout.addStretch()

        self.mail_badge = QLabel("邮箱未配置")
        self.mail_badge.setObjectName("mailBadgeWarn")
        self.mail_badge.setCursor(Qt.PointingHandCursor)
        self.mail_badge.setToolTip("点击前往设置页配置邮箱")
        self.mail_badge.setFixedHeight(26)
        self.mail_badge.setAlignment(Qt.AlignCenter)
        self.mail_badge.mousePressEvent = lambda _e: self.navigate("settings")
        layout.addWidget(self.mail_badge, 0, Qt.AlignVCenter)
        layout.addSpacing(6)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(THEMES.keys()))
        current = self.store.get_theme()
        self.theme_combo.setCurrentText(current if current in THEMES else DEFAULT_THEME)
        self.theme_combo.currentTextChanged.connect(self._on_theme_changed)
        layout.addWidget(self.theme_combo, 0, Qt.AlignVCenter)

        return bar

    def _create_tutorial_dialog(self) -> TutorialDialog:
        return TutorialDialog(self)

    def _show_tutorial(self):
        self._tutorial_dialog = self._create_tutorial_dialog()
        self._tutorial_dialog.exec()

    # ---------- 侧栏 ----------
    def _build_sidebar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("sideBar")
        bar.setFixedWidth(186)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(0)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("navList")
        self._nav_ids: list[str] = []
        for page_id, text in NAV_ITEMS:
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, page_id)
            self.nav_list.addItem(item)
            self._nav_ids.append(page_id)
        layout.addWidget(self.nav_list, 1)
        self.nav_list.currentRowChanged.connect(self._on_nav_changed)
        self.nav_list.setIconSize(self.nav_list.iconSize())
        return bar

    def _refresh_nav_icons(self):
        from .icons import make_icon
        from .theme import theme_colors
        primary = theme_colors(self.store.get_theme())["primary"]
        current = self.nav_list.currentRow()
        for row in range(self.nav_list.count()):
            item = self.nav_list.item(row)
            color = primary if row == current else "#8A8087"
            item.setIcon(make_icon(self._nav_ids[row], color, 16))

    # ---------- 页面 ----------
    def _build_pages(self):
        from .pages.settings import SettingsPage
        from .pages.editors import EditorsPage
        from .pages.manuscripts import ManuscriptsPage
        from .pages.dashboard import DashboardPage
        from .pages.submit import SubmitPage
        from .pages.records import RecordsPage
        from .pages.replies import RepliesPage
        from .pages.sales import SalesPage
        constructors = {
            "settings": lambda: SettingsPage(self.db, self.store, self),
            "editors": lambda: EditorsPage(self.db, self.store, self),
            "manuscripts": lambda: ManuscriptsPage(self.db, self.store, self),
            "dashboard": lambda: DashboardPage(self.db, self.store, self),
            "submit": lambda: SubmitPage(self.db, self.store, self),
            "records": lambda: RecordsPage(self.db, self.store, self),
            "replies": lambda: RepliesPage(self.db, self.store, self),
            "sales": lambda: SalesPage(self.db, self.store, self),
        }
        for page_id, title in NAV_ITEMS:
            if page_id in constructors:
                page = constructors[page_id]()
            else:
                page = PlaceholderPage(self.db, self.store, self, title)
            self._pages[page_id] = page
            self.stack.addWidget(page)

    def navigate(self, page_id: str):
        if page_id not in self._pages:
            return
        page = self._pages[page_id]
        self.stack.setCurrentWidget(page)
        row = self._nav_ids.index(page_id)
        if self.nav_list.currentRow() != row:
            self.nav_list.blockSignals(True)
            self.nav_list.setCurrentRow(row)
            self.nav_list.blockSignals(False)
        self.page_title.setText(dict(NAV_ITEMS).get(page_id, ""))
        self._refresh_nav_icons()
        if hasattr(page, "refresh"):
            page.refresh()

    def _on_nav_changed(self, row: int):
        if 0 <= row < len(self._nav_ids):
            self.navigate(self._nav_ids[row])

    def _on_data_changed(self):
        current = self.stack.currentWidget()
        if current is not None and hasattr(current, "refresh"):
            current.refresh()
        self.update_mail_badge()

    # ---------- 定时投稿调度 ----------
    def _check_scheduled(self):
        from .workers import SendWorker
        if self._sched_worker is not None and self._sched_worker.isRunning():
            return
        due = self.db.due_scheduled()
        if not due:
            return
        mailboxes = [m for m in self.store.load_mailboxes()
                     if m.enabled and m.address
                     and self.db.count_today(m.address) < m.daily_limit]
        if not mailboxes:
            return  # 无可用邮箱，保持待发，下次再试
        _one, interval, _daily = self.store.get_strategy()
        jobs = []
        for s in due:
            attachment = None
            if s.manuscript_id:
                manuscript = self.db.get_manuscript(s.manuscript_id)
                if manuscript and manuscript.file_path:
                    attachment = manuscript.file_path
            jobs.append({"submission_id": s.id, "to": s.to_email,
                         "subject": s.subject, "body": s.body,
                         "message_id": s.message_id,
                         "attachment_path": attachment})
        self._sched_worker = SendWorker(mailboxes, jobs, interval, self, db=self.db)
        self._sched_worker.item_done.connect(self._on_sched_item_done)
        self._sched_worker.all_done.connect(self._on_sched_all_done)
        self._sched_worker.start()

    def _on_sched_item_done(self, submission_id: int, ok: bool, error: str,
                            mailbox_address: str):
        if submission_id > 0:
            self.db.update_status(submission_id, "已发" if ok else "失败")
            if mailbox_address:
                self.db.update_from_mailbox(submission_id, mailbox_address)

    def _on_sched_all_done(self):
        self._sched_worker = None
        self.data_changed.emit()

    # ---------- 检查更新 ----------
    def check_update(self, manual: bool = False):
        """后台检查新版本。manual=True 时无更新/失败也弹提示（设置页手动触发）。"""
        from .workers import UpdateCheckWorker
        if self._update_worker is not None and self._update_worker.isRunning():
            return
        self._update_worker = UpdateCheckWorker(self)
        self._update_worker.result.connect(
            lambda info, error: self._on_update_result(info, error, manual))
        self._update_worker.start()

    def _on_update_result(self, info, error: str, manual: bool):
        self._update_worker = None
        if info is None:
            if manual:
                if error:
                    QMessageBox.warning(self, "检查更新",
                                        f"检查失败，请检查网络后重试。\n（{error}）")
                else:
                    QMessageBox.information(self, "检查更新",
                                            f"当前已是最新版本（{APP_VERSION}）")
            return
        box = QMessageBox(self)
        box.setWindowTitle("发现新版本")
        box.setIcon(QMessageBox.Information)
        box.setText(f"发现新版本 {info['version']}（当前 {APP_VERSION}）")
        if info.get("notes"):
            box.setInformativeText(info["notes"])
        download_btn = None
        if info.get("download_url"):
            download_btn = box.addButton("去下载", QMessageBox.AcceptRole)
        box.addButton("下次再说", QMessageBox.RejectRole)
        box.exec()
        if download_btn is not None and box.clickedButton() is download_btn:
            QDesktopServices.openUrl(QUrl(info["download_url"]))

    # ---------- 顶栏状态 ----------
    def update_mail_badge(self):
        mailboxes = self.store.load_mailboxes()
        enabled = sum(1 for m in mailboxes if m.enabled and m.address)
        if enabled:
            self.mail_badge.setText(f"邮箱已配置 {enabled} 个")
            self.mail_badge.setToolTip(
                f"已启用 {enabled} 个邮箱；点击前往设置页，可按需继续添加邮箱")
            self.mail_badge.setObjectName("mailBadgeOk")
        else:
            self.mail_badge.setText("邮箱未配置")
            self.mail_badge.setToolTip("点击前往设置页配置邮箱，可按需继续添加")
            self.mail_badge.setObjectName("mailBadgeWarn")
        # objectName 变化后需刷新样式
        self.mail_badge.style().unpolish(self.mail_badge)
        self.mail_badge.style().polish(self.mail_badge)

    def _on_theme_changed(self, name: str):
        from PySide6.QtWidgets import QApplication
        apply_theme(QApplication.instance(), name)
        self.store.set_theme(name)
        self._refresh_nav_icons()
        # 同步设置页的主题选择（若已打开）
        settings_page = self._pages.get("settings")
        if settings_page is not None and hasattr(settings_page, "sync_theme_selection"):
            settings_page.sync_theme_selection(name)
