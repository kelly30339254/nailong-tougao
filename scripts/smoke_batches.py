"""多稿件批次升级的无网络冒烟测试。"""
from __future__ import annotations

from datetime import datetime
import json
import os
import sys
import tempfile
import threading
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["NAILONG_DATA_DIR"] = tempfile.mkdtemp(prefix="nailong_batches_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

passed = 0


def check(name, condition):
    global passed
    if not condition:
        raise AssertionError(name)
    passed += 1
    print(f"[PASS] {name}")


from app.db import Database
from app.settings_store import SettingsStore
from app.models import BatchManuscript, Editor, MailboxConfig, Manuscript, Submission
from app.batching import BatchPlanner, _template_sequence
from app.docx_reader import read_docx_stats
from app.workers import BatchSendCoordinator

db = Database()
store = SettingsStore(db)
check("新装恰有 5 套默认模板", len(db.list_letter_templates()) == 5)

# 稳定邮箱 ID + 默认关闭旧额度。
mailboxes = store.load_mailboxes()
first_id = mailboxes[0].mailbox_id
check("邮箱 ID 已持久化", bool(first_id) and store.load_mailboxes()[0].mailbox_id == first_id)
check("每日保护默认关闭", not mailboxes[0].limit_enabled)
store.save_mailbox(0, MailboxConfig(enabled=False))
check("旧调用未传 ID 仍沿用原槽位 ID",
      store.load_mailboxes()[0].mailbox_id == first_id)
mailboxes = store.load_mailboxes()

# 旧库迁移：旧模板保留、旧额度数值保留但开关关闭，重复执行不增殖。
import sqlite3
legacy_path = os.path.join(os.environ["NAILONG_DATA_DIR"], "legacy.db")
legacy_conn = sqlite3.connect(legacy_path)
legacy_conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT DEFAULT '')")
legacy_conn.executemany("INSERT INTO settings(key,value) VALUES(?,?)", [
    ("letter_subject_tpl", "旧主题 {编辑称呼} {作品名} {字数}"),
    ("letter_body_tpl", "旧正文 {编辑称呼} {作品名} {字数}"),
    ("mailbox_count", "1"),
    ("mailbox_0", json.dumps({"enabled": True, "address": "old@example.com",
                               "daily_limit": 17}, ensure_ascii=False)),
])
legacy_conn.commit()
legacy_conn.close()
legacy_db = Database(legacy_path)
legacy_store = SettingsStore(legacy_db)
legacy_mailbox = legacy_store.load_mailboxes()[0]
check("迁移保留旧模板并增加 5 套默认", len(legacy_db.list_letter_templates()) == 6
      and any(t.origin == "legacy" for t in legacy_db.list_letter_templates()))
check("迁移保留旧额度但默认关闭", legacy_mailbox.daily_limit == 17
      and not legacy_mailbox.limit_enabled and bool(legacy_mailbox.mailbox_id))
legacy_id = legacy_mailbox.mailbox_id
legacy_db.close()
legacy_db = Database(legacy_path)
legacy_store = SettingsStore(legacy_db)
check("迁移可重复执行", len(legacy_db.list_letter_templates()) == 6
      and legacy_store.load_mailboxes()[0].mailbox_id == legacy_id)
legacy_db.close()

for i, address in enumerate(("one@example.com", "two@example.com")):
    mailbox = mailboxes[i]
    mailbox.enabled = True
    mailbox.address = address
    mailbox.smtp_host = "smtp.invalid"
    store.save_mailbox(i, mailbox)
mailboxes = store.load_mailboxes()[:2]

# Word 保存值优先，缺失/零值回退；正文 XML 含表格单元格。
def make_docx(path, words):
    document = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t>中文 hello</w:t></w:r></w:p>'
        '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格 two</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        '</w:body></w:document>')
    app = (
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
        f'<Words>{words}</Words></Properties>')
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("docProps/app.xml", app)


valid_docx = os.path.join(db.files_dir, "saved.docx")
fallback_docx = os.path.join(db.files_dir, "fallback.docx")
make_docx(valid_docx, 321)
make_docx(fallback_docx, 0)
stats = read_docx_stats(valid_docx)
fallback = read_docx_stats(fallback_docx)
check("DOCX 优先 Word 最后保存值", stats.word_count == 321 and stats.source == "word_saved")
check("DOCX 零值回退且包含表格", fallback.source == "compatible" and "表格" in fallback.text)

# 完整标签匹配：短篇不能误命中超短篇；同组 OR、跨组 AND。
short = db.insert_editor(Editor(
    name="短篇世情", platform="A", email="a@example.com",
    genres="短篇 / 长篇", directions="世情、复仇"))
ultra = db.insert_editor(Editor(
    name="超短篇", platform="B", email="b@example.com",
    genres="超短篇", directions="甜宠"))
check("标签使用完整值匹配", [e.id for e in db.list_editors(genre=["短篇"])] == [short])
check("标签同组 OR 跨组 AND", [e.id for e in db.list_editors(
    genre=["短篇", "中篇"], direction=["复仇"])] == [short])

# 两稿独立收件人，包含同平台跳过；任务按稿件交错并均衡到两邮箱。
manuscript1 = db.insert_manuscript(Manuscript(
    title="期待审阅的故事", file_path=valid_docx, word_count=321,
    category="世情", reader_group="女频", emotion="爽",
    style="第三人称", genre_type="短篇", word_count_source="word_saved"))
manuscript2 = db.insert_manuscript(Manuscript(
    title="第二篇", file_path=fallback_docx, word_count=fallback.word_count,
    category="复仇", genre_type="短篇", word_count_source="compatible"))
same_platform = db.insert_editor(Editor(
    name="同站第二位", platform="A", email="same@example.com"))
editor_c = db.insert_editor(Editor(name="编辑C", platform="C", email="c@example.com"))
editor_d = db.insert_editor(Editor(name="编辑D", platform="D", email="d@example.com"))
editor_e = db.insert_editor(Editor(name="编辑E", platform="E", email="e@example.com"))
template_ids = [template.id for template in db.list_letter_templates()[:2]]
batch_id = db.create_batch("两稿测试")
db.save_batch_configuration(batch_id, [
    BatchManuscript(
        manuscript_id=manuscript1,
        mailbox_ids=[m.mailbox_id for m in mailboxes],
        template_ids=template_ids,
        target_editor_ids=[short, same_platform, editor_c]),
    BatchManuscript(
        manuscript_id=manuscript2,
        mailbox_ids=[m.mailbox_id for m in mailboxes],
        template_ids=template_ids,
        target_editor_ids=[editor_d, editor_e]),
])
planner = BatchPlanner(db, store)
preflight = planner.preflight(batch_id)
check("两稿没有笛卡尔积", preflight.total == 4)
check("同平台保留配置第一位", preflight.manuscripts[0].skipped["同平台"] == 1)
check("最短队列均衡分配", sorted(preflight.mailbox_counts.values()) == [2, 2])
check("队列跨稿件交错", [task.manuscript_id for task in preflight.tasks] == [
    manuscript1, manuscript2, manuscript1, manuscript2])
check("事实字段不被规则微调", all("期待审阅的故事" in task.subject + task.body
                                  for task in preflight.tasks
                                  if task.manuscript_id == manuscript1))
for manuscript_id in (manuscript1, manuscript2):
    sources = [task.template_source for task in preflight.tasks
               if task.manuscript_id == manuscript_id]
    check(f"稿件 {manuscript_id} 首轮模板不放回", len(sources) == len(set(sources)))

# 0/1/10/11 篇边界。
boundary_batch = db.create_batch("边界")
check("空批次预检被拒绝", bool(planner.preflight(boundary_batch).errors))
db.save_batch_configuration(boundary_batch, [BatchManuscript(manuscript_id=manuscript1)])
check("单篇批次可保存", len(db.list_batch_manuscripts(boundary_batch)) == 1)
boundary_ids = [manuscript1, manuscript2]
for index in range(8):
    boundary_ids.append(db.insert_manuscript(Manuscript(
        title=f"边界稿{index}", file_path=valid_docx, word_count=321)))
db.save_batch_configuration(boundary_batch, [
    BatchManuscript(manuscript_id=manuscript_id) for manuscript_id in boundary_ids])
check("十篇批次可保存", len(db.list_batch_manuscripts(boundary_batch)) == 10)
boundary_extra = db.insert_manuscript(Manuscript(
    title="第十一篇", file_path=valid_docx, word_count=321))
try:
    db.save_batch_configuration(boundary_batch, [
        BatchManuscript(manuscript_id=manuscript_id)
        for manuscript_id in boundary_ids + [boundary_extra]])
except ValueError:
    rejected_11 = True
else:
    rejected_11 = False
check("批次拒绝第 11 篇", rejected_11)

# 缺少必需占位符的模板在启动前即被预检阻止。
from app.models import LetterTemplate
invalid_template = db.insert_letter_template(LetterTemplate(
    name="无效模板", subject="普通投稿", body="没有事实占位符"))
invalid_batch = db.create_batch("无效模板")
db.save_batch_configuration(invalid_batch, [BatchManuscript(
    manuscript_id=manuscript1, mailbox_ids=[mailboxes[0].mailbox_id],
    template_ids=[invalid_template], target_editor_ids=[editor_c])])
check("缺必需占位符阻止启动", any(
    "缺少必需占位符" in error for error in planner.preflight(invalid_batch).errors))
db.delete_batch(invalid_batch)

_pf, ids = planner.activate(batch_id)
check("激活事务物化 4 条冻结记录", len(ids) == 4 and all(
    s.attachment_path and s.template_source and s.allowed_mailbox_ids
    for s in db.list_batch_submissions(batch_id)))


class FakeClock:
    def __init__(self):
        self.value = datetime(2026, 8, 20, 8, 0, 0)
        self.lock = threading.Lock()

    def now(self):
        with self.lock:
            return self.value

    def sleep(self, seconds):
        from datetime import timedelta
        with self.lock:
            self.value += timedelta(seconds=seconds)


class FixedRng:
    def __init__(self):
        self.values = [60, 180]
        self.index = 0
        self.lock = threading.Lock()

    def randint(self, low, high):
        with self.lock:
            value = self.values[self.index % len(self.values)]
            self.index += 1
        assert low <= value <= high
        return value


clock = FakeClock()
calls = []
call_lock = threading.Lock()


def fake_send(mailbox, to, subject, body, attachment_path=None, message_id=None):
    with call_lock:
        calls.append((mailbox.mailbox_id, to, clock.now()))
    return message_id or "fake"


coordinator = BatchSendCoordinator(
    db, batch_id, mailboxes, rng=FixedRng(), sleep_fn=clock.sleep,
    now_fn=clock.now, send_fn=fake_send)
coordinator.run()
check("多邮箱全部发送", len(calls) == 4 and all(
    s.status == "已发" for s in db.list_batch_submissions(batch_id)))
first_times = {}
mailbox_times = {}
for mailbox_id, _to, at in calls:
    first_times.setdefault(mailbox_id, at)
    mailbox_times.setdefault(mailbox_id, []).append(at)
check("多个邮箱首封同时立即", len(set(first_times.values())) == 1)
gaps = []
for times in mailbox_times.values():
    times.sort()
    gaps.extend((b - a).total_seconds() for a, b in zip(times, times[1:]))
check("后续间隔始终 60–180 秒", gaps and all(60 <= gap <= 180 for gap in gaps))
check("批次完成状态", db.get_batch(batch_id).status == "completed")

# 某个 SMTP 调用变慢时，其他邮箱的独立串行队列仍可继续领取下一封。
independent_editors = [db.insert_editor(Editor(
    name=f"独立队列编辑{i}", platform=f"I{i}", email=f"independent{i}@example.com"))
    for i in range(4)]
independent_batch = db.create_batch("邮箱独立队列")
db.save_batch_configuration(independent_batch, [BatchManuscript(
    manuscript_id=manuscript1, mailbox_ids=[m.mailbox_id for m in mailboxes],
    template_ids=template_ids, target_editor_ids=independent_editors)])
planner.activate(independent_batch)
fast_mailbox_second = threading.Event()
independent_counts = {mailbox.mailbox_id: 0 for mailbox in mailboxes}


class ZeroWaitRng:
    """只用于压缩本测试时间；区间正确性已由上一个用例覆盖。"""
    def randint(self, _low, _high):
        return 0


def independent_send(mailbox, to, subject, body, attachment_path=None, message_id=None):
    independent_counts[mailbox.mailbox_id] += 1
    if mailbox.mailbox_id == mailboxes[0].mailbox_id and independent_counts[mailbox.mailbox_id] == 1:
        if not fast_mailbox_second.wait(1):
            raise RuntimeError("另一邮箱的第二封被慢 SMTP 阻塞")
    if mailbox.mailbox_id == mailboxes[1].mailbox_id and independent_counts[mailbox.mailbox_id] == 2:
        fast_mailbox_second.set()


independent_worker = BatchSendCoordinator(
    db, independent_batch, mailboxes, rng=ZeroWaitRng(), send_fn=independent_send)
independent_worker.run()
check("慢邮箱不阻塞其他邮箱下一封", fast_mailbox_second.is_set()
      and all(s.status == "已发" for s in db.list_batch_submissions(independent_batch)))

# 暂停只影响后续领取，恢复后不重排。
pause_editors = [db.insert_editor(Editor(
    name=f"暂停编辑{i}", platform=f"P{i}", email=f"pause{i}@example.com"))
    for i in range(2)]
pause_batch = db.create_batch("暂停恢复")
db.save_batch_configuration(pause_batch, [BatchManuscript(
    manuscript_id=manuscript1, mailbox_ids=[mailboxes[0].mailbox_id],
    template_ids=template_ids, target_editor_ids=pause_editors)])
planner.activate(pause_batch)
pause_clock = FakeClock()
pause_calls = []
holder = {}


def pause_after_first(mailbox, to, subject, body, attachment_path=None, message_id=None):
    pause_calls.append(to)
    holder["worker"].pause()


pause_worker = BatchSendCoordinator(
    db, pause_batch, mailboxes, rng=FixedRng(), sleep_fn=pause_clock.sleep,
    now_fn=pause_clock.now, send_fn=pause_after_first)
holder["worker"] = pause_worker
pause_worker.run()
pause_statuses = [s.status for s in db.list_batch_submissions(pause_batch)]
check("暂停在当前 SMTP 后生效", pause_statuses.count("已发") == 1
      and pause_statuses.count("待发") == 1 and db.get_batch(pause_batch).status == "paused")
resume_worker = BatchSendCoordinator(
    db, pause_batch, mailboxes, rng=FixedRng(), sleep_fn=pause_clock.sleep,
    now_fn=pause_clock.now, send_fn=fake_send)
resume_worker.run()
check("恢复后继续未发队列", all(s.status == "已发"
                             for s in db.list_batch_submissions(pause_batch)))

# 邮箱级鉴权错误暂停该邮箱并把任务改分配给允许的其他邮箱。
auth_editors = [db.insert_editor(Editor(
    name=f"鉴权编辑{i}", platform=f"Q{i}", email=f"auth{i}@example.com"))
    for i in range(2)]
auth_batch = db.create_batch("邮箱故障重分配")
db.save_batch_configuration(auth_batch, [BatchManuscript(
    manuscript_id=manuscript2,
    mailbox_ids=[m.mailbox_id for m in mailboxes],
    template_ids=template_ids, target_editor_ids=auth_editors)])
planner.activate(auth_batch)
import smtplib
auth_success = []


def auth_failure(mailbox, to, subject, body, attachment_path=None, message_id=None):
    if mailbox.mailbox_id == mailboxes[0].mailbox_id:
        raise smtplib.SMTPAuthenticationError(535, b"bad auth")
    auth_success.append((mailbox.mailbox_id, to))


auth_worker = BatchSendCoordinator(
    db, auth_batch, mailboxes, rng=FixedRng(), sleep_fn=clock.sleep,
    now_fn=clock.now, send_fn=auth_failure)
auth_worker.run()
check("邮箱级故障后重分配", len(auth_success) == 2 and all(
    s.status == "已发" for s in db.list_batch_submissions(auth_batch)))
check("故障邮箱状态持久化", db.batch_mailbox_states(auth_batch)[
    mailboxes[0].mailbox_id]["state"] == "paused")

# 收件人级错误只失败一封，邮箱队列继续。
recipient_editors = [db.insert_editor(Editor(
    name=f"收件编辑{i}", platform=f"R{i}", email=f"recipient{i}@example.com"))
    for i in range(2)]
recipient_batch = db.create_batch("单封失败")
db.save_batch_configuration(recipient_batch, [BatchManuscript(
    manuscript_id=manuscript1, mailbox_ids=[mailboxes[1].mailbox_id],
    template_ids=template_ids, target_editor_ids=recipient_editors)])
planner.activate(recipient_batch)


def recipient_failure(mailbox, to, subject, body, attachment_path=None, message_id=None):
    if to.startswith("recipient0"):
        raise smtplib.SMTPRecipientsRefused({to: (550, b"user unknown")})


recipient_worker = BatchSendCoordinator(
    db, recipient_batch, mailboxes, rng=FixedRng(), sleep_fn=clock.sleep,
    now_fn=clock.now, send_fn=recipient_failure)
recipient_worker.run()
recipient_statuses = [s.status for s in db.list_batch_submissions(recipient_batch)]
check("单封失败不暂停邮箱", sorted(recipient_statuses) == ["失败", "已发"]
      and db.get_batch(recipient_batch).status == "completed")

# 临时网络错误只重试一次，持续失败后升级为邮箱级等待处理。
temporary_editor = db.insert_editor(Editor(
    name="临时错误编辑", platform="T", email="temporary@example.com"))
temporary_batch = db.create_batch("临时错误重试")
db.save_batch_configuration(temporary_batch, [BatchManuscript(
    manuscript_id=manuscript2, mailbox_ids=[mailboxes[0].mailbox_id],
    template_ids=template_ids, target_editor_ids=[temporary_editor])])
planner.activate(temporary_batch)
import socket
temporary_attempts = []


def temporary_failure(mailbox, to, subject, body, attachment_path=None, message_id=None):
    temporary_attempts.append(to)
    raise socket.gaierror("getaddrinfo failed")


temporary_worker = BatchSendCoordinator(
    db, temporary_batch, mailboxes, rng=FixedRng(), sleep_fn=clock.sleep,
    now_fn=clock.now, send_fn=temporary_failure)
temporary_worker.run()
temporary_row = db.list_batch_submissions(temporary_batch)[0]
check("临时错误仅受控重试一次", len(temporary_attempts) == 2
      and temporary_row.attempt_count == 2
      and temporary_row.status == "等待用户处理"
      and db.get_batch(temporary_batch).status == "waiting")
db.cancel_batch(temporary_batch)

# 主动开启单邮箱每日保护后，无法改分配的任务等待次日。
limited = store.load_mailboxes()[0]
limited.limit_enabled = True
limited.daily_limit = db.count_today(limited.address) + 1
store.save_mailbox(0, limited)
mailboxes = store.load_mailboxes()[:2]
limit_editors = [db.insert_editor(Editor(
    name=f"限额编辑{i}", platform=f"L{i}", email=f"limit{i}@example.com"))
    for i in range(2)]
limit_batch = db.create_batch("每日保护")
db.save_batch_configuration(limit_batch, [BatchManuscript(
    manuscript_id=manuscript2, mailbox_ids=[limited.mailbox_id],
    template_ids=template_ids, target_editor_ids=limit_editors)])
planner.activate(limit_batch)
limit_worker = BatchSendCoordinator(
    db, limit_batch, mailboxes, rng=FixedRng(), sleep_fn=clock.sleep,
    now_fn=clock.now, send_fn=fake_send)
limit_worker.run()
limit_statuses = [s.status for s in db.list_batch_submissions(limit_batch)]
check("每日保护达到后等待次日", limit_statuses.count("已发") == 1
      and limit_statuses.count("等待限额") == 1
      and db.get_batch(limit_batch).status == "waiting")
db.cancel_batch(limit_batch)
limited.limit_enabled = False
store.save_mailbox(0, limited)
mailboxes = store.load_mailboxes()[:2]

# 崩溃恢复不自动重发处于发送中的批次单封。
uncertain_editor = db.insert_editor(Editor(
    name="待确认编辑", platform="U", email="uncertain@example.com"))
uncertain_batch = db.create_batch("崩溃恢复")
db.save_batch_configuration(uncertain_batch, [BatchManuscript(
    manuscript_id=manuscript1, mailbox_ids=[mailboxes[0].mailbox_id],
    template_ids=template_ids, target_editor_ids=[uncertain_editor])])
planner.activate(uncertain_batch)
uncertain_sub = db.list_batch_submissions(uncertain_batch)[0]
db.reserve_daily_send(
    uncertain_sub.id, mailboxes[0].address, None, mailboxes[0].mailbox_id)
db.recover_stuck_sending()
check("崩溃单封变为结果待确认", db.list_batch_submissions(
    uncertain_batch)[0].status == "结果待确认"
      and db.get_batch(uncertain_batch).status == "paused")
db.resolve_uncertain_submission(uncertain_sub.id, True)
db.update_batch(uncertain_batch, status="completed")

# 旧版单封同样不得在崩溃后自动重发。
legacy_uncertain_editor = db.insert_editor(Editor(
    name="旧版待确认", platform="Legacy", email="legacy-uncertain@example.com"))
legacy_uncertain_id = db.insert_submission(Submission(
    manuscript_id=manuscript1, editor_id=legacy_uncertain_editor,
    to_email="legacy-uncertain@example.com", status="待发"))
db.reserve_daily_send(legacy_uncertain_id, mailboxes[0].address, None,
                      mailboxes[0].mailbox_id)
db.recover_stuck_sending()
legacy_uncertain_row = next(
    s for s in db.list_submissions() if s.id == legacy_uncertain_id)
check("旧版中断单封也进入结果待确认",
      legacy_uncertain_row.status == "结果待确认")
db.resolve_uncertain_submission(legacy_uncertain_id, False)

# 模板跨轮边界不连续。
sequence = _template_sequence(
    [("a", "", ""), ("b", "", ""), ("c", "", "")], 15, "stable")
check("模板轮次边界不连续", all(a[0] != b[0] for a, b in zip(sequence, sequence[1:])))

# AI 只发标题/结构化标签且一次调用返回指定数量。
from app import ai_smart
from app.ai_client import AiConfig
captured = []
original_chat = ai_smart.chat


def fake_chat(_config, messages, **_kwargs):
    captured.append(messages)
    items = [{
        "name": f"候选{i}",
        "subject": "{编辑称呼}｜{作品名}｜{字数}",
        "body": "{编辑称呼}您好，投稿《{作品名}》，共{字数}字。",
    } for i in range(5)]
    return json.dumps({"items": items}, ensure_ascii=False)


ai_smart.chat = fake_chat
try:
    candidates = ai_smart.generate_batch_letter_templates(
        AiConfig(api_key="x"), {"title": "标题", "category": "世情", "body": "秘密正文"}, 5)
finally:
    ai_smart.chat = original_chat
payload_text = captured[0][1]["content"]
check("AI 候选一次生成 5 套", len(captured) == 1 and len(candidates) == 5)
check("AI 请求不上传正文", "秘密正文" not in payload_text and '"body"' not in payload_text)

# 清空业务数据同时移除批次附件快照，但保留模板库和邮箱设置。
batch_files = os.path.join(db.files_dir, "batches")
os.makedirs(batch_files, exist_ok=True)
with open(os.path.join(batch_files, "stale.txt"), "w", encoding="utf-8") as handle:
    handle.write("snapshot")
template_count = len(db.list_letter_templates())
mailbox_id_before_clear = store.load_mailboxes()[0].mailbox_id
db.clear_business_data()
check("清空业务数据同步删除批次快照",
      not os.path.exists(batch_files) and db.list_batches() == []
      and db.list_submissions() == [] and db.list_manuscripts() == [])
check("清空业务数据保留模板与邮箱设置",
      len(db.list_letter_templates()) == template_count
      and store.load_mailboxes()[0].mailbox_id == mailbox_id_before_clear)

print(f"全部通过：{passed}/{passed}")
db.close()
