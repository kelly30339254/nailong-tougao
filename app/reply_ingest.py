"""收信结果写库的公共逻辑。"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .classifier import classify_reply_details
from .db import Database
from .models import Reply


@dataclass
class IngestResult:
    new_replies: int = 0
    invalid_marks: int = 0


def _thread_message_ids(*header_values: str) -> list[str]:
    values: list[str] = []
    for header in header_values:
        values.extend(re.findall(r"<[^<>]+>", header or ""))
    return list(dict.fromkeys(values))


def ingest_results(db: Database, mailbox_address: str, results: list) -> IngestResult:
    """写入退信/回信；只有明确且唯一的高置信结果才更新投稿状态。"""
    out = IngestResult()
    for item in results:
        if item.get("is_bounce"):
            for address in item.get("bounced_recipients") or []:
                out.invalid_marks += db.mark_email_invalid(address)
            continue

        from_email = (item.get("from_email") or "").lower()
        is_auto_reply = bool(item.get("is_auto_reply", False))
        if is_auto_reply:
            verdict, confidence, reason = (
                "自动回复", "high", "邮件头或主题表明这是自动回复")
        else:
            text = (item.get("subject") or "") + " " + (item.get("snippet") or "")
            verdict, confidence, reason = classify_reply_details(text)

        referenced_ids = _thread_message_ids(
            item.get("in_reply_to") or "", item.get("references") or "")
        match = db.find_submission_for_reply(
            from_email, mailbox_address, referenced_ids)
        reply = Reply(
            submission_id=match.id if match else None,
            from_email=from_email,
            subject=item.get("subject") or "",
            snippet=item.get("snippet") or "",
            body_full=item.get("body_full") or "",
            verdict=verdict,
            imap_uid=item.get("uid") or "",
            mailbox_address=mailbox_address,
            imap_folder=item.get("imap_folder") or "INBOX",
            uid_validity=item.get("uid_validity") or "",
            message_id=item.get("message_id") or "",
            in_reply_to=item.get("in_reply_to") or "",
            references=item.get("references") or "",
            is_auto_reply=is_auto_reply,
            classification_confidence=confidence,
            classification_reason=reason,
            received_at=item.get("received_at") or "",
        )
        reply_id = db.insert_reply(reply)
        if reply_id is None:
            continue
        out.new_replies += 1
        if (match is not None and confidence == "high"
                and verdict in {"过稿", "退稿", "需修改"}):
            db.update_reply_status(match.id, verdict)
    return out
