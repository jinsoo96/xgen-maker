"""증분 동기화 — UA `--auto-update`/incremental의 결정론 대응.

원리: 빌드 시 레포별 git HEAD를 그래프 meta에 기록 → sync 시
(기록 HEAD..현재 HEAD diff) + (워킹트리 미커밋 변경)의 파일만 재추출.
삭제 파일은 노드 자동 제거(refresh_files가 미존재 파일을 걷어냄).
트리거: ① MAKER 루프 사후(자동) ② `kg sync` 수동/스크립트 ③ git post-commit/post-merge 훅.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .graph import Graph
from .build import PY_EXTS, TS_EXTS, git_head, refresh_files


def _git_lines(repo_root: str | Path, *args: str) -> list[str]:
    result = subprocess.run(["git", *args], cwd=repo_root, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=60)
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def changed_files(repo_root: str | Path, old_sha: str | None) -> set[str] | None:
    """old_sha 이후 커밋 변경 + 워킹트리 변경. old_sha가 없거나 유효하지 않으면 None(풀리빌드 신호)."""
    if not old_sha:
        return None
    committed = _git_lines(repo_root, "diff", "--name-only", old_sha, "HEAD")
    if not committed and git_head(repo_root) != old_sha:
        # diff 실패(rebase로 sha 소실 등) — 풀리빌드로 폴백
        probe = subprocess.run(["git", "cat-file", "-e", old_sha], cwd=repo_root,
                               capture_output=True, timeout=30)
        if probe.returncode != 0:
            return None
    changed = set(committed)
    for line in _git_lines(repo_root, "status", "--porcelain"):
        path = line[3:].strip().strip('"')
        if " -> " in path:  # rename: 새 경로 채택
            path = path.split(" -> ", 1)[1].strip().strip('"')
        changed.add(path)
    return {p.replace("\\", "/") for p in changed}


def _relevant(files: set[str], scope: str | None) -> list[str]:
    out = []
    for rel in files:
        if Path(rel).suffix not in PY_EXTS | TS_EXTS:
            continue
        if scope and not rel.startswith(scope.rstrip("/") + "/"):
            continue
        out.append(rel)
    return sorted(out)


def sync_source(graph: Graph, source: dict) -> dict:
    """소스(빌드 당시 repo/root/scope 기록) 하나를 증분 동기화."""
    repo, root = source["repo"], source["root"]
    scope = source.get("scope") or None
    old_sha = graph.meta.get("repo_heads", {}).get(repo)
    new_sha = git_head(root)
    changed = changed_files(root, old_sha)
    if changed is None:
        return {"repo": repo, "action": "full_rebuild_needed",
                "reason": "기준 HEAD 없음/소실 — kg build로 재빌드 필요"}
    relevant = _relevant(changed, scope)
    if relevant:
        refresh_files(graph, repo, root, relevant)
    if new_sha:
        graph.meta.setdefault("repo_heads", {})[repo] = new_sha
    return {"repo": repo, "scope": scope or "-", "changed": len(relevant),
            "files": relevant[:20], "head": (new_sha or "")[:12]}


def repair_dangling(graph: Graph, sources: list[dict]) -> dict:
    """끊긴 엣지 자가복구 — 가리키는 파일이 실재하면 재추출, 아니면 엣지를 버린다.

    증분 sync는 '변경된 파일'만 다시 읽는다. 그래서 한 번 노드가 빠진 파일은
    그 파일이 다시 바뀌기 전까지 영영 복구되지 않고 끊긴 엣지로 남는다
    (예: BOM 때문에 파싱이 실패해 통째로 누락됐던 파일).
    """
    roots = {s["repo"]: s["root"] for s in sources if s.get("repo") and s.get("root")}
    ids = set(graph.nodes)
    missing = {e["dst"] for e in graph.edges if e["dst"] not in ids}
    missing |= {e["src"] for e in graph.edges if e["src"] not in ids}
    if not missing:
        return {"repaired": 0, "dropped": 0}
    by_repo: dict = {}
    for node_id in missing:
        repo, _, rel = node_id.partition(":")
        if rel and "#" not in rel and repo in roots:
            by_repo.setdefault(repo, []).append(rel)
    repaired = 0
    for repo, rels in by_repo.items():
        root = Path(roots[repo])
        real = [r for r in rels if (root / r).is_file()]
        if real:
            refresh_files(graph, repo, root, real)
            repaired += sum(1 for r in real if f"{repo}:{r}" in graph.nodes)
    ids = set(graph.nodes)
    before = len(graph.edges)
    graph.edges = [e for e in graph.edges if e["src"] in ids and e["dst"] in ids]
    graph._edge_seen = {(e["src"], e["dst"], e["kind"]) for e in graph.edges}
    return {"repaired": repaired, "dropped": before - len(graph.edges)}


def sync_all(graph: Graph) -> list[dict]:
    sources = graph.meta.get("sources", [])
    if not sources:
        return [{"action": "full_rebuild_needed",
                 "reason": "meta.sources 없음 — 구버전 그래프, kg build+merge 재실행 필요"}]
    results = [sync_source(graph, source) for source in sources]
    fix = repair_dangling(graph, sources)
    if fix["repaired"] or fix["dropped"]:
        results.append({"repo": "(무결성 복구)", "changed": fix["repaired"],
                        "action": f"끊긴 엣지 정리 — 파일 재추출 {fix['repaired']}개, "
                                  f"엣지 제거 {fix['dropped']}개"})
    return results


# ---- git 훅 (UA --auto-update 대응, opt-in) ----

_HOOK_MARK = "# xgen-maker-kg-sync"
_HOOK_BODY = """#!/bin/sh
{mark}
cd "{maker_dir}" && "{python}" -m xgen_maker kg sync --kg "{kg_path}" --quiet &
"""


def install_hooks(repo_path: str | Path, maker_dir: str | Path, kg_path: str | Path,
                  python_exe: str = "python") -> list[str]:
    hooks_dir = Path(repo_path) / ".git" / "hooks"
    if not hooks_dir.parent.exists():
        raise FileNotFoundError(f"git 저장소 아님: {repo_path}")
    hooks_dir.mkdir(exist_ok=True)
    body = _HOOK_BODY.format(mark=_HOOK_MARK,
                             maker_dir=Path(maker_dir).as_posix(),
                             python=python_exe,
                             kg_path=Path(kg_path).as_posix())
    written = []
    for name in ("post-commit", "post-merge", "post-checkout"):
        hook = hooks_dir / name
        if hook.exists() and _HOOK_MARK not in hook.read_text(encoding="utf-8", errors="ignore"):
            written.append(f"{name}: 기존 훅 존재 — 건너뜀(수동 병합 필요)")
            continue
        hook.write_text(body, encoding="utf-8", newline="\n")
        written.append(f"{name}: 설치")
    return written


def remove_hooks(repo_path: str | Path) -> list[str]:
    hooks_dir = Path(repo_path) / ".git" / "hooks"
    removed = []
    for name in ("post-commit", "post-merge", "post-checkout"):
        hook = hooks_dir / name
        if hook.exists() and _HOOK_MARK in hook.read_text(encoding="utf-8", errors="ignore"):
            hook.unlink()
            removed.append(f"{name}: 제거")
    return removed
