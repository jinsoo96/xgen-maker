"""요청이 직접 지목한 곳 찾기 — 추측하지 않고.

지금까지는 한글 요청을 LLM으로 영문 키워드로 바꾼 뒤 그 키워드를 코드 이름과
글자로 맞춰 봤다. 그런데 LLM이 대는 이름("BackButton")과 실제 코드의 이름
("PrevNav")이 같으리라는 보장이 없다. 결국 이름 맞추기가 된다.

그런데 사람은 대개 요청에 **정확한 지목**을 함께 준다.
  "/ontology 항목에서 뒤로가기 버튼"  → 화면 주소 /ontology
  "rag_service.py 의 임베딩"          → 파일 이름
  "validate_token 이 왜 실패하나"      → 심볼 이름
이건 추측할 필요가 없다. 그래프에서 그대로 찾으면 된다. 그리고 거기서 관계를 타고
나가면(라우트→파일→쓰는 기능→그 안의 코드) 범위가 정확히 좁혀진다.

이것이 지식그래프를 텍스트 색인이 아니라 지도로 쓰는 방법이다.
"""
from __future__ import annotations

import re

from .graph import Graph

# 화면 주소처럼 생긴 것: /ontology, /admin/users, /chat/[id]
_ROUTE = re.compile(r"(?<![\w/])/[a-z0-9][\w\-/\[\]]*", re.I)
# 파일 이름처럼 생긴 것: rag_service.py, page.tsx
_FILE = re.compile(r"\b[\w\-]+\.(?:py|tsx?|jsx?|rs|vue|svelte|go|java|rb)\b", re.I)
# 코드 식별자처럼 생긴 것: validate_token, OntologyRoute, ToolCard
_SYMBOL = re.compile(r"\b(?:[a-z]+(?:_[a-z0-9]+)+|[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+)\b")

# 앵커에서 뻗어 나갈 관계. 같은 패키지가 저장소마다 갈라져 있으므로 same_package도 탄다.
_WALK = ("route_of", "contains", "imports", "same_package", "resolves_to", "routes_via")


def mentions(query: str) -> dict[str, list[str]]:
    """요청에서 '지목'으로 보이는 것들을 뽑는다(있는 그대로, 해석하지 않는다)."""
    return {
        "routes": [m.group(0).rstrip("/") or "/" for m in _ROUTE.finditer(query)],
        "files": _FILE.findall(query),
        "symbols": _SYMBOL.findall(query),
    }


def find_anchors(graph: Graph, query: str) -> list[dict]:
    """요청이 지목한 노드들. 정확히 일치하는 것만 — 비슷한 것은 검색이 할 일이다."""
    found = mentions(query)
    if not any(found.values()):
        return []
    wanted_routes = {r.lower() for r in found["routes"]}
    wanted_files = {f.lower() for f in found["files"]}
    wanted_symbols = {s.lower() for s in found["symbols"]}
    anchors = []
    for node in graph.nodes.values():
        name = node["name"].lower()
        kind = node["kind"]
        if kind == "route" and name in wanted_routes:
            anchors.append(node)
        elif kind == "gateway_route" and name in wanted_routes:
            anchors.append(node)
        elif kind == "file" and name in wanted_files:
            anchors.append(node)
        elif kind in ("function", "class") and name in wanted_symbols:
            anchors.append(node)
    return anchors


def expand(graph: Graph, anchors: list[dict], hops: int = 5, limit: int = 120) -> list[dict]:
    """앵커에서 관계를 타고 나가며 관련 코드를 모은다(가까운 것부터).

    라우트 하나에서 시작해도 화면 파일 → 그 화면이 쓰는 기능 → (저장소를 건너)
    그 기능의 실제 파일까지 닿아야 한다. 중간에 끊기면 정작 고칠 코드에 못 간다.
    """
    if not anchors:
        return []
    forward: dict[str, list[dict]] = {}
    for edge in graph.edges:
        if edge["kind"] in _WALK:
            forward.setdefault(edge["src"], []).append(edge)

    seen = {a["id"] for a in anchors}
    out = [dict(a, hop=0) for a in anchors]
    frontier = [(a["id"], 0) for a in anchors]
    while frontier and len(out) < limit:
        node_id, depth = frontier.pop(0)
        if depth >= hops:
            continue
        for edge in forward.get(node_id, ()):
            target = edge["dst"]
            if target in seen:
                continue
            node = graph.nodes.get(target)
            if node is None:
                continue
            seen.add(target)
            out.append(dict(node, hop=depth + 1, via=edge["kind"]))
            frontier.append((target, depth + 1))
            if len(out) >= limit:
                break
    return out


def rank_within(scope: list[dict], query: str, keywords: str = "",
                k: int = 8) -> list[dict]:
    """좁혀진 범위 안에서 요청과 가까운 순으로.

    범위를 그래프가 정해 줬으므로 여기서 단어를 맞추는 일은 안전하다 — 엉뚱한
    저장소로 샐 수 없기 때문이다. 코드 용어 변환도 이 단계에서 비로소 제값을 한다.
    (범위를 정하는 데 쓰면 이름을 잘못 짚었을 때 통째로 빗나간다)
    """
    from .rank import tokenize
    words = set(tokenize(query)) | set(tokenize(keywords))
    if not words:
        return [dict(n, score=0.0) for n in scope[:k]]
    scored = []
    for node in scope:
        text = set(tokenize(node["name"])) | set(tokenize(node["path"]))
        hits = len(words & text)
        # 요청 단어를 많이 가질수록, 앵커에서 가까울수록 앞으로.
        # 화면 요청에서 정작 고칠 것은 부품이지 컨테이너가 아니다.
        weight = 2 if node["kind"] in ("function", "class", "file") else 1
        scored.append((hits * 10 * weight - node.get("hop", 0), node))
    scored.sort(key=lambda pair: -pair[0])
    return [dict(n, score=round(float(s), 1)) for s, n in scored[:k]]
