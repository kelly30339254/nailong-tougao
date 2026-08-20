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
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

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
policy = store.get_strategy()
check("设置自动保存及命名策略", policy.legacy_interval_seconds == 73
      and policy.min_interval_seconds == 60 and policy.max_interval_seconds == 180
      and settings_page.save_hint.text() == "已保存")

# 智选只排序并解释
submit_page.refresh()
submit_page._on_new_batch()
submit_page.library_add_combo.setCurrentIndex(
    submit_page.library_add_combo.findData(manuscript_id))
submit_page._on_add_batch_manuscript()
check("文稿已加入持久化批次",
      submit_page._batch_id is not None
      and submit_page.manuscript_combo.findData(manuscript_id) >= 0)
submit_page.smart_check.setChecked(True)
check("智选匹配排序", submit_page._current_editors[0].id == editor_a
      and "题材：悬疑" in submit_page.table.item(0, 12).text())
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
from app.smart_match import match_editor, manuscript_query
from app.models import Editor as _Ed
from app.ai_client import parse_json_object, DEFAULT_PROVIDER
from app.ai_smart import _VERDICTS
from app.pages.records import _humanize_send_error
q = manuscript_query(None, "悬疑", "短篇", "女频", "爽", "第一人称")
hit = match_editor(_Ed(id=1, genres="短篇", directions="悬疑、推理", status="正常收稿"), q)
miss = match_editor(_Ed(id=2, genres="短篇", directions="言情", status="正常收稿"), q)
check("原版智选近义/分字段", hit[0] > miss[0] and "题材：悬疑" in hit[1])
ultra = match_editor(_Ed(id=3, genres="超短篇", directions="综合", status="正常收稿"), q)
short = match_editor(_Ed(id=4, genres="短篇", directions="综合", status="正常收稿"), q)
check("原版智选不把超短篇当短篇", short[0] > ultra[0])
check("JSON 围栏可解析", parse_json_object("```json\n{\"a\":1}\n```")["a"] == 1)
check("AI 默认服务商", DEFAULT_PROVIDER == "SpaceXAI")
check("投稿页有 AI智选按钮", submit_page.ai_smart_btn.text() == "AI智选")
check("用模板生成按钮", any(
    b.text() == "用模板生成" for b in submit_page.findChildren(type(submit_page.ai_smart_btn))))
check("按本篇生成按钮", any(
    b.text() == "按本篇生成一封" for b in submit_page.findChildren(type(submit_page.ai_smart_btn))))
check("回信页有 AI判定", win._pages["replies"].ai_btn.text() == "AI判定")
check("失败原因可读", "授权" in _humanize_send_error("535 Authentication failed"))
check("AI判定可选结果", "过稿" in _VERDICTS and "待确认" in _VERDICTS)
check("工作台卡片可点", win._pages["dashboard"].stat_cards["未读回信"]._on_click is not None)
check("未接入时 AI 推荐按钮不可用", submit_page.ai_pick_btn.isEnabled() is False)
check("智选排序已标注不使用AI", submit_page.smart_check.text() == "智选排序（不使用AI）")
check("规则微调已标注不使用AI", "不使用AI" in settings_page.letter_vary_check.text())
from app.ai_smart import DEFAULT_TPL_REQUIREMENTS
check("默认生成要求含占位符", "{作品名}" in DEFAULT_TPL_REQUIREMENTS and "{编辑称呼}" in DEFAULT_TPL_REQUIREMENTS)

from app.auth_dialog import AuthDialog
auth = AuthDialog(None, "login")
check("登录模式字段", not auth.email_row.isHidden() and not auth.password_row.isHidden()
      and auth.code_row.isHidden() and auth.card_row.isHidden())
auth.set_mode("register")
check("注册模式字段", not auth.code_row.isHidden() and not auth.confirm_row.isHidden()
      and auth.current_mode() == "register")
auth.set_mode("card")
check("绑卡模式字段", not auth.card_row.isHidden() and auth.email_row.isHidden()
      and auth.cancel_btn.text() == "退出登录")
auth.close()

real_q = QMessageBox.question
real_exit = os._exit
os._exit = lambda code: None
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
try:
    win._quitting = False
    win._force_quit = False
    settings_page._on_logout()
    check("退出登录真正退出", win._force_quit is True)
finally:
    QMessageBox.question = real_q
    os._exit = real_exit

# 投稿信模板刷新不得把光标打回开头
settings_page.letter_body_edit.setPlainText("ABCDEFG")
cursor = settings_page.letter_body_edit.textCursor()
cursor.setPosition(4)
settings_page.letter_body_edit.setTextCursor(cursor)
store.save_letter_template(settings_page.letter_subject_edit.text(), "ABCDEFG")
settings_page.save_all()
settings_page.refresh()
check("模板保存刷新不丢光标",
      settings_page.letter_body_edit.toPlainText() == "ABCDEFG"
      and settings_page.letter_body_edit.textCursor().position() == 4)

# 多层筛选：短篇 + 正常收稿
submit_page.refresh()
submit_page.genre_combo.setCurrentText("短篇")
submit_page.status_combo.setCurrentText("正常收稿")
check("多层筛选短篇且正常收稿",
      len(submit_page._current_editors) >= 2
      and all("短篇" in (e.genres or "")
              and (e.status or "").startswith("正常收稿")
              for e in submit_page._current_editors))

from app.letter import vary_letter, apply_variant_slots
import random
s0, b0 = "投稿《谜案》12000字 悬疑", (
    "尊敬的老师编辑：\n\n    冒昧来信，向您自荐我的作品《谜案》。"
    "本篇全文约12000字，分类为悬疑。稿件完整内容请见邮件附件，期待您的审阅。\n")
s_a, b_a = vary_letter(s0, b0, "seed-a")
s_a2, b_a2 = vary_letter(s0, b0, "seed-a")
s_b, b_b = vary_letter(s0, b0, "seed-b")
check("微调同种子稳定", b_a == b_a2 and s_a == s_a2)
check("微调保留作品事实", "《谜案》" in b_a and "12000" in b_a and "悬疑" in b_a)
check("变位占位符展开",
      apply_variant_slots("请{变:审阅|过目}", random.Random(1)) in ("请审阅", "请过目")
      and "{变:" not in vary_letter("x", "请{变:审阅|过目}", "slot")[1])

# 一键收藏当前筛选（避开对话框）
added = db.set_favorites([editor_a], True)
check("一键收藏写入", added == 1 and db.get_editor(editor_a).favorite is True)

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
        MailboxConfig(enabled=True, address="sender@qq.com", daily_limit=1,
                      limit_enabled=True),
        MailboxConfig(enabled=True, address="second@qq.com", daily_limit=1,
                      limit_enabled=True),
    ], [{"submission_id": fallback_submission, "to": "fallback@example.com"}],
       0, db=db)
    send_worker.run()
finally:
    mailer.send_mail = original_send
fallback_row = next(s for s in db.list_submissions() if s.id == fallback_submission)
check("满额邮箱自动切换", sent_by == ["second@qq.com"]
      and fallback_row.from_mailbox == "second@qq.com")

from app.icons import APP_USER_MODEL_ID, app_icon, apply_windows_app_id
from app.main_window import QApplication as MainWindowQApp
check("应用图标非空", not app_icon().isNull())
check("应用身份常量", APP_USER_MODEL_ID == "com.nailong.tougao")
apply_windows_app_id()
check("托盘退出能解析 QApplication", MainWindowQApp is QApplication)
check("托盘退出函数已接线", callable(getattr(win, "_quit_app", None)))
check("托盘图标枚举走 MessageIcon",
      hasattr(QSystemTrayIcon.MessageIcon, "Information"))
old_tray = win._tray_icon
win._tray_icon = None
win.notify_tray("should not raise")
called = {}

class _FakeTray:
    def showMessage(self, title, message, icon, ms):
        called["args"] = (title, message, icon, ms)

win._tray_icon = _FakeTray()
win.notify_tray("收到 1 封新回信", ms=4000)
check("托盘通知不读实例 Information",
      called["args"][1] == "收到 1 封新回信"
      and called["args"][2] == QSystemTrayIcon.MessageIcon.Information)
win._tray_icon = old_tray
from PySide6.QtGui import QCloseEvent
session_event = QCloseEvent()
win._session_quit = True
win._force_quit = False
win._tray_icon = object()  # 假装有托盘，确认会话退出不会 hide
win.closeEvent(session_event)
check("安装器/关机关闭不会缩到托盘", session_event.isAccepted() or win._force_quit is True)
win._session_quit = False
win._force_quit = False
win._tray_icon = None
if win._tray_icon is not None:
    check("托盘菜单未挂在隐藏窗口上",
          win._tray_menu is not None and win._tray_menu.parent() is None)

check("教程及指定文案", len(LINKS) == 3
      and LINKS[0][1] == "AI辅助写作·短篇收稿风向"
      and LINKS[1][1] == "长篇网文风向·实用创作工具"
      and LINKS[2][0] == "使用教程"
      and LINKS[2][1] == "功能列表·文字操作指南"
      and LINKS[2][3] == TUTORIAL_ACTION
      and all("BV1pMMQ6MEBx" not in item[3] for item in LINKS))

from app.widgets import PagedTable, PageBar
from app.workers import ImportEditorsWorker

ok_n, skip_n = db.upsert_editors_bulk([
    Editor(name="批量甲", email="bulk-a@example.com"),
    Editor(name="批量甲重复", email="bulk-a@example.com"),
    Editor(name="无邮箱", email=""),
])
check("upsert_editors_bulk 去重", ok_n == 1 and skip_n == 2)
total, page = db.list_submissions_page(offset=0, limit=1, order_by="id", desc=True)
check("list_submissions_page 分页", total >= 1 and len(page) == 1)
total_r, page_r = db.list_replies_page(keyword="不存在的关键词xyz", offset=0, limit=50)
check("list_replies_page 搜索落空", total_r == 0 and page_r == [])
total_s, page_s = db.list_sales_page(offset=0, limit=50)
check("list_sales_page 可调用", total_s == len(page_s))
bar = PageBar()
bar.set_total(120)
check("PageBar 页数", bar.pages == 3 and bar.page_size == 50)
paged = PagedTable(["A", "B"], sort_keys=["a", "b"])
paged.set_items([type("R", (), {"id": i, "a": i, "b": str(i)})() for i in range(60)])
check("PagedTable 默认每页 50", paged.table.rowCount() == 50 and paged.bar.total == 60)
from app.workers import DownloadUpdateWorker
from app import update_check as _uc
check("DownloadUpdateWorker 已注册", callable(DownloadUpdateWorker))
check("ImportEditorsWorker 已注册", callable(ImportEditorsWorker))
_uc.fetch_json = lambda url, timeout=15: {
    "version": "99.9.9", "notes": "", "download_url": "https://x",
    "github_url": "https://github.com/x/y/a.exe"}
fresh = _uc.check_for_update()
check("check_for_update 含 github_url", fresh is not None and "github_url" in fresh)

win._force_quit = True
win.close()
db.close()
print()
print(f"全部通过：{sum(results)}/{len(results)} 项")
