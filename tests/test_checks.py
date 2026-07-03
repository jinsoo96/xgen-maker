import tempfile
import unittest
from pathlib import Path

from xgen_maker.loop.testing import (run_checks, check_python_syntax, check_pytest,
                                     check_node_tests)


class TestChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_syntax_pass(self):
        (self.root / "ok.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        result = check_python_syntax(self.root, ["ok.py"])
        self.assertEqual(result["status"], "passed")

    def test_syntax_fail_blocks(self):
        (self.root / "bad.py").write_text("def f(:\n    return\n", encoding="utf-8")
        result = run_checks(self.root, ["bad.py"])
        self.assertTrue(result["blocked"])
        self.assertEqual(result["summary"]["py_syntax"], "failed")

    def test_pytest_skipped_without_config(self):
        (self.root / "a.py").write_text("x = 1\n", encoding="utf-8")
        result = check_pytest(self.root, ["a.py"])
        self.assertEqual(result["status"], "skipped")
        self.assertIn("구성 없음", result["reason"])

    def test_pytest_runs_and_fails(self):
        (self.root / "calc.py").write_text("def add(a, b):\n    return a - b\n",
                                           encoding="utf-8")
        tests_dir = self.root / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_calc.py").write_text(
            "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent.parent))\n"
            "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
            encoding="utf-8")
        result = check_pytest(self.root, ["calc.py"])
        self.assertEqual(result["status"], "failed")

    def test_pytest_runs_and_passes(self):
        (self.root / "calc.py").write_text("def add(a, b):\n    return a + b\n",
                                           encoding="utf-8")
        tests_dir = self.root / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_calc.py").write_text(
            "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent.parent))\n"
            "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
            encoding="utf-8")
        result = run_checks(self.root, ["calc.py"])
        self.assertFalse(result["blocked"])
        self.assertEqual(result["summary"]["pytest"], "passed")

    def test_node_skipped_without_node_modules(self):
        (self.root / "a.ts").write_text("export const x = 1\n", encoding="utf-8")
        result = check_node_tests(self.root, ["a.ts"])
        self.assertEqual(result["status"], "skipped")
        self.assertIn("node_modules", result["reason"])


if __name__ == "__main__":
    unittest.main()
