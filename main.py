"""奶龙投稿助手 入口。

隐藏自检：环境变量 NAILONG_SMOKE=1 时，构造主窗口并遍历全部页面，
打印 SMOKE_OK 后以退出码 0 退出（异常则以非 0 退出），供无头验证（含 exe）。
"""
import os
import sys

from PySide6.QtWidgets import QApplication, QDialog

from app import license as lic
from app.db import Database
from app.settings_store import SettingsStore
from app.theme import apply_theme, THEMES, DEFAULT_THEME
from app.main_window import MainWindow, NAV_ITEMS


def _make_window():
    db = Database()
    store = SettingsStore(db)
    from app.theme import resource_path
    seed_path = resource_path(os.path.join("app", "data", "builtin_editors.json"))
    # 启动自检：编辑表为空（如被清空过）则清标记重新播种
    if db.counts()["编辑总数"] == 0:
        db.clear_seed_marker()
    inserted, _skipped = db.seed_builtin_editors(seed_path)
    if inserted:
        print(f"已导入内置编辑 {inserted} 位")
    app = QApplication.instance()
    theme = store.get_theme()
    apply_theme(app, theme if theme in THEMES else DEFAULT_THEME)
    return MainWindow(db, store)


def smoke() -> int:
    _app = QApplication(sys.argv)
    try:
        window = _make_window()
        for page_id, _title in NAV_ITEMS:
            window.navigate(page_id)
        for theme_name in THEMES:
            apply_theme(_app, theme_name)
    except Exception as exc:
        print(f"SMOKE_FAIL: {exc}", file=sys.stderr)
        return 1
    print("SMOKE_OK", flush=True)
    return 0


def main():
    if os.environ.get("NAILONG_SMOKE") == "1":
        sys.exit(smoke())

    app = QApplication(sys.argv)
    app.setApplicationName("奶龙投稿助手")
    if not lic.is_activated():
        from app.activation_dialog import ActivationDialog
        if ActivationDialog().exec() != QDialog.Accepted:
            sys.exit(0)
    window = _make_window()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
