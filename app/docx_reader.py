"""txt / docx 正文读取与字数统计。"""
from __future__ import annotations

import re

_CJK_RE = re.compile(r"[一-鿿㐀-䶿豈-﫿]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’\-][A-Za-z0-9]+)*")


def read_docx_text(path: str) -> str:
    """用 python-docx 读取 docx 全部段落文本。"""
    import docx  # python-docx
    document = docx.Document(path)
    return "\n".join(p.text for p in document.paragraphs)


def read_txt(path: str) -> str:
    """utf-8 优先，失败回退 gbk。"""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def count_cjk_words(text: str) -> int:
    """CJK 字符数 + 连续拉丁词数。"""
    cjk = len(_CJK_RE.findall(text))
    latin = len(_LATIN_WORD_RE.findall(text))
    return cjk + latin
