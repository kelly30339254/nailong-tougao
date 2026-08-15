"""阶段 3 冒烟测试：投稿方案 / 投递记录 / 回信中心。

用法：.venv/Scripts/python.exe scripts/smoke_phase3.py
（QT_QPA_PLATFORM=offscreen + 临时 NAILONG_DATA_DIR，不弹 GUI）
"""
import os
import sys
import tempfile
import time

_tmp = tempfile.mkdtemp(prefix="nailong_smoke3_")
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
from app.models import Editor, Manuscript, Submission, MailboxConfig, AuthorInfo

db = Database()
store = SettingsStore(db)
store.save_fetch_config(False, 30, 45)          # 关闭自动收信
store.save_strategy(True, 5, 30)                # 一稿一投开、间隔 5 秒
store.save_mailbox(0, MailboxConfig(enabled=True, address="me@qq.com", auth_code="x",
                                    smtp_host="smtp.qq.com", imap_host="imap.qq.com"))
store.save_author(AuthorInfo(real_name="作者甲", pen_name="奶龙", phone="138"))

from PySide6.QtWidgets import QApplication, QMessageBox
qapp = QApplication.instance() or QApplication([])

# 屏蔽模态框（offscreen 下 exec 会阻塞）
warnings = []
questions = []
from app.pages import submit as submit_mod
from app.pages import records as records_mod
from app.pages import replies as replies_mod
for mod in (submit_mod, records_mod, replies_mod):
    mod.QMessageBox.warning = staticmethod(lambda *a, **k: warnings.append(a[-1]))
    mod.QMessageBox.information = staticmethod(lambda *a, **k: None)
    mod.QMessageBox.question = staticmethod(lambda *a, **k: (questions.append(a[-1]), QMessageBox.Yes)[1])

from app.main_window import MainWindow
win = MainWindow(db, store)
submit_page = win._pages["submit"]
records_page = win._pages["records"]
replies_page = win._pages["replies"]
check("8 页全部真实页面", all(type(win._pages[p]).__name__ != "PlaceholderPage"
                             for p in win._pages))

# ---------- 投稿方案：校验分支 ----------
submit_page._on_start()
check("校验：空表单提示", any("作品名称" in w for w in warnings))
warnings.clear()

# 无启用邮箱分支（临时禁用）
store.save_mailbox(0, MailboxConfig(enabled=False))
submit_page.title_edit.setText("测试文稿")
submit_page.words_edit.setText("10000")
submit_page.subject_edit.setText("投稿《测试文稿》10000字 悬疑")
submit_page.body_edit.setPlainText("正文")
submit_page._on_start()
check("校验：无启用邮箱提示", any("邮箱" in w for w in warnings))
warnings.clear()
store.save_mailbox(0, MailboxConfig(enabled=True, address="me@qq.com", auth_code="x",
                                    smtp_host="smtp.qq.com", imap_host="imap.qq.com"))

# 造编辑：ed1(平台A,有pending) / ed2(平台A) / ed3(平台B) / ed4(小黑屋)
eid1 = db.insert_editor(Editor(name="编辑一", platform="平台A", email="ed1@x.com"))
eid2 = db.insert_editor(Editor(name="编辑二", platform="平台A", email="ed2@x.com"))
eid3 = db.insert_editor(Editor(name="编辑三", platform="平台B", email="ed3@x.com"))
eid4 = db.insert_editor(Editor(name="编辑四", platform="平台C", email="ed4@x.com",
                               blacklisted=True))
mid = db.insert_manuscript(Manuscript(title="测试文稿", word_count=10000, category="悬疑"))
pending_sid = db.insert_submission(Submission(manuscript_id=mid, editor_id=eid1,
                                              to_email="ed1@x.com"))
db.update_status(pending_sid, "已发")   # 已发且无回复 → 一稿一投 pending

submit_page.refresh()
check("小黑屋不出现在列表", all(eid4 not in [submit_page.table.item(r, 0).data(32)
                                             for r in range(submit_page.table.rowCount())]
                                for _ in [0]) and submit_page.table.rowCount() == 3)

# 临时 docx 选择（patch 文件对话框）
import docx
docx_path = os.path.join(_tmp, "临时文稿.docx")
d = docx.Document()
d.add_paragraph("临时文稿正文内容用于统计字数。")
d.save(docx_path)
submit_mod.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (docx_path, ""))
temp_index = submit_page.manuscript_combo.count() - 1
submit_page._on_manuscript_changed(temp_index)
check("临时 docx 填入标题字数", submit_page.title_edit.text() == "临时文稿"
      and submit_page.words_edit.text().isdigit()
      and submit_page._current_manuscript_id() is None)
# 切回文稿库文稿
submit_page.manuscript_combo.setCurrentIndex(0)
check("选中文稿带出信息", submit_page._current_manuscript_id() == mid
      and submit_page.title_edit.text() == "测试文稿")

# 生成投稿信
submit_page.subject_edit.setText("")
submit_page.body_edit.setPlainText("")
submit_page._on_build_letter()
check("生成投稿信", submit_page.subject_edit.text() == "投稿《测试文稿》10000字 悬疑"
      and "祝工作顺利，万事顺意！" in submit_page.body_edit.toPlainText()
      and "奶龙" not in submit_page.body_edit.toPlainText())  # 无落款行
# 已有内容时再次生成 → 弹覆盖确认（patched 返回 Yes）
submit_page._on_build_letter()
check("已有内容时弹覆盖确认", len(questions) == 1)

# 未勾选编辑分支
submit_page._checked_ids.clear()
submit_page._on_start()
check("校验：未勾选编辑提示", any("勾选" in w for w in warnings))
warnings.clear()

# ---------- 投稿方案：发信流程（假 mailer） ----------
import app.mailer as mailer_mod
send_calls = []
orig_send = mailer_mod.send_mail
def fake_send(mailbox, to, subject, body, attachment_path=None, message_id=None):
    send_calls.append((mailbox.address, to))
    if to == "ed3@x.com":
        raise Exception("535 模拟失败")
mailer_mod.send_mail = fake_send
try:
    submit_page._checked_ids.update({eid1, eid2, eid3})
    submit_page._update_checked_label()
    submit_page._on_start()
    worker = submit_page._send_worker
    check("SendWorker 已启动（按钮态）", worker is not None
          and not submit_page.start_btn.isEnabled()
          and submit_page.stop_btn.isEnabled())
    while worker.isRunning():
        qapp.processEvents()
        time.sleep(0.05)
    qapp.processEvents()   # 让队列信号（item_done/all_done）投递
    time.sleep(0.1)
    qapp.processEvents()
finally:
    mailer_mod.send_mail = orig_send

log_text = submit_page.log_edit.toPlainText()
check("一稿一投跳过日志", "跳过（一稿一投保护）：编辑一" in log_text)
check("只发 2 封（ed2/ed3）", len(send_calls) == 2
      and {c[1] for c in send_calls} == {"ed2@x.com", "ed3@x.com"})
check("全部完成日志", "全部完成：成功 1 失败 1 跳过 1" in log_text)
check("按钮态恢复", submit_page.start_btn.isEnabled()
      and not submit_page.stop_btn.isEnabled())

subs = {s.id: s for s in db.list_submissions()}
new_subs = [s for s in subs.values() if s.id != pending_sid]
check("submissions 落库 2 条新记录", len(new_subs) == 2)
ok_sub = next(s for s in new_subs if s.to_email == "ed2@x.com")
fail_sub = next(s for s in new_subs if s.to_email == "ed3@x.com")
check("成功记录状态+发信邮箱", ok_sub.status == "已发" and ok_sub.sent_at != ""
      and ok_sub.from_mailbox == "me@qq.com")
check("失败记录状态", fail_sub.status == "失败")
check("count_today 只计已发", db.count_today("me@qq.com") == 1)  # pending 无发信邮箱，失败不计

# 单日上限分支：把上限调成 1 → 全部超限
store.save_mailbox(0, MailboxConfig(enabled=True, address="me@qq.com", auth_code="x",
                                    daily_limit=1))
submit_page._checked_ids.update({eid2})
submit_page._on_start()
check("校验：今日已达上限提示", any("上限" in w for w in warnings))
warnings.clear()
store.save_mailbox(0, MailboxConfig(enabled=True, address="me@qq.com", auth_code="x"))

# ---------- 投递记录 ----------
records_page.status_combo.setCurrentText("全部状态")
records_page.refresh()
check("投递记录行数", records_page.table.rowCount() == 3)
records_page.status_combo.setCurrentText("已发")
check("状态筛选已发", records_page.table.rowCount() == 2)
records_page.status_combo.setCurrentText("失败")
check("状态筛选失败", records_page.table.rowCount() == 1)
records_page.status_combo.setCurrentText("全部状态")
records_page.reply_combo.setCurrentText("未回复")
check("回复筛选未回复", records_page.table.rowCount() == 3)
records_page.reply_combo.setCurrentText("全部")

# 删除一条记录
before = len(db.list_submissions())
records_page._on_delete(fail_sub)
check("删除投递记录", len(db.list_submissions()) == before - 1
      and all(s.id != fail_sub.id for s in db.list_submissions()))

# ---------- 回信中心 ----------
fake_results = [{
    "from_email": "ed2@x.com", "subject": "Re: 投稿《测试文稿》",
    "snippet": "稿件我们决定采用，请保持电话畅通。",
    "uid": "7001", "received_at": "2026-08-02 11:00:00",
}, {
    "from_email": "stranger@x.com", "subject": "广告",
    "snippet": "与编辑无关", "uid": "7002", "received_at": "2026-08-02 11:01:00",
}]
# 注意：FetchWorker 已按编辑列表过滤，这里模拟过滤后的结果时也含陌生发件人，
# ingest 不做过滤（上游已过滤），但陌生发件人匹配不到 submission，只会写一条 reply。
new_count = replies_page._on_mailbox_result("me@qq.com", fake_results)
check("ingest 返回新到数", replies_page._new_count == 2)
ok_sub_row = [s for s in db.list_submissions() if s.to_email == "ed2@x.com"][0]
check("过稿回写 submission", ok_sub_row.reply_status == "过稿"
      and ok_sub_row.replied_at != "")
check("未读计数", db.unread_count() == 2)
replies_page._on_mailbox_result("me@qq.com", fake_results)
check("重复 uid 不再插入", db.unread_count() == 2)

replies_page.refresh()
check("回信表格行数", replies_page.table.rowCount() == 2)
replies_page.unread_check.setChecked(True)
check("只看未读", replies_page.table.rowCount() == 2)
# 标记已读
rid = db.list_replies()[0].id
replies_page._on_mark_read(rid)
check("标记已读", db.unread_count() == 1)
replies_page.unread_check.setChecked(True)
check("只看未读过滤生效", replies_page.table.rowCount() == 1)
replies_page.unread_check.setChecked(False)

# 收信完成提示
replies_page._new_count = 5
replies_page._on_fetch_done()
check("收信完成提示", replies_page.status_label.text() == "本次新到 5 封")

# 判定颜色/查看全文对话框可实例化（不 exec）
from app.pages.replies import ReplyDetailDialog
dlg = ReplyDetailDialog(db.list_replies()[0], replies_page)
check("查看全文对话框可实例化", dlg.windowTitle() == "回信全文")

# 工作台联动：过稿统计已更新
dashboard_page = win._pages["dashboard"]
dashboard_page.refresh()
check("工作台过稿统计联动", dashboard_page.stat_cards["过稿"].number.text() == "1")

# ---------- navigate 全 8 页 ----------
for page_id in ("dashboard", "submit", "records", "replies", "sales",
                "manuscripts", "editors", "settings"):
    win.navigate(page_id)
    check(f"navigate {page_id}", win.stack.currentWidget() is win._pages[page_id])

# ---------- 问题 1-8 防回归断言 ----------
from PySide6.QtWidgets import QHeaderView, QLabel
from app.theme import render_qss
from app.main_window import PromoButton, TUTORIAL_ACTION

qss = render_qss("蔷薇粉")
check("combo 下拉箭头样式（SVG 路径已替换）", "arrow_down.svg" in qss
      and "{arrow_down}" not in qss and "{primary}" not in qss)
check("编辑表格最小高度 >=260", submit_page.table.minimumHeight() >= 260)
check("操作列固定宽不被截断",
      records_page.table.horizontalHeader().sectionResizeMode(7) == QHeaderView.Fixed
      and records_page.table.columnWidth(7) >= 80
      and replies_page.table.horizontalHeader().sectionResizeMode(6) == QHeaderView.Fixed
      and replies_page.table.columnWidth(6) >= 170
      and win._pages["editors"].table.horizontalHeader().sectionResizeMode(10) == QHeaderView.Fixed
      and win._pages["editors"].table.columnWidth(10) >= 200
      and win._pages["manuscripts"].table.horizontalHeader().sectionResizeMode(9) == QHeaderView.Fixed
      and win._pages["manuscripts"].table.columnWidth(9) >= 170)
check("表格最后一列不被拉伸挤压",
      not records_page.table.horizontalHeader().stretchLastSection()
      and not replies_page.table.horizontalHeader().stretchLastSection()
      and not submit_page.table.horizontalHeader().stretchLastSection()
      and not win._pages["editors"].table.horizontalHeader().stretchLastSection()
      and not win._pages["manuscripts"].table.horizontalHeader().stretchLastSection())
tab_texts = [submit_page.tab_bar.tabText(i) for i in range(submit_page.tab_bar.count())]
check("无随机推荐标签", "随机推荐" not in tab_texts and tab_texts == ["全部编辑", "收藏分类"])
settings_page = win._pages["settings"]
settings_tabs = [settings_page.tabs.tabText(i) for i in range(settings_page.tabs.count())]
check("设置页 6 个标签无作者与落款", len(settings_tabs) == 6
      and "作者与落款" not in settings_tabs
      and "投稿信模板" in settings_tabs and "数据备份" in settings_tabs)
check("侧栏无分组标签", len(win.findChildren(QLabel, "navGroupLabel")) == 0)
promo_buttons = win.findChildren(PromoButton)
check("顶栏 3 个功能按钮", len(promo_buttons) == 3
      and sum(button._url == TUTORIAL_ACTION for button in promo_buttons) == 1
      and all("BV1pMMQ6MEBx" not in button._url for button in promo_buttons))
tutorial_dialog = win._create_tutorial_dialog()
tutorial_text = tutorial_dialog.browser.toPlainText()
check("本地文字教程内容完整", all(section in tutorial_text for section in (
    "功能总览", "第一次使用", "智选编辑", "邮箱授权码", "定时投递",
    "回信与人工确认", "稿费和备份", "常见问题")))
tutorial_dialog.close()
check("表格单元格带 tooltip", win._pages["editors"].table.item(0, 1).toolTip()
      == win._pages["editors"].table.item(0, 1).text())
check("文稿下拉项带 tooltip", submit_page.manuscript_combo.count() >= 1)

print()
print(f"全部通过：{sum(RESULTS)}/{len(RESULTS)} 项")
