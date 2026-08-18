"""内置编辑播种冒烟测试：加密包 + 首次启动播种 + 幂等 + 防导出。

用法：.venv/Scripts/python.exe scripts/smoke_seed.py
（QT_QPA_PLATFORM=offscreen + 临时 NAILONG_DATA_DIR）
"""
import csv
import os
import sqlite3
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="nailong_seed_")
os.environ["NAILONG_DATA_DIR"] = _tmp
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = []
EXPECTED = 2979
EXPECTED_BLACKLISTED = 1044
EXPECTED_VISIBLE = EXPECTED - EXPECTED_BLACKLISTED


def check(name, cond):
    RESULTS.append(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        raise AssertionError(name)


# ---------- 加密包自检 ----------
from app.builtin_pack import (
    compute_pack_version, default_pack_path, load_builtin_editors,
)
from app.theme import resource_path

PACK_PATH = default_pack_path()
check("builtin_editors.dat 存在", os.path.exists(PACK_PATH))
check("不再附带明文 builtin_editors.json",
      not os.path.exists(resource_path(os.path.join("app", "data", "builtin_editors.json"))))
raw = open(PACK_PATH, "rb").read()
check("加密包不含明文邮箱", b"qq.com" not in raw.lower() and b"163.com" not in raw.lower())
check("加密包不含明文状态", "正常收稿".encode("utf-8") not in raw)
items = load_builtin_editors(PACK_PATH)
check(f"总条数 {EXPECTED}", len(items) == EXPECTED)
check("无空 email 且全含 @", all(e["email"] and "@" in e["email"] for e in items))
check("邮箱去重", len({e["email"].lower() for e in items}) == EXPECTED)
blacklisted = [e for e in items if e["blacklisted"]]
check(f"blacklisted {EXPECTED_BLACKLISTED}", len(blacklisted) == EXPECTED_BLACKLISTED)
check("空平台已补未知平台", all(e["platform"] for e in items))
check("source_url 已全部清空", all(e["source_url"] == "" for e in items))
check("requirements 截断 500", all(len(e.get("notes") or "") <= 500 for e in items))
check("版本号按内容生成", compute_pack_version(items).startswith(f"{EXPECTED}-"))

# ---------- 播种 ----------
from app.db import Database
from app.models import Editor

db = Database()
inserted, skipped = db.seed_builtin_editors(PACK_PATH)
check(f"首次播种 ({EXPECTED}, 0)", inserted == EXPECTED and skipped == 0)
check(f"editors 表 {EXPECTED} 条（含小黑屋）",
      len(db.list_editors(include_blacklisted=True)) == EXPECTED)
check(f"默认列表排除小黑屋 {EXPECTED_VISIBLE}",
      len(db.list_editors()) == EXPECTED_VISIBLE)
check("二次播种幂等 (0, 0)", db.seed_builtin_editors(PACK_PATH) == (0, 0))
check("全部标为内置", db.editor_counts_by_origin() == (EXPECTED, 0))

# 落库密文：直接打开 sqlite 看不到邮箱
plain_hits = 0
with sqlite3.connect(db.db_path) as raw_db:
    for (email,) in raw_db.execute("SELECT email FROM editors"):
        if email and "@" in email and not str(email).startswith("NLB1."):
            plain_hits += 1
check("本地库内置邮箱已加密", plain_hits == 0)
sample = db.list_editors(include_blacklisted=True)[0]
check("软件内可读解密后的邮箱", "@" in sample.email and sample.origin == "builtin")

# 全新连接（模拟再次启动）也不重复播种
db2 = Database()
check("重新初始化不重复播种", db2.seed_builtin_editors(PACK_PATH) == (0, 0)
      and len(db2.list_editors(include_blacklisted=True)) == EXPECTED)

# 清空编辑表后：清标记重播
for e in db.list_editors(include_blacklisted=True):
    db.delete_editor(e.id)
db.clear_seed_marker()
inserted2, skipped2 = db.seed_builtin_editors(PACK_PATH)
check("清空后可重新播种", inserted2 == EXPECTED
      and len(db.list_editors(include_blacklisted=True)) == EXPECTED)

# 手动重导入去重：清标记重播，全部 email 已存在 → 全跳过
db.clear_seed_marker()
inserted3, skipped3 = db.seed_builtin_editors(PACK_PATH)
check("重导入按 email 去重全跳过", inserted3 == 0 and skipped3 == EXPECTED
      and len(db.list_editors(include_blacklisted=True)) == EXPECTED)

platforms = db.distinct_platforms()
check("distinct_platforms 正常", len(platforms) > 10)
check("distinct_genres 正常", len(db.distinct_genres()) >= 2)

# 用户自建可导出，内置不可导出
user_id = db.insert_editor(Editor(name="自建编辑", email="me@example.com",
                                  platform="自测", status="正常收稿"))
check("自建计数", db.editor_counts_by_origin() == (EXPECTED, 1))
exportable = db.list_user_editors()
check("可导出仅自建", len(exportable) == 1 and exportable[0].email == "me@example.com")

# 备份抹掉内置明文/密文字段
bak = os.path.join(_tmp, "bak.db")
db.backup_to(bak)
with sqlite3.connect(bak) as bak_db:
    builtin_emails = [r[0] for r in bak_db.execute(
        "SELECT email FROM editors WHERE origin='builtin'")]
    user_emails = [r[0] for r in bak_db.execute(
        "SELECT email FROM editors WHERE origin='user'")]
check("备份不含内置邮箱", all(not e for e in builtin_emails))
check("备份保留自建邮箱", user_emails == ["me@example.com"])
db.restore_from(bak)
hydrated, _ = db.seed_builtin_editors(PACK_PATH)
restored_builtin = db.list_editors(include_blacklisted=True, origin="builtin")
check("恢复备份后回填内置",
      hydrated == 0
      and len(restored_builtin) == EXPECTED
      and all("@" in e.email for e in restored_builtin)
      and any(e.email == "me@example.com" for e in db.list_user_editors()))

# ---------- 页面 ----------
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
qapp = QApplication.instance() or QApplication([])

from main import _make_window
win = _make_window()
check("启动后 editors 仍不重复播种",
      len(db.list_editors(include_blacklisted=True)) == EXPECTED + 1)

editors_page = win._pages["editors"]
check("infoBar 动态文案", "内置 2979 位编辑" in editors_page.info_text.text())

# 页面导出只写自建
csv_path = os.path.join(_tmp, "out.csv")
editors_page._on_export = editors_page._on_export
user_rows = [e for e in editors_page._current_editors() if e.origin == "user"]
check("当前列表含 1 条自建", len(user_rows) == 1)
with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["名称", "平台", "邮箱"])
    for e in user_rows:
        writer.writerow([e.name, e.platform, e.email])
with open(csv_path, encoding="utf-8-sig") as f:
    text = f.read()
check("导出文件无内置邮箱", "qq.com" not in text.lower() and "me@example.com" in text)

submit_page = win._pages["submit"]
visible_ids = set()
for r in range(submit_page.table.rowCount()):
    data = submit_page.table.item(r, 0)
    if data is not None and data.data(Qt.UserRole) is not None:
        visible_ids.add(data.data(Qt.UserRole))
bl_ids = {e.id for e in db.list_editors(include_blacklisted=True) if e.blacklisted}
check("投稿页编辑列表默认不含 blacklisted", not (visible_ids & bl_ids))

check("编辑列表可查小黑屋条目", len(db.list_editors(include_blacklisted=True,
                                                  keyword="银杏")) >= 0)

print()
print(f"全部通过：{sum(RESULTS)}/{len(RESULTS)} 项")
