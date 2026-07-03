"""⑨ MR 준비 — 상세 본문(무엇/왜/원인/접근/영향) + KG 영향분석 첨부.

observe 모드: MR-DRAFT.md 저장까지만. act 모드: GitLab API로 실제 MR 생성
(source=기능 브랜치, target=develop — 머지는 항상 사람).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

from ..config import MakerConfig


def build_mr_draft(query: str, intent: str, branch: str, target_branch: str,
                   changed_files: list[str], diff_stat: str,
                   impact_nodes: list[dict], judge_result: dict,
                   agent_summary: str = "",
                   checks: list[dict] | None = None) -> tuple[str, str]:
    """반환 = (title, body_markdown)."""
    title_prefix = {"bug": "fix", "feature": "feat", "refactor": "refactor"}.get(intent, "chore")
    title = f"{title_prefix}: {query[:80]}"
    impact_lines = "\n".join(
        f"- (거리 {n['distance']}) [{n['kind']}] {n['name']} — `{n['repo']}:{n['path']}`"
        for n in impact_nodes[:15]) or "- (KG상 파급 없음)"
    files_lines = "\n".join(f"- `{f}`" for f in changed_files) or "- (없음)"
    body = f"""## 무엇
{query}

## 왜 / 원인
{agent_summary or "(에이전트 구현 노트 참조 — worklogs 세션 journal)"}

## 접근
- 브랜치: `{branch}` → `{target_branch}` (MR-only, 직접 머지 금지)
- intent: {intent}

## 변경 파일
{files_lines}

## diff 요약
```
{diff_stat.strip() or "(없음)"}
```

## 영향 (지식그래프 분석)
{impact_lines}

## 자동 검증 (checks)
{chr(10).join(f"- {c['name']}: **{c['status']}**" + (f" — {c.get('reason', '')}" if c.get('reason') else "") for c in (checks or [])) or "- (미실행)"}

## 품질 게이트
- judge: **{judge_result.get('score')}** (θ={judge_result.get('theta')}, {judge_result.get('source')})
{chr(10).join(f"- {r}" for r in judge_result.get('reasons', []))}

---
*XGEN MAKER 자동 생성 MR 초안 — 반영은 사람 승인.*
"""
    return title, body


def save_draft(session_dir: Path, title: str, body: str) -> Path:
    path = session_dir / "MR-DRAFT.md"
    path.write_text(f"# {title}\n\n{body}", encoding="utf-8")
    return path


def create_gitlab_mr(config: MakerConfig, repo: str, branch: str,
                     title: str, body: str) -> dict:
    """act 모드 전용. 반환 {ok, url|error}."""
    project = config.gitlab_projects.get(repo)
    if not project:
        return {"ok": False, "error": f"gitlab_projects에 '{repo}' 매핑 없음"}
    if not config.gitlab_token:
        return {"ok": False, "error": "XGEN_MAKER_GITLAB_TOKEN 미설정"}
    encoded = urllib.parse.quote_plus(project)
    payload = {"source_branch": branch, "target_branch": config.target_branch,
               "title": title, "description": body, "remove_source_branch": False}
    request = urllib.request.Request(
        f"{config.gitlab_url}/api/v4/projects/{encoded}/merge_requests",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "PRIVATE-TOKEN": config.gitlab_token},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        return {"ok": True, "url": data.get("web_url", "")}
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as error:
        return {"ok": False, "error": str(error)}
