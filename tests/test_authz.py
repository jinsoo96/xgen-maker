import unittest
from unittest.mock import patch

from xgen_maker.config import MakerConfig
from xgen_maker.loop import authz


class TestAuthorize(unittest.TestCase):
    def _cfg(self, **kw):
        token = kw.pop("_token", "tok")
        base = dict(gitlab_url="https://gitlab.corp.internal",
                    gitlab_projects={"frontend": "grp/frontend"})
        base.update(kw)
        cfg = MakerConfig(**base)
        # 토큰 프로퍼티를 고정(로그인 저장 무시)
        patch.object(type(cfg), "gitlab_token",
                     property(lambda s: token)).start()
        return cfg

    def test_no_token_denied(self):
        cfg = self._cfg(_token="")
        r = authz.authorize(cfg, "frontend")
        self.assertFalse(r["ok"])
        self.assertIn("토큰", r["reason"])

    def test_placeholder_url_denied(self):
        cfg = self._cfg(gitlab_url="https://gitlab.example.com")
        r = authz.authorize(cfg, "frontend")
        self.assertFalse(r["ok"])
        self.assertIn("예시", r["reason"])

    def test_unmapped_repo_denied(self):
        cfg = self._cfg()
        r = authz.authorize(cfg, "unknown-repo")
        self.assertFalse(r["ok"])
        self.assertIn("매핑 없음", r["reason"])

    def test_member_developer_allowed(self):
        cfg = self._cfg()
        with patch.object(authz, "_api", side_effect=[
                {"id": 7, "username": "kim"},          # /user
                {"id": 7, "access_level": 30}]):        # members/all
            r = authz.authorize(cfg, "frontend")
        self.assertTrue(r["ok"])
        self.assertEqual(r["user"], "kim")
        self.assertEqual(r["level"], 30)

    def test_non_member_denied(self):
        import urllib.error
        cfg = self._cfg()
        with patch.object(authz, "_api", side_effect=[
                {"id": 9, "username": "outsider"},
                urllib.error.HTTPError("u", 404, "nf", {}, None)]):
            r = authz.authorize(cfg, "frontend")
        self.assertFalse(r["ok"])
        self.assertIn("멤버 아님", r["reason"])

    def test_insufficient_level_denied(self):
        cfg = self._cfg()
        with patch.object(authz, "_api", side_effect=[
                {"id": 5, "username": "guest"},
                {"id": 5, "access_level": 10}]):        # Guest < Developer
            r = authz.authorize(cfg, "frontend")
        self.assertFalse(r["ok"])
        self.assertIn("접근레벨", r["reason"])

    def test_invalid_token_denied(self):
        import urllib.error
        cfg = self._cfg()
        with patch.object(authz, "_api",
                          side_effect=urllib.error.HTTPError("u", 401, "no", {}, None)):
            r = authz.authorize(cfg, "frontend")
        self.assertFalse(r["ok"])
        self.assertIn("토큰 무효", r["reason"])

    def tearDown(self):
        patch.stopall()


class TestPipelineActGate(unittest.TestCase):
    def test_act_denied_stops_before_work(self):
        # act 모드 + 미인가 → outcome=unauthorized, 작업 미진행
        from xgen_maker.loop.pipeline import MakerLoop
        from xgen_maker.kg.graph import Graph
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            g = Graph()
            g.add_node("frontend:app.charge", "function", "charge",
                       repo="frontend", meta={"file": "app.py"})
            kg = Path(tmp) / "kg.json"
            g.save(kg)
            cfg = MakerConfig(
                repos={"frontend": tmp}, kg_path=str(kg),
                mode="act", allow_write=True, llm_enabled=False, verbose=False,
                gitlab_url="https://gitlab.example.com",  # placeholder → 거부
                gitlab_projects={"frontend": "grp/frontend"},
                worklogs_dir=str(Path(tmp) / "wl"))
            report = MakerLoop(cfg).run("charge 함수 버그 고쳐줘")
        self.assertEqual(report["outcome"], "unauthorized")
        self.assertFalse(report["authorize"]["ok"])


if __name__ == "__main__":
    unittest.main()
