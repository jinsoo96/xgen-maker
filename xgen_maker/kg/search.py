"""KG 검색·영향분석 — 루프의 착지점 특정(③)과 MR 영향분석(⑨)의 재료.

search: 토큰/부분일치/퍼지 점수 상위 k.
impact: 역방향 BFS — "이 노드가 바뀌면 누가 영향받나" (imports·calls·resolves_to·route_of·contains 역추적).
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from .graph import Graph

_SPLIT = re.compile(r"[^a-z0-9가-힣]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def tokenize(text: str) -> set[str]:
    """식별자를 사람이 읽는 단어로 쪼갠다.

    `listApiCollections`·`main-tool-management-api-collections`·`user_id`는 통째로 두면
    어떤 토큰과도 안 맞는다. 코드 이름은 원래 여러 단어를 붙여 만든 것이라, 그 규칙을
    되돌려 놓아야 검색이 닿는다. (단어 사전이 아니라 문자열 규칙이다)
    """
    return {t for t in _SPLIT.split(_CAMEL.sub(" ", text).lower()) if len(t) >= 2}


def _haystack(node: dict, cache: dict | None = None) -> tuple[set[str], str]:
    """이 노드를 가리킬 수 있는 모든 말.

    이름·경로뿐 아니라 저장소명과 **의미층(요약·문서)**까지 넣는다. enrich가 만들어 둔
    요약에는 "프론트 feature 패키지…", "핸들러 …" 처럼 사람 말이 들어 있는데, 검색이
    그걸 안 보면 그래프가 아는 것을 검색이 모르는 상태가 된다.

    캐시는 노드 밖에 둔다 — 노드 dict에 넣으면 Graph.save가 그대로 직렬화하려다 깨진다.
    """
    if cache is not None:
        cached = cache.get(node["id"])
        if cached is not None:
            return cached
    meta = node.get("meta") or {}
    words = tokenize(node["name"]) | tokenize(node["path"]) | tokenize(node["repo"])
    words |= tokenize(node["kind"])
    for key in ("summary", "doc", "package", "route_path", "module", "service"):
        value = meta.get(key)
        if isinstance(value, str) and value:
            words |= tokenize(value)
    # 단어 집합과 함께 이어붙인 문자열도 둔다. 한국어는 어간에 조사가 붙어("취소"→"취소를")
    # 토큰이 정확히 같지 않고, 영어도 복수형("tool"→"tools")이 그렇다. 조사·어미 목록을
    # 적는 대신 부분일치로 받는다.
    result = (words, " ".join(sorted(words)))
    if cache is not None:
        cache[node["id"]] = result
    return result


def _score(node: dict, query: str, tokens: list[str], cache: dict | None = None) -> float:
    name = node["name"].lower()
    path = node["path"].lower()
    query_lower = query.lower()
    words, blob = _haystack(node, cache)
    score = 0.0
    if name == query_lower:
        score += 100
    elif query_lower in name:
        score += 60
    for token in tokens:
        if token in name:
            score += 25
        elif token in words:          # 이름에 없어도 경로·저장소·요약이 가리키면 인정
            score += 18
        elif len(token) >= 2 and token in blob:   # 조사·복수형이 붙은 형태("취소를", "tools")
            score += 12
        if token in path:
            score += 15
    score += SequenceMatcher(None, name, query_lower).ratio() * 30
    if node["kind"] in ("endpoint", "route"):
        score += 5
    if node["meta"].get("deprecated"):
        score -= 60  # 사람이 deprecated 표시한 노드 — 착지 회피(R8 오버레이)
    return score


def search(graph: Graph, query: str, k: int = 10,
           kinds: tuple[str, ...] | None = None) -> list[dict]:
    tokens = sorted(tokenize(query))
    cache = graph.__dict__.setdefault("_tok_cache", {})   # 그래프 밖 캐시(직렬화 대상 아님)
    scored = []
    for node in graph.nodes.values():
        if kinds and node["kind"] not in kinds:
            continue
        score = _score(node, query, tokens, cache)
        if score > 20:
            scored.append((score, node))
    scored.sort(key=lambda pair: -pair[0])
    return [{"score": round(score, 1), **node} for score, node in scored[:k]]


def _dependents_index(graph: Graph) -> dict[str, set[str]]:
    """dst가 바뀌면 src가 영향받는 방향으로 역인덱스 구성."""
    index: dict[str, set[str]] = {}
    for edge in graph.edges:
        src, dst, kind = edge["src"], edge["dst"], edge["kind"]
        if kind in ("imports", "calls", "resolves_to", "route_of", "contains"):
            index.setdefault(dst, set()).add(src)
    return index


def _forward_index(graph: Graph) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = {}
    for edge in graph.edges:
        index.setdefault(edge["src"], []).append(edge)
    return index


# 체인 확장에 쓰는 엣지 — 개발 착지 시 "같이 봐야 하는" 관계
_CHAIN_EDGES = ("imports", "calls", "resolves_to", "route_of", "contains")


def retrieve_chain(graph: Graph, query: str, k: int = 6, hops: int = 2,
                   graph_weight: float = 0.5) -> dict:
    """체인 인식 검색 (graph-tool-call wRRF 차용).

    벡터/키워드 단일 매치가 아니라, 시드 검색 결과를 그래프로 확장해 '워크플로우'를 돌려준다.
    - seeds = 기존 텍스트 스코어 상위 k
    - expand = 각 시드에서 _CHAIN_EDGES를 hops만큼 순회(파일 버튼→호출→백엔드 엔드포인트 등)
    - fuse = wRRF: 1/(60+text_rank) + graph_weight/(60+graph_rank)
    반환 {seeds, chain(융합 정렬), by_relation}.
    """
    seeds = search(graph, query, k=k)
    if not seeds:
        return {"seeds": [], "chain": [], "by_relation": {}}
    text_rank = {hit["id"]: i for i, hit in enumerate(seeds)}
    fwd = _forward_index(graph)

    # 그래프 확장 — BFS로 도달 노드에 최단 hop 기록 + 관계 라벨 수집
    reached: dict[str, int] = {}
    relations: dict[str, set[str]] = {}
    frontier = [(s["id"], 0) for s in seeds]
    visited = {s["id"] for s in seeds}
    while frontier:
        node_id, depth = frontier.pop(0)
        if depth >= hops:
            continue
        for edge in fwd.get(node_id, ()):
            if edge["kind"] not in _CHAIN_EDGES:
                continue
            dst = edge["dst"]
            relations.setdefault(dst, set()).add(edge["kind"])
            if dst not in reached or depth + 1 < reached[dst]:
                reached[dst] = depth + 1
            if dst not in visited:
                visited.add(dst)
                frontier.append((dst, depth + 1))

    # graph rank = hop 오름차순
    graph_ranked = sorted(reached, key=lambda nid: reached[nid])
    graph_rank = {nid: i for i, nid in enumerate(graph_ranked)}

    fused: dict[str, float] = {}
    for nid in set(text_rank) | set(graph_rank):
        score = 0.0
        if nid in text_rank:
            score += 1.0 / (60 + text_rank[nid])
        if nid in graph_rank:
            score += graph_weight / (60 + graph_rank[nid])
        fused[nid] = score

    chain = []
    for nid in sorted(fused, key=lambda n: -fused[n]):
        node = graph.nodes.get(nid)
        if node is None:
            continue
        chain.append({"rrf": round(fused[nid], 5),
                      "relation": sorted(relations.get(nid, [])) or ["seed"],
                      "hop": reached.get(nid, 0), **node})

    by_relation: dict[str, list[str]] = {}
    for nid, rels in relations.items():
        for rel in rels:
            by_relation.setdefault(rel, []).append(graph.nodes[nid]["name"]
                                                   if nid in graph.nodes else nid)
    return {"seeds": seeds, "chain": chain[:k * 4], "by_relation": by_relation}


def impact(graph: Graph, node_id: str, depth: int = 3) -> list[dict]:
    if node_id not in graph.nodes:
        return []
    index = _dependents_index(graph)
    visited = {node_id}
    frontier = [node_id]
    result: list[dict] = []
    for distance in range(1, depth + 1):
        next_frontier: list[str] = []
        for current in frontier:
            for dependent in index.get(current, ()):
                if dependent in visited:
                    continue
                visited.add(dependent)
                next_frontier.append(dependent)
                node = graph.nodes[dependent]
                result.append({"distance": distance, **node})
        frontier = next_frontier
        if not frontier:
            break
    return result
