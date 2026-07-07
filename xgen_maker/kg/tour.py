"""가이드 투어 — 의존성 순서(기반→상위)로 정렬한 코드베이스 읽기 순서 (UA guided tour의 결정론 대응물).

레포 내 파일 imports DAG를 Kahn 위상정렬(순환은 연결도 순으로 절단)하고,
연결도 상위 파일만 추려 요약과 함께 TOUR 마크다운을 생성한다.
"""
from __future__ import annotations

from pathlib import Path

from .graph import Graph


def reading_order(graph: Graph, repo: str, limit: int = 30) -> list[dict]:
    files = {n["id"]: n for n in graph.nodes_by_kind("file") if n["repo"] == repo}
    imports: dict[str, set[str]] = {fid: set() for fid in files}   # file → 의존 대상
    dependents: dict[str, set[str]] = {fid: set() for fid in files}
    for edge in graph.edges:
        if edge["kind"] == "imports" and edge["src"] in files and edge["dst"] in files:
            imports[edge["src"]].add(edge["dst"])
            dependents[edge["dst"]].add(edge["src"])

    degree = {fid: len(imports[fid]) + len(dependents[fid]) for fid in files}
    in_count = {fid: len(deps) for fid, deps in imports.items()}
    ready = sorted([f for f, c in in_count.items() if c == 0],
                   key=lambda f: -len(dependents[f]))
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for dependent in sorted(dependents[current], key=lambda f: -degree[f]):
            in_count[dependent] -= 1
            if in_count[dependent] == 0:
                ready.append(dependent)
    remaining = sorted((f for f in files if f not in set(order)),
                       key=lambda f: -degree[f])  # 순환 참여분
    order.extend(remaining)

    ranked = [files[fid] for fid in order]
    important = sorted(ranked, key=lambda n: -degree[n["id"]])[:limit]
    keep = {n["id"] for n in important}
    return [{"order": i + 1, "dependents": len(dependents[n["id"]]), **n}
            for i, n in enumerate(n for n in ranked if n["id"] in keep)]


def render_tour(graph: Graph, repo: str, out_path: str | Path, limit: int = 30) -> Path:
    steps = reading_order(graph, repo, limit)
    lines = [f"# 가이드 투어 — {repo}", "",
             f"> 의존성 순서(기반 모듈 → 상위 모듈) 상위 {len(steps)}개 파일. "
             f"이 순서로 읽으면 아래층부터 이해가 쌓인다.", ""]
    for step in steps:
        summary = " ".join(step["meta"].get("summary", "").split())
        lines.append(f"## {step['order']}. `{step['path']}`")
        lines.append(f"- 의존받는 곳 {step['dependents']}곳")
        if summary:
            lines.append(f"- {summary}")
        lines.append("")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
