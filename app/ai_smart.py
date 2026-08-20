"""AI智选 / AI微调：在已接入 API 时调用大模型；失败由调用方回退原版。"""
from __future__ import annotations

from .ai_client import AiConfig, AiError, chat, parse_json_object
from .smart_match import editor_payload

_RANK_SYS = (
    "你是网文投稿顾问。根据作品标签和编辑公开收稿资料，判断哪些编辑更适合这篇稿。"
    "只依据给定字段，不要编造邮箱或征稿规则。"
    "返回 JSON：{\"items\":[{\"id\":数字,\"score\":0到100整数,\"reason\":\"不超过30字\","
    "\"recommend\":true或false}]}"
    "未提供的编辑不要出现。停止收稿的编辑 score 给 0 且 recommend=false。"
)

DEFAULT_TPL_REQUIREMENTS = (
    "请写一封可复用的投稿信模板（不是发给某个编辑的定稿）。\n"
    "要求：\n"
    "1. 主题和正文合起来必须保留：{编辑称呼} {作品名} {字数}\n"
    "   {分类} {读者分类} {情绪} {作品风格} {作品类型} 为可选事实占位符\n"
    "2. 语气恭敬、简短，适合群发给不同编辑\n"
    "3. 不要出现电话、地址、银行卡、承诺过稿\n"
    "4. 可以酌情加入 {变:措辞A|措辞B} 供规则微调（不使用AI）使用\n"
    "5. 只返回主题和正文"
)

_TPL_SYS = (
    "你写网文投稿信模板，不是某一封定稿。"
    "主题和正文合起来必须原样包含占位符：{编辑称呼} {作品名} {字数}。"
    "不要写电话、地址、银行卡、承诺过稿。"
    "返回 JSON：{\"subject\":\"主题模板\",\"body\":\"正文模板\"}"
)

_VARY_SYS = (
    "你微调投稿信客套措辞，避免群发内容完全相同被判垃圾邮件。"
    "必须保留：作品名（含书名号）、字数、分类、编辑称呼、事实陈述。"
    "只改问候、自荐用语、祝颂语和少量连接词，语气保持恭敬。"
    "不要添加电话、地址、银行卡、承诺过稿等原文没有的内容。"
    "返回 JSON：{\"subject\":\"主题\",\"body\":\"正文\"}"
)

_BATCH_TEMPLATES_SYS = (
    "你为一篇网文生成多套可审核、可轮换的投稿信模板。"
    "每套的主题与正文合起来必须原样包含 {编辑称呼}、{作品名}、{字数}。"
    "可以使用 {分类}、{读者分类}、{情绪}、{作品风格}、{作品类型}，不得改写这些事实。"
    "只变化问候、自荐、衔接和祝颂，不要加入电话、地址、银行卡或过稿承诺。"
    "返回 JSON：{\"items\":[{\"name\":\"名称\",\"subject\":\"主题模板\","
    "\"body\":\"正文模板\"}]}。items 数量必须与用户要求一致。"
)


def rank_editors(config: AiConfig, query: dict, editors: list,
                 timeout: int = 60) -> dict[int, tuple[int, str, bool]]:
    """返回 {editor_id: (score, reason, recommend)}。"""
    if not editors:
        return {}
    chunk_size = 40
    if len(editors) > chunk_size:
        merged: dict[int, tuple[int, str, bool]] = {}
        for i in range(0, len(editors), chunk_size):
            merged.update(rank_editors(config, query, editors[i:i + chunk_size], timeout))
        return merged
    payload = [editor_payload(e) for e in editors if e.id is not None]
    user = (
        "作品："
        f"分类={query.get('category','')}；篇幅/类型={query.get('genre_type','')}；"
        f"读者群={query.get('reader_group','')}；情绪={query.get('emotion','')}；"
        f"风格={query.get('style','')}；字数={query.get('word_count','')}；"
        f"标题={query.get('title','')}\n"
        f"候选编辑（共{len(payload)}）：\n"
        + json_dumps(payload)
    )
    text = chat(
        config,
        [{"role": "system", "content": _RANK_SYS},
         {"role": "user", "content": user}],
        timeout=timeout, temperature=0.2, max_tokens=1800)
    data = parse_json_object(text)
    items = data.get("items")
    if not isinstance(items, list):
        raise AiError("AI智选结果缺少 items")
    allowed = {e.id for e in editors}
    result: dict[int, tuple[int, str, bool]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            eid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if eid not in allowed:
            continue
        try:
            score = int(item.get("score", 0))
        except (TypeError, ValueError):
            score = 0
        score = max(0, min(100, score))
        reason = str(item.get("reason") or "AI已评估").strip()[:40]
        recommend = bool(item.get("recommend"))
        result[eid] = (score, reason, recommend)
    if not result:
        raise AiError("AI智选没有返回有效编辑")
    return result


def vary_letter_ai(config: AiConfig, subject: str, body: str, editor_name: str,
                   title: str, extra: str = "", timeout: int = 40) -> tuple[str, str]:
    user = (
        f"编辑称呼：{editor_name}\n作品名：{title}\n{extra}\n"
        f"原主题：\n{subject}\n\n原正文：\n{body}"
    )
    text = chat(
        config,
        [{"role": "system", "content": _VARY_SYS},
         {"role": "user", "content": user}],
        timeout=timeout, temperature=0.7, max_tokens=900)
    data = parse_json_object(text)
    new_subject = str(data.get("subject") or "").strip()
    new_body = str(data.get("body") or "").strip()
    if not new_subject or not new_body:
        raise AiError("AI微调未返回完整主题/正文")
    if title and title not in new_body and title not in new_subject:
        # 模型丢掉作品名则作废，回退给调用方
        raise AiError("AI微调丢掉了作品名")
    return new_subject, new_body


_CLASSIFY_SYS = (
    "你判断编辑给作者的回信结果。只根据主题和正文，不要编造没有的承诺。"
    "自动回复、询问材料、客套寒暄不要判成过稿。"
    "返回 JSON：{\"verdict\":\"过稿|退稿|需修改|待确认|其他\",\"reason\":\"不超过30字\"}"
)

_LETTER_SYS = (
    "你为当前这篇作品写一封投稿信，不是通用模板。"
    "正文里用 {编辑称呼} 代替具体姓名，发送时会替换。"
    "必须提到作品名和字数。语气恭敬、简短。"
    "不要写电话、地址、银行卡、承诺过稿。"
    "返回 JSON：{\"subject\":\"主题\",\"body\":\"正文\"}"
)

_TAG_SYS = (
    "你根据作品标题和正文开头，建议网文标签。"
    "category 从这些里选或给一个更贴切的短词：言情、悬疑、世情、脑洞、惊悚、奇幻、科幻、武侠、现实、其他。"
    "reader_group 只能是：男频、女频、通用。"
    "emotion 从：甜、虐、爽、燃、暖、虐心、轻松 中选最接近的。"
    "style 从：第一人称、第三人称、多视角 中选。"
    "genre_type 用短词描述篇幅或类型，如短篇、长篇、中短篇、短剧。"
    "返回 JSON：{\"category\":\"\",\"genre_type\":\"\",\"reader_group\":\"\",\"emotion\":\"\",\"style\":\"\",\"reason\":\"不超过20字\"}"
)

_EDITOR_SYS = (
    "你根据编辑公开资料，用一句话说明其收稿口味，并判断是否适合给定作品。"
    "fit 只能是：适合、一般、不适合。"
    "返回 JSON：{\"summary\":\"不超过40字\",\"fit\":\"适合|一般|不适合\",\"reason\":\"不超过30字\"}"
)

_VERDICTS = {"过稿", "退稿", "需修改", "待确认", "其他"}


def classify_reply_ai(config: AiConfig, subject: str, body: str,
                      timeout: int = 40) -> tuple[str, str]:
    user = f"主题：{subject or '（无）'}\n正文：\n{(body or '')[:2500]}"
    text = chat(
        config,
        [{"role": "system", "content": _CLASSIFY_SYS},
         {"role": "user", "content": user}],
        timeout=timeout, temperature=0.1, max_tokens=200)
    data = parse_json_object(text)
    verdict = str(data.get("verdict") or "").strip()
    if verdict not in _VERDICTS:
        raise AiError("AI 判定结果无效")
    reason = str(data.get("reason") or "AI已评估").strip()[:40]
    return verdict, reason


def generate_letter_for_work(config: AiConfig, query: dict,
                             timeout: int = 50) -> tuple[str, str]:
    user = (
        f"作品名：{query.get('title','')}\n字数：{query.get('word_count','')}\n"
        f"分类：{query.get('category','')}\n篇幅：{query.get('genre_type','')}\n"
        f"读者群：{query.get('reader_group','')}\n情绪：{query.get('emotion','')}\n"
        f"风格：{query.get('style','')}"
    )
    text = chat(
        config,
        [{"role": "system", "content": _LETTER_SYS},
         {"role": "user", "content": user}],
        timeout=timeout, temperature=0.6, max_tokens=900)
    data = parse_json_object(text)
    subject = str(data.get("subject") or "").strip()
    body = str(data.get("body") or "").strip()
    if not subject or not body:
        raise AiError("AI 未返回完整主题和正文")
    title = str(query.get("title") or "")
    if title and title not in subject and title not in body:
        raise AiError("生成结果未包含作品名")
    if "{编辑称呼}" not in body:
        body = "尊敬的{编辑称呼}编辑：\n\n" + body
    return subject, body


def suggest_manuscript_tags(config: AiConfig, title: str, excerpt: str,
                            timeout: int = 40) -> dict:
    user = f"标题：{title or '（无）'}\n正文开头：\n{(excerpt or '')[:1800]}"
    text = chat(
        config,
        [{"role": "system", "content": _TAG_SYS},
         {"role": "user", "content": user}],
        timeout=timeout, temperature=0.2, max_tokens=300)
    data = parse_json_object(text)
    return {
        "category": str(data.get("category") or "").strip(),
        "genre_type": str(data.get("genre_type") or "").strip(),
        "reader_group": str(data.get("reader_group") or "").strip(),
        "emotion": str(data.get("emotion") or "").strip(),
        "style": str(data.get("style") or "").strip(),
        "reason": str(data.get("reason") or "").strip()[:40],
    }


def summarize_editor(config: AiConfig, editor, query: dict | None = None,
                     timeout: int = 40) -> dict:
    payload = editor_payload(editor)
    extra = ""
    if query:
        extra = (
            f"\n对照作品：分类={query.get('category','')} 篇幅={query.get('genre_type','')} "
            f"读者={query.get('reader_group','')} 情绪={query.get('emotion','')}"
        )
    text = chat(
        config,
        [{"role": "system", "content": _EDITOR_SYS},
         {"role": "user", "content": json_dumps(payload) + extra}],
        timeout=timeout, temperature=0.2, max_tokens=250)
    data = parse_json_object(text)
    fit = str(data.get("fit") or "一般").strip()
    if fit not in ("适合", "一般", "不适合"):
        fit = "一般"
    return {
        "summary": str(data.get("summary") or "").strip()[:60],
        "fit": fit,
        "reason": str(data.get("reason") or "").strip()[:40],
    }


def generate_letter_template(config: AiConfig, requirements: str,
                             timeout: int = 50) -> tuple[str, str]:
    text = chat(
        config,
        [{"role": "system", "content": _TPL_SYS},
         {"role": "user", "content": (requirements or DEFAULT_TPL_REQUIREMENTS).strip()}],
        timeout=timeout, temperature=0.5, max_tokens=900)
    data = parse_json_object(text)
    subject = str(data.get("subject") or "").strip()
    body = str(data.get("body") or "").strip()
    if not subject or not body:
        raise AiError("AI 未返回完整的主题和正文")
    missing = [p for p in ("{编辑称呼}", "{作品名}", "{字数}")
               if p not in subject + body]
    if missing:
        raise AiError("生成结果缺少占位符：" + "、".join(missing))
    return subject, body


def generate_batch_letter_templates(config: AiConfig, metadata: dict, count: int = 5,
                                    timeout: int = 70) -> list[dict]:
    """一次调用生成当前批次候选；上传内容严格限于标题和结构化标签。"""
    count = int(count)
    if not 2 <= count <= 10:
        raise ValueError("AI 候选数量必须为 2–10")
    allowed_keys = (
        "title", "word_count", "category", "genre_type", "reader_group",
        "emotion", "style",
    )
    safe_metadata = {key: str(metadata.get(key, "")) for key in allowed_keys}
    user = f"请生成 {count} 套。作品结构化信息：\n{json_dumps(safe_metadata)}"
    text = chat(
        config,
        [{"role": "system", "content": _BATCH_TEMPLATES_SYS},
         {"role": "user", "content": user}],
        timeout=timeout, temperature=0.8, max_tokens=min(5000, 500 * count))
    data = parse_json_object(text)
    items = data.get("items")
    if not isinstance(items, list) or len(items) != count:
        raise AiError(f"AI 应返回 {count} 套候选，实际格式或数量不符")
    from .letter import validate_letter_template
    result: list[dict] = []
    literal_facts = {
        str(safe_metadata.get(key) or "").strip()
        for key in ("title", "category", "genre_type", "reader_group", "emotion", "style")
        if str(safe_metadata.get(key) or "").strip()
    }
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise AiError(f"第 {index + 1} 套候选格式无效")
        subject = str(item.get("subject") or "").strip()
        body = str(item.get("body") or "").strip()
        issues = validate_letter_template(subject, body)
        if issues:
            raise AiError(f"第 {index + 1} 套候选：{'；'.join(issues)}")
        hardcoded = [fact for fact in literal_facts if fact in subject + body]
        if hardcoded:
            raise AiError(
                f"第 {index + 1} 套候选把事实写死了，应改用占位符：{'、'.join(hardcoded)}")
        result.append({
            "name": str(item.get("name") or f"AI 候选 {index + 1}").strip(),
            "subject": subject, "body": body, "selected": True,
            "origin": "ai",
        })
    return result


def json_dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)
