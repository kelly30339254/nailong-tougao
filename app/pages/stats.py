"""统计分析：时间范围 + 趋势 + 漏斗 + 文稿排行。"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPainter, QPen, QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QTableWidget, QFrame, QAbstractItemView, QHeaderView,
)

from ..widgets import mk_item, export_csv


class TrendChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._points: list[tuple[str, int]] = []
        self.setMinimumHeight(160)

    def set_points(self, points: list[tuple[str, int]]):
        self._points = points
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, QColor("#FFFFFF"))
        if not self._points:
            painter.setPen(QColor("#A0989E"))
            painter.drawText(self.rect(), Qt.AlignCenter, "暂无趋势数据")
            return
        values = [v for _d, v in self._points]
        vmax = max(values) or 1
        left, top, right, bottom = 36, 10, w - 10, h - 24
        painter.setPen(QColor("#E7E2E7"))
        painter.drawLine(left, bottom, right, bottom)
        painter.drawLine(left, top, left, bottom)
        n = len(self._points)
        span = max(right - left, 1)
        pts = []
        for i, (_d, v) in enumerate(self._points):
            x = left + span * i / max(n - 1, 1)
            y = bottom - (bottom - top) * v / vmax
            pts.append(QPointF(x, y))
        painter.setPen(QPen(QColor("#D6336C"), 2))
        for a, b in zip(pts, pts[1:]):
            painter.drawLine(a, b)
        painter.setFont(QFont(self.font().family(), 8))
        painter.setPen(QColor("#A0989E"))
        painter.drawText(4, top + 10, str(vmax))
        painter.drawText(4, bottom, "0")


class StatsPage(QWidget):
    RANGES = [("近 7 天", 7), ("近 30 天", 30), ("近 90 天", 90), ("全部", 0)]

    def __init__(self, db, store, main_window):
        super().__init__()
        self.db = db
        self.store = store
        self.main_window = main_window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        tool = QHBoxLayout()
        tool.addWidget(QLabel("时间范围"))
        self.range_combo = QComboBox()
        for label, _days in self.RANGES:
            self.range_combo.addItem(label)
        self.range_combo.currentIndexChanged.connect(self.refresh)
        tool.addWidget(self.range_combo)
        export_btn = QPushButton("导出统计 CSV")
        export_btn.clicked.connect(self._on_export)
        tool.addWidget(export_btn)
        tool.addStretch()
        layout.addLayout(tool)

        self.funnel = QLabel()
        self.funnel.setObjectName("hintText")
        self.funnel.setWordWrap(True)
        layout.addWidget(self.funnel)

        chart_card = QFrame()
        chart_card.setObjectName("card")
        chart_box = QVBoxLayout(chart_card)
        chart_box.addWidget(QLabel("投递量趋势"))
        self.chart = TrendChart()
        chart_box.addWidget(self.chart)
        layout.addWidget(chart_card)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["文稿", "投递", "回复", "过稿", "过稿率"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        self._rows: list[list] = []
        self.refresh()

    def _since(self) -> str:
        days = self.RANGES[self.range_combo.currentIndex()][1]
        if not days:
            return ""
        return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    def refresh(self):
        since = self._since()
        subs = self.db.list_submissions()
        if since:
            subs = [s for s in subs if (s.sent_at or s.scheduled_at or "")[:10] >= since
                    or (not s.sent_at and s.status in ("待发", "定时待发"))]
        sent = [s for s in subs if s.status == "已发"]
        replied = [s for s in sent if s.reply_status and s.reply_status != "无"]
        passed = [s for s in sent if s.reply_status == "过稿"]
        sales = self.db.list_sales() if hasattr(self.db, "list_sales") else []
        sold = len(sales)
        self.funnel.setText(
            f"漏斗：投递 {len(sent)} → 有回复 {len(replied)} → 过稿 {len(passed)} → 售出 {sold}")
        by_day: dict[str, int] = defaultdict(int)
        for s in sent:
            day = (s.sent_at or "")[:10]
            if day:
                by_day[day] += 1
        points = sorted(by_day.items())[-30:]
        self.chart.set_points(points)
        manuscripts = {m.id: m for m in self.db.list_manuscripts()}
        agg: dict[int, list] = {}
        for s in sent:
            rec = agg.setdefault(s.manuscript_id or 0, [0, 0, 0])
            rec[0] += 1
            if s.reply_status and s.reply_status != "无":
                rec[1] += 1
            if s.reply_status == "过稿":
                rec[2] += 1
        ranked = sorted(agg.items(), key=lambda kv: (-kv[1][2], -kv[1][0]))
        self.table.setRowCount(len(ranked))
        self._rows = []
        for row, (mid, rec) in enumerate(ranked):
            title = manuscripts[mid].title if mid in manuscripts else "（已删除）"
            rate = f"{rec[2] / rec[0] * 100:.0f}%" if rec[0] else "-"
            vals = [title, str(rec[0]), str(rec[1]), str(rec[2]), rate]
            self._rows.append(vals)
            for col, text in enumerate(vals):
                self.table.setItem(row, col, mk_item(text))

    def _on_export(self):
        export_csv(self, ["文稿", "投递", "回复", "过稿", "过稿率"],
                   self._rows, "投稿统计.csv")
