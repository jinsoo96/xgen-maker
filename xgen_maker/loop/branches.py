"""로컬 브랜치 관리 — 무엇을 지워도 되는지 기록으로 판단한다.

브랜치는 쌓인다. 그런데 이름만 봐서는 MAKER가 만든 것과 사람이 직접 만든 것이
구분되지 않는다(둘 다 feature/…). 지우다 사람의 작업을 날리면 되돌릴 방법이 없다.

그래서 추측하지 않는다. MAKER가 만든 브랜치는 세션 기록(journal)에 남아 있으므로
그걸 대조한다. 기록에 없으면 사람 것으로 보고 더 강하게 경고한다.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..config import is_protected_branch


def _git(root: str | Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=60)
    if result.returncode != 0:
        raise OSError(result.stderr.strip()[:200])
    return result.stdout


def maker_branches(worklogs_dir: str | Path) -> dict[str, set[str]]:
    """MAKER가 만든 브랜치 — 저장소별 이름 집합. 세션 기록이 근거다."""
    made: dict[str, set[str]] = {}
    root = Path(worklogs_dir)
    if not root.is_dir():
        return made
    for journal in root.glob("*/journal.jsonl"):
        try:
            lines = journal.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("step") == "branch" and event.get("branch"):
                made.setdefault(event.get("repo", ""), set()).add(event["branch"])
    return made


def list_local(config) -> list[dict]:
    """저장소별 로컬 브랜치와 '지워도 되는가'에 필요한 사실들."""
    made = maker_branches(config.worklogs_dir)
    target = config.target_branch
    out: list[dict] = []
    seen_roots: set[str] = set()
    for repo, root in (config.repos or {}).items():
        key = str(Path(root).resolve()).lower()
        if key in seen_roots:      # 한 클론을 여러 스코프가 공유하면 한 번만
            continue
        seen_roots.add(key)
        try:
            current = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
            raw = _git(root, "for-each-ref", "--format=%(refname:short)\t%(committerdate:short)",
                       "refs/heads")
        except OSError:
            continue
        merged: set[str] = set()
        for ref in (f"origin/{target}", target):
            try:
                merged |= {b.strip().lstrip("* ").strip()
                           for b in _git(root, "branch", "--merged", ref).splitlines()
                           if b.strip()}
                break
            except OSError:
                continue
        for line in raw.splitlines():
            if not line.strip():
                continue
            name, _, when = line.partition("\t")
            out.append({
                "repo": repo, "name": name, "when": when,
                "current": name == current,
                "protected": is_protected_branch(name),
                "merged": name in merged,
                "by_maker": name in made.get(repo, set()),
            })
    return out


def delete_local(config, repo: str, name: str) -> dict:
    """로컬 브랜치 삭제. 안전 규칙을 여기서 다시 확인한다(화면을 믿지 않는다)."""
    root = (config.repos or {}).get(repo)
    if not root:
        return {"ok": False, "error": f"'{repo}' 저장소 경로가 설정되지 않았습니다"}
    if not name or is_protected_branch(name):
        return {"ok": False, "error": f"'{name}'은 보호 브랜치입니다 — 지울 수 없습니다"}
    try:
        current = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except OSError as e:
        return {"ok": False, "error": str(e)}
    if name == current:
        return {"ok": False,
                "error": f"'{name}'은 지금 체크아웃되어 있습니다 — 다른 브랜치로 옮긴 뒤 지우세요"}
    try:
        # -D는 병합 안 된 것도 지운다. 화면에서 확인을 받았을 때만 여기까지 온다.
        _git(root, "branch", "-D", name)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "repo": repo, "name": name,
            "note": "로컬에서만 지웠습니다 — 원격 브랜치는 그대로입니다"}
