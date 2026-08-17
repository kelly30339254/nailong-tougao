# 服务端说明（卡密激活 + 检测更新）

**状态：已完成部署（2026-08-13，通过 CloudBase CLI 部署）。**
本文档记录实际部署情况和日常维护命令。

## 实际架构

- 环境：`nailong-d4g922z6h6d9ff59e`（ap-shanghai，体验版）
- 客户端硬编码的地址（CloudBase 默认域名，不是主站域名）：
  - 账号接口：`…/api/auth`（登录 / 注册 / 绑卡 / 校验会话，云函数 `auth`）
  - 版本信息：`…/version.json`（静态托管根目录的 `server/version.json`）
- 激活体系（v1.3.0+）：邮箱账号登录 + 卡密绑定到账号，不再用机器指纹核销。
- 数据库：账号与卡密绑定记录在 CloudBase；卡密规范化形式仍为无连字符大写。
- 注意：主站 `nailong.zhiyuxiezuo.com` 走 EdgeOne，指向前端 SPA（任意路径都返回
  index.html），**不能**用来挂 API 或 version.json，除非以后在 EdgeOne 控制台加回源规则。

## 日常维护

以下命令都在 `server/cloudbase/` 目录下执行（CLI 命令：`npx -y -p @cloudbase/cli tcb ...`，
登录态在本机已保持）。注意 Git Bash 下凡是以 `/` 开头的参数要加 `MSYS_NO_PATHCONV=1`。

### 补充卡密

```bash
# 1. 项目根目录生成新卡密（数量自定）
.venv/Scripts/python.exe scripts/make_cardkeys.py 100 -o server/cardkeys-新批次.json

# 2. 导入发放台账（记录库存，防止重复发放）
.venv/Scripts/python.exe scripts/cardkey_ledger.py import server/cardkeys-新批次.json

# 3. 分批导入 CloudBase（每批 20 条，CLI 有命令长度限制），也可直接在 CloudBase
#    控制台 数据库 → cardkeys → 导入该 JSON 文件
```

### 卡密发放台账（防止弄混）

最简单的方式：**双击项目根目录的 `卡密台账.bat`**，菜单操作（取卡/查库存/标记/导入），不用敲命令。

命令行方式（cmd 窗口，注意用反斜杠）：

```bat
rem 买家买卡时：取 1 张库存卡密并标记（备注填买家微信/闲鱼号等）
.venv\Scripts\python.exe scripts\cardkey_ledger.py give 1 --note "买家微信xxx"
rem 查看库存和最近发放记录
.venv\Scripts\python.exe scripts\cardkey_ledger.py status
rem 手动标记某张卡密（如测试用掉）
.venv\Scripts\python.exe scripts\cardkey_ledger.py mark NLKXXXX... --note "原因"
```

台账只记「发没发给买家」；卡密是否已被激活以数据库 `used` 字段为准。

卡密文件（`cardkeys-*.json`）已加入 `.gitignore`，不要提交或外传——里面是未核销的卡密。

### 改激活接口代码

```bash
cd server/cloudbase
npx -y -p @cloudbase/cli tcb fn deploy activate --force
```

### 发版（每次更新软件）

1. 改 `app/__init__.py` 的 `APP_VERSION`（如 `1.3.6`）。
2. 运行 `build_installer.bat` 打包（会自动十进制升版，可用 `--no-bump` 保持当前号）。
3. 上传安装包到网盘 / GitHub Release。
4. **必须**同步 `server/version.json` 的 `version` / `notes` / `download_url`（可选 `github_url`）。打 `v*` 标签时 Actions 会产出 `version-json` artifact（已填 GitHub Release 直链），下载后核对再部署：
   ```bash
   cd server/cloudbase
   MSYS_NO_PATHCONV=1 npx -y -p @cloudbase/cli tcb hosting deploy ../version.json version.json
   ```
5. 在一台干净机器上打开旧版，确认能收到更新提示。不部署 version.json，用户永远看不到新版本。

### 查卡密使用情况

```bash
cd server/cloudbase
# 已使用的卡密
npx -y -p @cloudbase/cli tcb db nosql execute --json --command \
  '[{"TableName":"cardkeys","CommandType":"QUERY","Command":"{\"find\":\"cardkeys\",\"filter\":{\"used\":true},\"limit\":100}"}]'
# 总数
npx -y -p @cloudbase/cli tcb db nosql execute --json --command \
  '[{"TableName":"cardkeys","CommandType":"COMMAND","Command":"{\"count\":\"cardkeys\"}"}]'
```

### 给用户换机/补激活

用户换电脑后旧卡密无法复用。处理方式：在 CloudBase 控制台数据库里把该用户那条
卡密记录的 `used` 改回 `false`（或直接给他一张新卡密）。

## 目录内容

- `cloudbase/cloudbaserc.json` — CLI 部署配置（envId、函数运行时）
- `cloudbase/activate/index.js` — 激活云函数（查库 + 原子核销）
- `cloudbase/activate/package.json` — 函数依赖
- `version.json` — 版本信息（发版时改这里再上传）
