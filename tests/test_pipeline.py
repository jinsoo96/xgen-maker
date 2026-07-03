import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from xgen_maker.config import MakerConfig
from xgen_maker.kg.build import build_repo
from xgen_maker.loop.pipeline import MakerLoop

APP_SOURCE = '''def greet(name):
    return "hi " + name


def farewell(name):
    return "bye " + name
'''

STUB_AGENT = '''import pathlib
path = pathlib.Path("app.py")
source = path.read_text(encoding="utf-8")
source = source.replace('return "hi " + name', 'return "hi, " + str(name)')
path.write_text(source, encoding="utf-8")
print("stub agent: app.py patched")
'''


def init_repo(root: Path) -> None:
    for args in (["init", "-b", "trunk"],
                 ["config", "user.email", "maker@test.local"],
                 ["config", "user.name", "maker-test"]):
        subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)
    (root / "app.py").write_text(APP_SOURCE, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, capture_output=True, check=True)


class TestPipelineE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.repo_root = self.base / "demo"
        self.repo_root.mkdir()
        init_repo(self.repo_root)

        graph = build_repo("demo", self.repo_root)
        self.kg_path = self.base / "kg.json"
        graph.save(self.kg_path)

        stub = self.base / "stub_agent.py"
        stub.write_text(STUB_AGENT, encoding="utf-8")
        self.config = MakerConfig(
            repos={"demo": str(self.repo_root)},
            kg_path=str(self.kg_path),
            mode="observe",
            allow_write=True,
            llm_enabled=False,
            verbose=False,
            agent_cmd=f'"{sys.executable}" "{stub}"',
            worklogs_dir=str(self.base / "worklogs"))

    def tearDown(self):
        self.tmp.cleanup()

    def _current_branch(self) -> str:
        result = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                                cwd=self.repo_root, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        return result.stdout.strip()

    def test_observe_full_loop(self):
        loop = MakerLoop(self.config)
        report = loop.run("greet 함수가 이름 처리에서 에러 나는 버그 고쳐줘")

        self.assertEqual(report["outcome"], "mr_prepared")
        self.assertTrue(report["branch"].startswith("fix/"))
        self.assertEqual(self._current_branch(), report["branch"])

        session = Path(report["session_dir"])
        self.assertTrue((session / "journal.jsonl").exists())
        self.assertTrue((session / "MR-DRAFT.md").exists())
        self.assertTrue((session / "SUMMARY.md").exists())
        draft = (session / "MR-DRAFT.md").read_text(encoding="utf-8")
        self.assertIn("## 무엇", draft)
        self.assertIn("app.py", draft)

        steps = [json.loads(line)["step"] for line in
                 (session / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
        for expected in ("session_start", "intent", "kg_search", "branch",
                         "implement", "judge", "commit", "mr_ready", "kg_refresh",
                         "session_end"):
            self.assertIn(expected, steps)

        log = subprocess.run(["git", "log", "--oneline"], cwd=self.repo_root,
                             capture_output=True, text=True,
                             encoding="utf-8", errors="replace").stdout
        self.assertIn("fix:", log)

    def test_plan_only_when_write_disabled(self):
        self.config.allow_write = False
        loop = MakerLoop(self.config)
        report = loop.run("greet 함수 버그 고쳐줘")
        self.assertEqual(report["outcome"], "planned")
        self.assertTrue(Path(report["mr_draft"]).exists())
        self.assertEqual(self._current_branch(), "trunk")  # 레포 미접촉

    def test_question_answers_without_touching_repo(self):
        loop = MakerLoop(self.config)
        report = loop.run("farewell 함수 어디 있어?")
        self.assertEqual(report["outcome"], "answered")
        self.assertIn("farewell", report["answer"])
        self.assertEqual(self._current_branch(), "trunk")

    def test_broken_syntax_blocked_by_checks(self):
        stub_bad = self.base / "stub_bad.py"
        stub_bad.write_text(
            'import pathlib\n'
            'pathlib.Path("app.py").write_text("def greet(:\\n    return\\n", encoding="utf-8")\n'
            'print("stub: wrote broken syntax")\n', encoding="utf-8")
        self.config.agent_cmd = f'"{sys.executable}" "{stub_bad}"'
        loop = MakerLoop(self.config)
        report = loop.run("greet 함수 버그 고쳐줘")
        self.assertEqual(report["outcome"], "checks_failed")
        self.assertEqual(report["checks"]["py_syntax"], "failed")
        # MR 초안이 만들어지지 않아야 함 (게이트 차단)
        self.assertNotIn("mr_draft", report)

    def test_dirty_worktree_blocks_branch(self):
        (self.repo_root / "dirty.txt").write_text("x", encoding="utf-8")
        loop = MakerLoop(self.config)
        report = loop.run("greet 함수 버그 고쳐줘")
        self.assertEqual(report["outcome"], "branch_failed")
        self.assertIn("깨끗", report["error"])


if __name__ == "__main__":
    unittest.main()
