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


if __name__ == "__main__":
    import urllib.error
    unittest.main()


if __name__ == "__main__":
    import urllib.error
    unittest.main()
