"""后台线程（QThread）：主线程做 DB 写入，worker 只 emit 数据。"""
from __future__ import annotations

import time

from PySide6.QtCore import QThread, Signal

from .models import MailboxConfig
from . import mailer, receiver


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
                 interval_seconds: int, parent=None):
        super().__init__(parent)
        # 只保留已启用的邮箱
        self.mailboxes = [m for m in mailboxes if m.enabled]
        self.jobs = jobs
        self.interval_seconds = interval_seconds
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
            mailbox = self.mailboxes[i % len(self.mailboxes)]
            sid = job.get("submission_id", -1)
            to = job.get("to", "")
            self.progress.emit(i + 1, total, f"正在发送 {to}（{mailbox.address}）")
            try:
                mailer.send_mail(mailbox, to, job.get("subject", ""), job.get("body", ""),
                                 job.get("attachment_path") or None)
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
                self.progress.emit(f"{m.address} 收信失败：{exc}")
                self.mailbox_result.emit(m.address, [])
        self.all_done.emit()
