"""GitLab 관측 (read-only) — 브랜치·본인 MR 이력. MAKER는 조회만, 변경은 push/MR 준비 경로로만.

자격: config.gitlab_token(=env/.env/auth). 프로젝트 경로: config.gitlab_projects[repo].
"""
from __future__ import annotations

import urllib.parse
import urllib.request
import urllib.error
import json


def _api(config, path: str, timeout: int = 25) -> object | None:
    token = config.gitlab_token
    if not token:
        return None
    url = config.gitlab_url.rstrip("/") + "/api/v4" + path
    request = urllib.request.Request(url, headers={"PRIVATE-TOKEN": token})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None


def _project_enc(config, repo: str) -> str | None:
    proj = config.gitlab_projects.get(repo)
    return urllib.parse.quote_plus(proj) if proj else None


def branches(config, repo: str, limit: int = 100) -> dict:
    """레포 브랜치 개요: 보호·release(develop/stg/main)·최근 feature/fix."""
    enc = _project_enc(config, repo)
    if enc is None:
        return {"error": f"'{repo}' gitlab_projects 매핑 없음"}
    data = _api(config, f"/projects/{enc}/repository/branches?per_page={limit}")
    if not isinstance(data, list):
        return {"error": "조회 실패(토큰/네트워크)"}
    release = [b["name"] for b in data if b["name"] in ("develop", "stg", "staging", "main", "master")]
    protected = [b["name"] for b in data if b.get("protected")]
    work = [{"name": b["name"], "merged": b.get("merged", False),
             "author": (b.get("commit") or {}).get("author_name", ""),
             "when": (b.get("commit") or {}).get("committed_date", "")[:10]}
            for b in data if b["name"].startswith(("fix/", "feature/", "refactor/", "chore/"))]
    work.sort(key=lambda b: b["when"], reverse=True)
    return {"total": len(data), "release": release, "protected": protected,
            "work_recent": work[:15]}


def my_mrs(config, state: str = "all", limit: int = 15) -> list[dict]:
    """본인(토큰 소유자=김진수) MR 이력."""
    data = _api(config, f"/merge_requests?scope=created_by_me&state={state}"
                        f"&per_page={limit}&order_by=updated_at")
    if not isinstance(data, list):
        return []
    return [{"iid": m["iid"], "state": m["state"], "title": m["title"],
             "source": m["source_branch"], "target": m["target_branch"],
             "url": m.get("web_url", ""), "updated": m.get("updated_at", "")[:10],
             "project": m.get("references", {}).get("full", "")}
            for m in data]


def maker_mrs(config, limit: int = 15) -> list[dict]:
    """MAKER가 만든 브랜치(fix/*-<slug>)에서 나온 MR만 필터."""
    mrs = my_mrs(config, "all", 50)
    maker = [m for m in mrs if m["source"].startswith(("fix/", "feature/", "refactor/"))
             and any(seg in m["source"] for seg in ("-", "task-"))]
    return maker[:limit]
