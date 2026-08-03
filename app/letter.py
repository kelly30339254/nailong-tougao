"""投稿信模板生成（本地模板，无 AI）。模板可由用户在设置页自定义。"""
from __future__ import annotations

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

PLACEHOLDER_HINT = "可用占位符：{编辑称呼} {作品名} {字数} {分类}（不认识的 {占位符} 会原样保留）"


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
