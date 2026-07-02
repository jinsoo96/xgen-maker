import tempfile
import unittest
from pathlib import Path

from xgen_maker.kg.graph import Graph
from xgen_maker.kg.build import build_repo
from xgen_maker.kg.workspaces import scan_workspaces, scan_aliases, ImportResolver
from xgen_maker.kg.enrich import enrich_deterministic, enrich_llm, deterministic_summary
from xgen_maker.kg.domains import build_domains, trace_flow, render_domain_map
from xgen_maker.kg.tour import reading_order, render_tour


def make_monorepo(root: Path) -> None:
    (root / "package.json").write_text('{"name": "root-monorepo"}', encoding="utf-8")
    widget = root / "features" / "chat-widget"
    (widget / "src").mkdir(parents=True)
    (widget / "package.json").write_text('{"name": "@xgen/chat-widget"}', encoding="utf-8")
    (widget / "src" / "index.ts").write_text(
        "/** 채팅 위젯 feature 진입점 */\nexport function ChatWidget() {\n"
        "  return api.get('/api/chat/history')\n}\n", encoding="utf-8")
    app = root / "apps" / "web"
    (app / "src" / "app" / "chat").mkdir(parents=True)
    (app / "src" / "components").mkdir(parents=True)
    (app / "tsconfig.json").write_text(
        '{\n  // comment allowed\n  "compilerOptions": {"baseUrl": ".", '
        '"paths": {"@/*": ["./src/*"],}},\n}', encoding="utf-8")
    (app / "src" / "components" / "header.tsx").write_text(
        "export function Header() { return null }\n", encoding="utf-8")
    (app / "src" / "app" / "chat" / "page.tsx").write_text(
        "import { Header } from '@/components/header'\n"
        "import { ChatWidget } from '@xgen/chat-widget'\n"
        "export default function Page() { return null }\n", encoding="utf-8")


class TestWorkspaces(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        make_monorepo(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_workspaces_and_aliases(self):
        workspaces = scan_workspaces(self.root)
        self.assertEqual(workspaces.get("@xgen/chat-widget"), "features/chat-widget")
        aliases = scan_aliases(self.root)
        self.assertTrue(any(prefix == "@/" for prefix, _ in aliases))

    def test_resolver_alias_and_workspace(self):
        graph = build_repo("fe", self.root)
        known = {n["path"] for n in graph.nodes.values() if n["kind"] == "file"}
        resolver = ImportResolver(self.root, known,
                                  scan_workspaces(self.root), scan_aliases(self.root))
        hit = resolver.resolve("@/components/header", "apps/web/src/app/chat/page.tsx")
        self.assertEqual(hit, ("file", "apps/web/src/components/header.tsx"))
        hit = resolver.resolve("@xgen/chat-widget", "apps/web/src/app/chat/page.tsx")
        self.assertEqual(hit, ("feature", "@xgen/chat-widget"))

    def test_build_wires_alias_imports_and_features(self):
        graph = build_repo("fe", self.root)
        page_id = "fe:apps/web/src/app/chat/page.tsx"
        import_targets = {e["dst"] for e in graph.edges
                          if e["kind"] == "imports" and e["src"] == page_id}
        self.assertIn("fe:apps/web/src/components/header.tsx", import_targets)
        self.assertIn("fe:feature:@xgen/chat-widget", import_targets)
        feature = graph.nodes["fe:feature:@xgen/chat-widget"]
        self.assertEqual(feature["kind"], "feature")
        members = {e["dst"] for e in graph.edges
                   if e["kind"] == "contains" and e["src"] == feature["id"]}
        self.assertIn("fe:features/chat-widget/src/index.ts", members)

    def test_ts_doc_captured(self):
        graph = build_repo("fe", self.root)
        index = graph.nodes["fe:features/chat-widget/src/index.ts"]
        self.assertIn("채팅 위젯", index["meta"].get("doc", ""))


class TestEnrich(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "svc.py").write_text(
            '"""세션 관리 서비스."""\n\ndef save(x):\n    """세션 저장."""\n    return x\n',
            encoding="utf-8")
        self.graph = build_repo("be", self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_docstring_priority(self):
        node = self.graph.nodes["be:svc.py"]
        self.assertEqual(deterministic_summary(self.graph, node), "세션 관리 서비스.")

    def test_enrich_deterministic_fills_all(self):
        filled = enrich_deterministic(self.graph)
        self.assertEqual(filled, len(self.graph.nodes))
        func = self.graph.nodes["be:svc.py#save"]
        self.assertEqual(func["meta"]["summary"], "세션 저장.")

    def test_enrich_llm_with_stub(self):
        enrich_deterministic(self.graph)
        stub = lambda base, model, messages, **kw: {"summary": "LLM이 쓴 요약"}
        stats = enrich_llm(self.graph, "http://x", "m", {}, limit=10, chat_fn=stub)
        # 대상 kind(route/endpoint/feature/file) 중 이 그래프엔 file 1개뿐
        self.assertEqual(stats["llm_done"], 1)
        node = self.graph.nodes["be:svc.py"]
        self.assertEqual(node["meta"]["summary_src"], "llm")
        self.assertEqual(node["meta"]["summary"], "LLM이 쓴 요약")

    def test_enrich_llm_early_stop_when_down(self):
        stub = lambda base, model, messages, **kw: None
        stats = enrich_llm(self.graph, "http://x", "m", {}, limit=50, chat_fn=stub)
        self.assertEqual(stats["llm_done"], 0)
        self.assertLessEqual(stats["llm_failed"], 3)


class TestDomainsAndTour(unittest.TestCase):
    def _flow_graph(self) -> Graph:
        g = Graph()
        g.add_node("fe:route:/chat", "route", "/chat", "fe", "app/chat/page.tsx")
        g.add_node("fe:app/chat/page.tsx", "file", "page.tsx", "fe", "app/chat/page.tsx")
        g.add_node("fe:feature:@xgen/chat", "feature", "@xgen/chat", "fe", "features/chat")
        g.add_node("fe:features/chat/api.ts", "file", "api.ts", "fe", "features/chat/api.ts")
        g.add_node("fe:features/chat/api.ts#CALL GET /api/chat/history", "api_call",
                   "GET /api/chat/history", "fe", "features/chat/api.ts",
                   method="GET", norm_path="/api/chat/history")
        g.add_node("be:chat.py#EP GET /chat/history", "endpoint", "GET /chat/history",
                   "be", "chat.py", method="GET", route_path="/chat/history")
        g.add_edge("fe:route:/chat", "fe:app/chat/page.tsx", "route_of")
        g.add_edge("fe:app/chat/page.tsx", "fe:feature:@xgen/chat", "imports")
        g.add_edge("fe:feature:@xgen/chat", "fe:features/chat/api.ts", "contains")
        g.add_edge("fe:features/chat/api.ts",
                   "fe:features/chat/api.ts#CALL GET /api/chat/history", "contains")
        g.add_edge("fe:features/chat/api.ts#CALL GET /api/chat/history",
                   "be:chat.py#EP GET /chat/history", "resolves_to")
        return g

    def test_build_domains(self):
        g = self._flow_graph()
        g.add_node("fe:route:/admin/users", "route", "/admin/users", "fe", "x")
        created = build_domains(g)
        self.assertEqual(created, 2)  # chat, admin
        names = {n["name"] for n in g.nodes_by_kind("domain")}
        self.assertEqual(names, {"chat", "admin"})

    def test_trace_flow_reaches_endpoint(self):
        g = self._flow_graph()
        flow = trace_flow(g, "fe:route:/chat")
        self.assertIn("fe:feature:@xgen/chat", flow["features"])
        self.assertIn("be:chat.py#EP GET /chat/history", flow["endpoints"])

    def test_render_domain_map(self):
        g = self._flow_graph()
        build_domains(g)
        with tempfile.TemporaryDirectory() as tmp:
            out = render_domain_map(g, Path(tmp) / "map.html")
            content = out.read_text(encoding="utf-8")
        self.assertIn("/chat", content)
        self.assertIn("GET /chat/history", content)

    def test_reading_order_foundation_first(self):
        g = Graph()
        for name in ("a", "b", "c"):
            g.add_node(f"r:{name}.py", "file", f"{name}.py", "r", f"{name}.py")
        g.add_edge("r:a.py", "r:b.py", "imports")   # a → b → c 의존
        g.add_edge("r:b.py", "r:c.py", "imports")
        order = [step["path"] for step in reading_order(g, "r")]
        self.assertEqual(order, ["c.py", "b.py", "a.py"])

    def test_render_tour(self):
        g = self._flow_graph()
        with tempfile.TemporaryDirectory() as tmp:
            out = render_tour(g, "fe", Path(tmp) / "tour.md")
            content = out.read_text(encoding="utf-8")
        self.assertIn("가이드 투어", content)


if __name__ == "__main__":
    unittest.main()
