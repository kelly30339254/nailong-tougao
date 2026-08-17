// 账号鉴权云函数（腾讯云开发 CloudBase，Node.js 运行时）
//
// 统一入口：POST /api/auth，event.body 为 JSON {action, ...}
// 响应：JSON {ok: true/false, msg, token?, email?, card_bound?, ...}
//
// 架构说明：
//   本软件是纯 Python 桌面应用，无法使用 @cloudbase/js-sdk（Node 专用），
//   因此效仿 activate 云函数模式，把账号体系全部封装在 HTTP 云函数内。
//   - 邮箱验证码的「发送 + 校验」走 CloudBase Auth 的 HTTP API（满足“通过
//     CloudBase 发送验证码”），上海地域 ap-shanghai 已支持。
//   - 账号(users)、会话(sessions，单点登录)、卡密绑定(cardkeys) 完全自建，
//     密码用 bcryptjs 加盐哈希，会话用强随机 token + active 标志实现“新登录踢旧”。
//
// action 列表：
//   send_code   发送邮箱验证码  {email, target: "register"|"login"|"reset"}
//   register    注册           {email, password, verification_id, code}
//   login       密码登录        {email, password}
//   reset_password 找回密码     {email, new_password, verification_id, code}
//   validate    校验会话        {token}
//   logout      退出登录        {token}
//   bind_card   绑定卡密        {token, card_key}
//   status      账号卡密状态    {token}
const cloud = require("@cloudbase/node-sdk");

const app = cloud.init({ env: cloud.SYMBOL_CURRENT_ENV });
const db = app.database();
// SCF 运行时 getCurrentEnvId 可能返回空，需要多级 fallback 保证构造出正确的 Auth HTTP 域名
const ENV_ID =
  (app.getCurrentEnvId ? app.getCurrentEnvId() : "") ||
  process.env.TCB_ENV ||
  (app.config && app.config.env) ||
  "";
// CloudBase Auth HTTP 开放接口的域名格式为 https://{envId}.api.tcloudbasegateway.com
const AUTH_BASE = `https://${ENV_ID || ""}.api.tcloudbasegateway.com`;

const USERS = "users";
const SESSIONS = "sessions";
const CARDKEYS = "cardkeys";

// 密码规则：8~32 位，须含字母和数字
const PASSWORD_RE = /^(?=.*[A-Za-z])(?=.*\d)[A-Za-z\d@$!%*#?&_-]{8,32}$/;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
// 验证码有效期（秒）
const CODE_TTL = 600;

function resp(statusCode, obj) {
  return {
    statusCode,
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(obj),
  };
}

function ok(obj) {
  return resp(200, Object.assign({ ok: true }, obj));
}
function fail(msg) {
  return resp(200, { ok: false, msg });
}

// ---------- 工具：CloudBase Auth 验证码 HTTP API ----------
function httpsPost(url, data) {
  const { URL } = require("url");
  const https = require("https");
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const body = Buffer.from(JSON.stringify(data), "utf-8");
    const req = https.request(
      {
        hostname: u.hostname,
        path: u.pathname,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": body.length,
          // 身份认证接口要求携带 x-device-id 标识客户端设备
          "x-device-id": "nailong-post-auth-server",
        },
      },
      (res) => {
        let raw = "";
        res.on("data", (c) => (raw += c));
        res.on("end", () => {
          try {
            resolve({ status: res.statusCode, data: JSON.parse(raw) });
          } catch (e) {
            resolve({ status: res.statusCode, data: { error: raw } });
          }
        });
      }
    );
    req.on("error", reject);
    req.write(body);
    req.end();
  });
}

// 发送验证码：target 为 CloudBase 语义（register/login -> ANY，reset -> USER）
async function sendCloudCode(email, cloudTarget) {
  return httpsPost(`${AUTH_BASE}/auth/v1/verification`, {
    target: cloudTarget,
    email,
  });
}

// 校验验证码：成功返回 verification_token（仅用于证明邮箱归属，不用于登录）
async function verifyCloudCode(email, verificationId, code) {
  return httpsPost(`${AUTH_BASE}/auth/v1/verification/verify`, {
    verification_id: verificationId,
    verification_code: String(code).trim(),
  });
}

function cloudTargetOf(target) {
  // reset 必须 target=USER（账号须已存在）；register/login 用 ANY
  return target === "reset" ? "USER" : "ANY";
}

// ---------- 工具：会话 / 用户 ----------
function genToken() {
  return require("crypto").randomBytes(24).toString("hex");
}

function sessionKey(token) {
  return { token };
}

// 校验会话 token，返回 {ok, uid, email}；被踢下线（active=false）视为失效
async function checkSession(token) {
  if (!token) return { ok: false, reason: "no_token" };
  const s = await db.collection(SESSIONS).where({ token }).get();
  const sess = s.data && s.data[0];
  if (!sess) return { ok: false, reason: "session_expired" };
  if (!sess.active) return { ok: false, reason: "kicked" };
  // 会话过期保护（90 天未活动）
  const last = new Date(sess.last_seen_at || sess.created_at || 0).getTime();
  if (Date.now() - last > 90 * 24 * 3600 * 1000) {
    await db.collection(SESSIONS).where({ token }).update({ active: false });
    return { ok: false, reason: "session_expired" };
  }
  // 续期
  await db.collection(SESSIONS).where({ token }).update({
    last_seen_at: new Date().toISOString(),
  });
  const u = await db.collection(USERS).where({ uid: sess.uid }).get();
  const user = u.data && u.data[0];
  if (!user) return { ok: false, reason: "session_expired" };
  return { ok: true, uid: user.uid, email: user.email };
}

// 单点登录：置该 uid 的其它 active 会话失效，再写新会话
async function createSession(uid) {
  await db.collection(SESSIONS).where({ uid, active: true }).update({
    active: false,
    kicked_at: new Date().toISOString(),
  });
  const token = genToken();
  await db.collection(SESSIONS).add({
    token,
    uid,
    active: true,
    created_at: new Date().toISOString(),
    last_seen_at: new Date().toISOString(),
  });
  return token;
}

// 查询账号是否已绑定卡密
async function cardBoundState(uid) {
  const r = await db
    .collection(CARDKEYS)
    .where({ bound_user: uid })
    .limit(1)
    .get();
  return !!(r.data && r.data.length > 0);
}

// ---------- action 处理 ----------
async function handleSendCode(payload) {
  const email = String(payload.email || "").toLowerCase().trim();
  if (!EMAIL_RE.test(email)) return fail("邮箱格式不正确");
  const target = payload.target === "reset" ? "reset" : "register";
  try {
    const r = await sendCloudCode(email, cloudTargetOf(target));
    console.log("send_code raw resp:", r.status, JSON.stringify(r.data));
    if (r.data && r.data.verification_id) {
      return ok({ verification_id: r.data.verification_id, expires_in: CODE_TTL });
    }
    const desc =
      (r.data && (r.data.error_description || r.data.error)) ||
      "验证码发送失败";
    return fail(mapCloudError(desc));
  } catch (e) {
    console.error("send_code failed:", e);
    return fail("验证码发送失败，请检查邮箱登录服务是否开启");
  }
}

// 校验验证码是否有效（供 register / reset_password 复用）
async function ensureCode(email, verificationId, code) {
  if (!verificationId) return { ok: false, msg: "请先获取验证码" };
  if (!code) return { ok: false, msg: "请输入验证码" };
  try {
    const r = await verifyCloudCode(email, verificationId, code);
    if (r.data && r.data.verification_token) return { ok: true };
    const desc =
      (r.data && (r.data.error_description || r.data.error)) || "验证码错误";
    return { ok: false, msg: mapCloudError(desc) };
  } catch (e) {
    console.error("verify code failed:", e);
    return { ok: false, msg: "验证码校验失败，请重试" };
  }
}

async function handleRegister(payload) {
  const email = String(payload.email || "").toLowerCase().trim();
  const password = String(payload.password || "");
  if (!EMAIL_RE.test(email)) return fail("邮箱格式不正确");
  if (!PASSWORD_RE.test(password))
    return fail("密码需 8~32 位，且同时包含字母和数字");

  const code = await ensureCode(email, payload.verification_id, payload.code);
  if (!code.ok) return fail(code.msg);

  // 查重
  const exist = await db.collection(USERS).where({ email }).get();
  if (exist.data && exist.data.length > 0) {
    return fail("该邮箱已注册，请直接登录");
  }

  const uid = genToken();
  const bcrypt = require("bcryptjs");
  const passwordHash = await bcrypt.hash(password, 10);
  await db.collection(USERS).add({
    uid,
    email,
    password_hash: passwordHash,
    created_at: new Date().toISOString(),
  });

  const token = await createSession(uid);
  const bound = await cardBoundState(uid);
  return ok({ token, email, card_bound: bound });
}

async function handleLogin(payload) {
  const email = String(payload.email || "").toLowerCase().trim();
  const password = String(payload.password || "");
  if (!email || !password) return fail("请输入邮箱和密码");

  const u = await db.collection(USERS).where({ email }).get();
  const user = u.data && u.data[0];
  if (!user) return fail("邮箱或密码不正确");
  const bcrypt = require("bcryptjs");
  const match = await bcrypt.compare(password, user.password_hash);
  if (!match) return fail("邮箱或密码不正确");

  const token = await createSession(user.uid);
  const bound = await cardBoundState(user.uid);
  return ok({ token, email: user.email, card_bound: bound });
}

async function handleResetPassword(payload) {
  const email = String(payload.email || "").toLowerCase().trim();
  const newPassword = String(payload.new_password || "");
  if (!EMAIL_RE.test(email)) return fail("邮箱格式不正确");
  if (!PASSWORD_RE.test(newPassword))
    return fail("密码需 8~32 位，且同时包含字母和数字");

  const u = await db.collection(USERS).where({ email }).get();
  const user = u.data && u.data[0];
  if (!user) return fail("该邮箱尚未注册");

  const code = await ensureCode(email, payload.verification_id, payload.code);
  if (!code.ok) return fail(code.msg);

  const bcrypt = require("bcryptjs");
  const passwordHash = await bcrypt.hash(newPassword, 10);
  await db.collection(USERS).where({ email }).update({
    password_hash: passwordHash,
    updated_at: new Date().toISOString(),
  });
  return ok({ msg: "密码已重置，请用新密码登录" });
}

async function handleValidate(payload) {
  const s = await checkSession(payload.token);
  if (!s.ok) {
    return fail(s.reason === "kicked" ? "账号已在其他设备登录" : "登录已失效，请重新登录");
  }
  const bound = await cardBoundState(s.uid);
  return ok({ email: s.email, card_bound: bound });
}

async function handleLogout(payload) {
  if (payload.token) {
    await db.collection(SESSIONS).where({ token: payload.token }).update({
      active: false,
      logged_out_at: new Date().toISOString(),
    });
  }
  return ok({ msg: "已退出登录" });
}

async function handleBindCard(payload) {
  const s = await checkSession(payload.token);
  if (!s.ok) {
    return fail(s.reason === "kicked" ? "账号已在其他设备登录" : "登录已失效，请重新登录");
  }

  const cardKey = String(payload.card_key || "")
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, "");
  if (!cardKey) return fail("请输入卡密");

  // 查询该卡密
  let card;
  try {
    const r = await db.collection(CARDKEYS).where({ key: cardKey }).get();
    card = r.data && r.data[0];
  } catch (e) {
    console.error("bind_card query failed:", e);
    return fail("服务器错误，请稍后重试");
  }
  if (!card) return fail("卡密不存在，请核对后重试");

  // 已绑定账号的卡密：仅允许同一账号复用（多设备）
  if (card.bound_user && card.bound_user !== s.uid) {
    return fail("该卡密已绑定其他账号");
  }

  // 未使用 -> 核销并绑定；老卡密(used=true 但 bound_user 为空) -> 首次绑定
  if (!card.used || !card.bound_user) {
    const upd = await db
      .collection(CARDKEYS)
      .where({ key: cardKey, used: false })
      .update({
        used: true,
        bound_user: s.uid,
        activated_at: new Date().toISOString(),
      });
    if (upd.updated > 0) {
      return ok({ card_bound: true, msg: "卡密绑定成功" });
    }
    // 若是老卡密（used 已 true），做首次绑定
    const upd2 = await db
      .collection(CARDKEYS)
      .where({ key: cardKey, used: true, bound_user: db.command.exists(false) })
      .update({
        bound_user: s.uid,
        bound_at: new Date().toISOString(),
      });
    if (upd2.updated > 0) {
      return ok({ card_bound: true, msg: "卡密绑定成功" });
    }
    return fail("该卡密已被使用，无法绑定");
  }

  // 已使用且 bound_user 已绑定当前账号（同一账号换设备复用）
  return ok({ card_bound: true, msg: "卡密绑定成功" });
}

async function handleStatus(payload) {
  const s = await checkSession(payload.token);
  if (!s.ok) {
    return fail(s.reason === "kicked" ? "账号已在其他设备登录" : "登录已失效，请重新登录");
  }
  const bound = await cardBoundState(s.uid);
  return ok({ email: s.email, card_bound: bound });
}

function mapCloudError(desc) {
  if (/rate_limit|频率/i.test(desc)) return "验证码发送过于频繁，请 60 秒后重试";
  if (/captcha/i.test(desc)) return "需要完成图形验证码，请稍后在软件中重试";
  if (/user_not_found|不存在/i.test(desc)) return "该邮箱尚未注册";
  return desc;
}

const HANDLERS = {
  send_code: handleSendCode,
  register: handleRegister,
  login: handleLogin,
  reset_password: handleResetPassword,
  validate: handleValidate,
  logout: handleLogout,
  bind_card: handleBindCard,
  status: handleStatus,
};

exports.main = async (event) => {
  let payload;
  try {
    const body =
      typeof event.body === "string" ? event.body : JSON.stringify(event.body || {});
    payload = JSON.parse(body);
  } catch (e) {
    return resp(400, { ok: false, msg: "请求格式错误" });
  }
  const action = String(payload.action || "");
  const handler = HANDLERS[action];
  if (!handler) return fail("未知操作");
  try {
    return await handler(payload);
  } catch (e) {
    console.error(`auth action ${action} failed:`, e);
    return resp(500, { ok: false, msg: "服务器错误，请稍后重试" });
  }
};
