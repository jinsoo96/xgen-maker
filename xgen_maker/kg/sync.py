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
from .build import PY_EXTS, TS_EXTS, RUST_EXTS, git_head, refresh_files
from .extract_config import CONFIG_EXTS


def _git_lines(repo_root: str | Path, *args: str) -> list[str]:
    result = subprocess.run(["git", *args], cwd=repo_root, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=60)
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def _git_zfields(repo_root: str | Path, *args: str) -> list[str]:
    """NUL 구분으로 받는다 — 경로에 따옴표·이스케이프가 섞이지 않게.

    git은 기본적으로 ASCII 밖 경로를 C 스타일로 감싼다("\\354\\240\\225...").
    그걸 그대로 경로로 쓰면 존재하지 않는 파일을 가리키고, 증분 갱신은 조용히
    건너뛴다 — 그 파일의 노드는 영영 낡은 채로 남는다(실측: 한글 파일명이
    '/354/240/225/...'로 뭉개졌다). -z를 주면 git이 감싸지 않는다.
    """
    result = subprocess.run(["git", *args], cwd=repo_root, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=60)
    if result.returncode != 0:
        return []
    return [field for field in result.stdout.split("\0") if field]


def changed_files(repo_root: str | Path, old_sha: str | None,
                  ref: str = "") -> set[str] | None:
    """old_sha 이후 변경 파일. 없거나 유효하지 않으면 None(풀리빌드 신호).

    ref를 주면 그 커밋까지의 차이만 본다 — 워킹트리 변경은 그 커밋에 없는 코드이므로
    섞지 않는다. 그래프를 만든 기준과 갱신 기준이 어긋나면 한 그래프에 두 시점이 섞인다.
    ref가 없을 때만(워킹트리 기반 그래프) 미커밋 변경까지 반영한다.
    """
    if not old_sha:
        return None
    target = ref or "HEAD"
    committed = _git_zfields(repo_root, "diff", "--name-only", "-z", old_sha, target)
    if not committed and git_head(repo_root, target) != old_sha:
        # diff 실패(rebase로 sha 소실 등) — 풀리빌드로 폴백
        probe = subprocess.run(["git", "cat-file", "-e", old_sha], cwd=repo_root,
                               capture_output=True, timeout=30)
        if probe.returncode != 0:
            return None
    changed = set(committed)
    if not ref:
        # -z 형식: 항목마다 "XY <경로>\0", 이름 변경만 뒤에 옛 경로가 한 칸 더 붙는다.
        # 옛 경로는 이미 사라진 파일이라 갱신 대상이 아니다 — 건너뛴다.
        fields = _git_zfields(repo_root, "status", "--porcelain", "-z")
        index = 0
        while index < len(fields):
            entry = fields[index]
            index += 1
            status, path = entry[:2], entry[3:]
            if path:
                changed.add(path)
            if status and status[0] in ("R", "C"):
                index += 1              # 짝지어 오는 옛 경로를 소비
    return {p.replace("\\", "/") for p in changed}


def _relevant(files: set[str], scope: str | None) -> list[str]:
    # 빌드가 수집하는 확장자와 같아야 한다. Rust가 빠져 있어 게이트웨이(.rs) 변경이
    # 증분 반영되지 않았다 — 빌드엔 있고 sync엔 없는 언어는 조용히 낡는다.
    out = []
    for rel in files:
        if Path(rel).suffix.lower() not in PY_EXTS | TS_EXTS | RUST_EXTS | CONFIG_EXTS:
            continue
        if scope and not rel.startswith(scope.rstrip("/") + "/"):
            continue
        out.append(rel)
    return sorted(out)


def sync_source(graph: Graph, source: dict) -> dict:
    """소스(빌드 당시 repo/root/scope 기록) 하나를 증분 동기화."""
    repo, root = source["repo"], source["root"]
    scope = source.get("scope") or None
    # 그래프를 만든 기준으로 갱신한다(빌드가 origin/develop을 봤으면 sync도 그것을).
    ref = source.get("ref") or ""
    old_sha = graph.meta.get("repo_heads", {}).get(repo)
    new_sha = git_head(root, ref) if ref else git_head(root)
    changed = changed_files(root, old_sha, ref)
    if changed is None:
        return {"repo": repo, "action": "full_rebuild_needed",
                "reason": "기준 HEAD 없음/소실 — kg build로 재빌드 필요"}
    relevant = _relevant(changed, scope)
    if relevant:
        refresh_files(graph, repo, root, relevant, ref=ref or None)
    if new_sha:
        graph.meta.setdefault("repo_heads", {})[repo] = new_sha
    return {"repo": repo, "scope": scope or "-", "changed": len(relevant),
            "files": relevant[:20], "head": (new_sha or "")[:12],
            "basis": ref or "워킹트리"}


def repair_dangling(graph: Graph, sources: list[dict]) -> dict:
    """끊긴 엣지 자가복구 — 가리키는 파일이 실재하면 재추출, 아니면 엣지를 버린다.

    증분 sync는 '변경된 파일'만 다시 읽는다. 그래서 한 번 노드가 빠진 파일은
    그 파일이 다시 바뀌기 전까지 영영 복구되지 않고 끊긴 엣지로 남는다
    (예: BOM 때문에 파싱이 실패해 통째로 누락됐던 파일).
    """
    roots = {s["repo"]: s["root"] for s in sources if s.get("repo") and s.get("root")}
    refs = {s["repo"]: (s.get("ref") or "") for s in sources if s.get("repo")}
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
        # 복구도 그래프를 만든 기준에서 읽는다(refresh_files가 ref 기준 존재 여부를 판정).
        ref = refs.get(repo) or ""
        real = rels if ref else [r for r in rels if (root / r).is_file()]
        if real:
            refresh_files(graph, repo, root, real, ref=ref or None)
            repaired += sum(1 for r in real if f"{repo}:{r}" in graph.nodes)
    ids = set(graph.nodes)
    before = len(graph.edges)
    graph.edges = [e for e in graph.edges if e["src"] in ids and e["dst"] in ids]
    graph._edge_seen = {(e["src"], e["dst"], e["kind"]) for e in graph.edges}
    graph.touch()
    return {"repaired": repaired, "dropped": before - len(graph.edges)}


def _resync_gateway(graph: Graph, sources: list[dict]) -> dict | None:
    """게이트웨이 라우팅 테이블을 다시 읽는다.

    증분 갱신은 코드 파일만 본다. 그런데 "이 API가 어느 백엔드로 가나"의 답은 설정
    파일에 있고, 그게 바뀌는 순간이 바로 새 모듈이 붙는 순간이다. 설정은 작아서
    매번 다시 읽어도 싸다 — 바뀌었는지 따지느니 그냥 갈아끼운다.
    """
    from pathlib import Path
    from .extract_gateway import extract_gateway_routes, link_gateway_routes, find_services_file

    owners = [s for s in sources
              if s.get("root") and find_services_file(s["root"]) is not None]
    if not owners:
        return None
    stale = {n["id"] for n in graph.nodes_by_kind("gateway_route")}
    for node_id in stale:
        graph.nodes.pop(node_id, None)
    graph.edges = [e for e in graph.edges
                   if e["src"] not in stale and e["dst"] not in stale]
    graph._edge_seen = {(e["src"], e["dst"], e["kind"]) for e in graph.edges}
    graph.touch()
    for source in owners:
        extract_gateway_routes(graph, source["repo"], Path(source["root"]))
    links = link_gateway_routes(graph)
    return {"repo": "(API 관문)", "changed": 0,
            "action": f"라우팅 테이블 {len(graph.nodes_by_kind('gateway_route'))}개 · "
                      f"백엔드 연결 {links['serves']} · 호출 연결 {links['calls']}"}


def sync_all(graph: Graph) -> list[dict]:
    sources = graph.meta.get("sources", [])
    if not sources:
        return [{"action": "full_rebuild_needed",
                 "reason": "meta.sources 없음 — 구버전 그래프, kg build+merge 재실행 필요"}]
    results = [sync_source(graph, source) for source in sources]
    gw = _resync_gateway(graph, sources)
    if gw:
        results.append(gw)
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
