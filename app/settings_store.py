"""设置读写：settings 表键值存取，邮箱（默认 6 个，可自行添加）/ 作者信息 / 策略 / 收信 / 主题。"""
from __future__ import annotations

import json
import uuid

from .db import Database
from .models import AuthorInfo, DeliveryPolicy, MailboxConfig
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
            changed = False
            auth_code = data.get("auth_code", "")
            if isinstance(auth_code, str) and auth_code:
                if credential_store.set(key, auth_code):
                    data.pop("auth_code", None)
                    changed = True
            if not str(data.get("mailbox_id", "")).strip():
                data["mailbox_id"] = uuid.uuid4().hex
                changed = True
            if "limit_enabled" not in data:
                # 升级保留旧数值，但保护默认关闭（即本地不设上限）。
                data["limit_enabled"] = False
                changed = True
            if changed:
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
            key = f"mailbox_{i}"
            raw = self.get(key, "")
            if raw:
                try:
                    d = json.loads(raw)
                    mailbox_id = str(d.get("mailbox_id", "")).strip() or uuid.uuid4().hex
                    limit_enabled = bool(d.get("limit_enabled", False))
                    if d.get("mailbox_id") != mailbox_id or "limit_enabled" not in d:
                        d["mailbox_id"] = mailbox_id
                        d["limit_enabled"] = limit_enabled
                        self.set(key, json.dumps(d, ensure_ascii=False))
                    stored_auth_code = credential_store.get(key)
                    result.append(MailboxConfig(
                        mailbox_id=mailbox_id,
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
                        limit_enabled=limit_enabled,
                        daily_limit=int(d.get("daily_limit", 20)),
                    ))
                    continue
                except (ValueError, TypeError, KeyError):
                    pass
            mailbox_id = uuid.uuid4().hex
            default_data = {
                "mailbox_id": mailbox_id, "enabled": False,
                "provider": "QQ邮箱", "address": "", "display_name": "",
                "smtp_host": "", "smtp_port": 465, "smtp_ssl": True,
                "imap_host": "", "imap_port": 993,
                "limit_enabled": False, "daily_limit": 20,
            }
            self.set(key, json.dumps(default_data, ensure_ascii=False))
            result.append(MailboxConfig(mailbox_id=mailbox_id))
        return result

    def save_mailbox(self, index: int, cfg: MailboxConfig):
        key = f"mailbox_{index}"
        mailbox_id = str(cfg.mailbox_id or "").strip()
        if not mailbox_id:
            try:
                existing = json.loads(self.get(key, "") or "{}")
                mailbox_id = str(existing.get("mailbox_id", "")).strip()
            except (ValueError, TypeError, AttributeError):
                mailbox_id = ""
        mailbox_id = mailbox_id or uuid.uuid4().hex
        if cfg.auth_code:
            if not credential_store.set(key, cfg.auth_code):
                raise RuntimeError("无法安全保存邮箱授权码")
        else:
            # 授权码为空时尝试清理旧凭据；凭据系统不可用或没有旧凭据时
            # 不应阻塞其他设置（如投稿信模板）的保存。
            credential_store.delete(key)
        d = {
            "mailbox_id": mailbox_id,
            "enabled": cfg.enabled, "provider": cfg.provider, "address": cfg.address,
            "display_name": cfg.display_name,
            "smtp_host": cfg.smtp_host, "smtp_port": cfg.smtp_port, "smtp_ssl": cfg.smtp_ssl,
            "imap_host": cfg.imap_host, "imap_port": cfg.imap_port,
            "limit_enabled": cfg.limit_enabled, "daily_limit": cfg.daily_limit,
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
    def get_strategy(self) -> DeliveryPolicy:
        """返回命名策略；新批次的随机间隔固定为 60–180 秒。"""
        one_draft = self.get("strategy_one_draft", "1") == "1"
        interval = self._safe_int("strategy_interval", 45)
        daily = self._safe_int("strategy_daily_limit", 30)
        return DeliveryPolicy(
            one_draft_protection=one_draft,
            min_interval_seconds=60,
            max_interval_seconds=180,
            legacy_interval_seconds=interval,
            legacy_daily_limit=daily,
        )

    def save_strategy(self, one_draft_protection: bool, interval_seconds: int,
                      daily_limit: int | None = None):
        """保存兼容字段；新批次不会读取固定间隔或全局额度。"""
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
        legacy_subject = self.get("letter_subject_tpl")
        legacy_body = self.get("letter_body_tpl")
        if legacy_subject or legacy_body:
            return (legacy_subject or DEFAULT_SUBJECT_TPL,
                    legacy_body or DEFAULT_BODY_TPL)
        templates = self.db.list_letter_templates()
        template = templates[0] if templates else None
        return ((template.subject if template else DEFAULT_SUBJECT_TPL),
                (template.body if template else DEFAULT_BODY_TPL))

    def save_letter_template(self, subject_tpl: str, body_tpl: str):
        # 保留旧键，确保旧定时记录、备份和旧版本回退仍能读取。
        self.set("letter_subject_tpl", subject_tpl)
        self.set("letter_body_tpl", body_tpl)
        from .models import LetterTemplate
        legacy = next((t for t in self.db.list_letter_templates()
                       if t.origin == "legacy"), None)
        if legacy is None:
            template_id = self.db.insert_letter_template(LetterTemplate(
                name="旧版模板", subject=subject_tpl, body=body_tpl,
                origin="legacy"))
        else:
            legacy.subject = subject_tpl
            legacy.body = body_tpl
            self.db.update_letter_template(legacy)
            template_id = legacy.id
        self.set("letter_current_template_id", str(template_id))

    def get_letter_vary(self) -> bool:
        return self.get("letter_vary", "1") == "1"

    def save_letter_vary(self, enabled: bool):
        self.set("letter_vary", "1" if enabled else "0")

    def get_letter_ai_vary(self) -> bool:
        return self.get("letter_ai_vary", "0") == "1"

    def save_letter_ai_vary(self, enabled: bool):
        self.set("letter_ai_vary", "1" if enabled else "0")

    def get_urge_template(self) -> tuple[str, str]:
        from .letter import DEFAULT_URGE_SUBJECT, DEFAULT_URGE_BODY
        return (self.get("urge_subject_tpl") or DEFAULT_URGE_SUBJECT,
                self.get("urge_body_tpl") or DEFAULT_URGE_BODY)

    def save_urge_template(self, subject: str, body: str):
        self.set("urge_subject_tpl", subject)
        self.set("urge_body_tpl", body)

    def get_ai_config(self):
        from .ai_client import AiConfig, DEFAULT_PROVIDER, load_api_key, preset_for
        provider = self.get("ai_provider", "") or DEFAULT_PROVIDER
        preset_url, preset_model, _hint = preset_for(provider)
        return AiConfig(
            provider=provider,
            base_url=self.get("ai_base_url", "") or preset_url,
            model=self.get("ai_model", "") or preset_model,
            api_key=load_api_key(),
        )

    def save_ai_config(self, provider: str, base_url: str, model: str, api_key: str):
        from .ai_client import save_api_key
        self.set("ai_provider", provider or "")
        self.set("ai_base_url", (base_url or "").strip())
        self.set("ai_model", (model or "").strip())
        if not save_api_key(api_key):
            raise RuntimeError("无法安全保存 API Key")

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
