import json
import tempfile
import unittest
from pathlib import Path

from xgen_maker.kg.graph import Graph
from xgen_maker.mcp_server import KgMcpServer


def make_server(tmp: Path) -> KgMcpServer:
    graph = Graph()
    graph.add_node("be:api.py", "file", "api.py", "be", "api.py")
    graph.add_node("be:api.py#get_user", "function", "get_user", "be", "api.py", 5)
    graph.add_edge("be:api.py", "be:api.py#get_user", "contains")
    kg_path = tmp / "kg.json"
    graph.save(kg_path)
    return KgMcpServer(str(kg_path))


class TestMcpServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = make_server(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_initialize(self):
        response = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                       "params": {}})
        self.assertEqual(response["result"]["serverInfo"]["name"], "xgen-maker-kg")

    def test_tools_list(self):
        response = self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in response["result"]["tools"]}
        self.assertEqual(names, {"kg_search", "kg_node", "kg_impact", "kg_stats"})

    def test_tools_call_search(self):
        response = self.server.handle({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "kg_search", "arguments": {"query": "get_user"}}})
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertTrue(payload["results"])
        self.assertEqual(payload["results"][0]["name"], "get_user")

    def test_tools_call_node_and_stats(self):
        response = self.server.handle({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "kg_node", "arguments": {"id": "be:api.py"}}})
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["node"]["name"], "api.py")
        self.assertEqual(len(payload["edges"]), 1)

        response = self.server.handle({
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "kg_stats", "arguments": {}}})
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["nodes"], 2)
        self.assertEqual(payload["repos"], ["be"])

    def test_notification_ignored_and_unknown_method(self):
        self.assertIsNone(self.server.handle(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}))
        response = self.server.handle({"jsonrpc": "2.0", "id": 9, "method": "nope"})
        self.assertIn("error", response)


if __name__ == "__main__":
    unittest.main()
