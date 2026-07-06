import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from xgen_maker.config import MakerConfig
from xgen_maker.loop.git_ops import GitRepo
from xgen_maker.loop.rollback import last_action, undo


def _repo(root: Path):
    for a in (["init", "-b", "develop"], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"]):
        subprocess.run(["git", *a], cwd=root, capture_output=True)
    (root / "a.py").write_text("x=1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
    subprocess.run(["git", "commit", "-m", "i"], cwd=root, capture_output=True)


class TestRollback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.repo = self.base / "r"; self.repo.mkdir()
        _repo(self.repo)
        wl = self.base / "wl" / "2026-07-06-000000-demo"; wl.mkdir(parents=True)
        evs = [{"step": "branch", "status": "ok", "branch": "fix/rollback-test-branch",
                "base": "develop", "repo": "r"},
               {"step": "commit", "status": "ok"},
               {"step": "session_end", "status": "mr_prepared", "repo": "r"}]
        (wl / "journal.jsonl").write_text("\n".join(json.dumps(e) for e in evs), encoding="utf-8")
        self.cfg = MakerConfig(repos={"r": str(self.repo)},
                               worklogs_dir=str(self.base / "wl"), target_branch="develop")

    def tearDown(self):
        self.tmp.cleanup()

    def test_last_action_detected(self):
        act = last_action(self.cfg.worklogs_dir)
        self.assertEqual(act["branch"], "fix/rollback-test-branch")
        self.assertEqual(act["repo"], "r")
        self.assertFalse(act["pushed"])

    def test_undo_deletes_local_branch(self):
        g = GitRepo(self.repo)
        g.create_branch("fix/rollback-test-branch")
        (self.repo / "a.py").write_text("x=2\n", encoding="utf-8")
        g.commit_all("t", "b")
        self.assertEqual(g.current_branch(), "fix/rollback-test-branch")
        res = undo(self.cfg, last_action(self.cfg.worklogs_dir))
        self.assertTrue(res["ok"])
        self.assertEqual(g.current_branch(), "develop")
        self.assertNotIn("fix/rollback-test-branch", g._run("branch"))

    def test_no_action_when_no_branch(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertIsNone(last_action(t))


class TestWorktree(unittest.TestCase):
    def test_worktree_add_remove(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "r"; root.mkdir()
            _repo(root)
            g = GitRepo(root)
            wt = Path(tmp) / "wt"
            wg = g.add_worktree(wt, "feature/isolated-work", "HEAD")
            self.assertTrue((wt / "a.py").exists())
            self.assertEqual(wg.current_branch(), "feature/isolated-work")
            g.remove_worktree(wt)
            self.assertFalse(wt.exists())


if __name__ == "__main__":
    unittest.main()
