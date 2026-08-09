"""함께 볼 파일 — 착지한 곳과 이어져 있어 같이 고쳐야 할 가능성이 있는 파일.

검색은 "어디를 고칠까"의 답을 하나 준다. 그런데 실제 MR은 파일 하나로 끝나지
않는다 — 실측: 실제 머지된 MR 265건 중 파일 하나만 고친 건 79건뿐이고, 5개
이상을 고친 건이 67건이다. 착지 10개만 주면 그 MR이 고친 파일의 44.2%만 덮고,
전부 덮은 건 25.7%에 그쳤다. 나머지는 에이전트가 존재조차 모르는 채로 구현한다.

그래서 착지한 파일들이 부르고·가져오는 이웃을 표로 세어 상위만 덧붙인다.
1홉 이웃은 중앙값 58개·최대 310개라 통째로는 못 준다. 상한 12개일 때:
  착지10만        평균 덮음 44.2% · 전부 덮음 25.7%
  +이웃 8개       평균 덮음 53.7% · 전부 덮음 36.6%
  +이웃 12개      평균 덮음 57.1% · 전부 덮음 40.4%   ← 채택
  +이웃 20개      평균 덮음 59.9% · 전부 덮음 43.8%
20개가 더 덮지만 이름 8개를 더 얹는 값으로 3.4%p다. 이건 '여기도 고쳐라'가
아니라 '이것과 이어져 있다'는 단서이므로, 목록이 길수록 단서가 흐려진다.

엣지 종류별 가중은 두지 않는다 — imports/calls를 same_package보다 무겁게 줘도
결과가 소수점까지 같았다(균등·같은폴더낮춤·같은폴더제외 세 방식 동일).
근거 없는 손잡이는 두지 않는다.
"""
from __future__ import annotations

from collections import defaultdict

from .graph import Graph

# 착지 10개 기준 상한. 위 실측 곡선 참고 — 바꾸려면 다시 재고 나서 바꿀 것.
_CAP = 12
# repo→file 포함 관계는 이웃이 아니다. 그걸 세면 같은 저장소 전체가 이웃이 된다.
_NOT_NEIGHBOR = frozenset({"contains"})


def companion_files(graph: Graph, landing: list[dict], cap: int = _CAP) -> list[dict]:
    """착지 결과와 이어진 파일들 — 상위 착지의 이웃일수록 강한 단서로 센다."""
    top: list[str] = []
    for hit in landing:
        path = hit.get("path") or ""
        if path and path not in top:
            top.append(path)
    if not top:
        return []

    node_path = {nid: (node.get("path") or "") for nid, node in graph.nodes.items()}
    node_repo = {nid: (node.get("repo") or "") for nid, node in graph.nodes.items()}
    adjacent: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    repo_of: dict[str, str] = {}
    for edge in graph.edges:
        if edge["kind"] in _NOT_NEIGHBOR:
            continue
        src, dst = node_path.get(edge["src"], ""), node_path.get(edge["dst"], "")
        if not src or not dst or src == dst:
            continue
        adjacent[src][dst].add(edge["kind"])
        adjacent[dst][src].add(edge["kind"])
        repo_of.setdefault(src, node_repo.get(edge["src"], ""))
        repo_of.setdefault(dst, node_repo.get(edge["dst"], ""))

    landed = set(top)
    votes: dict[str, float] = defaultdict(float)
    why: dict[str, set[str]] = defaultdict(set)
    for position, path in enumerate(top):
        weight = 1.0 / (1 + position)
        for neighbor, kinds in adjacent.get(path, {}).items():
            if neighbor in landed:
                continue
            votes[neighbor] += weight * len(kinds)
            why[neighbor] |= kinds
    ranked = sorted(votes, key=lambda p: (-votes[p], p))[:cap]
    return [{"path": path, "repo": repo_of.get(path, ""),
             "relations": sorted(why[path]), "score": round(votes[path], 4)}
            for path in ranked]


def as_prompt_block(companions: list[dict], limit: int = _CAP) -> str:
    """에이전트에게 줄 단서 블록. 고치라는 지시가 아니라 '이어져 있다'는 사실이다."""
    if not companions:
        return ""
    lines = ["## 착지한 코드와 이어진 파일",
             "고쳐야 한다는 뜻이 아니다. 이번 변경이 이 파일들의 기대를 깨는지 확인하고,",
             "깨뜨린다면 같이 고친다. 아니면 건드리지 않는다.", ""]
    for item in companions[:limit]:
        relations = ", ".join(item["relations"]) or "연결"
        lines.append(f"- {item['path']} ({relations})")
    return "\n".join(lines)
