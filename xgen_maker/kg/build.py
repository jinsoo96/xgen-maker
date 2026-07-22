"""레포 워커 — 파일 수집 → 언어별 추출기 → 레포 KG. 병합 + 크로스레포 링크까지.

무거운 디렉토리(node_modules/.git/.next 등)는 스킵. max_files 가드로 폭주 방지.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from .graph import Graph
from .extract_python import extract_python_file
from .extract_typescript import extract_ts_file
from .extract_rust import extract_rust_file, link_rust_routes, RUST_EXTS
from .extract_gateway import extract_gateway_routes, link_gateway_routes
from .routes_nextjs import extract_routes
from .crossrepo import link_api_calls, link_feature_packages
from .workspaces import ImportResolver, scan_workspaces, scan_aliases

SKIP_DIRS = {"node_modules", ".git", ".next", "dist", "build", "__pycache__",
             ".venv", "venv", ".turbo", "coverage", ".pnpm-store", ".cache",
             "storybook-static", ".idea", ".vscode", "migrations_backup"}
PY_EXTS = {".py"}
TS_EXTS = {".ts", ".tsx", ".js", ".jsx"}
# Rust — 게이트웨이처럼 전 요청이 지나는 서비스가 여기 있다(추출기 없으면 통째로 안 보임)


def git_head(repo_root: str | Path) -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root,
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def collect_files(repo_root: Path, scope: str | None = None,
                  max_files: int = 20000) -> list[str]:
    base = repo_root / scope if scope else repo_root
    rel_files: list[str] = []
    stack = [base]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except (PermissionError, OSError):
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                    stack.append(entry)
            elif entry.suffix in PY_EXTS | TS_EXTS | RUST_EXTS:
                rel_files.append(entry.relative_to(repo_root).as_posix())
                if len(rel_files) >= max_files:
                    return rel_files
    return rel_files


def build_repo(repo: str, repo_root: str | Path, scope: str | None = None,
               max_files: int = 20000) -> Graph:
    repo_root = Path(repo_root)
    graph = Graph()
    started = time.time()
    rel_files = collect_files(repo_root, scope, max_files)
    known = set(rel_files)
    graph.add_node(f"{repo}", "repo", repo, repo, str(repo_root))

    resolver = None
    if any(Path(f).suffix in TS_EXTS for f in rel_files):
        resolver = ImportResolver(repo_root, known,
                                  scan_workspaces(repo_root), scan_aliases(repo_root))

    ts_files: list[str] = []
    for rel in rel_files:
        suffix = Path(rel).suffix
        try:
            if suffix in PY_EXTS:
                extract_python_file(graph, repo, repo_root, rel, known)
            elif suffix in TS_EXTS:
                extract_ts_file(graph, repo, repo_root, rel, known, resolver=resolver)
                ts_files.append(rel)      # Next.js 라우트(page.tsx)는 TS 파일에서만 나온다
            elif suffix in RUST_EXTS:
                extract_rust_file(graph, repo, repo_root, rel, known)
        except (OSError, RecursionError):
            continue
        file_id = f"{repo}:{rel}"
        if file_id in graph.nodes:
            graph.add_edge(repo, file_id, "contains")

    route_count = extract_routes(graph, repo, ts_files)
    link_rust_routes(graph, repo)  # 라우트→핸들러는 파일 간이라 추출이 끝난 뒤 연결
    extract_gateway_routes(graph, repo, repo_root)  # 게이트웨이면 라우팅 테이블도(아니면 0)
    if resolver is not None:
        _attach_feature_members(graph, repo, rel_files, resolver.workspaces)
    graph.meta = {"repo": repo, "root": str(repo_root), "scope": scope or "",
                  "files": len(rel_files), "routes": route_count,
                  "git_head": git_head(repo_root),
                  "build_seconds": round(time.time() - started, 2)}
    return graph


def _attach_feature_members(graph: Graph, repo: str, rel_files: list[str],
                            workspaces: dict[str, str]) -> None:
    """스캔된 파일을 소속 워크스페이스 feature 노드 아래로 묶는다 (UI/UX KG 단위)."""
    for name, ws_dir in workspaces.items():
        prefix = ws_dir + "/"
        members = [rel for rel in rel_files if rel.startswith(prefix)]
        if not members:
            continue
        feature_id = f"{repo}:feature:{name}"
        graph.add_node(feature_id, "feature", name, repo, ws_dir, package=name)
        graph.add_edge(repo, feature_id, "contains")
        for member in members:
            member_id = f"{repo}:{member}"
            if member_id in graph.nodes:
                graph.add_edge(feature_id, member_id, "contains")


def refresh_files(graph: Graph, repo: str, repo_root: str | Path,
                  rel_files: list[str]) -> int:
    """증분 갱신(⑩) — 변경 파일의 노드/엣지를 걷어내고 재추출, 크로스레포 재링크."""
    repo_root = Path(repo_root)
    targets = {f.replace("\\", "/") for f in rel_files}
    dropped = {node_id for node_id, node in graph.nodes.items()
               if node["repo"] == repo and node["path"] in targets and node["kind"] != "repo"}
    graph.nodes = {nid: n for nid, n in graph.nodes.items() if nid not in dropped}
    graph.edges = [e for e in graph.edges
                   if e["src"] not in dropped and e["dst"] not in dropped]
    graph._edge_seen = {(e["src"], e["dst"], e["kind"]) for e in graph.edges}

    known = {n["path"] for n in graph.nodes.values()
             if n["repo"] == repo and n["kind"] == "file"} | targets
    ts_files: list[str] = []
    for rel in sorted(targets):
        file_path = repo_root / rel
        if not file_path.is_file():
            continue
        try:
            if file_path.suffix in PY_EXTS:
                extract_python_file(graph, repo, repo_root, rel, known)
            elif file_path.suffix in TS_EXTS:
                extract_ts_file(graph, repo, repo_root, rel, known)
                ts_files.append(rel)      # Next.js 라우트(page.tsx)는 TS 파일에서만 나온다
            elif file_path.suffix in RUST_EXTS:
                extract_rust_file(graph, repo, repo_root, rel, known)
        except (OSError, RecursionError):
            continue
        if f"{repo}:{rel}" in graph.nodes:
            graph.add_edge(repo, f"{repo}:{rel}", "contains")
    extract_routes(graph, repo, ts_files)
    link_rust_routes(graph, repo)  # 라우트→핸들러는 파일 간이라 추출 후 연결
    link_api_calls(graph)
    return len(targets)


def merge_and_link(graphs: list[Graph]) -> tuple[Graph, int]:
    merged = Graph()
    for graph in graphs:
        merged.merge(graph)
    merged.meta = {
        "repos": [g.meta.get("repo", "?") for g in graphs],
        # 증분 sync의 기준점 — 소스 스펙(repo/root/scope)과 빌드 시점 HEAD 보존
        "sources": [{"repo": g.meta.get("repo", "?"), "root": g.meta.get("root", ""),
                     "scope": g.meta.get("scope", "")} for g in graphs],
        "repo_heads": {g.meta.get("repo", "?"): g.meta.get("git_head")
                       for g in graphs if g.meta.get("git_head")},
    }
    links = link_api_calls(merged)
    feature_links = link_feature_packages(merged)
    merged.meta["crossrepo_links"] = links
    merged.meta["feature_links"] = feature_links
    # 게이트웨이 경유 경로는 모든 레포가 모인 뒤에야 이을 수 있다(호출·라우팅표·백엔드가 각각 다른 레포)
    merged.meta["gateway_links"] = link_gateway_routes(merged)
    return merged, links
