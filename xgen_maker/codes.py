"""이벤트/결과 코드 카탈로그 — MAKER 루프가 내보내는 이름의 단일 정본.

차용: CocoRoF/geny-executor (events/catalog.py, core/errors.py)의 설계 —
"이벤트 스트림/에러 문자열은 사실상 호스트 계약인데 리터럴로 흩어지면 오탐·유실 버그가 난다"는
교훈을 MAKER journal에 적용. 값(문자열)은 기존과 동일 → 기존 로그 소비자 무손상.

계약:
- 값 = 와이어 문자열. `Event.INTENT == "intent"` (이름 레지스트리, 리네임 아님)
- append-only. 새 이름 추가는 OK, 기존 이름 변경/삭제는 파괴적 변경
- 완결성은 test_codes로 강제: pipeline이 journal.event(...)에 넘기는 step 리터럴은
  전부 여기 등록돼 있어야 한다(uncatalogued emit 차단)
"""
from __future__ import annotations

from enum import Enum


class Event(str, Enum):
    """루프 진행 이벤트(journal step). '지금 뭘 하는지' 로그의 어휘."""
    SESSION_START = "session_start"
    INTENT = "intent"
    QUERY_EXPAND = "query_expand"
    KG_SEARCH = "kg_search"
    ANSWER = "answer"
    IMPACT = "impact"
    CHAIN = "chain"
    LEGACY_CHECK = "legacy_check"
    PLAN_ONLY = "plan_only"
    BRANCH = "branch"
    IMPLEMENT = "implement"
    CHECKS = "checks"
    CHECKS_DETAIL = "checks_detail"
    VERIFY = "verify"
    JUDGE = "judge"
    COMMIT = "commit"
    PUSH = "push"
    MR_CREATE = "mr_create"
    MR_READY = "mr_ready"
    DEPLOY = "deploy"
    KG_REFRESH = "kg_refresh"
    SESSION_END = "session_end"


class Outcome(str, Enum):
    """루프의 종료 결과. report['outcome']의 안정 코드."""
    ANSWERED = "answered"              # 질문 intent — 검색 답변만
    NO_LANDING = "no_landing"         # 착지 실패
    PLANNED = "planned"               # plan-only — MR 초안까지, 레포 미접촉
    BRANCH_FAILED = "branch_failed"   # 워킹트리 dirty 등
    IMPLEMENT_FAILED = "implement_failed"  # 코딩에이전트 실패
    CHECKS_FAILED = "checks_failed"   # 자동 검증(테스트/구문) 실패 → MR 차단
    JUDGE_FAILED = "judge_failed"     # 품질 게이트 θ 미달
    PUSH_FAILED = "push_failed"       # act 푸시 실패
    MR_PREPARED = "mr_prepared"       # 정상 — MR 준비(observe) 또는 MR 생성(act)


class ErrorCode(str, Enum):
    """실패 원인의 안정 식별자 — maker.<component>.<reason>. 로그 그룹핑·재시도 분기용."""
    GIT_DIRTY = "maker.git.dirty_worktree"
    GIT_BRANCH_ILLEGAL = "maker.git.illegal_branch"
    GIT_PROTECTED_PUSH = "maker.git.protected_push"
    AGENT_NOT_FOUND = "maker.agent.not_found"
    AGENT_TIMEOUT = "maker.agent.timeout"
    AGENT_EXIT = "maker.agent.nonzero_exit"
    CHECKS_SYNTAX = "maker.checks.syntax_failed"
    CHECKS_TEST = "maker.checks.test_failed"
    JUDGE_BELOW_THETA = "maker.judge.below_theta"
    JUDGE_INFRA_VETO = "maker.judge.infra_veto"
    JUDGE_EMPTY_DIFF = "maker.judge.empty_diff"
    MR_NO_PROJECT = "maker.mr.no_project_mapping"
    MR_NO_TOKEN = "maker.mr.no_token"
    DEPLOY_REFUSED = "maker.deploy.interlock_refused"


# 이벤트별 페이로드 설명(엄격 스키마 아님 — 관측용 문서)
PAYLOADS: dict[str, str] = {
    Event.INTENT: "intent, scores, source, branch_prefix",
    Event.KG_SEARCH: "hits[] (id, kind, score)",
    Event.IMPACT: "target, affected(int)",
    Event.BRANCH: "branch, base",
    Event.CHECKS: "py_syntax/pytest/node_test 각 상태",
    Event.JUDGE: "score, passed, source, theta, reasons[]",
    Event.COMMIT: "sha, files(int)",
    Event.MR_CREATE: "ok, url|error",
    Event.DEPLOY: "sent(bool), plan|reason",
    Event.SESSION_END: "(status=Outcome 값)",
}

ALL_EVENTS = frozenset(e.value for e in Event)
ALL_OUTCOMES = frozenset(o.value for o in Outcome)
