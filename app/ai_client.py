"""OpenAI 兼容的聊天补全客户端。默认 SpaceXAI（xAI），也可用其他兼容接口。"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass

import certifi

from . import credential_store

AI_KEY_NAME = "ai_api_key"

# 提供商预设：名称 → (base_url, 默认模型, 说明)
AI_PRESETS: dict[str, tuple[str, str, str]] = {
    "SpaceXAI": ("https://api.x.ai/v1", "grok-4.5",
                 "xAI Grok，申请地址 https://console.x.ai"),
    "OpenAI": ("https://api.openai.com/v1", "gpt-4o-mini",
               "OpenAI 官方接口"),
    "DeepSeek": ("https://api.deepseek.com/v1", "deepseek-chat",
                 "DeepSeek 兼容接口"),
    "通义千问": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus",
              "阿里云百炼兼容模式"),
    "自定义": ("", "", "自行填写 Base URL 与模型名，需兼容 OpenAI Chat Completions"),
}

DEFAULT_PROVIDER = "SpaceXAI"


@dataclass
class AiConfig:
    provider: str = DEFAULT_PROVIDER
    base_url: str = "https://api.x.ai/v1"
    model: str = "grok-4.5"
    api_key: str = ""

    def configured(self) -> bool:
        return bool(self.api_key.strip() and self.base_url.strip() and self.model.strip())


class AiError(RuntimeError):
    pass


def load_api_key() -> str:
    return credential_store.get(AI_KEY_NAME) or ""


def save_api_key(value: str) -> bool:
    key = (value or "").strip()
    if not key:
        credential_store.delete(AI_KEY_NAME)
        return True
    return credential_store.set(AI_KEY_NAME, key)


def preset_for(provider: str) -> tuple[str, str, str]:
    return AI_PRESETS.get(provider, AI_PRESETS["自定义"])


def chat(config: AiConfig, messages: list[dict], timeout: int = 45,
         temperature: float = 0.4, max_tokens: int = 1200) -> str:
    """调用 /chat/completions，返回 assistant 文本。失败抛 AiError。"""
    if not config.configured():
        raise AiError("尚未配置 API Key / 接口地址 / 模型")
    url = config.base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": config.model.strip(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Authorization": f"Bearer {config.api_key.strip()}",
            "Content-Type": "application/json",
        })
    ctx = ssl.create_default_context(cafile=certifi.where())
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise AiError(_friendly_http(exc.code, body)) from exc
    except urllib.error.URLError as exc:
        raise AiError(f"无法连接接口：{exc.reason}") from exc
    except TimeoutError as exc:
        raise AiError("接口超时，请稍后重试") from exc
    try:
        obj = json.loads(raw)
        return (obj["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AiError("接口返回格式无法解析") from exc


def parse_json_object(text: str) -> dict:
    """从模型输出中抽出第一个 JSON 对象。"""
    blob = (text or "").strip()
    if blob.startswith("```"):
        blob = re_strip_fence(blob)
    start = blob.find("{")
    end = blob.rfind("}")
    if start < 0 or end <= start:
        raise AiError("模型未返回 JSON")
    try:
        data = json.loads(blob[start:end + 1])
    except ValueError as exc:
        raise AiError("模型 JSON 无法解析") from exc
    if not isinstance(data, dict):
        raise AiError("模型 JSON 不是对象")
    return data


def re_strip_fence(text: str) -> str:
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


def _friendly_http(code: int, body: str) -> str:
    snippet = (body or "").replace("\n", " ")[:180]
    if code in (401, 403):
        return f"API Key 无效或无权访问（{code}）"
    if code == 429:
        return "接口请求过于频繁或额度用尽"
    if code >= 500:
        return f"模型服务暂时不可用（{code}）"
    return f"接口错误 {code}：{snippet or '无详情'}"
