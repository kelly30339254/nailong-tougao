"""工作台页：6 统计卡、新手指引、近期动态、后台自动收信。"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QTableWidget, QAbstractItemView, QHeaderView,
    QSizePolicy, QDialog, QTextBrowser, QTabWidget,
)

from ..workers import FetchWorker
from ..reply_ingest import ingest_results
from ..widgets import mk_item, make_dot
from ..theme import theme_colors
from ..announcements import load_announcements

STAT_KEYS = ["编辑总数", "文稿", "待回复", "过稿", "退稿", "未读回信"]
# 注意：卡片键与 db.counts() 仅"文稿"→"文稿数"一个差异，直接在 refresh 内映射，无冗余表


def _relative_time(time_str: str) -> str:
    """把 'YYYY-MM-DD HH:MM:SS' 转成相对时间，超过 7 天显示日期。"""
    try:
        dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return time_str or ""
    delta = datetime.now() - dt
    if delta.days >= 7:
        return dt.strftime("%Y-%m-%d")
    if delta.days >= 1:
        return f"{delta.days} 天前"
    hours = delta.seconds // 3600
    if hours >= 1:
        return f"{hours} 小时前"
    minutes = delta.seconds // 60
    if minutes >= 1:
        return f"{minutes} 分钟前"
    return "刚刚"


class _ActivityRow(QWidget):
    """近期动态一行：圆点 + 可换行正文 + 时间。按分配宽度计算高度，避免半行被裁。"""

    def __init__(self, text: str, time_text: str, color: str):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(8)
        row.addWidget(make_dot(color, 8), 0, Qt.AlignTop)
        self._label = QLabel(text)
        self._label.setWordWrap(True)
        self._label.setToolTip(text)
        self._label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        # Ignored 横向：按分配宽度换行，并正确上报高度
        self._label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._label.setMinimumWidth(80)
        row.addWidget(self._label, 1)
        time_label = QLabel(time_text)
        time_label.setObjectName("hintText")
        time_label.setAlignment(Qt.AlignRight | Qt.AlignTop)
        time_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        time_w = max(64, time_label.sizeHint().width() + 4)
        time_label.setFixedWidth(time_w)
        self._time_w = time_w
        row.addWidget(time_label, 0, Qt.AlignTop)

    def hasHeightForWidth(self) -> bool:
        return True

    def _wrapped_height(self, width: int) -> int:
        text_w = max(80, width - 8 - 8 - 8 - self._time_w)
        return max(self._label.fontMetrics().height() + 6,
                   self._label.heightForWidth(text_w) + 6)

    def heightForWidth(self, width: int) -> int:
        return self._wrapped_height(width)

    def sizeHint(self) -> QSize:
        # 用卡片半宽估算，避免 Ignored 策略按 80px 算出十几行高的 sizeHint
        width = self.width() if self.width() > 80 else 360
        return QSize(width, self._wrapped_height(width))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        h = self._wrapped_height(max(self.width(), 1))
        if self.minimumHeight() != h:
            self.setMinimumHeight(h)
            self.updateGeometry()


class StatCard(QFrame):
    def __init__(self, title: str, on_click=None):
        super().__init__()
        self.setObjectName("card")
        self._on_click = on_click
        if on_click:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip(f"查看{title}")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)
        bar = QFrame()
        bar.setObjectName("statBar")
        bar.setFixedWidth(4)
        outer.addWidget(bar)
        layout = QVBoxLayout()
        layout.setSpacing(4)
        self.number = QLabel("0")
        self.number.setObjectName("statNumber")
        layout.addWidget(self.number)
        label = QLabel(title)
        label.setObjectName("statTitle")
        layout.addWidget(label)
        outer.addLayout(layout)
        outer.addStretch()

    def set_value(self, value: int):
        self.number.setText(str(value))

    def mousePressEvent(self, event):
        if self._on_click and event.button() == Qt.LeftButton:
            self._on_click()
        super().mousePressEvent(event)


class DashboardPage(QWidget):
    def __init__(self, db, store, main_window):
        super().__init__()
        self.db = db
        self.store = store
        self.main_window = main_window
        self._fetch_worker: FetchWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(12)

        self._announcements = load_announcements()
        if self._announcements:
            latest = self._announcements[0]
            banner = QFrame()
            banner.setObjectName("infoBar")
            banner_row = QHBoxLayout(banner)
            banner_row.setContentsMargins(12, 8, 12, 8)
            banner_text = QLabel(
                f"{latest['date']}  ·  {latest['title']}  —  {latest['summary']}")
            banner_text.setObjectName("infoBarText")
            banner_text.setWordWrap(True)
            banner_row.addWidget(banner_text, 1)
            detail_btn = QPushButton("查看更新")
            detail_btn.setObjectName("iconBtn")
            detail_btn.clicked.connect(self._show_announcements)
            banner_row.addWidget(detail_btn)
            layout.addWidget(banner)

        # 统计卡一行 6 张
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        self.stat_cards: dict[str, StatCard] = {}
        _stat_nav = {
            "编辑总数": "editors", "文稿": "manuscripts", "待回复": "records",
            "过稿": "replies", "退稿": "replies", "未读回信": "replies",
        }
        for key in STAT_KEYS:
            target = _stat_nav[key]
            card = StatCard(key, on_click=lambda page=target: self.main_window.navigate(page))
            self.stat_cards[key] = card
            stats_row.addWidget(card, 1)
        layout.addLayout(stats_row)

        # 下方左右两卡
        bottom = QHBoxLayout()
        bottom.setSpacing(12)

        guide_card = QFrame()
        guide_card.setObjectName("card")
        guide_layout = QVBoxLayout(guide_card)
        guide_layout.setContentsMargins(16, 14, 16, 14)
        guide_title = QLabel("新手指引")
        guide_title.setObjectName("cardTitle")
        guide_layout.addWidget(guide_title)
        self.guide_box = QVBoxLayout()
        self.guide_box.setSpacing(8)
        guide_layout.addLayout(self.guide_box)
        guide_layout.addStretch()
        bottom.addWidget(guide_card, 1)

        activity_card = QFrame()
        activity_card.setObjectName("card")
        activity_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        activity_layout = QVBoxLayout(activity_card)
        activity_layout.setContentsMargins(16, 14, 16, 14)
        activity_layout.setSpacing(8)
        activity_title = QLabel("近期动态")
        activity_title.setObjectName("cardTitle")
        activity_layout.addWidget(activity_title)

        # 动态条目放进可滚动区：条目一多时不再被卡片圆角裁掉下半截
        self.activity_scroll = QScrollArea()
        self.activity_scroll.setObjectName("activityScroll")
        self.activity_scroll.setWidgetResizable(True)
        self.activity_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.activity_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.activity_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.activity_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.activity_scroll.setStyleSheet(
            "QScrollArea#activityScroll { background: transparent; border: none; }"
        )
        self.activity_scroll.viewport().setStyleSheet("background: transparent;")
        activity_inner = QWidget()
        activity_inner.setObjectName("activityInner")
        activity_inner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        activity_inner.setStyleSheet("QWidget#activityInner { background: transparent; }")
        self.activity_box = QVBoxLayout(activity_inner)
        self.activity_box.setContentsMargins(0, 0, 6, 0)
        self.activity_box.setSpacing(8)
        self.activity_box.setAlignment(Qt.AlignTop)
        self.activity_scroll.setWidget(activity_inner)
        activity_layout.addWidget(self.activity_scroll, 1)

        more_btn = QPushButton("查看全部 →")
        more_btn.setObjectName("iconBtn")
        more_btn.setCursor(Qt.PointingHandCursor)
        more_btn.clicked.connect(lambda: self.main_window.navigate("records"))
        more_row = QHBoxLayout()
        more_row.setContentsMargins(0, 0, 0, 0)
        more_row.addStretch()
        more_row.addWidget(more_btn)
        activity_layout.addLayout(more_row)
        bottom.addWidget(activity_card, 1)

        layout.addLayout(bottom, 1)

        # 数据统计卡（一整行）
        stats_card = QFrame()
        stats_card.setObjectName("card")
        stats_layout = QVBoxLayout(stats_card)
        stats_layout.setContentsMargins(16, 14, 16, 14)
        stats_head = QHBoxLayout()
        stats_title = QLabel("数据统计")
        stats_title.setObjectName("cardTitle")
        stats_head.addWidget(stats_title)
        stats_note = QLabel("仅统计本机软件内的投递记录，不代表其他平台或全网数据")
        stats_note.setObjectName("hintText")
        stats_head.addWidget(stats_note)
        stats_head.addStretch()
        stats_layout.addLayout(stats_head)

        self.stats_content = QHBoxLayout()
        self.stats_table = QTableWidget(0, 5)
        self.stats_table.setHorizontalHeaderLabels(["平台", "投递", "回复", "过稿", "过稿率"])
        self.stats_table.verticalHeader().setVisible(False)
        self.stats_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.stats_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.stats_table.setMaximumHeight(200)
        header = self.stats_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setStretchLastSection(False)
        self.stats_content.addWidget(self.stats_table, 3)
        self.overview_box = QVBoxLayout()
        self.overview_box.setSpacing(6)
        self.stats_content.addLayout(self.overview_box, 2)
        stats_layout.addLayout(self.stats_content)

        self.stats_empty = QLabel("还没有投递数据，统计将在投稿后生成")
        self.stats_empty.setObjectName("hintText")
        stats_layout.addWidget(self.stats_empty)
        layout.addWidget(stats_card)

        self._fetch_timer = QTimer(self)
        self._fetch_timer.timeout.connect(self._maybe_fetch)
        self._fetch_config_key: tuple | None = None  # (auto, interval)，变化时重启定时器

        self.refresh()

    def _show_announcements(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("更新公告")
        dlg.resize(660, 460)
        box = QVBoxLayout(dlg)
        tabs = QTabWidget()
        for item in self._announcements:
            view = QTextBrowser()
            view.setHtml(
                f"<h2>{item['title']}</h2>"
                f"<p><b>版本 {item['version']} · {item['date']}</b></p>"
                f"<p>{item['details'].replace(chr(10), '<br>')}</p>")
            tabs.addTab(view, f"v{item['version']}")
        box.addWidget(tabs)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dlg.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close_btn)
        box.addLayout(row)
        dlg.exec()

    # ---------- 刷新 ----------
    def refresh(self):
        counts = self.db.counts()
        for key in STAT_KEYS:
            count_key = "文稿数" if key == "文稿" else key
            self.stat_cards[key].set_value(counts.get(count_key, 0))
        self._refresh_guide()
        self._refresh_activity()
        self._refresh_stats()
        self._sync_fetch_timer()

    def _sync_fetch_timer(self):
        """收信设置变化后重启后台自动收信定时器（此前改配置不生效需重启应用）。"""
        auto_fetch, interval_minutes, _lookback = self.store.get_fetch_config()
        key = (bool(auto_fetch), int(interval_minutes))
        if key == self._fetch_config_key:
            return
        self._fetch_config_key = key
        self._fetch_timer.stop()
        if auto_fetch and interval_minutes > 0:
            self._fetch_timer.start(interval_minutes * 60 * 1000)

    def _clear_box(self, box: QVBoxLayout):
        while box.count():
            item = box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # setParent(None) 立即从布局/绘制中移除，避免 deleteLater 前重影
                widget.setParent(None)
                widget.deleteLater()

    def _refresh_guide(self):
        self._clear_box(self.guide_box)
        items: list[tuple[str, str]] = []  # (说明文字, 跳转 page_id)

        mailboxes = self.store.load_mailboxes()
        if not any(m.enabled and m.address for m in mailboxes):
            items.append(("配置发信邮箱后才能投递", "settings"))
        if not self.db.list_manuscripts():
            items.append(("还没有文稿，先建一篇", "manuscripts"))

        # 催稿提醒
        urge_days = self.store.get_urge_days()
        stale = self.db.stale_submissions(urge_days)
        if stale:
            items.append((f"有 {len(stale)} 篇投稿超过 {urge_days} 天未回复，建议改投别家",
                          "records"))

        if not items:
            row = QHBoxLayout()
            row.addWidget(make_dot(theme_colors(self.store.get_theme())["primary"], 8))
            ok = QLabel("一切就绪，可以去发起投稿了")
            ok.setStyleSheet("color: #2F9E44;")
            row.addWidget(ok, 1)
            wrap = QWidget()
            wrap.setLayout(row)
            self.guide_box.addWidget(wrap)
            return
        for text, page_id in items:
            row = QHBoxLayout()
            label = QLabel("• " + text)
            row.addWidget(label, 1)
            btn = QPushButton("去处理")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, p=page_id: self.main_window.navigate(p))
            row.addWidget(btn)
            wrap = QWidget()
            wrap.setLayout(row)
            self.guide_box.addWidget(wrap)

    def _refresh_activity(self):
        self._clear_box(self.activity_box)
        activities = self.db.recent_activity(10)
        if not activities:
            empty = QLabel("暂无记录")
            empty.setObjectName("hintText")
            self.activity_box.addWidget(empty)
            inner = self.activity_scroll.widget()
            if inner is not None:
                inner.adjustSize()
            return
        for act in activities:
            self.activity_box.addWidget(_ActivityRow(
                act["text"], _relative_time(act["time"]),
                theme_colors(self.store.get_theme())["primary"]
                if act["kind"] == "投稿" else "#2F9E44",
            ))
        inner = self.activity_scroll.widget()
        if inner is not None:
            inner.adjustSize()

    # ---------- 数据统计 ----------
    def _refresh_stats(self):
        self._clear_box(self.overview_box)
        stats = self.db.platform_stats()
        has_data = bool(stats)
        self.stats_table.setVisible(has_data)
        self.stats_empty.setVisible(not has_data)
        if not has_data:
            self.stats_table.setRowCount(0)
            return

        self.stats_table.setRowCount(len(stats[:8]))
        for row, r in enumerate(stats[:8]):
            pass_rate = f"{r['passed'] / r['total'] * 100:.0f}%" if r["total"] else "—"
            for col, text in enumerate([r["platform"], str(r["total"]),
                                        str(r["replied"]), str(r["passed"]), pass_rate]):
                self.stats_table.setItem(row, col, mk_item(text))

        total_sent = sum(r["total"] for r in stats)
        total_replied = sum(r["replied"] for r in stats)
        total_passed = sum(r["passed"] for r in stats)
        avg_days = self.db.avg_reply_days()
        overview_items = [
            ("总投递", f"{total_sent} 封"),
            ("总回复", f"{total_replied} 封"),
            ("整体过稿率", f"{total_passed / total_sent * 100:.0f}%（{total_passed} 篇）"
             if total_sent else "—"),
            ("平均回复时长", f"{avg_days:.1f} 天" if avg_days is not None else "—"),
        ]
        for label_text, value_text in overview_items:
            row = QHBoxLayout()
            label = QLabel(label_text)
            label.setObjectName("hintText")
            row.addWidget(label)
            row.addStretch()
            value = QLabel(value_text)
            row.addWidget(value)
            wrap = QWidget()
            wrap.setLayout(row)
            self.overview_box.addWidget(wrap)
        if len(stats) > 8:
            note = QLabel("表格仅显示投递最多的 8 个平台，上方合计为全部平台")
            note.setObjectName("hintText")
            self.overview_box.addWidget(note)
        self.overview_box.addStretch()

    # ---------- 后台自动收信 ----------
    def _maybe_fetch(self):
        if self._fetch_worker is not None and self._fetch_worker.isRunning():
            return
        auto_fetch, _interval, lookback_days = self.store.get_fetch_config()
        if not auto_fetch:
            return
        mailboxes = self.store.load_mailboxes()
        if not any(m.enabled and m.address for m in mailboxes):
            return
        editor_emails = {e.email for e in self.db.list_editors(include_blacklisted=True)
                         if e.email}
        if not editor_emails:
            return
        self._fetch_failures: list[tuple[str, str]] = []
        self._fetch_new = 0
        self._fetch_worker = FetchWorker(mailboxes, editor_emails, lookback_days, self)
        self._fetch_worker.mailbox_result.connect(self._on_mailbox_result)
        self._fetch_worker.mailbox_failed.connect(self._on_mailbox_failed)
        self._fetch_worker.all_done.connect(self._on_fetch_done)
        self._fetch_worker.start()

    def _on_mailbox_result(self, address: str, results: list):
        """主线程写库：去重 insert replies、匹配 submissions 回写 reply_status。"""
        res = ingest_results(self.db, address, results)
        self._fetch_new = getattr(self, "_fetch_new", 0) + res.new_replies

    def _on_mailbox_failed(self, address: str, error: str):
        self._fetch_failures.append((address, error))

    def _on_fetch_done(self):
        self._fetch_worker = None
        failures = getattr(self, "_fetch_failures", [])
        if failures:
            addresses = "、".join(address for address, _error in failures)
            self.main_window.notify_status(
                f"后台收信失败（{addresses}），请到回信中心查看邮箱配置。", 10000)
        # 只发一次 data_changed（当前页刷新由 main_window 负责）；
        # 若收信发生在后台（当前页不是工作台），则自行刷新工作台统计
        self.main_window.data_changed.emit()
        if self.main_window.stack.currentWidget() is not self:
            self.refresh()
        new_n = getattr(self, "_fetch_new", 0)
        if new_n and self.store.get("notify_replies", "1") != "0":
            self.main_window.notify_tray(f"收到 {new_n} 封新回信", ms=4000)
            self.main_window.set_replies_badge(new_n)
