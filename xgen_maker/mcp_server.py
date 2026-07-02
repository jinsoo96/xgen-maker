"""KG MCP 서버 (T1) — stdio JSON-RPC로 지식그래프를 MCP 툴로 노출.

의존성 0(stdlib). 하네스 ToolSource·Claude Code 등 MCP 클라이언트가 그대로 연결.
툴: kg_search / kg_node / kg_impact / kg_stats
"""
from __future__ import annotations

import json
import sys

from .kg.graph import Graph
from .kg.search import search, impact

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {"name": "kg_search",
     "description": "지식그래프에서 이름/경로/의미 기반으로 노드(파일·함수·클래스·엔드포인트·라우트)를 검색한다. 개발 착지점 특정용.",
     "inputSchema": {"type": "object", "required": ["query"],
                     "properties": {"query": {"type": "string"},
                                    "k": {"type": "integer", "default": 10},
                                    "kinds": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "kg_node",
     "description": "노드 id의 상세(메타·연결 엣지)를 조회한다.",
     "inputSchema": {"type": "object", "required": ["id"],
                     "properties": {"id": {"type": "string"}}}},
    {"name": "kg_impact",
     "description": "노드가 변경될 때 영향받는 노드들을 역방향 BFS로 반환한다(MR 영향분석).",
     "inputSchema": {"type": "object", "required": ["id"],
                     "properties": {"id": {"type": "string"},
                                    "depth": {"type": "integer", "default": 3}}}},
    {"name": "kg_stats",
     "description": "그래프 통계(노드/엣지 kind별 개수, 레포 목록).",
     "inputSchema": {"type": "object", "properties": {}}},
]


class KgMcpServer:
    def __init__(self, kg_path: str):
        self.graph = Graph.load(kg_path)

    def _call_tool(self, name: str, args: dict) -> dict:
        if name == "kg_search":
            kinds = tuple(args["kinds"]) if args.get("kinds") else None
            return {"results": search(self.graph, args["query"],
                                      k=int(args.get("k", 10)), kinds=kinds)}
        if name == "kg_node":
            node = self.graph.nodes.get(args["id"])
            if node is None:
                return {"error": f"노드 없음: {args['id']}"}
            edges = [{"dir": direction, **edge}
                     for direction, edge in self.graph.neighbors(args["id"])[:100]]
            return {"node": node, "edges": edges}
        if name == "kg_impact":
            return {"impact": impact(self.graph, args["id"], int(args.get("depth", 3)))}
        if name == "kg_stats":
            stats = self.graph.stats()
            stats["repos"] = sorted({n["repo"] for n in self.graph.nodes.values()})
            return stats
        return {"error": f"알 수 없는 툴: {name}"}

    def handle(self, message: dict) -> dict | None:
        method = message.get("method", "")
        msg_id = message.get("id")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "xgen-maker-kg", "version": "0.1.0"}}}
        if method.startswith("notifications/"):
            return None
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
        if method == "tools/call":
            params = message.get("params", {})
            try:
                result = self._call_tool(params.get("name", ""),
                                         params.get("arguments", {}) or {})
                content = [{"type": "text",
                            "text": json.dumps(result, ensure_ascii=False, default=str)}]
                return {"jsonrpc": "2.0", "id": msg_id,
                        "result": {"content": content, "isError": "error" in result}}
            except Exception as error:  # 툴 오류는 프로토콜 오류로 승격하지 않는다
                return {"jsonrpc": "2.0", "id": msg_id,
                        "result": {"content": [{"type": "text", "text": str(error)}],
                                   "isError": True}}
        if msg_id is not None:
            return {"jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32601, "message": f"method not found: {method}"}}
        return None

    def serve(self) -> None:
        # MCP stdio는 UTF-8 고정 — Windows 로케일(cp949) 기본값을 덮어쓴다
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            response = self.handle(message)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()


def main(kg_path: str) -> None:
    KgMcpServer(kg_path).serve()
