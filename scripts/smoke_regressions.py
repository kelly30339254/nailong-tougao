"""本轮功能与缺陷修复的离线回归测试。"""
import json
import os
import sqlite3
import sys
import tempfile

tmp = tempfile.mkdtemp(prefix="nailong_regression_")
os.environ["NAILONG_DATA_DIR"] = tmp
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app import mailer, receiver
from app.db import Database
from app.main_window import LINKS, MainWindow, TUTORIAL_ACTION
from app.models import Editor, MailboxConfig, Manuscript, Reply, Sale, Submission
from app.reply_ingest import ingest_results
from app.settings_store import SettingsStore
from app.workers import FetchWorker, SendWorker

results = []


def check(name, condition):
    condition = bool(condition)
    results.append(condition)
    print(f"[{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        raise AssertionError(name)


app = QApplication.instance() or QApplication([])
db = Database(os.path.join(tmp, "regression.db"))
store = SettingsStore(db)

editor_a = db.insert_editor(Editor(
    name="悬疑编辑", email="mystery@example.com", genres="短篇",
    directions="悬疑、推理", status="正常收稿"))
editor_b = db.insert_editor(Editor(
    name="言情编辑", email="romance@example.com", genres="短篇",
    directions="甜宠、言情", status="正常收稿"))
manuscript_id = db.insert_manuscript(Manuscript(
    title="谜案", category="悬疑", genre_type="短篇", reader_group="女频",
    emotion="爽", style="第一人称", word_count=12000))

win = MainWindow(db, store)
editors_page = win._pages["editors"]
submit_page = win._pages["submit"]
settings_page = win._pages["settings"]

# 小黑屋可见并恢复
db.toggle_blacklisted(editor_a)
editors_page.refresh()
editors_page.blacklist_combo.setCurrentText("小黑屋")
check("小黑屋可查看", any(e.id == editor_a for e in editors_page._current_editors()))
editors_page._toggle_blacklist(editor_a)
editors_page.blacklist_combo.setCurrentText("正常编辑")
check("小黑屋可恢复", any(e.id == editor_a for e in editors_page._current_editors()))

# 邮箱预设和自动保存
card = settings_page.mailbox_cards[0]
check("QQ服务器默认填入", card.smtp_host_edit.text() == "smtp.qq.com"
      and card.imap_host_edit.text() == "imap.qq.com"
      and not card.smtp_host_edit.isEnabled())
settings_page.interval_spin.setValue(73)
QTest.qWait(850)
check("设置自动保存", store.get_strategy()[1] == 73
      and settings_page.save_hint.text() == "已保存")

# 智选只排序并解释
submit_page.refresh()
index = submit_page.manuscript_combo.findData(manuscript_id)
submit_page.manuscript_combo.setCurrentIndex(index)
submit_page.smart_check.setChecked(True)
check("智选匹配排序", submit_page._current_editors[0].id == editor_a
      and submit_page.table.item(0, 12).text().startswith("9分"))
check("智选展示准确命中值", "题材：悬疑" in submit_page.table.item(0, 12).text()
      and "篇幅：短篇" in submit_page.table.item(0, 12).text())
check("智选展示编辑资料", submit_page.table.horizontalHeaderItem(5).text() == "稿件类型"
      and submit_page.table.item(0, 5).text() == "短篇"
      and submit_page.table.item(0, 6).text() == "悬疑、推理"
      and submit_page.table.item(0, 7).text() == "正常收稿")
details_tip = submit_page.table.item(0, 12).toolTip()
check("智选详情支持二次确认", "稿件类型：短篇" in details_tip
      and "收稿方向：悬疑、推理" in details_tip
      and "收稿状态：正常收稿" in details_tip)
check("智选不自动勾选", not submit_page._checked_ids)

# 每编辑真实个性化、唯一 Message-ID 和原子一稿一投
submit_page.subject_edit.setText("投稿《谜案》")
submit_page.body_edit.setPlainText("{编辑称呼}，您好。请审阅附件。")
submit_page._checked_ids = {editor_a, editor_b}
jobs, skipped = submit_page._build_jobs("待发")
check("每编辑投稿信不同", len(jobs) == 2 and jobs[0]["body"] != jobs[1]["body"])
check("每封Message-ID不同", jobs[0]["message_id"] != jobs[1]["message_id"])
duplicate = db.insert_submission_if_allowed(Submission(
    manuscript_id=manuscript_id, editor_id=editor_a, status="定时待发"), True)
check("定时待发防重复", duplicate is None)

# 每封发送前额度原子预留
first_id = jobs[0]["submission_id"]
second_id = jobs[1]["submission_id"]
check("首封额度预留", db.reserve_daily_send(first_id, "sender@qq.com", 1))
check("批内超限被阻止", not db.reserve_daily_send(second_id, "sender@qq.com", 1))
db.update_status(first_id, "已发")

# 自动回复与模糊语境不自动更新
reply_editor = db.insert_editor(Editor(name="结果编辑", email="result@example.com"))
reply_submission = db.insert_submission(Submission(
    manuscript_id=manuscript_id, editor_id=reply_editor,
    to_email="result@example.com", from_mailbox="sender@qq.com",
    status="已发", message_id="<result@nailong.local>"))
ingest_results(db, "sender@qq.com", [{
    "from_email": "result@example.com", "subject": "自动回复：稿件已采用",
    "snippet": "已采用", "uid": "100", "uid_validity": "1",
    "is_auto_reply": True,
}])
current = next(s for s in db.list_submissions() if s.id == reply_submission)
check("自动回复不判过稿", current.reply_status == "无"
      and db.list_replies()[0].verdict == "自动回复")
ingest_results(db, "sender@qq.com", [{
    "from_email": "result@example.com", "subject": "写作建议",
    "snippet": "这样过稿更容易~", "uid": "101", "uid_validity": "1",
}])
ambiguous_reply = next(r for r in db.list_replies() if r.imap_uid == "101")
current = next(s for s in db.list_submissions() if s.id == reply_submission)
check("模糊过稿语境待确认", ambiguous_reply.verdict == "待确认"
      and current.reply_status == "无")
db.confirm_reply_verdict(ambiguous_reply.id, "过稿")
current = next(s for s in db.list_submissions() if s.id == reply_submission)
check("人工确认更新投稿", current.reply_status == "过稿")

# 不同收件邮箱相同 UID 不丢失
uid_a = db.insert_reply(Reply(
    from_email="same@example.com", imap_uid="200", mailbox_address="a@qq.com",
    imap_folder="INBOX", uid_validity="9"))
uid_b = db.insert_reply(Reply(
    from_email="same@example.com", imap_uid="200", mailbox_address="b@qq.com",
    imap_folder="INBOX", uid_validity="9"))
check("多邮箱UID独立", uid_a is not None and uid_b is not None)

# 来源链接迁移不再清空
migration_path = os.path.join(tmp, "migration.db")
migration_db = Database(migration_path)
migration_db._conn.execute(
    "INSERT INTO editors(name,source_url) VALUES(?,?)", ("自建", "https://example.com"))
migration_db._conn.execute(
    "DELETE FROM settings WHERE key=?", ("source_cleared_v1",))
migration_db._conn.commit()
migration_db.close()
migration_db = Database(migration_path)
source = migration_db._conn.execute(
    "SELECT source_url FROM editors WHERE name=?", ("自建",)).fetchone()[0]
check("迁移保留来源链接", source == "https://example.com")
migration_db.close()

# 备份强制移除旧明文授权码
db._conn.execute(
    "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
    ("mailbox_99", json.dumps({"address": "old@qq.com", "auth_code": "secret"})))
db._conn.commit()
backup_path = os.path.join(tmp, "safe-backup.db")
db.backup_to(backup_path)
backup = sqlite3.connect(backup_path)
raw = backup.execute("SELECT value FROM settings WHERE key='mailbox_99'").fetchone()[0]
backup.close()
check("备份不含授权码", "secret" not in raw and "auth_code" not in json.loads(raw))

# 完整日期和旧月份并存兼容
db.insert_sale(Sale(manuscript_id=manuscript_id, payment_date="2026-08-11"))
db.insert_sale(Sale(manuscript_id=manuscript_id, payment_month="2026-07"))
sales = db.list_sales()
check("打款日期可记录", any(s.payment_date == "2026-08-11" for s in sales))
check("旧打款月份保留", any(s.payment_month == "2026-07" and not s.payment_date for s in sales))

# 收信失败使用独立信号，不伪装成空结果
original_fetch = receiver.fetch_replies
receiver.fetch_replies = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("模拟失败"))
try:
    worker = FetchWorker([
        MailboxConfig(enabled=True, address="broken@qq.com")], {"result@example.com"}, 1)
    failures = []
    successes = []
    worker.mailbox_failed.connect(lambda address, error: failures.append((address, error)))
    worker.mailbox_result.connect(lambda address, items: successes.append((address, items)))
    worker.run()
finally:
    receiver.fetch_replies = original_fetch
check("收信失败独立报告", failures and failures[0][0] == "broken@qq.com" and not successes)

# 首选邮箱额度满时自动尝试下一邮箱
fallback_editor = db.insert_editor(Editor(name="备用编辑", email="fallback@example.com"))
fallback_submission = db.insert_submission(Submission(
    manuscript_id=manuscript_id, editor_id=fallback_editor,
    to_email="fallback@example.com", status="待发"))
sent_by = []
original_send = mailer.send_mail
mailer.send_mail = lambda mailbox, *_args, **_kwargs: sent_by.append(mailbox.address)
try:
    send_worker = SendWorker([
        MailboxConfig(enabled=True, address="sender@qq.com", daily_limit=1),
        MailboxConfig(enabled=True, address="second@qq.com", daily_limit=1),
    ], [{"submission_id": fallback_submission, "to": "fallback@example.com"}],
       0, db=db)
    send_worker.run()
finally:
    mailer.send_mail = original_send
fallback_row = next(s for s in db.list_submissions() if s.id == fallback_submission)
check("满额邮箱自动切换", sent_by == ["second@qq.com"]
      and fallback_row.from_mailbox == "second@qq.com")

check("教程及指定文案", len(LINKS) == 3
      and LINKS[0][1] == "AI辅助写作·短篇收稿风向"
      and LINKS[1][1] == "长篇网文风向·实用创作工具"
      and LINKS[2][0] == "使用教程"
      and LINKS[2][1] == "功能列表·文字操作指南"
      and LINKS[2][3] == TUTORIAL_ACTION
      and all("BV1pMMQ6MEBx" not in item[3] for item in LINKS))

win.close()
db.close()
print()
print(f"全部通过：{sum(results)}/{len(results)} 项")
