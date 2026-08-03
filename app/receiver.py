"""IMAP 只读取信：匹配编辑列表发件人，绝不删邮件、不写标记。"""
from __future__ import annotations

import imaplib
import re
from datetime import datetime, timedelta
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

from .models import MailboxConfig

_TIMEOUT = 30

# 退信通知识别：From 含这些标识，或主题含退信关键词
_BOUNCE_FROM_HINTS = ("mailer-daemon", "postmaster")
_BOUNCE_SUBJECT_WORDS = ("退信", "Delivery Status", "Undelivered", "无法投递", "投递失败")

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _is_bounce(from_email: str, subject: str) -> bool:
    if any(h in from_email for h in _BOUNCE_FROM_HINTS):
        return True
    return any(w in subject for w in _BOUNCE_SUBJECT_WORDS)


def _decode_str(value: str | None) -> str:
    """RFC2047 解码头部字段。"""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _extract_address(from_header: str) -> str:
    return parseaddr(_decode_str(from_header))[1].strip().lower()


def _strip_html(html: str) -> str:
    text = re.sub(r"<style.*?</style>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_body(msg) -> str:
    plain, html = "", ""
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        ctype = part.get_content_type()
        disp = str(part.get("Content-Disposition") or "")
        if "attachment" in disp.lower():
            continue
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
        except Exception:
            continue
        if ctype == "text/plain" and not plain:
            plain = text
        elif ctype == "text/html" and not html:
            html = text
    if plain.strip():
        return plain
    return _strip_html(html)


def fetch_replies(mailbox: MailboxConfig, editor_emails: set, lookback_days: int) -> list[dict]:
    """只读抓取最近 lookback_days 天内的相关邮件。

    返回 list[dict]，键：from_email, subject, snippet, uid, received_at（ISO 字符串）、
    is_bounce、bounced_recipients（退信正文里属于编辑列表的被退邮箱，普通来信为空列表）。
    保留编辑来信；退信通知（mailer-daemon/postmaster 或退信类主题）也抓回用于失效标记。
    使用 readonly select 且不调用 store，不删邮件、不写已读标记。
    """
    editor_emails = {e.strip().lower() for e in editor_emails if e and e.strip()}
    results: list[dict] = []
    if not editor_emails:
        return results

    since = (datetime.now() - timedelta(days=lookback_days)).strftime("%d-%b-%Y")
    imap = imaplib.IMAP4_SSL(mailbox.imap_host, mailbox.imap_port)
    try:
        imap.login(mailbox.address, mailbox.auth_code)
        imap.select("INBOX", readonly=True)
        typ, data = imap.search(None, "SINCE", since)
        if typ != "OK" or not data or not data[0]:
            return results
        for num in data[0].split():
            typ, msg_data = imap.fetch(num, "(UID RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            uid = ""
            head = msg_data[0][0]
            if isinstance(head, bytes):
                m = re.search(rb"UID\s+(\d+)", head)
                if m:
                    uid = m.group(1).decode()
            try:
                msg = message_from_bytes(raw)
            except Exception:
                continue
            from_email = _extract_address(msg.get("From", ""))
            subject = _decode_str(msg.get("Subject", ""))
            body = _extract_body(msg)
            snippet = re.sub(r"\s+", " ", body).strip()[:300]
            bounce = _is_bounce(from_email, subject)
            if not bounce and from_email not in editor_emails:
                continue
            received_at = ""
            date_hdr = msg.get("Date", "")
            if date_hdr:
                try:
                    received_at = parsedate_to_datetime(date_hdr).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    received_at = ""
            # 退信：从正文提取属于编辑列表的被退邮箱
            bounced: list[str] = []
            if bounce:
                found = {addr.lower() for addr in _EMAIL_RE.findall(body)}
                bounced = sorted(found & editor_emails)
            results.append({
                "from_email": from_email,
                "subject": subject,
                "snippet": snippet,
                "uid": uid,
                "received_at": received_at,
                "is_bounce": bounce,
                "bounced_recipients": bounced,
            })
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return results
