"""后台线程（QThread）：网络和耗时任务不阻塞界面。"""
from __future__ import annotations

import time

from PySide6.QtCore import QThread, Signal

from .models import MailboxConfig
from . import mailer, receiver, updater, update_check


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
    item_done = Signal(int, bool, str, str)   # submission_id, ok, error, 实际发信邮箱地址
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
                self.item_done.emit(job.get("submission_id", -1), False, "无已启用邮箱", "")
            self.all_done.emit()
            return

        for i, job in enumerate(self.jobs):
            if self._stopped:
                self.progress.emit(i, total, "已手动停止")
                break
            sid = job.get("submission_id", -1)
            to = job.get("to", "")
            mailbox = self.mailboxes[i % len(self.mailboxes)]
            try:
                if self.db is not None:
                    selected = None
                    start = i % len(self.mailboxes)
                    for offset in range(len(self.mailboxes)):
                        candidate = self.mailboxes[(start + offset) % len(self.mailboxes)]
                        if self.db.reserve_daily_send(
                                sid, candidate.address, candidate.daily_limit):
                            selected = candidate
                            break
                    if selected is None:
                        message = "所有启用邮箱今日额度已用完或任务状态已变化"
                        self.progress.emit(i + 1, total, f"跳过 {to}：{message}")
                        self.item_done.emit(sid, False, message, "")
                        continue
                    mailbox = selected
                self.progress.emit(i + 1, total, f"正在发送 {to}（{mailbox.address}）")
                send_kwargs = {}
                if job.get("message_id"):
                    send_kwargs["message_id"] = job["message_id"]
                mailer.send_mail(
                    mailbox, to, job.get("subject", ""), job.get("body", ""),
                    job.get("attachment_path") or None, **send_kwargs)
                self.item_done.emit(sid, True, "", mailbox.address)
            except Exception as exc:
                self.item_done.emit(sid, False, str(exc), mailbox.address)
            # 发信间隔（可被 stop 打断），最后一封不必等
            if i < total - 1 and not self._stopped:
                deadline = time.time() + self.interval_seconds
                while time.time() < deadline and not self._stopped:
                    time.sleep(0.1)
        self.all_done.emit()


class FetchWorker(QThread):
    """逐邮箱抓取回信，只读。"""

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

    def run(self):
        for m in self.mailboxes:
            self.progress.emit(f"正在检查 {m.address} 的收件箱……")
            try:
                results = receiver.fetch_replies(m, self.editor_emails, self.lookback_days)
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
