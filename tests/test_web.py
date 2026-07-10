import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from xgen_maker.kg.graph import Graph
from xgen_maker.config import MakerConfig
from xgen_maker import web


def _make_kg(tmp: Path) -> Path:
    g = Graph()
    g.add_node("r:pay.py", "file", "pay.py", "r", "pay.py")
    g.add_node("r:pay.py#charge", "function", "charge", "r", "pay.py", 10)
    g.add_edge("r:pay.py", "r:pay.py#charge", "contains")
    kg = tmp / "kg.json"
    g.save(kg)
    return kg


class TestWebServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        kg = _make_kg(base)
        cfg = base / "cfg.json"
        cfg.write_text(f'{{"kg_path": "{kg.as_posix()}", "worklogs_dir": "{base.as_posix()}/wl", '
                       f'"llm_enabled": false, "verbose": false}}', encoding="utf-8")
        config = MakerConfig.from_file(cfg)
        graph = Graph.load(config.kg_path)
        web.MakerWebHandler.config = config
        web.MakerWebHandler.graph = graph
        from http.server import ThreadingHTTPServer
        cls.server = ThreadingHTTPServer(("127.0.0.1", 8761), web.MakerWebHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.tmp.cleanup()

    def _get(self, path, timeout=15):
        with urllib.request.urlopen(f"http://127.0.0.1:8761{path}", timeout=timeout) as r:
            return r.status, r.read().decode("utf-8")

    def test_index_page(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("XGEN MAKER", body)
        self.assertIn("EventSource", body)  # SSE 클라이언트

    def test_api_info(self):
        status, body = self._get("/api/info")
        data = json.loads(body)
        self.assertEqual(data["nodes"], 2)
        self.assertEqual(data["repos"], 1)

    def test_api_sync(self):
        # Sync 버튼 백엔드 — 소스 없는 테스트 그래프는 full_rebuild_needed(변경 0)로 안전 반환
        status, body = self._get("/api/sync")
        self.assertEqual(status, 200)
        d = json.loads(body)
        self.assertTrue(d["ok"])
        self.assertEqual(d["changed"], 0)
        self.assertIn("nodes", d)

    def test_sync_button_in_page(self):
        _, body = self._get("/")
        self.assertIn('id="sync"', body)      # 헤더 버튼
        self.assertIn("/api/sync", body)       # 클릭 핸들러

    def test_api_info_has_repo_names(self):
        _, body = self._get("/api/info")
        self.assertIn("repo_names", json.loads(body))

    def test_api_diagnostics(self):
        # 진단 탭 백엔드 — SDK 계약·엔진·카탈로그(로컬만)
        status, body = self._get("/api/diagnostics")
        self.assertEqual(status, 200)
        d = json.loads(body)
        self.assertIn("sdk", d)
        self.assertIn("engine", d)
        self.assertIn("catalog", d)
        self.assertIn("capabilities", d["catalog"])

    def test_new_tabs_in_page(self):
        _, body = self._get("/")
        for marker in ('data-t="branches"', 'data-t="diag"',
                       "/api/branches", "/api/release", "/api/diagnostics"):
            self.assertIn(marker, body)

    def test_maker_palette_and_light_theme(self):
        # 디자인 시스템(토큰 구조)은 Geny 방식, 팔레트는 XGEN MAKER 고유(청사진+단조)
        _, body = self._get("/")
        for dark_token in ("--primary:#3aa8c9", "--bg:#0e161d", "--ember:#d99a63",
                           "--glow:0 0 16px rgba(58,168,201,.15)"):
            self.assertIn(dark_token, body)
        self.assertIn("@media (prefers-color-scheme:light)", body)
        for light_token in ("--primary:#2b8aa8", "--bg:#eff5f7", "--border:#dbe7ec"):
            self.assertIn(light_token, body)
        # Geny 라벤더 팔레트를 그대로 쓰지 않는다(고유 팔레트)
        for lavender in ("#8573b8", "#1a1726", "#8268cf", "#f4f1f9"):
            self.assertNotIn(lavender, body)

    def test_badges_always_have_pill_background(self):
        # 미정의 클래스(outcome 등)도 중립 배경 — 투명 배지 방지
        _, body = self._get("/")
        self.assertIn(".badge{padding:2px 8px", body)
        self.assertIn("background:var(--neutral-bg)", body)
        self.assertIn(".badge.ok", body)
        self.assertIn(".badge.fail", body)

    def test_sse_run_streams_events_and_result(self):
        import urllib.parse
        q = urllib.parse.quote("charge 함수 어디 있어")
        status, body = self._get(f"/api/run?q={q}&mode=plan", timeout=30)
        self.assertEqual(status, 200)
        # SSE 이벤트 파싱
        events = [json.loads(ln[6:]) for ln in body.splitlines()
                  if ln.startswith("data: ")]
        types = [e["type"] for e in events]
        self.assertIn("event", types)
        self.assertIn("result", types)
        result = [e for e in events if e["type"] == "result"][0]
        self.assertEqual(result["report"]["outcome"], "answered")
        self.assertIn("charge", result["report"]["answer"])

    def test_404(self):
        with self.assertRaises(urllib.error.HTTPError):
            self._get("/nope")

    def test_dashboard_history_api(self):
        status, body = self._get("/api/history")
        self.assertEqual(status, 200)
        self.assertIn("sessions", json.loads(body))

    def test_dashboard_status_api(self):
        status, body = self._get("/api/status")
        d = json.loads(body)
        self.assertEqual([s["branch"] for s in d["ladder"]], ["develop", "stg", "main"])

    def test_dashboard_mrs_api(self):
        status, body = self._get("/api/mrs")
        d = json.loads(body)
        self.assertIn("mine", d)
        self.assertIn("maker", d)


class TestJournalInjection(unittest.TestCase):
    """웹 동시성 근본해결 — 전역 몽키패치 대신 인스턴스별 journal 팩토리 주입."""

    def test_maker_loop_uses_injected_factory(self):
        import tempfile
        from pathlib import Path
        from xgen_maker.loop.pipeline import MakerLoop
        from xgen_maker.config import MakerConfig
        with tempfile.TemporaryDirectory() as tmp:
            g = Graph()
            g.add_node("r:a.py", "function", "charge", repo="r",
                       meta={"file": "a.py", "path": "a.py"})
            kg = Path(tmp) / "kg.json"
            g.save(kg)
            cfg = MakerConfig(kg_path=str(kg), worklogs_dir=str(Path(tmp) / "wl"),
                              llm_enabled=False, verbose=False)
            captured = {}

            from xgen_maker.loop.journal import Journal

            def factory(worklogs_dir, q, verbose=False):
                captured["used"] = True
                return Journal(worklogs_dir, q, verbose=False)

            MakerLoop(cfg, graph=g, journal_factory=factory).run("charge 어디 있어")
            self.assertTrue(captured.get("used"), "주입한 journal 팩토리가 쓰여야 함")


class TestWebLoopbackGuard(unittest.TestCase):
    """무인증 노출 가드 — 비-loopback 바인드는 명시 동의 없으면 거부."""

    def test_non_loopback_refused_without_optin(self):
        import os
        os.environ.pop("XGEN_MAKER_WEB_ALLOW_REMOTE", None)
        with self.assertRaises(SystemExit):
            web.serve(None, host="0.0.0.0", port=0)

    def test_non_loopback_allowed_with_optin(self):
        # 명시 동의가 있으면 가드 통과 → 없는 config에서 막힘(serve_forever 도달 X, SystemExit 아님)
        import os
        os.environ["XGEN_MAKER_WEB_ALLOW_REMOTE"] = "1"
        try:
            with self.assertRaises(Exception) as ctx:
                web.serve("/nonexistent/config.json", host="0.0.0.0", port=0)
            self.assertNotIsInstance(ctx.exception, SystemExit)
        finally:
            os.environ.pop("XGEN_MAKER_WEB_ALLOW_REMOTE", None)

    def test_loopback_passes_guard(self):
        # 127.0.0.1은 가드를 그냥 통과(없는 config에서 SystemExit 아닌 다른 예외)
        with self.assertRaises(Exception) as ctx:
            web.serve("/nonexistent/config.json", host="127.0.0.1", port=0)
        self.assertNotIsInstance(ctx.exception, SystemExit)


if __name__ == "__main__":
    import urllib.error
    unittest.main()
