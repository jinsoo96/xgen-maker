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
import time
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


def _chat_claude_cli(messages: list[dict], timeout: int,
                     diag: dict | None = None) -> str | None:
    """claude CLI 구독 로그인으로 단발 완성 — API 키 불필요.

    순수 LLM 완성으로 쓰려면 프로젝트 컨텍스트(CLAUDE.md·git·MCP)를 끊어야 한다:
    - system 역할은 --system-prompt 로 전체 override(기본 에이전트 프롬프트 대체)
    - 중립 임시 디렉토리에서 실행(repo cwd의 CLAUDE.md/git 오염 차단)

    diag를 주면 실패 사유를 담아 준다. 이걸 안 하면 호출자는 None만 받고, 화면에는
    "변환 실패"만 남아 왜 실패했는지 영영 알 수 없다(실측: 간헐 실패인데 진단 불가).
    """
    import tempfile
    from .auth import claude_command

    def _note(reason: str) -> None:
        if diag is not None:
            diag["reason"] = reason

    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    user = "\n\n".join(m["content"] for m in messages if m["role"] != "system")
    # 프롬프트는 인자가 아니라 stdin으로 넘긴다. Windows에서 claude는 .CMD 심이라
    # cmd /c 를 거치는데, cmd는 인자 안의 줄바꿈에서 잘라 버린다 — 첫 줄만 모델에
    # 닿는다. 에러도 없고 응답도 그럴듯해서 조용히 틀린다(실측: 판정 LLM이
    # "[request] 한 줄"만 보고 diff 없이 점수를 매기고 있었다).
    args = ["-p", "--output-format", "text"]
    if system:
        # --system-prompt도 인자라 같은 함정에 걸린다. 지금 쓰는 것들은 모두 한 줄이라
        # 안전하지만, 여러 줄이면 인자로는 온전히 못 넘긴다 — 본문 앞에 붙여 보낸다.
        if "\n" in system:
            user = f"{system}\n\n{user}"
        else:
            args += ["--system-prompt", system]
    command = claude_command(args)
    if command is None:
        _note("claude CLI를 찾지 못했습니다")
        return None
    try:
        with tempfile.TemporaryDirectory() as neutral:
            result = subprocess.run(
                command, input=user, cwd=neutral, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        _note(f"{timeout}초 안에 응답이 없었습니다")
        return None
    except OSError as error:
        _note(f"실행 실패: {str(error)[:120]}")
        return None
    if result.returncode != 0:
        _note(f"종료코드 {result.returncode}: "
              f"{(result.stderr or '').strip()[:160] or '(stderr 없음)'}")
        return None
    text = (result.stdout or "").strip()
    if not text:
        _note(f"빈 응답(stderr: {(result.stderr or '').strip()[:120] or '없음'})")
        return None
    return text


def chat(base: str, model: str, messages: list[dict], max_tokens: int = 800,
         temperature: float = 0.2, timeout: int = 60,
         diag: dict | None = None) -> str | None:
    if base == "claude_cli":
        return _chat_claude_cli(messages, timeout, diag)
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
    # 프롬프트는 stdin으로 — question에 줄바꿈이 섞이면 인자로는 첫 줄만 전달된다.
    command = claude_command(["-p", "--output-format", "text"])
    if command is None:
        return None
    try:
        with tempfile.TemporaryDirectory() as neutral:   # repo CLAUDE.md/git 오염 차단
            result = subprocess.run(command, input=prompt, cwd=neutral,
                                    capture_output=True, text=True,
                                    encoding="utf-8", errors="replace",
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


def _first_json_object(text: str) -> dict | None:
    """응답에서 첫 JSON 오브젝트. 중괄호 균형을 세어 정확히 한 개만 떼어낸다.

    `\\{.*\\}` 하나로 잡으면 탐욕적이라 첫 `{`부터 마지막 `}`까지 삼킨다. 모델이 JSON
    앞뒤에 중괄호가 섞인 설명을 붙이는 순간 통째로 파싱 실패가 된다 — 그러면 착지
    어휘가 없는 채로 검색이 돌아 엉뚱한 서비스로 샌다.
    """
    for start in range(len(text)):
        if text[start] != "{":
            continue
        depth = 0
        in_str = escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break          # 이 후보는 실패 — 다음 '{'부터 다시
                    return parsed if isinstance(parsed, dict) else None
        # 균형이 안 맞으면(잘린 응답) 다음 '{' 후보로
    return None


def json_chat(base: str, model: str, messages: list[dict],
              retries: int = 0, diag: dict | None = None, **kw) -> dict | None:
    """응답에서 첫 JSON 오브젝트를 관대하게 파싱.

    retries>0이면 빈 응답·파싱 실패 시 그만큼 다시 시도한다. claude CLI 구독은 가끔
    빈 stdout을 돌려주는데, 한 번의 실패로 착지 어휘 변환이 통째로 날아가면 검색이
    엉뚱한 서비스로 샌다 — 착지처럼 결과가 걸린 호출에서만 켠다. 실패는 간헐적이라
    시도 사이를 조금 띄운다(즉시 재시도는 같은 순간의 장애를 그대로 다시 맞는다).
    """
    last = {}
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(min(2.0, 0.5 * attempt))
        text = chat(base, model, messages, diag=last, **kw)
        if text:
            parsed = _first_json_object(text)
            if parsed is not None:
                return parsed
            last = {"reason": f"JSON을 찾지 못했습니다(응답 {len(text)}자)"}
    if diag is not None and last.get("reason"):
        diag["reason"] = last["reason"]
    return None
