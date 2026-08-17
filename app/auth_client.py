"""账号登录客户端：会话持久化 + auth 云函数 HTTP 调用封装。

纯 Python 桌面应用，无 @cloudbase/js-sdk，全部逻辑走 auth 云函数
（HTTP 路由 /api/auth，action 分发）。登录态存 %APPDATA%\\NailongPost\\session.json。
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request

from .db import data_dir

# auth 云函数（CloudBase 默认域名 HTTP 路由；部署见 server/README.md）
# 可用环境变量 NAILONG_AUTH_URL 覆盖（供本地 mock 冒烟测试）
AUTH_URL = os.environ.get(
    "NAILONG_AUTH_URL",
    "https://nailong-d4g922z6h6d9ff59e-1455870789"
    ".tcloudbaseapp.com/api/auth",
)

_SESSION_FILE = "session.json"
# 无会话 / 会话校验失败但本地有卡密绑定痕迹时的离线宽限天数（本次先做简单：在线校验为主）
OFFLINE_GRACE_DAYS = 0


def _ssl_context() -> ssl.SSLContext:
    """HTTPS 校验用 CA 证书上下文（PyInstaller 打包后需 certifi 兜底）。"""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _session_path() -> str:
    return os.path.join(data_dir(), _SESSION_FILE)


class AuthClient:
    """auth 云函数 HTTP 客户端 + 本地会话管理。"""

    def __init__(self, url: str = "", timeout: int = 12):
        # url 为空时运行时读取环境变量（便于本地 mock 冒烟测试替换端口）
        self.url = url or os.environ.get("NAILONG_AUTH_URL", AUTH_URL)
        self.timeout = timeout

    # ---------- HTTP ----------
    def _post(self, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=body, method="POST",
            headers={
                "User-Agent": "NailongPost/1.0 (+auth)",
                "Content-Type": "application/json",
                "Accept": "application/json",
            })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout,
                                        context=_ssl_context()) as resp:
                raw = resp.read(64 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read(4 * 1024).decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            extra = f"（{detail.strip()}）" if detail.strip() else ""
            raise ConnectionError(f"服务器返回错误（HTTP {exc.code}）{extra}")
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ConnectionError(f"无法连接服务器，请检查网络后重试（{reason}）")
        if len(raw) > 64 * 1024:
            raise ConnectionError("服务器响应异常")
        try:
            payload_resp = json.loads(raw.decode("utf-8-sig"))
        except ValueError:
            raise ConnectionError("服务器响应异常")
        if not isinstance(payload_resp, dict):
            raise ConnectionError("服务器响应异常")
        return payload_resp

    def _call(self, action: str, **params) -> dict:
        payload = {"action": action, **params}
        resp = self._post(payload)
        return resp

    # ---------- 本地会话 ----------
    def load_session(self) -> dict | None:
        try:
            with open(_session_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not data.get("token"):
            return None
        return data

    def save_session(self, session: dict):
        path = _session_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False)

    def clear_session(self):
        path = _session_path()
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    # ---------- 业务方法 ----------
    def send_code(self, email: str, target: str = "register"
                  ) -> tuple[bool, str, str]:
        """发送邮箱验证码。target: register(注册/登录用) / reset(找回密码)。

        返回 (ok, msg, verification_id)，verification_id 供注册/重置校验用。
        """
        try:
            resp = self._call("send_code", email=email, target=target)
        except ConnectionError as exc:
            return False, str(exc), ""
        if resp.get("ok"):
            return (True,
                    str(resp.get("msg") or "验证码已发送，请查收邮件"),
                    str(resp.get("verification_id") or ""))
        return False, str(resp.get("msg") or "验证码发送失败"), ""

    def register(self, email: str, code: str, password: str,
                 verification_id: str) -> tuple[bool, str, dict | None]:
        """注册账号。成功返回 (True, msg, session)，session 含 token/email。"""
        try:
            resp = self._call("register", email=email, code=code,
                              password=password, verification_id=verification_id)
        except ConnectionError as exc:
            return False, str(exc), None
        if resp.get("ok"):
            session = {
                "token": resp.get("token"),
                "email": resp.get("email", email),
                "card_bound": bool(resp.get("card_bound")),
            }
            if session.get("token"):
                self.save_session(session)
            return True, str(resp.get("msg") or "注册成功"), session
        return False, str(resp.get("msg") or "注册失败"), None

    def login(self, email: str, password: str) -> tuple[bool, str, dict | None]:
        """密码登录。成功返回 (True, msg, session)。"""
        try:
            resp = self._call("login", email=email, password=password)
        except ConnectionError as exc:
            return False, str(exc), None
        if resp.get("ok"):
            session = {
                "token": resp.get("token"),
                "email": resp.get("email", email),
                "card_bound": bool(resp.get("card_bound")),
            }
            if session.get("token"):
                self.save_session(session)
            return True, str(resp.get("msg") or "登录成功"), session
        return False, str(resp.get("msg") or "登录失败"), None

    def reset_password(self, email: str, code: str, new_password: str,
                       verification_id: str) -> tuple[bool, str]:
        """找回密码：校验验证码后重置密码。"""
        try:
            resp = self._call("reset_password", email=email, code=code,
                              new_password=new_password, verification_id=verification_id)
        except ConnectionError as exc:
            return False, str(exc)
        if resp.get("ok"):
            return True, str(resp.get("msg") or "密码已重置")
        return False, str(resp.get("msg") or "重置失败")

    def validate(self, token: str) -> tuple[bool, dict]:
        """校验会话是否仍有效。返回 (ok, info)。ok=False 时 info['code'] 区分原因。"""
        try:
            resp = self._call("validate", token=token)
        except ConnectionError as exc:
            return False, {"code": "network", "msg": str(exc)}
        if resp.get("ok"):
            return True, {
                "email": resp.get("email", ""),
                "card_bound": bool(resp.get("card_bound")),
            }
        code = "session_expired"
        msg = str(resp.get("msg") or "登录已失效")
        if "其他设备" in msg:
            code = "kicked"
        return False, {"code": code, "msg": msg}

    def logout(self, token: str) -> bool:
        """退出登录，本地清理。返回是否调用成功（失败也清本地）。"""
        try:
            self._call("logout", token=token)
        except ConnectionError:
            pass
        finally:
            self.clear_session()
        return True

    def bind_card(self, token: str, card_key: str) -> tuple[bool, str]:
        """核销卡密并绑定到当前账号。"""
        try:
            resp = self._call("bind_card", token=token, card_key=card_key)
        except ConnectionError as exc:
            return False, str(exc)
        if resp.get("ok"):
            sess = self.load_session()
            if sess:
                sess["card_bound"] = True
                self.save_session(sess)
            return True, str(resp.get("msg") or "卡密绑定成功")
        return False, str(resp.get("msg") or "绑定失败")

    def status(self, token: str) -> tuple[bool, dict]:
        """查询当前账号卡密状态。返回 (ok, info)。"""
        try:
            resp = self._call("status", token=token)
        except ConnectionError as exc:
            return False, {"code": "network", "msg": str(exc)}
        if resp.get("ok"):
            return True, {
                "email": resp.get("email", ""),
                "card_bound": bool(resp.get("card_bound")),
            }
        return False, {"code": "session_expired", "msg": str(resp.get("msg") or "登录已失效")}


# 模块级单例（与旧 license 模块用法保持一致，方便各页 import）
_client = AuthClient()


def client() -> AuthClient:
    return _client
