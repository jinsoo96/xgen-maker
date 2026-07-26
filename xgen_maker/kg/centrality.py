"""그래프 중심성 — 무엇이 구조적으로 중요한 코드인가(질의와 무관한 사전 신호).

착지 순위에서 오래 빠져 있던 신호. BM25는 "요청과 글자가 얼마나 겹치나"만 본다.
그래서 같은 말을 가진 함수가 여럿이면, 코드에서 실제로 중심인 함수(다들 호출하는
그 함수)와 구석의 헬퍼를 구별하지 못한다("워크플로우 노드 실행"이 중심
WorkflowRunNode.execute 대신 주변부 게이트 함수로 착지했다).

기법 출처(웹 조사): RANGER(2509.25257)·코드 중심성 랭킹 연구가 공통으로 쓰는
"PageRank로 함수 중요도" — 한 노드는 그를 가리키는(호출·임포트) 노드들이 중요할수록
중요하다. 여기서는 calls·imports 엣지 위에서 표준 PageRank를 돌린다(src→dst로 rank가
흐르므로 dst=피호출/피임포트가 호출자들의 중요도를 모은다).

부스트가 아니라 사전값이다 — 질의 관련도가 여전히 지배하고, 중심성은 동점을 가른다.
"""
from __future__ import annotations

import math

# rank가 흐르는 관계 — 의존의 방향(누가 나를 쓰는가)이 중요도를 만든다.
_CENTRALITY_EDGES = ("calls", "imports")
_DAMPING = 0.85
_ITERATIONS = 20


def pagerank(nodes: list[dict], edges: list[dict],
             kinds: tuple[str, ...] = _CENTRALITY_EDGES,
             damping: float = _DAMPING, iterations: int = _ITERATIONS) -> dict[str, float]:
    """calls·imports 그래프 위 표준 PageRank. 반환 {node_id: rank}.

    src→dst 방향으로 rank가 흐른다(호출자→피호출자). 나가는 엣지가 없는 노드(dangling)의
    rank는 전체에 고루 되돌려, 링크 없는 큰 조각이 rank를 빨아들이지 않게 한다.
    """
    ids = [n["id"] for n in nodes]
    n = len(ids)
    if n == 0:
        return {}
    idset = set(ids)
    out_links: dict[str, list[str]] = {}
    for edge in edges:
        if edge["kind"] in kinds and edge["src"] in idset and edge["dst"] in idset:
            out_links.setdefault(edge["src"], []).append(edge["dst"])
    out_deg = {src: len(dsts) for src, dsts in out_links.items()}
    dangling = [i for i in ids if i not in out_links]

    rank = {i: 1.0 / n for i in ids}
    base = (1.0 - damping) / n
    for _ in range(iterations):
        nxt = {i: base for i in ids}
        # dangling 노드의 rank는 전체에 고루 재분배
        dshare = damping * sum(rank[i] for i in dangling) / n
        for src, dsts in out_links.items():
            share = damping * rank[src] / out_deg[src]
            for dst in dsts:
                nxt[dst] += share
        if dshare:
            for i in ids:
                nxt[i] += dshare
        rank = nxt
    return rank


def centrality(nodes: list[dict], edges: list[dict]) -> dict[str, float]:
    """PageRank를 [0,1]로 정규화(로그·최댓값). 반환 {node_id: 0..1}.

    로그를 쓰는 이유: rank는 소수의 허브에 몰려 분포가 극단적이다. 그대로 곱하면
    허브 하나가 순위를 지배한다. 로그로 눌러 '중심일수록 조금 더'로 만든다.
    """
    pr = pagerank(nodes, edges)
    if not pr:
        return {}
    top = max(pr.values())
    if top <= 0:
        return {i: 0.0 for i in pr}
    denom = math.log1p(top)
    return {i: (math.log1p(v) / denom if denom else 0.0) for i, v in pr.items()}
