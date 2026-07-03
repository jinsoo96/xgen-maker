"""LLM 클라이언트 — stdlib(urllib)만. OpenAI-호환 + Anthropic 지원. 실패 시 None(호출부 휴리스틱 폴백).

base가 'anthropic:' 로 시작하면 Anthropic Messages API를 쓴다(키=env ANTHROPIC_API_KEY 또는 base 뒤 인라인).
그 외는 OpenAI-호환 /chat/completions (vLLM 등).
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.error


def _chat_openai(base: str, model: str, messages: list[dict], max_tokens: int,
                 temperature: float, timeout: int) -> str | None:
    payload = {"model": model, "messages": messages,
               "max_tokens": max_tokens, "temperature": temperature}
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("XGEN_MAKER_LLM_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]
    except (urllib.error.URLError, OSError, KeyError, IndexError, json.JSONDecodeError, TimeoutError):
        return None


def _chat_anthropic(model: str, messages: list[dict], max_tokens: int,
                    temperature: float, timeout: int) -> str | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    user_msgs = [{"role": m["role"], "content": m["content"]}
                 for m in messages if m["role"] != "system"]
    payload = {"model": model, "max_tokens": max_tokens, "temperature": temperature,
               "messages": user_msgs}
    if system:
        payload["system"] = system
    request = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-api-key": api_key,
                 "anthropic-version": "2023-06-01"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        return "".join(block.get("text", "") for block in data.get("content", []))
    except (urllib.error.URLError, OSError, KeyError, IndexError, json.JSONDecodeError, TimeoutError):
        return None


def chat(base: str, model: str, messages: list[dict], max_tokens: int = 800,
         temperature: float = 0.2, timeout: int = 60) -> str | None:
    if base.startswith("anthropic"):
        return _chat_anthropic(model, messages, max_tokens, temperature, timeout)
    return _chat_openai(base, model, messages, max_tokens, temperature, timeout)


def json_chat(base: str, model: str, messages: list[dict], **kw) -> dict | None:
    """응답에서 첫 JSON 오브젝트를 관대하게 파싱."""
    text = chat(base, model, messages, **kw)
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
