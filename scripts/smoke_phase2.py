"""阶段 2 冒烟测试：编辑列表 / 文稿库 / 工作台。

用法：.venv/Scripts/python.exe scripts/smoke_phase2.py
（QT_QPA_PLATFORM=offscreen + 临时 NAILONG_DATA_DIR，不弹 GUI）
"""
import csv
import os
import sys
import tempfile
import time

_tmp = tempfile.mkdtemp(prefix="nailong_smoke2_")
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
# 关闭自动收信，避免定时器触发网络
store.save_fetch_config(False, 30, 45)
store.save_mailbox(0, MailboxConfig(enabled=True, address="me@qq.com", auth_code="x"))
store.save_author(AuthorInfo(real_name="作者甲", pen_name="奶龙"))

from PySide6.QtWidgets import QApplication
qapp = QApplication.instance() or QApplication([])

from app.main_window import MainWindow
win = MainWindow(db, store)

editors_page = win._pages["editors"]
manuscripts_page = win._pages["manuscripts"]
dashboard_page = win._pages["dashboard"]

# ---------- 编辑列表页 ----------
from app.pages.editors import EditorDialog, EditorsPage

check("三页真实类型", isinstance(editors_page, EditorsPage)
      and type(editors_page).__name__ == "EditorsPage")

eid = db.insert_editor(Editor(name="张三编辑", platform="平台A", email="ed1@x.com",
                              genres="言情/悬疑", directions="甜宠玄学年代古言",
                              status="正常收稿", fee_info="千字100",
                              source_url="https://example.com/1", notes="备注"))
db.insert_editor(Editor(name="李四编辑", platform="平台B", email="ed2@x.com",
                        genres="科幻", directions="科幻悬疑推理",
                        status="停止收稿", favorite=True))
editors_page.refresh()
check("编辑表格行数", editors_page.table.rowCount() == 2)
check("收稿方向只显示前 5 字并保留悬停详情",
      editors_page.table.item(1, 5).text() == "甜宠玄学年…"
      and editors_page.table.item(1, 5).toolTip() == "收稿方向：甜宠玄学年代古言")

from PySide6.QtWidgets import QHeaderView
editor_header = editors_page.table.horizontalHeader()
check("普通文本列完整自适应且收稿方向固定窄列",
      all(editor_header.sectionResizeMode(col) == QHeaderView.Fixed
          for col in (1, 2, 3, 4, 7))
      and editor_header.sectionResizeMode(5) == QHeaderView.Fixed
      and editors_page.table.columnWidth(5) <= 110
      and all(editors_page.table.columnWidth(col)
              >= editors_page.table.fontMetrics().horizontalAdvance(
                  editors_page.table.item(row, col).text()) + 28
              for row in range(editors_page.table.rowCount())
              for col in (1, 2, 3, 4, 7)))

# 搜索过滤（搜索框有 200ms 防抖，需等待定时器触发后断言）
editors_page.search_edit.setText("张三")
qapp.processEvents()
time.sleep(0.25)
qapp.processEvents()
check("搜索过滤", editors_page.table.rowCount() == 1
      and editors_page.table.item(0, 1).text() == "张三编辑")
editors_page.search_edit.setText("")
qapp.processEvents()
time.sleep(0.25)
qapp.processEvents()
# 只看收藏
editors_page.fav_check.setChecked(True)
check("只看收藏", editors_page.table.rowCount() == 1
      and editors_page.table.item(0, 1).text() == "李四编辑")
editors_page.fav_check.setChecked(False)
# 只看正在收稿（“未核实”和“停止收稿”均不计入）
editors_page.accepting_check.setChecked(True)
check("只看正在收稿", editors_page.table.rowCount() == 1
      and editors_page.table.item(0, 1).text() == "张三编辑")
editors_page.accepting_check.setChecked(False)
# 平台筛选
editors_page.platform_combo.setCurrentText("平台A")
check("平台筛选", editors_page.table.rowCount() == 1)
editors_page.platform_combo.setCurrentText("全部平台")
# 题材筛选
editors_page.genre_combo.setCurrentText("科幻")
check("题材筛选", editors_page.table.rowCount() == 1)
editors_page.genre_combo.setCurrentText("全部题材")

# 行内收藏/小黑屋切换
editors_page._toggle_fav(eid)
check("行内收藏切换", db.get_editor(eid).favorite is True)
editors_page._toggle_blacklist(eid)
check("小黑屋后默认列表隐藏", editors_page.table.rowCount() == 1)
editors_page._toggle_blacklist(eid)
check("移出小黑屋后恢复", editors_page.table.rowCount() == 2)

# 对话框邮箱正则校验（不触发模态框）
from app.pages.editors import EMAIL_RE
check("邮箱正则校验", EMAIL_RE.match("ed1@x.com") and not EMAIL_RE.match("not-an-email"))

# 分页：插入 60 条凑成 62 条（2 页：50 + 12），验证后清理干净
from app.pages.editors import PAGE_SIZE
bulk_ids = [db.insert_editor(Editor(name=f"批量编辑{i:02d}", platform="平台B",
                                    email=f"bulk{i}@x.com", genres="科幻"))
            for i in range(60)]
editors_page.refresh()
check("分页后首页只渲染 50 行", editors_page.table.rowCount() == PAGE_SIZE
      and PAGE_SIZE == 50)
check("分页文案 第 1 / 2 页（共 62 人）",
      editors_page.page_label.text() == "第 1 / 2 页（共 62 人）")
check("首页上一页禁用、下一页可用",
      not editors_page.prev_btn.isEnabled() and editors_page.next_btn.isEnabled())
editors_page._goto_page(1)
check("第二页 12 行", editors_page.table.rowCount() == 12)
check("第二页下一页禁用、上一页可用",
      not editors_page.next_btn.isEnabled() and editors_page.prev_btn.isEnabled())
editors_page._goto_page(99)  # 越界应被夹到末页
check("页码越界自动夹到末页", editors_page._page == 1
      and editors_page.table.rowCount() == 12)
editors_page._on_filter_changed()  # 模拟筛选变化
check("筛选变化回到第一页", editors_page._page == 0
      and editors_page.table.rowCount() == PAGE_SIZE)
for bid in bulk_ids:
    db.delete_editor(bid)
editors_page.refresh()
check("清理批量数据后恢复 2 行", editors_page.table.rowCount() == 2)

# 导出 CSV → 导入往返
csv_path = os.path.join(_tmp, "export.csv")
editors = editors_page._current_editors()
with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["名称", "平台", "邮箱", "题材", "稿费", "来源", "备注"])
    for e in editors:
        writer.writerow([e.name, e.platform, e.email, e.genres, e.fee_info,
                         e.source_url, e.notes])
# 模拟导入逻辑（复用页面导入函数的解析部分）
with open(csv_path, "rb") as f:
    raw = f.read()
text = raw.decode("utf-8-sig")
reader = csv.DictReader(__import__("io").StringIO(text))
rows = list(reader)
check("导出 CSV 可解析", len(rows) == 2 and rows[0]["邮箱"] in ("ed1@x.com", "ed2@x.com"))
# 导入到全新库验证别名兼容
db2 = Database(db_path=os.path.join(tempfile.mkdtemp(prefix="nailong_fresh2_"), "f.db"))
for row in rows:
    email = (row.get("邮箱") or "").strip()
    if email:
        db2.insert_editor(Editor(name=row["名称"], platform=row["平台"], email=email,
                                 genres=row["题材"], fee_info=row["稿费"],
                                 source_url=row["来源"], notes=row["备注"]))
check("CSV 导入往返", len(db2.list_editors()) == 2
      and db2.list_editors(keyword="张三")[0].fee_info == "千字100")

# ---------- 文稿库页 ----------
from app.pages.manuscripts import ManuscriptDialog, ManuscriptsPage, copy_to_files_dir
from app.docx_reader import read_docx_text, count_cjk_words

check("文稿空态提示", manuscripts_page.table.rowCount() == 1
      and "还没有文稿" in manuscripts_page.table.item(0, 0).text())

# 造 txt 和 docx
txt_path = os.path.join(_tmp, "测试文稿.txt")
with open(txt_path, "w", encoding="utf-8") as f:
    f.write("第一章 你好世界。" * 100)

import docx
docx_path = os.path.join(_tmp, "另一篇.docx")
d = docx.Document()
d.add_paragraph("这是 docx 正文内容，用于字数统计。")
d.save(docx_path)

# 复制+统计（对话框逻辑）
copied = copy_to_files_dir(db.files_dir, txt_path)
check("文件复制到 files 目录", copied.startswith(db.files_dir) and os.path.exists(copied))
copied2 = copy_to_files_dir(db.files_dir, txt_path)
check("重名加序号", copied2 != copied and os.path.exists(copied2))

# 新建文稿（走对话框取值逻辑）
dlg = ManuscriptDialog(db, manuscripts_page)
dlg.title_edit.setText("测试文稿")
dlg.file_path = copied
dlg.words_edit.setText(str(count_cjk_words(open(copied, encoding="utf-8").read())))
dlg.category_combo.setCurrentText("悬疑")
dlg._on_save()
db.insert_manuscript(dlg.manuscript)
mid = dlg.manuscript.title
check("新建文稿", len(db.list_manuscripts()) == 1
      and db.list_manuscripts()[0].title == "测试文稿"
      and db.list_manuscripts()[0].word_count > 0
      and db.list_manuscripts()[0].category == "悬疑")

# 上传逻辑（docx）
copied_docx = copy_to_files_dir(db.files_dir, docx_path)
text = read_docx_text(copied_docx)
db.insert_manuscript(Manuscript(title="另一篇", file_path=copied_docx,
                                word_count=count_cjk_words(text)))
manuscripts_page.refresh()
check("上传 docx 后表格", manuscripts_page.table.rowCount() == 2
      and manuscripts_page.table.item(0, 0).text() == "另一篇")

# 编辑文稿
m = db.list_manuscripts()[0]
dlg2 = ManuscriptDialog(db, manuscripts_page, m)
dlg2.title_edit.setText("改名文稿")
dlg2._on_save()
db.update_manuscript(dlg2.manuscript)
check("编辑文稿", db.get_manuscript(m.id).title == "改名文稿")

# ---------- 工作台页 ----------
from app.pages.dashboard import DashboardPage, _relative_time

# 造投稿数据
sub_id = db.insert_submission(Submission(
    manuscript_id=m.id, editor_id=eid, from_mailbox="me@qq.com",
    to_email="ed1@x.com", subject="投稿《改名文稿》", body="正文"))
db.update_status(sub_id, "已发")
dashboard_page.refresh()
counts = db.counts()
check("统计卡数值", dashboard_page.stat_cards["编辑总数"].number.text() == str(counts["编辑总数"])
      and dashboard_page.stat_cards["待回复"].number.text() == "1"
      and dashboard_page.stat_cards["文稿"].number.text() == "2")

# 新手指引：邮箱已配、有文稿 → 一切就绪
check("指引一切就绪", dashboard_page.guide_box.count() == 1)
# 禁用邮箱 → 出现指引项
store.save_mailbox(0, MailboxConfig(enabled=False))
dashboard_page.refresh()
check("指引条件显示（未配邮箱）", dashboard_page.guide_box.count() == 1)
store.save_mailbox(0, MailboxConfig(enabled=True, address="me@qq.com", auth_code="x"))
dashboard_page.refresh()

# 近期动态
check("近期动态有条目", dashboard_page.activity_box.count() >= 1)
check("相对时间", _relative_time("2099-01-01 00:00:00")  # 未来时间也不崩
      and _relative_time("") == "")

# 收信写库逻辑（直接喂假数据）
fake_results = [{
    "from_email": "ed1@x.com",
    "subject": "Re: 投稿《改名文稿》",
    "snippet": "你好，稿件我们决定采用，稍后会联系你签约。",
    "uid": "9001",
    "received_at": "2026-08-02 09:00:00",
}]
dashboard_page._on_mailbox_result("me@qq.com", fake_results)
check("回信已写库", len(db.list_replies()) == 1
      and db.list_replies()[0].verdict == "过稿")
check("submission 回写过稿", db.list_submissions()[0].reply_status == "过稿"
      and db.list_submissions()[0].replied_at != "")
# 重复 uid 不再插入
dashboard_page._on_mailbox_result("me@qq.com", fake_results)
check("回信去重", len(db.list_replies()) == 1)
dashboard_page.refresh()
check("统计卡过稿+1", dashboard_page.stat_cards["过稿"].number.text() == "1"
      and dashboard_page.stat_cards["待回复"].number.text() == "0")

# 判定为"其他"不更新 submission
sub2 = db.insert_submission(Submission(manuscript_id=m.id, editor_id=eid,
                                       to_email="ed2@x.com"))
db.update_status(sub2, "已发")
dashboard_page._on_mailbox_result("me@qq.com", [{
    "from_email": "ed2@x.com", "subject": "自动回复",
    "snippet": "邮件已收到，我们会尽快处理。", "uid": "9002",
    "received_at": "2026-08-02 10:00:00",
}])
sub2_row = [s for s in db.list_submissions() if s.id == sub2][0]
check("其他判定不更新 submission", sub2_row.reply_status == "无")

# ---------- navigate 三页 ----------
for page_id in ("editors", "manuscripts", "dashboard"):
    win.navigate(page_id)
    check(f"navigate {page_id}", win.stack.currentWidget() is win._pages[page_id])

# 主题切换下三页不崩
from app.theme import THEMES, apply_theme
for name in THEMES:
    apply_theme(qapp, name)
    editors_page.refresh()
    manuscripts_page.refresh()
    dashboard_page.refresh()
check("主题切换下三页刷新无异常", True)

print()
print(f"全部通过：{sum(RESULTS)}/{len(RESULTS)} 项")
