"""QSpinBox 上下按钮真点击验证（QTest）：箭头热区必须可用。

用法：.venv/Scripts/python.exe scripts/smoke_spin.py
（QT_QPA_PLATFORM=offscreen + 临时 NAILONG_DATA_DIR）
"""
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="nailong_spin_")
os.environ["NAILONG_DATA_DIR"] = _tmp
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = []


def check(name, cond):
    RESULTS.append(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        raise AssertionError(name)


from app.db import Database
from app.settings_store import SettingsStore

db = Database()
store = SettingsStore(db)

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QSpinBox, QStyle, QStyleOptionSpinBox
from PySide6.QtTest import QTest

qapp = QApplication.instance() or QApplication([])

from app.main_window import MainWindow
from app.theme import render_qss, apply_theme

# 渲染后样式包含上下箭头 SVG 路径
qss = render_qss("蔷薇粉")
check("qss 含 arrow_up/arrow_down 且无遗留占位符",
      "arrow_up.svg" in qss and "arrow_down.svg" in qss
      and "{arrow_up}" not in qss and "{arrow_down}" not in qss)

win = MainWindow(db, store)
win.show()
qapp.processEvents()
settings_page = win._pages["settings"]
settings_page.tabs.setCurrentIndex(2)  # 投递策略
qapp.processEvents()


def click_spin(spin: QSpinBox, up: bool):
    opt = QStyleOptionSpinBox()
    spin.initStyleOption(opt)
    sc = QStyle.SC_SpinBoxUp if up else QStyle.SC_SpinBoxDown
    rect = spin.style().subControlRect(QStyle.CC_SpinBox, opt, sc, spin)
    return rect


for name, spin in (("每封间隔", settings_page.interval_spin),
                   ("每日上限", settings_page.daily_limit_spin),
                   ("催稿提醒", settings_page.urge_days_spin)):
    up_rect = click_spin(spin, True)
    down_rect = click_spin(spin, False)
    check(f"{name}：上下按钮矩形有效", up_rect.isValid() and down_rect.isValid()
          and up_rect.width() >= 16 and down_rect.width() >= 16)
    v0 = spin.value()
    QTest.mouseClick(spin, Qt.LeftButton, pos=up_rect.center())
    qapp.processEvents()
    check(f"{name}：点上箭头 +1", spin.value() == v0 + 1)
    QTest.mouseClick(spin, Qt.LeftButton, pos=down_rect.center())
    qapp.processEvents()
    check(f"{name}：点下箭头 -1", spin.value() == v0)

print()
print(f"全部通过：{sum(RESULTS)}/{len(RESULTS)} 项")
