import tempfile
import unittest
from pathlib import Path

from xgen_maker.kg.graph import Graph
from xgen_maker.kg.build import build_repo, merge_and_link, refresh_files
from xgen_maker.kg.search import search, impact
from xgen_maker.kg.routes_nextjs import route_from_rel
from xgen_maker.kg.crossrepo import link_api_calls

PY_SOURCE = '''from fastapi import APIRouter
from svc import util

router = APIRouter(prefix="/users")


@router.get("/{user_id}")
async def get_user(user_id):
    return util.load(user_id)


def helper():
    return 1


def caller():
    return helper()
'''

TS_SOURCE = """import { load } from './util'

export function UserList() {
  return api.get(`/api/users/${userId}`)
}

export class UserStore {}
"""


def make_py_repo(root: Path) -> None:
    (root / "svc").mkdir(parents=True)
    (root / "svc" / "__init__.py").write_text("", encoding="utf-8")
    (root / "svc" / "util.py").write_text("def load(x):\n    return x\n", encoding="utf-8")
    (root / "api.py").write_text(PY_SOURCE, encoding="utf-8")


def make_ts_repo(root: Path) -> None:
    app = root / "apps" / "web" / "src" / "app" / "(main)" / "users"
    app.mkdir(parents=True)
    (app / "page.tsx").write_text(TS_SOURCE, encoding="utf-8")
    (root / "apps" / "web" / "src" / "app" / "(main)" / "users" / "util.ts").write_text(
        "export const load = () => 1\n", encoding="utf-8")


class TestGraph(unittest.TestCase):
    def test_roundtrip_and_merge(self):
        g1 = Graph()
        g1.add_node("r:a.py", "file", "a.py", "r", "a.py")
        g1.add_node("r:a.py#f", "function", "f", "r", "a.py", 3)
        g1.add_edge("r:a.py", "r:a.py#f", "contains")
        g2 = Graph()
        g2.add_node("r2:b.ts", "file", "b.ts", "r2", "b.ts")
        g1.merge(g2)
        self.assertEqual(len(g1.nodes), 3)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "g.json"
            g1.save(path)
            loaded = Graph.load(path)
        self.assertEqual(set(loaded.nodes), set(g1.nodes))
        self.assertEqual(len(loaded.edges), 1)

    def test_edge_dedup(self):
        g = Graph()
        g.add_node("a", "file", "a", "r")
        g.add_node("b", "file", "b", "r")
        g.add_edge("a", "b", "imports")
        g.add_edge("a", "b", "imports")
        self.assertEqual(len(g.edges), 1)


class TestPythonExtract(unittest.TestCase):
    def test_endpoints_imports_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_py_repo(root)
            graph = build_repo("be", root)
        endpoints = graph.nodes_by_kind("endpoint")
        self.assertEqual(len(endpoints), 1)
        self.assertEqual(endpoints[0]["meta"]["method"], "GET")
        self.assertEqual(endpoints[0]["meta"]["route_path"], "/users/{user_id}")
        self.assertIn("be:api.py#get_user", graph.nodes)
        self.assertIn("be:api.py#caller", graph.nodes)
        import_edges = [e for e in graph.edges if e["kind"] == "imports"]
        self.assertTrue(any(e["dst"] == "be:svc/util.py" for e in import_edges))
        call_edges = [e for e in graph.edges if e["kind"] == "calls"
                      and e["dst"] == "be:api.py#helper"]
        self.assertTrue(call_edges)


class TestTsExtractAndRoutes(unittest.TestCase):
    def test_exports_apicalls_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_ts_repo(root)
            graph = build_repo("fe", root)
        names = {n["name"] for n in graph.nodes.values()}
        self.assertIn("UserList", names)
        self.assertIn("UserStore", names)
        calls = graph.nodes_by_kind("api_call")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["meta"]["norm_path"], "/api/users/*")
        routes = graph.nodes_by_kind("route")
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["name"], "/users")

    def test_routes_survive_incremental_refresh(self):
        """증분 갱신에서도 화면 라우트가 나와야 한다.

        회귀: Rust 추출기를 붙이며 TS 파일 수집이 Rust 분기로 옮겨가, page.tsx가
        라우트 추출 대상에서 통째로 빠졌다(전체 빌드·증분 양쪽).
        """
        from xgen_maker.kg.build import refresh_files
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_ts_repo(root)
            graph = build_repo("fe", root)
            self.assertTrue(graph.nodes_by_kind("route"))

            page = root / "src" / "app" / "settings" / "page.tsx"
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text("export default function Settings(){return null}\n",
                            encoding="utf-8")
            refresh_files(graph, "fe", root, {"src/app/settings/page.tsx"})
        self.assertIn("/settings", {n["name"] for n in graph.nodes_by_kind("route")})

    def test_route_from_rel(self):
        self.assertEqual(route_from_rel("apps/web/src/app/(main)/dashboard/page.tsx"),
                         "/dashboard")
        self.assertEqual(route_from_rel("src/app/page.tsx"), "/")
        self.assertEqual(route_from_rel("src/app/chat/[id]/page.tsx"), "/chat/[id]")
        self.assertIsNone(route_from_rel("src/components/page-header.tsx"))


class TestCrossRepo(unittest.TestCase):
    def test_link_with_gateway_prefix_and_wildcard(self):
        graph = Graph()
        graph.add_node("be:api.py#EP GET /users/{user_id}", "endpoint", "GET /users/{user_id}",
                       "be", "api.py", 5, method="GET", route_path="/users/{user_id}")
        graph.add_node("fe:page.tsx#CALL GET /api/users/*", "api_call", "GET /api/users/*",
                       "fe", "page.tsx", 4, method="GET", norm_path="/api/users/*")
        graph.add_node("fe:other.tsx#CALL POST /api/users/*", "api_call", "POST /api/users/*",
                       "fe", "other.tsx", 4, method="POST", norm_path="/api/users/*")
        links = link_api_calls(graph)
        self.assertEqual(links, 1)
        resolve = [e for e in graph.edges if e["kind"] == "resolves_to"]
        self.assertEqual(resolve[0]["src"], "fe:page.tsx#CALL GET /api/users/*")

    def test_full_pipeline_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            be, fe = root / "be", root / "fe"
            make_py_repo(be := root / "be")
            make_ts_repo(fe := root / "fe")
            g_be = build_repo("be", be)
            g_fe = build_repo("fe", fe)
            merged, links = merge_and_link([g_be, g_fe])
        self.assertEqual(links, 1)


class TestSearchImpact(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        make_py_repo(root)
        self.graph = build_repo("be", root)
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def test_search_finds_endpoint(self):
        hits = search(self.graph, "get_user")
        self.assertTrue(hits)
        self.assertTrue(any("get_user" in h["name"] for h in hits[:3]))

    def test_named_repo_wins(self):
        """저장소를 지목하면 그 저장소가 이긴다.

        회귀: 같은 이름의 함수가 여러 저장소에 있을 때(health, login 같은 흔한 이름)
        "게이트웨이의 health"라고 말해도 엉뚱한 저장소로 착지했다 — 점수에 repo가
        아예 반영되지 않았다.
        """
        from xgen_maker.kg.graph import Graph
        graph = Graph()
        for repo in ("alpha-gateway", "beta-worker"):
            graph.add_node(repo, "repo", repo, repo, f"/{repo}")
            graph.add_node(f"{repo}:h.py#health", "function", "health", repo, "h.py", 1)
        top = search(graph, "alpha-gateway health", k=1)[0]
        self.assertEqual(top["repo"], "alpha-gateway")

    def test_identifier_is_split_into_words(self):
        """붙여 쓴 식별자를 단어로 되돌려야 검색이 닿는다(사전이 아니라 문자열 규칙)."""
        from xgen_maker.kg.search import tokenize
        self.assertIn("collections", tokenize("@xgen/main-tool-management-api-collections"))
        self.assertIn("api", tokenize("listApiCollections"))
        self.assertIn("user", tokenize("user_id"))

    def test_rare_word_beats_common_word(self):
        """흔한 단어는 저절로 약해져야 한다.

        회귀: 점수가 손으로 정한 상수의 합이라 "api"(수천 노드)와 "collections"(몇 개)를
        같게 봤다. 그래서 "api 도구 목록" 같은 요청이 온갖 백엔드를 끌어왔다.
        """
        from xgen_maker.kg.graph import Graph
        graph = Graph()
        graph.add_node("r", "repo", "r", "r", "/r")
        for i in range(40):                       # "api"는 흔하다
            graph.add_node(f"r:api{i}.py#h", "function", f"api_handler_{i}", "r", f"api{i}.py", 1)
        graph.add_node("r:x.py#c", "function", "api_collections_card", "r", "x.py", 1)
        top = search(graph, "api collections", k=1)[0]
        self.assertEqual(top["name"], "api_collections_card")

    def test_no_score_threshold_hides_results(self):
        """점수 임계값으로 자르지 않는다 — 코퍼스마다 점수 범위가 달라 임의로 자르면
        작은 저장소에서 전부 사라진다."""
        from xgen_maker.kg.graph import Graph
        graph = Graph()
        graph.add_node("r", "repo", "r", "r", "/r")
        graph.add_node("r:a.py#f", "function", "widget", "r", "a.py", 1)
        self.assertTrue(search(graph, "widget", k=5))

    def test_exact_identifier_wins(self):
        """식별자를 그대로 치면 그 노드가 1등이어야 한다."""
        from xgen_maker.kg.graph import Graph
        graph = Graph()
        graph.add_node("r", "repo", "r", "r", "/r")
        graph.add_node("r:a.py#g", "function", "get_user", "r", "a.py", 1)
        graph.add_node("r:b.py#g2", "function", "get_user_sessions_by_id", "r", "b.py", 1)
        graph.add_node("r:c.py#g3", "endpoint", "GET /user", "r", "c.py", 1,
                       method="GET", route_path="/user")
        self.assertEqual(search(graph, "get_user", k=1)[0]["name"], "get_user")

    def test_search_uses_semantic_layer(self):
        """enrich가 채운 요약을 검색이 봐야 한다 — 안 보면 그래프가 아는 걸 검색이 모른다."""
        from xgen_maker.kg.graph import Graph
        graph = Graph()
        graph.add_node("r", "repo", "r", "r", "/r")
        graph.add_node("r:a.py#f1", "function", "f1", "r", "a.py", 1,
                       summary="결제 취소를 처리하는 핸들러")
        graph.add_node("r:b.py#f2", "function", "f2", "r", "b.py", 1,
                       summary="이미지 썸네일 생성")
        top = search(graph, "결제 취소", k=1)
        self.assertEqual(top[0]["id"], "r:a.py#f1")

    def test_token_cache_does_not_leak_into_saved_graph(self):
        """검색 캐시가 노드에 들어가면 저장이 깨진다(set은 JSON이 못 담는다)."""
        from xgen_maker.kg.graph import Graph
        with tempfile.TemporaryDirectory() as tmp:
            graph = Graph()
            graph.add_node("r", "repo", "r", "r", "/r")
            graph.add_node("r:a.py#f", "function", "f", "r", "a.py", 1)
            search(graph, "anything")
            out = Path(tmp) / "g.json"
            graph.save(out)                      # 예외 없이 저장돼야 한다
            reloaded = Graph.load(out)
            self.assertTrue(all("_tok" not in n for n in reloaded.nodes.values()))

    def test_impact_traverses_reverse(self):
        affected = impact(self.graph, "be:svc/util.py", depth=3)
        ids = {n["id"] for n in affected}
        self.assertIn("be:api.py", ids)

    def test_refresh_files(self):
        target = self.root / "api.py"
        target.write_text(PY_SOURCE.replace("def helper():", "def helper_renamed():")
                          .replace("return helper()", "return helper_renamed()"),
                          encoding="utf-8")
        refresh_files(self.graph, "be", self.root, ["api.py"])
        self.assertIn("be:api.py#helper_renamed", self.graph.nodes)
        self.assertNotIn("be:api.py#helper", self.graph.nodes)


if __name__ == "__main__":
    unittest.main()


class TestGraphSource(unittest.TestCase):
    """그래프를 어느 코드로 만드는가 — 워킹트리는 사람의 작업 브랜치라 뒤처져 있다."""

    def _repo(self, root: Path) -> None:
        import subprocess
        root.mkdir(parents=True)
        run = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True,
                                        capture_output=True)
        run("init", "-q")
        run("config", "user.email", "t@t")
        run("config", "user.name", "t")
        (root / "base.py").write_text("def shared():\n    return 1\n", encoding="utf-8")
        run("add", "-A")
        run("commit", "-qm", "base")
        run("branch", "-M", "develop")
        # develop에만 있는 파일
        (root / "only_on_develop.py").write_text("def newest():\n    return 2\n",
                                                 encoding="utf-8")
        run("add", "-A")
        run("commit", "-qm", "develop only")
        # 사람은 뒤처진 작업 브랜치를 체크아웃해 둔다
        run("checkout", "-q", "-b", "my/work", "HEAD~1")

    def test_ref_sees_files_the_worktree_does_not(self):
        from xgen_maker.kg.source import open_source
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "r"
            self._repo(root)
            self.assertFalse((root / "only_on_develop.py").exists())

            worktree = build_repo("r", root)
            from_ref = build_repo("r", root, ref="develop")
        names = lambda g: {n["name"] for n in g.nodes_by_kind("function")}
        self.assertNotIn("newest", names(worktree))   # 체크아웃 기준이면 안 보인다
        self.assertIn("newest", names(from_ref))      # 통합 브랜치 기준이면 보인다
        self.assertIn("shared", names(from_ref))
        self.assertEqual(from_ref.meta["ref"], "develop")

    def test_reading_from_ref_never_touches_the_worktree(self):
        """최신을 보겠다고 남의 체크아웃을 바꾸면 안 된다."""
        import subprocess
        from xgen_maker.kg.source import open_source
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "r"
            self._repo(root)
            before = subprocess.run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
                                    capture_output=True, text=True).stdout.strip()
            build_repo("r", root, ref="develop")
            after = subprocess.run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
                                   capture_output=True, text=True).stdout.strip()
            self.assertEqual(before, after)
            self.assertFalse((root / "only_on_develop.py").exists())

    def test_unknown_ref_falls_back_to_worktree(self):
        """받은 적 없는 ref를 주면 그래프를 비우지 말고 있는 것으로 만든다."""
        from xgen_maker.kg.source import open_source, WorktreeSource
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "r"
            self._repo(root)
            self.assertIsInstance(open_source(root, "origin/nope"), WorktreeSource)
            graph = build_repo("r", root, ref="origin/nope")
            self.assertIn("shared", {n["name"] for n in graph.nodes_by_kind("function")})
