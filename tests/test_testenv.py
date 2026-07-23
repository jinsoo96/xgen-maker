"""대상 저장소 테스트가 실제로 도는지 — 빠진 의존성을 깔아 가며.

"의존성 없어서 skip"이 결론이 아니라, 깔 수 있는 건 깔고 진짜 돌린다.
"""
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from xgen_maker.loop.testenv import run_pytest_with_deps, _next_installable


def _importable(mod: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(mod) is not None


def _make_repo(root: Path, test_body: str) -> None:
    (root / "tests").mkdir(parents=True)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)
    (root / "tests" / "test_x.py").write_text(textwrap.dedent(test_body), encoding="utf-8")


class TestRunsRealTests(unittest.TestCase):
    def test_passing_suite_reports_passed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "r"
            _make_repo(root, """
                def test_math():
                    assert 1 + 1 == 2
                def test_str():
                    assert "a".upper() == "A"
            """)
            r = run_pytest_with_deps("r", root, ["mod.py"], timeout=120)
            self.assertEqual(r["status"], "passed")
            self.assertEqual(r["passed"], 2)

    def test_failing_suite_reports_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "r"
            _make_repo(root, """
                def test_broken():
                    assert 1 == 2
            """)
            r = run_pytest_with_deps("r", root, ["mod.py"], timeout=120)
            self.assertEqual(r["status"], "failed")
            self.assertEqual(r["failed"], 1)

    def test_no_py_change_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "r"
            _make_repo(root, "def test_x():\n    assert True\n")
            r = run_pytest_with_deps("r", root, ["style.css"], timeout=60)
            self.assertEqual(r["status"], "skipped")
            self.assertIn("py 변경 없음", r["reason"])

    def test_missing_dep_is_detected_for_install(self):
        """수집 실패 로그에서 깔 수 있는 PyPI 모듈을 골라낸다(사설/네임스페이스는 제외)."""
        out = "ModuleNotFoundError: No module named 'psutil'"
        self.assertEqual(_next_installable(out, set()), "psutil")
        # 사설/앱 네임스페이스는 자동설치 대상이 아니다
        self.assertIsNone(_next_installable(
            "No module named 'xgen_sdk'", set()))
        self.assertIsNone(_next_installable(
            "No module named 'controller.helper'", set()))


class TestAutoInstallsMissingDep(unittest.TestCase):
    """빠진 PyPI 의존성을 깔고 다시 돌려 실제로 통과시킨다."""

    def test_installs_and_passes(self):
        # 표준 라이브러리에 없고 PyPI에 있는 가벼운 패키지로 검증
        probe = "wrapt"                       # 작고 순수, 의존성 없음
        if _importable(probe):
            self.skipTest(f"{probe}가 현재 env에 이미 있어 자동설치 경로를 못 본다")
        # 캐시가 남아 있으면 '설치했다'가 아니라 '이미 있다'가 되므로 비우고 시작한다
        import shutil
        from xgen_maker.loop.testenv import _cache_dir
        shutil.rmtree(_cache_dir("testenv-probe"), ignore_errors=True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "r"
            _make_repo(root, f"""
                import {probe}
                def test_uses_dep():
                    assert {probe} is not None
            """)
            r = run_pytest_with_deps("testenv-probe", root, ["mod.py"], timeout=180)
            # 핵심: 빠진 의존성을 스스로 깔아 테스트를 통과시켰다
            self.assertEqual(r["status"], "passed", r.get("reason") or r.get("output"))
            self.assertIn(probe, r["installed"])


class TestRustCheck(unittest.TestCase):
    """Rust는 cargo를 스스로 찾아 돌린다 — 부른 셸 PATH에 없어도."""

    def test_no_rs_change_skips(self):
        from xgen_maker.loop.testing import check_rust_tests
        with tempfile.TemporaryDirectory() as tmp:
            r = check_rust_tests(Path(tmp), ["a.py"], timeout=30)
            self.assertEqual(r["status"], "skipped")
            self.assertIn("rust 변경 없음", r["reason"])

    def test_no_cargo_toml_skips(self):
        from xgen_maker.loop.testing import check_rust_tests
        with tempfile.TemporaryDirectory() as tmp:
            r = check_rust_tests(Path(tmp), ["src/x.rs"], timeout=30)
            self.assertEqual(r["status"], "skipped")
            self.assertIn("Cargo.toml", r["reason"])
