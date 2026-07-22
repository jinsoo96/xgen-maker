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
# 표준값 0.75는 길이 정규화가 세다. 코드 블록은 길이 편차가 커서(짧은 헬퍼 vs 긴 서비스
# 클래스) 길다는 이유로 관련 있는 함수가 밀린다. probe가 코드 검색용으로 낮춘 값을 따른다.
_B = 0.5

# 언어 키워드는 어느 코드에나 있어 변별력이 없다. 요청 문장에 섞여 들어오면 노이즈만 된다.
_CODE_STOPWORDS = frozenset("""
if else for while return break continue def class function func const let var
import from export default public private static void new this self true false null none
type interface struct enum impl trait fn pub mut async await try catch throw finally
""".split())

# 노드 종류별 사전 가중치 — 질의와 무관하게, 무엇이 '고칠 자리'로 알맞은가.
# 컨테이너(저장소·기능)는 좌표가 아니고, 정의 자리(함수·클래스)가 좌표다.
_KIND_BOOST = {
    "function": 2.0, "class": 1.8, "endpoint": 1.6, "route": 1.6,
    "gateway_route": 1.4, "file": 1.3, "api_call": 1.2,
    # 컨테이너는 문서가 짧아(이름·경로뿐) 길이 정규화로 점수가 부풀기 쉽다.
    # 그런데 "여기를 고쳐라"의 답이 될 수는 없으므로 확실히 눌러 둔다.
    "feature": 0.5, "repo": 0.15,
}
# 테스트는 대개 고칠 대상이 아니라 고친 뒤 돌리는 것이다. 요청이 테스트를 지목하면 예외.
_TEST_MARKERS = ("test", "spec", "__tests__", "conftest", "fixture")
_TEST_PENALTY = 0.35
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
        self.meta: dict[str, tuple[str, str]] = {}   # node_id → (종류, 경로소문자)
        for node in nodes:
            terms = node_terms(node)
            if not terms:
                continue
            node_id = node["id"]
            self.length[node_id] = len(terms)
            # 테스트 판정은 경로만으론 부족하다 — TestFooInline처럼 이름만 테스트인 것도 있다
            self.meta[node_id] = (node.get("kind", ""),
                                  f"{node.get('path', '')} {node.get('name', '')}".lower())
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
        """쿼리 → {node_id: 점수}. 임계값으로 자르지 않는다 — 순위는 호출자가 정한다.

        점수 = BM25 × 폭넓게 맞은 정도 × 종류 가중치.
        BM25만 쓰면 흔한 단어 하나를 여러 번 가진 노드가, 여러 단어를 고루 가진 노드를
        이긴다. 요청이 막연할수록 그 편향이 커진다 — 그래서 '몇 개나 맞았나'를 곱한다.
        """
        tokens = [t for t in tokenize(query) if t not in _CODE_STOPWORDS]
        if not tokens:
            tokens = tokenize(query)          # 전부 걸러졌으면 원래 토큰으로
        scores: dict[str, float] = {}
        matched: dict[str, set[str]] = {}
        for token in tokens:
            for term in self.match_terms(token):
                # 접두사로 이어 붙인 어휘는 정확히 같은 말이 아니므로 그만큼만 인정한다
                fidelity = len(token) / len(term) if term != token else 1.0
                weight = self.idf(term) * fidelity
                for node_id, tf in self.postings[term].items():
                    length = self.length[node_id]
                    norm = tf * (_K1 + 1) / (tf + _K1 * (1 - _B + _B * length / self.avg_len))
                    scores[node_id] = scores.get(node_id, 0.0) + weight * norm
                    matched.setdefault(node_id, set()).add(token)

        wants_test = any(m in query.lower() for m in _TEST_MARKERS)
        total = len(tokens)
        for node_id, base in scores.items():
            coverage = len(matched[node_id]) / total
            scores[node_id] = base * (1.0 + coverage ** 1.5 * 2.0) * self._prior(node_id, wants_test)
        return scores

    def _prior(self, node_id: str, wants_test: bool) -> float:
        """질의와 무관한 가중치 — 무엇이 '고칠 자리'로 알맞은가."""
        node = self.meta.get(node_id)
        if node is None:
            return 1.0
        boost = _KIND_BOOST.get(node[0], 1.0)
        if not wants_test and any(m in node[1] for m in _TEST_MARKERS):
            boost *= _TEST_PENALTY
        return boost
