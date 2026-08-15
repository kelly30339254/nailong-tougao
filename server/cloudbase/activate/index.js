// 卡密激活云函数（腾讯云开发 CloudBase，Node.js 运行时）
//
// 请求：POST JSON {"card_key": "NLK-XXXX-XXXX-XXXX", "machine_id": "..."}
// 响应：JSON {"ok": true/false, "msg": "..."}
//
// 卡密存放在云数据库 cardkeys 集合，记录结构：
//   {key: string, used: boolean, machine_id: string, activated_at: string}
// 核销用「where key 且未使用 → update 置为已用」的条件更新，天然防并发重复核销。
const cloud = require("@cloudbase/node-sdk");

const app = cloud.init({ env: cloud.SYMBOL_CURRENT_ENV });
const db = app.database();
const COLL = "cardkeys";

function resp(statusCode, obj) {
  return {
    statusCode,
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(obj),
  };
}

exports.main = async (event) => {
  let payload;
  try {
    const body = typeof event.body === "string"
      ? event.body
      : JSON.stringify(event.body || {});
    payload = JSON.parse(body);
  } catch (e) {
    return resp(400, { ok: false, msg: "请求格式错误" });
  }

  // 规范化：去掉空格/连字符并转大写，与客户端 normalize_key 行为一致
  const cardKey = String(payload.card_key || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  const machineId = String(payload.machine_id || "").trim();
  if (!cardKey || !machineId) {
    return resp(400, { ok: false, msg: "参数缺失" });
  }

  // 原子核销：仅当卡密存在且未使用时更新成功
  let updated = 0;
  try {
    const res = await db.collection(COLL)
      .where({ key: cardKey, used: false })
      .update({
        used: true,
        machine_id: machineId,
        activated_at: new Date().toISOString(),
      });
    updated = res.updated || 0;
  } catch (e) {
    console.error("activate update failed:", e);
    return resp(500, { ok: false, msg: "服务器错误，请稍后重试" });
  }
  if (updated > 0) {
    return resp(200, { ok: true, msg: "激活成功" });
  }

  // 区分「卡密不存在」和「已被使用」
  try {
    const q = await db.collection(COLL).where({ key: cardKey }).get();
    if (!q.data || q.data.length === 0) {
      return resp(200, { ok: false, msg: "卡密不存在，请核对后重试" });
    }
  } catch (e) {
    console.error("activate query failed:", e);
    return resp(500, { ok: false, msg: "服务器错误，请稍后重试" });
  }
  return resp(200, { ok: false, msg: "该卡密已被使用，一张卡密只能激活一次" });
};
