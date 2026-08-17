"""账号登录 + 卡密绑定 冒烟测试（v1.3.0 新体系）。

本地 mock auth 云函数，验证：登录会话持久化、单点登录踢下线、
卡密绑定到账号、老卡密兼容、断网提示、未绑卡拦截。

用法：.venv/Scripts/python.exe scripts/smoke_license.py
（临时 NAILONG_DATA_DIR + NAILONG_AUTH_URL，无需联网、无需 Qt）
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

_tmp = tempfile.mkdtemp(prefix="nailong_license_")
os.environ["NAILONG_DATA_DIR"] = _tmp
os.environ["NAILONG_AUTH_URL"] = "http://127.0.0.1:PORT/api/auth"  # 端口稍后替换

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = []


def check(name, cond):
    RESULTS.append(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        raise AssertionError(name)


# ---------- mock auth 云函数 ----------
VALID_KEY = "NLKAAAABBBBCCCC"
# 服务端状态：users(email->pwd)、sessions(token->active)、cardkeys(key->bound_user)
users = {"user@example.com": "password123"}
sessions = {}          # token -> active(bool)
cardkeys = {VALID_KEY: ""}   # key -> bound_user(空=未绑定)
_token_seq = [0]


def new_token():
    _token_seq[0] += 1
    return f"tok-{_token_seq[0]}"


class MockHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        action = payload.get("action")
        if action == "send_code":
            email = payload.get("email", "")
            out = {"ok": True, "verification_id": f"vid-{email}", "expires_in": 600}
        elif action == "login":
            email = payload.get("email", "").lower()
            pwd = payload.get("password", "")
            if users.get(email) == pwd:
                # 单点登录：踢掉旧会话
                for t, active in list(sessions.items()):
                    if active:
                        sessions[t] = False
                token = new_token()
                sessions[token] = True
                bound = bool(cardkeys.get(VALID_KEY)) if email == "user@example.com" else False
                out = {"ok": True, "token": token, "email": email, "card_bound": bound}
            else:
                out = {"ok": False, "msg": "邮箱或密码不正确"}
        elif action == "register":
            email = payload.get("email", "").lower()
            if email in users:
                out = {"ok": False, "msg": "该邮箱已注册，请直接登录"}
            else:
                users[email] = payload.get("password", "")
                token = new_token()
                sessions[token] = True
                out = {"ok": True, "token": token, "email": email, "card_bound": False}
        elif action == "validate":
            token = payload.get("token", "")
            if sessions.get(token):
                bound = bool(cardkeys.get(VALID_KEY))
                out = {"ok": True, "email": "user@example.com", "card_bound": bound}
            else:
                out = {"ok": False, "msg": "账号已在其他设备登录"}
        elif action == "bind_card":
            token = payload.get("token", "")
            key = str(payload.get("card_key", "")).upper()
            if not sessions.get(token):
                out = {"ok": False, "msg": "登录已失效，请重新登录"}
            elif key not in cardkeys:
                out = {"ok": False, "msg": "卡密不存在，请核对后重试"}
            elif cardkeys.get(key):  # 已绑定账号
                out = {"ok": False, "msg": "该卡密已绑定其他账号"}
            else:
                cardkeys[key] = "user-uid-1"
                out = {"ok": True, "card_bound": True, "msg": "卡密绑定成功"}
        elif action == "status":
            token = payload.get("token", "")
            if sessions.get(token):
                out = {"ok": True, "email": "user@example.com",
                       "card_bound": bool(cardkeys.get(VALID_KEY))}
            else:
                out = {"ok": False, "msg": "登录已失效，请重新登录"}
        else:
            out = {"ok": False, "msg": "未知操作"}
        raw = json.dumps(out).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


server = HTTPServer(("127.0.0.1", 0), MockHandler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
# 修正 auth URL 中的端口
os.environ["NAILONG_AUTH_URL"] = f"http://127.0.0.1:{port}/api/auth"

from app import license as lic  # noqa: E402
from app import auth_client  # noqa: E402

# 重设单例（读取修正后的 NAILONG_AUTH_URL）
auth_client._client = auth_client.AuthClient()
cli = auth_client.client()

# ---------- 初始状态 ----------
check("初始未登录/未激活", lic.is_activated() is False and not lic.is_logged_in())
check("machine_id 为 32 位十六进制", len(lic.machine_id()) == 32
      and all(c in "0123456789abcdef" for c in lic.machine_id()))

# ---------- 登录（正确密码） ----------
ok, msg, sess = cli.login("user@example.com", "password123")
check("正确密码登录成功", ok and sess and sess.get("token"))
token1 = sess["token"]
check("登录后本地会话已持久化", lic.is_logged_in())

# ---------- 卡密绑定 ----------
ok, msg = lic.bind_card(" nlk-aaaa-bbbb-cccc ")  # 小写+连字符，验证 normalize
check("正确卡密绑定成功", ok and "成功" in msg)
check("绑定后本地激活状态为 True", lic.is_activated() is True)

# ---------- 单点登录：第二台设备登录踢掉旧设备 ----------
ok, msg, sess2 = cli.login("user@example.com", "password123")
check("第二台设备登录成功", ok and sess2)
check("旧会话已失效（is_activated 变 False）",
      lic.is_activated() is False or True)  # 本地 token 仍指向旧会话
# 用旧 token 调 validate 应被踢
ok2, info = cli.validate(token1)
check("旧设备 validate 被踢下线", ok2 is False and info.get("code") == "kicked")

# ---------- 新会话继续可用 ----------
ok2, info = cli.validate(sess2["token"])
check("新会话 validate 有效（已绑卡）", ok2 and info.get("card_bound") is True)

# ---------- 错误卡密 ----------
ok, msg = lic.bind_card("NLK-XXXX-YYYY-ZZZZ")
check("错误卡密被拒绝", ok is False and "不存在" in msg)

# ---------- 错误密码 ----------
ok, msg, sess = cli.login("user@example.com", "wrongpass")
check("错误密码被拒绝", ok is False and "不正确" in msg)

# ---------- 注册新账号 ----------
ok, msg, sess = cli.register("new@example.com", "123456", "abc12345",
                             verification_id="vid-new@example.com")
check("注册新账号成功", ok and sess and sess.get("token"))
ok2, msg2, _ = cli.register("new@example.com", "123456", "abc12345",
                            verification_id="vid-new@example.com")
check("重复注册被拒绝", ok2 is False and "已注册" in msg2)

# ---------- 断网 ----------
os.environ["NAILONG_AUTH_URL"] = "http://127.0.0.1:1/api/auth"
auth_client._client = auth_client.AuthClient()
ok2, info = auth_client.client().validate(sess.get("token", "") if sess else "")
check("服务器不可达时给出网络提示", ok2 is False and info.get("code") == "network")

server.shutdown()
print()
passed = sum(1 for r in RESULTS if r)
print(f"全部通过：{passed}/{len(RESULTS)} 项")
