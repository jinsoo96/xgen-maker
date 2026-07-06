import tempfile
import unittest
from pathlib import Path

from xgen_maker.loop.learnings import record, retrieve, as_prompt_block, _all, area_of


class TestLearnings(unittest.TestCase):
    def test_record_retrieve_relevance(self):
        with tempfile.TemporaryDirectory() as tmp:
            record(tmp, "xgen-workflow", "harness/endpoints", "pitfall",
                   "harness endpoint 수정 시 locale_ko 확인")
            record(tmp, "xgen-workflow", "service/quality", "fix",
                   "품질평가는 stage2 라우팅 확인")
            got = retrieve(tmp, "xgen-workflow", ["harness", "endpoint", "locale"])
            self.assertTrue(got)
            self.assertIn("locale", got[0]["note"])
            block = as_prompt_block(got)
            self.assertIn("실수 반복 금지", block)

    def test_isolated_by_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            record(tmp, "repo-a", "x", "note", "a-learning")
            record(tmp, "repo-b", "y", "note", "b-learning")
            self.assertEqual(len(_all(tmp, "repo-a")), 1)
            self.assertEqual(len(_all(tmp, "repo-b")), 1)

    def test_empty_and_prompt_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(retrieve(tmp, "none", ["x"]), [])
        self.assertEqual(as_prompt_block([]), "")

    def test_area_of(self):
        landing = [{"path": "features/main-doc/src/x.tsx", "name": "X"}]
        self.assertEqual(area_of(landing), "features/main-doc/src")
        self.assertEqual(area_of([]), "?")


if __name__ == "__main__":
    unittest.main()
