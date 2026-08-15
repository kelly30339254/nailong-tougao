"""检测更新冒烟测试：版本比较 + check_for_update 三种响应。

用法：.venv/Scripts/python.exe scripts/smoke_update.py
（mock fetch_json，无需联网、无需 Qt）
"""
import os
import sys
import tempfile

os.environ["NAILONG_DATA_DIR"] = tempfile.mkdtemp(prefix="nailong_update_")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = []


def check(name, cond):
    RESULTS.append(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        raise AssertionError(name)


from app import APP_VERSION  # noqa: E402
from app import update_check  # noqa: E402

# ---------- 版本比较 ----------
check("1.1.0 > 1.0.0", update_check.is_newer("1.1.0", "1.0.0") is True)
check("同版本不算新", update_check.is_newer("1.0.0", "1.0.0") is False)
check("旧版本不算新", update_check.is_newer("0.9.9", "1.0.0") is False)
check("带 v 前缀", update_check.is_newer("v1.0.1", "1.0.0") is True)
check("位数不同 1.0.0.1 > 1.0.0", update_check.is_newer("1.0.0.1", "1.0.0") is True)
check("1.10.0 > 1.9.0（按数字而非字符串）",
      update_check.is_newer("1.10.0", "1.9.0") is True)

# ---------- check_for_update：有新版 ----------
def fake_fetch_new(url, timeout=15):
    return {"version": "99.0.0", "notes": "大更新", "download_url": "https://pan.quark.cn/s/x"}

update_check.fetch_json = fake_fetch_new
info = update_check.check_for_update()
check("有新版返回信息", info is not None and info["version"] == "99.0.0"
      and info["download_url"] == "https://pan.quark.cn/s/x")

# ---------- check_for_update：同版本 ----------
def fake_fetch_same(url, timeout=15):
    return {"version": APP_VERSION, "notes": "", "download_url": ""}

update_check.fetch_json = fake_fetch_same
check("同版本返回 None", update_check.check_for_update() is None)

# ---------- check_for_update：坏数据 ----------
update_check.fetch_json = lambda url, timeout=15: {"foo": "bar"}
check("缺 version 字段返回 None", update_check.check_for_update() is None)

# ---------- check_for_update：网络异常向上抛 ----------
def fake_fetch_down(url, timeout=15):
    raise OSError("connection refused")

update_check.fetch_json = fake_fetch_down
try:
    update_check.check_for_update()
    raised = False
except OSError:
    raised = True
check("网络异常向上抛（由调用方静默处理）", raised)

print()
print(f"全部通过：{sum(RESULTS)}/{len(RESULTS)} 项")
