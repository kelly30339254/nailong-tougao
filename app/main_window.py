"""主窗口：顶栏 + 侧栏导航 + QStackedWidget。"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt, Signal, QUrl, QTimer, QSize
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QDialog, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QComboBox, QListWidget, QListWidgetItem,
    QStackedWidget, QFrame, QMessageBox, QSystemTrayIcon, QMenu,
)

from .icons import app_icon

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

# 拍平的 8 个导航项（无分组标题）
NAV_ITEMS = [
    ("dashboard", "工作台"),
    ("submit", "发起投稿"),
    ("records", "投递记录"),
    ("replies", "回信中心"),
    ("sales", "稿费记录"),
    ("stats", "统计分析"),
    ("manuscripts", "文稿库"),
    ("editors", "编辑列表"),
    ("settings", "设置"),
]


class PromoButton(QFrame):
    """顶栏按钮：可打开外部链接或执行本地操作（压缩为单行，副标题进 tooltip）。"""

    def __init__(self, title: str, subtitle: str, tooltip: str, url: str,
                 on_click=None, parent=None):
        super().__init__(parent)
        self.setObjectName("promoBtn")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{title}：{subtitle}\n{tooltip}" if subtitle else tooltip)
        self._url = url
        self._on_click = on_click
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 2, 10, 2)
        layout.setSpacing(0)
        title_label = QLabel(title)
        title_label.setObjectName("promoTitle")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(title_label)

    def mousePressEvent(self, _event):
        if self._on_click is not None:
            self._on_click()
        elif self._url:
            QDesktopServices.openUrl(QUrl(self._url))


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

        # 关闭窗口最小化到系统托盘
        self._force_quit = False
        self._session_quit = False
        self._setup_tray()

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
            promo.setFixedHeight(30)
            layout.addWidget(promo, 0, Qt.AlignVCenter)

        layout.addStretch()

        self.mail_badge = QLabel("邮箱未配置")
        self.mail_badge.setObjectName("mailBadgeWarn")
        self.mail_badge.setCursor(Qt.PointingHandCursor)
        self.mail_badge.setToolTip("点击前往设置页配置邮箱")
        self.mail_badge.setFixedHeight(26)
        self.mail_badge.setAlignment(Qt.AlignCenter)
        self.mail_badge.mousePressEvent = self._on_mail_badge_click
        layout.addWidget(self.mail_badge, 0, Qt.AlignVCenter)
        layout.addSpacing(6)

        theme_label = QLabel("主题")
        theme_label.setObjectName("hintText")
        layout.addWidget(theme_label, 0, Qt.AlignVCenter)
        self.theme_combo = QComboBox()
        self.theme_combo.setToolTip("切换主题色（自动保存）")
        self.theme_combo.addItems(list(THEMES.keys()))
        current = self.store.get_theme()
        self.theme_combo.setCurrentText(current if current in THEMES else DEFAULT_THEME)
        self.theme_combo.currentTextChanged.connect(self._on_theme_changed)
        layout.addWidget(self.theme_combo, 0, Qt.AlignVCenter)

        return bar

    def _create_tutorial_dialog(self) -> TutorialDialog:
        return TutorialDialog(self)

    def _show_tutorial(self):
        # 复用同一对话框实例，避免每次点击累积未销毁的窗口
        if getattr(self, "_tutorial_dialog", None) is None:
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
        self.nav_list.setIconSize(QSize(18, 18))
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
        from .pages.stats import StatsPage
        constructors = {
            "settings": lambda: SettingsPage(self.db, self.store, self),
            "editors": lambda: EditorsPage(self.db, self.store, self),
            "manuscripts": lambda: ManuscriptsPage(self.db, self.store, self),
            "dashboard": lambda: DashboardPage(self.db, self.store, self),
            "submit": lambda: SubmitPage(self.db, self.store, self),
            "records": lambda: RecordsPage(self.db, self.store, self),
            "replies": lambda: RepliesPage(self.db, self.store, self),
            "sales": lambda: SalesPage(self.db, self.store, self),
            "stats": lambda: StatsPage(self.db, self.store, self),
        }
        for page_id, title in NAV_ITEMS:
            page = constructors[page_id]()
            self._pages[page_id] = page
            self.stack.addWidget(page)

    def set_replies_badge(self, count: int):
        for row, pid in enumerate(self._nav_ids):
            if pid != "replies":
                continue
            item = self.nav_list.item(row)
            base = dict(NAV_ITEMS).get("replies", "回信中心")
            item.setText(f"{base}（{count}）" if count else base)
            break

    def navigate(self, page_id: str):
        if page_id not in self._pages:
            return
        if page_id == "replies":
            self.set_replies_badge(0)
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

    def keyPressEvent(self, event):
        """快捷键：Ctrl+1..8 切页，F5 刷新当前页。"""
        if event.modifiers() & Qt.ControlModifier:
            idx = event.key() - Qt.Key_1
            if 0 <= idx < len(NAV_ITEMS):
                self.navigate(NAV_ITEMS[idx][0])
                return
        if event.key() == Qt.Key_F5:
            current = self.stack.currentWidget()
            if current is not None and hasattr(current, "refresh"):
                current.refresh()
            return
        super().keyPressEvent(event)

    def _on_data_changed(self):
        current = self.stack.currentWidget()
        if current is not None and hasattr(current, "refresh"):
            current.refresh()
        self.update_mail_badge()

    # ---------- 定时投稿调度 ----------
    def notify_status(self, message: str, ms: int = 8000):
        """主窗口状态栏提示，超时后自动隐藏（避免残留一条永远空的状态栏）。"""
        bar = self.statusBar()
        gen = getattr(self, "_status_gen", 0) + 1
        self._status_gen = gen
        bar.showMessage(message, ms)

        def _hide():
            if getattr(self, "_status_gen", 0) == gen:
                bar.hide()

        QTimer.singleShot(ms + 200, _hide)

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
        self._sched_ok = self._sched_fail = self._sched_skip = 0
        self._sched_worker = SendWorker(mailboxes, jobs, interval, self, db=self.db)
        self._sched_worker.item_done.connect(self._on_sched_item_done)
        self._sched_worker.all_done.connect(self._on_sched_all_done)
        self._sched_worker.start()

    def _on_sched_item_done(self, submission_id: int, ok: bool, error: str,
                            mailbox_address: str, skipped: bool = False):
        if submission_id > 0:
            if skipped:
                self.db.update_status(submission_id, "已跳过", sent_at="")
                self._sched_skip += 1
            else:
                self.db.update_status(
                    submission_id, "已发" if ok else "失败",
                    error=error if not ok else None)
                if mailbox_address:
                    self.db.update_from_mailbox(submission_id, mailbox_address)
                if ok:
                    self._sched_ok += 1
                else:
                    self._sched_fail += 1

    def _on_sched_all_done(self):
        self._sched_worker = None
        text = f"定时投稿完成：成功 {self._sched_ok}，失败 {self._sched_fail}"
        if self._sched_skip:
            text += f"，跳过 {self._sched_skip}"
        self.notify_status(text, 10000)
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
        # 用户曾选择"忽略此版本"：即使服务器仍报该版本也不打扰
        if info is not None and self.store.get("ignored_version") == info["version"]:
            return
        if info is None:
            if manual:
                if error:
                    QMessageBox.warning(self, "检查更新",
                                        f"检查失败，请检查网络后重试。\n（{error}）")
                else:
                    QMessageBox.information(self, "检查更新",
                                            f"当前已是最新版本（{APP_VERSION}）")
            elif error:
                # 自动检查失败：弱提示，不弹窗打扰
                self.notify_status("检查更新失败，请检查网络。", 6000)
            return
        box = QMessageBox(self)
        box.setWindowTitle("发现新版本")
        box.setIcon(QMessageBox.Information)
        box.setText(f"发现新版本 {info['version']}（当前 {APP_VERSION}）")
        if info.get("notes"):
            box.setInformativeText(info["notes"])
        github_url = (info.get("github_url") or "").strip()
        download_url = (info.get("download_url") or "").strip()
        inapp_btn = None
        if github_url:
            inapp_btn = box.addButton("应用内下载", QMessageBox.AcceptRole)
        netdisk_btn = None
        if download_url:
            netdisk_btn = box.addButton("网盘下载", QMessageBox.ActionRole)
        ignore_btn = box.addButton("忽略此版本", QMessageBox.DestructiveRole)
        box.addButton("下次再说", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if inapp_btn is not None and clicked is inapp_btn:
            self._download_update(github_url, info["version"])
        elif netdisk_btn is not None and clicked is netdisk_btn:
            QDesktopServices.openUrl(QUrl(download_url))
        elif clicked is ignore_btn:
            self.store.set("ignored_version", info["version"])

    def _download_update(self, url: str, version: str):
        import tempfile
        from urllib.parse import urlparse
        from .widgets import ProgressDialog
        from .workers import DownloadUpdateWorker

        name = os.path.basename(urlparse(url).path) or f"nailong-{version}.bin"
        dest = os.path.join(tempfile.gettempdir(), name)
        dlg = ProgressDialog("下载更新", self)
        worker = DownloadUpdateWorker(url, dest, self)

        def on_progress(got: int, total: int):
            if total:
                dlg.set_progress(got, total, f"已下载 {got // 1024} / {total // 1024} KB")
            else:
                dlg.set_progress(1, 1, f"已下载 {got // 1024} KB")
            if dlg.is_cancelled():
                worker.stop()

        def on_ok(path: str):
            dlg.accept()
            self._offer_install(path)

        def on_fail(message: str):
            dlg.reject()
            if message != "已取消下载":
                QMessageBox.warning(self, "下载失败", message)

        worker.progress.connect(on_progress)
        worker.finished_ok.connect(on_ok)
        worker.failed.connect(on_fail)
        self._download_worker = worker
        worker.start()
        dlg.exec()
        if dlg.is_cancelled() and worker.isRunning():
            worker.stop()

    def _offer_install(self, path: str):
        box = QMessageBox(self)
        box.setWindowTitle("下载完成")
        box.setText("安装包已就绪，立即安装将退出当前程序。")
        box.setInformativeText(path)
        install_btn = box.addButton("立即安装", QMessageBox.AcceptRole)
        box.addButton("稍后安装", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not install_btn:
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception as exc:
            QMessageBox.warning(self, "无法启动安装包", str(exc))
            return
        self._quit_app()

    # ---------- 顶栏状态 ----------
    def _on_mail_badge_click(self, _event):
        """点击邮箱徽章：跳转设置页并定位到「发信邮箱」标签（不再记住上次停留位置）。"""
        self.navigate("settings")
        settings_page = self._pages.get("settings")
        if settings_page is not None and hasattr(settings_page, "tabs"):
            settings_page.tabs.setCurrentIndex(0)

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

    # ---------- 系统托盘 ----------
    def _setup_tray(self):
        """点击右上角×时最小化到托盘；托盘菜单提供「打开/退出」。"""
        self._tray_icon = None
        self._tray_menu = None
        self._quitting = False
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return  # 系统无托盘则走正常关闭流程
        app = QApplication.instance()
        if app is not None:
            # 窗口隐藏到托盘后不要因为「没有可见窗口」而自动退出
            app.setQuitOnLastWindowClosed(False)
            try:
                app.commitDataRequest.connect(self._on_session_quit)
            except Exception:
                pass

        icon = app_icon()
        # 托盘和菜单都不挂在主窗口上：窗口隐藏后，子菜单点击在 Windows 上会丢事件
        tray = QSystemTrayIcon(icon)
        tray.setToolTip("奶龙投稿助手（点 × 会缩到这里，退出请用菜单「退出软件」）")
        menu = QMenu()
        open_action = menu.addAction("打开主界面")
        open_action.triggered.connect(self._show_window)
        menu.addSeparator()
        quit_action = menu.addAction("退出软件")
        # 等右键菜单先收起再退出；在菜单回调里立刻 hide 托盘，Windows 会把后续动作吞掉
        quit_action.triggered.connect(lambda: QTimer.singleShot(0, self._quit_app))
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        self._tray_menu = menu
        self._tray_icon = tray

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_window()

    def _show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_app(self):
        if self._quitting:
            return
        self._quitting = True
        self._force_quit = True
        tray = self._tray_icon
        self._tray_icon = None
        if tray is not None:
            tray.setContextMenu(None)
            tray.hide()
            tray.deleteLater()
        self._tray_menu = None

        app = QApplication.instance()
        if app is not None:
            for w in list(app.topLevelWidgets()):
                try:
                    if isinstance(w, QDialog) and w.isVisible():
                        w.reject()
                except Exception:
                    pass
            self.close()
            app.quit()

        # 兜底：后台线程卡住事件循环时，仍保证托盘「退出」能结束进程
        QTimer.singleShot(2000, lambda: os._exit(0))
        import threading
        threading.Timer(2.5, os._exit, args=(0,)).start()

    def _on_session_quit(self, *_args):
        """关机、注销或安装程序（Restart Manager）要求退出时，不要缩到托盘。"""
        self._session_quit = True
        self._quit_app()

    def nativeEvent(self, eventType, message):
        if sys.platform == "win32":
            try:
                kind = bytes(eventType)
                if kind == b"windows_generic_MSG":
                    import ctypes
                    from ctypes import wintypes
                    msg = wintypes.MSG.from_address(int(message))
                    # WM_QUERYENDSESSION / WM_ENDSESSION：安装器或系统要关进程
                    if msg.message in (0x0011, 0x0016):
                        self._session_quit = True
                        self._force_quit = True
            except Exception:
                pass
        return super().nativeEvent(eventType, message)

    def closeEvent(self, event):
        app = QApplication.instance()
        if self._session_quit or (app is not None and app.isSavingSession()):
            self._force_quit = True
        # 点右上角×：拦截并最小化到托盘，后台继续接收回信/定时投稿
        if self._tray_icon is not None and not self._force_quit:
            event.ignore()
            self.hide()
            self._tray_icon.showMessage(
                "奶龙投稿助手", "程序已最小化到托盘，点击图标可重新打开。",
                QSystemTrayIcon.Information, 3000)
            return
        super().closeEvent(event)
