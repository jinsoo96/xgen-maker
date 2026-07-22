"""검색 순위 — BM25 역색인.

점수를 손으로 정한 상수의 합으로 매기면(이름 +25, 경로 +15 …) 흔한 단어가 이긴다.
"api"는 수천 노드에 있고 "collections"는 몇 개에만 있는데, 상수 합산은 둘을 같게 본다.
그래서 "api 도구 목록"이 온갖 백엔드를 끌어왔다.

BM25는 단어의 희귀도(IDF)를 코퍼스에서 직접 재고, 문서 길이로 정규화한다. 튜닝할
상수도, 잘라낼 임계값도 없다 — 희귀한 단어를 많이 가진 노드가 이긴다.

필드는 반복 횟수로 무게를 준다(BM25F의 간이형). 이름은 그 노드의 정체이므로 여러 번,
경로·요약은 한 번. 도메인 어휘가 아니라 구조에 대한 가중이다.
"""
from __future__ import annotations

import math
import re
from bisect import bisect_left

_SPLIT = re.compile(r"[^0-9a-z가-힣]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# BM25 표준 파라미터. 도메인 값이 아니라 알고리즘 상수다.
_K1 = 1.5
_B = 0.75
# 이름은 노드의 정체라 더 무겁게, 경로는 그 다음.
_FIELD_WEIGHT = {"name": 3, "path": 2, "repo": 1, "kind": 1, "meta": 1}
_META_KEYS = ("summary", "doc", "package", "route_path", "module", "service", "handler")


def tokenize(text: str) -> list[str]:
    """붙여 쓴 식별자를 사람이 읽는 단어로 되돌린다.

    `listApiCollections`·`main-tool-management-api-collections`·`user_id`를 통째로 두면
    어떤 토큰과도 안 맞는다. 코드 이름은 원래 여러 단어를 이어 만든 것이라 그 규칙을
    되돌린다. 단어 목록이 아니라 문자열 규칙이라 새 어휘가 나와도 손댈 게 없다.
    """
    return [t for t in _SPLIT.split(_CAMEL.sub(" ", text).lower()) if t]


def node_terms(node: dict) -> list[str]:
    """이 노드를 가리킬 수 있는 모든 말 — 이름·경로·저장소·종류·의미층(요약/문서)."""
    terms: list[str] = []
    for field, source in (("name", node.get("name", "")),
                          ("path", node.get("path", "")),
                          ("repo", node.get("repo", "")),
                          ("kind", node.get("kind", ""))):
        words = tokenize(str(source))
        terms.extend(words * _FIELD_WEIGHT[field])
    meta = node.get("meta") or {}
    for key in _META_KEYS:
        value = meta.get(key)
        if isinstance(value, str) and value:
            terms.extend(tokenize(value))
    return terms


class Bm25Index:
    """노드 코퍼스의 역색인. 그래프가 바뀌면 다시 만든다."""

    def __init__(self, nodes: list[dict]):
        self.postings: dict[str, dict[str, int]] = {}
        self.length: dict[str, float] = {}
        for node in nodes:
            terms = node_terms(node)
            if not terms:
                continue
            node_id = node["id"]
            self.length[node_id] = len(terms)
            counts: dict[str, int] = {}
            for term in terms:
                counts[term] = counts.get(term, 0) + 1
            for term, tf in counts.items():
                self.postings.setdefault(term, {})[node_id] = tf
        self.total = len(self.length) or 1
        self.avg_len = sum(self.length.values()) / self.total
        self.vocab = sorted(self.postings)
        self._idf: dict[str, float] = {}

    def idf(self, term: str) -> float:
        cached = self._idf.get(term)
        if cached is None:
            df = len(self.postings.get(term, ()))
            cached = math.log(1 + (self.total - df + 0.5) / (df + 0.5))
            self._idf[term] = cached
        return cached

    def match_terms(self, token: str) -> list[str]:
        """쿼리 토큰에 대응하는 코퍼스 어휘.

        한국어는 어간에 조사가 붙고("취소"→"취소를") 영어는 복수형이 붙는다("tool"→"tools").
        조사·어미 목록을 적는 대신 접두사 관계로 잇는다 — 양쪽 방향 모두.
        """
        if token in self.postings:
            return [token]
        hits = []
        start = bisect_left(self.vocab, token)
        for term in self.vocab[start:]:                 # 코퍼스 어휘가 더 긴 경우
            if not term.startswith(token):
                break
            hits.append(term)
        for cut in range(len(token) - 1, 1, -1):        # 쿼리 토큰이 더 긴 경우
            prefix = token[:cut]
            if prefix in self.postings:
                hits.append(prefix)
                break
        return hits

    def search(self, query: str) -> dict[str, float]:
        """쿼리 → {node_id: 점수}. 임계값으로 자르지 않는다 — 순위는 호출자가 정한다."""
        scores: dict[str, float] = {}
        for token in tokenize(query):
            for term in self.match_terms(token):
                # 접두사로 이어 붙인 어휘는 정확히 같은 말이 아니므로 그만큼만 인정한다
                fidelity = len(token) / len(term) if term != token else 1.0
                weight = self.idf(term) * fidelity
                for node_id, tf in self.postings[term].items():
                    length = self.length[node_id]
                    norm = tf * (_K1 + 1) / (tf + _K1 * (1 - _B + _B * length / self.avg_len))
                    scores[node_id] = scores.get(node_id, 0.0) + weight * norm
        return scores
