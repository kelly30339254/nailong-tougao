"""稿费记录功能冒烟测试：sales CRUD / 文稿库已售徽章 / 对话框校验 / 统计 / 动态。

用法：.venv/Scripts/python.exe scripts/smoke_sales.py
（QT_QPA_PLATFORM=offscreen + 临时 NAILONG_DATA_DIR）
"""
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="nailong_sales_")
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
from app.models import Manuscript, Sale, MailboxConfig

db = Database()
store = SettingsStore(db)
store.save_fetch_config(False, 30, 45)
store.save_mailbox(0, MailboxConfig(enabled=True, address="me@qq.com", auth_code="x"))

# 迁移建表
tables = {r["name"] for r in db._conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'")}
check("sales 表已建", "sales" in tables)

m1 = db.insert_manuscript(Manuscript(title="深夜便利店的女人", word_count=12800, category="悬疑"))
m2 = db.insert_manuscript(Manuscript(title="回到明朝当赘婿", word_count=35600, category="脑洞"))

# ---------- CRUD 往返 ----------
sid = db.insert_sale(Sale(manuscript_id=m1, platform="番茄小说", editor_name="夏暖",
                          amount=800.0, sale_date="2026-07-15", payment_month="2026-08",
                          notes="买断"))
check("insert/list", len(db.list_sales()) == 1
      and db.list_sales()[0].manuscript_title == "深夜便利店的女人")
sale = db.list_sales()[0]
sale.amount = 1000.0
sale.payment_month = "2026-09"
db.update_sale(sale)
check("update", db.list_sales()[0].amount == 1000.0
      and db.list_sales()[0].payment_month == "2026-09")
db.insert_sale(Sale(manuscript_id=m1, platform="七猫", amount=None, sale_date="2026-08-01"))
check("空金额可存", len(db.sales_for_manuscript(m1)) == 2
      and db.sales_for_manuscript(m1)[0].amount is None)
count, total = db.sales_summary()
check("summary 篇数+合计", count == 2 and total == 1000.0)  # 1000（改后）+ 空金额不计

from PySide6.QtWidgets import QApplication, QMessageBox, QDialog, QLabel
qapp = QApplication.instance() or QApplication([])

# 屏蔽模态框
from app.pages import sales as sales_mod
warnings = []
sales_mod.QMessageBox.warning = staticmethod(lambda *a, **k: warnings.append(a[-1]))
sales_mod.QMessageBox.information = staticmethod(lambda *a, **k: None)
sales_mod.QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
from app.pages import manuscripts as ms_mod
ms_mod.QMessageBox.warning = staticmethod(lambda *a, **k: warnings.append(a[-1]))
ms_mod.QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

from app.main_window import MainWindow
win = MainWindow(db, store)
sales_page = win._pages["sales"]
manuscripts_page = win._pages["manuscripts"]
dashboard_page = win._pages["dashboard"]

# ---------- 对话框校验 ----------
from app.pages.sales import SaleDialog, MONTH_RE

dlg = SaleDialog(db, sales_page)
dlg.amount_edit.setText("八百块")
dlg._on_save()
check("金额非数字拒绝", any("数字" in w for w in warnings) and dlg.result() != QDialog.Accepted)
warnings.clear()
dlg.amount_edit.setText("800")
dlg.month_edit.setText("2026年9月")
dlg._on_save()
check("月份格式拒绝", any("yyyy-MM" in w for w in warnings) and dlg.result() != QDialog.Accepted)
warnings.clear()
dlg.month_edit.setText("2026-09")
dlg._on_save()
check("合法表单通过", dlg.result() == QDialog.Accepted
      and dlg.sale.manuscript_id in (m1, m2)
      and dlg.sale.amount == 800.0 and dlg.sale.payment_month == "2026-09")
check("月份正则", MONTH_RE.match("2026-09") and not MONTH_RE.match("2026-9")
      and not MONTH_RE.match("26-09"))
# 空库文稿下拉
db_empty = Database(db_path=os.path.join(tempfile.mkdtemp(prefix="nailong_es_"), "e.db"))
dlg2 = SaleDialog(db_empty, sales_page)
dlg2._on_save()
check("无文稿时必选校验", any("文稿" in w for w in warnings))
warnings.clear()

# ---------- 文稿库联动 ----------
manuscripts_page.refresh()
# m1 有售出 → 第 1 行（id 倒序 m2 无售出, m1 row? id DESC → m2 row0, m1 row1）
badge_widget = manuscripts_page.table.cellWidget(1, 1)
badge_label = badge_widget.findChild(QLabel) if badge_widget else None
check("已售徽章出现", badge_label is not None and badge_label.text() == "已售")
check("徽章 tooltip 详情（取最新一条售出）", "七猫" in (badge_label.toolTip() or ""))
check("未售文稿无徽章", manuscripts_page.table.cellWidget(0, 1) is None)

# 行内"售出"按钮预选（patch exec：先走 _on_save 把表单写进 sale，再接受）
orig_exec = SaleDialog.exec
def _fake_exec(self):
    self._on_save()
    return self.result() or QDialog.Accepted
SaleDialog.exec = _fake_exec
try:
    manuscripts_page._on_add_sale(m2)
finally:
    SaleDialog.exec = orig_exec
m2_sales = db.sales_for_manuscript(m2)
check("售出按钮预选文稿", len(m2_sales) == 1 and m2_sales[0].manuscript_id == m2)

# 对话框预选校验
dlg3 = SaleDialog(db, sales_page, preselect_manuscript_id=m1)
check("对话框预选文稿", dlg3.ms_combo.currentData() == m1)

# ---------- 稿费记录页 ----------
sales_page.refresh()
check("售出表格行数", sales_page.table.rowCount() == 3)  # m1×2 + m2×1
check("统计文案", "已售出 3 篇" in sales_page.summary_label.text()
      and "稿费合计" in sales_page.summary_label.text())
# 删除一条
before = len(db.list_sales())
sales_page._on_delete(db.list_sales()[0])
check("删除售出记录", len(db.list_sales()) == before - 1)

# ---------- 工作台动态 ----------
acts = db.recent_activity(10)
check("recent_activity 含售出", any(a["kind"] == "售出" and "售出《" in a["text"]
                                    for a in acts))
dashboard_page.refresh()

# ---------- 删文稿连带删 sales ----------
db.delete_manuscript(m1)
check("删文稿连带删售出", db.sales_for_manuscript(m1) == []
      and all(s.manuscript_id != m1 for s in db.list_sales()))

# ---------- 导航 ----------
win.navigate("sales")
check("navigate sales", win.stack.currentWidget() is sales_page)
# 空态
db2 = Database(db_path=os.path.join(tempfile.mkdtemp(prefix="nailong_es2_"), "e2.db"))
check("空库 summary 为 0", db2.sales_summary() == (0, 0))

print()
print(f"全部通过：{sum(RESULTS)}/{len(RESULTS)} 项")
