"""卡密激活冒烟测试：本地 mock 激活服务器，验证激活/拒绝/一次性/机器绑定。

用法：.venv/Scripts/python.exe scripts/smoke_license.py
（临时 NAILONG_DATA_DIR，无需联网、无需 Qt）
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

_tmp = tempfile.mkdtemp(prefix="nailong_license_")
os.environ["NAILONG_DATA_DIR"] = _tmp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = []


def check(name, cond):
    RESULTS.append(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        raise AssertionError(name)


# ---------- mock 激活服务器 ----------
VALID_KEY = "NLKAAAABBBBCCCC"   # 规范化形式（无连字符，与入库一致）
_used_keys = set()


class MockHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        key = str(body.get("card_key", "")).upper()
        if key == VALID_KEY and key not in _used_keys:
            _used_keys.add(key)
            payload = {"ok": True, "msg": "激活成功"}
        elif key == VALID_KEY:
            payload = {"ok": False, "msg": "该卡密已被使用，一张卡密只能激活一次"}
        else:
            payload = {"ok": False, "msg": "卡密不存在，请核对后重试"}
        raw = json.dumps(payload).encode("utf-8")
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
MOCK_URL = f"http://127.0.0.1:{port}/api/activate"

from app import license as lic  # noqa: E402

# ---------- 初始状态 ----------
check("初始未激活", lic.is_activated() is False)
check("machine_id 为 32 位十六进制", len(lic.machine_id()) == 32
      and all(c in "0123456789abcdef" for c in lic.machine_id()))

# ---------- 错误卡密 ----------
ok, msg = lic.activate("NLK-XXXX-YYYY-ZZZZ", url=MOCK_URL)
check("错误卡密被拒绝", ok is False and "不存在" in msg)
check("失败后仍未激活", lic.is_activated() is False)

# ---------- 格式校验 ----------
ok, msg = lic.activate("!!", url=MOCK_URL)
check("非法格式本地拒绝", ok is False and "格式" in msg)

# ---------- 正确卡密（小写+连字符输入，验证 normalize） ----------
ok, msg = lic.activate(" nlk-aaaa-bbbb-cccc ", url=MOCK_URL)
check("正确卡密激活成功", ok is True and "成功" in msg)
check("激活后本地放行", lic.is_activated() is True)
check("license.json 已写入数据目录",
      os.path.exists(os.path.join(_tmp, "license.json")))

# ---------- 同一卡密二次核销（服务端一次性） ----------
ok, msg = lic.activate(VALID_KEY, url=MOCK_URL)
check("同一卡密二次激活被拒（一次性）", ok is False and "已被使用" in msg)

# ---------- 机器指纹篡改（模拟拷贝到别的电脑） ----------
lic_path = os.path.join(_tmp, "license.json")
with open(lic_path, encoding="utf-8") as f:
    data = json.load(f)
data["machine"] = "0" * 32
with open(lic_path, "w", encoding="utf-8") as f:
    json.dump(data, f)
check("指纹不匹配则要求重新激活", lic.is_activated() is False)

# ---------- 断网 ----------
ok, msg = lic.activate(VALID_KEY, url="http://127.0.0.1:1/api/activate", timeout=2)
check("服务器不可达时给出网络提示", ok is False and "网络" in msg)

server.shutdown()
print()
print(f"全部通过：{sum(RESULTS)}/{len(RESULTS)} 项")
