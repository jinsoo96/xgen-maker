"""OpenAI-호환 LLM 클라이언트 — stdlib(urllib)만 사용. 실패 시 None 반환(호출부는 휴리스틱 폴백)."""
from __future__ import annotations

import json
import re
import urllib.request
import urllib.error


def chat(base: str, model: str, messages: list[dict], max_tokens: int = 800,
         temperature: float = 0.2, timeout: int = 60) -> str | None:
    payload = {"model": model, "messages": messages,
               "max_tokens": max_tokens, "temperature": temperature}
    request = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]
    except (urllib.error.URLError, OSError, KeyError, IndexError, json.JSONDecodeError, TimeoutError):
        return None


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
