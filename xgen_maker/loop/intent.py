"""② Goal 해석 — 쿼리를 개발 intent로 분류 (휴리스틱 우선, LLM 보정 옵션)."""
from __future__ import annotations

from .. import llm

BUG_HINTS = ("버그", "고쳐", "고쳐줘", "고장", "안 돼", "안돼", "안된다", "에러", "오류", "깨져", "깨짐",
             "fix", "bug", "error", "broken", "crash", "실패")
FEATURE_HINTS = ("추가", "만들어", "만들자", "새로", "구현해", "붙여", "feature", "add",
                 "implement", "create", "지원")
# 리팩터/정리 — 제거·축소·수정·교체 등 '코드를 바꾸는 명령형'을 폭넓게 포함(질문으로 안 새게)
REFACTOR_HINTS = ("리팩토링", "리팩터", "정리해", "정리", "개선해", "개선", "refactor", "cleanup", "단순화",
                  "지워", "지우", "삭제", "제거", "없애", "빼줘", "빼라", "빼자", "줄여", "줄이", "간소화",
                  "수정해", "수정", "변경", "바꿔", "바꾸", "교체", "이름바꿔", "옮겨",
                  "remove", "delete", "strip", "drop", "prune", "rename", "replace", "modify", "update", "change")
QUESTION_HINTS = ("뭐야", "뭐가", "어디", "어때", "왜", "how", "what", "where", "why",
                  "설명", "알려줘", "있어?", "?")

BRANCH_PREFIX = {"bug": "fix/", "feature": "feature/", "refactor": "refactor/", "question": ""}


def _hit(query: str, hints: tuple[str, ...]) -> int:
    lowered = query.lower()
    return sum(1 for hint in hints if hint in lowered)


def classify(query: str, llm_base: str | None = None, llm_model: str | None = None) -> dict:
    scores = {"bug": _hit(query, BUG_HINTS), "feature": _hit(query, FEATURE_HINTS),
              "refactor": _hit(query, REFACTOR_HINTS), "question": _hit(query, QUESTION_HINTS)}
    # 변경 동사가 있으면 질문 어미보다 우선 — 단 질문 신호가 더 강하면 질문으로 남긴다.
    # ('설정 어디서 변경되는지 알려줘'처럼 서술형 '변경/수정'이 질문을 가로채면 답 대신
    #  브랜치를 만들어 코드를 고치려 든다. 반대 방향 오분류는 답만 주고 끝이라 훨씬 안전.)
    change_score = max(scores["bug"], scores["feature"], scores["refactor"])
    if change_score > 0 and change_score >= scores["question"]:
        intent = max(("bug", "feature", "refactor"), key=lambda k: scores[k])
        source = "heuristic"
    elif scores["question"] > 0:
        intent, source = "question", "heuristic"
    else:
        intent, source = "question", "heuristic-default"
        if llm_base and llm_model:
            answer = llm.json_chat(llm_base, llm_model, [
                {"role": "system", "content":
                 'Classify the dev request. Reply JSON only: {"intent":"bug|feature|refactor|question"}'},
                {"role": "user", "content": query}], max_tokens=50, timeout=20)
            if answer and answer.get("intent") in BRANCH_PREFIX:
                intent, source = answer["intent"], "llm"
    return {"intent": intent, "scores": scores, "source": source,
            "branch_prefix": BRANCH_PREFIX[intent]}
