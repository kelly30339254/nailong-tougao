"""投稿信模板生成（本地模板，无 AI）。模板可由用户在设置页自定义。"""
from __future__ import annotations

import hashlib
import random
import re

DEFAULT_SUBJECT_TPL = "投稿《{作品名}》{字数}字 {分类}"

DEFAULT_BODY_TPL = (
    "尊敬的{编辑称呼}编辑：\n"
    "\n"
    "    您好！\n"
    "\n"
    "    冒昧来信，向您自荐我的作品《{作品名}》。本篇全文约{字数}字，分类为{分类}。"
    "稿件完整内容请见邮件附件，期待您的审阅。\n"
    "\n"
    "    祝工作顺利，万事顺意！\n"
)

DEFAULT_URGE_SUBJECT = "请问《{作品名}》审稿进度（{原投日期}已投）"
DEFAULT_URGE_BODY = (
    "尊敬的{编辑称呼}编辑：\n\n"
    "    您好！此前于{原投日期}向您投递《{作品名}》（约{字数}字，{分类}），"
    "冒昧打扰想请问是否已安排审阅。若仍在队列中，我再耐心等候。\n\n"
    "    祝工作顺利！\n"
)

PLACEHOLDER_HINT = (
    "可用占位符：{编辑称呼} {作品名} {字数} {分类}。"
    "每封微调：{变:措辞A|措辞B|措辞C}（自动发信时按编辑轮换，避免正文完全相同）。"
    "不认识的 {占位符} 会原样保留。"
)

_VARIANT_RE = re.compile(r"\{变:([^}]*)\}")

# 只替换客套话，不动《书名》和数字。长的写在前面，避免短句先吃掉长句。
_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("稿件完整内容请见邮件附件", "完整稿件请见附件", "全文请见本邮件附件", "正文详见附件"),
    ("冒昧来信，向您自荐", "特来信向您自荐", "写信向您推荐", "冒昧打扰，向您自荐"),
    ("期待您的审阅", "盼您拨冗审阅", "恳请您审阅", "期待您抽空审阅"),
    ("祝工作顺利，万事顺意！", "祝工作顺利！", "顺颂编安！", "祝编安，万事顺意！"),
    ("向您自荐我的作品", "向您投稿我的作品", "呈上我的作品"),
    ("本篇全文约", "本稿约", "全文约", "本篇约"),
    ("分类为", "题材为"),
)


def render_template(tpl: str, mapping: dict) -> str:
    """替换已知占位符；不认识的 {xxx} 原样保留，不报错。"""
    out = tpl
    for key, value in mapping.items():
        out = out.replace("{" + key + "}", str(value))
    return out


def build_letter(title: str, word_count, category: str, editor_name: str,
                 subject_tpl: str | None = None,
                 body_tpl: str | None = None) -> tuple[str, str]:
    """返回 (subject, body)。模板为空时用默认模板。"""
    mapping = {"编辑称呼": editor_name, "作品名": title,
               "字数": word_count, "分类": category}
    subject = render_template(subject_tpl or DEFAULT_SUBJECT_TPL, mapping)
    body = render_template(body_tpl or DEFAULT_BODY_TPL, mapping)
    return subject, body


def personalize_letter(subject: str, body: str, editor_name: str) -> tuple[str, str]:
    """只做真实称呼个性化，不随机改写作品事实或插入干扰字符。"""
    name = (editor_name or "").strip() or "老师"
    placeholder = "{编辑称呼}"
    had_placeholder = placeholder in subject or placeholder in body
    subject = subject.replace(placeholder, name)
    body = body.replace(placeholder, name)
    if not had_placeholder and editor_name.strip():
        body = f"{name}，您好：\n\n{body}"
    return subject, body


def _rng_from_seed(seed: str) -> random.Random:
    digest = hashlib.sha256((seed or "0").encode("utf-8", errors="replace")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def apply_variant_slots(text: str, rng: random.Random) -> str:
    """展开 {变:A|B|C}，按 rng 选一项。"""
    def repl(match: re.Match) -> str:
        options = [part.strip() for part in match.group(1).split("|") if part.strip()]
        if not options:
            return ""
        return rng.choice(options)
    return _VARIANT_RE.sub(repl, text)


def _apply_synonyms(text: str, rng: random.Random) -> str:
    protected: list[str] = []

    def stash(match: re.Match) -> str:
        protected.append(match.group(0))
        return f"\x00{len(protected) - 1}\x00"

    work = re.sub(r"《[^》]*》", stash, text)
    for group in _SYNONYM_GROUPS:
        found_at = -1
        found_phrase = ""
        for phrase in group:
            idx = work.find(phrase)
            if idx >= 0 and (found_at < 0 or idx < found_at):
                found_at = idx
                found_phrase = phrase
        if found_at < 0:
            continue
        replacement = rng.choice(group)
        work = work[:found_at] + replacement + work[found_at + len(found_phrase):]
    for i, original in enumerate(protected):
        work = work.replace(f"\x00{i}\x00", original, 1)
    return work


def vary_letter(subject: str, body: str, seed: str) -> tuple[str, str]:
    """按编辑微调客套措辞，书名号内的作品名和数字不改。同一 seed 结果稳定。"""
    rng = _rng_from_seed(seed)
    subject = apply_variant_slots(subject, rng)
    body = apply_variant_slots(body, rng)
    subject = _apply_synonyms(subject, rng)
    body = _apply_synonyms(body, rng)
    return subject, body
