"""요청이 지목한 곳으로 착지 — 이름을 추측해 맞추지 않는다."""
import unittest

from xgen_maker.kg.graph import Graph
from xgen_maker.kg.anchor import mentions, find_anchors, expand, rank_within


def _graph() -> Graph:
    """화면 → 파일 → (저장소를 건너) 기능 → 그 안의 부품."""
    g = Graph()
    g.add_node("app", "repo", "app", "app", "/app")
    g.add_node("feat", "repo", "feat", "feat", "/feat")
    g.add_node("app:route:/ontology", "route", "/ontology", "app",
               "src/app/ontology/page.tsx")
    g.add_node("app:src/app/ontology/page.tsx", "file", "page.tsx", "app",
               "src/app/ontology/page.tsx")
    g.add_node("app:feature:@x/onto", "feature", "@x/onto", "app", "")
    g.add_node("feat:feature:@x/onto", "feature", "@x/onto", "feat", "features/onto")
    g.add_node("feat:features/onto/src/back-nav.tsx", "file", "back-nav.tsx", "feat",
               "features/onto/src/back-nav.tsx")
    g.add_node("feat:features/onto/src/table.tsx", "file", "table.tsx", "feat",
               "features/onto/src/table.tsx")
    # 요청과 무관한 다른 저장소 — 이름만 비슷해도 끌려오면 안 된다
    g.add_node("other", "repo", "other", "other", "/other")
    g.add_node("other:t.py#test_back_button", "function", "test_back_button", "other",
               "tests/t.py", 1)
    g.add_edge("app:route:/ontology", "app:src/app/ontology/page.tsx", "route_of")
    g.add_edge("app:src/app/ontology/page.tsx", "app:feature:@x/onto", "imports")
    g.add_edge("app:feature:@x/onto", "feat:feature:@x/onto", "same_package")
    g.add_edge("feat:feature:@x/onto", "feat:features/onto/src/back-nav.tsx", "contains")
    g.add_edge("feat:feature:@x/onto", "feat:features/onto/src/table.tsx", "contains")
    return g


class TestMentions(unittest.TestCase):
    def test_picks_out_what_the_request_points_at(self):
        found = mentions("/ontology 항목에서 뒤로가기버튼 고쳐줘")
        self.assertIn("/ontology", found["routes"])

        found = mentions("rag_service.py 에서 임베딩 실패")
        self.assertIn("rag_service.py", found["files"])

        found = mentions("validate_token 이 왜 실패하나")
        self.assertIn("validate_token", found["symbols"])

    def test_plain_request_has_no_anchor(self):
        self.assertEqual(mentions("로그인이 안 돼요")["routes"], [])


class TestAnchorLanding(unittest.TestCase):
    def setUp(self):
        self.g = _graph()

    def test_route_anchor_reaches_the_feature_files(self):
        """화면 주소 하나로 시작해 저장소를 건너 그 화면의 부품까지 닿아야 한다."""
        anchors = find_anchors(self.g, "/ontology 뒤로가기 버튼")
        self.assertEqual([a["name"] for a in anchors], ["/ontology"])
        scope = {n["id"] for n in expand(self.g, anchors)}
        self.assertIn("feat:features/onto/src/back-nav.tsx", scope)
        self.assertIn("feat:features/onto/src/table.tsx", scope)

    def test_scope_excludes_unrelated_repo(self):
        """이름이 비슷하다는 이유로 관계없는 저장소가 끌려오면 안 된다."""
        scope = {n["id"] for n in expand(self.g, find_anchors(self.g, "/ontology"))}
        self.assertNotIn("other:t.py#test_back_button", scope)

    def test_ranking_inside_scope_uses_translated_words(self):
        """범위가 정해진 뒤에는 단어 맞추기가 안전하다 — 밖으로 샐 수 없다."""
        scope = expand(self.g, find_anchors(self.g, "/ontology 뒤로가기 버튼"))
        top = rank_within(scope, "/ontology 뒤로가기 버튼", "back nav button", k=1)[0]
        self.assertEqual(top["name"], "back-nav.tsx")

    def test_no_anchor_returns_nothing(self):
        """지목이 없으면 앵커도 없다 — 그때는 검색이 할 일이다."""
        self.assertEqual(find_anchors(self.g, "버튼이 이상해요"), [])
        self.assertEqual(expand(self.g, []), [])
