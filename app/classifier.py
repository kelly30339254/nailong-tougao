"""回信分类：明确结果自动归类，模糊表述留给用户确认。"""
from __future__ import annotations

_REVISE_WORDS = ("修改", "改稿", "润色", "返修")
_REJECT_WORDS = ("退稿", "不采用", "未通过", "不合适", "很遗憾")
_PASS_WORDS = (
    "采用", "录用", "留用", "刊用", "终审通过", "已通过审核",
    "通过终审", "请提供收款信息", "请签约",
)
_AMBIGUOUS_PASS_PHRASES = (
    "更容易过稿", "如何过稿", "过稿要求", "未过稿", "不代表过稿",
    "过稿率", "过稿概率",
)


def classify_reply_details(text: str) -> tuple[str, str, str]:
    """返回 (判定, 置信度, 依据)；低置信结果不得自动更新投稿状态。"""
    text = text or ""
    if any(word in text for word in _REVISE_WORDS):
        return "需修改", "high", "包含明确修改请求"
    if any(word in text for word in _REJECT_WORDS):
        return "退稿", "high", "包含明确退稿表述"
    if any(phrase in text for phrase in _AMBIGUOUS_PASS_PHRASES):
        return "待确认", "low", "仅讨论过稿或包含否定/条件语境"
    if any(word in text for word in _PASS_WORDS):
        return "过稿", "high", "包含明确采用表述"
    if "过稿" in text:
        return "待确认", "low", "仅命中过稿一词，语境不明确"
    return "其他", "high", "未命中明确结果"


def classify_reply(text: str) -> str:
    """兼容旧调用，仅返回判定。"""
    return classify_reply_details(text)[0]
