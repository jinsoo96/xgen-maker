import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xgen_maker.config import MakerConfig
from xgen_maker.loop import gitlab_observe as GO
from xgen_maker.loop.history import read_sessions


class TestGitlabObserve(unittest.TestCase):
    def setUp(self):
        self.cfg = MakerConfig(gitlab_projects={"svc-frontend": "grp/frontend"})

    def test_branches_needs_mapping(self):
        r = GO.branches(self.cfg, "unmapped-repo")
        self.assertIn("error", r)

    def test_branches_parse(self):
        fake = [
            {"name": "develop", "protected": True, "commit": {}},
            {"name": "fix/task-1", "merged": False,
             "commit": {"author_name": "Alice Kim", "committed_date": "2026-07-06T00:00:00"}},
            {"name": "feature/x", "merged": True,
             "commit": {"author_name": "Bob Son", "committed_date": "2026-07-01T00:00:00"}},
        ]
        with patch.object(GO, "_api", return_value=fake):
            r = GO.branches(self.cfg, "svc-frontend")
        self.assertEqual(r["release"], ["develop"])
        self.assertEqual(r["protected"], ["develop"])
        self.assertEqual(len(r["work_recent"]), 2)
        self.assertEqual(r["work_recent"][0]["name"], "fix/task-1")  # 최신순

    def test_my_mrs_parse(self):
        fake = [{"iid": 42, "state": "opened", "title": "fix: x",
                 "source_branch": "fix/some-feature-x",
                 "target_branch": "develop", "web_url": "http://gl/mr/42",
                 "updated_at": "2026-07-06T00:00:00", "references": {"full": "grp/frontend!42"}}]
        with patch.object(GO, "_api", return_value=fake):
            mrs = GO.my_mrs(self.cfg)
            maker = GO.maker_mrs(self.cfg)
        self.assertEqual(mrs[0]["iid"], 42)
        self.assertEqual(maker[0]["source"], "fix/some-feature-x")

    def test_no_token_returns_empty(self):
        cfg = MakerConfig(gitlab_projects={})
        with patch.object(type(cfg), "gitlab_token", property(lambda s: "")):
            self.assertEqual(GO.my_mrs(cfg), [])


class TestHistory(unittest.TestCase):
    def test_read_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess = root / "2026-07-06-000000-demo"
            sess.mkdir()
            events = [
                {"step": "session_start", "status": "ok", "query": "버그 고쳐줘"},
                {"step": "branch", "status": "ok", "branch": "fix/demo"},
                {"step": "release", "status": "ok", "env": "dev"},
                {"step": "mr_create", "status": "ok", "url": "http://gl/mr/1"},
                {"step": "session_end", "status": "mr_prepared"},
            ]
            (sess / "journal.jsonl").write_text(
                "\n".join(json.dumps(e, ensure_ascii=False) for e in events), encoding="utf-8")
            sessions = read_sessions(root)
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s["query"], "버그 고쳐줘")
        self.assertEqual(s["outcome"], "mr_prepared")
        self.assertEqual(s["branch"], "fix/demo")
        self.assertEqual(s["env"], "dev")
        self.assertEqual(s["mr"], "http://gl/mr/1")

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(read_sessions(Path(tmp) / "nope"), [])


if __name__ == "__main__":
    unittest.main()
