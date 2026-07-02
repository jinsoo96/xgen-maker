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
