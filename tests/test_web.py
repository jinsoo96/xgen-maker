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

    def test_graph_status_api(self):
        status, body = self._get("/api/graph")
        self.assertEqual(status, 200)
        d = json.loads(body)
        self.assertEqual(d["nodes"], 2)
        self.assertEqual(d["edges"], 1)
        self.assertIn("by_kind", d)
        self.assertIn("repos", d)

    def test_subgraph_query_returns_nodes_edges(self):
        # 심볼 검색 → 착지 노드 + 이웃(양방향) 서브그래프
        status, body = self._get("/api/subgraph?q=charge")
        self.assertEqual(status, 200)
        d = json.loads(body)
        ids = {n["id"] for n in d["nodes"]}
        self.assertIn("r:pay.py#charge", ids)
        self.assertIn("r:pay.py", ids)          # contains 엣지로 연결된 이웃 포함
        self.assertTrue(any(e["kind"] == "contains" for e in d["edges"]))
        self.assertTrue(any(n.get("seed") for n in d["nodes"]))  # 착지점 표시

    def test_subgraph_empty_query_overview(self):
        # 빈 쿼리 → 최다연결 노드 기반 개요 그래프(빈 캔버스 방지)
        _, body = self._get("/api/subgraph?q=")
        d = json.loads(body)
        self.assertTrue(d["nodes"])

    def test_subgraph_no_match_reason(self):
        _, body = self._get("/api/subgraph?q=zzz_no_such_symbol")
        d = json.loads(body)
        self.assertEqual(d["nodes"], [])
        self.assertIn("reason", d)

    def test_graph_viewer_in_page(self):
        _, body = self._get("/")
        for marker in ('data-t="graph"', "id=gsvg", "drawGraph", "/api/subgraph",
                       'id="newsess"', "id=gq"):
            self.assertIn(marker, body)

    def test_new_feature_tabs_in_page(self):
        # 4대 신규 기능 표면이 페이지에 실려 있는가
        _, body = self._get("/")
        for marker in ('data-t="ui"', 'data-t="login"', "showSession", "openNodeEditor",
                       "/api/annotate", "/api/ui-snap", "/api/auth", "doUndo"):
            self.assertIn(marker, body)

    def test_annotate_persists_and_applies(self):
        # R8 — 노드 편집이 응답 ok + 라이브 그래프 meta 반영
        h = web.MakerWebHandler.__new__(web.MakerWebHandler)
        h.config = self.__class__.config if hasattr(self.__class__, "config") else None
        # setUpClass의 config/graph를 클래스 속성에서 가져온다
        h.config = web.MakerWebHandler.config
        h.graph = web.MakerWebHandler.graph
        try:
            r = h._annotate({"node": ["r:pay.py#charge"], "note": ["레거시"], "deprecated": ["1"]})
            self.assertTrue(r["ok"])
            self.assertEqual(h.graph.nodes["r:pay.py#charge"]["meta"]["note"], "레거시")
            self.assertTrue(h.graph.nodes["r:pay.py#charge"]["meta"]["deprecated"])
        finally:
            # 공유 그래프 오염 방지 — 편집 원복(deprecated면 다른 테스트 검색이 이 노드를 회피)
            meta = h.graph.nodes["r:pay.py#charge"]["meta"]
            meta.pop("note", None); meta.pop("deprecated", None)
            # overlay 파일도 삭제 — SSE 실행의 MakerLoop가 재로드+재적용하므로
            ov = Path(h.config.kg_path).parent / "overlay.json"
            if ov.exists():
                ov.unlink()

    def test_annotate_unknown_node(self):
        h = web.MakerWebHandler.__new__(web.MakerWebHandler)
        h.config = web.MakerWebHandler.config
        h.graph = web.MakerWebHandler.graph
        r = h._annotate({"node": ["nope:nope"], "note": ["x"]})
        self.assertFalse(r["ok"])

    def test_ui_status_api(self):
        status, body = self._get("/api/ui-status")
        self.assertEqual(status, 200)
        d = json.loads(body)
        for k in ("pillow", "playwright", "baselines", "recent"):
            self.assertIn(k, d)

    def test_ui_image_path_jail(self):
        # 경로 탈출·비-png 차단
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/ui-image?f=../../../etc/hosts")
        self.assertIn(ctx.exception.code, (403, 404))

    def test_auth_info_api(self):
        status, body = self._get("/api/auth")
        self.assertEqual(status, 200)
        d = json.loads(body)
        for k in ("provider", "gitlab_url", "gitlab_token_set", "auth_file_exists"):
            self.assertIn(k, d)

    def test_node_code_api(self):
        # 노드 → 실제 코드 블록 (임시 그래프의 함수 노드는 파일이 없으므로 에러 경로 확인)
        h = web.MakerWebHandler.__new__(web.MakerWebHandler)
        h.config = web.MakerWebHandler.config
        h.graph = web.MakerWebHandler.graph
        r = h._node_code("r:pay.py#charge")
        # 테스트 그래프의 repo 'r'은 config.repos에 없음 → 명확한 에러(크래시 아님)
        self.assertIn("ok", r)
        self.assertFalse(r["ok"])
        self.assertIn("error", r)
        # 없는 노드
        self.assertFalse(h._node_code("nope")["ok"])

    def test_tests_api(self):
        status, body = self._get("/api/tests")
        self.assertEqual(status, 200)
        self.assertIn("runs", json.loads(body))

    def test_activity_api_no_token_graceful(self):
        # 토큰 없거나 매핑 없어도 크래시 없이 error 반환(UnboundLocalError 회귀 방지)
        status, body = self._get("/api/activity?repo=nope&q=x")
        self.assertEqual(status, 200)
        d = json.loads(body)
        self.assertTrue("error" in d or "commits" in d)

    def test_resume_and_tests_tab_in_page(self):
        _, body = self._get("/")
        for marker in ("resumebtn", "loadNodeCode", "/api/node-code", "/api/tests",
                       "/api/activity", 'data-t="tests"', "이어서 실행"):
            self.assertIn(marker, body)

    def test_stop_and_interconnect_in_page(self):
        # 실행 중지 + 클릭 연동(landing→코드, 코드→작업, 브랜치→활동, 큰 그래프)
        _, body = self._get("/")
        for marker in ('id="stopbtn"', "/api/stop", "run_id", "jumpToNodeCode",
                       "gcwork", "이 코드로 작업", "brow", "onwheel", "id=gn"):
            self.assertIn(marker, body)

    def test_stop_unknown_run_graceful(self):
        _, body = self._get("/api/stop?id=nonexistent")
        d = json.loads(body)
        self.assertFalse(d["ok"])

    def test_no_duplicate_ids_across_tabs(self):
        # 검수에서 나온 버그: showSession이 작업이력·테스트 두 탭에 렌더되는데
        # id로 조회하면 먼저 그려진 탭 것만 잡혀 나중 탭 버튼이 죽었다 → 클래스로 전환.
        _, body = self._get("/")
        for dup in ('id="undobtn"', 'id="resumebtn"', 'id="undoout"',
                    'id="undoremote"', 'id="histcols"', 'id="histlist"'):
            self.assertNotIn(dup, body, f"{dup}는 두 탭에 중복 렌더됨 — 클래스여야 함")
        for cls_ in ("undobtn", "resumebtn", "undoout", "undoremote"):
            self.assertIn(cls_, body)
        # 패널 범위 조회를 쓰는지
        self.assertIn("d.querySelector('.undobtn')", body)

    def test_path_jail_rejects_sibling_prefix(self):
        # 검수에서 나온 버그: startswith 기반 jail은 형제 디렉토리(kg-secrets)를 통과시켰다
        import tempfile
        from pathlib import Path
        h = web.MakerWebHandler.__new__(web.MakerWebHandler)
        h.config = web.MakerWebHandler.config
        h.graph = web.MakerWebHandler.graph
        kg_root = Path(h.config.kg_path).parent.resolve()
        sibling = kg_root.parent / (kg_root.name + "-secrets")
        # 형제 디렉토리는 jail 밖이어야 한다
        self.assertFalse(sibling.is_relative_to(kg_root),
                         "형제 디렉토리가 jail 안으로 판정되면 안 됨")
        # 소스에 startswith 기반 jail이 남아있지 않은지
        src = Path(web.__file__).read_text(encoding="utf-8")
        self.assertNotIn("startswith(str(", src, "경로 jail은 is_relative_to를 써야 함")

    def test_ui_slug_distinct_for_colliding_urls(self):
        # 검수에서 나온 버그: a/b 와 a-b 가 같은 슬러그 → 기준선 덮어씀
        s1 = web.MakerWebHandler._ui_slug("http://h/a/b")
        s2 = web.MakerWebHandler._ui_slug("http://h/a-b")
        self.assertNotEqual(s1, s2)
        self.assertEqual(s1, web.MakerWebHandler._ui_slug("http://h/a/b"))  # 결정론

    def test_ui_baseline_legacy_fallback(self):
        # 재검수에서 나온 버그: 슬러그에 해시를 붙이자 이미 저장된 기준선이 조용히 고아가 됐다.
        # 읽을 땐 옛 형식도 인정해야 한다(안 그러면 갤러리엔 보이는데 "기준 없음"이라 뜸).
        import tempfile
        from pathlib import Path
        h = web.MakerWebHandler.__new__(web.MakerWebHandler)
        url = "http://h:8790/"
        with tempfile.TemporaryDirectory() as t:
            bd = Path(t)
            legacy = bd / (web.MakerWebHandler._ui_slug_legacy(url) + ".png")
            legacy.write_bytes(b"\x89PNG")
            got = h._ui_baseline_path(bd, url, web.MakerWebHandler._ui_slug(url))
            self.assertEqual(got, legacy, "옛 형식 기준선을 찾아야 함")
            # 새 형식이 있으면 그게 우선
            new = bd / (web.MakerWebHandler._ui_slug(url) + ".png")
            new.write_bytes(b"\x89PNG")
            self.assertEqual(h._ui_baseline_path(bd, url, web.MakerWebHandler._ui_slug(url)), new)

    def test_ghost_button_style_not_scoped_to_gsearch(self):
        # 재검수에서 나온 버그: .gsearch button.ghost 로 한정돼 세션 상세의 '이어서 실행'과
        # 그래프의 '지금 동기화'가 스타일을 못 받았다(둘 다 .gsearch 밖).
        _, body = self._get("/")
        self.assertIn("button.ghost{", body)
        self.assertNotIn(".gsearch button.ghost{", body)

    def test_sync_invalidates_adjacency_cache(self):
        # 재검수에서 나온 버그: sync가 그래프를 제자리 변경하는데 인접 캐시를 안 버렸다.
        # 노드/엣지 수가 같아도 내용이 바뀔 수 있으므로 무조건 무효화해야 한다.
        import re
        from pathlib import Path
        src = Path(web.__file__).read_text(encoding="utf-8")
        sync_block = src[src.index('parsed.path == "/api/sync"'):]
        sync_block = sync_block[:sync_block.index("elif parsed.path")]
        self.assertIn("_adj_ver = None", sync_block,
                      "/api/sync는 인접 캐시를 무효화해야 함")

    def test_adjacency_cache_is_class_level(self):
        # 검수에서 나온 버그: 인스턴스 속성이라 요청마다 16k 인접 재구축(캐시 무효)
        h1 = web.MakerWebHandler.__new__(web.MakerWebHandler)
        h1.config = web.MakerWebHandler.config; h1.graph = web.MakerWebHandler.graph
        a1 = h1._adjacency()
        h2 = web.MakerWebHandler.__new__(web.MakerWebHandler)  # 새 요청 = 새 인스턴스
        h2.config = web.MakerWebHandler.config; h2.graph = web.MakerWebHandler.graph
        a2 = h2._adjacency()
        self.assertIs(a1, a2, "핸들러가 새로 생겨도 같은 인접 캐시를 써야 함")

    def test_repo_graph_level1(self):
        # 1단계 — 레포 간 그래프(노드=레포). 테스트 그래프는 repo 'r' 하나뿐
        status, body = self._get("/api/repo-graph")
        self.assertEqual(status, 200)
        d = json.loads(body)
        self.assertEqual(d["level"], "repo")
        self.assertEqual([n["kind"] for n in d["nodes"]], ["repo"])
        self.assertEqual(d["nodes"][0]["name"], "r")
        self.assertEqual(d["nodes"][0]["count"], 2)  # r 레포에 노드 2개

    def test_repo_drilldown_level2(self):
        # 2단계 — repo 지정 시 그 레포 내부 그래프
        _, body = self._get("/api/subgraph?repo=r&n=50")
        d = json.loads(body)
        self.assertEqual(d["level"], "node")
        self.assertEqual(d["repo"], "r")
        self.assertEqual(len(d["nodes"]), 2)
        self.assertTrue(all(n["repo"] == "r" for n in d["nodes"]))

    def test_repo_drilldown_unknown_repo(self):
        _, body = self._get("/api/subgraph?repo=nope")
        d = json.loads(body)
        self.assertEqual(d["nodes"], [])
        self.assertIn("reason", d)

    def test_drilldown_ui_in_page(self):
        _, body = self._get("/")
        for marker in ("/api/repo-graph", "showRepo", "gcrumb", "jumpToRepoGraph",
                       "rchip", "__showRepo"):
            self.assertIn(marker, body)

    def test_subgraph_size_param(self):
        # n 파라미터로 노드 수 제어(작은 그래프 방지) — 테스트 그래프는 2노드뿐이라 그대로
        _, body = self._get("/api/subgraph?q=&n=500")
        d = json.loads(body)
        self.assertIn("total_nodes", d)

    def test_maker_branches_from_journal(self):
        # journal의 branch/ok 이벤트에서만 MAKER 브랜치를 뽑는다(이름 추측 금지)
        from xgen_maker.loop.history import maker_branches
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as t:
            sess = Path(t) / "2026-07-10-000000-x"; sess.mkdir()
            (sess / "journal.jsonl").write_text(
                '{"step":"branch","status":"ok","branch":"fix/real-1"}\n'
                '{"step":"branch","status":"fail","branch":"fix/never"}\n', encoding="utf-8")
            got = maker_branches(t)
            self.assertIn("fix/real-1", got)
            self.assertNotIn("fix/never", got)

    def test_session_detail_and_undo_shape(self):
        # 세션 상세는 없는 세션에 404, undo는 미확인 시 미리보기(삭제 안 함)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/session?id=does-not-exist")
        self.assertEqual(ctx.exception.code, 404)
        _, body = self._get("/api/undo?id=does-not-exist")
        d = json.loads(body)
        self.assertFalse(d["ok"])  # 브랜치 없는(존재X) 세션 → 되돌릴 것 없음

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
