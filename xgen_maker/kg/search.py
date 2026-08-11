"""KG 검색·영향분석 — 루프의 착지점 특정(③)과 MR 영향분석(⑨)의 재료.

search: 토큰/부분일치/퍼지 점수 상위 k.
impact: 역방향 BFS — "이 노드가 바뀌면 누가 영향받나" (imports·calls·resolves_to·route_of·contains 역추적).
"""
from __future__ import annotations

from .graph import Graph
from .rank import Bm25Index, tokenize


def _index(graph: Graph) -> Bm25Index:
    """그래프별 역색인. 노드 구성이 바뀌면 다시 만든다.

    색인은 그래프 객체 밖 속성에 둔다 — nodes 안에 넣으면 Graph.save가 통째로
    직렬화하려다 깨진다. 중심성(PageRank)도 여기서 함께 계산해 색인에 넘긴다 —
    질의와 무관한 구조 신호라 색인 수명과 같이 캐시하면 된다.
    """
    # 노드 '개수'로 무효화하면 개명처럼 수가 그대로인 변경을 놓쳐 옛 색인이 남는다
    # (실측: 갱신 뒤 새 심볼이 검색에 아예 안 잡히고 재착지가 빈 결과였다).
    version = getattr(graph, "rev", len(graph.nodes))
    cached = graph.__dict__.get("_bm25")
    if cached is not None and graph.__dict__.get("_bm25_ver") == version:
        return cached
    from .centrality import centrality
    nodes = list(graph.nodes.values())
    index = Bm25Index(nodes, centrality=centrality(nodes, graph.edges))
    graph.__dict__["_bm25"] = index
    graph.__dict__["_bm25_ver"] = version
    graph.__dict__.pop("_lexicon", None)      # 색인이 새로 서면 어휘 대응도 다시 배운다
    return index


def lexicon(graph: Graph) -> dict[str, list[str]]:
    """이 코드베이스의 한글↔코드 어휘 대응. 색인과 같은 수명으로 캐시한다."""
    cached = graph.__dict__.get("_lexicon")
    if cached is not None:
        return cached
    _index(graph)                              # rev 기준 캐시 정리를 태운다
    from .lexicon import build_lexicon
    built = build_lexicon(list(graph.nodes.values()))
    graph.__dict__["_lexicon"] = built
    return built


# 바깥 라우팅 신호(LLM이 고른 저장소)에 줄 가중. 거르지 않고 밀어주기만 한다.
# 실제 머지된 MR 265건, 실제로 보내는 프롬프트가 낸 추측으로 재고 네 분할로 검증:
#   1.00(끔) R@1 0.419 R@10 0.777 MRR 0.534 | A 0.789 B 0.765 C 0.864 D 0.692
#   1.15     R@1 0.419 R@10 0.785 MRR 0.539
#   1.30     R@1 0.415 R@10 0.796 MRR 0.538 | A 0.782 B 0.811 C 0.886 D 0.707  ← 채택
#   1.50     R@1 0.404 R@10 0.796 MRR 0.531   (R@10 같은데 1위·MRR만 잃는다)
#   1.80     R@1 0.415 R@10 0.758 MRR 0.522   전 분할 하락
# 세게 주면 안 되는 이유가 숫자에 그대로 있다 — 이 신호는 절반쯤만 맞다(47.9%).
# 값은 "틀려도 검색 근거가 이길 수 있는 크기"여야 한다.
# ⚠️ 별도 라우팅 전용 프롬프트로 재면 1.5가 최적으로 보인다. 그건 배포하지 않는
#    프롬프트다 — 두 추측의 일치율이 62.5%라 값이 옮겨가지 않는다. 재려면 실제로
#    보내는 프롬프트로 잴 것.
# 처음 보는 MR 2,412건에서도 확인했다(같은 표본에서 고른 다른 상수 셋은 최적점이
# 옮겨 갔으므로): 1.0 R@10 0.807 · 1.15 0.809 · 1.3 0.809 MRR 0.573 ← 유지 ·
# 1.5 0.810/0.571. 1.0~1.5가 평평하고 1.3이 MRR 최고다.
_REPO_HINT = 1.3


def search(graph: Graph, query: str, k: int = 10,
           kinds: tuple[str, ...] | None = None, hint_repo: str = "") -> list[dict]:
    """쿼리와 관련된 노드 상위 k개(BM25).

    점수 임계값을 두지 않는다. "몇 점 이상"은 코퍼스마다 달라 임의로 자르면 작은
    저장소에서 다 잘리거나 큰 저장소에서 쓰레기가 통과한다. 순위만 매기고 k로 자른다.

    hint_repo는 "이 저장소일 것 같다"는 바깥 신호(예: LLM 라우팅)다. 거르지 않고
    가중만 준다 — 그 추측이 틀렸을 때 정답을 아예 못 보게 되면 안 된다.
    """
    scores = _index(graph).search(query)
    if not scores:
        return []
    if hint_repo and _REPO_HINT != 1.0:
        for node_id in scores:
            node = graph.nodes.get(node_id)
            if node is not None and node.get("repo") == hint_repo:
                scores[node_id] *= _REPO_HINT
    # 사람이 식별자를 그대로 쳤다면 그 노드를 지목한 것이다. 그 신호를 점수에 상수로
    # 섞으면 "얼마를 더할지"를 또 손으로 정하게 된다. 정렬 차원을 나눠 우선순위로 둔다.
    needle = query.strip().lower()
    results = []
    for node_id, score in scores.items():
        node = graph.nodes.get(node_id)
        if node is None or (kinds and node["kind"] not in kinds):
            continue
        if (node.get("meta") or {}).get("deprecated"):
            continue          # 사람이 "쓰지 말라"고 표시한 코드로는 착지하지 않는다(R8)
        # 이름이 그대로 입력된 노드를 앞세우되, 컨테이너(저장소·기능)는 예외다.
        # 저장소 이름은 그 안의 모든 코드와 겹치므로 "payments"만 쳐도 늘 정확히
        # 일치해, 정작 고칠 함수를 밀어낸다.
        name = node.get("name", "").lower()
        pointable = node["kind"] not in ("repo", "feature")
        exact = pointable and name == needle
        partial = pointable and not exact and len(needle) > 2 and needle in name
        results.append(((exact, partial, score), node))
    results.sort(key=lambda pair: pair[0], reverse=True)
    return [{"score": round(key[2], 1), **node} for key, node in results[:k]]


def _dependents_index(graph: Graph) -> dict[str, set[str]]:
    """dst가 바뀌면 src가 영향받는 방향으로 역인덱스 구성."""
    index: dict[str, set[str]] = {}
    for edge in graph.edges:
        src, dst, kind = edge["src"], edge["dst"], edge["kind"]
        # same_package는 프론트엔드 스코프(app/lib/features)를 잇는 유일한 다리다.
        # 빼면 "이걸 고치면 누가 깨지나"가 스코프 안에서만 보여, 에이전트에게 건네는
        # 의존자 목록에서 다른 패키지의 사용처가 통째로 빠진다.
        if kind in ("imports", "calls", "resolves_to", "route_of", "contains",
                    "same_package"):
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
