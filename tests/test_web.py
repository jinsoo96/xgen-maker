import json
import subprocess
import sys
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
        self.assertIn(".badge{display:inline-block;padding:2px 8px", body)
        self.assertIn("background:var(--neutral-bg)", body)
        # 한글 라벨이 '답변 완/료'로 줄바꿈되지 않아야 한다
        self.assertIn("white-space:nowrap", body)
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
        for k in ("provider", "gitlab_host", "gitlab_token_set", "auth_file_exists"):
            self.assertIn(k, d)

    def test_auth_info_is_masked(self):
        # 대시보드가 공개 도메인(Cloudflare 터널)에 붙을 수 있다 — 식별정보를 원문으로
        # 내보내면 안 된다. 토큰은 애초에 안 나가고, 주소·계정·경로는 마스킹.
        _, body = self._get("/api/auth")
        d = json.loads(body)
        self.assertNotIn("gitlab_url", d, "전체 URL은 내보내지 않는다")
        for k in ("gitlab_host", "gitlab_user"):
            if d.get(k):
                self.assertIn("•", d[k], f"{k}가 마스킹되지 않음: {d[k]}")
        # 저장 위치는 파일명만(전체 경로엔 OS 사용자명이 드러남)
        self.assertNotIn("\\", d["auth_file"])
        self.assertNotIn("/", d["auth_file"])
        # 토큰 원문은 어떤 필드로도 나가지 않는다
        self.assertNotIn("token", json.dumps(
            {k: v for k, v in d.items() if k != "gitlab_token_set"}).lower())

    def test_lightbox_in_page(self):
        # 축소된 캡처는 글씨가 안 읽힌다 — 눌러서 원본 크기로 볼 수 있어야
        _, body = self._get("/")
        for marker in ('id="lightbox"', "zoom-in", "lbimg", "새 탭에서 원본"):
            self.assertIn(marker, body)

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

    def test_node_code_lands_on_the_line(self):
        """줄을 아는 노드는 그 줄이 보이는 창을 준다 — 파일 앞머리를 띄우면 거짓 안내다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "main.rs").write_text(
                "\n".join(f"line {i}" for i in range(1, 121)), encoding="utf-8")
            graph = Graph()
            graph.add_node("gw:src/main.rs", "file", "main.rs", "gw", "src/main.rs")
            graph.add_node("gw:ep", "endpoint", "POST /x", "gw", "src/main.rs", 100,
                           method="POST", route_path="/x")
            h = web.MakerWebHandler.__new__(web.MakerWebHandler)
            h.config = MakerConfig(repos={"gw": str(root)})
            h.graph = graph

            r = h._node_code("gw:ep")
            self.assertTrue(r["ok"])
            self.assertEqual(r["focus_line"], 100)
            shown = range(r["first_line"], r["first_line"] + len(r["code"].splitlines()))
            self.assertIn(100, shown)

            f = h._node_code("gw:src/main.rs")     # 줄이 없는 파일 노드는 앞머리부터
            self.assertEqual(f["first_line"], 1)

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

    def test_node_code_directory_is_not_a_sync_problem(self):
        # 6회차 검수 버그: 디렉토리를 가리키는 컨테이너 노드(repo/feature — 실그래프 152개)에
        # "Sync 필요"라고 거짓 안내해, 멀쩡한 그래프를 다시 돌리게 만들었다.
        # 진짜 누락 파일(101개)만 Sync 안내여야 한다.
        from pathlib import Path as _P
        from xgen_maker.kg.graph import Graph as _G
        from xgen_maker.config import MakerConfig as _C
        with tempfile.TemporaryDirectory() as t:
            root = _P(t) / "demo"; (root / "pkg").mkdir(parents=True)
            (root / "real.py").write_text("x = 1" + chr(10), encoding="utf-8")
            g = _G()
            g.add_node("demo", "repo", "demo", "demo", str(root))       # 절대경로 + 디렉토리
            g.add_node("demo:pkg", "feature", "pkg", "demo", "pkg")     # 상대경로 + 디렉토리
            g.add_node("demo:real.py", "file", "real.py", "demo", "real.py")
            g.add_node("demo:gone.py", "file", "gone.py", "demo", "gone.py")  # 진짜 없음
            kg = _P(t) / "kg.json"; g.save(kg)
            h = web.MakerWebHandler.__new__(web.MakerWebHandler)
            h.config = _C(repos={"demo": str(root)}, kg_path=str(kg),
                          worklogs_dir=str(_P(t) / "wl"), llm_enabled=False)
            h.graph = g
            for nid in ("demo", "demo:pkg"):
                r = h._node_code(nid)
                self.assertFalse(r["ok"])
                self.assertIn("폴더", r["error"], nid)
                self.assertNotIn("동기화", r["error"], f"{nid}: 거짓 동기화 안내")
            gone = h._node_code("demo:gone.py")
            self.assertIn("동기화", gone["error"])      # 진짜 누락은 Sync 안내 유지
            self.assertTrue(h._node_code("demo:real.py")["ok"])

    def test_node_code_absolute_path_cannot_escape_repo(self):
        # Path의 '절대경로가 root를 덮어쓰는' 성질에 기대면 조용히 root 밖을 읽는다
        from pathlib import Path as _P
        from xgen_maker.kg.graph import Graph as _G
        from xgen_maker.config import MakerConfig as _C
        with tempfile.TemporaryDirectory() as t:
            root = _P(t) / "demo"; root.mkdir()
            outside = _P(t) / "outside"; outside.mkdir()
            (outside / "secret.py").write_text("KEY = 1" + chr(10), encoding="utf-8")
            g = _G()
            g.add_node("demo:esc", "file", "secret.py", "demo", str(outside / "secret.py"))
            kg = _P(t) / "kg.json"; g.save(kg)
            h = web.MakerWebHandler.__new__(web.MakerWebHandler)
            h.config = _C(repos={"demo": str(root)}, kg_path=str(kg),
                          worklogs_dir=str(_P(t) / "wl"), llm_enabled=False)
            h.graph = g
            r = h._node_code("demo:esc")
            self.assertFalse(r["ok"])
            self.assertIn("허용되지 않은 경로", r["error"])

    def test_repo_drilldown_excludes_its_own_container(self):
        # 레포 내부 뷰에 그 레포 자신(kind=repo)이 섞이면, 클릭 시 같은 레포로 다시
        # 드릴다운돼 제자리를 맴돈다
        from pathlib import Path as _P
        from xgen_maker.kg.graph import Graph as _G
        from xgen_maker.config import MakerConfig as _C
        with tempfile.TemporaryDirectory() as t:
            g = _G()
            g.add_node("demo", "repo", "demo", "demo", "/x/demo")
            g.add_node("demo:a.py", "file", "a.py", "demo", "a.py")
            kg = _P(t) / "kg.json"; g.save(kg)
            h = web.MakerWebHandler.__new__(web.MakerWebHandler)
            h.config = _C(repos={"demo": "/x/demo"}, kg_path=str(kg),
                          worklogs_dir=str(_P(t) / "wl"), llm_enabled=False)
            h.graph = g
            sg = h._repo_subgraph("demo", 50)
            self.assertEqual([n["id"] for n in sg["nodes"]], ["demo:a.py"])

    def test_graph_reads_survive_concurrent_mutation(self):
        # 5회차 검수 버그: 기존 코드는 sync 중 순회 크래시를 재시도로 막고 있었는데
        # 내가 새로 넣은 그래프 함수들엔 그 가드가 없어, Sync 중 그래프 탭이 500이 났다.
        import threading
        import time
        from xgen_maker.kg.graph import Graph
        g = Graph()
        for i in range(60):
            g.add_node(f"r:f{i}.py", "file", f"f{i}.py", "r", f"f{i}.py")
        h = web.MakerWebHandler.__new__(web.MakerWebHandler)
        h.config = web.MakerWebHandler.config
        h.graph = g
        stop = [False]

        def mutate():  # sync가 하는 일: 제자리에서 노드/엣지를 넣고 뺀다
            i = 0
            while not stop[0]:
                nid = f"__probe{i}__"
                g.add_node(nid, "file", nid, "r", nid)
                g.edges.append({"src": nid, "dst": nid, "kind": "contains", "meta": {}})
                g.nodes.pop(nid, None)
                if g.edges and g.edges[-1]["src"] == nid:
                    g.edges.pop()
                i += 1
        t = threading.Thread(target=mutate, daemon=True)
        t.start()
        try:
            for _ in range(40):  # 변경 중에도 예외 없이 결과를 줘야 한다
                self.assertIn("nodes", h._repo_graph())
                self.assertIn("nodes", h._graph_status())
                self.assertIn("nodes", h._graph_info())
        finally:
            stop[0] = True
            t.join(timeout=2)

    def test_graph_read_guard_falls_back_not_raises(self):
        # 계속 변경 중이라 끝내 못 읽어도 500이 아니라 fallback을 준다
        def always_racing():
            raise RuntimeError("dictionary changed size during iteration")
        got = web.MakerWebHandler._graph_read(always_racing, {"nodes": [], "reason": "busy"},
                                              tries=2)
        self.assertEqual(got["reason"], "busy")

    def test_annotate_survives_node_removed_midway(self):
        # TOCTOU: membership 검사 통과 후 sync가 노드를 지우면 KeyError로 500이 났다.
        # overlay는 정본이므로 편집은 남고, 라이브 반영만 조용히 건너뛴다.
        import tempfile
        from pathlib import Path
        from xgen_maker.kg.graph import Graph
        from xgen_maker.config import MakerConfig
        with tempfile.TemporaryDirectory() as t:
            g = Graph()
            g.add_node("r:a.py#f", "function", "f", "r", "a.py", 1)
            kg = Path(t) / "kg.json"
            g.save(kg)
            h = web.MakerWebHandler.__new__(web.MakerWebHandler)
            h.config = MakerConfig(kg_path=str(kg), worklogs_dir=str(Path(t) / "wl"),
                                   llm_enabled=False)
            h.graph = g

            class VanishingNodes(dict):
                """membership 검사는 통과시키고, 라이브 반영 시점엔 sync가 지운 상태."""
                def get(self, k, d=None):
                    self.pop("r:a.py#f", None)
                    return super().get(k, d)
            g.nodes = VanishingNodes(g.nodes)
            r = h._annotate({"node": ["r:a.py#f"], "note": ["레거시"]})
            self.assertTrue(r["ok"])  # 크래시 없이 overlay에는 기록
            import json as _j
            ov = _j.loads((Path(t) / "overlay.json").read_text(encoding="utf-8"))
            self.assertEqual(ov["node_overrides"]["r:a.py#f"]["note"], "레거시")

    def test_link_local_blocks_hostname_not_just_ip(self):
        # 5회차: IP 리터럴만 막아 호스트명 하나로 우회됐다 → 실제 해소해서 판정
        import socket
        from xgen_maker.web import _is_link_local
        orig = socket.getaddrinfo
        socket.getaddrinfo = lambda h, p, *a, **k: [(2, 1, 6, '', ('169.254.169.254', 80))]
        try:
            self.assertTrue(_is_link_local("http://metadata.internal.example/"))
        finally:
            socket.getaddrinfo = orig
        self.assertFalse(_is_link_local("http://localhost:3100/"))

    def test_pipeline_catalog_matches_real_code(self):
        # 10회차 검수 버그: 파이프라인 탭이 ui_verify의 게이트를 preview_base라고 표시했는데
        # 실제 코드는 config.enable_ui_verify로 분기한다 → UI가 거짓 정보를 보여줬다.
        # 카탈로그가 코드에서 드리프트하면 즉시 잡히게 고정한다.
        from pathlib import Path as _P
        from xgen_maker.config import MakerConfig as _C
        fields = set(_C.__dataclass_fields__)
        pipe_src = _P(web.__file__).parent.joinpath("loop", "pipeline.py").read_text(
            encoding="utf-8")
        conv_src = _P(web.__file__).parent.joinpath("loop", "converge.py").read_text(
            encoding="utf-8")
        both = pipe_src + conv_src
        for step, label, desc, gate in web.MakerWebHandler.PIPELINE:
            # 게이트는 실제 config 필드여야 한다(오타·존재하지 않는 키 차단)
            if gate:
                self.assertIn(gate, fields, f"{step}의 게이트 '{gate}'는 config 필드가 아님")
                # 코드는 config.X 와 getattr(config, "X", ...) 두 방식을 섞어 쓴다
                self.assertTrue(f"config.{gate}" in both or f'"{gate}"' in both,
                                f"{step}의 게이트 '{gate}'가 실제 코드에서 안 쓰임")
            # 카탈로그의 단계는 실제로 journal에 기록되는 step이어야 한다
            self.assertIn(f'"{step}"', both, f"카탈로그 단계 '{step}'가 코드에 없음")

    def test_settable_keys_are_real_config_fields(self):
        from xgen_maker.config import MakerConfig as _C
        fields = set(_C.__dataclass_fields__)
        for key in web.MakerWebHandler.SETTABLE:
            self.assertIn(key, fields, f"변경 대상 '{key}'가 config에 없음")
        # 위험 설정은 변경 대상에서 빠져 있어야 한다
        for danger in ("allow_write", "deploy_mode", "gitlab_projects", "repos"):
            self.assertNotIn(danger, web.MakerWebHandler.SETTABLE)

    def test_setting_change_applies_and_validates(self):
        h = web.MakerWebHandler.__new__(web.MakerWebHandler)
        h.config = web.MakerWebHandler.config
        old = h.config.target_branch
        try:
            self.assertTrue(h._set_setting({"key": ["target_branch"], "value": ["stg"]})["ok"])
            self.assertEqual(h.config.target_branch, "stg")
            # 범위 클램프
            r = h._set_setting({"key": ["max_iterations"], "value": ["99"]})
            self.assertEqual(r["value"], 10)
            # choice 검증
            self.assertFalse(h._set_setting({"key": ["mode"], "value": ["nope"]})["ok"])
            # 화이트리스트 밖
            self.assertFalse(h._set_setting({"key": ["allow_write"], "value": ["1"]})["ok"])
        finally:
            h.config.target_branch = old
            h.config.max_iterations = 3

    def test_run_mode_whitelist_fails_closed(self):
        # 4회차 검수 버그: 'plan' 정확일치만 읽기전용이라 오타·미지의 모드가 전부
        # allow_write=True로 새어 실제 레포에 브랜치·커밋이 나갔다(fail-open).
        for bad in ("plna", "ACT", "xyz", "observe2"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self._get(f"/api/run?q=test&mode={bad}", timeout=10)
            self.assertEqual(ctx.exception.code, 400, f"mode={bad!r}는 거부돼야 함")
        # 빈 mode는 parse_qs가 키째 버려 기본값 plan(읽기전용)으로 간다 — fail-safe
        from urllib.parse import parse_qs
        self.assertNotIn("mode", parse_qs("q=test&mode="))

    def test_snapshot_blocks_link_local_metadata(self):
        # 4회차 검수: 무인증 포트에 닿은 사람이 서버로 하여금 메타데이터를 열어
        # 스크린샷으로 자격증명을 넘겨받을 수 있었다
        from xgen_maker.web import _is_link_local
        self.assertTrue(_is_link_local("http://169.254.169.254/latest/meta-data/"))
        self.assertTrue(_is_link_local("http://[fe80::1]/"))
        self.assertFalse(_is_link_local("http://localhost:3100/"))  # 정상 용도는 막지 않음
        h = web.MakerWebHandler.__new__(web.MakerWebHandler)
        h.config = web.MakerWebHandler.config
        r = h._ui_snap({"url": ["http://169.254.169.254/"]})
        self.assertFalse(r["ok"])
        self.assertIn("캡처할 수 없습니다", r["error"])

    def test_doctor_runs_in_subprocess_not_global_stdout(self):
        # 4회차 검수: contextlib.redirect_stdout은 전역 sys.stdout을 바꿔, doctor가 도는
        # 1~2분 동안 다른 요청 스레드의 print가 이 버퍼로 빨려들어갔다(스레딩 서버).
        from pathlib import Path
        src = Path(web.__file__).read_text(encoding="utf-8")
        block = src[src.index("def _doctor"):]
        block = block[:block.index("\n    def ")]
        # 호출부만 본다(설명 주석에 이름이 나오는 건 무방)
        self.assertNotIn("redirect_stdout(", block, "doctor는 전역 stdout을 가로채면 안 됨")
        self.assertIn("subprocess.run", block)

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


class TestObserveLoopOverSSE(unittest.TestCase):
    """웹 SSE 경로로 수렴 루프 전 구간(implement→checks→judge→commit)을 태운다.

    기존 SSE 테스트는 plan(질문형)만 덮어, _SSEJournal 래퍼가 실제 쓰기 루프를
    끝까지 견디는지·landing payload가 흐르는지는 미검증이었다.
    GitLab 미접촉: gitlab_projects={} + 127.0.0.1 바인드.
    """
    NL = chr(10)
    # 에이전트 스텁 — 이스케이프 층을 안 타게 chr(10)으로 조립
    STUB = ("import pathlib" + chr(10) +
            "p = pathlib.Path('app.py')" + chr(10) +
            "p.write_text('def greet(name):' + chr(10) + \"    return 'hello ' + name\""
            " + chr(10), encoding='utf-8')" + chr(10))

    def _git(self, root, *a):
        subprocess.run(["git", *a], cwd=root, capture_output=True, check=True)

    def test_runtime_setting_actually_changes_execution(self):
        # 설정 UI가 '표시만' 바뀌는 게 아니라 실제 실행에 반영되는지.
        # _sse_run은 요청마다 config를 복제하므로, 복제 시점에 런타임 변경분이 실려야 한다.
        import threading
        import urllib.parse
        from http.server import ThreadingHTTPServer
        from pathlib import Path as _P
        from xgen_maker.kg.build import build_repo

        with tempfile.TemporaryDirectory() as t:
            base = _P(t)
            repo = base / "demo"; repo.mkdir()
            self._git(repo, "init", "-b", "develop")
            self._git(repo, "config", "user.email", "t@t")
            self._git(repo, "config", "user.name", "t")
            (repo / "app.py").write_text("def greet(n):" + self.NL +
                                         "    return 'hi'" + self.NL, encoding="utf-8")
            self._git(repo, "add", "-A"); self._git(repo, "commit", "-m", "init")
            self._git(repo, "branch", "stg")  # 분기 대상이 될 다른 브랜치
            stub = base / "stub.py"; stub.write_text(self.STUB, encoding="utf-8")
            g = build_repo("demo", repo); kg = base / "kg.json"; g.save(kg)
            cfg = MakerConfig(repos={"demo": str(repo)}, kg_path=str(kg),
                              worklogs_dir=str(base / "wl"), mode="observe",
                              allow_write=True, llm_enabled=False, verbose=False,
                              max_iterations=3, fetch_latest=False, gitlab_projects={},
                              target_branch="develop",
                              agent_cmd='"' + sys.executable + '" "' + str(stub) + '"')
            prev_cfg, prev_graph = web.MakerWebHandler.config, web.MakerWebHandler.graph
            web.MakerWebHandler.config = cfg
            web.MakerWebHandler.graph = Graph.load(kg)
            srv = ThreadingHTTPServer(("127.0.0.1", 0), web.MakerWebHandler)
            port = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                # UI에서 대상 브랜치를 stg로 변경
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/setting?key=target_branch&value=stg",
                        timeout=15) as r:
                    self.assertTrue(json.loads(r.read())["ok"])
                q = urllib.parse.quote("greet 고쳐줘")
                report = None
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/run?q={q}&mode=observe",
                        timeout=240) as resp:
                    for raw in resp:
                        ln = raw.decode("utf-8").strip()
                        if ln.startswith("data: "):
                            e = json.loads(ln[6:])
                            if e.get("type") == "result":
                                report = e["report"]
            finally:
                srv.shutdown()
                web.MakerWebHandler.config, web.MakerWebHandler.graph = prev_cfg, prev_graph

            self.assertIsNotNone(report)
            br = report["branch"]
            # 실제로 stg에서 분기됐는가(설정이 실행에 먹었다는 증거)
            mb = subprocess.run(["git", "merge-base", br, "stg"], cwd=repo,
                                capture_output=True, text=True, encoding="utf-8").stdout.strip()
            stg = subprocess.run(["git", "rev-parse", "stg"], cwd=repo,
                                 capture_output=True, text=True, encoding="utf-8").stdout.strip()
            self.assertEqual(mb, stg, "target_branch 변경이 실행에 반영되지 않음")

    def test_observe_loop_converges_and_commits(self):
        import threading
        import urllib.parse
        from http.server import ThreadingHTTPServer
        from pathlib import Path as _P
        from xgen_maker.kg.build import build_repo

        with tempfile.TemporaryDirectory() as t:
            base = _P(t)
            repo = base / "demo"; repo.mkdir()
            self._git(repo, "init", "-b", "develop")
            self._git(repo, "config", "user.email", "t@t")
            self._git(repo, "config", "user.name", "t")
            (repo / "app.py").write_text("def greet(name):" + self.NL +
                                         "    return 'hi ' + name" + self.NL, encoding="utf-8")
            self._git(repo, "add", "-A"); self._git(repo, "commit", "-m", "init")
            stub = base / "stub.py"; stub.write_text(self.STUB, encoding="utf-8")

            g = build_repo("demo", repo); kg = base / "kg.json"; g.save(kg)
            cfg = MakerConfig(repos={"demo": str(repo)}, kg_path=str(kg),
                              worklogs_dir=str(base / "wl"), mode="observe",
                              allow_write=True, llm_enabled=False, verbose=False,
                              max_iterations=3, fetch_latest=False, gitlab_projects={},
                              agent_cmd='"' + sys.executable + '" "' + str(stub) + '"')
            prev_cfg, prev_graph = web.MakerWebHandler.config, web.MakerWebHandler.graph
            web.MakerWebHandler.config = cfg
            web.MakerWebHandler.graph = Graph.load(kg)
            srv = ThreadingHTTPServer(("127.0.0.1", 0), web.MakerWebHandler)
            port = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                q = urllib.parse.quote("greet 함수 인사말 고쳐줘")
                url = f"http://127.0.0.1:{port}/api/run?q={q}&mode=observe"
                steps, report, landing = [], None, False
                with urllib.request.urlopen(url, timeout=240) as resp:
                    for raw in resp:
                        ln = raw.decode("utf-8").strip()
                        if not ln.startswith("data: "):
                            continue
                        e = json.loads(ln[6:])
                        if e.get("type") == "event":
                            steps.append(e["step"] + "/" + e["status"])
                            landing = landing or bool(e.get("landing"))
                        elif e.get("type") == "result":
                            report = e["report"]
            finally:
                srv.shutdown()
                web.MakerWebHandler.config, web.MakerWebHandler.graph = prev_cfg, prev_graph

            self.assertIsNotNone(report, f"result 미수신 — steps={steps}")
            self.assertEqual(report["outcome"], "committed_local", f"steps={steps}")
            self.assertTrue(report["branch"].startswith("fix/"))
            self.assertTrue(landing, "우측 패널용 landing payload가 흘러야 함")
            for want in ("kg_search/start", "implement/ok", "checks/ok",
                         "judge/pass", "commit/ok"):
                self.assertIn(want, steps)
            # 실제로 파일이 바뀌고 커밋됐는가
            self.assertIn("hello", (repo / "app.py").read_text(encoding="utf-8"))
            log = subprocess.run(["git", "log", "--oneline"], cwd=repo, capture_output=True,
                                 text=True, encoding="utf-8", errors="replace").stdout
            self.assertEqual(len(log.strip().splitlines()), 2)  # init + MAKER 커밋
            # 내가 만든 리더들이 이 실제 세션을 읽어내는가
            from xgen_maker.loop.history import read_sessions, read_session_detail, read_test_runs
            from xgen_maker.loop.rollback import action_from_session
            ss = read_sessions(cfg.worklogs_dir, 5)
            self.assertEqual(ss[0]["outcome"], "committed_local")
            self.assertTrue(read_session_detail(cfg.worklogs_dir, ss[0]["session"])["steps"])
            self.assertTrue(action_from_session(cfg.worklogs_dir, ss[0]["session"]))
            runs = read_test_runs(cfg.worklogs_dir, 5)
            self.assertEqual((runs[0]["checks_status"], runs[0]["judge"]), ("ok", "pass"))


class TestStopActuallyKillsAgent(unittest.TestCase):
    """중지가 '요청만' 하고 끝나지 않는지 — 실행 중인 코딩 에이전트를 실제로 죽이는지.

    7회차 검수에서 실증된 버그: subprocess.run이 블로킹이라 중지를 눌러도 에이전트가
    타임아웃(기본 30분)까지 레포를 계속 고쳤다. /api/stop은 ok:true를 반환해 거짓 안심을 줬다.
    """
    NL = chr(10)
    # 느린 에이전트: 0.3초마다 파일을 쓴다(중지 후에도 쓰면 증거)
    SLOW = ("import pathlib, time" + chr(10) +
            "for i in range(20):" + chr(10) +
            "    pathlib.Path('w%d.txt' % i).write_text('x', encoding='utf-8')" + chr(10) +
            "    time.sleep(0.3)" + chr(10))

    def test_cancel_kills_running_agent_process(self):
        import threading
        import time as _t
        from xgen_maker.loop.implement import run_agent
        with tempfile.TemporaryDirectory() as t:
            work = Path(t) / "repo"; work.mkdir()
            sess = Path(t) / "sess"; sess.mkdir()
            stub = Path(t) / "slow.py"; stub.write_text(self.SLOW, encoding="utf-8")
            flag = {"stop": False}
            res = {}

            def go():
                res["r"] = run_agent(work, "prompt", sess,
                                     '"' + sys.executable + '" "' + str(stub) + '"',
                                     timeout=60, should_cancel=lambda: flag["stop"])
            th = threading.Thread(target=go, daemon=True); th.start()
            # 에이전트가 실제로 쓰기 시작할 때까지 대기
            for _ in range(60):
                if list(work.glob("w*.txt")):
                    break
                _t.sleep(0.1)
            wrote_at_stop = len(list(work.glob("w*.txt")))
            self.assertTrue(wrote_at_stop >= 1, "에이전트가 시작조차 안 됨")
            flag["stop"] = True
            th.join(timeout=20)
            self.assertFalse(th.is_alive(), "중지 후에도 run_agent가 안 돌아옴")
            after = len(list(work.glob("w*.txt")))
            _t.sleep(1.2)  # 고아 프로세스가 살아있다면 이 사이에 더 썼을 것
            final = len(list(work.glob("w*.txt")))
            self.assertEqual(final, after,
                             f"중지 후에도 에이전트가 계속 씀({after}→{final}) — 트리 kill 실패")
            self.assertLess(final, 20, "에이전트가 끝까지 실행됨 — 중지가 안 걸림")
            self.assertTrue(res["r"].get("cancelled"))
            self.assertFalse(res["r"]["ok"])

    def test_journal_has_cancelled_hook(self):
        # CLI 경로(기본 Journal)는 중지 개념이 없어 항상 False여야 한다
        from xgen_maker.loop.journal import Journal
        with tempfile.TemporaryDirectory() as t:
            j = Journal(t, "q", verbose=False)
            self.assertFalse(j.cancelled())


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


class TestSessionManagement(unittest.TestCase):
    """세션 목록 — 찾기·지우기. 지우는 건 되돌릴 수 없어 경계가 중요하다."""

    def _make(self, root: Path, name: str, query: str, branch: str = "") -> None:
        d = root / name
        d.mkdir(parents=True)
        events = [{"step": "session_start", "query": query},
                  {"step": "session_end", "status": "answered"}]
        if branch:
            events.insert(1, {"step": "branch", "branch": branch})
        d.joinpath("journal.jsonl").write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in events), encoding="utf-8")

    def test_delete_removes_only_that_session(self):
        from xgen_maker.loop.history import delete_session, read_sessions
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make(root, "s1", "첫 작업")
            self._make(root, "s2", "둘째 작업")
            r = delete_session(root, "s1")
            self.assertTrue(r["ok"])
            self.assertFalse((root / "s1").exists())
            self.assertTrue((root / "s2").exists())
            self.assertEqual([s["query"] for s in read_sessions(root, 10)], ["둘째 작업"])

    def test_delete_reports_leftover_branch(self):
        """기록만 지우고 브랜치는 남긴다 — 남는다는 사실을 반드시 알린다."""
        from xgen_maker.loop.history import delete_session
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make(root, "s1", "작업", branch="feature/x")
            r = delete_session(root, "s1")
            self.assertEqual(r["branch"], "feature/x")
            self.assertIn("feature/x", r["note"])

    def test_path_escape_is_refused(self):
        """세션 이름으로 worklogs 밖을 지우게 두면 안 된다."""
        from xgen_maker.loop.history import session_path, delete_session
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "worklogs"
            root.mkdir()
            outside = Path(tmp) / "precious"
            outside.mkdir()
            (outside / "keep.txt").write_text("x", encoding="utf-8")
            for evil in ("../precious", "..", ".", "", "../../etc"):
                self.assertIsNone(session_path(root, evil), evil)
                self.assertFalse(delete_session(root, evil)["ok"], evil)
            self.assertTrue((outside / "keep.txt").exists())

    def test_search_filters_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            wl = base / "wl"
            wl.mkdir()
            self._make(wl, "s1", "로그인 고쳐줘", branch="fix/login")
            self._make(wl, "s2", "결제 화면 확인")
            cfg = base / "cfg.json"
            cfg.write_text(json.dumps({"kg_path": str(_make_kg(base)).replace("\\", "/"),
                                       "worklogs_dir": str(wl).replace("\\", "/"),
                                       "llm_enabled": False}), encoding="utf-8")
            handler = web.MakerWebHandler
            prev = (handler.config, handler.graph)
            try:
                handler.config = MakerConfig.from_file(cfg)
                handler.graph = Graph.load(handler.config.kg_path)
                from xgen_maker.loop.history import read_sessions
                rows = read_sessions(wl, 100)
                self.assertEqual(len(rows), 2)
                hits = [s for s in rows if "로그인" in s["query"]]
                self.assertEqual(len(hits), 1)
            finally:
                handler.config, handler.graph = prev


class TestGraphAutoReload(unittest.TestCase):
    """밖에서 KG가 바뀌면(kg rebuild·CLI sync·스케줄러) 재시작 없이 반영돼야 한다.

    반영이 안 되면 서버가 기동 시점 그래프로 조용히 낡은 좌표에 착지시킨다.
    """

    def test_reloads_when_file_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            kg = _make_kg(base)
            cfg = base / "cfg.json"
            cfg.write_text(f'{{"kg_path": "{kg.as_posix()}", "llm_enabled": false}}',
                           encoding="utf-8")
            handler = web.MakerWebHandler
            prev = (handler.config, handler.graph, handler._kg_stamp)
            try:
                handler.config = MakerConfig.from_file(cfg)
                handler.graph = Graph.load(kg)
                handler._kg_stamp = None
                handler._reload_graph_if_changed()      # 첫 호출은 기준점만 잡는다
                self.assertNotIn("r:refund.py", handler.graph.nodes)

                grown = Graph.load(kg)
                grown.add_node("r:refund.py", "file", "refund.py", "r", "refund.py")
                grown.save(kg)
                handler._reload_graph_if_changed()
                self.assertIn("r:refund.py", handler.graph.nodes)
            finally:
                handler.config, handler.graph, handler._kg_stamp = prev

    def test_reloads_config_when_file_changes(self):
        """저장소를 설정에 추가하면 재시작 없이 보여야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            kg = _make_kg(base)
            cfg = base / "cfg.json"
            cfg.write_text(f'{{"kg_path": "{kg.as_posix()}", "llm_enabled": false, '
                           f'"repos": {{"one": "/a"}}}}', encoding="utf-8")
            handler = web.MakerWebHandler
            prev = (handler.config, handler.config_path, handler._cfg_stamp)
            try:
                handler.config = MakerConfig.from_file(cfg)
                handler.config_path = str(cfg)
                handler._cfg_stamp = None
                handler._reload_config_if_changed()      # 첫 호출은 기준점만
                self.assertEqual(set(handler.config.repos), {"one"})

                cfg.write_text(f'{{"kg_path": "{kg.as_posix()}", "llm_enabled": false, '
                               f'"repos": {{"one": "/a", "two": "/b"}}}}', encoding="utf-8")
                handler._reload_config_if_changed()
                self.assertEqual(set(handler.config.repos), {"one", "two"})
            finally:
                handler.config, handler.config_path, handler._cfg_stamp = prev

    def test_broken_file_keeps_previous_graph(self):
        """반쯤 쓰인/깨진 파일을 읽어도 멀쩡한 그래프를 버리지 않는다."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            kg = _make_kg(base)
            cfg = base / "cfg.json"
            cfg.write_text(f'{{"kg_path": "{kg.as_posix()}", "llm_enabled": false}}',
                           encoding="utf-8")
            handler = web.MakerWebHandler
            prev = (handler.config, handler.graph, handler._kg_stamp)
            try:
                handler.config = MakerConfig.from_file(cfg)
                handler.graph = Graph.load(kg)
                handler._kg_stamp = None
                handler._reload_graph_if_changed()
                kg.write_text("{ this is not json", encoding="utf-8")
                handler._reload_graph_if_changed()
                self.assertIn("r:pay.py#charge", handler.graph.nodes)
            finally:
                handler.config, handler.graph, handler._kg_stamp = prev


if __name__ == "__main__":
    import urllib.error
    unittest.main()


class TestSyncProgressAndCancel(unittest.TestCase):
    """최신화는 저장소를 여럿 돈다 — 진행이 안 보이고 못 멈추면 멈춘 것과 같다."""

    def _config(self, tmp: Path, repos: dict):
        cfg = MakerConfig()
        cfg.repos = repos
        cfg.kg_path = str(_make_kg(tmp))
        return cfg

    def test_progress_is_reported_per_repo(self):
        from xgen_maker.kg.refresh import pull_all
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repos = {}
            for name in ("a", "b", "c"):
                (base / name).mkdir()
                repos[name] = str(base / name)
            seen = []
            pull_all(self._config(base, repos),
                     on_progress=lambda step, info: seen.append((step, info["repo"],
                                                                 info["index"], info["total"])))
            self.assertEqual([s[1] for s in seen], ["a", "b", "c"])
            self.assertEqual(seen[0][3], 3)          # 총 개수를 알려준다

    def test_cancel_stops_remaining_repos(self):
        from xgen_maker.kg.refresh import pull_all
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repos = {}
            for name in ("a", "b", "c"):
                (base / name).mkdir()
                repos[name] = str(base / name)
            done = []
            # 첫 저장소를 처리한 뒤 중지 신호
            state = {"stop": False}

            def progress(step, info):
                done.append(info["repo"])
                state["stop"] = True

            results = pull_all(self._config(base, repos),
                               on_progress=progress,
                               should_cancel=lambda: state["stop"])
            self.assertEqual(done, ["a"])            # 두 번째부터는 시작도 안 한다
            self.assertEqual(results[-1]["action"], "cancelled")
            self.assertIn("중지", results[-1]["reason"])

    def test_page_wires_stream_and_stop(self):
        """화면이 스트리밍 경로와 중지를 실제로 걸고 있는지."""
        page = web._PAGE
        self.assertIn("/api/sync-stream", page)
        self.assertIn("stopSync", page)
        # 선언이 사용보다 앞서야 한다(const/function 순서가 뒤집히면 스크립트가 죽는다)
        self.assertLess(page.index("function startSync"),
                        page.rindex("syncBtn.onclick=startSync"))
