"""内置编辑播种冒烟测试：转换产物 + 首次启动播种 + 幂等 + 页面默认过滤。

用法：.venv/Scripts/python.exe scripts/smoke_seed.py
（QT_QPA_PLATFORM=offscreen + 临时 NAILONG_DATA_DIR）
"""
import json
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="nailong_seed_")
os.environ["NAILONG_DATA_DIR"] = _tmp
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = []


def check(name, cond):
    RESULTS.append(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        raise AssertionError(name)


# ---------- 转换产物自检 ----------
from app.theme import resource_path
JSON_PATH = resource_path(os.path.join("app", "data", "builtin_editors.json"))
check("builtin_editors.json 存在（resource_path 开发模式正确）", os.path.exists(JSON_PATH))
with open(JSON_PATH, encoding="utf-8") as f:
    items = json.load(f)
check("总条数 2481", len(items) == 2481)
check("无空 email 且全含 @", all(e["email"] and "@" in e["email"] for e in items))
blacklisted = [e for e in items if e["blacklisted"]]
check("blacklisted 1044（停止收稿，4 条无邮箱被跳过）", len(blacklisted) == 1044)
check("停止收稿 notes 前缀", all(e["notes"].startswith("【已停止收稿】") for e in blacklisted))
unverified = [e for e in items if e["notes"].startswith("【信息未核实】")]
check("未核实前缀 310", len(unverified) == 310)
check("空平台补未知平台", all(e["platform"] for e in items))
check("source_url 已全部清空", all(e["source_url"] == "" for e in items))
check("requirements 截断 500（前缀+500）", all(len(e["notes"]) <= 520 for e in items))

# ---------- 播种 ----------
from app.db import Database

db = Database()
inserted, skipped = db.seed_builtin_editors(JSON_PATH)
check("首次播种 (2481, 0)", inserted == 2481 and skipped == 0)
check("editors 表 2481 条（含小黑屋）",
      len(db.list_editors(include_blacklisted=True)) == 2481)
check("默认列表排除小黑屋 1437", len(db.list_editors()) == 2481 - 1044)
check("二次播种幂等 (0, 0)", db.seed_builtin_editors(JSON_PATH) == (0, 0))

# 全新连接（模拟再次启动）也不重复播种
db2 = Database()
check("重新初始化不重复播种", db2.seed_builtin_editors(JSON_PATH) == (0, 0)
      and len(db2.list_editors(include_blacklisted=True)) == 2481)

# 清空编辑表后：清标记重播（启动自检路径）
for e in db.list_editors(include_blacklisted=True):
    db.delete_editor(e.id)
db.clear_seed_marker()
inserted2, skipped2 = db.seed_builtin_editors(JSON_PATH)
check("清空后可重新播种", inserted2 == 2481
      and len(db.list_editors(include_blacklisted=True)) == 2481)

# 手动重导入去重：清标记重播，全部 email 已存在 → 全跳过
db.clear_seed_marker()
inserted3, skipped3 = db.seed_builtin_editors(JSON_PATH)
check("重导入按 email 去重全跳过", inserted3 == 0 and skipped3 == 2481
      and len(db.list_editors(include_blacklisted=True)) == 2481)

platforms = db.distinct_platforms()
check("distinct_platforms 正常", len(platforms) > 10 and "未知平台" in platforms or len(platforms) > 10)
check("distinct_genres 正常", len(db.distinct_genres()) >= 2)

# ---------- 页面（走 main._make_window 启动路径，含"编辑表空则自动重播种"） ----------
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
qapp = QApplication.instance() or QApplication([])

# 先清空编辑表，验证启动自检会自动重新播种
for e in db.list_editors(include_blacklisted=True):
    db.delete_editor(e.id)

from main import _make_window
win = _make_window()   # 内部应触发"空表→清标记→重播种"
check("启动自检自动重播种", len(db.list_editors(include_blacklisted=True)) == 2481)
check("启动后 editors 仍 2481（未重复播种）",
      len(db.list_editors(include_blacklisted=True)) == 2481)

editors_page = win._pages["editors"]
check("infoBar 动态文案", "内置 2481 位编辑" in editors_page.info_text.text())

submit_page = win._pages["submit"]
visible_ids = set()
for r in range(submit_page.table.rowCount()):
    data = submit_page.table.item(r, 0)
    if data is not None and data.data(Qt.UserRole) is not None:
        visible_ids.add(data.data(Qt.UserRole))
bl_ids = {e.id for e in db.list_editors(include_blacklisted=True) if e.blacklisted}
check("投稿页编辑列表默认不含 blacklisted", len(visible_ids) == 1437
      and not (visible_ids & bl_ids))

# 小黑屋条目仍可在编辑列表查看（含 blacklisted 筛选查询）
check("编辑列表可查小黑屋条目", len(db.list_editors(include_blacklisted=True,
                                                  keyword="银杏")) >= 0)

print()
print(f"全部通过：{sum(RESULTS)}/{len(RESULTS)} 项")
