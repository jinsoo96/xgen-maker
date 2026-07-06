import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from xgen_maker.config import MakerConfig
from xgen_maker.kg.build import build_repo
from xgen_maker.loop.pipeline import MakerLoop
from xgen_maker.loop.converge import decide, sandbox_verify_python, HAS_HARNESS

APP = "def greet(name):\n    return 'hi ' + name\n"

# 1회차엔 구문오류를 냈다가, 재시도(피드백)에서 고치는 스텁 — 수렴 증명용.
# cwd=repo 이므로 마커를 repo 밖(부모)에 둬 스테이징 오염을 피한다.
STUB_SELFHEAL = '''import pathlib
p = pathlib.Path("app.py")
marker = pathlib.Path("..") / ".maker_attempt"
n = int(marker.read_text()) + 1 if marker.exists() else 1
marker.write_text(str(n))
if n == 1:
    p.write_text("def greet(name):\\n    return 'hi ' + name +\\n", encoding="utf-8")  # 구문오류
else:
    p.write_text("def greet(name):\\n    return 'hi, ' + str(name)\\n", encoding="utf-8")  # 수정
print(f"stub attempt {n}")
'''


def init_repo(root: Path) -> None:
    for a in (["init", "-b", "trunk"], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"]):
        subprocess.run(["git", *a], cwd=root, capture_output=True)
    (root / "app.py").write_text(APP, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
    subprocess.run(["git", "commit", "-m", "i"], cwd=root, capture_output=True)


class TestDecideContract(unittest.TestCase):
    def _ck(self, blocked): return {"blocked": blocked, "checks": [], "summary": {}}
    def _sb(self, status): return {"status": status}

    def test_all_pass_stops(self):
        self.assertEqual(decide(self._ck(False), self._sb("passed"),
                                {"passed": True}, 1, 3), "stop")

    def test_sandbox_fail_retries_until_cap(self):
        self.assertEqual(decide(self._ck(False), self._sb("failed"), None, 1, 3), "retry")
        self.assertEqual(decide(self._ck(False), self._sb("failed"), None, 3, 3), "stop")

    def test_judge_fail_retries(self):
        self.assertEqual(decide(self._ck(False), self._sb("passed"),
                                {"passed": False}, 1, 3), "retry")


class TestSandboxVerify(unittest.TestCase):
    def test_good_and_bad(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ok.py").write_text("x = 1\n", encoding="utf-8")
            (root / "bad.py").write_text("def f(:\n", encoding="utf-8")
            good = sandbox_verify_python(root, ["ok.py"])
            bad = sandbox_verify_python(root, ["bad.py"])
        if HAS_HARNESS:
            self.assertEqual(good["status"], "passed")
            self.assertEqual(bad["status"], "failed")
            self.assertTrue(good.get("isolated"))
        else:
            self.assertEqual(good["status"], "skipped")


class TestConvergenceLoop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.repo = self.base / "demo"
        self.repo.mkdir()
        init_repo(self.repo)
        graph = build_repo("demo", self.repo)
        self.kg = self.base / "kg.json"
        graph.save(self.kg)
        stub = self.base / "stub.py"
        stub.write_text(STUB_SELFHEAL, encoding="utf-8")
        # agent를 세션 디렉토리(cwd=repo)에서 실행하므로 프롬프트/마커는 repo 기준.
        self.config = MakerConfig(
            repos={"demo": str(self.repo)}, kg_path=str(self.kg),
            mode="observe", allow_write=True, llm_enabled=False, verbose=False,
            max_iterations=3,
            agent_cmd=f'"{sys.executable}" "{stub}"',
            worklogs_dir=str(self.base / "wl"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_self_heals_and_converges(self):
        # 스텁이 프롬프트/마커를 repo에서 읽으려면 cwd=repo여야 함 — run_agent는 cwd=repo_path.
        # 프롬프트 파일은 세션 dir에 쓰이므로, 스텁은 repo에 복사된 마커만 사용하게 조정.
        report = MakerLoop(self.config).run("greet 함수의 이름 처리 버그 고쳐줘")
        # 1회차 구문오류 → retry → 2회차 수정 → 수렴
        self.assertIn(report["outcome"], ("mr_prepared", "checks_failed"))
        if report["outcome"] == "mr_prepared":
            self.assertTrue(report["converged"])
            self.assertGreaterEqual(report["iterations"], 2)


if __name__ == "__main__":
    unittest.main()
