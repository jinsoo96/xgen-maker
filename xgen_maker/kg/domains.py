"""도메인/플로우 뷰 — UI/UX KG의 의미층 (UA domain view의 결정론 대응물).

도메인 = 라우트 1차 세그먼트 그룹. 플로우 = 라우트 → 페이지 파일 → (imports BFS)
→ feature/파일 → api_call → resolves_to → 백엔드 엔드포인트 체인.
산출: domain 노드/엣지 주입 + 사람용 domain-map.html.
"""
from __future__ import annotations

import html
from pathlib import Path

from .graph import Graph


def build_domains(graph: Graph) -> int:
    """route 노드들을 도메인 노드로 그룹핑. 반환 = 생성된 domain 노드 수."""
    created = 0
    for route in graph.nodes_by_kind("route"):
        segments = [s for s in route["name"].split("/") if s]
        domain_name = segments[0] if segments else "root"
        if domain_name.startswith("[") or domain_name.startswith("("):
            domain_name = "dynamic"
        domain_id = f"{route['repo']}:domain:{domain_name}"
        if domain_id not in graph.nodes:
            created += 1
        graph.add_node(domain_id, "domain", domain_name, route["repo"], ui=True)
        graph.add_edge(domain_id, route["id"], "contains")
    return created


def trace_flow(graph: Graph, route_id: str, depth: int = 3) -> dict:
    """라우트 하나의 프론트→백엔드 플로우 추적."""
    flow = {"route": route_id, "files": [], "features": [], "api_calls": [], "endpoints": []}
    out_index: dict[str, list[dict]] = {}
    for edge in graph.edges:
        out_index.setdefault(edge["src"], []).append(edge)

    start_files = [e["dst"] for e in out_index.get(route_id, []) if e["kind"] == "route_of"]
    visited = set(start_files)
    frontier = list(start_files)
    for _ in range(depth):
        next_frontier: list[str] = []
        for current in frontier:
            node = graph.nodes.get(current)
            if node is None:
                continue
            bucket = {"file": "files", "feature": "features"}.get(node["kind"])
            if bucket and current not in flow[bucket]:
                flow[bucket].append(current)
            for edge in out_index.get(current, ()):
                dst_node = graph.nodes.get(edge["dst"])
                if dst_node is None or edge["dst"] in visited:
                    continue
                if edge["kind"] == "imports" or \
                        (edge["kind"] == "contains" and dst_node["kind"] in ("file", "api_call")):
                    visited.add(edge["dst"])
                    if dst_node["kind"] == "api_call":
                        flow["api_calls"].append(edge["dst"])
                        for resolve_edge in out_index.get(edge["dst"], ()):
                            if resolve_edge["kind"] == "resolves_to":
                                if resolve_edge["dst"] not in flow["endpoints"]:
                                    flow["endpoints"].append(resolve_edge["dst"])
                    else:
                        next_frontier.append(edge["dst"])
        frontier = next_frontier
    return flow


_CSS = """body{font:14px/1.6 'Segoe UI',sans-serif;background:#111827;color:#e5e7eb;margin:0;padding:24px}
h1{font-size:20px} h2{font-size:16px;color:#f472b6;border-bottom:1px solid #374151;padding-bottom:4px;margin-top:28px}
.route{margin:10px 0 18px 12px} .route>b{color:#34d399;font-size:14px}
.summary{color:#9ca3af;margin:2px 0 6px 0}
.chain{margin-left:16px} .chain div{margin:2px 0}
.tag{display:inline-block;padding:0 6px;border-radius:4px;font-size:11px;margin-right:6px}
.t-feature{background:#065f46} .t-call{background:#78350f} .t-ep{background:#1e3a8a} .t-file{background:#374151}
code{color:#fbbf24;font-size:12px}"""


def render_domain_map(graph: Graph, out_path: str | Path) -> Path:
    domains = sorted(graph.nodes_by_kind("domain"), key=lambda n: n["name"])
    parts = ["<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'>",
             "<title>XGEN MAKER — Domain Map</title><style>", _CSS, "</style></head><body>",
             "<h1>XGEN MAKER · 도메인/플로우 뷰 (UI/UX KG)</h1>",
             f"<p class='summary'>도메인 {len(domains)}개 · 라우트 "
             f"{len(graph.nodes_by_kind('route'))}개 — 라우트→feature→API호출→백엔드 엔드포인트 체인</p>"]

    def esc(text: str) -> str:
        return html.escape(str(text))

    for domain in domains:
        routes = [graph.nodes[e["dst"]] for _, e in graph.neighbors(domain["id"])
                  if e["kind"] == "contains" and e["src"] == domain["id"]
                  and e["dst"] in graph.nodes]
        parts.append(f"<h2>/{esc(domain['name'])} <small>({len(routes)} routes)</small></h2>")
        for route in sorted(routes, key=lambda r: r["name"]):
            flow = trace_flow(graph, route["id"])
            summary = route["meta"].get("summary", "")
            parts.append(f"<div class='route'><b>{esc(route['name'])}</b>"
                         f"<div class='summary'>{esc(summary)}</div><div class='chain'>")
            for feature_id in flow["features"][:8]:
                feature = graph.nodes[feature_id]
                parts.append(f"<div><span class='tag t-feature'>feature</span>"
                             f"{esc(feature['name'])}</div>")
            for call_id in flow["api_calls"][:10]:
                call = graph.nodes[call_id]
                parts.append(f"<div><span class='tag t-call'>call</span>"
                             f"<code>{esc(call['name'])}</code></div>")
            for endpoint_id in flow["endpoints"][:10]:
                endpoint = graph.nodes[endpoint_id]
                parts.append(f"<div><span class='tag t-ep'>endpoint</span>"
                             f"<code>{esc(endpoint['name'])}</code> "
                             f"<span class='summary'>[{esc(endpoint['repo'])}] "
                             f"{esc(endpoint['meta'].get('summary', ''))}</span></div>")
            if not (flow["features"] or flow["api_calls"] or flow["endpoints"]):
                parts.append("<div class='summary'>(정적 화면 — 추적된 백엔드 의존 없음)</div>")
            parts.append("</div></div>")
    parts.append("</body></html>")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path
