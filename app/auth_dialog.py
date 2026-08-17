"""账号登录/注册/找回密码/绑定卡密对话框。

三种模式状态机：登录 / 注册 / 忘记密码；以及「已登录但未绑卡」时的绑定卡密模式。
支持邮箱验证码发送（注册/登录用）、60 秒重发倒计时、发码后锁定邮箱输入框。
所有联网操作在后台 QThread 中进行，避免卡界面。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel,
    QLineEdit, QPushButton, QMessageBox, QFrame,
)

from . import auth_client, license as lic
from .icons import app_icon


class _AuthTask(QThread):
    """通用后台联网任务。fn: () -> (bool, msg[, session])"""

    result = Signal(object, object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            out = self.fn()
            self.result.emit(True, out)
        except Exception as exc:
            self.result.emit(False, f"操作失败：{exc}")


class AuthDialog(QDialog):
    """登录/注册/找回密码/绑定卡密。

    exec() 返回 Accepted 表示「已登录且已绑定卡密」，可进入主界面。
    若传入 initial_mode="card"，表示账号已登录但未绑卡，直接进卡片绑定。
    """

    def __init__(self, parent=None, initial_mode: str = "login"):
        super().__init__(parent)
        self.setObjectName("authDialog")
        self.setWindowTitle("账号登录")
        self.setWindowIcon(app_icon())
        self.setFixedWidth(420)
        self.setMinimumHeight(520)
        self.setModal(True)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self._worker: _AuthTask | None = None
        self._verification_id: str = ""
        self._email_locked = False
        self._countdown = 0
        self._busy = False
        self._mode = "login"

        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._on_tick)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("authCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(28, 26, 28, 22)
        layout.setSpacing(10)

        icon = QLabel()
        icon.setAlignment(Qt.AlignCenter)
        icon.setPixmap(app_icon().pixmap(56, 56))
        layout.addWidget(icon)
        brand = QLabel("奶龙投稿助手")
        brand.setObjectName("authBrand")
        brand.setAlignment(Qt.AlignCenter)
        layout.addWidget(brand)

        self.title = QLabel()
        self.title.setObjectName("authTitle")
        self.title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.title)
        self.hint = QLabel()
        self.hint.setObjectName("authHint")
        self.hint.setWordWrap(True)
        self.hint.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        layout.addWidget(self.hint)

        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("name@example.com")
        self.email_edit.setMaxLength(120)
        self.email_row = self._labeled("邮箱", self.email_edit)
        layout.addWidget(self.email_row)

        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("6 位验证码")
        self.code_edit.setMaxLength(10)
        self.send_code_btn = QPushButton("获取验证码")
        self.send_code_btn.setObjectName("authSecondary")
        self.send_code_btn.clicked.connect(self._on_send_code)
        code_inner = QWidget()
        code_line = QHBoxLayout(code_inner)
        code_line.setContentsMargins(0, 0, 0, 0)
        code_line.setSpacing(8)
        code_line.addWidget(self.code_edit, 1)
        code_line.addWidget(self.send_code_btn)
        self.code_row = self._labeled("验证码", code_inner)
        layout.addWidget(self.code_row)

        self.password_edit = QLineEdit()
        self.password_edit.setPlaceholderText("请输入密码")
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_row = self._labeled("密码", self.password_edit)
        layout.addWidget(self.password_row)

        self.confirm_edit = QLineEdit()
        self.confirm_edit.setPlaceholderText("确认密码")
        self.confirm_edit.setEchoMode(QLineEdit.Password)
        self.confirm_row = self._labeled("确认密码", self.confirm_edit)
        layout.addWidget(self.confirm_row)

        self.card_edit = QLineEdit()
        self.card_edit.setPlaceholderText("例如：NLK-XXXX-XXXX-XXXX")
        self.card_edit.setMaxLength(80)
        self.card_row = self._labeled("卡密", self.card_edit)
        layout.addWidget(self.card_row)

        self.error_label = QLabel("")
        self.error_label.setObjectName("authError")
        self.error_label.setWordWrap(True)
        self.error_label.setAlignment(Qt.AlignHCenter)
        layout.addWidget(self.error_label)

        self.submit_btn = QPushButton("登录")
        self.submit_btn.setObjectName("primaryBtn")
        self.submit_btn.setMinimumHeight(38)
        self.submit_btn.setDefault(True)
        self.submit_btn.clicked.connect(self._on_submit)
        layout.addWidget(self.submit_btn)

        self.switch_widget = QWidget()
        self.switch_row = QHBoxLayout(self.switch_widget)
        self.switch_row.setContentsMargins(0, 4, 0, 0)
        self.switch_row.setAlignment(Qt.AlignCenter)
        self.link_register = QPushButton("没有账号？注册")
        self.link_register.setObjectName("authLink")
        self.link_register.setFlat(True)
        self.link_register.setCursor(Qt.PointingHandCursor)
        self.link_register.clicked.connect(lambda: self.set_mode("register"))
        self.switch_row.addWidget(self.link_register)
        self.link_login = QPushButton("已有账号？登录")
        self.link_login.setObjectName("authLink")
        self.link_login.setFlat(True)
        self.link_login.setCursor(Qt.PointingHandCursor)
        self.link_login.clicked.connect(lambda: self.set_mode("login"))
        self.switch_row.addWidget(self.link_login)
        self.link_reset = QPushButton("忘记密码？")
        self.link_reset.setObjectName("authLink")
        self.link_reset.setFlat(True)
        self.link_reset.setCursor(Qt.PointingHandCursor)
        self.link_reset.clicked.connect(lambda: self.set_mode("reset"))
        self.switch_row.addWidget(self.link_reset)
        layout.addWidget(self.switch_widget)

        self.cancel_btn = QPushButton("退出")
        self.cancel_btn.setObjectName("authGhost")
        self.cancel_btn.setFlat(True)
        self.cancel_btn.clicked.connect(self._on_cancel)
        layout.addWidget(self.cancel_btn, 0, Qt.AlignHCenter)
        layout.addStretch(1)
        outer.addWidget(card)

        self.set_mode(initial_mode)
        self.email_edit.setFocus()

    @staticmethod
    def _labeled(caption: str, widget: QWidget) -> QWidget:
        wrap = QWidget()
        box = QVBoxLayout(wrap)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)
        label = QLabel(caption)
        label.setObjectName("authFieldLabel")
        box.addWidget(label)
        box.addWidget(widget)
        return wrap

    # ---------- 模式 ----------
    def set_mode(self, mode: str):
        self._mode = mode
        self._verification_id = ""
        self._email_locked = False
        self._set_status("")
        if mode == "card":
            self.title.setText("绑定卡密")
            self.hint.setText("一张卡密绑定本账号后，可在任意设备登录使用。")
            self.email_row.hide()
            self.code_row.hide()
            self.password_row.hide()
            self.confirm_row.hide()
            self.card_row.show()
            self.submit_btn.setText("绑定卡密")
            self.switch_widget.setVisible(False)
            self.cancel_btn.setText("退出登录")
            self.card_edit.setFocus()
            return
        self.email_row.show()
        self.password_row.show()
        self.card_row.hide()
        if mode == "register":
            self.title.setText("注册账号")
            self.hint.setText("填写邮箱，获取验证码后设置密码（需联网）。")
            self.code_row.show()
            self.confirm_row.show()
            self.submit_btn.setText("注册并登录")
            self.confirm_edit.setPlaceholderText("确认密码")
        elif mode == "reset":
            self.title.setText("找回密码")
            self.hint.setText("输入注册邮箱，获取验证码后设置新密码。")
            self.code_row.show()
            self.confirm_row.show()
            self.submit_btn.setText("重置密码")
            self.confirm_edit.setPlaceholderText("确认新密码")
        else:
            self._mode = "login"
            self.title.setText("欢迎回来")
            self.hint.setText("使用邮箱和密码登录，登录后可绑定卡密。")
            self.code_row.hide()
            self.confirm_row.hide()
            self.submit_btn.setText("登录")
        self.switch_widget.setVisible(True)
        self.link_register.setVisible(self._mode != "register")
        self.link_login.setVisible(self._mode != "login")
        self.link_reset.setVisible(self._mode != "reset")
        self.cancel_btn.setText("退出")
        self._reset_countdown()
        self._refresh_email_enabled()

    def current_mode(self) -> str:
        return self._mode

    def _on_cancel(self):
        if self._mode == "card":
            lic.logout()
            self.reject()
            mw = self._find_main_window()
            if mw is not None:
                mw._quit_app()
            return
        self.reject()

    def _find_main_window(self):
        widget = self.parent()
        while widget is not None:
            if hasattr(widget, "_quit_app"):
                return widget
            widget = widget.parent()
        app = QApplication.instance()
        if app is not None:
            for top in app.topLevelWidgets():
                if hasattr(top, "_quit_app"):
                    return top
        return None

    def _set_status(self, text: str, kind: str | None = None):
        if kind is None:
            kind = "info" if (text or "").startswith("正在") else "error"
        self.error_label.setText(text)
        self.error_label.setProperty("kind", kind)
        style = self.error_label.style()
        style.unpolish(self.error_label)
        style.polish(self.error_label)

    # ---------- 验证码 ----------
    def _on_send_code(self):
        email = self.email_edit.text().strip().lower()
        if not email or "@" not in email:
            self._set_status("请输入正确的邮箱地址")
            return
        self._set_busy(True)
        self._set_status("正在发送验证码……")
        self.send_code_btn.setEnabled(False)
        self.send_code_btn.setText("发送中…")
        target = "reset" if self.current_mode() == "reset" else "register"
        self._worker = _AuthTask(lambda: auth_client.client().send_code(email, target), self)
        self._worker.result.connect(lambda ok, out: self._on_send_result(ok, out, email))
        self._worker.start()

    def _on_send_result(self, ok: bool, out, email: str):
        if ok and isinstance(out, tuple) and len(out) == 3:
            ok, msg, verification_id = out
        elif isinstance(out, tuple):
            ok, msg, verification_id = out[0], str(out[1]), ""
        else:
            msg, verification_id = str(out), ""
        if ok:
            self._verification_id = verification_id
            self._set_status(f"验证码已发送到 {email}，10 分钟内有效", "ok")
            self._start_countdown(60)
            self._email_locked = True
            self._refresh_email_enabled()
            self.code_edit.setFocus()
            self.code_edit.setText("")
        else:
            self._set_status(msg)
        self._set_busy(False)
        if not self._countdown:
            self.send_code_btn.setEnabled(True)
            self.send_code_btn.setText("获取验证码")

    def _start_countdown(self, seconds: int):
        self._countdown = seconds
        self.send_code_btn.setText(f"重新发送({seconds}s)")
        self.send_code_btn.setEnabled(False)
        self._countdown_timer.start()

    def _on_tick(self):
        if self._countdown > 0:
            self._countdown -= 1
            self.send_code_btn.setText(f"重新发送({self._countdown}s)")
        if self._countdown <= 0:
            self._countdown_timer.stop()
            self.send_code_btn.setText("获取验证码")
            self.send_code_btn.setEnabled(not self._busy)

    def _reset_countdown(self):
        self._countdown = 0
        self._countdown_timer.stop()
        self.send_code_btn.setText("获取验证码")
        self.send_code_btn.setEnabled(not self._busy)

    def _refresh_email_enabled(self):
        self.email_edit.setEnabled(not self._email_locked)

    # ---------- 提交 ----------
    def _on_submit(self):
        mode = self.current_mode()
        if mode == "card":
            self._submit_card()
        elif mode == "login":
            self._submit_login()
        elif mode == "register":
            self._submit_register()
        elif mode == "reset":
            self._submit_reset()

    def _submit_login(self):
        email = self.email_edit.text().strip().lower()
        password = self.password_edit.text()
        if not email or not password:
            self._set_status("请输入邮箱和密码")
            return
        self._set_busy(True)
        self._set_status("正在登录……")
        self._worker = _AuthTask(
            lambda: auth_client.client().login(email, password), self)
        self._worker.result.connect(self._on_login_result)
        self._worker.start()

    def _on_login_result(self, ok: bool, out):
        self._set_busy(False)
        if not ok:
            self._set_status(str(out))
            return
        _ok, msg, session = out
        if not session or not session.get("token"):
            self._set_status("登录失败，请重试")
            return
        self._after_auth_ok(session)

    def _submit_register(self):
        email = self.email_edit.text().strip().lower()
        code = self.code_edit.text().strip()
        password = self.password_edit.text()
        confirm = self.confirm_edit.text()
        if not email or not code or not password:
            self._set_status("请完整填写邮箱、验证码和密码")
            return
        if password != confirm:
            self._set_status("两次输入的密码不一致")
            return
        self._set_busy(True)
        self._set_status("正在注册……")
        self._worker = _AuthTask(
            lambda: auth_client.client().register(
                email, code, password, self._verification_id), self)
        self._worker.result.connect(self._on_register_result)
        self._worker.start()

    def _on_register_result(self, ok: bool, out):
        self._set_busy(False)
        if not ok:
            self._set_status(str(out))
            return
        _ok, msg, session = out
        if not session or not session.get("token"):
            self._set_status("注册失败，请重试")
            return
        self._after_auth_ok(session)

    def _submit_reset(self):
        email = self.email_edit.text().strip().lower()
        code = self.code_edit.text().strip()
        new_password = self.password_edit.text()
        confirm = self.confirm_edit.text()
        if not email or not code or not new_password:
            self._set_status("请完整填写邮箱、验证码和新密码")
            return
        if new_password != confirm:
            self._set_status("两次输入的密码不一致")
            return
        self._set_busy(True)
        self._set_status("正在重置密码……")
        self._worker = _AuthTask(
            lambda: auth_client.client().reset_password(
                email, code, new_password, self._verification_id), self)
        self._worker.result.connect(self._on_reset_result)
        self._worker.start()

    def _on_reset_result(self, ok: bool, out):
        self._set_busy(False)
        if not ok:
            self._set_status(str(out))
            return
        _ok, msg = out
        QMessageBox.information(self, "重置成功", msg or "密码已重置，请用新密码登录")
        self.password_edit.setText("")
        self.confirm_edit.setText("")
        self.code_edit.setText("")
        self.set_mode("login")

    def _submit_card(self):
        key = self.card_edit.text().strip()
        if not key:
            self._set_status("请输入卡密")
            return
        self._set_busy(True)
        self._set_status("正在绑定卡密……")
        self._worker = _AuthTask(
            lambda: lic.bind_card(key), self)
        self._worker.result.connect(self._on_card_result)
        self._worker.start()

    def _on_card_result(self, ok: bool, out):
        self._set_busy(False)
        if not ok:
            self._set_status(str(out))
            return
        _ok, msg = out
        if _ok:
            self.accept()
        else:
            self._set_status(msg)

    # ---------- 通用 ----------
    def _after_auth_ok(self, session: dict):
        if session.get("card_bound"):
            self.accept()
        else:
            self.set_mode("card")

    def _set_busy(self, busy: bool):
        self._busy = busy
        for w in (self.email_edit, self.code_edit, self.password_edit,
                  self.confirm_edit, self.card_edit, self.submit_btn,
                  self.cancel_btn, self.link_register, self.link_login,
                  self.link_reset):
            w.setEnabled(not busy)
        # 验证码按钮单独处理（倒计时/发送中）
        if self.current_mode() != "login" and not self.card_edit.isVisible():
            self.send_code_btn.setEnabled(not busy and self._countdown <= 0)

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._worker.wait(3000)
        if self._countdown_timer.isActive():
            self._countdown_timer.stop()
        super().closeEvent(event)
