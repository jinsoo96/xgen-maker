"""Next.js App Router 라우트 맵 추출 — UI/UX KG의 골격.

`**/app/**/page.{tsx,jsx,ts,js}` → 라우트 노드. `(group)` 세그먼트 제거, `[param]` 유지.
"""
from __future__ import annotations

import re
from .graph import Graph

_PAGE_RE = re.compile(r"(?:^|/)app/(.*?)?page\.(tsx|jsx|ts|js)$")


def route_from_rel(rel: str) -> str | None:
    match = _PAGE_RE.search(rel.replace("\\", "/"))
    if not match:
        return None
    middle = (match.group(1) or "").strip("/")
    segments = [seg for seg in middle.split("/")
                if seg and not (seg.startswith("(") and seg.endswith(")")) and seg != "."]
    return "/" + "/".join(segments)


def extract_routes(graph: Graph, repo: str, rel_files: list[str]) -> int:
    count = 0
    for rel in rel_files:
        route = route_from_rel(rel)
        if route is None:
            continue
        route_id = f"{repo}:route:{route}"
        graph.add_node(route_id, "route", route, repo, rel, ui=True)
        graph.add_edge(route_id, f"{repo}:{rel}", "route_of")
        count += 1
    return count
