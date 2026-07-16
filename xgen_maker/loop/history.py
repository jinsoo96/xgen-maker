"""MAKER 본인 이력 — worklogs/*/journal.jsonl에서 자기가 한 작업을 읽는다.

각 세션: 쿼리 → outcome → 브랜치 → MR. "내가 뭘 했는지" 확인용.
"""
from __future__ import annotations

import json
from pathlib import Path


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
