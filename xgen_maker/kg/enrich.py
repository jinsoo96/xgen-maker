"""의미층(semantic layer) 주입 — 2층 구조의 윗층.

1단 결정론: docstring(meta.doc) 우선, 없으면 구조 기반 템플릿 요약. 비용 0, 전 노드.
2단 LLM: 우선순위 노드(라우트→엔드포인트→feature→핵심 파일)에 평문 한국어 요약을
         배치 생성해 meta.summary를 대체. 재실행 시 이미 LLM 요약된 노드는 스킵(재개 가능).
"""
from __future__ import annotations

from pathlib import Path

from .. import llm
from .graph import Graph

_LLM_KIND_PRIORITY = ("route", "endpoint", "feature", "file")

_SUMMARY_SYSTEM = (
    "You are a senior engineer documenting a codebase. "
    "Summarize the given code unit in 1-2 Korean sentences: what it does and its role. "
    'Reply JSON only: {"summary": "..."}')


def _contained(graph: Graph, node_id: str) -> list[dict]:
    out = []
    for direction, edge in graph.neighbors(node_id):
        if direction == "out" and edge["kind"] == "contains":
            child = graph.nodes.get(edge["dst"])
            if child:
                out.append(child)
    return out


def deterministic_summary(graph: Graph, node: dict) -> str:
    doc = node["meta"].get("doc", "")
    if doc:
        return doc
    kind = node["kind"]
    if kind == "file":
        children = _contained(graph, node["id"])
        classes = [c["name"] for c in children if c["kind"] == "class"][:4]
        functions = [c["name"] for c in children if c["kind"] == "function"][:5]
        endpoints = [c["name"] for c in children if c["kind"] == "endpoint"][:4]
        parts = []
        if endpoints:
            parts.append("엔드포인트 " + ", ".join(endpoints))
        if classes:
            parts.append("클래스 " + ", ".join(classes))
        if functions:
            parts.append("함수 " + ", ".join(functions))
        lang = node["meta"].get("lang", "")
        return f"{lang} 파일 — " + (" · ".join(parts) if parts else "심볼 없음")
    if kind == "endpoint":
        return (f"{node['meta'].get('method', '')} {node['meta'].get('route_path', '')} — "
                f"핸들러 {node['meta'].get('handler', '?')} ({node['path']})")
    if kind == "route":
        return f"화면 라우트 {node['name']} — {node['path']}"
    if kind == "feature":
        members = len(_contained(graph, node["id"]))
        return f"프론트 feature 패키지 {node['name']} — 파일 {members}개"
    if kind == "api_call":
        return f"프론트 API 호출 {node['name']} ({node['path']})"
    if kind in ("class", "function"):
        return f"{kind} {node['name']} — {node['path']}:{node['line']}"
    return node["name"]


def enrich_deterministic(graph: Graph) -> int:
    """summary가 없는 모든 노드에 결정론 요약 주입. 반환 = 채운 노드 수."""
    filled = 0
    for node in graph.nodes.values():
        if node["meta"].get("summary"):
            continue
        node["meta"]["summary"] = deterministic_summary(graph, node)
        node["meta"]["summary_src"] = "deterministic"
        filled += 1
    return filled


def _code_head(node: dict, repos: dict[str, str], lines: int = 50) -> str:
    repo_path = repos.get(node["repo"])
    if not repo_path or not node["path"]:
        return ""
    file_path = Path(repo_path) / node["path"]
    if not file_path.is_file():
        return ""
    try:
        # utf-8-sig — BOM이 첫 줄 선두에 섞여 요약 품질을 떨어뜨리지 않게
        return "\n".join(file_path.read_text(encoding="utf-8-sig", errors="ignore")
                         .splitlines()[:lines])
    except OSError:
        return ""


def _llm_context(graph: Graph, node: dict, repos: dict[str, str]) -> str:
    children = _contained(graph, node["id"])
    symbols = ", ".join(c["name"] for c in children[:15])
    head = _code_head(node, repos)
    return (f"[kind] {node['kind']}\n[name] {node['name']}\n"
            f"[path] {node['repo']}:{node['path']}\n"
            f"[doc] {node['meta'].get('doc', '')}\n[contains] {symbols}\n"
            + (f"[code head]\n{head}" if head else ""))


def _degree_index(graph: Graph) -> dict[str, int]:
    degree: dict[str, int] = {}
    for edge in graph.edges:
        degree[edge["src"]] = degree.get(edge["src"], 0) + 1
        degree[edge["dst"]] = degree.get(edge["dst"], 0) + 1
    return degree


def enrich_llm(graph: Graph, base: str, model: str, repos: dict[str, str],
               limit: int = 200, timeout: int = 45,
               kinds: tuple[str, ...] = _LLM_KIND_PRIORITY,
               chat_fn=None) -> dict:
    """LLM 요약 배치 주입. 반환 stats. chat_fn은 테스트 치환용(기본 llm.json_chat)."""
    chat = chat_fn or llm.json_chat
    degree = _degree_index(graph)
    targets = [n for n in graph.nodes.values()
               if n["kind"] in kinds and n["meta"].get("summary_src") != "llm"]
    targets.sort(key=lambda n: (kinds.index(n["kind"]), -degree.get(n["id"], 0)))
    done, failed = 0, 0
    for node in targets[:limit]:
        answer = chat(base, model, [
            {"role": "system", "content": _SUMMARY_SYSTEM},
            {"role": "user", "content": _llm_context(graph, node, repos)}],
            max_tokens=200, timeout=timeout)
        if answer and isinstance(answer.get("summary"), str) and answer["summary"].strip():
            node["meta"]["summary"] = answer["summary"].strip()[:300]
            node["meta"]["summary_src"] = "llm"
            done += 1
        else:
            failed += 1
            if failed >= 3 and done == 0:
                break  # 엔드포인트 다운 — 조기 중단
    return {"targets": len(targets), "llm_done": done, "llm_failed": failed,
            "remaining": max(0, len(targets) - done - failed)}
