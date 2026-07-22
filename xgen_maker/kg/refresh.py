"""레포 최신화 — 원격을 받아오고 지식그래프를 맞춘다.

**안전 불변식(이 모듈이 지키는 약속)**
사용자의 작업 상태를 절대 건드리지 않는다. 이 서버의 레포들은 사람이 쓰던 작업
브랜치(fix/*, refactor/*)에 있고, 업스트림이 없는 경우도 많다. 그래서:

- 하는 것   : `git fetch --prune`(워킹트리 불변) + 안전할 때만 `merge --ff-only`
- 안 하는 것: checkout · stash · rebase · reset · 비-FF merge · force — 하나도 안 한다
- 못 하면   : 건너뛰고 **이유를 남긴다**(조용히 실패하지 않는다)

fast-forward 조건(셋 다 만족해야):
  ① 워킹트리 깨끗(미커밋 변경 0)  ② 현재 브랜치에 업스트림 있음  ③ 뒤처지기만 함(발산 아님)

fetch는 항상 성공하므로 origin/* 참조는 최신이 된다. 다만 지식그래프는 '체크아웃된
파일'을 읽으므로, FF를 못 한 레포는 그래프도 develop이 아닌 그 작업 브랜치 기준이다.
이 사실을 결과에 담아 사용자가 오해하지 않게 한다.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _git(root: str | Path, *args: str, timeout: int = 180) -> tuple[int, str]:
    proc = subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return proc.returncode, (proc.stdout or "").strip() or (proc.stderr or "").strip()


def _ahead_behind(root: str | Path) -> tuple[int, int] | None:
    """(ahead, behind) — 업스트림이 없으면 None."""
    code, out = _git(root, "rev-list", "--left-right", "--count", "@{upstream}...HEAD")
    if code != 0:
        return None
    try:
        behind, ahead = (int(x) for x in out.split())
        return ahead, behind
    except ValueError:
        return None


def pull_repo(repo: str, root: str | Path, token: str = "") -> dict:
    """레포 하나를 안전하게 최신화. 반환 {repo, branch, action, reason, behind, ahead}."""
    root = Path(root)
    if not (root / ".git").exists():
        return {"repo": repo, "action": "skipped", "reason": "git 저장소가 아닙니다"}

    code, branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch if code == 0 else "?"

    # ① fetch — 워킹트리를 건드리지 않는다. 실패해도 로컬은 그대로.
    code, out = _git(root, "fetch", "--prune", "origin")
    if code != 0:
        return {"repo": repo, "branch": branch, "action": "fetch_failed",
                "reason": out[-160:]}

    # ② 안전 조건 검사 — 하나라도 어긋나면 건드리지 않는다
    _, dirty = _git(root, "status", "--porcelain")
    if dirty.strip():
        return {"repo": repo, "branch": branch, "action": "fetched_only",
                "reason": f"미커밋 변경 {len(dirty.splitlines())}개 — 작업 보호를 위해 그대로 둠"}

    ab = _ahead_behind(root)
    if ab is None:
        return {"repo": repo, "branch": branch, "action": "fetched_only",
                "reason": "현재 브랜치에 업스트림이 없어 당길 대상이 없음"}
    ahead, behind = ab
    if behind == 0:
        return {"repo": repo, "branch": branch, "action": "already_latest",
                "ahead": ahead, "behind": 0}
    if ahead > 0:
        return {"repo": repo, "branch": branch, "action": "fetched_only",
                "ahead": ahead, "behind": behind,
                "reason": f"로컬 커밋 {ahead}개와 갈라짐 — 자동 병합하지 않음(수동 확인 필요)"}

    # ③ 뒤처지기만 함 + 깨끗 → fast-forward만 수행(병합 커밋을 만들지 않는다)
    code, out = _git(root, "merge", "--ff-only", "@{upstream}")
    if code != 0:
        return {"repo": repo, "branch": branch, "action": "ff_failed",
                "behind": behind, "reason": out[-160:]}
    return {"repo": repo, "branch": branch, "action": "updated", "behind": behind,
            "ahead": 0}


def pull_all(config) -> list[dict]:
    """config.repos 전체를 안전 최신화. 같은 경로를 공유하는 레포는 한 번만 당긴다."""
    seen: dict[str, dict] = {}
    results = []
    for repo, root in (config.repos or {}).items():
        key = str(Path(root).resolve()).lower()
        if key in seen:  # frontend-app/lib/features처럼 한 클론을 여러 스코프가 공유
            shared = dict(seen[key])
            shared["repo"] = repo
            shared["shared_with"] = seen[key]["repo"]
            results.append(shared)
            continue
        try:
            r = pull_repo(repo, root)
        except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as e:
            r = {"repo": repo, "action": "error", "reason": str(e)[:160]}
        seen[key] = r
        results.append(r)
    return results


def refresh_all(config, graph=None, save: bool = True) -> dict:
    """최신화 전 과정: 원격 받아오기 → 그래프 증분 반영 → 저장.

    graph를 주면 그걸 갱신하고(웹처럼 살아있는 그래프), 없으면 파일에서 읽어 쓴다.
    """
    from .graph import Graph
    from .sync import sync_all
    from .enrich import enrich_deterministic
    from .overlay import load_overlay, apply_overlay

    pulls = pull_all(config)
    own_graph = graph is None
    g = graph if graph is not None else Graph.load(config.kg_path)

    sync = sync_all(g)
    changed = sum(r.get("changed", 0) for r in sync)
    if changed or any(r.get("action") for r in sync):
        enrich_deterministic(g)
        if save:
            g.save(config.kg_path)
            overlay = load_overlay(Path(config.kg_path).parent / "overlay.json")
            if overlay["node_overrides"] or overlay["custom_edges"]:
                apply_overlay(g, overlay)
                g.save(config.kg_path)
    elif own_graph and save:
        g.save(config.kg_path)

    updated = [p["repo"] for p in pulls if p.get("action") == "updated"]
    held = [p for p in pulls if p.get("action") in ("fetched_only", "ff_failed")]
    return {
        "pulls": pulls, "sync": sync, "changed": changed,
        "nodes": len(g.nodes), "edges": len(g.edges),
        "updated_repos": updated,
        # 그래프는 '체크아웃된 파일' 기준이다 — FF 못 한 레포는 그 사실을 알려야 오해가 없다
        "not_on_latest": [{"repo": p["repo"], "branch": p.get("branch", ""),
                           "reason": p.get("reason", "")} for p in held],
    }
