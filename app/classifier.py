"""回信关键词分类：过稿 / 退稿 / 需修改 / 其他。"""

_PASS_WORDS = ("采用", "过稿", "留用", "录用", "终审通过", "签约")
_REJECT_WORDS = ("退稿", "不采用", "未通过", "遗憾", "不适合")
_REVISE_WORDS = ("修改", "改稿", "润色", "返修")


def classify_reply(text: str) -> str:
    """优先级：需修改 > 过稿 > 退稿 > 其他。"""
    text = text or ""
    if any(w in text for w in _REVISE_WORDS):
        return "需修改"
    if any(w in text for w in _PASS_WORDS):
        return "过稿"
    if any(w in text for w in _REJECT_WORDS):
        return "退稿"
    return "其他"
