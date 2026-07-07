"""지식그래프 코어 모델 — 노드/엣지, 저장/로드, 병합.

노드 id 규약: "<repo>:<relpath>" (파일) · "<repo>:<relpath>#<symbol>" (심볼)
             · "<repo>:route:<path>" (라우트) · "<repo>:<relpath>#EP <METHOD> <path>" (엔드포인트)
"""
from __future__ import annotations

import json
from pathlib import Path

NODE_KINDS = ("repo", "file", "class", "function", "endpoint", "api_call", "route", "feature")
EDGE_KINDS = ("contains", "imports", "calls", "route_of", "resolves_to")


class Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []
        self._edge_seen: set[tuple] = set()
        self.meta: dict = {}

    # ---- 구성 ----
    def add_node(self, node_id: str, kind: str, name: str, repo: str,
                 path: str = "", line: int = 0, **meta) -> dict:
        existing = self.nodes.get(node_id)
        if existing is not None:
            if meta:
                existing["meta"].update(meta)
            return existing
        node = {"id": node_id, "kind": kind, "name": name, "repo": repo,
                "path": path, "line": line, "meta": meta}
        self.nodes[node_id] = node
        return node

    def add_edge(self, src: str, dst: str, kind: str, **meta) -> None:
        key = (src, dst, kind)
        if key in self._edge_seen:
            return
        self._edge_seen.add(key)
        self.edges.append({"src": src, "dst": dst, "kind": kind, "meta": meta})

    def merge(self, other: "Graph") -> None:
        for node in other.nodes.values():
            self.add_node(node["id"], node["kind"], node["name"], node["repo"],
                          node["path"], node["line"], **node["meta"])
        for edge in other.edges:
            self.add_edge(edge["src"], edge["dst"], edge["kind"], **edge["meta"])

    # ---- 조회 ----
    def nodes_by_kind(self, kind: str) -> list[dict]:
        return [n for n in self.nodes.values() if n["kind"] == kind]

    def neighbors(self, node_id: str) -> list[tuple[str, dict]]:
        """(방향, 엣지) 목록. 방향은 'out'(내가 src) / 'in'(내가 dst)."""
        out: list[tuple[str, dict]] = []
        for edge in self.edges:
            if edge["src"] == node_id:
                out.append(("out", edge))
            elif edge["dst"] == node_id:
                out.append(("in", edge))
        return out

    def stats(self) -> dict:
        by_node: dict[str, int] = {}
        for node in self.nodes.values():
            by_node[node["kind"]] = by_node.get(node["kind"], 0) + 1
        by_edge: dict[str, int] = {}
        for edge in self.edges:
            by_edge[edge["kind"]] = by_edge.get(edge["kind"], 0) + 1
        return {"nodes": len(self.nodes), "edges": len(self.edges),
                "nodes_by_kind": by_node, "edges_by_kind": by_edge}

    # ---- 영속화 ----
    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"meta": self.meta, "stats": self.stats(),
                   "nodes": list(self.nodes.values()), "edges": self.edges}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Graph":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        graph = cls()
        graph.meta = data.get("meta", {})
        for node in data["nodes"]:
            graph.add_node(node["id"], node["kind"], node["name"], node["repo"],
                           node.get("path", ""), node.get("line", 0), **node.get("meta", {}))
        for edge in data["edges"]:
            graph.add_edge(edge["src"], edge["dst"], edge["kind"], **edge.get("meta", {}))
        return graph
