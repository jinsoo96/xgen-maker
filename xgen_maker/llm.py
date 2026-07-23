"""LLM 클라이언트 — stdlib(urllib)만. OpenAI-호환 + Anthropic 지원. 실패 시 None(호출부 휴리스틱 폴백).

base가 'anthropic:' 로 시작하면 Anthropic Messages API를 쓴다(키=env ANTHROPIC_API_KEY 또는 base 뒤 인라인).
그 외는 OpenAI-호환 /chat/completions (vLLM 등).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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


def _chat_claude_cli(messages: list[dict], timeout: int) -> str | None:
    """claude CLI 구독 로그인으로 단발 완성 — API 키 불필요.

    순수 LLM 완성으로 쓰려면 프로젝트 컨텍스트(CLAUDE.md·git·MCP)를 끊어야 한다:
    - system 역할은 --system-prompt 로 전체 override(기본 에이전트 프롬프트 대체)
    - 중립 임시 디렉토리에서 실행(repo cwd의 CLAUDE.md/git 오염 차단)
    """
    import tempfile
    from .auth import claude_command
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    user = "\n\n".join(m["content"] for m in messages if m["role"] != "system")
    args = ["-p", user, "--output-format", "text"]
    if system:
        args += ["--system-prompt", system]
    command = claude_command(args)
    if command is None:
        return None
    try:
        with tempfile.TemporaryDirectory() as neutral:
            result = subprocess.run(
                command, cwd=neutral, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def chat(base: str, model: str, messages: list[dict], max_tokens: int = 800,
         temperature: float = 0.2, timeout: int = 60) -> str | None:
    if base == "claude_cli":
        return _chat_claude_cli(messages, timeout)
    if base.startswith("anthropic"):
        return _chat_anthropic(model, messages, max_tokens, temperature, timeout)
    return _chat_openai(base, model, messages, max_tokens, temperature, timeout)


_VISION_FORMAT = ('\n\nReply JSON only: '
                  '{"renders_ok": true/false, "issues": ["..."], "summary": "..."}')


def _vision_judge_cli(image_path: str, question: str, timeout: int) -> dict | None:
    """구독 로그인(claude CLI)으로 스크린샷 판정 — API 키 불필요.

    claude CLI는 이미지를 읽을 수 있다. 경로를 주고 판정을 시키면 구독으로 처리된다.
    이게 없으면 구독 사용자는 비전 검증을 아예 못 쓴다(초기 목적이었다).
    """
    import tempfile
    from pathlib import Path
    from .auth import claude_command
    # 중립 임시 디렉토리에서 실행하므로 상대경로는 못 푼다 — 절대경로로 준다.
    # "reply JSON only"를 경로 바로 뒤·질문 앞에 둔다 — 뒤에 두면 산문으로 답한다.
    abs_path = str(Path(image_path).resolve())
    prompt = (f"Read the image at {abs_path} and reply JSON only: "
              '{"renders_ok": true/false, "issues": ["..."], "summary": "..."}. '
              f"{question}")
    command = claude_command(["-p", prompt, "--output-format", "text"])
    if command is None:
        return None
    try:
        with tempfile.TemporaryDirectory() as neutral:   # repo CLAUDE.md/git 오염 차단
            result = subprocess.run(command, cwd=neutral, capture_output=True,
                                    text=True, encoding="utf-8", errors="replace",
                                    timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"\{.*\}", result.stdout, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def vision_judge(image_path: str, question: str,
                 model: str = "claude-sonnet-5", timeout: int = 60) -> dict | None:
    """스크린샷을 비전 LLM으로 판정 (Visual Feedback Loop 패턴).

    API 키가 있으면 Messages API로, 없으면 구독 로그인(claude CLI)으로 판정한다.
    반환 {"renders_ok": bool, "issues": [...], "summary": "..."} (JSON 강제).
    """
    import base64
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _vision_judge_cli(image_path, question, timeout)
    try:
        raw = open(image_path, "rb").read()
    except OSError:
        return None
    media = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
    payload = {
        "model": model, "max_tokens": 500,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media,
                                         "data": base64.b64encode(raw).decode()}},
            {"type": "text", "content_hint": "screenshot",
             "text": question + '\n\nReply JSON only: '
                     '{"renders_ok": true/false, "issues": ["..."], "summary": "..."}'},
        ]}],
    }
    # content의 text 항목 정규화(위 dict에 content_hint 오타 방지)
    payload["messages"][0]["content"][1] = {
        "type": "text",
        "text": question + '\n\nReply JSON only: '
                '{"renders_ok": true/false, "issues": ["..."], "summary": "..."}'}
    request = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-api-key": api_key,
                 "anthropic-version": "2023-06-01"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        text = "".join(b.get("text", "") for b in data.get("content", []))
    except (urllib.error.URLError, OSError, KeyError, json.JSONDecodeError, TimeoutError):
        return None
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
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
