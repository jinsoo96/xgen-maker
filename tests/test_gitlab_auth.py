import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xgen_maker import auth
from xgen_maker.config import MakerConfig


class TestGitlabAuth(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake = Path(self.tmp.name) / "auth.json"
        os.environ.pop("XGEN_MAKER_GITLAB_TOKEN", None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_load_gitlab_token(self):
        with patch.object(auth, "AUTH_FILE", self.fake), \
             patch.object(auth, "AUTH_DIR", self.fake.parent):
            auth.save_auth(auth.Auth(gitlab_token="glpat-xyz", gitlab_user="me"))
            loaded = auth.load_auth()
        self.assertEqual(loaded.gitlab_token, "glpat-xyz")
        self.assertEqual(loaded.gitlab_user, "me")

    def test_resolve_prefers_env(self):
        os.environ["XGEN_MAKER_GITLAB_TOKEN"] = "env-token"
        try:
            self.assertEqual(auth.resolve_gitlab_token(), "env-token")
        finally:
            os.environ.pop("XGEN_MAKER_GITLAB_TOKEN", None)

    def test_config_gitlab_token_from_stored(self):
        with patch.object(auth, "AUTH_FILE", self.fake), \
             patch.object(auth, "AUTH_DIR", self.fake.parent):
            auth.save_auth(auth.Auth(gitlab_token="stored-tok"))
            cfg = MakerConfig()
            self.assertEqual(cfg.gitlab_token, "stored-tok")

    def test_verify_token_ok(self):
        import io, json as _json
        payload = io.BytesIO(_json.dumps({"username": "u", "id": 1}).encode())
        payload.__enter__ = lambda s=payload: s
        payload.__exit__ = lambda *a: None
        with patch("urllib.request.urlopen", return_value=payload):
            r = auth.gitlab_verify_token("https://gl", "tok")
        self.assertTrue(r["ok"])
        self.assertEqual(r["user"], "u")

    def test_password_grant_httperror_graceful(self):
        import urllib.error
        err = urllib.error.HTTPError("u", 400, "Bad", {}, None)
        err.read = lambda: b'{"error":"invalid_grant"}'
        with patch("urllib.request.urlopen", side_effect=err):
            r = auth.gitlab_login_password("https://gl", "me", "pw")
        self.assertFalse(r["ok"])
        self.assertIn("invalid_grant", r["reason"])

    def test_apply_to_env_sets_gitlab(self):
        os.environ.pop("XGEN_MAKER_GITLAB_TOKEN", None)
        auth.apply_to_env(auth.Auth(gitlab_token="gl-tok"))
        self.assertEqual(os.environ.get("XGEN_MAKER_GITLAB_TOKEN"), "gl-tok")
        os.environ.pop("XGEN_MAKER_GITLAB_TOKEN", None)


class TestPushAuthUrl(unittest.TestCase):
    def test_push_builds_authenticated_url(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for a in (["init", "-b", "trunk"], ["config", "user.email", "t@t"],
                      ["config", "user.name", "t"],
                      ["remote", "add", "origin", "https://gitlab.example.com/xgen2.0/demo.git"]):
                subprocess.run(["git", *a], cwd=root, capture_output=True)
            (root / "a.txt").write_text("x", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
            subprocess.run(["git", "commit", "-m", "i"], cwd=root, capture_output=True)
            from xgen_maker.loop.git_ops import GitRepo
            repo = GitRepo(root)
            repo.create_branch("fix/demo-push-url")
            calls = []
            orig = repo._run
            def spy(*args, **kw):
                calls.append(args)
                if "push" in args:
                    return ""  # 실제 push 안 함(네트워크 차단)
                return orig(*args, **kw)
            repo._run = spy
            repo.push("fix/demo-push-url", token="SECRET")
            push_calls = [c for c in calls if "push" in c]
            joined = " ".join(push_calls[0])
            self.assertIn("oauth2:SECRET@gitlab.example.com/xgen2.0/demo.git", joined)


if __name__ == "__main__":
    unittest.main()
