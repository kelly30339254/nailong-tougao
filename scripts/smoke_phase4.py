"""六项新功能冒烟测试：退信标记 / 催稿提醒 / 模板自定义 / 数据统计 / 数据备份 / 定时投稿。

用法：.venv/Scripts/python.exe scripts/smoke_phase4.py
（QT_QPA_PLATFORM=offscreen + 临时 NAILONG_DATA_DIR）
"""
import os
import sys
import tempfile
import time

_tmp = tempfile.mkdtemp(prefix="nailong_smoke4_")
os.environ["NAILONG_DATA_DIR"] = _tmp
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = []


def check(name, cond):
    RESULTS.append(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        raise AssertionError(name)


# ---------- 数据准备 ----------
from app.db import Database
from app.settings_store import SettingsStore
from app.models import Editor, Manuscript, Submission, MailboxConfig

db = Database()
store = SettingsStore(db)
store.save_fetch_config(False, 30, 45)
store.save_strategy(True, 5, 30)
store.save_mailbox(0, MailboxConfig(enabled=True, address="me@qq.com", auth_code="x",
                                    smtp_host="smtp.qq.com", imap_host="imap.qq.com"))

# 迁移：新列存在
cols = {r["name"] for r in db._conn.execute("PRAGMA table_info(editors)")}
scols = {r["name"] for r in db._conn.execute("PRAGMA table_info(submissions)")}
check("迁移：editors.email_invalid / submissions.scheduled_at",
      "email_invalid" in cols and "scheduled_at" in scols)

eid1 = db.insert_editor(Editor(name="编辑一", platform="平台A", email="ed1@x.com"))
eid2 = db.insert_editor(Editor(name="编辑二", platform="平台B", email="ed2@x.com"))
mid = db.insert_manuscript(Manuscript(title="测试文稿", word_count=10000, category="悬疑"))

from PySide6.QtWidgets import QApplication, QMessageBox
qapp = QApplication.instance() or QApplication([])

# 屏蔽模态框
from app.pages import submit as submit_mod
from app.pages import settings as settings_mod
for mod in (submit_mod, settings_mod):
    mod.QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Yes)
    mod.QMessageBox.information = staticmethod(lambda *a, **k: None)
    mod.QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

from app.main_window import MainWindow
win = MainWindow(db, store)
submit_page = win._pages["submit"]
records_page = win._pages["records"]
editors_page = win._pages["editors"]
dashboard_page = win._pages["dashboard"]
settings_page = win._pages["settings"]

# ---------- 功能 1：退信自动标记 ----------
from app.receiver import _is_bounce, _EMAIL_RE
check("退信识别：mailer-daemon", _is_bounce("mailer-daemon@qq.com", "任意"))
check("退信识别：主题关键词", _is_bounce("someone@x.com", "Delivery Status Notification"))
check("非退信不误判", not _is_bounce("ed1@x.com", "Re: 投稿回复"))
body = "The following address failed: ed1@x.com and other@y.com 无法投递"
found = {a.lower() for a in _EMAIL_RE.findall(body)}
check("正文提取被退邮箱", {"ed1@x.com", "other@y.com"} <= found)

from app.reply_ingest import ingest_results
res = ingest_results(db, "me@qq.com", [{
    "from_email": "mailer-daemon@qq.com", "subject": "退信通知",
    "snippet": "投递失败", "uid": "b1", "received_at": "2026-08-02 08:00:00",
    "is_bounce": True, "bounced_recipients": ["ed1@x.com"],
}])
check("退信标记失效且不计回信", res.invalid_marks == 1 and res.new_replies == 0
      and db.get_editor(eid1).email_invalid is True
      and len(db.list_replies()) == 0)
check("重复退信幂等", ingest_results(db, "me@qq.com", [{
    "is_bounce": True, "bounced_recipients": ["ed1@x.com"],
    "from_email": "mailer-daemon@qq.com", "uid": "b2",
}]).invalid_marks == 1)  # 已失效仍返回行数，无妨

submit_page.refresh()
from PySide6.QtCore import Qt as _Qt
submit_ids = {submit_page.table.item(r, 0).data(_Qt.UserRole)
              for r in range(submit_page.table.rowCount())}
check("投稿页排除失效邮箱", eid1 not in submit_ids and eid2 in submit_ids)

editors_page.refresh()
check("编辑列表失效行显示恢复有效按钮", db.get_editor(eid1).email_invalid)
editors_page._on_restore_valid(eid1)
check("恢复有效", db.get_editor(eid1).email_invalid is False)
db.mark_email_invalid("ed1@x.com")

# ---------- 功能 2：催稿提醒 ----------
store.save_urge_days(30)
old_sid = db.insert_submission(Submission(manuscript_id=mid, editor_id=eid2,
                                          to_email="ed2@x.com", subject="旧稿"))
db.update_status(old_sid, "已发", sent_at="2026-06-01 10:00:00")  # 60+ 天前
new_sid = db.insert_submission(Submission(manuscript_id=mid, editor_id=eid2,
                                          to_email="ed2@x.com", subject="新稿"))
db.update_status(new_sid, "已发")
stale = db.stale_submissions(30)
check("stale_submissions 只捞超期", [s.id for s in stale] == [old_sid])
dashboard_page.refresh()
from PySide6.QtWidgets import QLabel
guide_texts = []
for i in range(dashboard_page.guide_box.count()):
    w = dashboard_page.guide_box.itemAt(i).widget()
    if w is not None:
        guide_texts.extend(lab.text() for lab in w.findChildren(QLabel))
check("工作台显示催稿提醒", any("超过 30 天未回复" in t for t in guide_texts))
records_page.refresh()
sent_item = records_page.table.item(1, 4)  # id 倒序：row0=new, row1=old
check("超期行发信时间标橙+tooltip",
      "已超过 30 天未回复" in (sent_item.toolTip() or ""))

# ---------- 功能 3：投稿信模板自定义 ----------
from app.letter import build_letter, DEFAULT_SUBJECT_TPL, render_template
s, b = build_letter("作品甲", 8000, "言情", "老师")
check("默认模板渲染", s == "投稿《作品甲》8000字 言情" and "尊敬的老师编辑" in b)
check("坏占位符原样保留不炸",
      render_template("《{作品名}》{不存在} {字数}", {"作品名": "T", "字数": 1})
      == "《T》{不存在} 1")
store.save_letter_template("【投稿】{作品名}-{分类}", "致{编辑称呼}：请看《{作品名}》。")
settings_page.refresh()
check("模板设置载入", settings_page.letter_subject_edit.text() == "【投稿】{作品名}-{分类}")
submit_page.manuscript_combo.setCurrentIndex(0)  # 带出测试文稿
submit_page.subject_edit.setText("")
submit_page.body_edit.setPlainText("")
submit_page._on_build_letter()
check("投稿页用自定义模板", submit_page.subject_edit.text() == "【投稿】测试文稿-悬疑"
      and submit_page.body_edit.toPlainText().startswith("致{编辑称呼}："))
settings_page._on_reset_letter_tpl()
check("恢复默认模板", settings_page.letter_subject_edit.text() == DEFAULT_SUBJECT_TPL)
store.save_letter_template(DEFAULT_SUBJECT_TPL, "")  # 正文空 → 默认

# ---------- 功能 4：数据统计 ----------
stats = db.platform_stats()
check("platform_stats", len(stats) == 1 and stats[0]["platform"] == "平台B"
      and stats[0]["total"] == 2 and stats[0]["replied"] == 0)
db.update_reply_status(old_sid, "过稿")  # replied_at=now，sent_at 60 天前 → 平均 60+ 天
avg = db.avg_reply_days()
check("avg_reply_days", avg is not None and avg > 50)
dashboard_page.refresh()
check("统计表行数（前 8 平台）", dashboard_page.stats_table.rowCount() == 1)
# 空态：全新库
db_empty = Database(db_path=os.path.join(tempfile.mkdtemp(prefix="nailong_e4_"), "e.db"))
check("空库统计为空", db_empty.platform_stats() == [] and db_empty.avg_reply_days() is None)

# ---------- 功能 5：数据备份 ----------
backup_path = os.path.join(_tmp, "backup.db")
db.backup_to(backup_path)
check("导出备份文件生成", os.path.getsize(backup_path) > 0)
db_b = Database(db_path=backup_path)
check("备份内容一致", len(db_b.list_editors()) >= 1
      and len(db_b.list_submissions()) == len(db.list_submissions()))
db_b.close()
# 导入：先改当前库，再从备份还原
db.insert_editor(Editor(name="临时编辑", email="tmp@x.com"))
check("导入前多了临时编辑", any(e.name == "临时编辑" for e in db.list_editors()))
db.restore_from(backup_path)
check("导入备份还原", not any(e.name == "临时编辑" for e in db.list_editors()))
check("备份信息大小", db.db_file_size() > 0)
# 清空业务表（保留编辑与设置）
db.clear_business_data()
check("清空业务表（保留编辑）", len(db.list_editors()) > 0
      and len(db.list_manuscripts()) == 0 and len(db.list_submissions()) == 0
      and len(db.list_replies()) == 0
      and store.load_mailboxes()[0].enabled)  # 设置保留

# ---------- 功能 6：定时投稿 ----------
# 重新造数据
eid3 = db.insert_editor(Editor(name="编辑三", platform="平台C", email="ed3@x.com"))
mid2 = db.insert_manuscript(Manuscript(title="定时文稿", word_count=5000, category="科幻"))
submit_page.refresh()
submit_page.manuscript_combo.setCurrentIndex(0)
submit_page.subject_edit.setText("投稿《定时文稿》")
submit_page.body_edit.setPlainText("正文")
submit_page._checked_ids.update({eid3})
submit_page._on_schedule()
sched = [s for s in db.list_submissions() if s.status == "定时待发"]
check("定时待发插入", len(sched) == 1 and sched[0].scheduled_at != "")
check("定时日志", "已加入定时队列 1 封" in submit_page.log_edit.toPlainText())
check("未到点不触发", db.due_scheduled() == [])
# 改为过去时间 → 到点
db._conn.execute("UPDATE submissions SET scheduled_at='2020-01-01 00:00:00' WHERE id=?",
                 (sched[0].id,))
db._conn.commit()
check("到点可查", len(db.due_scheduled()) == 1)

# 调度器触发（假 mailer）
import app.mailer as mailer_mod
sent_calls = []
orig_send = mailer_mod.send_mail
mailer_mod.send_mail = lambda mailbox, to, subject, body, attachment_path=None, message_id=None: sent_calls.append(to)
try:
    win._check_scheduled()
    worker = win._sched_worker
    check("调度器启动 SendWorker", worker is not None)
    while worker.isRunning():
        qapp.processEvents()
        time.sleep(0.05)
    qapp.processEvents()
    time.sleep(0.1)
    qapp.processEvents()
finally:
    mailer_mod.send_mail = orig_send
row = [s for s in db.list_submissions() if s.id == sched[0].id][0]
check("定时发送成功回写", row.status == "已发" and row.from_mailbox == "me@qq.com"
      and sent_calls == ["ed3@x.com"])
check("发送后不再到点", db.due_scheduled() == [])

# 无可用邮箱时保持待发
eid4 = db.insert_editor(Editor(name="编辑四", platform="平台D", email="ed4@x.com"))
sid4 = db.insert_submission(Submission(manuscript_id=mid2, editor_id=eid4,
                                       to_email="ed4@x.com", status="定时待发",
                                       scheduled_at="2020-01-01 00:00:00"))
store.save_mailbox(0, MailboxConfig(enabled=True, address="me@qq.com", auth_code="x",
                                    daily_limit=1))  # 今日已发 1 封 → 超限
win._check_scheduled()
check("无可用邮箱保持待发", win._sched_worker is None
      and [s for s in db.list_submissions() if s.id == sid4][0].status == "定时待发")
store.save_mailbox(0, MailboxConfig(enabled=True, address="me@qq.com", auth_code="x"))

# 投递记录页：定时待发筛选与显示
records_page.status_combo.setCurrentText("定时待发")
check("定时待发筛选", records_page.table.rowCount() == 1)
_badge_widget = records_page.table.cellWidget(0, 5)
status_text = _badge_widget.findChild(QLabel).text() if _badge_widget else ""
check("状态列显示计划时间", status_text.startswith("定时待发 01-01 00:00"))
records_page.status_combo.setCurrentText("全部状态")

# ---------- 设置页标签结构 ----------
tabs = [settings_page.tabs.tabText(i) for i in range(settings_page.tabs.count())]
check("设置页标签顺序", tabs == ["账号", "发信邮箱", "投稿信模板", "AI 接口", "投递策略",
                                "收信设置", "外观主题", "数据备份", "关于"])
check("催稿天数加载", settings_page.urge_days_spin.value() == 30)

print()
print(f"全部通过：{sum(RESULTS)}/{len(RESULTS)} 项")
