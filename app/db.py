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
    replied_at TEXT DEFAULT '',
    scheduled_at TEXT DEFAULT '',
    message_id TEXT DEFAULT ''
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
    mailbox_address TEXT DEFAULT '',
    imap_folder TEXT DEFAULT 'INBOX',
    uid_validity TEXT DEFAULT '',
    message_id TEXT DEFAULT '',
    in_reply_to TEXT DEFAULT '',
    reference_ids TEXT DEFAULT '',
    is_auto_reply INTEGER DEFAULT 0,
    classification_confidence TEXT DEFAULT '',
    classification_reason TEXT DEFAULT '',
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
            # WAL + busy_timeout：双实例/多线程并发写不再抛 database is locked
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=10000")
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
        if "message_id" not in subs_cols:
            self._conn.execute(
                "ALTER TABLE submissions ADD COLUMN message_id TEXT DEFAULT ''")
        if "last_error" not in subs_cols:
            self._conn.execute(
                "ALTER TABLE submissions ADD COLUMN last_error TEXT DEFAULT ''")
        if "last_urged_at" not in subs_cols:
            self._conn.execute(
                "ALTER TABLE submissions ADD COLUMN last_urged_at TEXT DEFAULT ''")
        replies_cols = {r["name"] for r in
                        self._conn.execute("PRAGMA table_info(replies)").fetchall()}
        reply_columns = (
            ("mailbox_address", "TEXT DEFAULT ''"),
            ("imap_folder", "TEXT DEFAULT 'INBOX'"),
            ("uid_validity", "TEXT DEFAULT ''"),
            ("message_id", "TEXT DEFAULT ''"),
            ("in_reply_to", "TEXT DEFAULT ''"),
            ("reference_ids", "TEXT DEFAULT ''"),
            ("is_auto_reply", "INTEGER DEFAULT 0"),
            ("classification_confidence", "TEXT DEFAULT ''"),
            ("classification_reason", "TEXT DEFAULT ''"),
            ("body_full", "TEXT DEFAULT ''"),
        )
        for name, definition in reply_columns:
            if name not in replies_cols:
                self._conn.execute(f"ALTER TABLE replies ADD COLUMN {name} {definition}")
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
                payment_date TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT ''
            )""")
        sales_cols = {r["name"] for r in
                      self._conn.execute("PRAGMA table_info(sales)").fetchall()}
        if "payment_date" not in sales_cols:
            self._conn.execute(
                "ALTER TABLE sales ADD COLUMN payment_date TEXT DEFAULT ''")
        # 旧版本曾错误清空全部来源链接；这里只保留迁移标记，不再修改用户数据。
        marker = self._conn.execute(
            "SELECT value FROM settings WHERE key='source_cleared_v1'").fetchone()
        if not marker:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES('source_cleared_v1', '1')"
                " ON CONFLICT(key) DO UPDATE SET value='1'")
        # 高频查询索引：投稿状态筛选/邮箱额度统计/回信关联去重
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_subs_editor ON submissions(editor_id)")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_subs_manuscript ON submissions(manuscript_id)")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_subs_status_reply ON submissions(status, reply_status)")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_subs_mailbox_sent ON submissions(from_mailbox, sent_at)")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_subs_message_id ON submissions(message_id)")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_replies_submission ON replies(submission_id)")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_replies_dedupe ON replies(mailbox_address, imap_folder, message_id)")

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
            # 级联清理该编辑的投递记录与回信，避免幽灵数据污染统计
            rows = self._conn.execute(
                "SELECT id FROM submissions WHERE editor_id=?", (editor_id,)).fetchall()
            if rows:
                ids = [r["id"] for r in rows]
                marks = ",".join("?" for _ in ids)
                self._conn.execute(f"DELETE FROM replies WHERE submission_id IN ({marks})", ids)
                self._conn.execute(f"DELETE FROM submissions WHERE id IN ({marks})", ids)

    def toggle_favorite(self, editor_id: int) -> bool:
        with self._lock, self._conn:
            self._conn.execute("UPDATE editors SET favorite = 1 - favorite WHERE id=?", (editor_id,))
            r = self._conn.execute("SELECT favorite FROM editors WHERE id=?", (editor_id,)).fetchone()
            return bool(r["favorite"]) if r else False

    def set_favorites(self, editor_ids: list[int], favorite: bool = True) -> int:
        """批量设置收藏。返回实际更新的行数。"""
        ids = [int(eid) for eid in editor_ids if eid]
        if not ids:
            return 0
        with self._lock, self._conn:
            marks = ",".join("?" for _ in ids)
            cur = self._conn.execute(
                f"UPDATE editors SET favorite=? WHERE id IN ({marks})",
                [int(favorite), *ids])
            return cur.rowcount

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

    def distinct_statuses(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT status FROM editors WHERE status != '' ORDER BY status").fetchall()
        return [r["status"] for r in rows]

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
            # 连带删除其售出记录与关联投递/回信，避免幽灵数据污染统计
            self._conn.execute("DELETE FROM sales WHERE manuscript_id=?", (manuscript_id,))
            rows = self._conn.execute(
                "SELECT id FROM submissions WHERE manuscript_id=?", (manuscript_id,)).fetchall()
            if rows:
                ids = [r["id"] for r in rows]
                marks = ",".join("?" for _ in ids)
                self._conn.execute(f"DELETE FROM replies WHERE submission_id IN ({marks})", ids)
                self._conn.execute(f"DELETE FROM submissions WHERE id IN ({marks})", ids)

    # ---------- sales（稿费记录） ----------
    @staticmethod
    def _row_to_sale(r: sqlite3.Row) -> Sale:
        return Sale(id=r["id"], manuscript_id=r["manuscript_id"], platform=r["platform"],
                    editor_name=r["editor_name"], amount=r["amount"],
                    sale_date=r["sale_date"], payment_month=r["payment_month"],
                    payment_date=r["payment_date"], notes=r["notes"], created_at=r["created_at"],
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
                "payment_month,payment_date,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (s.manuscript_id, s.platform, s.editor_name, s.amount,
                 s.sale_date, s.payment_month, s.payment_date, s.notes,
                 s.created_at or _now()))
            return cur.lastrowid

    def update_sale(self, s: Sale):
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sales SET manuscript_id=?,platform=?,editor_name=?,amount=?,"
                "sale_date=?,payment_month=?,payment_date=?,notes=? WHERE id=?",
                (s.manuscript_id, s.platform, s.editor_name, s.amount,
                 s.sale_date, s.payment_month, s.payment_date, s.notes, s.id))

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
                          replied_at=r["replied_at"], scheduled_at=r["scheduled_at"],
                          message_id=r["message_id"],
                          last_error=r["last_error"] if "last_error" in r.keys() else "")

    def insert_submission(self, s: Submission) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO submissions(manuscript_id,editor_id,from_mailbox,to_email,subject,body,"
                "status,reply_status,sent_at,replied_at,scheduled_at,message_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (s.manuscript_id, s.editor_id, s.from_mailbox, s.to_email, s.subject, s.body,
                 s.status, s.reply_status, s.sent_at, s.replied_at, s.scheduled_at,
                 s.message_id))
            return cur.lastrowid

    def insert_submission_if_allowed(self, s: Submission,
                                     protect: bool = True) -> int | None:
        """原子创建投稿；开启一稿一投时，存在活动记录则返回 None。"""
        columns = ("manuscript_id,editor_id,from_mailbox,to_email,subject,body,"
                   "status,reply_status,sent_at,replied_at,scheduled_at,message_id")
        values = (s.manuscript_id, s.editor_id, s.from_mailbox, s.to_email,
                  s.subject, s.body, s.status, s.reply_status, s.sent_at,
                  s.replied_at, s.scheduled_at, s.message_id)
        with self._lock, self._conn:
            if not protect:
                marks = ",".join("?" for _ in values)
                cur = self._conn.execute(
                    f"INSERT INTO submissions({columns}) VALUES({marks})", values)
                return cur.lastrowid
            cur = self._conn.execute(
                f"INSERT INTO submissions({columns}) "
                "SELECT ?,?,?,?,?,?,?,?,?,?,?,? WHERE NOT EXISTS ("
                "SELECT 1 FROM submissions WHERE manuscript_id=? AND editor_id=?"
                " AND (status IN ('待发','定时待发','发送中')"
                " OR (status='已发' AND reply_status='无')))",
                values + (s.manuscript_id, s.editor_id))
            return cur.lastrowid if cur.rowcount == 1 else None

    def reserve_daily_send(self, submission_id: int, mailbox_address: str,
                           daily_limit: int) -> bool:
        """逐封原子预留邮箱当日额度，并把任务置为发送中。"""
        if daily_limit <= 0 or not mailbox_address:
            return False
        today = date.today().strftime("%Y-%m-%d") + "%"
        now = _now()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE submissions SET from_mailbox=?,status='发送中',sent_at=?"
                " WHERE id=? AND status IN ('待发','定时待发')"
                " AND (SELECT COUNT(*) FROM submissions"
                " WHERE lower(from_mailbox)=lower(?)"
                " AND status IN ('已发','发送中')"
                " AND sent_at LIKE ?) < ?",
                (mailbox_address, now, submission_id, mailbox_address, today,
                 daily_limit))
            return cur.rowcount == 1

    def update_status(self, submission_id: int, status: str, sent_at: str | None = None,
                      error: str | None = None):
        with self._lock, self._conn:
            if sent_at is None and status == "已发":
                sent_at = _now()
            if sent_at is not None:
                self._conn.execute(
                    "UPDATE submissions SET status=?, sent_at=?, last_error=? WHERE id=?",
                    (status, sent_at, error or "", submission_id))
            else:
                self._conn.execute(
                    "UPDATE submissions SET status=?, last_error=? WHERE id=?",
                    (status, error or "", submission_id))

    def recover_stuck_sending(self, minutes: int = 30) -> int:
        """启动/定期调用：把卡在「发送中」超过 minutes 的记录回退为待发。

        崩溃后残留的「发送中」会永久占用当日额度并堵死一稿一投，
        此方法按 sent_at 超时判定回收，返回回收条数。
        """
        cutoff = (datetime.now() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE submissions SET status='待发', sent_at=''"
                " WHERE status='发送中' AND sent_at != '' AND sent_at < ?",
                (cutoff,))
            return cur.rowcount

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
        """导出 zip（data.db + files/），或兼容旧调用直接写 .db。"""
        import shutil
        import tempfile
        import zipfile
        dest_path = dest_path or ""
        tmp_dir = tempfile.mkdtemp(prefix="nailong_bak_")
        try:
            tmp_db = os.path.join(tmp_dir, "data.db")
            with self._lock:
                dest = sqlite3.connect(tmp_db)
                try:
                    self._conn.backup(dest)
                    rows = dest.execute(
                        "SELECT key,value FROM settings WHERE key LIKE 'mailbox_%'").fetchall()
                    for key, value in rows:
                        try:
                            data = json.loads(value)
                        except (TypeError, ValueError):
                            continue
                        if isinstance(data, dict) and "auth_code" in data:
                            data.pop("auth_code", None)
                            dest.execute(
                                "UPDATE settings SET value=? WHERE key=?",
                                (json.dumps(data, ensure_ascii=False), key))
                    dest.commit()
                finally:
                    dest.close()
            if dest_path.lower().endswith(".zip"):
                with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    zf.write(tmp_db, "data.db")
                    files_dir = self.files_dir
                    if os.path.isdir(files_dir):
                        for root, _dirs, names in os.walk(files_dir):
                            for name in names:
                                full = os.path.join(root, name)
                                rel = os.path.relpath(full, os.path.dirname(files_dir))
                                zf.write(full, rel.replace("\\", "/"))
            else:
                shutil.copy2(tmp_db, dest_path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def restore_from(self, src_path: str):
        """从 zip 或旧版纯 .db 覆盖当前库；恢复前先兜底备份。"""
        import shutil
        import tempfile
        import zipfile
        self._safety_backup()
        src_path = src_path or ""
        tmp_dir = tempfile.mkdtemp(prefix="nailong_rst_")
        try:
            if src_path.lower().endswith(".zip"):
                with zipfile.ZipFile(src_path, "r") as zf:
                    zf.extractall(tmp_dir)
                db_src = os.path.join(tmp_dir, "data.db")
                files_src = os.path.join(tmp_dir, "files")
                if os.path.isdir(files_src):
                    dest_files = self.files_dir
                    if os.path.isdir(dest_files):
                        shutil.rmtree(dest_files, ignore_errors=True)
                    shutil.copytree(files_src, dest_files)
            else:
                db_src = src_path
            with self._lock:
                src = sqlite3.connect(db_src)
                try:
                    src.backup(self._conn)
                finally:
                    src.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _safety_backup(self):
        backup_dir = os.path.join(os.path.dirname(self.db_path), "backups")
        os.makedirs(backup_dir, exist_ok=True)
        name = f"恢复前_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        try:
            self.backup_to(os.path.join(backup_dir, name))
        except Exception:
            pass

    def auto_backup_if_due(self, interval_days: int = 7, keep: int = 5) -> str | None:
        last = ""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key='last_auto_backup_at'").fetchone()
            last = row["value"] if row else ""
        due = True
        if last:
            try:
                prev = datetime.strptime(last[:19], "%Y-%m-%d %H:%M:%S")
                due = datetime.now() - prev >= timedelta(days=max(1, interval_days))
            except ValueError:
                due = True
        if not due:
            return None
        backup_dir = os.path.join(os.path.dirname(self.db_path), "backups")
        os.makedirs(backup_dir, exist_ok=True)
        dest = os.path.join(backup_dir, f"自动_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip")
        self.backup_to(dest)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO settings(key,value) VALUES('last_auto_backup_at',?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (_now(),))
        zips = sorted(
            (os.path.join(backup_dir, n) for n in os.listdir(backup_dir)
             if n.startswith("自动_") and n.endswith(".zip")),
            key=os.path.getmtime)
        for old in zips[:-max(1, keep)]:
            try:
                os.remove(old)
            except OSError:
                pass
        return dest

    def list_submissions_for_manuscript(self, manuscript_id: int) -> list[Submission]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM submissions WHERE manuscript_id=? ORDER BY id DESC",
                (manuscript_id,)).fetchall()
        return [self._row_to_submission(r) for r in rows]

    def update_submission_letter(self, submission_id: int, subject: str, body: str):
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE submissions SET subject=?, body=? WHERE id=?",
                (subject, body, submission_id))

    def update_scheduled_at(self, submission_id: int, scheduled_at: str):
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE submissions SET scheduled_at=? WHERE id=?",
                (scheduled_at, submission_id))

    def mark_urged(self, submission_id: int):
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE submissions SET last_urged_at=? WHERE id=?",
                (_now(), submission_id))

    def clear_business_data(self):
        """清空文稿、投递记录、回信、稿费 4 张业务表；保留编辑列表与设置。"""
        with self._lock, self._conn:
            for table in ("manuscripts", "submissions", "replies", "sales"):
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
        """一稿一投判定：待发、发送中或已发未回复均视为活动记录。"""
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM submissions WHERE manuscript_id=? AND editor_id=?"
                " AND (status IN ('待发','定时待发','发送中')"
                " OR (status='已发' AND reply_status='无'))"
                " ORDER BY id DESC LIMIT 1",
                (manuscript_id, editor_id)).fetchone()
        return self._row_to_submission(r) if r else None

    def find_submission_for_reply(self, from_email: str, mailbox_address: str,
                                  referenced_message_ids: list[str]) -> Submission | None:
        """按邮件线程精确关联；没有线程信息时只接受唯一邮箱候选。"""
        message_ids = list(dict.fromkeys(
            value.strip() for value in referenced_message_ids if value and value.strip()))
        with self._lock:
            if message_ids:
                marks = ",".join("?" for _ in message_ids)
                rows = self._conn.execute(
                    f"SELECT * FROM submissions WHERE message_id IN ({marks})"
                    " AND lower(to_email)=lower(?)"
                    " AND lower(from_mailbox)=lower(?)"
                    " AND reply_status='无'",
                    message_ids + [from_email, mailbox_address]).fetchall()
                if len(rows) == 1:
                    return self._row_to_submission(rows[0])
                if len(rows) > 1:
                    return None
            rows = self._conn.execute(
                "SELECT * FROM submissions WHERE status='已发' AND reply_status='无'"
                " AND lower(to_email)=lower(?)"
                " AND lower(from_mailbox)=lower(?)",
                (from_email, mailbox_address)).fetchall()
            if len(rows) == 1:
                return self._row_to_submission(rows[0])
            if len(rows) > 1:
                return None
            # 兼容旧记录：早期版本未保存发件邮箱，仅在候选唯一时关联。
            rows = self._conn.execute(
                "SELECT * FROM submissions WHERE status='已发' AND reply_status='无'"
                " AND lower(to_email)=lower(?)",
                (from_email,)).fetchall()
        return self._row_to_submission(rows[0]) if len(rows) == 1 else None

    def count_editor_last_days(self, editor_id: int, days: int = 7) -> int:
        return self.count_editors_last_days([editor_id], days).get(editor_id, 0)

    def count_editors_last_days(self, editor_ids: list[int], days: int = 7) -> dict[int, int]:
        ids = [int(i) for i in editor_ids if i]
        if not ids:
            return {}
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        marks = ",".join("?" for _ in ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT editor_id, COUNT(*) AS c FROM submissions"
                f" WHERE editor_id IN ({marks}) AND status='已发' AND sent_at >= ?"
                f" GROUP BY editor_id",
                ids + [since]).fetchall()
        result = {eid: 0 for eid in ids}
        for r in rows:
            result[int(r["editor_id"])] = int(r["c"])
        return result

    def count_editor_related(self, editor_id: int) -> tuple[int, int]:
        with self._lock:
            subs = self._conn.execute(
                "SELECT COUNT(*) AS c FROM submissions WHERE editor_id=?", (editor_id,)).fetchone()["c"]
            replies = self._conn.execute(
                "SELECT COUNT(*) AS c FROM replies WHERE submission_id IN "
                "(SELECT id FROM submissions WHERE editor_id=?)", (editor_id,)).fetchone()["c"]
        return int(subs), int(replies)

    def count_manuscript_related(self, manuscript_id: int) -> tuple[int, int]:
        with self._lock:
            subs = self._conn.execute(
                "SELECT COUNT(*) AS c FROM submissions WHERE manuscript_id=?",
                (manuscript_id,)).fetchone()["c"]
            replies = self._conn.execute(
                "SELECT COUNT(*) AS c FROM replies WHERE submission_id IN "
                "(SELECT id FROM submissions WHERE manuscript_id=?)",
                (manuscript_id,)).fetchone()["c"]
        return int(subs), int(replies)

    def count_today(self, mailbox_name: str) -> int:
        """该邮箱今日已发数（大小写不敏感，与 reserve_daily_send 口径一致）。"""
        today = date.today().strftime("%Y-%m-%d")
        with self._lock:
            r = self._conn.execute(
                "SELECT COUNT(*) AS c FROM submissions"
                " WHERE lower(from_mailbox)=lower(?) AND status='已发' AND sent_at LIKE ?",
                (mailbox_name, today + "%")).fetchone()
        return r["c"]

    # ---------- replies ----------
    @staticmethod
    def _row_to_reply(r: sqlite3.Row) -> Reply:
        return Reply(id=r["id"], submission_id=r["submission_id"], from_email=r["from_email"],
                     subject=r["subject"], snippet=r["snippet"], verdict=r["verdict"],
                     is_read=bool(r["is_read"]), imap_uid=r["imap_uid"],
                     mailbox_address=r["mailbox_address"],
                     imap_folder=r["imap_folder"], uid_validity=r["uid_validity"],
                     message_id=r["message_id"], in_reply_to=r["in_reply_to"],
                     references=r["reference_ids"], is_auto_reply=bool(r["is_auto_reply"]),
                     classification_confidence=r["classification_confidence"],
                     classification_reason=r["classification_reason"],
                     body_full=r["body_full"] if "body_full" in r.keys() else "",
                     received_at=r["received_at"])

    def insert_reply(self, r: Reply) -> int | None:
        """按 message_id（首选）/ imap_uid 去重，重复返回 None。"""
        with self._lock, self._conn:
            # message_id 稳定且不依赖 UIDVALIDITY：uid 为空或漂移时也能去重
            if r.message_id:
                dup = self._conn.execute(
                    "SELECT id FROM replies WHERE lower(mailbox_address)=lower(?)"
                    " AND imap_folder=? AND message_id=? LIMIT 1",
                    (r.mailbox_address or "", r.imap_folder, r.message_id)).fetchone()
                if dup:
                    return None
            if r.imap_uid:
                if r.mailbox_address:
                    dup = self._conn.execute(
                        "SELECT id FROM replies WHERE mailbox_address=? AND imap_folder=?"
                        " AND uid_validity=? AND imap_uid=?",
                        (r.mailbox_address, r.imap_folder, r.uid_validity, r.imap_uid)).fetchone()
                else:
                    dup = self._conn.execute(
                        "SELECT id FROM replies WHERE imap_uid=? AND from_email=?",
                        (r.imap_uid, r.from_email)).fetchone()
                if dup:
                    return None
            cur = self._conn.execute(
                "INSERT INTO replies(submission_id,from_email,subject,snippet,verdict,is_read,imap_uid,"
                "mailbox_address,imap_folder,uid_validity,message_id,in_reply_to,reference_ids,"
                "is_auto_reply,classification_confidence,classification_reason,body_full,received_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r.submission_id, r.from_email, r.subject, r.snippet, r.verdict,
                 int(r.is_read), r.imap_uid, r.mailbox_address, r.imap_folder,
                 r.uid_validity, r.message_id, r.in_reply_to, r.references,
                 int(r.is_auto_reply), r.classification_confidence,
                 r.classification_reason, r.body_full or "", r.received_at or _now()))
            return cur.lastrowid

    def delete_reply(self, reply_id: int):
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM replies WHERE id=?", (reply_id,))

    def delete_replies(self, ids: list[int]):
        if not ids:
            return
        marks = ",".join("?" for _ in ids)
        with self._lock, self._conn:
            self._conn.execute(f"DELETE FROM replies WHERE id IN ({marks})", ids)

    def list_replies(self, unread_only: bool = False) -> list[Reply]:
        sql = "SELECT * FROM replies"
        if unread_only:
            sql += " WHERE is_read = 0"
        sql += " ORDER BY id DESC"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [self._row_to_reply(r) for r in rows]

    def confirm_reply_verdict(self, reply_id: int, verdict: str) -> bool:
        """保存用户确认结果；有唯一投稿关联时同步更新投稿状态。

        - 过稿/退稿/需修改：回写投稿状态
        - 其他/自动回复：撤销此前自动回写（回到待回复），避免误判永久污染统计
        """
        if verdict not in {"过稿", "退稿", "需修改", "其他", "自动回复"}:
            raise ValueError("不支持的回信判定")
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT submission_id FROM replies WHERE id=?", (reply_id,)).fetchone()
            if row is None:
                return False
            self._conn.execute(
                "UPDATE replies SET verdict=?,classification_confidence='manual',"
                " classification_reason='用户手动确认' WHERE id=?",
                (verdict, reply_id))
            if row["submission_id"] is not None:
                if verdict in {"过稿", "退稿", "需修改"}:
                    self._conn.execute(
                        "UPDATE submissions SET reply_status=?,replied_at=? WHERE id=?",
                        (verdict, _now(), row["submission_id"]))
                else:
                    self._conn.execute(
                        "UPDATE submissions SET reply_status='无',replied_at='' WHERE id=?",
                        (row["submission_id"],))
            return True

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

    # ---------- 分页查询 ----------
    @staticmethod
    def _order_clause(order_by: str | None, mapping: dict[str, str],
                      default: str, desc: bool) -> str:
        col = mapping.get(order_by or "", default)
        direction = "DESC" if desc else "ASC"
        return f"{col} {direction}"

    def list_submissions_page(self, *, status_filter: str | None = None,
                              reply_filter: str | None = None,
                              keyword: str | None = None,
                              offset: int = 0, limit: int = 50,
                              order_by: str = "id",
                              desc: bool = True) -> tuple[int, list[Submission]]:
        """投递记录分页：可按文稿名/编辑/邮箱搜索，SQL ORDER BY + LIMIT。"""
        order_map = {
            "id": "s.id",
            "title": "IFNULL(m.title,'')",
            "editor": "IFNULL(e.name,'')",
            "platform": "IFNULL(e.platform,'')",
            "from_mailbox": "s.from_mailbox",
            "sent_at": "s.sent_at",
            "status": "s.status",
            "reply_status": "s.reply_status",
        }
        where = ["1=1"]
        args: list = []
        if status_filter:
            where.append("s.status=?")
            args.append(status_filter)
        if reply_filter == "未回复":
            where.append("(s.reply_status='' OR s.reply_status='无')")
        elif reply_filter and reply_filter not in ("全部", "全部判定"):
            where.append("s.reply_status=?")
            args.append(reply_filter)
        if keyword:
            like = f"%{keyword}%"
            where.append(
                "(IFNULL(m.title,'') LIKE ? OR IFNULL(e.name,'') LIKE ?"
                " OR IFNULL(e.email,'') LIKE ? OR IFNULL(s.to_email,'') LIKE ?"
                " OR IFNULL(s.from_mailbox,'') LIKE ?)")
            args.extend([like, like, like, like, like])
        where_sql = " AND ".join(where)
        from_sql = (
            "FROM submissions s"
            " LEFT JOIN manuscripts m ON m.id=s.manuscript_id"
            " LEFT JOIN editors e ON e.id=s.editor_id"
            f" WHERE {where_sql}")
        order_sql = self._order_clause(order_by, order_map, "s.id", desc)
        limit = max(1, int(limit or 50))
        offset = max(0, int(offset or 0))
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) AS c {from_sql}", args).fetchone()["c"]
            rows = self._conn.execute(
                f"SELECT s.* {from_sql} ORDER BY {order_sql}, s.id DESC"
                " LIMIT ? OFFSET ?",
                args + [limit, offset]).fetchall()
        return total, [self._row_to_submission(r) for r in rows]

    def list_replies_page(self, *, unread_only: bool = False,
                          verdict: str | None = None,
                          keyword: str | None = None,
                          offset: int = 0, limit: int = 50,
                          order_by: str = "id",
                          desc: bool = True) -> tuple[int, list[Reply]]:
        order_map = {
            "id": "id",
            "from_email": "from_email",
            "verdict": "verdict",
            "subject": "subject",
            "snippet": "snippet",
            "received_at": "received_at",
        }
        where = ["1=1"]
        args: list = []
        if unread_only:
            where.append("is_read=0")
        if verdict and verdict not in ("全部判定", "全部"):
            where.append("verdict=?")
            args.append(verdict)
        if keyword:
            like = f"%{keyword}%"
            where.append(
                "(IFNULL(from_email,'') LIKE ? OR IFNULL(subject,'') LIKE ?"
                " OR IFNULL(snippet,'') LIKE ?)")
            args.extend([like, like, like])
        where_sql = " AND ".join(where)
        order_sql = self._order_clause(order_by, order_map, "id", desc)
        limit = max(1, int(limit or 50))
        offset = max(0, int(offset or 0))
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) AS c FROM replies WHERE {where_sql}",
                args).fetchone()["c"]
            rows = self._conn.execute(
                f"SELECT * FROM replies WHERE {where_sql}"
                f" ORDER BY {order_sql}, id DESC LIMIT ? OFFSET ?",
                args + [limit, offset]).fetchall()
        return total, [self._row_to_reply(r) for r in rows]

    def list_sales_page(self, *, keyword: str | None = None,
                        date_from: str | None = None,
                        date_to: str | None = None,
                        offset: int = 0, limit: int = 50,
                        order_by: str = "id",
                        desc: bool = True) -> tuple[int, list[Sale]]:
        order_map = {
            "id": "s.id",
            "title": "IFNULL(m.title,'')",
            "platform": "s.platform",
            "editor_name": "s.editor_name",
            "amount": "s.amount",
            "sale_date": "s.sale_date",
            "payment_date": "s.payment_date",
            "notes": "s.notes",
        }
        where = ["1=1"]
        args: list = []
        if keyword:
            like = f"%{keyword}%"
            where.append(
                "(IFNULL(m.title,'') LIKE ? OR IFNULL(s.platform,'') LIKE ?"
                " OR IFNULL(s.editor_name,'') LIKE ? OR IFNULL(s.notes,'') LIKE ?)")
            args.extend([like, like, like, like])
        if date_from:
            where.append("s.sale_date>=?")
            args.append(date_from)
        if date_to:
            where.append("s.sale_date<=?")
            args.append(date_to)
        where_sql = " AND ".join(where)
        from_sql = (
            "FROM sales s LEFT JOIN manuscripts m ON m.id=s.manuscript_id"
            f" WHERE {where_sql}")
        order_sql = self._order_clause(order_by, order_map, "s.id", desc)
        limit = max(1, int(limit or 50))
        offset = max(0, int(offset or 0))
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) AS c {from_sql}", args).fetchone()["c"]
            rows = self._conn.execute(
                f"SELECT s.*, m.title AS mtitle {from_sql}"
                f" ORDER BY {order_sql}, s.id DESC LIMIT ? OFFSET ?",
                args + [limit, offset]).fetchall()
        return total, [self._row_to_sale(r) for r in rows]

    def upsert_editors_bulk(self, editors: list[Editor]) -> tuple[int, int]:
        """单事务按邮箱去重插入编辑。已存在或邮箱为空则跳过。返回 (导入, 跳过)。"""
        imported = skipped = 0
        with self._lock, self._conn:
            existing = {
                (r["email"] or "").strip().lower()
                for r in self._conn.execute(
                    "SELECT email FROM editors WHERE email != ''").fetchall()
            }
            for e in editors:
                email = (e.email or "").strip()
                if not email:
                    skipped += 1
                    continue
                key = email.lower()
                if key in existing:
                    skipped += 1
                    continue
                self._conn.execute(
                    "INSERT INTO editors(name,platform,email,genres,directions,status,"
                    "fee_info,source_url,notes,favorite,blacklisted,created_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (e.name or email, e.platform or "", email, e.genres or "",
                     e.directions or "", e.status or "", e.fee_info or "",
                     e.source_url or "", e.notes or "", int(bool(e.favorite)),
                     int(bool(e.blacklisted)), e.created_at or _now()))
                existing.add(key)
                imported += 1
        return imported, skipped

    def set_blacklisted_many(self, editor_ids: list[int], blacklisted: bool) -> int:
        ids = [int(eid) for eid in editor_ids if eid]
        if not ids:
            return 0
        marks = ",".join("?" for _ in ids)
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"UPDATE editors SET blacklisted=? WHERE id IN ({marks})",
                [int(bool(blacklisted)), *ids])
            return cur.rowcount

    def mark_read_many(self, reply_ids: list[int]) -> int:
        ids = [int(rid) for rid in reply_ids if rid]
        if not ids:
            return 0
        marks = ",".join("?" for _ in ids)
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"UPDATE replies SET is_read=1 WHERE id IN ({marks})", ids)
            return cur.rowcount

    def delete_submissions(self, ids: list[int]) -> int:
        ids = [int(sid) for sid in ids if sid]
        if not ids:
            return 0
        marks = ",".join("?" for _ in ids)
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"DELETE FROM submissions WHERE id IN ({marks})", ids)
            return cur.rowcount

    def delete_sales(self, ids: list[int]) -> int:
        ids = [int(sid) for sid in ids if sid]
        if not ids:
            return 0
        marks = ",".join("?" for _ in ids)
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"DELETE FROM sales WHERE id IN ({marks})", ids)
            return cur.rowcount

    def count_editors_related_many(self, editor_ids: list[int]) -> tuple[int, int]:
        ids = [int(eid) for eid in editor_ids if eid]
        if not ids:
            return 0, 0
        marks = ",".join("?" for _ in ids)
        with self._lock:
            n_sub = self._conn.execute(
                f"SELECT COUNT(*) AS c FROM submissions WHERE editor_id IN ({marks})",
                ids).fetchone()["c"]
            n_rep = self._conn.execute(
                f"SELECT COUNT(*) AS c FROM replies WHERE submission_id IN "
                f"(SELECT id FROM submissions WHERE editor_id IN ({marks}))",
                ids).fetchone()["c"]
        return n_sub, n_rep

    def count_manuscripts_related_many(self, manuscript_ids: list[int]) -> tuple[int, int]:
        ids = [int(mid) for mid in manuscript_ids if mid]
        if not ids:
            return 0, 0
        marks = ",".join("?" for _ in ids)
        with self._lock:
            n_sub = self._conn.execute(
                f"SELECT COUNT(*) AS c FROM submissions WHERE manuscript_id IN ({marks})",
                ids).fetchone()["c"]
            n_rep = self._conn.execute(
                f"SELECT COUNT(*) AS c FROM replies WHERE submission_id IN "
                f"(SELECT id FROM submissions WHERE manuscript_id IN ({marks}))",
                ids).fetchone()["c"]
        return n_sub, n_rep
