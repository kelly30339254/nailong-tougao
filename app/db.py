"""SQLite 数据层：连接、建表、CRUD、工作台统计、内置编辑播种。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from datetime import datetime, date, timedelta

from .models import Editor, Manuscript, Submission, Reply, Sale

SCHEMA = """
CREATE TABLE IF NOT EXISTS editors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    platform TEXT DEFAULT '',
    email TEXT DEFAULT '',
    genres TEXT DEFAULT '',
    directions TEXT DEFAULT '',
    status TEXT DEFAULT '',
    fee_info TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    favorite INTEGER DEFAULT 0,
    blacklisted INTEGER DEFAULT 0,
    created_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS manuscripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    file_path TEXT DEFAULT '',
    word_count INTEGER DEFAULT 0,
    category TEXT DEFAULT '',
    reader_group TEXT DEFAULT '',
    emotion TEXT DEFAULT '',
    style TEXT DEFAULT '',
    genre_type TEXT DEFAULT '',
    created_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    manuscript_id INTEGER,
    editor_id INTEGER,
    from_mailbox TEXT DEFAULT '',
    to_email TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    body TEXT DEFAULT '',
    status TEXT DEFAULT '待发',
    reply_status TEXT DEFAULT '无',
    sent_at TEXT DEFAULT '',
    replied_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER,
    from_email TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    snippet TEXT DEFAULT '',
    verdict TEXT DEFAULT '其他',
    is_read INTEGER DEFAULT 0,
    imap_uid TEXT DEFAULT '',
    received_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);
"""


def data_dir() -> str:
    """数据目录，可用环境变量 NAILONG_DATA_DIR 覆盖（测试用）。"""
    override = os.environ.get("NAILONG_DATA_DIR")
    if override:
        return override
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "NailongPost")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Database:
    def __init__(self, db_path: str | None = None):
        self._dir = data_dir()
        os.makedirs(self._dir, exist_ok=True)
        os.makedirs(os.path.join(self._dir, "files"), exist_ok=True)
        if db_path is None:
            db_path = os.path.join(self._dir, "nailong.db")
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)
            self._migrate()

    def _migrate(self):
        """老库平滑升级：缺列则 ALTER TABLE 补列；一次性数据迁移带 settings 标记。"""
        editors_cols = {r["name"] for r in
                        self._conn.execute("PRAGMA table_info(editors)").fetchall()}
        if "email_invalid" not in editors_cols:
            self._conn.execute(
                "ALTER TABLE editors ADD COLUMN email_invalid INTEGER DEFAULT 0")
        if "directions" not in editors_cols:
            self._conn.execute(
                "ALTER TABLE editors ADD COLUMN directions TEXT DEFAULT ''")
        if "status" not in editors_cols:
            self._conn.execute(
                "ALTER TABLE editors ADD COLUMN status TEXT DEFAULT ''")
        subs_cols = {r["name"] for r in
                     self._conn.execute("PRAGMA table_info(submissions)").fetchall()}
        if "scheduled_at" not in subs_cols:
            self._conn.execute(
                "ALTER TABLE submissions ADD COLUMN scheduled_at TEXT DEFAULT ''")
        # 稿费记录表
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manuscript_id INTEGER,
                platform TEXT DEFAULT '',
                editor_name TEXT DEFAULT '',
                amount REAL,
                sale_date TEXT DEFAULT '',
                payment_month TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT ''
            )""")
        # 一次性：清空内置数据的来源链接（来源列保留，有值才显示链接）
        marker = self._conn.execute(
            "SELECT value FROM settings WHERE key='source_cleared_v1'").fetchone()
        if not marker:
            self._conn.execute("UPDATE editors SET source_url=''")
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES('source_cleared_v1', '1')"
                " ON CONFLICT(key) DO UPDATE SET value='1'")

    @property
    def files_dir(self) -> str:
        return os.path.join(self._dir, "files")

    def close(self):
        with self._lock:
            self._conn.close()

    # ---------- 内置编辑播种 ----------
    def seed_builtin_editors(self, json_path: str) -> tuple[int, int]:
        """导入内置编辑数据，返回 (inserted, skipped)。

        - 已有 builtin_seeded 标记时返回 (0, 0)（先 clear_seed_marker 才会再播）
        - 按 email 去重：editors 表已有该 email 则跳过
        """
        with self._lock:
            r = self._conn.execute(
                "SELECT value FROM settings WHERE key='builtin_seeded'").fetchone()
            if r:
                return (0, 0)
            if not os.path.exists(json_path):
                return (0, 0)
            with open(json_path, encoding="utf-8") as f:
                items = json.load(f)
            seen = {row["email"].lower() for row in self._conn.execute(
                "SELECT email FROM editors WHERE email != ''").fetchall()}
            rows = []
            skipped = 0
            for d in items:
                email = (d.get("email") or "").strip()
                if not email:
                    continue
                if email.lower() in seen:
                    skipped += 1
                    continue
                seen.add(email.lower())
                rows.append(
                    (d.get("name", ""), d.get("platform", ""), email,
                     d.get("genres", ""), d.get("directions", ""), d.get("status", ""),
                     d.get("fee_info", ""), d.get("source_url", ""),
                     d.get("notes", ""), int(d.get("favorite", 0)),
                     int(d.get("blacklisted", 0)), d.get("created_at", "")))
            with self._conn:
                self._conn.executemany(
                    "INSERT INTO editors(name,platform,email,genres,directions,status,"
                    "fee_info,source_url,notes,favorite,blacklisted,created_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows)
                self._conn.execute(
                    "INSERT INTO settings(key, value) VALUES('builtin_seeded', '1')"
                    " ON CONFLICT(key) DO UPDATE SET value='1'")
            return (len(rows), skipped)

    def clear_seed_marker(self):
        """清除播种标记，使 seed_builtin_editors 可以再次执行。"""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM settings WHERE key='builtin_seeded'")

    # ---------- editors ----------
    def list_editors(self, keyword: str | None = None, platform: str | None = None,
                     genre: str | None = None, direction: str | None = None,
                     favorites_only: bool = False,
                     include_blacklisted: bool = False) -> list[Editor]:
        sql = "SELECT * FROM editors WHERE 1=1"
        args: list = []
        if keyword:
            sql += " AND (name LIKE ? OR email LIKE ? OR genres LIKE ? OR directions LIKE ?)"
            like = f"%{keyword}%"
            args += [like, like, like, like]
        if platform:
            sql += " AND platform = ?"
            args.append(platform)
        if genre:
            sql += " AND genres LIKE ?"
            args.append(f"%{genre}%")
        if direction:
            sql += " AND directions LIKE ?"
            args.append(f"%{direction}%")
        if favorites_only:
            sql += " AND favorite = 1"
        if not include_blacklisted:
            sql += " AND blacklisted = 0"
        sql += " ORDER BY favorite DESC, id DESC"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [self._row_to_editor(r) for r in rows]

    @staticmethod
    def _row_to_editor(r: sqlite3.Row) -> Editor:
        return Editor(id=r["id"], name=r["name"], platform=r["platform"],
                      email=r["email"], genres=r["genres"], fee_info=r["fee_info"],
                      source_url=r["source_url"], notes=r["notes"],
                      directions=r["directions"] if "directions" in r.keys() else "",
                      status=r["status"] if "status" in r.keys() else "",
                      favorite=bool(r["favorite"]), blacklisted=bool(r["blacklisted"]),
                      email_invalid=bool(r["email_invalid"]),
                      created_at=r["created_at"])

    def get_editor(self, editor_id: int) -> Editor | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM editors WHERE id=?", (editor_id,)).fetchone()
        return self._row_to_editor(r) if r else None

    def insert_editor(self, e: Editor) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO editors(name,platform,email,genres,directions,status,fee_info,source_url,notes,favorite,blacklisted,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (e.name, e.platform, e.email, e.genres, e.directions, e.status,
                 e.fee_info, e.source_url, e.notes, int(e.favorite),
                 int(e.blacklisted), e.created_at or _now()))
            return cur.lastrowid

    def update_editor(self, e: Editor):
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE editors SET name=?,platform=?,email=?,genres=?,directions=?,status=?,"
                "fee_info=?,source_url=?,notes=?,favorite=?,blacklisted=? WHERE id=?",
                (e.name, e.platform, e.email, e.genres, e.directions, e.status,
                 e.fee_info, e.source_url, e.notes, int(e.favorite),
                 int(e.blacklisted), e.id))

    def delete_editor(self, editor_id: int):
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM editors WHERE id=?", (editor_id,))

    def toggle_favorite(self, editor_id: int) -> bool:
        with self._lock, self._conn:
            self._conn.execute("UPDATE editors SET favorite = 1 - favorite WHERE id=?", (editor_id,))
            r = self._conn.execute("SELECT favorite FROM editors WHERE id=?", (editor_id,)).fetchone()
            return bool(r["favorite"]) if r else False

    def toggle_blacklisted(self, editor_id: int) -> bool:
        with self._lock, self._conn:
            self._conn.execute("UPDATE editors SET blacklisted = 1 - blacklisted WHERE id=?", (editor_id,))
            r = self._conn.execute("SELECT blacklisted FROM editors WHERE id=?", (editor_id,)).fetchone()
            return bool(r["blacklisted"]) if r else False

    def distinct_platforms(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT platform FROM editors WHERE platform != '' ORDER BY platform").fetchall()
        return [r["platform"] for r in rows]

    def distinct_genres(self) -> list[str]:
        """genres 以 / 、，,空格 分隔存储，拆分后去重。"""
        with self._lock:
            rows = self._conn.execute("SELECT DISTINCT genres FROM editors WHERE genres != ''").fetchall()
        result: list[str] = []
        seen = set()
        for r in rows:
            for part in r["genres"].replace("，", "/").replace(",", "/").replace("、", "/").replace(" ", "/").split("/"):
                part = part.strip()
                if part and part not in seen:
                    seen.add(part)
                    result.append(part)
        return sorted(result)

    def distinct_directions(self) -> list[str]:
        """收稿方向（directions）拆分去重，与 genres 相同的分隔约定。"""
        with self._lock:
            rows = self._conn.execute("SELECT DISTINCT directions FROM editors WHERE directions != ''").fetchall()
        result: list[str] = []
        seen = set()
        for r in rows:
            for part in r["directions"].replace("，", "/").replace(",", "/").replace("、", "/").replace(" ", "/").split("/"):
                part = part.strip()
                if part and part not in seen:
                    seen.add(part)
                    result.append(part)
        return sorted(result)

    # ---------- manuscripts ----------
    @staticmethod
    def _row_to_manuscript(r: sqlite3.Row) -> Manuscript:
        return Manuscript(id=r["id"], title=r["title"], file_path=r["file_path"],
                          word_count=r["word_count"], category=r["category"],
                          reader_group=r["reader_group"], emotion=r["emotion"],
                          style=r["style"], genre_type=r["genre_type"],
                          created_at=r["created_at"])

    def list_manuscripts(self) -> list[Manuscript]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM manuscripts ORDER BY id DESC").fetchall()
        return [self._row_to_manuscript(r) for r in rows]

    def get_manuscript(self, manuscript_id: int) -> Manuscript | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM manuscripts WHERE id=?", (manuscript_id,)).fetchone()
        return self._row_to_manuscript(r) if r else None

    def insert_manuscript(self, m: Manuscript) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO manuscripts(title,file_path,word_count,category,reader_group,emotion,style,genre_type,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (m.title, m.file_path, m.word_count, m.category, m.reader_group,
                 m.emotion, m.style, m.genre_type, m.created_at or _now()))
            return cur.lastrowid

    def update_manuscript(self, m: Manuscript):
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE manuscripts SET title=?,file_path=?,word_count=?,category=?,reader_group=?,"
                "emotion=?,style=?,genre_type=? WHERE id=?",
                (m.title, m.file_path, m.word_count, m.category, m.reader_group,
                 m.emotion, m.style, m.genre_type, m.id))

    def delete_manuscript(self, manuscript_id: int):
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM manuscripts WHERE id=?", (manuscript_id,))
            # 连带删除其售出记录
            self._conn.execute("DELETE FROM sales WHERE manuscript_id=?", (manuscript_id,))

    # ---------- sales（稿费记录） ----------
    @staticmethod
    def _row_to_sale(r: sqlite3.Row) -> Sale:
        return Sale(id=r["id"], manuscript_id=r["manuscript_id"], platform=r["platform"],
                    editor_name=r["editor_name"], amount=r["amount"],
                    sale_date=r["sale_date"], payment_month=r["payment_month"],
                    notes=r["notes"], created_at=r["created_at"],
                    manuscript_title=r["mtitle"] if "mtitle" in r.keys() else "")

    def list_sales(self) -> list[Sale]:
        """联表带文稿标题，按 id 倒序（最新在前）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.*, m.title AS mtitle FROM sales s"
                " LEFT JOIN manuscripts m ON m.id = s.manuscript_id"
                " ORDER BY s.id DESC").fetchall()
        return [self._row_to_sale(r) for r in rows]

    def insert_sale(self, s: Sale) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO sales(manuscript_id,platform,editor_name,amount,sale_date,"
                "payment_month,notes,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (s.manuscript_id, s.platform, s.editor_name, s.amount,
                 s.sale_date, s.payment_month, s.notes, s.created_at or _now()))
            return cur.lastrowid

    def update_sale(self, s: Sale):
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sales SET manuscript_id=?,platform=?,editor_name=?,amount=?,"
                "sale_date=?,payment_month=?,notes=? WHERE id=?",
                (s.manuscript_id, s.platform, s.editor_name, s.amount,
                 s.sale_date, s.payment_month, s.notes, s.id))

    def delete_sale(self, sale_id: int):
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM sales WHERE id=?", (sale_id,))

    def sales_for_manuscript(self, manuscript_id: int) -> list[Sale]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.*, m.title AS mtitle FROM sales s"
                " LEFT JOIN manuscripts m ON m.id = s.manuscript_id"
                " WHERE s.manuscript_id=? ORDER BY s.id DESC", (manuscript_id,)).fetchall()
        return [self._row_to_sale(r) for r in rows]

    def sales_summary(self) -> tuple[int, float]:
        """返回 (售出篇数, 金额合计)。"""
        with self._lock:
            r = self._conn.execute(
                "SELECT COUNT(*) AS c, COALESCE(SUM(amount), 0) AS total FROM sales").fetchone()
        return r["c"], r["total"]

    # ---------- submissions ----------
    @staticmethod
    def _row_to_submission(r: sqlite3.Row) -> Submission:
        return Submission(id=r["id"], manuscript_id=r["manuscript_id"], editor_id=r["editor_id"],
                          from_mailbox=r["from_mailbox"], to_email=r["to_email"],
                          subject=r["subject"], body=r["body"], status=r["status"],
                          reply_status=r["reply_status"], sent_at=r["sent_at"],
                          replied_at=r["replied_at"], scheduled_at=r["scheduled_at"])

    def insert_submission(self, s: Submission) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO submissions(manuscript_id,editor_id,from_mailbox,to_email,subject,body,"
                "status,reply_status,sent_at,replied_at,scheduled_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (s.manuscript_id, s.editor_id, s.from_mailbox, s.to_email, s.subject, s.body,
                 s.status, s.reply_status, s.sent_at, s.replied_at, s.scheduled_at))
            return cur.lastrowid

    def update_status(self, submission_id: int, status: str, sent_at: str | None = None):
        with self._lock, self._conn:
            if sent_at is None and status == "已发":
                sent_at = _now()
            if sent_at is not None:
                self._conn.execute("UPDATE submissions SET status=?, sent_at=? WHERE id=?",
                                   (status, sent_at, submission_id))
            else:
                self._conn.execute("UPDATE submissions SET status=? WHERE id=?",
                                   (status, submission_id))

    def update_reply_status(self, submission_id: int, reply_status: str):
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE submissions SET reply_status=?, replied_at=? WHERE id=?",
                (reply_status, _now(), submission_id))

    def update_from_mailbox(self, submission_id: int, mailbox_address: str):
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE submissions SET from_mailbox=? WHERE id=?",
                (mailbox_address, submission_id))

    def delete_submission(self, submission_id: int):
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM submissions WHERE id=?", (submission_id,))

    # ---------- 邮箱失效标记（退信） ----------
    def mark_email_invalid(self, email: str) -> int:
        """按邮箱地址置 email_invalid=1，返回影响行数。"""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE editors SET email_invalid=1 WHERE lower(email)=lower(?)",
                (email.strip(),))
            return cur.rowcount

    def clear_email_invalid(self, editor_id: int):
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE editors SET email_invalid=0 WHERE id=?", (editor_id,))

    # ---------- 云端同步 ----------
    def sync_editors(self, items: list[dict]) -> dict:
        """将云端编辑数据合并到本地。

        合并策略（幂等，可重复执行）：
        - 按 email 匹配：已存在则更新 directions/status/genres/fee_info/notes；
          不存在则新增。已收藏/小黑屋/退信标记保留。
        返回 {"inserted": n, "updated": m, "total": 本地总数}
        """
        inserted = updated = 0
        with self._lock, self._conn:
            existing = {r["email"].lower(): r["id"]
                        for r in self._conn.execute(
                            "SELECT id, email FROM editors WHERE email != ''").fetchall()}
            for d in items:
                email = (d.get("email") or "").strip()
                if not email:
                    continue
                key = email.lower()
                if key in existing:
                    eid = existing[key]
                    self._conn.execute(
                        "UPDATE editors SET name=?,platform=?,genres=?,directions=?,"
                        "status=?,fee_info=?,notes=?,source_url=? WHERE id=?",
                        (d.get("name", ""), d.get("platform", ""),
                         d.get("genres", ""), d.get("directions", ""),
                         d.get("status", ""), d.get("fee_info", ""),
                         d.get("notes", ""), d.get("source_url", ""), eid))
                    updated += 1
                else:
                    self._conn.execute(
                        "INSERT INTO editors(name,platform,email,genres,directions,status,"
                        "fee_info,source_url,notes,blacklisted,created_at)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (d.get("name", ""), d.get("platform", ""), email,
                         d.get("genres", ""), d.get("directions", ""),
                         d.get("status", ""), d.get("fee_info", ""),
                         d.get("source_url", ""), d.get("notes", ""),
                         int(d.get("status") == "停止收稿"), _now()))
                    existing[key] = -1  # 防止重复 key 重复插入
                    inserted += 1
        return {"inserted": inserted, "updated": updated,
                "total": self.counts()["编辑总数"]}

    # ---------- 催稿提醒 ----------
    def stale_submissions(self, days: int) -> list[Submission]:
        """已发超过 days 天且 reply_status=无 的记录。"""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM submissions WHERE status='已发' AND reply_status='无'"
                " AND sent_at != '' AND sent_at < ? ORDER BY sent_at",
                (cutoff,)).fetchall()
        return [self._row_to_submission(r) for r in rows]

    # ---------- 定时投稿 ----------
    def due_scheduled(self, now_str: str | None = None) -> list[Submission]:
        """到点的定时待发记录。"""
        now_str = now_str or _now()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM submissions WHERE status='定时待发'"
                " AND scheduled_at != '' AND scheduled_at <= ? ORDER BY scheduled_at",
                (now_str,)).fetchall()
        return [self._row_to_submission(r) for r in rows]

    # ---------- 数据统计 ----------
    def platform_stats(self) -> list[dict]:
        """各平台投递表现：平台/投递/回复/过稿，按投递数倒序。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT COALESCE(NULLIF(e.platform, ''), '未知平台') AS platform,"
                " COUNT(*) AS total,"
                " SUM(CASE WHEN s.reply_status != '无' THEN 1 ELSE 0 END) AS replied,"
                " SUM(CASE WHEN s.reply_status = '过稿' THEN 1 ELSE 0 END) AS passed"
                " FROM submissions s LEFT JOIN editors e ON e.id = s.editor_id"
                " WHERE s.status = '已发'"
                " GROUP BY platform ORDER BY total DESC").fetchall()
        return [{"platform": r["platform"], "total": r["total"],
                 "replied": r["replied"], "passed": r["passed"]} for r in rows]

    def avg_reply_days(self) -> float | None:
        """平均回复时长（天，sent_at → replied_at），无数据返回 None。"""
        with self._lock:
            r = self._conn.execute(
                "SELECT AVG(julianday(replied_at) - julianday(sent_at)) AS d"
                " FROM submissions WHERE sent_at != '' AND replied_at != ''").fetchone()
        return r["d"] if r and r["d"] is not None else None

    # ---------- 数据备份 ----------
    def db_file_size(self) -> int:
        try:
            return os.path.getsize(self.db_path)
        except OSError:
            return 0

    def backup_to(self, dest_path: str):
        """用 sqlite3 backup API 导出到目标路径。"""
        with self._lock:
            dest = sqlite3.connect(dest_path)
            try:
                self._conn.backup(dest)
            finally:
                dest.close()

    def restore_from(self, src_path: str):
        """用 sqlite3 backup API 从源文件覆盖当前库（需重启生效）。"""
        with self._lock:
            src = sqlite3.connect(src_path)
            try:
                src.backup(self._conn)
            finally:
                src.close()

    def clear_business_data(self):
        """清空文稿、投递记录、回信 3 张业务表；保留编辑列表与设置。"""
        with self._lock, self._conn:
            for table in ("manuscripts", "submissions", "replies"):
                self._conn.execute(f"DELETE FROM {table}")

    def list_submissions(self, status_filter: str | None = None) -> list[Submission]:
        sql = "SELECT * FROM submissions"
        args: list = []
        if status_filter:
            sql += " WHERE status = ?"
            args.append(status_filter)
        sql += " ORDER BY id DESC"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [self._row_to_submission(r) for r in rows]

    def find_pending(self, manuscript_id: int, editor_id: int) -> Submission | None:
        """一稿一投判定：同稿件同编辑存在 已发且 reply_status=无 的记录。"""
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM submissions WHERE manuscript_id=? AND editor_id=?"
                " AND status='已发' AND reply_status='无' ORDER BY id DESC LIMIT 1",
                (manuscript_id, editor_id)).fetchone()
        return self._row_to_submission(r) if r else None

    def count_editor_last_days(self, editor_id: int, days: int = 7) -> int:
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            r = self._conn.execute(
                "SELECT COUNT(*) AS c FROM submissions WHERE editor_id=? AND status='已发' AND sent_at >= ?",
                (editor_id, since)).fetchone()
        return r["c"]

    def count_today(self, mailbox_name: str) -> int:
        """该邮箱今日已发数。"""
        today = date.today().strftime("%Y-%m-%d")
        with self._lock:
            r = self._conn.execute(
                "SELECT COUNT(*) AS c FROM submissions WHERE from_mailbox=? AND status='已发' AND sent_at LIKE ?",
                (mailbox_name, today + "%")).fetchone()
        return r["c"]

    # ---------- replies ----------
    @staticmethod
    def _row_to_reply(r: sqlite3.Row) -> Reply:
        return Reply(id=r["id"], submission_id=r["submission_id"], from_email=r["from_email"],
                     subject=r["subject"], snippet=r["snippet"], verdict=r["verdict"],
                     is_read=bool(r["is_read"]), imap_uid=r["imap_uid"],
                     received_at=r["received_at"])

    def insert_reply(self, r: Reply) -> int | None:
        """按 imap_uid + from_email 去重，重复返回 None。"""
        with self._lock, self._conn:
            if r.imap_uid:
                dup = self._conn.execute(
                    "SELECT id FROM replies WHERE imap_uid=? AND from_email=?",
                    (r.imap_uid, r.from_email)).fetchone()
                if dup:
                    return None
            cur = self._conn.execute(
                "INSERT INTO replies(submission_id,from_email,subject,snippet,verdict,is_read,imap_uid,received_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (r.submission_id, r.from_email, r.subject, r.snippet, r.verdict,
                 int(r.is_read), r.imap_uid, r.received_at or _now()))
            return cur.lastrowid

    def list_replies(self, unread_only: bool = False) -> list[Reply]:
        sql = "SELECT * FROM replies"
        if unread_only:
            sql += " WHERE is_read = 0"
        sql += " ORDER BY id DESC"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [self._row_to_reply(r) for r in rows]

    def mark_read(self, reply_id: int):
        with self._lock, self._conn:
            self._conn.execute("UPDATE replies SET is_read=1 WHERE id=?", (reply_id,))

    def unread_count(self) -> int:
        with self._lock:
            r = self._conn.execute("SELECT COUNT(*) AS c FROM replies WHERE is_read=0").fetchone()
        return r["c"]

    # ---------- 工作台统计 ----------
    def counts(self) -> dict:
        with self._lock:
            editors = self._conn.execute("SELECT COUNT(*) AS c FROM editors").fetchone()["c"]
            manuscripts = self._conn.execute("SELECT COUNT(*) AS c FROM manuscripts").fetchone()["c"]
            pending = self._conn.execute(
                "SELECT COUNT(*) AS c FROM submissions WHERE status='已发' AND reply_status='无'").fetchone()["c"]
            passed = self._conn.execute(
                "SELECT COUNT(*) AS c FROM submissions WHERE reply_status='过稿'").fetchone()["c"]
            rejected = self._conn.execute(
                "SELECT COUNT(*) AS c FROM submissions WHERE reply_status='退稿'").fetchone()["c"]
            unread = self._conn.execute("SELECT COUNT(*) AS c FROM replies WHERE is_read=0").fetchone()["c"]
        return {"编辑总数": editors, "文稿数": manuscripts, "待回复": pending,
                "过稿": passed, "退稿": rejected, "未读回信": unread}

    def recent_activity(self, limit: int = 10) -> list[dict]:
        """最近投稿 + 回信混合，按时间倒序。每项 {kind, text, time}。"""
        items: list[dict] = []
        with self._lock:
            subs = self._conn.execute(
                "SELECT s.id, s.status, s.sent_at, m.title AS mtitle, e.name AS ename"
                " FROM submissions s"
                " LEFT JOIN manuscripts m ON m.id = s.manuscript_id"
                " LEFT JOIN editors e ON e.id = s.editor_id"
                " ORDER BY s.id DESC LIMIT ?", (limit,)).fetchall()
            reps = self._conn.execute(
                "SELECT r.from_email, r.verdict, r.received_at, r.subject"
                " FROM replies r ORDER BY r.id DESC LIMIT ?", (limit,)).fetchall()
            sales = self._conn.execute(
                "SELECT s.platform, s.amount, s.created_at, m.title AS mtitle"
                " FROM sales s LEFT JOIN manuscripts m ON m.id = s.manuscript_id"
                " ORDER BY s.id DESC LIMIT ?", (limit,)).fetchall()
        for s in subs:
            items.append({
                "kind": "投稿",
                "text": f"向 {s['ename'] or '未知编辑'} 投递《{s['mtitle'] or '未知文稿'}》（{s['status']}）",
                "time": s["sent_at"] or "",
            })
        for r in reps:
            items.append({
                "kind": "回信",
                "text": f"收到 {r['from_email']} 的回信（{r['verdict']}）：{r['subject']}",
                "time": r["received_at"] or "",
            })
        for s in sales:
            amount_text = f"（{s['amount']:g} 元）" if s["amount"] is not None else ""
            items.append({
                "kind": "售出",
                "text": f"售出《{s['mtitle'] or '未知文稿'}》→ {s['platform'] or '未知平台'}{amount_text}",
                "time": s["created_at"] or "",
            })
        items.sort(key=lambda x: x["time"], reverse=True)
        return items[:limit]
