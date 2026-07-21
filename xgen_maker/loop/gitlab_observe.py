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


def activity(config, repo: str, query: str = "", limit: int = 30) -> dict:
    """레포 커밋 활동 — 누가 언제 뭘 고쳤나. query 있으면 커밋 메시지 검색(GitLab Search API)."""
    enc = _project_enc(config, repo)
    if enc is None:
        return {"error": f"'{repo}' gitlab_projects 매핑 없음"}
    q = query.strip()
    if q:
        # 커밋 메시지 검색
        path = (f"/projects/{enc}/search?scope=commits"
                f"&search={urllib.parse.quote_plus(q)}&per_page={limit}")
    else:
        path = f"/projects/{enc}/repository/commits?per_page={limit}"
    data = _api(config, path)
    if not isinstance(data, list):
        return {"error": "조회 실패(토큰/네트워크/권한)"}
    ql = q.lower()
    commits = []
    for c in data:
        author = c.get("author_name", "") or c.get("committer_name", "")
        message = c.get("message") or ""
        title = c.get("title") or (message.splitlines()[0] if message.strip() else "")
        # 검색어가 저자명에도 걸리면 포함(서버 검색은 메시지만 보므로 보강)
        if q and ql not in title.lower() and ql not in author.lower() and ql not in message.lower():
            continue
        commits.append({"sha": c.get("short_id", "") or c.get("id", "")[:8],
                        "author": author,
                        "when": (c.get("committed_date", "") or c.get("created_at", ""))[:16].replace("T", " "),
                        "title": title, "url": c.get("web_url", "")})
    return {"commits": commits[:limit], "query": q}


def _mr_rows(data) -> list[dict]:
    if not isinstance(data, list):
        return []
    return [{"iid": m["iid"], "state": m["state"], "title": m["title"],
             "source": m["source_branch"], "target": m["target_branch"],
             "url": m.get("web_url", ""), "updated": m.get("updated_at", "")[:10],
             "project": m.get("references", {}).get("full", ""),
             "author": (m.get("author") or {}).get("name", "")}
            for m in data]


def my_mrs(config, state: str = "all", limit: int = 15) -> list[dict]:
    """본인(토큰 소유자) MR 이력."""
    return _mr_rows(_api(config, f"/merge_requests?scope=created_by_me&state={state}"
                                 f"&per_page={limit}&order_by=updated_at"))


def team_mrs(config, state: str = "all", limit: int = 30, repo: str | None = None) -> list[dict]:
    """팀 전체 MR — 나뿐 아니라 누가 뭘 올렸는지. repo 지정 시 그 프로젝트만.

    프로젝트 범위가 있으면 그걸 쓰고(권한 내 정확), 없으면 토큰이 볼 수 있는
    전체에서 최신순으로 가져온다.
    """
    if repo:
        enc = _project_enc(config, repo)
        if enc is None:
            return []
        path = (f"/projects/{enc}/merge_requests?state={state}"
                f"&per_page={limit}&order_by=updated_at")
    else:
        path = f"/merge_requests?scope=all&state={state}&per_page={limit}&order_by=updated_at"
    return _mr_rows(_api(config, path))


def maker_mrs(config, limit: int = 15) -> list[dict]:
    """MAKER가 실제로 만든 MR만 — 로컬 journal에 기록된 브랜치와 일치하는 것.
    (이름 추측 금지: 사람이 손으로 만든 feature/* 브랜치를 MAKER 것으로 오인하지 않는다.)"""
    from .history import maker_branches
    made = maker_branches(config.worklogs_dir)
    if not made:
        return []
    mrs = my_mrs(config, "all", 50)
    return [m for m in mrs if m["source"] in made][:limit]
