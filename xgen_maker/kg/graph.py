"""지식그래프 코어 모델 — 노드/엣지, 저장/로드, 병합.

노드 id 규약: "<repo>:<relpath>" (파일) · "<repo>:<relpath>#<symbol>" (심볼)
             · "<repo>:route:<path>" (라우트) · "<repo>:<relpath>#EP <METHOD> <path>" (엔드포인트)
"""
from __future__ import annotations

import json
from pathlib import Path

# 노드·엣지 종류 목록을 상수로 두지 않는다. 아무도 안 읽는 채로 낡아 실제와 어긋났다
# (선언 8·5종 vs 실제 12·11종 — gateway_route·helm_app·same_package·routes_via 등이 빠져
# 있었다). 종류는 추출기가 늘리는 것이라, 목록을 손으로 적으면 반드시 뒤처진다.
# 지금 무엇이 있는지 알고 싶으면 그래프에 물어라: Graph.stats()["nodes_by_kind"].


class Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []
        self._edge_seen: set[tuple] = set()
        self.meta: dict = {}
        # 내용이 바뀔 때마다 오르는 번호. 파생 캐시(검색 색인·중심성·인접리스트)는
        # 이것으로 무효화한다. 노드 '개수'로 판단하면 이름만 바뀐 경우(개명, 심볼
        # 하나 추가+하나 삭제)를 못 잡아, 갱신했는데도 옛 색인이 계속 쓰인다
        # — 재착지가 조용히 빈 결과를 내던 실제 원인.
        self.rev: int = 0

    def touch(self) -> None:
        """nodes/edges를 메서드 밖에서 직접 갈아끼웠을 때 파생 캐시를 무효화한다."""
        self.rev += 1

    # ---- 구성 ----
    def add_node(self, node_id: str, kind: str, name: str, repo: str,
                 path: str = "", line: int = 0, **meta) -> dict:
        existing = self.nodes.get(node_id)
        if existing is not None:
            if meta:
                existing["meta"].update(meta)
                self.rev += 1
            return existing
        node = {"id": node_id, "kind": kind, "name": name, "repo": repo,
                "path": path, "line": line, "meta": meta}
        self.nodes[node_id] = node
        self.rev += 1
        return node

    def add_edge(self, src: str, dst: str, kind: str, **meta) -> None:
        key = (src, dst, kind)
        if key in self._edge_seen:
            return
        self._edge_seen.add(key)
        self.edges.append({"src": src, "dst": dst, "kind": kind, "meta": meta})
        self.rev += 1

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
        """원자적 저장 — 임시파일에 쓰고 os.replace로 교체.

        동시 save(웹 Sync + 루프 사후 갱신)가 겹쳐도 반쯤 쓰인 KG 파일이 남지 않는다.
        """
        import os
        import tempfile
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"meta": self.meta, "stats": self.stats(),
                   "nodes": list(self.nodes.values()), "edges": self.edges}
        import time
        handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".kgtmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            # 원자적 교체. Windows에선 대상이 동시에 열려 있으면 PermissionError → 짧게 재시도.
            for attempt in range(10):
                try:
                    os.replace(tmp_name, path)
                    break
                except PermissionError:
                    if attempt == 9:
                        raise
                    time.sleep(0.02)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

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
