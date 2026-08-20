"""数据模型（dataclass）。"""
from __future__ import annotations

from dataclasses import dataclass, field

CATEGORIES = ["言情", "悬疑", "世情", "脑洞", "惊悚", "奇幻", "科幻", "武侠", "现实", "其他"]
READER_GROUPS = ["男频", "女频", "通用"]
EMOTIONS = ["甜", "虐", "爽", "燃", "暖", "虐心", "轻松"]
STYLES = ["第一人称", "第三人称", "多视角"]


@dataclass
class Editor:
    id: int | None = None
    name: str = ""
    platform: str = ""
    email: str = ""
    genres: str = ""          # 品类：短篇/长篇/短剧…
    directions: str = ""      # 收稿方向（题材）：世情/追妻/虐文…
    status: str = ""          # 收稿状态：正常收稿/停止收稿/未核实
    fee_info: str = ""
    source_url: str = ""
    notes: str = ""
    favorite: bool = False
    blacklisted: bool = False
    email_invalid: bool = False
    origin: str = "user"      # builtin=内置（不可导出） / user=用户自建
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
    word_count_source: str = ""   # word_saved / compatible / manual
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
    status: str = "待发"          # 待发 / 已发 / 发送中 / 已跳过 / 失败 / 定时待发
    reply_status: str = "无"      # 无 / 过稿 / 退稿 / 需修改
    sent_at: str = ""
    replied_at: str = ""
    scheduled_at: str = ""
    message_id: str = ""
    last_error: str = ""          # 最近一次失败原因（发送失败/跳过原因）
    batch_id: int | None = None
    batch_manuscript_id: int | None = None
    assigned_mailbox_id: str = ""
    queue_order: int = 0
    attachment_path: str = ""
    template_source: str = ""
    allowed_mailbox_ids: list[str] = field(default_factory=list)
    attempt_count: int = 0
    next_attempt_at: str = ""
    manuscript_title_snapshot: str = ""
    editor_name_snapshot: str = ""
    editor_platform_snapshot: str = ""


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
    mailbox_address: str = ""
    imap_folder: str = "INBOX"
    uid_validity: str = ""
    message_id: str = ""
    in_reply_to: str = ""
    references: str = ""
    is_auto_reply: bool = False
    classification_confidence: str = ""
    classification_reason: str = ""
    body_full: str = ""
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
    payment_date: str = ""           # yyyy-MM-dd
    notes: str = ""
    created_at: str = ""
    manuscript_title: str = ""       # 联表查询时填充，非表字段


@dataclass
class MailboxConfig:
    mailbox_id: str = ""
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
    limit_enabled: bool = False
    daily_limit: int = 20


@dataclass(frozen=True)
class DeliveryPolicy:
    """命名化投递策略；新批次始终使用 60–180 秒随机间隔。

    ``legacy_*`` 仅供旧页面/旧冒烟测试在迁移期读取，不参与新批次调度。
    """

    one_draft_protection: bool = True
    min_interval_seconds: int = 60
    max_interval_seconds: int = 180
    legacy_interval_seconds: int = 45
    legacy_daily_limit: int = 30

    def __iter__(self):
        yield self.one_draft_protection
        yield self.legacy_interval_seconds
        yield self.legacy_daily_limit

    def __getitem__(self, index: int):
        return tuple(self)[index]

    def __eq__(self, other):
        if isinstance(other, tuple):
            return tuple(self) == other
        if isinstance(other, DeliveryPolicy):
            return (
                self.one_draft_protection == other.one_draft_protection
                and self.min_interval_seconds == other.min_interval_seconds
                and self.max_interval_seconds == other.max_interval_seconds
                and self.legacy_interval_seconds == other.legacy_interval_seconds
                and self.legacy_daily_limit == other.legacy_daily_limit
            )
        return NotImplemented


@dataclass
class LetterTemplate:
    id: int | None = None
    name: str = ""
    subject: str = ""
    body: str = ""
    origin: str = "user"          # builtin / legacy / user
    created_at: str = ""
    updated_at: str = ""


@dataclass
class SubmissionBatch:
    id: int | None = None
    name: str = ""
    status: str = "draft"         # draft / scheduled / running / paused / waiting / completed / cancelled
    scheduled_at: str = ""
    random_seed: str = ""
    pause_reason: str = ""
    created_at: str = ""
    updated_at: str = ""
    started_at: str = ""
    finished_at: str = ""


@dataclass
class BatchManuscript:
    id: int | None = None
    batch_id: int | None = None
    manuscript_id: int | None = None
    position: int = 0
    mailbox_ids: list[str] = field(default_factory=list)
    template_ids: list[int] = field(default_factory=list)
    ai_templates: list[dict] = field(default_factory=list)
    target_editor_ids: list[int] = field(default_factory=list)


@dataclass
class AuthorInfo:
    real_name: str = ""
    pen_name: str = ""
    phone: str = ""
    address: str = ""
    payment_info: str = ""
