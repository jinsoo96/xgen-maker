import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from xgen_maker.kg.graph import Graph
from xgen_maker import chat


def make_kg(tmp: Path) -> Path:
    graph = Graph()
    graph.add_node("be", "repo", "be", "be")
    graph.add_node("be:pay.py", "file", "pay.py", "be", "pay.py")
    graph.add_node("be:pay.py#charge", "function", "charge", "be", "pay.py", 10)
    graph.add_edge("be", "be:pay.py", "contains")
    graph.add_edge("be:pay.py", "be:pay.py#charge", "contains")
    kg_path = tmp / "kg.json"
    graph.save(kg_path)
    return kg_path


def run_with_input(lines: list[str], config_path: str | None) -> str:
    feed = iter(lines)
    buffer = io.StringIO()
    with patch("builtins.input", lambda _="": next(feed)):
        with redirect_stdout(buffer):
            chat.run_chat(config_path)
    return buffer.getvalue()


class TestChatRepl(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        kg_path = make_kg(base)
        config_path = base / "cfg.json"
        config_path.write_text(
            f'{{"kg_path": "{kg_path.as_posix()}", "worklogs_dir": "{base.as_posix()}/wl", '
            f'"llm_enabled": false, "verbose": false}}', encoding="utf-8")
        self.config_path = str(config_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stats_and_repos(self):
        out = run_with_input(["/stats", "/repos", "/quit"], self.config_path)
        self.assertIn("노드", out)
        self.assertIn("be", out)

    def test_search(self):
        out = run_with_input(["/search charge", "/quit"], self.config_path)
        self.assertIn("charge", out)

    def test_mode_switch(self):
        out = run_with_input(["/mode observe", "/mode bogus", "/quit"], self.config_path)
        self.assertIn("모드 → observe", out)
        self.assertIn("사용:", out)

    def test_question_query_runs_loop(self):
        out = run_with_input(["charge 함수 어디 있어?", "/quit"], self.config_path)
        self.assertIn("answered", out)
        self.assertIn("charge", out)

    def test_unknown_command(self):
        out = run_with_input(["/nope", "/quit"], self.config_path)
        self.assertIn("알 수 없는 명령", out)


if __name__ == "__main__":
    unittest.main()
