"""단계 이벤트를 사람 말로.

원래는 이벤트 데이터를 JSON으로 찍어 보냈다. 그런데 화면에 나가는 건
`kg_search start {}` 같은 조각이라, 지금 무엇을 하고 있는지도 무엇을 찾았는지도
알 수 없었다. 실시간 로그가 실시간으로 아무것도 말해주지 않은 셈이다.

여기서는 각 단계가 "무엇을 했는지"를 한 줄로 만든다. 모르는 단계는 억지로 꾸미지
않고 빈 문자열을 준다 — 없는 말을 지어내느니 조용한 편이 낫다.
"""
from __future__ import annotations


def _short(path: str, keep: int = 46) -> str:
    return path if len(path) <= keep else "…" + path[-(keep - 1):]


def _hit_label(hit: dict) -> str:
    """검색 결과 하나를 '이름 (파일:줄)'로."""
    node_id = hit.get("id", "")
    name = hit.get("name") or node_id.split("#")[-1].split(":")[-1] or node_id
    path = hit.get("path", "")
    line = hit.get("line")
    if path:
        return f"{name} ({_short(path)}{':' + str(line) if line else ''})"
    return name or node_id


def describe(step: str, status: str, data: dict) -> str:
    """한 단계가 무엇을 했는지 한 줄. 없으면 빈 문자열."""
    get = data.get

    if step == "intent":
        return f"요청 유형: {get('intent', '')}"
    if step == "kg_search":
        if status == "start":
            return "지식그래프에서 관련 코드를 찾는 중"
        hits = get("landing") or get("hits") or []
        if not hits:
            return "관련 코드를 찾지 못했습니다"
        first = _hit_label(hits[0]) if isinstance(hits[0], dict) else str(hits[0])
        more = f" 외 {len(hits) - 1}곳" if len(hits) > 1 else ""
        return f"{first}{more}"
    if step == "query_expand":
        if status == "start":
            return "요청을 코드 용어로 바꾸는 중"
        if status == "fail":
            return get("note", "코드 용어 변환에 실패해 원문으로 찾습니다")
        words = str(get("keywords", "")).split()
        head = " · ".join(words[:6])
        return f"코드 용어: {head}{' …' if len(words) > 6 else ''}"
    if step == "impact":
        return f"이 코드를 쓰는 곳 {get('affected', 0)}곳"
    if step == "chain":
        return f"함께 볼 코드 {get('nodes', 0)}개"
    if step == "legacy_check":
        return f"실제 코드 {get('bytes', 0):,}자를 근거로 첨부"
    if step == "learnings":
        if status == "skipped":
            return get("reason", "")
        return f"과거 교훈 {get('count', 0)}건 반영"
    if step == "authorize":
        return get("reason", "") if status == "skipped" else "작업 권한 확인됨"
    if step == "fetch_latest":
        if status == "skipped":
            return get("reason", "")
        if status == "already_latest":
            return f"{get('target', '')} 이미 최신"
        return f"{get('target', '')} 최신 반영 · 파일 {get('kg_refreshed', 0)}개"
    if step == "worktree":
        return get("reason", "") if status == "skipped" else "격리 작업 공간 사용"
    if step == "branch":
        if status == "fail":
            return get("error", "브랜치를 만들지 못했습니다")
        return f"{get('branch', '')} 생성 (기준 {get('base', '')})"
    if step == "implement":
        if status == "start":
            phase = "다시 시도" if get("phase") == "retry" else "첫 시도"
            return f"에이전트가 코드를 고치는 중 ({phase} {get('n', 1)}회차)"
        return f"{get('files', 0)}개 파일 수정됨"
    if step == "agent":
        return str(get("text", ""))       # 에이전트가 실제로 한 일 — 그대로 보여준다
    if step == "checks":
        parts = [f"{k} {v}" for k, v in (get("summary") or {}).items()]
        sandbox = get("sandbox", "")
        head = f"샌드박스 {sandbox}" if sandbox else ""
        return " · ".join(x for x in [head, *parts] if x)
    if step == "judge":
        return f"품질 점수 {get('score', 0)} ({get('source', '')})"
    if step == "iteration":
        return "통과 — 다음 단계로" if get("decision") == "stop" else "기준 미달 — 다시 시도"
    if step in ("verify", "ui_verify", "deploy_test"):
        return get("reason", "") if status == "skipped" else get("note", "") or status
    if step == "release":
        return f"배포 환경 {get('env', '')} · 경로 {' → '.join(get('promotion') or [])}"
    if step == "commit":
        return f"커밋 {str(get('sha', ''))[:10]} · {get('files', 0)}개 파일"
    if step == "push":
        return get("reason", "") if status == "skipped" else f"{get('branch', '')} 원격에 올림"
    if step == "mr_create":
        return get("reason", "") if status == "skipped" else get("url", "")
    if step == "mr_ready":
        return "병합 요청 초안 준비됨"
    if step == "plan_only":
        return "분석 결과와 초안을 정리했습니다"
    if step == "answer":
        return f"관련 코드 {get('hits', 0)}곳을 근거로 정리"
    if step == "kg_refresh":
        return f"바뀐 파일 {get('files', 0)}개를 지식그래프에 반영"
    if step == "cost":
        return (f"에이전트 {get('agent_calls', 0)}회 · "
                f"추정 {get('est_total_tokens', 0):,} 토큰")
    if step == "session_end":
        return ""
    return str(get("reason") or get("note") or get("error") or "")
