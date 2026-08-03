"""数据模型（dataclass）。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Editor:
    id: int | None = None
    name: str = ""
    platform: str = ""
    email: str = ""
    genres: str = ""
    fee_info: str = ""
    source_url: str = ""
    notes: str = ""
    favorite: bool = False
    blacklisted: bool = False
    email_invalid: bool = False
    created_at: str = ""


@dataclass
class Manuscript:
    id: int | None = None
    title: str = ""
    file_path: str = ""
    word_count: int = 0
    category: str = ""
    reader_group: str = ""
    emotion: str = ""
    style: str = ""
    genre_type: str = ""
    created_at: str = ""


@dataclass
class Submission:
    id: int | None = None
    manuscript_id: int | None = None
    editor_id: int | None = None
    from_mailbox: str = ""
    to_email: str = ""
    subject: str = ""
    body: str = ""
    status: str = "待发"          # 待发 / 已发 / 失败 / 定时待发
    reply_status: str = "无"      # 无 / 过稿 / 退稿 / 需修改
    sent_at: str = ""
    replied_at: str = ""
    scheduled_at: str = ""


@dataclass
class Reply:
    id: int | None = None
    submission_id: int | None = None
    from_email: str = ""
    subject: str = ""
    snippet: str = ""
    verdict: str = "其他"         # 过稿 / 退稿 / 需修改 / 其他
    is_read: bool = False
    imap_uid: str = ""
    received_at: str = ""


@dataclass
class Sale:
    id: int | None = None
    manuscript_id: int | None = None
    platform: str = ""
    editor_name: str = ""
    amount: float | None = None      # 稿费金额（元），可空
    sale_date: str = ""              # yyyy-MM-dd
    payment_month: str = ""          # yyyy-MM
    notes: str = ""
    created_at: str = ""
    manuscript_title: str = ""       # 联表查询时填充，非表字段


@dataclass
class MailboxConfig:
    enabled: bool = False
    provider: str = "QQ邮箱"
    address: str = ""
    auth_code: str = ""
    display_name: str = ""
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_ssl: bool = True
    imap_host: str = ""
    imap_port: int = 993
    daily_limit: int = 20


@dataclass
class AuthorInfo:
    real_name: str = ""
    pen_name: str = ""
    phone: str = ""
    address: str = ""
    payment_info: str = ""
