"""设置读写：settings 表键值存取，邮箱（默认 6 个，可自行添加）/ 作者信息 / 策略 / 收信 / 主题。"""
from __future__ import annotations

import json

from .db import Database
from .models import MailboxConfig, AuthorInfo
from . import credential_store

DEFAULT_MAILBOX_COUNT = 6
DEFAULT_THEME = "蔷薇粉"

_PROVIDER_PRESETS = {
    "QQ邮箱": ("smtp.qq.com", 465, True, "imap.qq.com", 993),
    "163邮箱": ("smtp.163.com", 465, True, "imap.163.com", 993),
    "126邮箱": ("smtp.126.com", 465, True, "imap.126.com", 993),
    "新浪邮箱": ("smtp.sina.com", 465, True, "imap.sina.com", 993),
    "Outlook": ("smtp.office365.com", 587, False, "imap.outlook.com", 993),
    "自定义": ("", 465, True, "", 993),
}


def provider_preset(provider: str):
    """返回 (smtp_host, smtp_port, smtp_ssl, imap_host, imap_port)，未知按自定义。"""
    return _PROVIDER_PRESETS.get(provider, _PROVIDER_PRESETS["自定义"])


PROVIDER_NAMES = list(_PROVIDER_PRESETS.keys())


class SettingsStore:
    def __init__(self, db: Database):
        self.db = db
        # Migrate legacy JSON auth codes before exposing mailbox settings.
        raw_count = self.get("mailbox_count", "")
        try:
            count = max(1, int(raw_count)) if raw_count else DEFAULT_MAILBOX_COUNT
        except ValueError:
            count = DEFAULT_MAILBOX_COUNT
        for i in range(count):
            key = f"mailbox_{i}"
            raw = self.get(key, "")
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                continue
            auth_code = data.get("auth_code", "")
            if not isinstance(auth_code, str) or not auth_code:
                continue
            if credential_store.set(key, auth_code):
                data.pop("auth_code", None)
                self.set(key, json.dumps(data, ensure_ascii=False))

    # ---------- 基础键值 ----------
    def get(self, key: str, default: str = "") -> str:
        with self.db._lock:
            r = self.db._conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set(self, key: str, value: str):
        with self.db._lock, self.db._conn:
            self.db._conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))

    def _safe_int(self, key: str, default: int) -> int:
        """读取整数设置；脏数据（非数字）回写默认值，避免启动/设置页崩溃。"""
        try:
            value = int(self.get(key, str(default)) or str(default))
        except (TypeError, ValueError):
            value = default
            self.set(key, str(default))
        return value

    # ---------- 邮箱 ----------
    def load_mailboxes(self) -> list[MailboxConfig]:
        """按 mailbox_count（默认 6）返回 MailboxConfig，不足补默认空配置。"""
        raw_count = self.get("mailbox_count", "")
        try:
            count = max(1, int(raw_count)) if raw_count else DEFAULT_MAILBOX_COUNT
        except ValueError:
            count = DEFAULT_MAILBOX_COUNT
        result: list[MailboxConfig] = []
        for i in range(count):
            raw = self.get(f"mailbox_{i}", "")
            if raw:
                try:
                    d = json.loads(raw)
                    stored_auth_code = credential_store.get(f"mailbox_{i}")
                    result.append(MailboxConfig(
                        enabled=bool(d.get("enabled", False)),
                        provider=d.get("provider", "QQ邮箱"),
                        address=d.get("address", ""),
                        auth_code=stored_auth_code if stored_auth_code is not None
                        else d.get("auth_code", ""),
                        display_name=d.get("display_name", ""),
                        smtp_host=d.get("smtp_host", ""),
                        smtp_port=int(d.get("smtp_port", 465)),
                        smtp_ssl=bool(d.get("smtp_ssl", True)),
                        imap_host=d.get("imap_host", ""),
                        imap_port=int(d.get("imap_port", 993)),
                        daily_limit=int(d.get("daily_limit", 20)),
                    ))
                    continue
                except (ValueError, TypeError, KeyError):
                    pass
            result.append(MailboxConfig())
        return result

    def save_mailbox(self, index: int, cfg: MailboxConfig):
        key = f"mailbox_{index}"
        if cfg.auth_code:
            if not credential_store.set(key, cfg.auth_code):
                raise RuntimeError("无法安全保存邮箱授权码")
        elif not credential_store.delete(key):
            raise RuntimeError("无法删除邮箱授权码")
        d = {
            "enabled": cfg.enabled, "provider": cfg.provider, "address": cfg.address,
            "display_name": cfg.display_name,
            "smtp_host": cfg.smtp_host, "smtp_port": cfg.smtp_port, "smtp_ssl": cfg.smtp_ssl,
            "imap_host": cfg.imap_host, "imap_port": cfg.imap_port,
            "daily_limit": cfg.daily_limit,
        }
        self.set(f"mailbox_{index}", json.dumps(d, ensure_ascii=False))

    def save_mailbox_count(self, count: int):
        """记录邮箱卡片数量（用户可自行添加更多）。"""
        self.set("mailbox_count", str(count))

    # ---------- 作者信息 ----------
    def load_author(self) -> AuthorInfo:
        return AuthorInfo(
            real_name=self.get("author_real_name"),
            pen_name=self.get("author_pen_name"),
            phone=self.get("author_phone"),
            address=self.get("author_address"),
            payment_info=self.get("author_payment_info"),
        )

    def save_author(self, a: AuthorInfo):
        self.set("author_real_name", a.real_name)
        self.set("author_pen_name", a.pen_name)
        self.set("author_phone", a.phone)
        self.set("author_address", a.address)
        self.set("author_payment_info", a.payment_info)

    # ---------- 投递策略 ----------
    def get_strategy(self) -> tuple[bool, int, int]:
        """返回 (one_draft_protection, interval_seconds, daily_limit)。"""
        one_draft = self.get("strategy_one_draft", "1") == "1"
        interval = self._safe_int("strategy_interval", 45)
        daily = self._safe_int("strategy_daily_limit", 30)
        return one_draft, interval, daily

    def save_strategy(self, one_draft_protection: bool, interval_seconds: int,
                      daily_limit: int | None = None):
        self.set("strategy_one_draft", "1" if one_draft_protection else "0")
        self.set("strategy_interval", str(interval_seconds))
        if daily_limit is not None:
            self.set("strategy_daily_limit", str(daily_limit))

    # ---------- 催稿提醒 ----------
    def get_urge_days(self) -> int:
        """催稿提醒天数，默认 30。"""
        return self._safe_int("urge_days", 30)

    def save_urge_days(self, days: int):
        self.set("urge_days", str(days))

    # ---------- 投稿信模板 ----------
    def get_letter_template(self) -> tuple[str, str]:
        """返回 (主题模板, 正文模板)，未自定义时返回默认模板。"""
        from .letter import DEFAULT_SUBJECT_TPL, DEFAULT_BODY_TPL
        return (self.get("letter_subject_tpl") or DEFAULT_SUBJECT_TPL,
                self.get("letter_body_tpl") or DEFAULT_BODY_TPL)

    def save_letter_template(self, subject_tpl: str, body_tpl: str):
        self.set("letter_subject_tpl", subject_tpl)
        self.set("letter_body_tpl", body_tpl)

    # ---------- 收信配置 ----------
    def get_fetch_config(self) -> tuple[bool, int, int]:
        """返回 (auto_fetch, interval_minutes, lookback_days)。"""
        auto = self.get("fetch_auto", "1") == "1"
        interval = self._safe_int("fetch_interval_minutes", 30)
        lookback = self._safe_int("fetch_lookback_days", 45)
        return auto, interval, lookback

    def save_fetch_config(self, auto_fetch: bool, interval_minutes: int, lookback_days: int):
        self.set("fetch_auto", "1" if auto_fetch else "0")
        self.set("fetch_interval_minutes", str(interval_minutes))
        self.set("fetch_lookback_days", str(lookback_days))

    # ---------- 主题 ----------
    def get_theme(self) -> str:
        return self.get("theme", DEFAULT_THEME)

    def set_theme(self, name: str):
        self.set("theme", name)
