"""MAKER 본인 이력 — worklogs/*/journal.jsonl에서 자기가 한 작업을 읽는다.

각 세션: 쿼리 → outcome → 브랜치 → MR. "내가 뭘 했는지" 확인용.
"""
from __future__ import annotations

import json
from pathlib import Path


def session_path(worklogs_dir: str | Path, session_id: str) -> Path | None:
    """세션 폴더 경로. 이름이 조작돼 worklogs 밖을 가리키면 거부한다."""
    root = Path(worklogs_dir).resolve()
    if not session_id or session_id in (".", ".."):
        return None
    target = (root / session_id).resolve()
    if not target.is_relative_to(root) or target == root:
        return None
    return target if target.is_dir() else None


def delete_session(worklogs_dir: str | Path, session_id: str) -> dict:
    """세션 기록을 지운다. 지우는 건 기록뿐 — 코드·브랜치는 건드리지 않는다.

    브랜치가 남아 있으면 알린다. 기록만 지우면 그 브랜치를 되돌릴 근거가 사라져,
    나중에 "이게 왜 있지" 하는 브랜치만 남는다.
    """
    import shutil
    target = session_path(worklogs_dir, session_id)
    if target is None:
        return {"ok": False, "error": "세션을 찾을 수 없습니다"}
    detail = read_session_detail(worklogs_dir, session_id) or {}   # 기록이 깨졌어도 지울 수 있게
    branch = detail.get("branch") or ""
    try:
        shutil.rmtree(target)
    except OSError as e:
        return {"ok": False, "error": f"삭제 실패: {e}"}
    return {"ok": True, "session": session_id, "branch": branch,
            "note": (f"브랜치 {branch}는 그대로 있습니다 — 필요하면 직접 정리하세요"
                     if branch else "")}


def read_sessions(worklogs_dir: str | Path, limit: int = 20) -> list[dict]:
    root = Path(worklogs_dir)
    if not root.is_dir():
        return []
    sessions = []
    for session_dir in sorted(root.iterdir(), reverse=True):
        journal = session_dir / "journal.jsonl"
        if not journal.is_file():
            continue
        events = []
        try:
            for line in journal.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            continue
        if not events:
            continue
        query = next((e.get("query") for e in events if e.get("step") == "session_start"), "")
        outcome = next((e.get("status") for e in events if e.get("step") == "session_end"), "?")
        branch = next((e.get("branch") for e in events if e.get("step") == "branch"), "")
        mr = next((e.get("url") for e in events if e.get("step") == "mr_create" and e.get("url")), "")
        env = next((e.get("env") for e in events if e.get("step") == "release"), "")
        sessions.append({"session": session_dir.name, "query": query,
                         "outcome": outcome, "branch": branch, "mr": mr,
                         "env": env, "steps": len(events)})
        if len(sessions) >= limit:
            break
    return sessions


# 이벤트별로 사람이 볼 한 줄 요약을 뽑는다(원시 dict는 감춘다)
def _step_summary(e: dict) -> str:
    step = e.get("step", "")
    if step == "session_start":
        return e.get("query", "")
    if step == "intent":
        return f"{e.get('intent', '')}"
    if step == "query_expand":
        return e.get("keywords", "")
    if step == "kg_search":
        hits = e.get("hits", [])
        top = hits[0].get("id", "") if hits else ""
        return f"{len(hits)}곳 후보 · 최상위 {top}" if hits else ""
    if step == "impact":
        return e.get("target", "")
    if step == "branch":
        return f"{e.get('branch', '')} (base {e.get('base', '')})"
    if step in ("commit", "push"):
        return e.get("branch", "") or e.get("sha", "")
    if step == "mr_create":
        return e.get("url", "")
    if step in ("checks", "judge"):
        return str({k: v for k, v in e.items()
                    if k not in ("step", "status", "ts", "iso", "time")})[:120]
    if step == "answer":
        return f"인용 {e.get('hits', '')}곳"
    return str({k: v for k, v in e.items()
                if k not in ("step", "status", "ts", "iso", "time")})[:120]


def read_test_runs(worklogs_dir: str | Path, limit: int = 40) -> list[dict]:
    """검증·테스트 이력 — 세션별 checks/verify/sandbox/judge 결과를 모은다(테스트 탭용)."""
    root = Path(worklogs_dir)
    if not root.is_dir():
        return []
    runs = []
    for session_dir in sorted(root.iterdir(), reverse=True):
        journal = session_dir / "journal.jsonl"
        if not journal.is_file():
            continue
        try:
            events = [json.loads(l) for l in
                      journal.read_text(encoding="utf-8").splitlines() if l.strip()]
        except (OSError, json.JSONDecodeError):
            continue
        checks = [e for e in events if e.get("step") == "checks"]
        verify = next((e for e in events if e.get("step") == "verify"), None)
        judge = next((e for e in events if e.get("step") == "judge"), None)
        if not checks and not verify and not judge:
            continue  # 검증을 아예 안 탄 세션(질문형 등)은 제외
        query = next((e.get("query") for e in events if e.get("step") == "session_start"), "")
        last = checks[-1] if checks else {}
        runs.append({
            "session": session_dir.name, "query": query,
            "iterations": len(checks),
            "checks_status": last.get("status", "-"),
            "sandbox": last.get("sandbox", verify.get("status") if verify else "-"),
            "regression": last.get("regression") or "-",
            "summary": last.get("summary", "") or (verify.get("reason", "") if verify else ""),
            "judge": (judge.get("status") if judge else "-"),
            # 엔진은 heuristic/llm을 정직하게 기록한다. 여기서 status만 뽑아버리면
            # '작고 집중된 diff면 무조건 통과'하는 휴리스틱 점수가 실제 품질 판정과
            # 똑같은 초록 배지로 보인다 → 근거(점수·출처)를 같이 올린다.
            "judge_score": (judge.get("score") if judge else None),
            "judge_source": (judge.get("source") if judge else ""),
        })
        if len(runs) >= limit:
            break
    return runs


def maker_branches(worklogs_dir: str | Path, limit: int = 200) -> set[str]:
    """MAKER가 실제로 만든 브랜치 이름 집합 — journal의 branch/ok 이벤트가 정본.
    (GitLab에서 이름만 보고 추측하지 않고, 자기가 만든 것만 정확히 안다.)
    세션은 무한히 쌓이므로 최근 limit개만 읽는다(MR 탭 열 때마다 전량 파싱 방지)."""
    root = Path(worklogs_dir)
    names: set[str] = set()
    if not root.is_dir():
        return names
    for session_dir in sorted(root.iterdir(), reverse=True)[:limit]:
        journal = session_dir / "journal.jsonl"
        if not journal.is_file():
            continue
        try:
            for line in journal.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                e = json.loads(line)
                if e.get("step") == "branch" and e.get("status") == "ok" and e.get("branch"):
                    names.add(e["branch"])
        except (OSError, json.JSONDecodeError):
            continue
    return names


def read_session_detail(worklogs_dir: str | Path, session_name: str) -> dict | None:
    """단일 세션 상세 — 단계 타임라인 + SUMMARY.md 원문."""
    sess_dir = Path(worklogs_dir) / session_name
    journal = sess_dir / "journal.jsonl"
    if not journal.is_file():
        return None
    events = []
    try:
        for line in journal.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        return None
    steps = [{"step": e.get("step", ""), "status": e.get("status", ""),
              "iso": e.get("iso", "") or e.get("ts", ""),
              "summary": _step_summary(e)} for e in events]
    query = next((e.get("query") for e in events if e.get("step") == "session_start"), "")
    outcome = next((e.get("status") for e in events if e.get("step") == "session_end"), "?")
    branch = next((e.get("branch") for e in events if e.get("step") == "branch"), "")
    mr = next((e.get("url") for e in events if e.get("step") == "mr_create" and e.get("url")), "")
    summary_md = ""
    sm = sess_dir / "SUMMARY.md"
    if sm.is_file():
        try:
            summary_md = sm.read_text(encoding="utf-8")
        except OSError:
            summary_md = ""
    return {"session": session_name, "query": query, "outcome": outcome,
            "branch": branch, "mr": mr, "steps": steps, "summary_md": summary_md}
