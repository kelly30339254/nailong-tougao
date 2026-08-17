"""首次启动登录/激活对话框（兼容入口）。

v1.3.0 起激活体系从「机器指纹 + 卡密核销」改为「邮箱账号登录 + 卡密绑定账号」。
本模块保留 ActivationDialog 类作为兼容入口，内部直接委托给 auth_dialog.AuthDialog。
"""
from __future__ import annotations

from .auth_dialog import AuthDialog


class ActivationDialog(AuthDialog):
    """账号登录/注册/找回密码/绑定卡密（兼容旧类名）。

    exec() 返回 Accepted 表示已登录且已绑定卡密，可进入主界面。
    """

    def __init__(self, parent=None, initial_mode: str = "login"):
        super().__init__(parent, initial_mode=initial_mode)
