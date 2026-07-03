"""코드 카탈로그 완결성 — geny-executor test_event_catalog 패턴 차용.

pipeline이 journal.event(EVENT, ...)에 넘기는 step 리터럴은 전부 Event enum에 있어야 한다
(uncatalogued emit 차단). outcome 리터럴은 전부 Outcome enum에 있어야 한다.
"""
import re
import unittest
from pathlib import Path

from xgen_maker.codes import Event, Outcome, ErrorCode, ALL_EVENTS, ALL_OUTCOMES

PIPELINE = Path(__file__).parent.parent / "xgen_maker" / "loop" / "pipeline.py"


class TestCatalogCompleteness(unittest.TestCase):
    def setUp(self):
        self.source = PIPELINE.read_text(encoding="utf-8")

    def test_all_journal_steps_catalogued(self):
        # journal.event("step", ...) 및 self.event("step", ...) 의 첫 인자 리터럴 수집
        steps = set(re.findall(r'\.event\(\s*"([a-z_]+)"', self.source))
        uncatalogued = steps - ALL_EVENTS
        self.assertFalse(uncatalogued,
                         f"카탈로그 미등록 이벤트: {uncatalogued} — codes.Event에 추가 필요")

    def test_all_outcomes_catalogued(self):
        outcomes = set(re.findall(r'"outcome":\s*"([a-z_]+)"', self.source))
        uncatalogued = outcomes - ALL_OUTCOMES
        self.assertFalse(uncatalogued,
                         f"카탈로그 미등록 outcome: {uncatalogued} — codes.Outcome에 추가 필요")

    def test_enum_values_are_wire_strings(self):
        # 리네임 방지 — 값 고정 회귀
        self.assertEqual(Event.KG_SEARCH.value, "kg_search")
        self.assertEqual(Outcome.MR_PREPARED.value, "mr_prepared")
        self.assertEqual(ErrorCode.GIT_DIRTY.value, "maker.git.dirty_worktree")

    def test_error_codes_are_namespaced(self):
        for code in ErrorCode:
            self.assertTrue(code.value.startswith("maker."),
                            f"{code} — maker.<component>.<reason> 형식 위반")
            self.assertEqual(code.value.count("."), 2, f"{code} 형식 위반")


if __name__ == "__main__":
    unittest.main()
