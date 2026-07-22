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
