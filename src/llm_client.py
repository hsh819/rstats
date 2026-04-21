"""OpenAI 兼容 LLM 客户端封装。无 API key 时降级到规则侧处理。"""
from __future__ import annotations

import json
from typing import Any

from . import config


class LLMClient:
    def __init__(self):
        self.enabled = config.LLM_ENABLED
        self._client = None
        if self.enabled:
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
            except ImportError:
                print("[llm] openai sdk not installed; running without LLM.")
                self.enabled = False

    def chat(self, messages: list[dict], *, temperature: float = 0.1, response_format: str | None = None, max_tokens: int = 2048) -> str:
        if not self.enabled:
            return ""
        kwargs: dict[str, Any] = dict(
            model=config.LLM_MODEL,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if response_format == "json":
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    def chat_json(self, messages: list[dict], **kw) -> dict:
        raw = self.chat(messages, response_format="json", **kw)
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end > start:
                try:
                    return json.loads(raw[start : end + 1])
                except json.JSONDecodeError:
                    pass
        return {"_raw": raw}


_singleton: LLMClient | None = None


def get_client() -> LLMClient:
    global _singleton
    if _singleton is None:
        _singleton = LLMClient()
    return _singleton
