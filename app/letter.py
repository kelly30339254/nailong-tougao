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

# 新安装随包提供五套可编辑模板；升级用户的旧模板会单独保留。
DEFAULT_TEMPLATE_SET: tuple[tuple[str, str, str], ...] = (
    ("简洁自荐", DEFAULT_SUBJECT_TPL, DEFAULT_BODY_TPL),
    ("礼貌投稿",
     "【投稿】《{作品名}》｜约{字数}字",
     "尊敬的{编辑称呼}编辑：\n\n您好！现向您投递作品《{作品名}》，全文约{字数}字。"
     "附件中为完整稿件，烦请您在方便时审阅。\n\n顺颂编安！"),
    ("作品说明",
     "投稿作品《{作品名}》（{字数}字）",
     "{编辑称呼}编辑，您好：\n\n冒昧来信投稿。《{作品名}》全文约{字数}字，"
     "稿件内容已附在邮件中，感谢您抽空阅读。\n\n祝工作顺利！"),
    ("正式投稿",
     "向您投稿：《{作品名}》/ {字数}字",
     "尊敬的{编辑称呼}编辑：\n\n您好！我想向您投稿作品《{作品名}》，字数约为{字数}。"
     "完整正文请查收附件，期待您的审阅意见。\n\n祝编安！"),
    ("温和问候",
     "《{作品名}》投稿（约{字数}字）",
     "{编辑称呼}编辑，您好！\n\n打扰了，向您投递我的作品《{作品名}》，全文约{字数}字。"
     "稿件见附件，感谢您的时间。\n\n祝一切顺利！"),
)

DEFAULT_URGE_SUBJECT = "请问《{作品名}》审稿进度（{原投日期}已投）"
DEFAULT_URGE_BODY = (
    "尊敬的{编辑称呼}编辑：\n\n"
    "    您好！此前于{原投日期}向您投递《{作品名}》（约{字数}字，{分类}），"
    "冒昧打扰想请问是否已安排审阅。若仍在队列中，我再耐心等候。\n\n"
    "    祝工作顺利！\n"
)

PLACEHOLDER_HINT = (
    "必需占位符：{编辑称呼} {作品名} {字数}。可选：{分类} {读者分类} "
    "{情绪} {作品风格} {作品类型}。"
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
                 body_tpl: str | None = None, *, reader_group: str = "",
                 emotion: str = "", style: str = "",
                 genre_type: str = "") -> tuple[str, str]:
    """返回 (subject, body)。模板为空时用默认模板。"""
    mapping = {
        "编辑称呼": editor_name, "作品名": title, "字数": word_count,
        "分类": category, "读者分类": reader_group, "情绪": emotion,
        "作品风格": style, "作品类型": genre_type,
    }
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


def vary_letter(subject: str, body: str, seed: str,
                protected_values=()) -> tuple[str, str]:
    """只微调非事实措辞；调用方可显式保护全部文稿事实字段。"""
    rng = _rng_from_seed(seed)
    protected: dict[str, str] = {}
    values = sorted({str(value) for value in protected_values if str(value)},
                    key=len, reverse=True)
    for index, value in enumerate(values):
        if value not in subject and value not in body:
            continue
        token = f"\x01FACT{index}\x02"
        protected[token] = value
        subject = subject.replace(value, token)
        body = body.replace(value, token)
    subject = apply_variant_slots(subject, rng)
    body = apply_variant_slots(body, rng)
    subject = _apply_synonyms(subject, rng)
    body = _apply_synonyms(body, rng)
    for token, value in protected.items():
        subject = subject.replace(token, value)
        body = body.replace(token, value)
    return subject, body


def validate_letter_template(subject: str, body: str) -> list[str]:
    """返回模板问题；空列表表示可以参与批次。"""
    combined = (subject or "") + "\n" + (body or "")
    issues = []
    if not (subject or "").strip():
        issues.append("主题为空")
    if not (body or "").strip():
        issues.append("正文为空")
    for placeholder in ("{编辑称呼}", "{作品名}", "{字数}"):
        if placeholder not in combined:
            issues.append(f"缺少必需占位符 {placeholder}")
    for match in _VARIANT_RE.finditer(combined):
        if len([part for part in match.group(1).split("|") if part.strip()]) < 2:
            issues.append("{变:...} 至少需要两个非空选项")
            break
    return issues


def shuffled_template_ids(template_ids: list[int], count: int, seed: str) -> list[int]:
    """随机不放回轮换；多模板时避免两个轮次边界相同。"""
    ids = list(dict.fromkeys(int(i) for i in template_ids))
    if not ids or count <= 0:
        return []
    rng = _rng_from_seed(seed)
    result: list[int] = []
    while len(result) < count:
        cycle = ids[:]
        rng.shuffle(cycle)
        if len(cycle) > 1 and result and cycle[0] == result[-1]:
            cycle[0], cycle[1] = cycle[1], cycle[0]
        result.extend(cycle)
    return result[:count]
