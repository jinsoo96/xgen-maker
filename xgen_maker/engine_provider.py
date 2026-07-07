"""엔진 LLM provider — claude 구독(로그인)을 xgen-harness 엔진 provider로 물린다.

엔진 풀 파이프라인(Pipeline.run)의 LLM 스테이지(s03/s08/s09 등)는 provider를 요구한다.
API 키 대신 사용자의 claude 구독을 쓰려면, 엔진 LLMProvider 계약(async chat 제너레이터)을
claude CLI로 구현해 등록한다. → 엔진이 사용자 구독으로 자기 스테이지를 구동.

(API 키가 있으면 built-in "anthropic" provider가 더 간단 — 이 모듈은 '구독' 경로용.)
"""
from __future__ import annotations

import asyncio


def build_cli_provider(engine):
    """엔진 LLMProvider를 상속한 claude-CLI provider 클래스를 동적 생성."""
    base = engine.providers.base.LLMProvider
    ProviderEvent = engine.providers.ProviderEvent
    ET = engine.providers.ProviderEventType

    class ClaudeCliProvider(base):
        """claude CLI 구독으로 완성 — API 키 불필요. tool/thinking 미지원(단발 완성)."""

        def __init__(self, api_key: str = "", model: str = "claude(subscription)",
                     base_url=None, timeout: int = 180):
            # create_provider(name, api_key, model, base_url) 시그니처 수용 — 키는 무시(구독)
            self._model = model or "claude(subscription)"
            self._timeout = timeout

        @property
        def provider_name(self) -> str:
            return "claude_cli"

        @property
        def model_name(self) -> str:
            return self._model

        def supports_tool_use(self) -> bool:
            return False

        def supports_thinking(self) -> bool:
            return False

        def supports_response_format(self) -> bool:
            return False

        def count_tokens(self, text: str):
            return (max(1, len(text) // 4), "approx")

        async def chat(self, messages, system=None, tools=None, temperature=0.7,
                       max_tokens=8192, stream=True, thinking=None,
                       tool_choice=None, response_format=None):
            from .llm import _chat_claude_cli
            msgs = list(messages)
            if system:
                msgs = [{"role": "system", "content": system}] + msgs
            text = await asyncio.to_thread(_chat_claude_cli, msgs, self._timeout)
            if not text:
                yield ProviderEvent(type=ET.ERROR, tool_input={},
                                    text="claude CLI 완성 실패 — maker login(claude) 확인")
                return
            yield ProviderEvent(type=ET.TEXT_DELTA, tool_input={}, text=text)
            yield ProviderEvent(type=ET.USAGE, tool_input={},
                                output_tokens=self.count_tokens(text)[0])
            yield ProviderEvent(type=ET.STOP, tool_input={}, stop_reason="end_turn")

    return ClaudeCliProvider


def cli_provider(engine, model: str | None = None):
    """claude-CLI provider 인스턴스 생성(엔진 풀 파이프라인에 주입용)."""
    return build_cli_provider(engine)(model=model or "claude(subscription)")
