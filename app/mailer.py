"""SMTP 发信与邮箱连通性测试。"""
from __future__ import annotations

import os
import smtplib
import imaplib
from email.header import Header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from email.utils import formataddr, make_msgid

from .models import MailboxConfig

_TIMEOUT = 30


def _display_name(mailbox: MailboxConfig) -> str:
    if mailbox.display_name.strip():
        return mailbox.display_name.strip()
    # 留空用邮箱前缀
    return mailbox.address.split("@")[0] if "@" in mailbox.address else mailbox.address


def send_mail(mailbox: MailboxConfig, to: str, subject: str, body: str,
              attachment_path: str | None = None,
              message_id: str | None = None) -> str:
    """发送邮件，异常向上抛出。"""
    from_addr = formataddr((str(Header(_display_name(mailbox), "utf-8")), mailbox.address))

    if attachment_path:
        msg = MIMEMultipart()
        msg.attach(MIMEText(body, "plain", "utf-8"))
        filename = os.path.basename(attachment_path)
        with open(attachment_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        # RFC2231 编码附件文件名，避免中文乱码
        part.add_header("Content-Disposition", "attachment", filename=("utf-8", "", filename))
        msg.attach(part)
    else:
        msg = MIMEText(body, "plain", "utf-8")

    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = Header(subject, "utf-8")
    domain = mailbox.address.rsplit("@", 1)[-1] if "@" in mailbox.address else None
    actual_message_id = (message_id or "").strip() or make_msgid(domain=domain)
    msg["Message-ID"] = actual_message_id

    if mailbox.smtp_ssl:
        server = smtplib.SMTP_SSL(mailbox.smtp_host, mailbox.smtp_port, timeout=_TIMEOUT)
    else:
        server = smtplib.SMTP(mailbox.smtp_host, mailbox.smtp_port, timeout=_TIMEOUT)
        server.ehlo()
        server.starttls()
        server.ehlo()
    try:
        server.login(mailbox.address, mailbox.auth_code)
        server.sendmail(mailbox.address, [to], msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return actual_message_id


def _friendly_error(exc: Exception) -> str:
    text = str(exc)
    if "535" in text or "Authentication" in text or "authentication failed" in text.lower():
        return "登录失败：邮箱地址或授权码错误（注意填授权码，不是登录密码）"
    if "getaddrinfo" in text or "Name or service not known" in text or "11001" in text:
        return "无法连接服务器：请检查主机地址和网络"
    if "timed out" in text or "timeout" in text.lower():
        return "连接超时：请检查主机、端口或网络"
    if "SSL" in text or "ssl" in text:
        return "SSL 连接失败：请检查端口与 SSL 设置是否匹配"
    return f"失败：{text}"


def test_mailbox(mailbox: MailboxConfig) -> tuple[bool, str]:
    """依次测试 SMTP 登录和 IMAP 登录，返回 (ok, message)。"""
    if not mailbox.address or not mailbox.auth_code:
        return False, "请先填写邮箱地址和授权码"
    if not mailbox.smtp_host:
        return False, "请先填写 SMTP 主机"

    # SMTP
    try:
        if mailbox.smtp_ssl:
            server = smtplib.SMTP_SSL(mailbox.smtp_host, mailbox.smtp_port, timeout=_TIMEOUT)
        else:
            server = smtplib.SMTP(mailbox.smtp_host, mailbox.smtp_port, timeout=_TIMEOUT)
            server.ehlo()
            server.starttls()
            server.ehlo()
        try:
            server.login(mailbox.address, mailbox.auth_code)
        finally:
            try:
                server.quit()
            except Exception:
                pass
    except Exception as exc:
        return False, "SMTP " + _friendly_error(exc)

    # IMAP
    if not mailbox.imap_host:
        return False, "SMTP 登录成功；但未填写 IMAP 主机，无法收信"
    try:
        imap = imaplib.IMAP4_SSL(mailbox.imap_host, mailbox.imap_port, timeout=15)
        try:
            imap.login(mailbox.address, mailbox.auth_code)
        finally:
            try:
                imap.logout()
            except Exception:
                pass
    except Exception as exc:
        return False, "SMTP 登录成功；IMAP " + _friendly_error(exc)

    return True, "SMTP 与 IMAP 均连接成功"
