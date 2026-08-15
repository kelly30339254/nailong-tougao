"""工作台页：6 统计卡、新手指引、近期动态、后台自动收信。"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QTableWidget, QAbstractItemView, QHeaderView,
)

from ..workers import FetchWorker
from ..reply_ingest import ingest_results
from ..widgets import mk_item, make_dot
from ..theme import theme_colors

STAT_KEYS = ["编辑总数", "文稿", "待回复", "过稿", "退稿", "未读回信"]
# db.counts() 返回的键 → 卡片标题映射
_COUNT_KEY_MAP = {
    "编辑总数": "编辑总数", "文稿": "文稿数", "待回复": "待回复",
    "过稿": "过稿", "退稿": "退稿", "未读回信": "未读回信",
}


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


class StatCard(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("card")
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

        # 统计卡一行 6 张
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        self.stat_cards: dict[str, StatCard] = {}
        for key in STAT_KEYS:
            card = StatCard(key)
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
        activity_layout = QVBoxLayout(activity_card)
        activity_layout.setContentsMargins(16, 14, 16, 14)
        activity_title = QLabel("近期动态")
        activity_title.setObjectName("cardTitle")
        activity_layout.addWidget(activity_title)
        self.activity_box = QVBoxLayout()
        self.activity_box.setSpacing(6)
        activity_layout.addLayout(self.activity_box)
        activity_layout.addStretch()
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

        # 后台自动收信定时器
        self._fetch_timer = QTimer(self)
        self._fetch_timer.timeout.connect(self._maybe_fetch)
        auto_fetch, interval_minutes, _lookback = self.store.get_fetch_config()
        if auto_fetch:
            self._fetch_timer.start(interval_minutes * 60 * 1000)

        self.refresh()

    # ---------- 刷新 ----------
    def refresh(self):
        counts = self.db.counts()
        for title, count_key in _COUNT_KEY_MAP.items():
            self.stat_cards[title].set_value(counts.get(count_key, 0))
        self._refresh_guide()
        self._refresh_activity()
        self._refresh_stats()

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
            return
        for act in activities:
            row = QHBoxLayout()
            color = (theme_colors(self.store.get_theme())["primary"]
                     if act["kind"] == "投稿" else "#2F9E44")
            dot = make_dot(color, 8)
            row.addWidget(dot, 0, Qt.AlignTop)
            text = QLabel(act["text"])
            text.setWordWrap(True)
            row.addWidget(text, 1)
            time_label = QLabel(_relative_time(act["time"]))
            time_label.setObjectName("hintText")
            row.addWidget(time_label, 0, Qt.AlignTop)
            wrap = QWidget()
            wrap.setLayout(row)
            self.activity_box.addWidget(wrap)

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
        self._fetch_worker = FetchWorker(mailboxes, editor_emails, lookback_days, self)
        self._fetch_worker.mailbox_result.connect(self._on_mailbox_result)
        self._fetch_worker.mailbox_failed.connect(self._on_mailbox_failed)
        self._fetch_worker.all_done.connect(self._on_fetch_done)
        self._fetch_worker.start()

    def _on_mailbox_result(self, address: str, results: list):
        """主线程写库：去重 insert replies、匹配 submissions 回写 reply_status。"""
        ingest_results(self.db, address, results)

    def _on_mailbox_failed(self, address: str, error: str):
        self._fetch_failures.append((address, error))

    def _on_fetch_done(self):
        self._fetch_worker = None
        failures = getattr(self, "_fetch_failures", [])
        if failures:
            addresses = "、".join(address for address, _error in failures)
            self.main_window.statusBar().showMessage(
                f"后台收信失败（{addresses}），请到回信中心查看邮箱配置。", 10000)
        self.main_window.data_changed.emit()
        self.refresh()
