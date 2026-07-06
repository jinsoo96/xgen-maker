"""작업 학습 메모리 — 하네스가 과거 작업을 참고해 실수를 반복하지 않게.

worklogs(세션 로그)와 별개로, `learnings/` 폴더에 "이 영역에서 이런 실수/교훈이 있었다"를 쌓는다.
루프는 구현 전에 관련 학습을 꺼내 에이전트 프롬프트에 주입한다(실수 방지).

저장: learnings/<repo>.jsonl  (한 줄 = 한 학습)
자동 기록: checks_failed·judge_failed 등 실패 시 "이 영역 이런 실패" 기록.
수동 기록: maker learn 명령.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


def _store(learnings_dir: str | Path, repo: str) -> Path:
    d = Path(learnings_dir)
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.-]", "_", repo) or "_"
    return d / f"{safe}.jsonl"


def record(learnings_dir: str | Path, repo: str, area: str, kind: str,
           note: str, query: str = "") -> None:
    """학습 1건 기록. kind: pitfall(함정)·fix(해결)·convention(규약)·note."""
    entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "repo": repo, "area": area, "kind": kind, "note": note[:400], "query": query[:120]}
    with _store(learnings_dir, repo).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _all(learnings_dir: str | Path, repo: str) -> list[dict]:
    path = _store(learnings_dir, repo)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def retrieve(learnings_dir: str | Path, repo: str, keywords: list[str],
             limit: int = 5) -> list[dict]:
    """repo의 학습 중 area/note가 keywords와 겹치는 것 상위 N (최신 우선)."""
    entries = _all(learnings_dir, repo)
    kw = {k.lower() for k in keywords if len(k) >= 3}
    scored = []
    for e in entries:
        hay = (e.get("area", "") + " " + e.get("note", "")).lower()
        score = sum(1 for k in kw if k in hay)
        if score or not kw:
            scored.append((score, e))
    scored.sort(key=lambda p: (-p[0], p[1].get("ts", "")), reverse=False)
    scored.sort(key=lambda p: (p[0], p[1].get("ts", "")), reverse=True)
    return [e for _, e in scored[:limit]]


def as_prompt_block(learnings: list[dict]) -> str:
    """구현 프롬프트에 넣을 '과거 학습' 블록."""
    if not learnings:
        return ""
    lines = ["[과거 학습 — 같은 실수 반복 금지]"]
    for e in learnings:
        lines.append(f"- ({e.get('kind')}) {e.get('area')}: {e.get('note')}")
    return "\n".join(lines)


def area_of(landing: list[dict]) -> str:
    """착지점에서 작업 영역(대표 경로) 추출."""
    if not landing:
        return "?"
    top = landing[0]
    path = top.get("path", "")
    return "/".join(path.split("/")[:3]) if path else top.get("name", "?")
