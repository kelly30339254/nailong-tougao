"""复现工作台「近期动态」溢出：10 条长文案 + 1280x800，截图并检查可滚动。"""
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="nailong_activity_")
os.environ["NAILONG_DATA_DIR"] = _tmp
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import Database
from app.settings_store import SettingsStore
from app.models import Editor, Manuscript, Submission, Reply, MailboxConfig

db = Database()
store = SettingsStore(db)
store.save_fetch_config(False, 30, 45)
store.save_mailbox(0, MailboxConfig(enabled=True, address="me@qq.com", auth_code="x"))

ed = db.insert_editor(Editor(name="工直编辑室张小北丁旧应", platform="工直文学",
                             email="ed@example.com", genres="短篇"))
ms = db.insert_manuscript(Manuscript(title="小二生下雨那天路过下塘的小旧应仔", word_count=8000))

titles = [
    "向 工直编辑室张小北丁旧应 投递《小二生下雨那天路过下塘的小旧应仔》（已发）",
    "收到 工直编辑室张小北丁旧应 的回信（过稿）：Re: 投稿《小二生下雨那天路过下塘的小旧应仔》",
]
for i in range(10):
    sid = db.insert_submission(Submission(
        manuscript_id=ms, editor_id=ed, from_mailbox="me@qq.com",
        to_email="ed@example.com",
        subject=f"投稿《小二生下雨那天路过下塘的小旧应仔》第{i+1}封",
    ))
    db.update_status(sid, "已发", sent_at=f"2026-07-{10 - i:02d} 10:00:00")
    if i % 2 == 0:
        db.insert_reply(Reply(
            submission_id=sid, from_email="ed@example.com",
            subject=f"Re: 投稿《小二生下雨那天路过下塘的小旧应仔》第{i+1}封",
            snippet="审阅通过", verdict="过稿", imap_uid=f"u{i}",
            received_at=f"2026-07-{10 - i:02d} 18:00:00",
        ))

from PySide6.QtWidgets import QApplication
from app.main_window import MainWindow

qapp = QApplication.instance() or QApplication([])
win = MainWindow(db, store)
win.resize(1280, 800)
win.show()
win.navigate("dashboard")
qapp.processEvents()

page = win._pages["dashboard"]
scroll = page.activity_scroll
inner = scroll.widget()
qapp.processEvents()

out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
os.makedirs(out_dir, exist_ok=True)
win.grab().save(os.path.join(out_dir, "fix_activity_top.png"))

bar = scroll.verticalScrollBar()
inner_h = inner.sizeHint().height()
view_h = scroll.viewport().height()
print(f"inner_hint={inner_h} viewport={view_h} bar_max={bar.maximum()} rows={page.activity_box.count()}")

# 滚到底，确认最后一条和「查看全部」都在
bar.setValue(bar.maximum())
qapp.processEvents()
win.grab().save(os.path.join(out_dir, "fix_activity_bottom.png"))
page.activity_scroll.parentWidget().grab().save(os.path.join(out_dir, "fix_activity_card.png"))

# 滚到底后仍应能看到最后一条动态，不能整片空白
last_row = None
for i in range(page.activity_box.count()):
    w = page.activity_box.itemAt(i).widget()
    if w is not None:
        last_row = w
last_visible = False
if last_row is not None:
    top_left = last_row.mapTo(scroll.viewport(), last_row.rect().topLeft())
    bottom_y = top_left.y() + last_row.height()
    last_visible = bottom_y > 0 and top_left.y() < scroll.viewport().height()
    print(f"last_row_y={top_left.y()} last_h={last_row.height()} last_text={last_row._label.text()[:40]}")

ok = (view_h > 0 and (bar.maximum() > 0 or inner_h <= view_h + 4) and last_visible)
print("SCROLL_OK" if ok else "SCROLL_FAIL")
if not ok:
    sys.exit(1)
print("DONE", out_dir)
