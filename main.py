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
from app.icons import APP_USER_MODEL_ID, app_icon, apply_windows_app_id, refresh_shell_icons
from app.widgets import WheelBlocker
from app.logging_setup import setup_logging, install_excepthook, get_logger


def _make_window():
    db = Database()
    store = SettingsStore(db)
    # 所有崩溃残留统一改为“结果待确认”，避免 SMTP 已成功但数据库未回写造成重复。
    recovered = db.recover_stuck_sending()
    if recovered:
        print(f"已处理 {recovered} 条中断的发送中记录")
    from app.builtin_pack import default_pack_path
    seed_path = default_pack_path()
    # 按内置包版本增量播种：新装全量导入，升级只补缺失项。
    # 内置数据以密文落库；用户清空列表后不会自动回灌。
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
    from PySide6.QtWidgets import QMessageBox
    from app.db import data_dir

    # 必须在 QApplication 之前设置，否则任务栏会沿用 python.exe / 旧 exe 缓存图标
    apply_windows_app_id()

    app = QApplication(sys.argv)
    app.setApplicationName("奶龙投稿助手")
    app.setApplicationDisplayName("奶龙投稿助手")
    app.setOrganizationName("Nailong")
    app.setOrganizationDomain("nailong.zhiyuxiezuo.com")
    app.setDesktopFileName(APP_USER_MODEL_ID)
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)
    # 全局禁用下拉框/数值框的鼠标滚轮（避免误切换/误增减），滚轮只滚动页面
    app._wheel_blocker = WheelBlocker(app)
    app.installEventFilter(app._wheel_blocker)

    lock = QLockFile(os.path.join(data_dir(), "app.lock"))
    lock.setStaleLockTime(5000)

    def _pid_is_our_app(pid: int) -> bool:
        """该 PID 是否是我们程序的活进程。进程名查不到时保守当作是。"""
        if pid <= 0:
            return False
        if sys.platform != "win32":
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False
        import ctypes
        from ctypes import wintypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return False  # 进程已不存在
        try:
            buf = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            if not ctypes.windll.kernel32.QueryFullProcessImageNameW(
                    h, 0, buf, ctypes.byref(size)):
                return True  # 无权查询（系统进程等），保守当作是
            name = os.path.basename(buf.value).lower()
            # 打包后是「奶龙投稿助手.exe」，开发时是 python
            return name in ("奶龙投稿助手.exe", "python.exe", "pythonw.exe")
        finally:
            ctypes.windll.kernel32.CloseHandle(h)

    def _lock_is_bogus() -> bool:
        """Qt 的过期锁判定很保守：主机名不同、锁文件损坏读不出信息时
        都永远视为有效锁，需手动识别这些情况后清除。"""
        pid, hostname, _appname = lock.getLockInfo()
        if not hostname:
            return True  # 锁文件损坏/0 字节（如拷贝中断、网盘同步占位）
        if hostname.lower() != socket.gethostname().lower():
            return True  # 从别的电脑整体拷来的数据目录
        # 主机名相同（新电脑常沿用旧电脑名）时，仅凭 PID 存活不可靠：
        # 可能是撞名的无关进程，需核对进程名是不是本程序
        return not _pid_is_our_app(pid)

    if not lock.tryLock(100) and _lock_is_bogus():
        lock.removeStaleLockFile()
        lock.tryLock(100)
    if not lock.isLocked():
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
    setup_logging()
    install_excepthook()
    log = get_logger("main")
    log.info("启动")

    # 账号登录守卫：先看本地会话，联网校验放到后台，避免断网卡 12 秒
    from PySide6.QtWidgets import QMessageBox as _QMsg
    from app.activation_dialog import ActivationDialog

    if not lic.is_logged_in():
        if ActivationDialog().exec() != QDialog.Accepted:
            sys.exit(0)
    elif not lic.is_activated():
        if ActivationDialog(initial_mode="card").exec() != QDialog.Accepted:
            sys.exit(0)
    window = _make_window()
    window.setWindowIcon(app_icon())
    if sys.platform == "win32":
        window.setProperty("_q_windowsAppId", APP_USER_MODEL_ID)
    window.show()

    def _after_auth_check(status: dict):
        code = status.get("code")
        if code == "kicked":
            _QMsg.information(window, "奶龙投稿助手",
                              "你的账号已在其他设备登录，本设备已下线。")
        if code in ("kicked", "expired"):
            if ActivationDialog(window).exec() != QDialog.Accepted:
                window._quit_app()
        elif code == "need_card":
            if ActivationDialog(window, initial_mode="card").exec() != QDialog.Accepted:
                window._quit_app()

    from app.workers import AiCallWorker
    window._auth_worker = AiCallWorker(lic.session_status, window)
    window._auth_worker.finished_ok.connect(_after_auth_check)
    window._auth_worker.failed.connect(lambda msg: log.warning("会话校验失败：%s", msg))
    window._auth_worker.start()

    import threading
    threading.Thread(target=refresh_shell_icons, daemon=True).start()
    try:
        interval = int(window.store.get("auto_backup_days") or "7")
        keep = int(window.store.get("auto_backup_keep") or "5")
        threading.Thread(
            target=lambda: window.db.auto_backup_if_due(interval, keep),
            daemon=True).start()
    except Exception:
        log.warning("自动备份启动失败", exc_info=True)
    code = app.exec()
    try:
        window.db.close()
    except Exception:
        pass
    sys.exit(code)


if __name__ == "__main__":
    main()
