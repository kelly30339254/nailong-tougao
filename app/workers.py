"""后台线程（QThread）：网络和耗时任务不阻塞界面。"""
from __future__ import annotations

import csv
import io
import logging
import os
import random
import shutil
import time
import threading
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as wait_futures
from datetime import datetime, timedelta

from PySide6.QtCore import QThread, Signal

from .models import Editor, MailboxConfig, Manuscript
from . import mailer, receiver, updater, update_check

_log = logging.getLogger(__name__)

_EDITOR_CSV_ALIASES = {
    "name": ("名称", "name"),
    "platform": ("平台", "platform"),
    "email": ("邮箱", "email"),
    "genres": ("题材", "genres"),
    "directions": ("收稿方向", "directions"),
    "status": ("状态", "status"),
    "fee_info": ("稿费", "fee_info"),
    "source_url": ("来源", "source_url"),
    "notes": ("备注", "notes"),
}


def _is_temp_smtp_error(exc: Exception) -> bool:
    import smtplib
    import socket
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionError)):
        return True
    if isinstance(exc, smtplib.SMTPResponseException) and 400 <= exc.smtp_code < 500:
        return True
    text = str(exc).lower()
    return "timed out" in text or "timeout" in text or "temporarily" in text


class VaryLettersWorker(QThread):
    progress = Signal(int, int, str)
    finished_ok = Signal(object)

    def __init__(self, jobs: list, cfg, title: str, extra: str, parent=None, db=None):
        super().__init__(parent)
        self.jobs = jobs
        self.cfg = cfg
        self.title = title
        self.extra = extra
        self.db = db
        self._stopped = False

    def stop(self):
        self._stopped = True

    def run(self):
        from . import ai_smart
        from .letter import vary_letter
        total = len(self.jobs)
        for i, job in enumerate(self.jobs):
            name = job.get("editor_name") or job.get("to") or ""
            self.progress.emit(i + 1, total, f"正在微调第 {i + 1}/{total} 封：{name}")
            subject, body = job.get("subject", ""), job.get("body", "")
            if not self._stopped:
                try:
                    subject, body = ai_smart.vary_letter_ai(
                        self.cfg, subject, body, name, self.title, self.extra)
                except Exception:
                    seed = f"{job.get('submission_id')}:{self.title}"
                    subject, body = vary_letter(subject, body, seed)
            else:
                seed = f"{job.get('submission_id')}:{self.title}"
                subject, body = vary_letter(subject, body, seed)
            job["subject"], job["body"] = subject, body
            if self.db is not None and job.get("submission_id"):
                try:
                    self.db.update_submission_letter(job["submission_id"], subject, body)
                except Exception:
                    pass
        self.finished_ok.emit(self.jobs)


class AiTestWorker(QThread):
    result = Signal(bool, str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config

    def run(self):
        from . import ai_client
        try:
            text = ai_client.chat(
                self.config,
                [{"role": "user", "content": "只回复：OK"}],
                timeout=25, temperature=0, max_tokens=16)
            ok = bool(text)
            self.result.emit(ok, "连接成功" if ok else "接口没有返回内容")
        except Exception as exc:
            self.result.emit(False, str(exc))


class AiRankWorker(QThread):
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, config, query: dict, editors: list, parent=None):
        super().__init__(parent)
        self.config = config
        self.query = query
        self.editors = editors

    def run(self):
        from . import ai_smart
        try:
            result = ai_smart.rank_editors(self.config, self.query, self.editors)
            self.finished_ok.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class AiCallWorker(QThread):
    """通用 AI 后台调用：fn() 的返回值经 finished_ok 传回。"""
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.finished_ok.emit(self.fn())
        except Exception as exc:
            self.failed.emit(str(exc))


class AiLetterTplWorker(QThread):
    finished_ok = Signal(str, str)
    failed = Signal(str)

    def __init__(self, config, requirements: str, parent=None):
        super().__init__(parent)
        self.config = config
        self.requirements = requirements

    def run(self):
        from . import ai_smart
        try:
            subject, body = ai_smart.generate_letter_template(self.config, self.requirements)
            self.finished_ok.emit(subject, body)
        except Exception as exc:
            self.failed.emit(str(exc))


class TestMailboxWorker(QThread):
    result = Signal(bool, str)

    def __init__(self, mailbox: MailboxConfig, parent=None):
        super().__init__(parent)
        self.mailbox = mailbox

    def run(self):
        try:
            ok, message = mailer.test_mailbox(self.mailbox)
        except Exception as exc:
            ok, message = False, f"测试失败：{exc}"
        self.result.emit(ok, message)


class SendWorker(QThread):
    """逐封发送，多邮箱轮转；失败继续下一封。DB 状态由主线程根据 item_done 回写。"""

    progress = Signal(int, int, str)          # 当前, 总数, 消息
    item_done = Signal(int, bool, str, str, bool)  # submission_id, ok, error, 实际发信邮箱, skipped(跳过≠失败)
    all_done = Signal()

    def __init__(self, mailboxes: list[MailboxConfig], jobs: list[dict],
                 interval_seconds: int, parent=None, db=None):
        super().__init__(parent)
        # 只保留已启用的邮箱
        self.mailboxes = [m for m in mailboxes if m.enabled]
        self.jobs = jobs
        self.interval_seconds = interval_seconds
        self.db = db
        self._stopped = False

    def stop(self):
        self._stopped = True

    def run(self):
        total = len(self.jobs)
        if not self.mailboxes:
            for i, job in enumerate(self.jobs):
                self.progress.emit(i + 1, total, f"无已启用邮箱，跳过 {job.get('to', '')}")
                self.item_done.emit(job.get("submission_id", -1), False, "无已启用邮箱", "", True)
            self.all_done.emit()
            return

        for i, job in enumerate(self.jobs):
            if self._stopped:
                self.progress.emit(i, total, "已手动停止")
                break
            sid = job.get("submission_id", -1)
            to = job.get("to", "")
            mailbox = self.mailboxes[i % len(self.mailboxes)]
            send_kwargs = {}
            try:
                if self.db is not None:
                    selected = None
                    start = i % len(self.mailboxes)
                    for offset in range(len(self.mailboxes)):
                        candidate = self.mailboxes[(start + offset) % len(self.mailboxes)]
                        if self.db.reserve_daily_send(
                                sid, candidate.address,
                                candidate.daily_limit if candidate.limit_enabled else None,
                                candidate.mailbox_id):
                            selected = candidate
                            break
                    if selected is None:
                        message = "所有启用邮箱今日额度已用完或任务状态已变化"
                        self.progress.emit(i + 1, total, f"跳过 {to}：{message}")
                        self.item_done.emit(sid, False, message, "", True)
                        continue
                    mailbox = selected
                self.progress.emit(i + 1, total, f"正在发送 {to}（{mailbox.address}）")
                send_kwargs = {}
                if job.get("message_id"):
                    send_kwargs["message_id"] = job["message_id"]
                mailer.send_mail(
                    mailbox, to, job.get("subject", ""), job.get("body", ""),
                    job.get("attachment_path") or None, **send_kwargs)
                self.item_done.emit(sid, True, "", mailbox.address, False)
            except Exception as exc:
                if (_is_temp_smtp_error(exc) and not self._stopped
                        and not os.environ.get("NAILONG_DATA_DIR")
                        and not os.environ.get("NAILONG_SMOKE")):
                    self.progress.emit(i + 1, total, f"{to} 临时失败，60 秒后重试…")
                    deadline = time.time() + 60
                    while time.time() < deadline and not self._stopped:
                        time.sleep(0.2)
                    if not self._stopped:
                        try:
                            mailer.send_mail(
                                mailbox, to, job.get("subject", ""), job.get("body", ""),
                                job.get("attachment_path") or None, **send_kwargs)
                            self.item_done.emit(sid, True, "", mailbox.address, False)
                            if i < total - 1 and not self._stopped:
                                wait_end = time.time() + self.interval_seconds
                                while time.time() < wait_end and not self._stopped:
                                    time.sleep(0.1)
                            continue
                        except Exception as exc2:
                            self.item_done.emit(sid, False, str(exc2), mailbox.address, False)
                    else:
                        self.item_done.emit(sid, False, str(exc), mailbox.address, False)
                else:
                    self.item_done.emit(sid, False, str(exc), mailbox.address, False)
            # 发信间隔（可被 stop 打断），最后一封不必等
            if i < total - 1 and not self._stopped:
                deadline = time.time() + self.interval_seconds
                while time.time() < deadline and not self._stopped:
                    time.sleep(0.1)
        self.all_done.emit()


class BatchSendCoordinator(QThread):
    """一个批次、每邮箱一条串行队列；邮箱之间并行。"""

    progress = Signal(int, int, str)
    item_done = Signal(int, str, str, str)  # id, status, error, mailbox address
    mailbox_paused = Signal(str, str)       # mailbox id, reason
    batch_done = Signal(str)                # completed / paused / waiting / cancelled
    all_done = Signal()

    PENDING_STATUSES = (
        "待发", "定时待发", "等待限额", "等待用户处理", "重试等待")

    def __init__(self, db, batch_id: int, mailboxes: list[MailboxConfig], parent=None,
                 *, rng=None, sleep_fn=None, now_fn=None, send_fn=None):
        super().__init__(parent)
        self.db = db
        self.batch_id = int(batch_id)
        self.mailboxes = {
            m.mailbox_id: m for m in mailboxes
            if m.enabled and m.address and m.mailbox_id
        }
        self.mailbox_order = [m.mailbox_id for m in mailboxes
                              if m.mailbox_id in self.mailboxes]
        self._rng = rng or random.SystemRandom()
        self._sleep = sleep_fn or time.sleep
        self._now = now_fn or datetime.now
        self._send = send_fn or mailer.send_mail
        self._pause_requested = threading.Event()
        self._cancel_requested = threading.Event()

    def pause(self):
        """当前 SMTP 调用结束后停止领取新任务。"""
        self._pause_requested.set()

    def stop(self):
        self.pause()

    def cancel(self):
        self._cancel_requested.set()
        self._pause_requested.set()

    def _interruptible_wait(self, seconds: float) -> bool:
        if seconds <= 0 or os.environ.get("NAILONG_SMOKE"):
            return not self._pause_requested.is_set()
        remaining = float(seconds)
        while remaining > 0:
            if self._pause_requested.is_set() or self._cancel_requested.is_set():
                return False
            step = min(1.0, remaining)
            self._sleep(step)
            remaining -= step
        return True

    @staticmethod
    def _parse_time(value: str) -> datetime | None:
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _smtp_error_kind(exc: Exception) -> str:
        import smtplib
        import socket
        import ssl
        text = str(exc).casefold()
        mailbox_words = (
            "auth", "535", "quota", "rate limit", "too many", "frequency",
            "daily limit", "sender", "login", "connection refused",
        )
        recipient_words = (
            "recipient", "mailbox unavailable", "user unknown", "no such user",
            "invalid address", "bad address",
        )
        if isinstance(exc, smtplib.SMTPRecipientsRefused):
            return "recipient"
        if any(word in text for word in recipient_words):
            return "recipient"
        if isinstance(exc, smtplib.SMTPAuthenticationError):
            return "mailbox"
        if any(word in text for word in mailbox_words):
            return "mailbox"
        if isinstance(exc, (TimeoutError, socket.timeout, socket.gaierror,
                            ConnectionError, smtplib.SMTPServerDisconnected,
                            ssl.SSLError)):
            return "temporary"
        if isinstance(exc, smtplib.SMTPResponseException):
            if 400 <= exc.smtp_code < 500:
                return "temporary"
            if exc.smtp_code in (550, 551, 552, 553):
                return "recipient"
        return "recipient"

    def _attempt(self, mailbox: MailboxConfig, submission):
        limit = mailbox.daily_limit if mailbox.limit_enabled else None
        if not self.db.reserve_daily_send(
                submission.id, mailbox.address, limit, mailbox.mailbox_id):
            return "limit", "已达到本地每日保护上限或任务状态已变化"
        self.db.mark_submission_attempt(submission.id)
        submission.attempt_count += 1
        kwargs = {"message_id": submission.message_id} if submission.message_id else {}
        try:
            self._send(mailbox, submission.to_email, submission.subject,
                       submission.body, submission.attachment_path or None, **kwargs)
            return "sent", ""
        except Exception as exc:
            kind = self._smtp_error_kind(exc)
            return kind, str(exc)

    def _next_tomorrow(self) -> datetime:
        now = self._now()
        return (now + timedelta(days=1)).replace(hour=0, minute=0, second=1,
                                                  microsecond=0)

    def run(self):
        submissions = self.db.list_batch_submissions(
            self.batch_id, self.PENDING_STATUSES)
        total = len(submissions)
        if not submissions:
            self.db.update_batch(self.batch_id, status="completed", pause_reason="")
            self.batch_done.emit("completed")
            self.all_done.emit()
            return
        if not self.mailboxes:
            self.db.update_batch(
                self.batch_id, status="waiting", pause_reason="没有可用发件邮箱")
            self.batch_done.emit("waiting")
            self.all_done.emit()
            return

        queues = {mid: deque() for mid in self.mailbox_order}
        next_order = {mid: 0 for mid in self.mailbox_order}
        disabled: set[str] = set()
        for submission in submissions:
            eligible = [mid for mid in submission.allowed_mailbox_ids
                        if mid in self.mailboxes]
            chosen = submission.assigned_mailbox_id
            if chosen not in eligible:
                if not eligible:
                    self.db.mark_submission_waiting(
                        submission.id, "等待用户处理", "允许的发件邮箱均不可用")
                    continue
                chosen = min(eligible, key=lambda mid: (len(queues[mid]),
                                                        self.mailbox_order.index(mid)))
                self.db.update_submission_assignment(
                    submission.id, chosen, next_order[chosen], "待发")
            queues[chosen].append(submission)
            next_order[chosen] = max(next_order[chosen], submission.queue_order + 1)

        persisted = self.db.batch_mailbox_states(self.batch_id)
        due_at = {mid: self._now() for mid in self.mailbox_order}
        for mid, state in persisted.items():
            parsed = self._parse_time(state.get("next_send_at", ""))
            if mid in due_at and parsed and parsed > due_at[mid]:
                due_at[mid] = parsed
        for mid, queue in queues.items():
            if queue:
                parsed = self._parse_time(queue[0].next_attempt_at)
                if parsed and parsed > due_at[mid]:
                    due_at[mid] = parsed

        completed = 0

        def reassign(items, failed_mid: str, waiting_status: str, reason: str,
                     tomorrow: datetime | None = None):
            nonlocal queues
            for submission in items:
                choices = [mid for mid in submission.allowed_mailbox_ids
                           if mid in self.mailboxes and mid not in disabled
                           and mid != failed_mid]
                if choices:
                    chosen = min(choices, key=lambda mid: (
                        len(queues[mid]), self.mailbox_order.index(mid)))
                    self.db.update_submission_assignment(
                        submission.id, chosen, next_order[chosen], "待发")
                    submission.assigned_mailbox_id = chosen
                    submission.queue_order = next_order[chosen]
                    next_order[chosen] += 1
                    queues[chosen].append(submission)
                else:
                    next_text = tomorrow.strftime("%Y-%m-%d %H:%M:%S") if tomorrow else ""
                    self.db.mark_submission_waiting(
                        submission.id, waiting_status, reason, next_text)

        self.db.update_batch(self.batch_id, status="running", pause_reason="")
        with ThreadPoolExecutor(max_workers=max(1, len(self.mailboxes))) as pool:
            inflight = {}
            while (any(queues[mid] for mid in self.mailbox_order) or inflight):
                stopping = (self._pause_requested.is_set()
                            or self._cancel_requested.is_set())
                now = self._now()
                busy_mailboxes = {item[0] for item in inflight.values()}
                if not stopping:
                    ready = [mid for mid in self.mailbox_order
                             if mid not in disabled and mid not in busy_mailboxes
                             and queues[mid] and due_at[mid] <= now]
                    for mid in ready:
                        submission = queues[mid].popleft()
                        mailbox = self.mailboxes[mid]
                        self.progress.emit(
                            completed, total,
                            f"正在发送 {submission.to_email}（{mailbox.address}）")
                        future = pool.submit(self._attempt, mailbox, submission)
                        inflight[future] = (mid, mailbox, submission)

                if not inflight:
                    if stopping:
                        break
                    future_times = [due_at[mid] for mid in self.mailbox_order
                                    if mid not in disabled and queues[mid]]
                    if not future_times:
                        break
                    seconds = max(0.0, (min(future_times) - self._now()).total_seconds())
                    if not self._interruptible_wait(min(seconds, 1.0)):
                        break
                    continue

                done, _pending = wait_futures(
                    tuple(inflight), timeout=0.2, return_when=FIRST_COMPLETED)
                if not done:
                    # 等待中的 SMTP 不占用其他邮箱；下一轮会重新检查哪些邮箱已到点。
                    continue

                for future in done:
                    mid, mailbox, submission = inflight.pop(future)
                    try:
                        outcome, error = future.result()
                    except Exception as exc:
                        outcome, error = "mailbox", str(exc)

                    smtp_attempted = outcome != "limit"
                    if smtp_attempted:
                        interval = int(self._rng.randint(60, 180))
                        due_at[mid] = self._now() + timedelta(seconds=interval)
                        next_text = due_at[mid].strftime("%Y-%m-%d %H:%M:%S")
                        self.db.set_batch_mailbox_state(
                            self.batch_id, mid, "waiting", next_text, error)

                    if outcome == "sent":
                        self.db.update_status(submission.id, "已发", error="")
                        self.db.update_from_mailbox(
                            submission.id, mailbox.address, mailbox.mailbox_id)
                        completed += 1
                        self.item_done.emit(
                            submission.id, "已发", "", mailbox.address)
                    elif outcome == "temporary" and submission.attempt_count < 2:
                        # 仅保留一次受控重试；等待由调度器管理，不阻塞其他邮箱。
                        retry_text = due_at[mid].strftime("%Y-%m-%d %H:%M:%S")
                        self.db.mark_submission_waiting(
                            submission.id, "重试等待", error, retry_text)
                        submission.next_attempt_at = retry_text
                        queues[mid].appendleft(submission)
                    elif outcome == "paused":
                        self.db.update_submission_assignment(
                            submission.id, mid, submission.queue_order, "待发")
                    elif outcome == "recipient":
                        self.db.update_status(submission.id, "失败", error=error)
                        self.db.update_from_mailbox(
                            submission.id, mailbox.address, mailbox.mailbox_id)
                        completed += 1
                        self.item_done.emit(
                            submission.id, "失败", error, mailbox.address)
                    elif outcome == "limit":
                        tomorrow = self._next_tomorrow()
                        waiting = [submission, *list(queues[mid])]
                        queues[mid].clear()
                        reassign(waiting, mid, "等待限额", "本地每日保护上限已到", tomorrow)
                        self.db.set_batch_mailbox_state(
                            self.batch_id, mid, "daily_limit",
                            tomorrow.strftime("%Y-%m-%d %H:%M:%S"), error)
                    else:  # mailbox-level failure
                        disabled.add(mid)
                        waiting = [submission, *list(queues[mid])]
                        queues[mid].clear()
                        reassign(waiting, mid, "等待用户处理", error)
                        self.db.set_batch_mailbox_state(
                            self.batch_id, mid, "paused", "", error)
                        self.mailbox_paused.emit(mid, error)

                    self.progress.emit(completed, total,
                                       f"已处理 {completed}/{total} 封")

        if self._cancel_requested.is_set():
            self.db.cancel_batch(self.batch_id)
            final_state = "cancelled"
        elif self._pause_requested.is_set():
            self.db.update_batch(
                self.batch_id, status="paused", pause_reason="用户暂停")
            final_state = "paused"
        else:
            remaining = self.db.list_batch_submissions(
                self.batch_id, self.PENDING_STATUSES + ("结果待确认",))
            if any(s.status == "结果待确认" for s in remaining):
                final_state = "paused"
                reason = "存在发送结果待确认的邮件"
            elif remaining:
                final_state = "waiting"
                reason = "部分任务等待限额恢复或邮箱处理"
            else:
                final_state = "completed"
                reason = ""
            self.db.update_batch(self.batch_id, status=final_state, pause_reason=reason)
        self.batch_done.emit(final_state)
        self.all_done.emit()


class FetchWorker(QThread):
    """逐邮箱抓取回信，只读；可手动停止（当前邮箱受 IMAP 超时约束）。"""

    progress = Signal(str)
    mailbox_result = Signal(str, list)        # 邮箱地址, 结果 list
    mailbox_failed = Signal(str, str)         # 邮箱地址, 错误
    all_done = Signal()

    def __init__(self, mailboxes: list[MailboxConfig], editor_emails: set,
                 lookback_days: int, parent=None):
        super().__init__(parent)
        self.mailboxes = [m for m in mailboxes if m.enabled]
        self.editor_emails = editor_emails
        self.lookback_days = lookback_days
        self._stopped = False

    def stop(self):
        self._stopped = True

    def run(self):
        for m in self.mailboxes:
            if self._stopped:
                self.progress.emit("已手动停止收信")
                break
            self.progress.emit(f"正在检查 {m.address} 的收件箱……")
            try:
                results = receiver.fetch_replies(m, self.editor_emails, self.lookback_days)
                if self._stopped:
                    break
                self.mailbox_result.emit(m.address, results)
            except Exception as exc:
                message = str(exc)
                self.progress.emit(f"{m.address} 收信失败：{message}")
                self.mailbox_failed.emit(m.address, message)
        self.all_done.emit()


class SyncEditorsWorker(QThread):
    """后台同步最新编辑数据。"""

    finished = Signal(bool, str, dict)   # ok, message, stats

    def __init__(self, db, url: str, parent=None):
        super().__init__(parent)
        self.db = db
        self.url = url

    def run(self):
        try:
            result = updater.sync_from_url(self.db, self.url)
            msg = (f"同步完成：新增 {result['inserted']} 位，"
                   f"更新 {result['updated']} 位，本地共 {result['total']} 位"
                   + (f"（数据版本 {result.get('version', '')}）" if result.get("version") else ""))
            self.finished.emit(True, msg, result)
        except Exception as exc:
            self.finished.emit(False, f"同步失败：{exc}", {})


class UpdateCheckWorker(QThread):
    """后台检查新版本。result: (更新信息 dict | None, 错误消息 str)。"""

    result = Signal(object, str)

    def run(self):
        try:
            info = update_check.check_for_update()
            self.result.emit(info, "")
        except Exception as exc:
            self.result.emit(None, str(exc))


class ImportEditorsWorker(QThread):
    """后台解析 CSV 并批量入库。"""

    progress = Signal(int, int, str)
    finished_ok = Signal(int, int)
    failed = Signal(str)

    def __init__(self, db, path: str, parent=None):
        super().__init__(parent)
        self.db = db
        self.path = path
        self._stopped = False

    def stop(self):
        self._stopped = True

    def run(self):
        try:
            editors = self._parse(self.path)
        except Exception as exc:
            _log.warning("导入编辑 CSV 失败", exc_info=True)
            self.failed.emit(str(exc))
            return
        total = len(editors)
        imported = skipped = 0
        batch: list[Editor] = []
        for i, editor in enumerate(editors, 1):
            if self._stopped:
                break
            batch.append(editor)
            if len(batch) >= 200 or i == total:
                ok, skip = self.db.upsert_editors_bulk(batch)
                imported += ok
                skipped += skip
                batch = []
            self.progress.emit(i, max(total, 1), editor.email or editor.name or "")
        self.finished_ok.emit(imported, skipped)

    @staticmethod
    def _parse(path: str) -> list[Editor]:
        with open(path, "rb") as f:
            raw = f.read()
        text = None
        for enc in ("utf-8-sig", "gbk"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("无法识别文件编码")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValueError("文件为空或缺少列头")
        header_map: dict[str, str] = {}
        for field in reader.fieldnames:
            key = (field or "").strip().lower()
            for attr, aliases in _EDITOR_CSV_ALIASES.items():
                if key in aliases:
                    header_map[attr] = field
        editors: list[Editor] = []
        for row in reader:
            email = (row.get(header_map.get("email", ""), "") or "").strip()
            editors.append(Editor(
                name=(row.get(header_map.get("name", ""), "") or "").strip() or email,
                platform=(row.get(header_map.get("platform", ""), "") or "").strip(),
                email=email,
                genres=(row.get(header_map.get("genres", ""), "") or "").strip(),
                directions=(row.get(header_map.get("directions", ""), "") or "").strip(),
                status=(row.get(header_map.get("status", ""), "") or "").strip(),
                fee_info=(row.get(header_map.get("fee_info", ""), "") or "").strip(),
                source_url=(row.get(header_map.get("source_url", ""), "") or "").strip(),
                notes=(row.get(header_map.get("notes", ""), "") or "").strip(),
            ))
        return editors


class ImportManuscriptsWorker(QThread):
    """后台读取文稿、复制到 files/ 并入库。"""

    progress = Signal(int, int, str)
    finished_ok = Signal(int, int, object)
    failed = Signal(str)

    def __init__(self, db, paths: list[str], parent=None):
        super().__init__(parent)
        self.db = db
        self.paths = list(paths)
        self._stopped = False

    def stop(self):
        self._stopped = True

    def run(self):
        from .docx_reader import read_document_stats
        ok = failed = 0
        errors: list[str] = []
        total = len(self.paths)
        for i, path in enumerate(self.paths, 1):
            if self._stopped:
                break
            name = os.path.basename(path)
            self.progress.emit(i, max(total, 1), name)
            try:
                stats = read_document_stats(path)
                copied = _copy_to_files_dir(self.db.files_dir, path)
                self.db.insert_manuscript(Manuscript(
                    title=os.path.splitext(name)[0], file_path=copied,
                    word_count=stats.word_count, word_count_source=stats.source))
                ok += 1
            except Exception as exc:
                _log.warning("导入文稿失败 %s", path, exc_info=True)
                failed += 1
                errors.append(f"{name}：{exc}")
        self.finished_ok.emit(ok, failed, errors)


class DownloadUpdateWorker(QThread):
    """后台下载安装包到本地临时目录。"""

    progress = Signal(int, int)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, url: str, dest_path: str, parent=None):
        super().__init__(parent)
        self.url = url
        self.dest_path = dest_path
        self._stopped = False

    def stop(self):
        self._stopped = True

    def run(self):
        import urllib.error
        import urllib.request
        tmp_path = self.dest_path + ".part"
        try:
            req = urllib.request.Request(
                self.url, headers={"User-Agent": "NailongPost-Updater/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                received = 0
                os.makedirs(os.path.dirname(self.dest_path) or ".", exist_ok=True)
                with open(tmp_path, "wb") as f:
                    while True:
                        if self._stopped:
                            try:
                                f.close()
                                os.remove(tmp_path)
                            except OSError:
                                pass
                            self.failed.emit("已取消下载")
                            return
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        received += len(chunk)
                        self.progress.emit(received, total)
            if total and received != total:
                self.failed.emit(f"文件大小不一致（{received}/{total}）")
                return
            os.replace(tmp_path, self.dest_path)
            _log.info("更新包已下载 %s (%s 字节)", self.dest_path, received)
            self.finished_ok.emit(self.dest_path)
        except Exception as exc:
            _log.warning("下载更新失败", exc_info=True)
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            if isinstance(exc, urllib.error.URLError):
                self.failed.emit(f"网络错误：{exc.reason}")
            else:
                self.failed.emit(str(exc))


def _copy_to_files_dir(files_dir: str, src: str) -> str:
    base = os.path.basename(src)
    stem, ext = os.path.splitext(base)
    dst = os.path.join(files_dir, base)
    i = 1
    while os.path.exists(dst):
        dst = os.path.join(files_dir, f"{stem}_{i}{ext}")
        i += 1
    shutil.copy2(src, dst)
    return dst
