"""UI 截图脚本：offscreen + 演示数据，逐页 grab 存 PNG 到 scripts/shots/。

用法：.venv/Scripts/python.exe scripts/shoot.py [输出目录]
默认输出 scripts/shots/；最终版用 scripts/shots/final/。
"""
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="nailong_shots_")
os.environ["NAILONG_DATA_DIR"] = _tmp
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# offscreen 平台默认字体库不含中文，指定系统字体目录避免文字豆腐块
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUT_DIR = (sys.argv[1] if len(sys.argv) > 1
           else os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots"))
os.makedirs(OUT_DIR, exist_ok=True)

from app.db import Database
from app.settings_store import SettingsStore
from app.models import Editor, Manuscript, Submission, Reply, MailboxConfig

db = Database()
store = SettingsStore(db)
store.save_fetch_config(False, 30, 45)
store.save_mailbox(0, MailboxConfig(enabled=True, address="me@qq.com", auth_code="x",
                                    smtp_host="smtp.qq.com", imap_host="imap.qq.com"))

# 演示数据
e1 = db.insert_editor(Editor(name="夏暖", platform="四季文学", email="xianuan@sijiwenxue.com",
                             genres="短篇、长篇", fee_info="千字100-300",
                             source_url="https://sijiwenxue.com/news/topic/3",
                             notes="主收爽文、历史爽文、现实文", favorite=True))
e2 = db.insert_editor(Editor(name="木兰", platform="墨香中文网", email="mulan@moxiang.com",
                             genres="言情、世情", fee_info="千字80",
                             notes="邮箱已失效示例", email_invalid=True))
e3 = db.insert_editor(Editor(name="青崖", platform="晋江文学城", email="qingya@jjwxc.net",
                             genres="奇幻、科幻", fee_info="千字120-500"))
e4 = db.insert_editor(Editor(name="白鹿原编辑", platform="起点中文网", email="bailu@qidian.com",
                             genres="现实、武侠", favorite=True))
db.insert_editor(Editor(name="银杏文学编辑", platform="银杏文学", email="yinxing@yinxiang.cn",
                        genres="悬疑、惊悚", blacklisted=True,
                        notes="【已停止收稿】2025 年起暂停收稿"))
# insert_editor 不写 email_invalid 列，失效标记走正式退信标记路径
db.mark_email_invalid("mulan@moxiang.com")

m1 = db.insert_manuscript(Manuscript(title="深夜便利店的女人", word_count=12800,
                                     category="悬疑", reader_group="女频", emotion="虐",
                                     style="第一人称", genre_type="短篇现实悬疑",
                                     created_at="2026-07-20 21:30:00"))
m2 = db.insert_manuscript(Manuscript(title="回到明朝当赘婿", word_count=35600,
                                     category="脑洞", reader_group="男频", emotion="爽",
                                     style="第三人称", genre_type="历史穿越",
                                     created_at="2026-07-28 09:12:00"))

s1 = db.insert_submission(Submission(manuscript_id=m1, editor_id=e1,
                                     from_mailbox="me@qq.com", to_email="xianuan@sijiwenxue.com",
                                     subject="投稿《深夜便利店的女人》12800字 悬疑"))
db.update_status(s1, "已发", sent_at="2026-06-20 14:22:00")   # 超期未回复
s2 = db.insert_submission(Submission(manuscript_id=m1, editor_id=e3,
                                     from_mailbox="me@qq.com", to_email="qingya@jjwxc.net",
                                     subject="投稿《深夜便利店的女人》12800字 悬疑"))
db.update_status(s2, "已发", sent_at="2026-07-30 10:05:00")
db.update_reply_status(s2, "过稿")
s3 = db.insert_submission(Submission(manuscript_id=m2, editor_id=e4,
                                     from_mailbox="me@qq.com", to_email="bailu@qidian.com",
                                     subject="投稿《回到明朝当赘婿》35600字 脑洞"))
db.update_status(s3, "已发", sent_at="2026-08-01 16:40:00")
db.update_reply_status(s3, "退稿")
s4 = db.insert_submission(Submission(manuscript_id=m2, editor_id=e2,
                                     to_email="mulan@moxiang.com",
                                     subject="投稿《回到明朝当赘婿》35600字 脑洞"))
db.update_status(s4, "失败", sent_at="2026-08-01 16:41:00")
db.insert_submission(Submission(manuscript_id=m1, editor_id=e4,
                                to_email="bailu@qidian.com",
                                subject="投稿《深夜便利店的女人》12800字 悬疑",
                                status="定时待发", scheduled_at="2026-08-03 10:00:00"))

db.insert_reply(Reply(submission_id=s2, from_email="qingya@jjwxc.net",
                      subject="Re: 投稿《深夜便利店的女人》",
                      snippet="你好，来稿已审阅，我们决定采用这篇稿件，后续会有签约编辑与你联系，请保持电话畅通。",
                      verdict="过稿", imap_uid="u1", received_at="2026-08-01 09:20:00"))
db.insert_reply(Reply(submission_id=s3, from_email="bailu@qidian.com",
                      subject="Re: 投稿《回到明朝当赘婿》",
                      snippet="很遗憾，本篇与我们的收稿方向不太合适，建议改投他处，祝创作顺利。",
                      verdict="退稿", imap_uid="u2", received_at="2026-08-02 08:15:00"))
db.insert_reply(Reply(submission_id=s1, from_email="xianuan@sijiwenxue.com",
                      subject="Re: 投稿《深夜便利店的女人》",
                      snippet="稿件整体不错，但结尾需要修改润色，建议强化反转后再投一版。",
                      verdict="需修改", imap_uid="u3", received_at="2026-08-02 11:02:00"))

# 售出演示数据
from app.models import Sale
db.insert_sale(Sale(manuscript_id=m1, platform="番茄小说", editor_name="夏暖",
                    amount=800.0, sale_date="2026-07-15", payment_month="2026-08",
                    notes="买断，首篇合作"))
db.insert_sale(Sale(manuscript_id=m2, platform="七猫中文网", editor_name="白鹿原编辑",
                    amount=1500.0, sale_date="2026-08-01", payment_month="2026-09",
                    notes=""))

from PySide6.QtWidgets import QApplication
qapp = QApplication.instance() or QApplication([])

from app.main_window import MainWindow, NAV_ITEMS
from app.theme import apply_theme

win = MainWindow(db, store)
win.resize(1280, 800)
win.show()
qapp.processEvents()


def shoot(name: str):
    path = os.path.join(OUT_DIR, f"{name}.png")
    win.grab().save(path)
    print("saved", path)


for page_id, title in NAV_ITEMS:
    win.navigate(page_id)
    qapp.processEvents()
    shoot(f"page_{page_id}")

# 投稿页勾选几家后重抓（展示勾选态与统计）
submit = win._pages["submit"]
submit._checked_ids.update({e1, e3, e4})
submit._reload_editors_table()
submit.subject_edit.setText("投稿《深夜便利店的女人》12800字 悬疑")
submit.body_edit.setPlainText("尊敬的编辑：\n\n    您好！\n")
qapp.processEvents()
win.navigate("submit")
qapp.processEvents()
shoot("page_submit_filled")

# 1440x900 的工作台与投稿页
win.resize(1440, 900)
win.show()
qapp.processEvents()
win.navigate("dashboard")
qapp.processEvents()
shoot("wide_dashboard")
win.navigate("submit")
qapp.processEvents()
shoot("wide_submit")

# 投稿页滚到底部（确认定时投递/按钮/日志区）
win.resize(1280, 800)
win.show()
win.navigate("submit")
from PySide6.QtWidgets import QScrollArea
scroll = submit.findChild(QScrollArea)
if scroll is not None:
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
qapp.processEvents()
shoot("page_submit_bottom")

print("DONE", OUT_DIR)
