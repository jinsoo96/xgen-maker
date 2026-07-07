"""고결한 규칙 회귀 증명 — 변경이 레거시(기존 테스트)를 개박살내면 차단·되먹임.

check_pytest가 변경 파일만이 아니라 레포 전체 스위트를 돌리므로, MAKER의 변경이
기존 동작을 깨면 pytest가 실패→blocked→수렴 루프가 retry(통과까지)한다.
"""
import subprocess
import tempfile
import unittest
from pathlib import Path

from xgen_maker.loop.testing import run_checks, check_pytest
from xgen_maker.loop.converge import _feedback, decide


LEGACY_APP = "def greet(name):\n    return 'hi, ' + name\n"
LEGACY_TEST = ("from app import greet\n"
               "def test_greet():\n    assert greet('kim') == 'hi, kim'\n")


class TestLegacyRegressionGate(unittest.TestCase):
    def _repo(self, tmp: Path) -> Path:
        (tmp / "app.py").write_text(LEGACY_APP, encoding="utf-8")
        (tmp / "tests").mkdir()
        (tmp / "tests" / "test_app.py").write_text(LEGACY_TEST, encoding="utf-8")
        return tmp

    def test_healthy_change_not_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            repo = self._repo(Path(t))
            # 레거시를 안 깨는 변경(주석 추가)
            (repo / "app.py").write_text(LEGACY_APP + "# safe\n", encoding="utf-8")
            r = run_checks(repo, ["app.py"])
            if r["summary"]["pytest"] == "skipped":
                self.skipTest("pytest 환경 없음")
            self.assertEqual(r["summary"]["pytest"], "passed")
            self.assertFalse(r["blocked"])

    def test_legacy_break_is_blocked_and_fed_back(self):
        with tempfile.TemporaryDirectory() as t:
            repo = self._repo(Path(t))
            # 레거시를 개박살내는 변경 — 기존 test_greet가 깨짐
            (repo / "app.py").write_text(
                "def greet(name):\n    return 'yo ' + name\n", encoding="utf-8")
            r = run_checks(repo, ["app.py"])
            if r["summary"]["pytest"] == "skipped":
                self.skipTest("pytest 환경 없음")
            # 레거시 회귀 → pytest 실패 → 차단
            self.assertEqual(r["summary"]["pytest"], "failed")
            self.assertTrue(r["blocked"])
            # 수렴 계약: 차단이면 retry(통과까지), 마지막 회차면 stop
            self.assertEqual(decide(r, {"status": "skipped"}, None, 1, 3), "retry")
            self.assertEqual(decide(r, {"status": "skipped"}, None, 3, 3), "stop")
            # 실패 상세가 다음 시도로 되먹여짐(agent가 회귀를 보고 고칠 수 있게)
            fb = _feedback(r, {"status": "skipped"}, None)
            self.assertIn("pytest 실패", fb)

    def test_pytest_runs_full_suite_not_just_changed(self):
        # 변경 파일에 test가 없어도, 레포 전체 스위트를 돌려 레거시 회귀를 잡는다
        with tempfile.TemporaryDirectory() as t:
            repo = self._repo(Path(t))
            # app.py를 깨고, 변경 목록엔 무관한 파일만 올려도 전체 스위트가 실패를 잡음
            (repo / "app.py").write_text(
                "def greet(name):\n    return 'broken'\n", encoding="utf-8")
            (repo / "other.py").write_text("x = 1\n", encoding="utf-8")
            r = check_pytest(repo, ["other.py"])  # 변경 목록엔 other.py만
            if r["status"] == "skipped":
                self.skipTest("pytest 환경 없음")
            self.assertEqual(r["status"], "failed")  # 그래도 레거시 깨짐을 탐지


if __name__ == "__main__":
    unittest.main()
