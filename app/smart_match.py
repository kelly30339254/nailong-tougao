"""原版智选：按稿件标签与编辑资料做加权匹配（不调用网络）。"""
from __future__ import annotations

import re
from typing import Any

_SPLIT = re.compile(r"[、,，/|；;+＋\s]+")

# 近义归并：查询词命中同组任一即算命中（避免只认字面相等）
_SYNONYM_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"悬疑", "推理", "刑侦", "破案", "侦探", "罪案", "密室"}),
    frozenset({"言情", "甜宠", "虐恋", "恋爱", "爱情", "甜文", "虐文", "现言", "古言"}),
    frozenset({"世情", "现实", "生活", "人间"}),
    frozenset({"古言", "古代", "穿越", "宫斗", "宅斗"}),
    frozenset({"玄幻", "修仙", "仙侠", "奇幻", "修真"}),
    frozenset({"科幻", "末世", "未来", "机甲"}),
    frozenset({"惊悚", "恐怖", "灵异", "鬼怪"}),
    frozenset({"爽文", "爽", "打脸", "逆袭", "快意"}),
    frozenset({"女频", "女主", "女向", "女频向"}),
    frozenset({"男频", "男主", "男向", "男频向"}),
    frozenset({"短篇", "中短篇"}),
    frozenset({"长篇", "连载"}),
    frozenset({"短剧", "竖屏", "漫剧"}),
    frozenset({"第一人称", "第一视角", "第一人称视角"}),
    frozenset({"第三人称", "第三视角"}),
)


def split_tokens(text: str) -> list[str]:
    return [part.strip().lower() for part in _SPLIT.split(text or "") if len(part.strip()) >= 2]


def _expand(token: str) -> set[str]:
    aliases = {token}
    for group in _SYNONYM_GROUPS:
        if token in group:
            aliases |= set(group)
    return aliases


def _match_field(query: str, target: str, weight: int) -> tuple[int, list[str]]:
    """查询词对目标文本打分。整词命中满分，仅近义/子串命中给半权。"""
    tokens = split_tokens(query)
    target_tokens = set(split_tokens(target))
    target_raw = (target or "").lower()
    score = 0
    hits: list[str] = []
    for token in tokens:
        aliases = _expand(token)
        exact = [alias for alias in aliases if alias in target_tokens]
        if exact:
            score += weight
            hits.append(token)
            continue
        # 「短篇」不要误伤「超短篇」：要求子串两侧不像汉字续写
        soft = [alias for alias in aliases if alias in target_raw and _standalone(alias, target_raw)]
        if soft:
            score += max(1, weight // 2)
            hits.append(token)
    return score, hits


def _standalone(needle: str, haystack: str) -> bool:
    idx = haystack.find(needle)
    if idx < 0:
        return False
    before = haystack[idx - 1] if idx > 0 else ""
    after = haystack[idx + len(needle)] if idx + len(needle) < len(haystack) else ""
    # 被「超/微」等修饰紧贴时降级为不算独立篇幅
    if before in "超微小极特":
        return False
    if after in "剧集章部":
        return needle in {"短", "长"}  # 几乎不会走到
    return True


def manuscript_query(manuscript, category: str, genre_type: str,
                     reader_group: str, emotion: str, style: str) -> dict[str, str]:
    if manuscript is not None:
        return {
            "category": manuscript.category or "",
            "genre_type": manuscript.genre_type or "",
            "reader_group": manuscript.reader_group or "",
            "emotion": manuscript.emotion or "",
            "style": manuscript.style or "",
            "title": manuscript.title or "",
            "word_count": str(manuscript.word_count or ""),
        }
    return {
        "category": category or "",
        "genre_type": genre_type or "",
        "reader_group": reader_group or "",
        "emotion": emotion or "",
        "style": style or "",
        "title": "",
        "word_count": "",
    }


def match_editor(editor, query: dict[str, str]) -> tuple[int, str]:
    """返回 (分数, 原因)。停止收稿仍计分，由调用方决定是否下沉。"""
    genres = editor.genres or ""
    directions = editor.directions or ""
    notes = editor.notes or ""
    status = (editor.status or "").strip()
    fields = (
        (query.get("category", ""), f"{directions} {genres}", 5, "题材"),
        (query.get("genre_type", ""), genres or directions, 4, "篇幅"),
        (query.get("reader_group", ""), f"{directions} {genres} {notes}", 2, "读者群"),
        (query.get("emotion", ""), f"{directions} {notes}", 2, "情绪"),
        (query.get("style", ""), f"{directions} {notes}", 2, "风格"),
    )
    score = 0
    reasons: list[str] = []
    has_tags = False
    for value, target, weight, label in fields:
        tokens = split_tokens(value)
        has_tags = has_tags or bool(tokens)
        part_score, hits = _match_field(value, target, weight)
        if hits:
            score += part_score
            reasons.append(f"{label}：{'、'.join(dict.fromkeys(hits))}")
    if status.startswith("正常收稿"):
        score += 1
        reasons.append("正在收稿")
    elif status.startswith("停止收稿"):
        score -= 8
        reasons.append("已停收")
    if not has_tags:
        return max(score, 0), "稿件标签不足"
    if not reasons:
        return 0, "暂无明确匹配"
    return max(score, 0), "；".join(reasons)


def sort_key(editor, score: int) -> tuple:
    status = (editor.status or "").strip()
    stopped = 1 if status.startswith("停止收稿") else 0
    accepting = 0 if status.startswith("正常收稿") else 1
    return (stopped, -score, accepting, editor.name or "")


def editor_payload(editor) -> dict[str, Any]:
    return {
        "id": editor.id,
        "name": editor.name or "",
        "platform": editor.platform or "",
        "genres": editor.genres or "",
        "directions": (editor.directions or "")[:80],
        "status": editor.status or "",
        "notes": (editor.notes or "")[:60],
    }
