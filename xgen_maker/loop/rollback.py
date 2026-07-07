"""롤백 안전망 — MAKER가 만든 브랜치·커밋·푸시를 되돌린다.

세션 journal에서 액션(repo·브랜치·base·push·MR)을 읽어 복원 계획을 세운다.
로컬 브랜치 삭제는 기본, 원격 브랜치 삭제는 --remote(외부 반영이라 명시 필요).
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import is_protected_branch, is_allowed_branch
from .git_ops import GitRepo, GitOpsError


def last_action(worklogs_dir: str | Path) -> dict | None:
    """최근 세션 journal에서 되돌릴 액션 추출."""
    root = Path(worklogs_dir)
    if not root.is_dir():
        return None
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
        branch_ev = next((e for e in events if e["step"] == "branch" and e["status"] == "ok"), None)
        if not branch_ev:
            continue  # 브랜치 안 만든 세션은 되돌릴 것 없음
        repo = next((e.get("repo") for e in events if e["step"] == "session_end"), "") or \
            next((e.get("repo") for e in events if "repo" in e), "")
        pushed = any(e["step"] == "push" and e["status"] == "ok" for e in events)
        mr = next((e.get("url") for e in events if e["step"] == "mr_create" and e.get("url")), "")
        committed = any(e["step"] == "commit" and e["status"] == "ok" for e in events)
        return {"session": session_dir.name, "branch": branch_ev.get("branch"),
                "base": branch_ev.get("base", ""), "repo": repo,
                "pushed": pushed, "mr": mr, "committed": committed}
    return None


def undo(config, action: dict, delete_remote: bool = False) -> dict:
    """액션 되돌리기. 반환 {ok, steps[], errors[]}."""
    repo = action.get("repo", "")
    branch = action.get("branch", "")
    base = action.get("base") or config.target_branch
    repo_path = config.repos.get(repo)
    steps, errors = [], []
    if not repo_path or not branch:
        return {"ok": False, "steps": [], "errors": ["repo 경로/브랜치 미상 — 수동 확인 필요"]}
    # 안전 불변식 재확인 — 저널이 오염/수기편집돼도 보호 브랜치는 절대 삭제 안 함
    if is_protected_branch(branch) or not is_allowed_branch(branch):
        return {"ok": False, "steps": [],
                "errors": [f"'{branch}'는 보호 브랜치이거나 허용 prefix 밖 — "
                           "롤백 삭제 거부(수동 확인 필요)"]}
    try:
        git = GitRepo(repo_path)
    except GitOpsError as e:
        return {"ok": False, "steps": [], "errors": [str(e)]}
    # 1) 현재 브랜치가 대상이면 base로 이동
    try:
        if git.current_branch() == branch:
            git.checkout(base)
            steps.append(f"checkout {base}")
    except GitOpsError as e:
        errors.append(f"checkout: {e}")
    # 2) 로컬 브랜치 삭제
    try:
        git._run("branch", "-D", branch)
        steps.append(f"로컬 브랜치 삭제 {branch}")
    except GitOpsError as e:
        errors.append(f"로컬 삭제: {e}")
    # 3) 원격 브랜치 삭제 (명시 시)
    if delete_remote and action.get("pushed"):
        token = config.gitlab_token
        try:
            remote_url = git._run("remote", "get-url", "origin").strip()
            if token and remote_url.startswith("https://"):
                host_path = remote_url.split("://", 1)[1].split("@")[-1]
                git._run("-c", "credential.helper=", "push",
                         f"https://oauth2:{token}@{host_path}", "--delete", branch)
            else:
                git._run("push", "origin", "--delete", branch)
            steps.append(f"원격 브랜치 삭제 {branch}")
        except GitOpsError as e:
            errors.append(f"원격 삭제: {e}")
    return {"ok": not errors, "steps": steps, "errors": errors,
            "mr_note": f"MR {action['mr']}는 수동 close 필요" if action.get("mr") else ""}
