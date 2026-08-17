"""账号激活：登录会话 + 卡密绑定账号（取代旧的「机器指纹 + 卡密核销」）。

体系说明（v1.3.0）：
- 激活 = 「已登录账号」且「账号已绑定卡密」。
- 登录态存本地 session.json（token + email + card_bound），账号可在多设备登录。
- 卡密绑定到账号而非机器，换机/重装后登录同一账号即可继续使用。
- 单点登录在服务端保证：新设备登录后旧设备会话被踢下线。
- 为兼容旧调用，保留 is_activated()/activate()/machine_id()/normalize_key()。
  machine_id() 仅用于新设备的 device_id 标识（会话表），不再作为激活依据。
"""
from __future__ import annotations

import re

from . import auth_client


def machine_id() -> str:
    """本机指纹（SHA-256 前 32 位），用于会话表的 device_id 标识。

    Windows 优先取注册表 MachineGuid（重装系统才变）；失败回退 MAC 地址。
    """
    import hashlib
    import sys
    raw = ""
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography") as k:
                raw, _ = winreg.QueryValueEx(k, "MachineGuid")
        except OSError:
            raw = ""
    if not raw:
        import uuid
        raw = f"mac-{uuid.getnode()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def normalize_key(card_key: str) -> str:
    """去掉空格和连字符、转大写，便于用户按分组格式输入。"""
    return re.sub(r"[\s-]+", "", card_key or "").upper()


# ---------- 会话状态 ----------

def session() -> dict | None:
    """当前本地会话（token/email/card_bound）或 None。"""
    return auth_client.client().load_session()


def is_logged_in() -> bool:
    """本地是否存在登录会话（token）。"""
    sess = session()
    return bool(sess and sess.get("token"))


def is_activated() -> bool:
    """是否可进入主界面：已登录且已绑定卡密（本地判断，不联网）。"""
    sess = session()
    return bool(sess and sess.get("token") and sess.get("card_bound"))


def session_status() -> dict:
    """联网校验会话状态，返回：
    {code, logged_in, card_bound, email, msg}
    code: ok / need_card / kicked / expired / network / no_session
    """
    sess = session()
    if not sess or not sess.get("token"):
        return {"code": "no_session", "logged_in": False,
                "card_bound": False, "email": "", "msg": "未登录"}
    ok, info = auth_client.client().validate(sess["token"])
    if not ok:
        code = info.get("code", "expired")
        if code == "kicked":
            auth_client.client().clear_session()
            return {"code": "kicked", "logged_in": False,
                    "card_bound": False, "email": sess.get("email", ""),
                    "msg": "账号已在其他设备登录，请重新登录"}
        if code == "network":
            # 离线宽限：本地曾绑卡则放行，否则提示需网络
            if sess.get("card_bound"):
                return {"code": "ok", "logged_in": True,
                        "card_bound": True, "email": sess.get("email", ""),
                        "msg": ""}
            return {"code": "network", "logged_in": False,
                    "card_bound": False, "email": sess.get("email", ""),
                    "msg": "无法连接服务器，请检查网络后重试"}
        auth_client.client().clear_session()
        return {"code": "expired", "logged_in": False,
                "card_bound": False, "email": sess.get("email", ""),
                "msg": "登录已失效，请重新登录"}
    email = info.get("email", sess.get("email", ""))
    card_bound = bool(info.get("card_bound"))
    # 同步最新状态到本地
    sess["email"] = email
    sess["card_bound"] = card_bound
    auth_client.client().save_session(sess)
    if not card_bound:
        return {"code": "need_card", "logged_in": True,
                "card_bound": False, "email": email, "msg": "尚未绑定卡密"}
    return {"code": "ok", "logged_in": True,
            "card_bound": True, "email": email, "msg": ""}


def bind_card(card_key: str, token: str | None = None) -> tuple[bool, str]:
    """把卡密绑定到当前登录账号。成功返回 (True, 提示)。"""
    key = normalize_key(card_key)
    if not key:
        return False, "请输入卡密"
    sess = token and {"token": token} or session()
    if not sess or not sess.get("token"):
        return False, "请先登录"
    return auth_client.client().bind_card(sess["token"], key)


def activate(card_key: str, url: str | None = None, timeout: int = 20) -> tuple[bool, str]:
    """兼容旧接口：绑定卡密到当前账号（需已登录）。"""
    return bind_card(card_key)


def logout() -> bool:
    """退出登录（清理本地会话并通知服务端）。"""
    sess = session()
    token = sess.get("token") if sess else None
    return auth_client.client().logout(token) if token else True
