"""⑨ MR 준비 — 상세 본문(무엇/왜/원인/접근/영향) + KG 영향분석 첨부.

observe 모드: MR-DRAFT.md 저장까지만. act 모드: GitLab API로 실제 MR 생성
(source=기능 브랜치, target=develop — 머지는 항상 사람).
"""
from __future__ import annotations

from ..codes import ErrorCode

import json
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

from ..config import MakerConfig


_REGRESSION_NOTE = {
    "verified": "✅ **검증됨** — 레포 테스트 스위트가 실제로 통과(레거시 안 깨짐 확인).",
    "partial": "🟡 **부분 검증** — 변경과 관련된 테스트만 통과. 전체 스위트는 돌리지 않았으므로 "
               "안 돌린 영역의 회귀는 **보증하지 않음**(리뷰·CI에서 확인 필요).",
    "unverified": "⚠️ **미검증** — 돌릴 테스트는 있으나 환경(의존성)에서 못 돌림. "
                  "레거시가 안 깨졌다고 **단정할 수 없음** — 테스트 환경에서 재검증 필요.",
    "failed": "❌ **실패** — 레거시 회귀 감지(이 MR은 차단되어야 함).",
    "none": "➖ 해당 없음 — 돌릴 회귀 테스트가 레포에 없음.",
}


def build_mr_draft(query: str, intent: str, branch: str, target_branch: str,
                   changed_files: list[str], diff_stat: str,
                   impact_nodes: list[dict], judge_result: dict,
                   agent_summary: str = "",
                   checks: list[dict] | None = None,
                   release_md: str = "", regression: str = "",
                   sandbox_isolated: bool | None = None) -> tuple[str, str]:
    """반환 = (title, body_markdown)."""
    title_prefix = {"bug": "fix", "feature": "feat", "refactor": "refactor"}.get(intent, "chore")
    title = f"{title_prefix}: {query[:80]}"
    sandbox_note = ("" if sandbox_isolated is None else
                    ("🔒 엔진 샌드박스 격리 검증" if sandbox_isolated
                     else "🔓 로컬 검증(엔진 샌드박스 미설치 — `pip install .[harness]`로 격리 활성)"))
    regression_note = _REGRESSION_NOTE.get(regression, "")
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
{chr(10).join(f"- {c.get('name', '?')}: **{c.get('status', '?')}**" + (f" — {c.get('reason', '')}" if c.get('reason') else "") for c in (checks or [])) or "- (미실행)"}

## 레거시 회귀 (기존 동작 안 깨졌나)
{regression_note or "- (미판정)"}
{("- 샌드박스: " + sandbox_note) if sandbox_note else ""}

## 품질 게이트
- judge: **{judge_result.get('score')}** (θ={judge_result.get('theta')}, {judge_result.get('source')})
{"- ⚠️ **휴리스틱 판정** — LLM judge 미가동(크기·집중도 기반 근사치). 실제 품질 평가 아님, 리뷰 필수." if judge_result.get('source') == 'heuristic' else ""}
{chr(10).join(f"- {r}" for r in judge_result.get('reasons', []))}

## 릴리즈 사다리 (develop → stg → main)
{release_md or "- (인프라 KG 없음 — maker kg infra 후 재병합 시 표시)"}

---
*XGEN MAKER 자동 생성 MR 초안 — 반영은 사람 승인.*
"""
    return title, body


def save_draft(session_dir: Path, title: str, body: str) -> Path:
    path = session_dir / "MR-DRAFT.md"
    path.write_text(f"# {title}\n\n{body}", encoding="utf-8")
    return path


def create_gitlab_mr(config: MakerConfig, repo: str, branch: str,
                     title: str, body: str, target_branch: str = "",
                     repo_root: str = "") -> dict:
    """act 모드 전용. 반환 {ok, url|error}.

    저장소가 실제로 이 GitLab에 있는지 먼저 본다. 설정에 매핑이 있다는 것만으로
    MR을 만들려 들면, 다른 호스트(GitHub 등)에 사는 저장소에 대해 GitLab에 요청이
    가고 토큰까지 함께 나간다. 같은 이름의 프로젝트가 거기 있으면 엉뚱한 저장소에
    MR이 열린다 — 실패보다 나쁘다.
    """
    project = config.gitlab_projects.get(repo)
    if not project:
        return {"ok": False, "code": ErrorCode.MR_NO_PROJECT.value,
                "error": f"gitlab_projects에 '{repo}' 매핑 없음"}
    root = repo_root or (config.repos or {}).get(repo, "")
    if root:
        from .git_ops import GitRepo
        if not GitRepo(root).token_host_matches(config.gitlab_url):
            return {"ok": False, "code": ErrorCode.MR_NO_PROJECT.value,
                    "error": f"'{repo}'의 원격은 이 GitLab이 아닙니다 — MR을 만들지 않습니다"}
    if not config.gitlab_token:
        return {"ok": False, "code": ErrorCode.MR_NO_TOKEN.value,
                "error": "XGEN_MAKER_GITLAB_TOKEN 미설정"}
    encoded = urllib.parse.quote_plus(project)
    # 저장소마다 통합 브랜치가 다르다. 전역 설정값을 그대로 쓰면 없는 브랜치를 대상으로 연다.
    payload = {"source_branch": branch,
               "target_branch": target_branch or config.target_branch,
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
