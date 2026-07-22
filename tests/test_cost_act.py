import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xgen_maker.config import MakerConfig
from xgen_maker.kg.build import build_repo
from xgen_maker.loop.cost import CostTracker
from xgen_maker.loop.learnings import record, _all
from xgen_maker.loop.pipeline import MakerLoop

APP = "def greet(name):\n    return 'hi ' + name\n"
STUB = ('import pathlib\n'
        'pathlib.Path("app.py").write_text("def greet(name):\\n    return \'hi, \'+str(name)\\n", encoding="utf-8")\n'
        'print("stub done")\n')


def init_repo(root: Path):
    for a in (["init", "-b", "develop"], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"]):
        subprocess.run(["git", *a], cwd=root, capture_output=True)
    (root / "app.py").write_text(APP, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
    subprocess.run(["git", "commit", "-m", "i"], cwd=root, capture_output=True)


class TestCostTracker(unittest.TestCase):
    def test_accumulate(self):
        c = CostTracker()
        c.add_agent("x" * 400, "y" * 200)
        c.add_llm(80, 40)
        s = c.summary()
        self.assertEqual(s["agent_calls"], 1)
        self.assertEqual(s["llm_calls"], 1)
        self.assertEqual(s["est_input_tokens"], 100 + 20)
        self.assertEqual(s["est_total_tokens"], 120 + 60)


class TestLearningsDedup(unittest.TestCase):
    def test_dedup_same_area_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            record(tmp, "r", "area/x", "pitfall", "같은  교훈 문장")
            record(tmp, "r", "area/x", "pitfall", "같은 교훈 문장")  # 공백만 다름 → 중복
            record(tmp, "r", "area/x", "fix", "다른 교훈")
            self.assertEqual(len(_all(tmp, "r")), 2)


class TestActMrRegression(unittest.TestCase):
    """act 경로가 push + MR 생성을 올바르게 호출하는지 (실네트워크 없이 mock)."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.repo = base / "demo"; self.repo.mkdir()
        init_repo(self.repo)
        graph = build_repo("demo", self.repo)
        kg = base / "kg.json"; graph.save(kg)
        stub = base / "stub.py"; stub.write_text(STUB, encoding="utf-8")
        self.config = MakerConfig(
            repos={"demo": str(self.repo)}, kg_path=str(kg), mode="act",
            allow_write=True, llm_enabled=False, verbose=False, fetch_latest=False,
            gitlab_projects={"demo": "grp/demo"},
            agent_cmd=f'"{sys.executable}" "{stub}"', worklogs_dir=str(base / "wl"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_act_calls_push_and_mr(self):
        pushes, mrs = [], []
        def fake_push(self_git, branch, **kw): pushes.append(branch)
        def fake_mr(cfg, repo, branch, title, body):
            mrs.append((repo, branch)); return {"ok": True, "url": "http://gl/mr/1"}
        ok_authz = lambda cfg, repo, **kw: {"ok": True, "user": "tester",
                                            "project": "grp/demo", "level": 40}
        with patch("xgen_maker.loop.git_ops.GitRepo.push", fake_push), \
             patch("xgen_maker.loop.authz.authorize", ok_authz), \
             patch("xgen_maker.loop.pipeline.create_gitlab_mr", fake_mr):
            report = MakerLoop(self.config).run("greet 함수 이름처리 버그 고쳐줘")
        self.assertEqual(report["outcome"], "mr_created")
        self.assertEqual(len(pushes), 1)
        self.assertTrue(pushes[0].startswith(("fix/", "feature/")))
        self.assertEqual(mrs[0][0], "demo")
        self.assertEqual(report["mr"]["url"], "http://gl/mr/1")
        self.assertIn("cost", report)  # 비용 집계도 확인
        self.assertGreaterEqual(report["cost"]["agent_calls"], 1)


if __name__ == "__main__":
    unittest.main()
