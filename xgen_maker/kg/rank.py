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
# 버전 리터럴 — 점으로 이어진 숫자. 쪼개면 변별력이 통째로 사라진다.
_VERSION = re.compile(r"(?<![\w.])\d+(?:\.\d+){1,3}(?![\w.])")

# BM25 표준 파라미터. 도메인 값이 아니라 알고리즘 상수다.
_K1 = 1.5
# 표준값 0.75는 길이 정규화가 세다. 코드 블록은 길이 편차가 커서(짧은 헬퍼 vs 긴 서비스
# 클래스) 길다는 이유로 관련 있는 함수가 밀린다.
# 값은 실제 머지된 MR 256건으로 훑어 정했다 — 0.0(정규화 없음) MRR 0.464 · 0.1 0.483 ·
# 0.2 0.493 · 0.3 0.488 · 0.5 0.474 · 0.75 0.456. 파일 노드가 참조 식별자까지 담게 되면서
# 문서 길이 편차가 더 커졌고, 그만큼 길이로 깎는 힘을 줄이는 쪽이 맞았다.
# ⚠️ 78건 표본에서는 0.5가 최적으로 보였다 — 작은 표본으로 이 값을 정하지 말 것.
_B = 0.2

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
    # 파일은 원래 1.3으로 눌러 뒀다(좌표가 덜 구체적이라). 그런데 파일 노드가 그 파일이
    # 참조하는 이름까지 담게 되면서 신호가 늘었고, 실제로 사람이 고친 자리가 파일 노드로
    # 잡히는 경우가 순위 실패의 44%였다. 256건으로 훑어 1.6으로 올린다 —
    # 1.3 MRR 0.494 · 1.6 0.498 · 2.0 0.489. 2.0은 상위10 회수는 가장 좋지만(0.766)
    # 착지 1위가 파일이 되는 비율이 10%→31%로 뛴다. 파일 노드에는 줄 번호가 없어
    # 에이전트가 열 자리를 잃는다. 1.6은 심볼 착지를 81%로 지키면서 이득만 취한다.
    "gateway_route": 1.4, "file": 1.6, "api_call": 1.2,
    # 컨테이너는 문서가 짧아(이름·경로뿐) 길이 정규화로 점수가 부풀기 쉽다.
    # 그런데 "여기를 고쳐라"의 답이 될 수는 없으므로 확실히 눌러 둔다.
    "feature": 0.5, "repo": 0.15,
}
# 테스트는 대개 고칠 대상이 아니라 고친 뒤 돌리는 것이다. 요청이 테스트를 지목하면 예외.
_TEST_MARKERS = ("test", "spec", "__tests__", "conftest", "fixture")
_TEST_PENALTY = 0.35
# 요청이 특정 서비스/저장소를 지목하면(예: "게이트웨이", "프론트엔드") 그 저장소로 편향한다.
# 서비스 이름 목록을 손으로 적지 않는다 — 저장소 이름 토큰을 그래프에서 그대로 뽑아,
# 쿼리 토큰과 겹치는 저장소를 지목된 것으로 본다. 코드 어휘를 더 많이 가졌다는 이유만으로
# 엉뚱한 서비스(대개 코퍼스를 지배하는 언어)가 이기던 문제를 바로잡는다.
# 값은 강한 종류 가중(function 2.0)과 견줄 만하게 — 지목이 명시적일 때만 켜진다.
_REPO_AFFINITY = 2.5
# 저장소 크기 보정. IDF는 코퍼스 전체에서 재므로, 노드가 3배 많은 저장소는 어떤
# 점수대에서든 뽑힐 기회가 3배다 — 그래서 검색이 '맞는 저장소'가 아니라 '큰 저장소'로
# 쏠린다. 실측(기능질의 265건): 한 저장소가 노드의 35.1%인데 정답인 건 12.5%,
# 1위로 뽑힌 건 28.7%였다. 반대로 노드 1.0%짜리 저장소가 정답 12.8%인데 1위는 4.9%.
# 예측 비중이 정답 비중이 아니라 노드 비중을 따라간다.
# 저장소별 사전확률을 이 표본에서 학습하지는 않는다 — 그건 '최근 어느 저장소에 MR이
# 많았나'를 외우는 것이라 일반화되지 않는다. 크기로만 눌러 기회를 고르게 한다.
# 값은 홀짝·전후 네 분할로 검증했다. α=0.1에서 R@1은 네 분할 모두(+0.015 +0.061
# +0.068 +0.008), MRR도 네 분할 모두(+0.018 +0.038 +0.054 +0.002) 올랐고 R@10은
# 중립(+0.015). α=0.2는 R@1을 더 올리지만 R@10이 분할마다 깎인다(-0.030 -0.045).
_REPO_SIZE_DAMP = 0.1
# 다시 하지 말 것: '저장소 단위 질의어 포괄도' 가중은 해롭다. 아깝게 놓친 건들에서
# 정답을 밀어낸 상위 칸의 69%가 다른 저장소이길래, 질의어가 고루 모인 저장소를
# 밀어주게 해봤다. 기능질의 265건에서 가중 0.5~5.0 · 지수 1~3 전 조합이 나빠졌다
# (R@10 0.762 → 0.736 이하). 큰 저장소일수록 어떤 질의어든 어딘가엔 있어서,
# 그 신호는 '맞는 저장소'가 아니라 '큰 저장소'를 가리킨다. 위 크기 보정이 같은
# 관찰에 대한 옳은 방향이다 — 큰 저장소를 밀어주는 게 아니라 눌러야 했다.

# 그래프 중심성(PageRank) 가중 — 같은 말을 가진 함수가 여럿일 때, 코드에서 실제로
# 중심인 것(다들 호출하는)을 앞세운다. 질의 관련도가 지배하도록 약하게(최대 +50%).
# 기법 출처(웹 조사): RANGER·코드 중심성 랭킹 연구의 "PageRank로 함수 중요도".
_CENTRALITY_WEIGHT = 0.5
# 이름은 노드의 정체라 더 무겁게, 경로는 그 다음.
# meta(요약·문서)는 실제 머지된 MR로 재 보고 1로 둔다. 2로 올리면 1위 적중은 오르지만
# (R@1 0.321→0.346) 상위 10 안에 정답이 들어올 확률이 떨어진다(R@10 0.679→0.641).
# 에이전트에게 건네는 근거 목록에 정답이 있느냐가 작업 성패를 가르므로 회수를 지킨다.
_FIELD_WEIGHT = {"name": 3, "path": 2, "repo": 1, "kind": 1, "meta": 1}
# refs = 그 파일이 정의하진 않았지만 다루는 이름들. 사람은 그 이름으로 찾는다.
# labels = 그 파일이 화면에 보여 주는 한글. 사용자는 UI에 적힌 말로 요청한다.
_META_KEYS = ("summary", "doc", "package", "route_path", "module", "service",
              "handler", "refs", "labels")


def tokenize(text: str) -> list[str]:
    """붙여 쓴 식별자를 사람이 읽는 단어로 되돌린다.

    `listApiCollections`·`main-tool-management-api-collections`·`user_id`를 통째로 두면
    어떤 토큰과도 안 맞는다. 코드 이름은 원래 여러 단어를 이어 만든 것이라 그 규칙을
    되돌린다. 단어 목록이 아니라 문자열 규칙이라 새 어휘가 나와도 손댈 게 없다.

    다만 버전만은 쪼개면 안 된다. "1.33.0"이 ["1","33","0"]이 되면 가장 변별력 높은
    정보가 사라진다 — 그 숫자들은 어디에나 있지만 "1.33.0"은 거의 없다. 의존성 갱신
    요청("xgen-sdk 1.33.0")에서 남는 단서가 그것뿐인 경우가 실제로 있었다.
    """
    versions = _VERSION.findall(text)
    words = [t for t in _SPLIT.split(_CAMEL.sub(" ", text).lower()) if t]
    return words + versions


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
            # 선언한 가중을 실제로 적용한다. 전에는 _FIELD_WEIGHT["meta"]를 적어 두고
            # 쓰지 않아, 의미층(요약·문서)의 무게를 조절할 손잡이가 없는 상태였다.
            terms.extend(tokenize(value) * _FIELD_WEIGHT["meta"])
    return terms


class Bm25Index:
    """노드 코퍼스의 역색인. 그래프가 바뀌면 다시 만든다."""

    def __init__(self, nodes: list[dict], centrality: dict[str, float] | None = None):
        self.postings: dict[str, dict[str, int]] = {}
        self.length: dict[str, float] = {}
        self.meta: dict[str, tuple[str, str, str]] = {}   # node_id → (종류, 경로소문자, 저장소)
        self.centrality = centrality or {}                # node_id → 0..1 (PageRank 정규화)
        self.repos: set[str] = set()
        self.repo_tokens: dict[str, set[str]] = {}   # 저장소 → 이름 토큰(지목 판정용)
        for node in nodes:
            terms = node_terms(node)
            if not terms:
                continue
            node_id = node["id"]
            self.length[node_id] = len(terms)
            repo = node.get("repo", "")
            # 테스트 판정은 경로만으론 부족하다 — TestFooInline처럼 이름만 테스트인 것도 있다
            self.meta[node_id] = (node.get("kind", ""),
                                  f"{node.get('path', '')} {node.get('name', '')}".lower(),
                                  repo)
            if repo and repo not in self.repo_tokens:
                self.repos.add(repo)
                self.repo_tokens[repo] = set(tokenize(repo))
            counts: dict[str, int] = {}
            for term in terms:
                counts[term] = counts.get(term, 0) + 1
            for term, tf in counts.items():
                self.postings.setdefault(term, {})[node_id] = tf
        # 저장소 이름 토큰의 '배타성' — 그 단어를 가진 노드 중 그 이름의 저장소 몫.
        # "frontend"는 98.9%(그 말을 하면 그 저장소를 뜻함), "mcp"는 25.8%(여러 곳에
        # 퍼진 도메인 용어). 지목 가중을 배타성만큼만 주면, 흔한 도메인 단어가
        # 우연히 저장소 이름과 겹쳤다는 이유로 검색이 그 저장소로 쏠리지 않는다.
        self.token_exclusivity: dict[str, float] = {}
        all_repo_tokens = set()
        for toks in self.repo_tokens.values():
            all_repo_tokens |= toks
        for tok in all_repo_tokens:
            owners = self.postings.get(tok, {})
            if not owners:
                continue
            mine = sum(1 for nid in owners
                       if tok in self.repo_tokens.get(self.meta.get(nid, ("", "", ""))[2], ()))
            self.token_exclusivity[tok] = mine / len(owners)
        # 저장소별 노드 수 — 크기가 곧 뽑힐 기회라서, 그 기회를 고르게 하는 데 쓴다.
        self.repo_size: dict[str, int] = {}
        for kind_path_repo in self.meta.values():
            repo_name = kind_path_repo[2]
            self.repo_size[repo_name] = self.repo_size.get(repo_name, 0) + 1
        self.avg_repo_size = (sum(self.repo_size.values())
                              / max(len(self.repo_size), 1)) or 1.0
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
        return self.search_with_coverage(query)[0]

    def search_with_coverage(self, query: str) -> tuple[dict[str, float], dict[str, int]]:
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
        # 저장소 지목 — 쿼리 토큰이 특정 저장소 이름 토큰과 겹치면 그 저장소로 편향한다.
        # 전부 걸리거나(변별력 0: 예 "xgen") 하나도 안 걸리면 적용하지 않는다.
        qtokens = set(tokens)
        targeted = {repo for repo, toks in self.repo_tokens.items() if toks & qtokens}
        # 지목에 쓰인 단어 자체도 기억한다. 그 단어를 '경로·이름에 직접 가진' 코드는,
        # 다른 저장소에 있더라도 똑같이 그 도메인의 코드다("harness"를 물었는데
        # harness_bridge/ 아래 코드가 이름만 겹친 저장소에 밀리면 안 된다).
        # 산문(요약·docstring)에만 언급한 노드는 해당 없다 — 그건 "이 코드가 그것"이
        # 아니라 "그것을 언급한다"에 불과하다.
        named = set()
        if targeted:
            for repo in targeted:
                named |= (self.repo_tokens[repo] & qtokens)
        if not (0 < len(targeted) < len(self.repos)):
            targeted = None
            named = set()
        total = len(tokens)
        for node_id, base in scores.items():
            coverage = len(matched[node_id]) / total
            scores[node_id] = base * (1.0 + coverage ** 1.5 * 2.0) * self._prior(
                node_id, wants_test, targeted, named)
        return scores, {nid: len(hit) for nid, hit in matched.items()}

    def _prior(self, node_id: str, wants_test: bool,
               targeted: set[str] | None = None,
               named: set[str] | None = None) -> float:
        """질의와 무관한 가중치 — 무엇이 '고칠 자리'로 알맞은가."""
        node = self.meta.get(node_id)
        if node is None:
            return 1.0
        boost = _KIND_BOOST.get(node[0], 1.0)
        if not wants_test and any(m in node[1] for m in _TEST_MARKERS):
            boost *= _TEST_PENALTY
        # 지목한 저장소에 있거나, 지목한 단어를 자기 경로·이름에 직접 가진 코드.
        # 가중은 그 단어의 배타성만큼만 — 도메인 용어(mcp·backend 등)는 거의 안 걸린다.
        if targeted and (node[2] in targeted
                         or (named and any(tok in node[1] for tok in named))):
            excl = max((self.token_exclusivity.get(tok, 0.0) for tok in (named or ())),
                       default=0.0)
            boost *= 1.0 + (_REPO_AFFINITY - 1.0) * excl
        # 큰 저장소일수록 뽑힐 기회가 많다 — 그만큼만 눌러 기회를 고르게 한다.
        if _REPO_SIZE_DAMP:
            size = self.repo_size.get(node[2], 0) or 1
            boost *= (self.avg_repo_size / size) ** _REPO_SIZE_DAMP
        # 구조적으로 중심인 코드를 약하게 앞세운다(동점 가르기 — 질의 관련도가 지배)
        cen = self.centrality.get(node_id)
        if cen:
            boost *= 1.0 + _CENTRALITY_WEIGHT * cen
        return boost
