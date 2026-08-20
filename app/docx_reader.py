"""TXT / DOCX 正文读取与兼容 Word 的字数来源识别。"""
from __future__ import annotations

from dataclasses import dataclass
import re
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’\-][A-Za-z0-9]+)*")
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_EP_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}"

WORD_COUNT_SOURCE_LABELS = {
    "word_saved": "Word 最后保存值",
    "compatible": "兼容统计",
    "manual": "手动",
}


@dataclass(frozen=True)
class DocumentStats:
    text: str
    word_count: int
    source: str

    @property
    def source_label(self) -> str:
        return WORD_COUNT_SOURCE_LABELS.get(self.source, self.source or "未知")


def _document_xml_text(xml_data: bytes) -> str:
    """按正文 XML 顺序提取文本；表格单元格位于 document.xml，亦会包含。"""
    root = ElementTree.fromstring(xml_data)
    parts: list[str] = []
    for element in root.iter():
        if element.tag == _W_NS + "t" and element.text:
            parts.append(element.text)
        elif element.tag == _W_NS + "tab":
            parts.append("\t")
        elif element.tag in (_W_NS + "br", _W_NS + "cr"):
            parts.append("\n")
        elif element.tag in (_W_NS + "p", _W_NS + "tr"):
            parts.append("\n")
    return "".join(parts).strip()


def _saved_word_count(archive: ZipFile) -> int | None:
    try:
        root = ElementTree.fromstring(archive.read("docProps/app.xml"))
    except (KeyError, ElementTree.ParseError):
        return None
    node = root.find(_EP_NS + "Words")
    if node is None or not (node.text or "").strip():
        return None
    try:
        value = int((node.text or "").strip())
    except ValueError:
        return None
    # 格式无法判断非零统计是否陈旧；仅排除空、负数、零和溢出式异常值。
    return value if 0 < value < 1_000_000_000 else None


def read_docx_stats(path: str) -> DocumentStats:
    try:
        with ZipFile(path) as archive:
            try:
                text = _document_xml_text(archive.read("word/document.xml"))
            except KeyError as exc:
                raise ValueError("DOCX 缺少正文 XML") from exc
            saved = _saved_word_count(archive)
    except BadZipFile as exc:
        raise ValueError("文件不是有效的 DOCX") from exc
    if saved is not None:
        return DocumentStats(text=text, word_count=saved, source="word_saved")
    return DocumentStats(text=text, word_count=count_cjk_words(text), source="compatible")


def read_docx_text(path: str) -> str:
    return read_docx_stats(path).text


def read_txt(path: str) -> str:
    """UTF-8 优先，失败回退 GB18030。"""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def count_cjk_words(text: str) -> int:
    """兼容统计：CJK 字符数 + 连续拉丁/数字词数。"""
    cjk = len(_CJK_RE.findall(text or ""))
    latin = len(_LATIN_WORD_RE.findall(text or ""))
    return cjk + latin


def read_document_stats(path: str) -> DocumentStats:
    if str(path).lower().endswith(".docx"):
        return read_docx_stats(path)
    text = read_txt(path)
    return DocumentStats(text=text, word_count=count_cjk_words(text), source="compatible")
