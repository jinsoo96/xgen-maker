"""크로스레포 연결 레이어 — MAKER KG의 차별화 지점.

frontend의 api_call 노드(정규화 URL)와 backend의 endpoint 노드(method+path)를 매칭해
`resolves_to` 엣지를 생성. 게이트웨이 prefix 차이는 suffix 정렬로 흡수.

매칭 규칙(오탐 방어):
- 정렬 구간에서 리터럴 세그먼트 완전일치 ≥ 1개 필수
- 와일드카드↔리터럴 교차 매칭 금지 (param↔param 만 허용)
- 후보 중 (리터럴 일치 수, 엔드포인트 경로 길이) 최대를 채택
"""
from __future__ import annotations

import re
from .graph import Graph


def _norm_endpoint_path(path: str) -> str:
    segments = [seg for seg in path.split("?")[0].split("/") if seg]
    out = []
    for seg in segments:
        if re.fullmatch(r"\{[^}]*\}|:[\w]+|\[[^\]]*\]", seg):
            out.append("*")
        else:
            out.append(seg)
    return "/" + "/".join(out)


def _match(call_segs: list[str], ep_segs: list[str]) -> tuple[int, int] | None:
    """FE 호출 suffix를 BE 경로에 정렬. 반환 (리터럴 일치 수, ep 길이) 또는 None."""
    if not ep_segs or len(call_segs) < len(ep_segs):
        return None
    tail = call_segs[len(call_segs) - len(ep_segs):]
    literal_hits = 0
    for call_seg, ep_seg in zip(tail, ep_segs):
        if call_seg == "*" and ep_seg == "*":
            continue  # param ↔ param
        if call_seg == "*" or ep_seg == "*":
            return None  # 와일드카드 ↔ 리터럴 교차 금지
        if call_seg != ep_seg:
            return None
        literal_hits += 1
    if literal_hits == 0:
        return None
    return literal_hits, len(ep_segs)


def link_feature_packages(graph: Graph) -> int:
    """스코프 간 feature 패키지 연결 — app scope의 feature:@x/N ↔ features scope의 실제 파일 보유 노드.

    같은 패키지명 feature 노드가 여러 스코프에 있으면 same_package 엣지로 잇는다(양방향).
    이게 있어야 라우트 페이지(app)→feature→실제 컴포넌트(features) 추적이 스코프를 넘는다.
    """
    by_name: dict[str, list[dict]] = {}
    for node in graph.nodes_by_kind("feature"):
        by_name.setdefault(node["name"], []).append(node)
    created = 0
    for name, nodes in by_name.items():
        if len(nodes) < 2:
            continue
        # 파일을 가장 많이 contains하는 노드를 정본으로
        def contained(n):
            return sum(1 for e in graph.edges
                       if e["kind"] == "contains" and e["src"] == n["id"])
        canonical = max(nodes, key=contained)
        for node in nodes:
            if node["id"] == canonical["id"]:
                continue
            graph.add_edge(node["id"], canonical["id"], "same_package")
            graph.add_edge(canonical["id"], node["id"], "same_package")
            created += 1
    return created


def link_api_calls(graph: Graph) -> int:
    """api_call ↔ endpoint 매칭. 반환 = 생성된 resolves_to 엣지 수."""
    endpoints = []
    for node in graph.nodes_by_kind("endpoint"):
        norm = _norm_endpoint_path(node["meta"].get("route_path", ""))
        segments = [seg for seg in norm.split("/") if seg]
        endpoints.append((node, norm, segments))
    created = 0
    for call in graph.nodes_by_kind("api_call"):
        call_segs = [seg for seg in call["meta"].get("norm_path", "").split("/") if seg]
        call_method = call["meta"].get("method", "GET")
        best = None
        best_score: tuple[int, int] = (0, 0)
        for endpoint, ep_norm, ep_segs in endpoints:
            if endpoint["meta"].get("method") != call_method:
                continue
            score = _match(call_segs, ep_segs)
            if score and score > best_score:
                best, best_score = (endpoint, ep_norm), score
        if best:
            graph.add_edge(call["id"], best[0]["id"], "resolves_to",
                           matched_path=best[1], literal_hits=best_score[0])
            created += 1
    return created
