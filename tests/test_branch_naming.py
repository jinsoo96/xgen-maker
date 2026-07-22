"""브랜치 이름 — 읽으면 무슨 작업인지 알아야 한다."""
import unittest

from xgen_maker.config import suggest_branch, branch_name_issue


class TestBranchSlug(unittest.TestCase):
    def test_keywords_are_not_just_concatenated(self):
        """회귀: 키워드를 그대로 이어붙여 중복되고 장황한 이름이 나왔다.

        "back button ontology page legacy style BackButton" →
        fix/back-button-ontology-page-legacy-style-backbutton
        """
        name = suggest_branch("fix/", "back button ontology page legacy style BackButton".split())
        self.assertNotIn("backbutton", name)          # 붙여 쓴 중복은 버린다
        self.assertLessEqual(name.count("-"), 4)      # 낱말 몇 개로 끝난다
        self.assertNotIn("button-button", name)

    def test_duplicate_words_removed(self):
        name = suggest_branch("fix/", ["health", "health_check", "check", "health"])
        parts = name.split("/")[1].split("-")
        self.assertEqual(len(parts), len(set(parts)))

    def test_summary_slug_is_used_as_is(self):
        """작업을 한 마디로 요약한 슬러그는 그대로 살린다."""
        self.assertEqual(suggest_branch("fix/", ["ontology-back-button-style"]),
                         "fix/ontology-back-button-style")

    def test_meaningless_tokens_dropped(self):
        """팀 규칙: js·251205 같은 의미 없는 이름 금지."""
        name = suggest_branch("fix/", ["js", "251205", "login", "redirect"])
        self.assertNotIn("js", name.split("/")[1].split("-"))
        self.assertNotIn("251205", name)
        self.assertIsNone(branch_name_issue(name))

    def test_never_empty(self):
        name = suggest_branch("fix/", ["", "!!!", "1"])
        self.assertIsNotNone(name.split("/")[1])
        self.assertTrue(name.startswith("fix/"))
