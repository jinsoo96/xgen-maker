import unittest

from xgen_maker.kg.graph import Graph
from xgen_maker.kg.search import retrieve_chain


def order_graph() -> Graph:
    g = Graph()
    g.add_node("fe:page.tsx", "file", "page.tsx", "fe", "page.tsx")
    g.add_node("fe:page.tsx#CALL GET /api/orders/*", "api_call", "GET /api/orders/*",
               "fe", "page.tsx", method="GET", norm_path="/api/orders/*")
    g.add_node("be:orders.py#EP GET /orders/*", "endpoint", "GET /orders/{id}",
               "be", "orders.py", method="GET", route_path="/orders/{id}")
    g.add_node("be:orders.py#cancel", "function", "cancel_order", "be", "orders.py", 20)
    g.add_edge("fe:page.tsx", "fe:page.tsx#CALL GET /api/orders/*", "contains")
    g.add_edge("fe:page.tsx#CALL GET /api/orders/*", "be:orders.py#EP GET /orders/*",
               "resolves_to")
    g.add_edge("be:orders.py#EP GET /orders/*", "be:orders.py#cancel", "calls")
    return g


class TestRetrieveChain(unittest.TestCase):
    def test_expands_frontend_call_to_backend_endpoint(self):
        g = order_graph()
        result = retrieve_chain(g, "page.tsx", k=3, hops=2)
        self.assertTrue(result["seeds"])
        names = {c["name"] for c in result["chain"] if c["hop"] > 0}
        self.assertIn("GET /orders/{id}", names)  # resolves_to 확장
        self.assertIn("resolves_to", result["by_relation"])

    def test_hops_limit(self):
        g = order_graph()
        one_hop = retrieve_chain(g, "page.tsx", k=3, hops=1)
        two_hop = retrieve_chain(g, "page.tsx", k=3, hops=2)
        names1 = {c["name"] for c in one_hop["chain"] if c["hop"] > 0}
        names2 = {c["name"] for c in two_hop["chain"] if c["hop"] > 0}
        # cancel_order 는 endpoint에서 calls로 2-hop 위치
        self.assertNotIn("cancel_order", names1)
        self.assertIn("cancel_order", names2)

    def test_empty_query_no_crash(self):
        g = order_graph()
        result = retrieve_chain(g, "nonexistent_zzz", k=3)
        self.assertEqual(result["seeds"], [])
        self.assertEqual(result["chain"], [])

    def test_rrf_fuses_text_and_graph(self):
        g = order_graph()
        result = retrieve_chain(g, "page.tsx", k=3, hops=2)
        # 융합 점수가 내림차순 정렬돼 있어야 함
        rrfs = [c["rrf"] for c in result["chain"]]
        self.assertEqual(rrfs, sorted(rrfs, reverse=True))


if __name__ == "__main__":
    unittest.main()
