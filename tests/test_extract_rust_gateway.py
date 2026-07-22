"""Rust 추출기 + 게이트웨이 라우팅 테이블 — 착지 좌표와 호출 경로가 실제로 나오는지."""
import tempfile
import unittest
from pathlib import Path

from xgen_maker.kg.build import build_repo, merge_and_link
from xgen_maker.kg.graph import Graph
from xgen_maker.kg.extract_gateway import link_gateway_routes

RUST_MAIN = """\
//! 관문 서비스.
use axum::Router;
use crate::routes::security;

pub struct GatewayConfig {
    pub port: u16,
}

impl GatewayConfig {
    pub fn from_env() -> Self {
        // "fn not_a_function" — 주석 속 선언은 잡지 않는다
        Self { port: 8080 }
    }
}

pub fn build_router() -> Router {
    Router::new()
        .route("/auth/login", post(routes::security::login))
        .route("/health", get(routes::health::health))
        .route("/:service/*tail", any(routes::proxy::proxy_stub))
}
"""

RUST_SECURITY = """\
pub async fn login() -> &'static str { "ok" }
pub async fn refresh() -> &'static str { "ok" }
"""

SERVICES_YAML = """\
base_path: /api
services:
  core-service:
    host: http://backend-one:8000
    modules:
      - admin
      - auth
  other-service:
    host: http://backend-two:8000
    modules:
      - docs
  missing-service:
    host: http://not-in-graph:8000
    modules:
      - ghost
"""


def _make_rust_repo(root: Path) -> None:
    (root / "src" / "routes").mkdir(parents=True)
    (root / "src" / "main.rs").write_text(RUST_MAIN, encoding="utf-8")
    (root / "src" / "routes" / "security.rs").write_text(RUST_SECURITY, encoding="utf-8")
    (root / "config").mkdir()
    (root / "config" / "services.yaml").write_text(SERVICES_YAML, encoding="utf-8")


class TestRustExtract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        _make_rust_repo(root)
        self.graph = build_repo("gw", root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_functions_and_impl_methods(self):
        names = {n["name"] for n in self.graph.nodes_by_kind("function")}
        self.assertIn("build_router", names)
        self.assertIn("GatewayConfig::from_env", names)   # impl 메서드는 타입으로 한정
        self.assertIn("login", names)
        self.assertNotIn("not_a_function", names)         # 주석 속 선언

    def test_struct_becomes_class(self):
        self.assertIn("GatewayConfig", {n["name"] for n in self.graph.nodes_by_kind("class")})

    def test_endpoints_use_shared_meta_key(self):
        """crossrepo가 meta['route_path']로 매칭한다 — 키 이름이 다르면 조용히 안 붙는다."""
        eps = self.graph.nodes_by_kind("endpoint")
        paths = {n["meta"]["route_path"] for n in eps}
        self.assertIn("/auth/login", paths)
        self.assertTrue(all("route_path" in n["meta"] for n in eps))

    def test_route_links_to_handler_in_another_file(self):
        login = next(n for n in self.graph.nodes_by_kind("function") if n["name"] == "login")
        edges = [e for e in self.graph.edges if e["kind"] == "route_of" and e["dst"] == login["id"]]
        self.assertTrue(edges, "라우트가 다른 파일의 핸들러로 연결돼야 한다")

    def test_line_numbers_land(self):
        login = next(n for n in self.graph.nodes_by_kind("function") if n["name"] == "login")
        source = (Path(self.tmp.name) / login["path"]).read_text(encoding="utf-8").splitlines()
        self.assertIn("login", source[login["line"] - 1])


class TestGatewayRoutingTable(unittest.TestCase):
    def test_call_reaches_backend_through_gateway(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_rust_repo(root)
            gw = build_repo("gw", root)

        backend = Graph()
        backend.add_node("backend-one", "repo", "backend-one", "backend-one", "/x")
        caller = Graph()
        caller.add_node("fe", "repo", "fe", "fe", "/y")
        caller.add_node("fe:call", "api_call", "GET /api/admin/list", "fe", "a.ts", 1,
                        method="GET", norm_path="/api/admin/list")

        merged, _ = merge_and_link([gw, backend, caller])
        routes = {n["meta"]["prefix"] for n in merged.nodes_by_kind("gateway_route")}
        self.assertIn("/api/admin", routes)

        via = [e for e in merged.edges if e["kind"] == "routes_via" and e["src"] == "fe:call"]
        self.assertEqual(len(via), 1)
        serves = [e for e in merged.edges if e["kind"] == "serves" and e["src"] == via[0]["dst"]]
        self.assertEqual([e["dst"] for e in serves], ["backend-one"])

    def test_unknown_backend_is_not_linked(self):
        """그래프에 없는 서비스로는 잇지 않는다 — 틀린 연결은 없는 것만 못하다."""
        graph = Graph()
        graph.add_node("gw", "repo", "gw", "gw", "/x")
        graph.add_node("gw:gwroute:/api/ghost", "gateway_route", "/api/ghost", "gw",
                       "config/services.yaml", service="missing", host="not-in-graph",
                       module="ghost", prefix="/api/ghost")
        result = link_gateway_routes(graph)
        self.assertEqual(result["serves"], 0)

    def test_longest_prefix_wins(self):
        graph = Graph()
        graph.add_node("gw", "repo", "gw", "gw", "/x")
        for prefix in ("/api/a", "/api/a/b"):
            graph.add_node(f"gw:gwroute:{prefix}", "gateway_route", prefix, "gw", "c.yaml",
                           service="s", host="h", module="m", prefix=prefix)
        graph.add_node("fe:call", "api_call", "GET /api/a/b/c", "fe", "a.ts", 1,
                       method="GET", norm_path="/api/a/b/c")
        link_gateway_routes(graph)
        via = [e for e in graph.edges if e["kind"] == "routes_via"]
        self.assertEqual([e["dst"] for e in via], ["gw:gwroute:/api/a/b"])


if __name__ == "__main__":
    unittest.main()
