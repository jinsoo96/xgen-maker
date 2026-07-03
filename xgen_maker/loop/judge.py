"""⑧ judge 게이트 — 산출물 품질 점수 ≥ θ 통과 시에만 MR 준비로 진행.

하드 veto(점수 무관 차단): 빈 diff, 인프라 파일 변경(기능 코드만 원칙).
점수: LLM judge(JSON) 우선, 실패 시 휴리스틱 폴백.
"""
from __future__ import annotations

from .. import llm
from ..config import MakerConfig, infra_files

_JUDGE_SYSTEM = (
    "You are a strict code-change judge. Score how well the diff addresses the request. "
    'Reply JSON only: {"score": 0.0-1.0, "reasons": ["..."]}. '
    "Penalize: unrelated changes, missing core fix, risky wide edits.")


def heuristic_score(diff_text: str, changed_files: list[str],
                    checks: dict | None = None) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.6
    if len(changed_files) <= 5:
        score += 0.2
        reasons.append(f"변경 파일 {len(changed_files)}개(집중적)")
    else:
        reasons.append(f"변경 파일 {len(changed_files)}개(광범위)")
    diff_lines = diff_text.count("\n")
    if diff_lines <= 400:
        score += 0.1
        reasons.append(f"diff {diff_lines}줄(소규모)")
    if checks and any(checks.get(k) == "passed" for k in ("pytest", "node_test")):
        score += 0.1
        reasons.append("자동 테스트 통과")
    elif any(("test" in f.lower() or "spec" in f.lower()) for f in changed_files):
        score += 0.1
        reasons.append("테스트 파일 동반 변경")
    return min(score, 1.0), reasons


def judge(config: MakerConfig, query: str, diff_text: str,
          changed_files: list[str], checks: dict | None = None) -> dict:
    if not diff_text.strip() and not changed_files:
        return {"score": 0.0, "passed": False, "veto": "빈 diff — 구현 산출물 없음",
                "reasons": [], "source": "veto"}
    touched_infra = infra_files(changed_files)
    if touched_infra:
        return {"score": 0.0, "passed": False,
                "veto": f"인프라 파일 변경 감지(기능 코드만 원칙): {touched_infra}",
                "reasons": [], "source": "veto"}

    source = "heuristic"
    score, reasons = heuristic_score(diff_text, changed_files, checks)
    if config.llm_enabled:
        answer = llm.json_chat(config.llm_base, config.llm_model, [
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content":
             f"[request]\n{query}\n\n[changed files]\n{changed_files}\n\n[diff (truncated)]\n{diff_text[:8000]}"}],
            max_tokens=300, timeout=45)
        if answer is not None and isinstance(answer.get("score"), (int, float)):
            score = max(0.0, min(1.0, float(answer["score"])))
            reasons = [str(r) for r in answer.get("reasons", [])][:5]
            source = "llm"
    return {"score": round(score, 3), "passed": score >= config.theta, "veto": None,
            "reasons": reasons, "source": source, "theta": config.theta}
