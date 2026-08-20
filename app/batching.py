"""投稿批次预检、确定性模板轮换与事务化任务物化。"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from email.utils import make_msgid
import os
import random
import shutil

from .letter import build_letter, validate_letter_template, vary_letter
from .models import BatchManuscript, Submission


@dataclass
class PlannedTask:
    batch_manuscript_id: int
    manuscript_id: int
    editor_id: int
    editor_name: str
    editor_platform: str
    manuscript_title: str
    to_email: str
    assigned_mailbox_id: str
    allowed_mailbox_ids: list[str]
    queue_order: int
    subject: str
    body: str
    template_source: str
    attachment_path: str


@dataclass
class ManuscriptPreflight:
    manuscript_id: int
    title: str
    configured: int = 0
    effective: int = 0
    skipped: Counter = field(default_factory=Counter)


@dataclass
class BatchPreflight:
    batch_id: int
    manuscripts: list[ManuscriptPreflight] = field(default_factory=list)
    tasks: list[PlannedTask] = field(default_factory=list)
    mailbox_counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    min_duration_seconds: int = 0
    max_duration_seconds: int = 0

    @property
    def total(self) -> int:
        return len(self.tasks)

    @property
    def skipped_total(self) -> int:
        return sum(sum(item.skipped.values()) for item in self.manuscripts)


def _template_sequence(templates: list[tuple[str, str, str]], count: int,
                       seed: str) -> list[tuple[str, str, str]]:
    """按稿件序列随机不放回，轮次边界不连续重复。"""
    if not templates or count <= 0:
        return []
    rng = random.Random(seed)
    result: list[tuple[str, str, str]] = []
    while len(result) < count:
        cycle = list(templates)
        rng.shuffle(cycle)
        if len(cycle) > 1 and result and cycle[0][0] == result[-1][0]:
            cycle[0], cycle[1] = cycle[1], cycle[0]
        result.extend(cycle)
    return result[:count]


class BatchPlanner:
    def __init__(self, db, store):
        self.db = db
        self.store = store

    def _templates(self, item: BatchManuscript) -> tuple[list[tuple[str, str, str]], list[str]]:
        result: list[tuple[str, str, str]] = []
        errors: list[str] = []
        for template_id in item.template_ids:
            template = self.db.get_letter_template(template_id)
            if template is None:
                errors.append(f"模板 #{template_id} 不存在")
                continue
            issues = validate_letter_template(template.subject, template.body)
            if issues:
                errors.append(f"模板“{template.name}”：{'；'.join(issues)}")
            else:
                result.append((f"library:{template.id}", template.subject, template.body))
        for index, candidate in enumerate(item.ai_templates or []):
            if not isinstance(candidate, dict) or candidate.get("selected", True) is False:
                continue
            subject = str(candidate.get("subject", ""))
            body = str(candidate.get("body", ""))
            name = str(candidate.get("name", f"AI 候选 {index + 1}"))
            issues = validate_letter_template(subject, body)
            if issues:
                errors.append(f"{name}：{'；'.join(issues)}")
            else:
                result.append((f"ai:{index}:{name}", subject, body))
        if not result:
            errors.append("没有可用的最终模板")
        return result, errors

    def preflight(self, batch_id: int) -> BatchPreflight:
        result = BatchPreflight(batch_id=batch_id)
        batch = self.db.get_batch(batch_id)
        if batch is None:
            result.errors.append("投稿批次不存在")
            return result
        configs = self.db.list_batch_manuscripts(batch_id)
        if not 1 <= len(configs) <= 10:
            result.errors.append("每批必须包含 1–10 篇文稿库稿件")
            return result

        policy = self.store.get_strategy()
        mailbox_order = [m for m in self.store.load_mailboxes()
                         if m.enabled and m.address and m.mailbox_id]
        mailboxes = {m.mailbox_id: m for m in mailbox_order}
        mailbox_positions = {m.mailbox_id: i for i, m in enumerate(mailbox_order)}
        queue_lengths = {m.mailbox_id: 0 for m in mailbox_order}
        seen_manuscripts: set[int] = set()

        for config in configs:
            manuscript = self.db.get_manuscript(config.manuscript_id)
            title = manuscript.title if manuscript else f"稿件 #{config.manuscript_id}"
            check = ManuscriptPreflight(
                manuscript_id=config.manuscript_id or 0, title=title,
                configured=len(config.target_editor_ids))
            result.manuscripts.append(check)
            if manuscript is None:
                result.errors.append(f"{title}：文稿不存在")
                continue
            if manuscript.id in seen_manuscripts:
                result.errors.append(f"《{title}》在批次中重复")
                continue
            seen_manuscripts.add(manuscript.id)
            if not manuscript.file_path or not os.path.isfile(manuscript.file_path):
                result.errors.append(f"《{title}》没有可用附件，请回到文稿库重新选择文件")
            allowed = [mid for mid in config.mailbox_ids if mid in mailboxes]
            if not allowed:
                result.errors.append(f"《{title}》至少选择一个已启用发件邮箱")
            templates, template_errors = self._templates(config)
            result.errors.extend(f"《{title}》：{error}" for error in template_errors)
            if not config.target_editor_ids:
                result.errors.append(f"《{title}》至少选择一位编辑")

            editors = []
            seen_platforms: set[str] = set()
            seen_addresses: set[str] = set()
            for editor_id in config.target_editor_ids:
                editor = self.db.get_editor(editor_id)
                if editor is None:
                    check.skipped["编辑不存在"] += 1
                    continue
                if editor.blacklisted:
                    check.skipped["小黑屋"] += 1
                    continue
                if editor.email_invalid or not (editor.email or "").strip():
                    check.skipped["失效邮箱"] += 1
                    continue
                address_key = editor.email.strip().casefold()
                if address_key in seen_addresses:
                    check.skipped["重复地址"] += 1
                    continue
                platform = (editor.platform or "").strip().casefold()
                if platform and platform in seen_platforms:
                    check.skipped["同平台"] += 1
                    continue
                if (policy.one_draft_protection
                        and self.db.active_submission_exists(
                            manuscript.id, editor.id, excluding_batch_id=batch_id)):
                    check.skipped["一稿一投"] += 1
                    continue
                seen_addresses.add(address_key)
                if platform:
                    seen_platforms.add(platform)
                editors.append(editor)
            check.effective = len(editors)
            if not allowed or not templates:
                continue
            template_sequence = _template_sequence(
                templates, len(editors), f"{batch.random_seed}:{config.id}:templates")
            for sequence, (editor, template) in enumerate(zip(editors, template_sequence)):
                mailbox_id = min(
                    allowed,
                    key=lambda mid: (queue_lengths[mid], mailbox_positions[mid]))
                queue_order = queue_lengths[mailbox_id]
                queue_lengths[mailbox_id] += 1
                source, subject_tpl, body_tpl = template
                subject, body = build_letter(
                    manuscript.title, manuscript.word_count, manuscript.category,
                    editor.name or "编辑老师", subject_tpl, body_tpl,
                    reader_group=manuscript.reader_group, emotion=manuscript.emotion,
                    style=manuscript.style, genre_type=manuscript.genre_type)
                if self.store.get_letter_vary():
                    protected = [
                        editor.name or "编辑老师", manuscript.title,
                        str(manuscript.word_count), manuscript.category,
                        manuscript.reader_group, manuscript.emotion,
                        manuscript.style, manuscript.genre_type,
                    ]
                    subject, body = vary_letter(
                        subject, body,
                        f"{batch.random_seed}:{config.id}:{sequence}:{editor.id}",
                        protected_values=protected)
                result.tasks.append(PlannedTask(
                    batch_manuscript_id=config.id or 0,
                    manuscript_id=manuscript.id or 0,
                    editor_id=editor.id or 0,
                    editor_name=editor.name or "编辑老师",
                    editor_platform=editor.platform or "",
                    manuscript_title=manuscript.title,
                    to_email=editor.email,
                    assigned_mailbox_id=mailbox_id,
                    allowed_mailbox_ids=list(allowed),
                    queue_order=queue_order,
                    subject=subject, body=body, template_source=source,
                    attachment_path=manuscript.file_path,
                ))

        # 按稿件轮流取一封再做最短队列分配，使单个邮箱队列也可跨稿件交错；
        # 每篇内部顺序不变，因此模板轮换不受网络完成顺序影响。
        grouped: dict[int, list[PlannedTask]] = {}
        group_order: list[int] = []
        for task in result.tasks:
            if task.batch_manuscript_id not in grouped:
                grouped[task.batch_manuscript_id] = []
                group_order.append(task.batch_manuscript_id)
            grouped[task.batch_manuscript_id].append(task)
        interleaved: list[PlannedTask] = []
        max_group = max((len(group) for group in grouped.values()), default=0)
        for offset in range(max_group):
            for group_id in group_order:
                if offset < len(grouped[group_id]):
                    interleaved.append(grouped[group_id][offset])
        queue_lengths = {m.mailbox_id: 0 for m in mailbox_order}
        for task in interleaved:
            mailbox_id = min(
                task.allowed_mailbox_ids,
                key=lambda mid: (queue_lengths[mid], mailbox_positions[mid]))
            task.assigned_mailbox_id = mailbox_id
            task.queue_order = queue_lengths[mailbox_id]
            queue_lengths[mailbox_id] += 1
        result.tasks = interleaved
        result.mailbox_counts = {mid: count for mid, count in queue_lengths.items() if count}
        if result.mailbox_counts:
            largest = max(result.mailbox_counts.values())
            result.min_duration_seconds = max(0, largest - 1) * policy.min_interval_seconds
            result.max_duration_seconds = max(0, largest - 1) * policy.max_interval_seconds
        if not result.tasks and not result.errors:
            result.errors.append("没有有效收件人，无法启动批次")
        return result

    def _snapshot_attachments(self, batch_id: int,
                              tasks: list[PlannedTask]) -> dict[int, str]:
        target_dir = os.path.join(self.db.files_dir, "batches", str(batch_id))
        os.makedirs(target_dir, exist_ok=True)
        result: dict[int, str] = {}
        for task in tasks:
            if task.batch_manuscript_id in result:
                continue
            ext = os.path.splitext(task.attachment_path)[1]
            target = os.path.join(target_dir, f"manuscript_{task.manuscript_id}{ext.lower()}")
            shutil.copy2(task.attachment_path, target)
            result[task.batch_manuscript_id] = target
        return result

    def activate(self, batch_id: int, *, scheduled_at: str = "") -> tuple[BatchPreflight, list[int]]:
        """重新执行确定性预检，冻结附件/文案并在单一事务内建立记录。"""
        preflight = self.preflight(batch_id)
        if preflight.errors:
            raise ValueError("\n".join(preflight.errors))
        snapshots = self._snapshot_attachments(batch_id, preflight.tasks)
        submissions: list[tuple[Submission, bool]] = []
        status = "定时待发" if scheduled_at else "待发"
        protect = self.store.get_strategy().one_draft_protection
        for task in preflight.tasks:
            submissions.append((Submission(
                manuscript_id=task.manuscript_id, editor_id=task.editor_id,
                to_email=task.to_email, subject=task.subject, body=task.body,
                status=status, scheduled_at=scheduled_at,
                message_id=make_msgid(domain="nailong.local"), batch_id=batch_id,
                batch_manuscript_id=task.batch_manuscript_id,
                assigned_mailbox_id=task.assigned_mailbox_id,
                allowed_mailbox_ids=task.allowed_mailbox_ids,
                queue_order=task.queue_order,
                attachment_path=snapshots[task.batch_manuscript_id],
                template_source=task.template_source,
                manuscript_title_snapshot=task.manuscript_title,
                editor_name_snapshot=task.editor_name,
                editor_platform_snapshot=task.editor_platform,
            ), protect))
        raw_ids = self.db.materialize_batch_submissions(batch_id, submissions)
        ids = [submission_id for submission_id in raw_ids if submission_id is not None]
        if not ids:
            raise ValueError("任务在最终一稿一投校验中全部被跳过")
        self.db.update_batch(
            batch_id, status="scheduled" if scheduled_at else "running",
            scheduled_at=scheduled_at, pause_reason="")
        return preflight, ids


def format_duration_range(min_seconds: int, max_seconds: int) -> str:
    def text(seconds: int) -> str:
        minutes = max(0, int(seconds)) // 60
        return f"约 {minutes // 60}小时{minutes % 60}分钟" if minutes >= 60 else f"约 {minutes} 分钟"
    return text(min_seconds) if min_seconds == max_seconds else f"{text(min_seconds)}–{text(max_seconds)}"
