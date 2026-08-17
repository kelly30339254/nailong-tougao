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
    # 回收崩溃残留的「发送中」（超过 30 分钟回退为待发，释放日额度与一稿一投）
    recovered = db.recover_stuck_sending()
    if recovered:
        print(f"已回收 {recovered} 条中断的发送中记录")
    from app.theme import resource_path
    seed_path = resource_path(os.path.join("app", "data", "builtin_editors.json"))
    # 仅首次（无 builtin_seeded 标记）播种内置编辑；
    # 用户清空编辑列表后重启不会重新灌入
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

    # 单实例保护：双开第二个实例直接提示退出，避免争用数据库报 database is locked
    import socket
    from PySide6.QtCore import QLockFile
    from app.db import data_dir
    lock = QLockFile(os.path.join(data_dir(), "app.lock"))
    lock.setStaleLockTime(5000)

    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        if sys.platform == "win32":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
            return bool(h)
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _lock_is_bogus() -> bool:
        """Qt 的过期锁判定很保守：主机名不同、锁文件损坏读不出信息时
        都永远视为有效锁，需手动识别这些情况后清除。"""
        pid, hostname, _appname = lock.getLockInfo()
        if not hostname:
            return True  # 锁文件损坏/0 字节（如拷贝中断、网盘同步占位）
        if hostname.lower() != socket.gethostname().lower():
            return True  # 从别的电脑整体拷来的数据目录
        return not _pid_alive(pid)  # 本机进程已不存在（崩溃残留）

    if not lock.tryLock(100) and _lock_is_bogus():
        lock.removeStaleLockFile()
        lock.tryLock(100)
    if not lock.isLocked():
        app = QApplication(sys.argv)
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(QMessageBox.Warning, "奶龙投稿助手",
                          "程序已在运行，请勿重复打开。\n\n"
                          "如果刚从别的电脑迁移数据、或上次异常退出，"
                          "这可能是残留锁文件误报。")
        force = box.addButton("强制启动", QMessageBox.AcceptRole)
        box.addButton("退出", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not force:
            sys.exit(1)
        # 用户确认没有别的实例在跑：删掉锁文件强行接管
        lock.removeStaleLockFile()
        lock.tryLock(100)

    app = QApplication(sys.argv)
    app.setApplicationName("奶龙投稿助手")
    if not lic.is_activated():
        from app.activation_dialog import ActivationDialog
        if ActivationDialog().exec() != QDialog.Accepted:
            sys.exit(0)
    window = _make_window()
    window.show()
    code = app.exec()
    try:
        window.db.close()
    except Exception:
        pass
    sys.exit(code)


if __name__ == "__main__":
    main()
