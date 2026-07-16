"""버그 검수에서 나온 보안·강건성 수정에 대한 회귀 테스트."""
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


class TestTokenRedaction(unittest.TestCase):
    def test_git_error_masks_token(self):
        from xgen_maker.loop.git_ops import GitRepo, GitOpsError, redact
        self.assertEqual(redact("git push https://oauth2:glpat-SECRET@h/p.git 실패"),
                         "git push https://oauth2:***@h/p.git 실패")
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            for a in (["init", "-b", "trunk"], ["config", "user.email", "a@b"],
                      ["config", "user.name", "x"],
                      ["remote", "add", "origin", "https://gitlab.invalid/g/p.git"]):
                subprocess.run(["git", *a], cwd=r, capture_output=True)
            (r / "f").write_text("x"); subprocess.run(["git", "add", "-A"], cwd=r, capture_output=True)
            subprocess.run(["git", "commit", "-m", "i"], cwd=r, capture_output=True)
            g = GitRepo(r); g.create_branch("fix/leak-demo")
            with self.assertRaises(GitOpsError) as ctx:
                g.push("fix/leak-demo", token="glpat-SUPERSECRET")
            self.assertNotIn("glpat-SUPERSECRET", str(ctx.exception))
            self.assertIn(":***@", str(ctx.exception))


class TestAuthzOriginExact(unittest.TestCase):
    def _cfg(self):
        from xgen_maker.config import MakerConfig
        c = MakerConfig(gitlab_url="https://gitlab.corp.internal",
                        gitlab_projects={"frontend": "team/frontend"})
        patch.object(type(c), "gitlab_token", property(lambda s: "tok")).start()
        return c

    def _run(self, origin):
        from xgen_maker.loop import authz
        with patch.object(authz, "_origin_url", return_value=origin), \
             patch.object(authz, "_api", side_effect=[{"id": 7, "username": "kim"},
                                                      {"id": 7, "access_level": 30}]):
            return authz.authorize(self._cfg(), "frontend", repo_path="/clone")

    def test_fork_rejected(self):
        self.assertFalse(self._run("https://gitlab.corp.internal/team/frontend-fork.git")["ok"])

    def test_exact_https_and_ssh_pass(self):
        self.assertTrue(self._run("https://oauth2:tok@gitlab.corp.internal/team/frontend.git")["ok"])
        self.assertTrue(self._run("git@gitlab.corp.internal:team/frontend.git")["ok"])

    def tearDown(self):
        patch.stopall()

    def test_origin_project_path(self):
        from xgen_maker.loop.authz import origin_project_path
        self.assertEqual(origin_project_path("https://u:p@h/g/r.git"), "g/r")
        self.assertEqual(origin_project_path("git@h:g/r.git"), "g/r")
        self.assertEqual(origin_project_path("ssh://h/g/r"), "g/r")
        self.assertNotEqual(origin_project_path("https://h/g/r-fork.git"), "g/r")


class TestLoopbackGuardClosed(unittest.TestCase):
    def test_empty_host_not_loopback(self):
        from xgen_maker.web import _LOOPBACK_HOSTS
        self.assertNotIn("", _LOOPBACK_HOSTS)  # "" → 0.0.0.0 바인드, loopback 아님


class TestAtomicGraphSave(unittest.TestCase):
    def test_concurrent_saves_never_corrupt(self):
        from xgen_maker.kg.graph import Graph
        with tempfile.TemporaryDirectory() as t:
            g = Graph()
            for i in range(200):
                g.add_node(f"r:f{i}.py", "file", f"f{i}.py", "r", f"f{i}.py")
            path = Path(t) / "kg.json"
            errors = []

            def read_valid():
                # Windows: 교체 순간엔 읽기가 잠깐 막힐 수 있어 짧게 재시도(손상 여부만 검증)
                import time
                for _ in range(20):
                    try:
                        return json.loads(path.read_text(encoding="utf-8"))
                    except (PermissionError, FileNotFoundError):
                        time.sleep(0.01)
                    # json.JSONDecodeError는 재시도 없이 실패 — 그게 곧 손상 증거
                return json.loads(path.read_text(encoding="utf-8"))

            def saver():
                try:
                    for _ in range(15):
                        g.save(path)
                        read_valid()  # 항상 완전한 JSON(부분 파일이면 JSONDecodeError)
                except json.JSONDecodeError as e:  # 손상만 실패로 집계
                    errors.append(e)
                except Exception:  # noqa: BLE001 — 그 외 전이적 OS 락은 무시
                    pass

            ts = [threading.Thread(target=saver) for _ in range(4)]
            [t.start() for t in ts]; [t.join() for t in ts]
            self.assertEqual(errors, [], f"동시 저장 중 손상: {errors[:2]}")
            self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))["nodes"]), 200)


class TestJournalCorruptTolerant(unittest.TestCase):
    def test_missing_keys_dont_crash(self):
        from xgen_maker.loop.history import read_sessions
        from xgen_maker.loop.rollback import last_action
        with tempfile.TemporaryDirectory() as t:
            sess = Path(t) / "2026-07-10-000000-x"; sess.mkdir()
            # 유효 JSON이지만 step/status 키가 빠진 라인(수기편집/구버전)
            (sess / "journal.jsonl").write_text(
                '{}\n{"step":"branch"}\n{"foo":1}\n', encoding="utf-8")
            # KeyError 없이 처리돼야 함
            self.assertEqual(read_sessions(Path(t)), read_sessions(Path(t)))  # 크래시 없음
            self.assertIsNone(last_action(Path(t)))  # 유효 branch ok 이벤트 없음


class TestCliFriendlyConfigError(unittest.TestCase):
    def test_missing_config_clean_error(self):
        from xgen_maker import cli
        with self.assertRaises(SystemExit):
            cli.main(["status", "--config", "/definitely/nope.json"])


if __name__ == "__main__":
    unittest.main()
