"""랭킹 사전가중치 — 무엇이 '고칠 자리'로 알맞은가.

기법 출처: probelabs/probe (BM25 × coverage × node-type). 코드 검색에서 검증된 것을
우리 노드 종류(function/class/endpoint/file/feature/repo)에 맞춰 옮겼다.
"""
import unittest

from xgen_maker.kg.graph import Graph
from xgen_maker.kg.search import search


class TestTestFilesAreDemoted(unittest.TestCase):
    """회귀: "뒤로가기 버튼 고쳐줘"가 테스트 함수로 착지했다."""

    def _graph(self) -> Graph:
        g = Graph()
        g.add_node("r", "repo", "r", "r", "/r")
        g.add_node("r:src/nav.py#back_button", "function", "back_button", "r",
                   "src/nav.py", 10)
        g.add_node("r:tests/test_nav.py#test_back_button", "function", "test_back_button",
                   "r", "tests/test_nav.py", 5)
        return g

    def test_real_code_beats_its_test(self):
        top = search(self._graph(), "back button", k=1)[0]
        self.assertEqual(top["path"], "src/nav.py")

    def test_test_wins_when_the_request_asks_for_it(self):
        """테스트를 고쳐 달라고 하면 페널티를 걸면 안 된다."""
        hits = search(self._graph(), "back button test", k=2)
        self.assertEqual(hits[0]["path"], "tests/test_nav.py")


class TestCoverageBeatsRepetition(unittest.TestCase):
    """흔한 단어 하나를 여러 번 가진 노드보다, 여러 단어를 고루 가진 노드가 낫다."""

    def test_broad_match_wins(self):
        g = Graph()
        g.add_node("r", "repo", "r", "r", "/r")
        # 'token'만 여러 번
        g.add_node("r:a.py#token_token_token", "function", "token_token_token", "r",
                   "a.py", 1, summary="token token token token token")
        # 'token'과 'validate' 둘 다
        g.add_node("r:b.py#validate_token", "function", "validate_token", "r", "b.py", 1)
        top = search(g, "validate token", k=1)[0]
        self.assertEqual(top["name"], "validate_token")


class TestContainersAreNotLandingSites(unittest.TestCase):
    """저장소·기능은 좌표가 아니다 — 고칠 자리는 정의가 있는 곳이다."""

    def test_function_beats_container(self):
        g = Graph()
        g.add_node("payments", "repo", "payments", "payments", "/payments")
        g.add_node("payments:feature:@x/payments", "feature", "@x/payments", "payments",
                   "features/payments")
        g.add_node("payments:src/pay.py#payments_charge", "function", "payments_charge",
                   "payments", "src/pay.py", 3)
        top = search(g, "payments", k=1)[0]
        self.assertEqual(top["kind"], "function")


class TestLanguageKeywordsAreNotSignal(unittest.TestCase):
    def test_stopwords_do_not_decide(self):
        from xgen_maker.kg.rank import _CODE_STOPWORDS
        for word in ("class", "function", "return", "import", "async"):
            self.assertIn(word, _CODE_STOPWORDS)


class TestRepoAffinity(unittest.TestCase):
    """요청이 특정 서비스/저장소를 지목하면 그 저장소로 편향한다.

    회귀: "게이트웨이에서 토큰 검증"이 코퍼스를 지배하는 다른 언어의 함수로 샜다 —
    게이트웨이 코드가 결과에 있어도 순위에서 밀렸다."""

    def _graph(self) -> Graph:
        g = Graph()
        # 게이트웨이(작은 저장소)의 진짜 토큰 검증
        g.add_node("gw", "repo", "gw", "gateway-svc", "/gw")
        g.add_node("gateway-svc:src/auth.rs#validate_token", "function",
                   "validate_token", "gateway-svc", "src/auth.rs", 5)
        # 다른 저장소(코퍼스 지배)에서 token을 더 많이 언급하는 함수
        for i in range(6):
            g.add_node(f"api-svc:h{i}.py#handle_token_{i}", "function",
                       f"handle_token_{i}", "api-svc", f"h{i}.py", 1,
                       summary="token token validate token auth token")
        return g

    def test_named_service_wins_even_if_outnumbered(self):
        # 서비스를 지목("gateway")하면 그 저장소의 검증 함수가 이긴다
        top = search(self._graph(), "gateway validate token", k=1)[0]
        self.assertEqual(top["repo"], "gateway-svc")
        self.assertEqual(top["name"], "validate_token")

    def test_no_service_named_no_affinity(self):
        # 저장소를 지목하지 않으면 편향이 걸리지 않는다(전부/전무 = 변별력 0)
        hits = search(self._graph(), "validate token", k=8)
        self.assertTrue(hits)  # 편향 없이도 정상 동작(순위만 다름)


class TestCentralityPrior(unittest.TestCase):
    """그래프에서 실제로 중심인 코드(다들 호출하는)를 약하게 앞세운다.

    회귀: 같은 말을 가진 함수가 여럿일 때 중심 함수 대신 주변부 헬퍼로 착지했다.
    """

    def test_central_function_beats_peripheral_twin(self):
        g = Graph()
        g.add_node("r", "repo", "r", "r", "/r")
        # 같은 이름·같은 관련도의 두 execute — 하나는 다들 부르고(중심), 하나는 아무도 안 부른다
        g.add_node("r:core.py#execute", "function", "execute", "r", "core/engine.py", 5)
        g.add_node("r:leaf.py#execute", "function", "execute", "r", "misc/leaf.py", 5)
        # 여러 호출자가 core의 execute를 부른다 → 중심성↑
        for i in range(8):
            caller = f"r:caller{i}.py"
            g.add_node(caller, "file", f"caller{i}.py", "r", f"c{i}.py")
            g.add_edge(caller, "r:core.py#execute", "calls")
        from xgen_maker.kg.centrality import centrality
        cen = centrality(list(g.nodes.values()), g.edges)
        self.assertGreater(cen.get("r:core.py#execute", 0), cen.get("r:leaf.py#execute", 0))
        top = search(g, "execute", k=1)[0]
        self.assertEqual(top["path"], "core/engine.py")  # 중심인 쪽으로 착지
