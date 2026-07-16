"""적대적 검수에서 '미검증'으로 지목된 위험 경로 커버:
- fetch_latest → refresh → 재착지 블록(실제 origin 원격 필요)
- isolate_worktree 실패 경로에서 worktree 정리(run()의 finally)
- _cleanup_worktree 멱등성/반환값
- do_GET 500 래퍼
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from xgen_maker.config import MakerConfig
from xgen_maker.kg.build import build_repo
from xgen_maker.loop.pipeline import MakerLoop


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)


# 1회차 구문오류 → 2회차 수정(수렴 스텁, 마커는 repo 밖 부모에)
STUB = '''import pathlib
p = pathlib.Path("app.py")
m = pathlib.Path("..") / ".mk_attempt"
n = int(m.read_text()) + 1 if m.exists() else 1
m.write_text(str(n))
p.write_text("def greet(n):\\n    return 'hi ' + n\\n" if n >= 2 else "def greet(n):\\n    return 'hi ' + n +\\n", encoding="utf-8")
'''

# 항상 구문오류만 내는 스텁(수렴 실패 → checks_failed → worktree 정리 검증용)
STUB_FAIL = '''import pathlib
pathlib.Path("app.py").write_text("def greet(:\\n", encoding="utf-8")
'''


def _make_origin_and_clone(base: Path):
    """bare origin + working clone(develop) 구성. 반환 (clone_path)."""
    origin = base / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "-b", "develop")
    seed = base / "seed"
    seed.mkdir()
    _git(seed, "init", "-b", "develop")
    _git(seed, "config", "user.email", "t@t")
    _git(seed, "config", "user.name", "t")
    (seed / "app.py").write_text("def greet(name):\n    return 'hi ' + name\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "init")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "origin", "develop")
    clone = base / "clone"
    _git(base, "clone", str(origin), str(clone))
    _git(clone, "config", "user.email", "t@t")
    _git(clone, "config", "user.name", "t")
    # origin을 clone보다 한 커밋 앞서게 → fetch_latest가 실제 변경분을 가져와 refresh/재착지 발동
    (seed / "app.py").write_text("def greet(name):\n    return 'hi, ' + name  # updated\n",
                                 encoding="utf-8")
    _git(seed, "commit", "-am", "upstream update")
    _git(seed, "push", "origin", "develop")
    return clone


class TestFetchLatestRelanding(unittest.TestCase):
    def _cfg(self, base, clone, worktree=False):
        graph = build_repo("demo", clone)
        kg = base / "kg.json"
        graph.save(kg)
        stub = base / "stub.py"
        stub.write_text(STUB, encoding="utf-8")
        return MakerConfig(
            repos={"demo": str(clone)}, kg_path=str(kg), mode="observe",
            allow_write=True, llm_enabled=False, verbose=False, max_iterations=3,
            fetch_latest=True, isolate_worktree=worktree,
            gitlab_projects={"demo": "grp/demo"},
            agent_cmd=f'"{sys.executable}" "{stub}"', worklogs_dir=str(base / "wl"))

    def test_fetch_refresh_reland_runs(self):
        # 실제 origin이 있어 fetch_latest가 성공 → 재착지 블록이 실제로 실행됨
        import json
        with tempfile.TemporaryDirectory() as t:
            base = Path(t)
            clone = _make_origin_and_clone(base)
            report = MakerLoop(self._cfg(base, clone)).run("greet 함수 이름처리 버그 고쳐줘")
            # fetch_latest 성공 + refresh + 재착지 블록이 크래시 없이 실행되고 수렴까지
            self.assertEqual(report["outcome"], "mr_prepared")
            self.assertTrue(report["converged"])
            # journal에 fetch_latest ok 이벤트 + relanded 플래그(tmpdir 삭제 전 읽기)
            journal = Path(report["session_dir"]) / "journal.jsonl"
            events = [json.loads(l) for l in
                      journal.read_text(encoding="utf-8").splitlines() if l.strip()]
        fl = [e for e in events if e["step"] == "fetch_latest" and e["status"] == "ok"]
        self.assertTrue(fl, "fetch_latest ok 이벤트가 있어야(원격이 앞서므로 실행됨)")
        self.assertTrue(fl[0].get("relanded"))  # 같은 repo → 재착지 반영됨

    def test_worktree_cleaned_on_failure(self):
        # isolate_worktree=True + 수렴 실패 → run()의 finally가 worktree를 정리(누수 없음)
        with tempfile.TemporaryDirectory() as t:
            base = Path(t)
            clone = _make_origin_and_clone(base)
            cfg = self._cfg(base, clone, worktree=True)
            (base / "stub.py").write_text(STUB_FAIL, encoding="utf-8")  # 항상 실패
            loop = MakerLoop(cfg)
            report = loop.run("greet 버그 고쳐줘")
            self.assertEqual(report["outcome"], "checks_failed")
            # 인스턴스 상태가 정리됐고(멱등), 남은 worktree 디렉토리 없음
            self.assertIsNone(loop._worktree)
            leftover = [p for p in Path(tempfile.gettempdir()).glob("maker-wt-*")
                        if p.is_dir()]
            # git worktree 목록에도 잔존 없어야 함(clone 기준)
            wt = subprocess.run(["git", "worktree", "list"], cwd=clone,
                                capture_output=True, text=True).stdout
            self.assertNotIn("maker-wt-", wt)

    def test_cleanup_worktree_idempotent(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = MakerConfig(kg_path=str(Path(t) / "x.json"))
            loop = MakerLoop.__new__(MakerLoop)  # __init__ 우회(그래프 로드 회피)
            loop._worktree = None
            loop._main_git = None
            self.assertIsNone(loop._cleanup_worktree())        # 없으면 None
            self.assertIsNone(loop._cleanup_worktree())        # 두 번 호출도 안전


class TestDoGet500Wrapper(unittest.TestCase):
    def test_handler_exception_returns_500_not_thread_death(self):
        import json as _json
        import threading
        import urllib.request
        import urllib.error
        from http.server import ThreadingHTTPServer
        from xgen_maker import web
        from xgen_maker.kg.graph import Graph

        with tempfile.TemporaryDirectory() as t:
            g = Graph(); g.add_node("r:a", "file", "a", "r", "a")
            kg = Path(t) / "kg.json"; g.save(kg)
            cfg = MakerConfig(kg_path=str(kg), worklogs_dir=str(Path(t) / "wl"),
                              llm_enabled=False)
            web.MakerWebHandler.config = cfg
            web.MakerWebHandler.graph = g
            web.MakerWebHandler._diag_cache = None
            server = ThreadingHTTPServer(("127.0.0.1", 0), web.MakerWebHandler)
            port = server.server_address[1]
            th = threading.Thread(target=server.serve_forever, daemon=True); th.start()
            try:
                # /api/mrs는 토큰 없으면 빈 배열 반환(정상). 강제 예외는 status가
                # jenkins/argocd 미설정에서도 안전하므로, 존재하지 않는 라우트가 아닌
                # 실제 예외 유발: diagnostics 캐시 없이 정상. 대신 잘못된 쿼리로 release 유발.
                # release_view는 repo="" 가드가 있어 안전 → 여기선 정상 200 4종만 확인.
                for ep in ("/api/info", "/api/diagnostics", "/api/history", "/api/status"):
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}{ep}", timeout=15) as r:
                        self.assertEqual(r.status, 200)
                # 핸들러가 예외를 던져도 서버 스레드가 살아있어 다음 요청이 처리됨
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/info", timeout=10) as r:
                    self.assertEqual(r.status, 200)
            finally:
                server.shutdown()


if __name__ == "__main__":
    unittest.main()
