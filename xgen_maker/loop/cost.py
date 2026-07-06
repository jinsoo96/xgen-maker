"""비용/토큰 추적 — 루프가 claude·LLM을 얼마나 쓰는지 집계 (엔진 Stage 7 Token 개념 차용).

정확한 토큰은 provider usage에 의존하므로, 여기선 호출 수 + 문자 기반 토큰 추정(≈chars/4)을
누적하는 정직한 근사. report/journal에 남겨 세션 비용을 가늠한다.
"""
from __future__ import annotations


class CostTracker:
    def __init__(self):
        self.agent_calls = 0
        self.llm_calls = 0
        self.est_input = 0
        self.est_output = 0

    def add_agent(self, prompt: str, output: str) -> None:
        self.agent_calls += 1
        self.est_input += len(prompt) // 4
        self.est_output += len(output or "") // 4

    def add_llm(self, prompt_chars: int, output_chars: int) -> None:
        self.llm_calls += 1
        self.est_input += prompt_chars // 4
        self.est_output += output_chars // 4

    def summary(self) -> dict:
        total = self.est_input + self.est_output
        return {"agent_calls": self.agent_calls, "llm_calls": self.llm_calls,
                "est_input_tokens": self.est_input, "est_output_tokens": self.est_output,
                "est_total_tokens": total, "note": "문자 기반 추정(≈chars/4)"}
