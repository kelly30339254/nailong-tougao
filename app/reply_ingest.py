"""收信结果写库的公共逻辑（dashboard 后台收信与回信中心"立即收信"共用）。"""
from __future__ import annotations

from dataclasses import dataclass

from .db import Database
from .models import Reply
from .classifier import classify_reply


@dataclass
class IngestResult:
    new_replies: int = 0      # 新插入的回信数
    invalid_marks: int = 0    # 退信导致的失效标记数


def ingest_results(db: Database, _mailbox_address: str, results: list) -> IngestResult:
    """把 FetchWorker 抓到的邮件写库。

    - 退信条目（is_bounce）：对 bounced_recipients 里的每个邮箱置 editors.email_invalid=1，
      不作为回信插入 replies 表
    - 普通来信：按 imap_uid + from_email 去重（db.insert_reply 内部处理）；
      verdict = classify_reply(主题 + 摘要)；匹配规则：replies.from_email ==
      submissions.to_email 且该 submission status=已发、reply_status=无，取最近一条；
      verdict 为 "其他" 不回写 submission
    """
    out = IngestResult()
    pending = [s for s in db.list_submissions()
               if s.status == "已发" and s.reply_status == "无"]
    for r in results:
        if r.get("is_bounce"):
            for addr in r.get("bounced_recipients") or []:
                out.invalid_marks += db.mark_email_invalid(addr)
            continue
        from_email = (r.get("from_email") or "").lower()
        text = (r.get("subject") or "") + " " + (r.get("snippet") or "")
        verdict = classify_reply(text)
        match = None
        for s in pending:
            if (s.to_email or "").lower() == from_email:
                if match is None or (s.id or 0) > (match.id or 0):
                    match = s
        reply = Reply(
            submission_id=match.id if match else None,
            from_email=from_email,
            subject=r.get("subject") or "",
            snippet=r.get("snippet") or "",
            verdict=verdict,
            imap_uid=r.get("uid") or "",
            received_at=r.get("received_at") or "",
        )
        rid = db.insert_reply(reply)
        if rid is None:
            continue
        out.new_replies += 1
        if match is not None and verdict != "其他":
            db.update_reply_status(match.id, verdict)
            pending.remove(match)  # 该投稿已判定，不再匹配后续回信
    return out
